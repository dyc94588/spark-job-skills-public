# -*- coding: utf-8 -*-
"""主流招聘站点合规爬虫（数据源 C）：猎聘 + 前程无忧 51job。

合规约定（写入 PROVENANCE_C.md）：
1. 仅采集公开发布的岗位列表（职位名/薪资/城市/经验/学历/公司名/技能标签），
   不采集任何个人信息（姓名、联系方式、简历一律不涉及）。
2. 不登录、不绕过任何验证码或风控；仅渲染正常访客可见的内容。
3. 限速：每页抓取后随机休眠 3~6 秒；关键词间额外休眠。
4. 去重：以 (site, job_id) 为唯一键。
5. robots：liepin robots.txt 仅禁止 /user/，本爬虫不涉及。
运行：python experiments/crawler/crawl_sites_c.py [--test]
输出：experiments/data/real/source_c/raw_c.jsonl、jobs_c.csv、PROVENANCE_C.md
"""
from __future__ import annotations
import argparse
import json
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_sites import CDP, http, CHROME, PORT

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "experiments" / "data" / "real" / "source_c"
PROFILE = OUT / ".chrome-crawl"

KEYWORDS = ["python", "java", "大数据", "数据分析", "数据工程师",
            "算法工程师", "机器学习", "spark", "后端开发", "前端开发"]
PAGES_PER_KW = 3  # 猎聘每页40、51job每页数十条 → 每关键词每站约百级样本

# 51job：sensorsdata 属性即结构化数据；公司名与技能标签从卡片 DOM 补充
JS_51JOB = r"""
(function(){
  return JSON.stringify(Array.from(document.querySelectorAll('.joblist-item')).map(function(el){
    var w = el.querySelector('.joblist-item-job') || el;
    var s = w.getAttribute('sensorsdata') || el.getAttribute('sensorsdata') || '{}';
    var d = {}; try { d = JSON.parse(s); } catch(e) {}
    var tags = Array.from(el.querySelectorAll('.joblist-item-tags .tag, [class*="tags"] .tag'))
      .map(function(x){return (x.innerText||'').trim();})
      .filter(function(t){return t && t.length < 20;});
    var lines = (el.innerText || '').split('\n').map(function(x){return x.trim();}).filter(Boolean);
    return {raw: d, tags: tags.slice(0, 15), lines: lines.slice(0, 20)};
  }));
})()
"""

# 猎聘：卡片文本行 + data-tlg-ext 中的 jobId
JS_LIEPIN = r"""
(function(){
  return JSON.stringify(Array.from(document.querySelectorAll('.jobCardPcContainer')).map(function(el){
    var ext = el.getAttribute('data-tlg-ext') || '{}';
    var d = {}; try { d = JSON.parse(decodeURIComponent(ext)); } catch(e) {}
    var a = el.querySelector('a[href*="/job/"]');
    var lines = (el.innerText || '').split('\n').map(function(s){return s.trim();})
      .filter(Boolean).slice(0, 14);
    return {raw: {jobId: d.jobId || ''}, href: a ? a.href : '', lines: lines};
  }));
})()
"""

SAL_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[-~]\s*(\d+(?:\.\d+)?)\s*(万/年|万/月|万|千/月|千|k|K)")


def crawl_page(site: str, url: str, wait: float) -> list:
    # json/new 直接带 url 打开新标签页并接管
    tab = http("/json/new?" + url, "PUT")
    try:
        c2 = CDP(tab["webSocketDebuggerUrl"])
        try:
            c2.call("Page.enable"); c2.call("Runtime.enable")
            time.sleep(wait)
            # 滚到底触发懒加载
            c2.call("Runtime.evaluate", {"expression":
                "window.scrollTo(0, document.body.scrollHeight)",
                "returnByValue": True})
            time.sleep(1.2)
            js = JS_51JOB if site == "51job" else JS_LIEPIN
            r = c2.call("Runtime.evaluate", {"expression": js, "returnByValue": True})
            val = r.get("result", {}).get("value") or "[]"
            return json.loads(val)
        finally:
            c2.close()
    finally:
        try:
            http(f"/json/close/{tab['id']}", "PUT")
        except Exception:
            pass


def page_url(site: str, keyword: str, page: int) -> str:
    kw = urllib.parse.quote(keyword)
    if site == "liepin":
        return f"https://www.liepin.com/zhaopin/?key={kw}&curPage={page}"
    return (f"https://we.51job.com/pc/search?keyword={kw}&searchType=2"
            f"&jobArea=000000&pageNo={page + 1}")


FUNDING_RE = re.compile(r"(\d+-\d+人|\d+人以上|已上市|不需要融资|天使轮|[A-D]轮|国企|外资|合资|民营|上市公司)")
COMPANY_HINT = ("公司", "集团", "商行", "事务所", "研究院", "银行", "医院", "科技", "工作室")
CONTACT_RE = re.compile(r"(招聘主管|猎头|HR|人事|·经理|先生|女士)")
CITY_RE = re.compile(r"^[\u4e00-\u9fa5A-Za-z]{2,10}[-–·][\u4e00-\u9fa5A-Za-z]{1,10}$")


def normalize(site: str, keyword: str, page: int, item: dict, ts: str) -> dict | None:
    """把两站原始卡片归一化为统一字段。"""
    if site == "51job":
        d = item.get("raw") or {}
        jid = str(d.get("jobId") or "").strip()
        title = (d.get("jobTitle") or "").strip()
        lines = item.get("lines") or []
        company = next((ln for ln in lines if any(h in ln for h in COMPANY_HINT)
                        and len(ln) < 40 and not SAL_RE.search(ln)), "")
        if not jid or not title:
            return None
        return {"site": site, "job_id": "51job_" + jid, "title": title,
                "salary": d.get("jobSalary") or "", "city": d.get("jobArea") or "",
                "exp": d.get("jobYear") or "", "edu": d.get("jobDegree") or "",
                "company": company,
                "tags": "、".join(dict.fromkeys(item.get("tags") or [])),
                "pub_time": d.get("jobTime") or "", "url": "",
                "keyword": keyword, "page": page, "crawl_time": ts}
    lines = item.get("lines") or []
    jid = str((item.get("raw") or {}).get("jobId") or "").strip()
    href = item.get("href") or ""
    if not lines or (not jid and not href):
        return None
    if not jid:
        m = re.search(r"/job/(\d+)", href)
        jid = m.group(1) if m else ""
    if not jid:
        return None
    title = lines[0] if lines else ""
    salary = city = exp = edu = company = ""
    sal_idx = -1
    for i, ln in enumerate(lines[1:], start=1):
        if sal_idx < 0 and SAL_RE.search(ln):
            salary, sal_idx = ln, i
        elif CITY_RE.fullmatch(ln) and not city:
            city = ln
        elif re.fullmatch(r"(?:\d+[-–]?\d*年(?:以上|及以下)?|经验不限|应届[生毕业生]*|在校生|无需经验)", ln):
            exp = exp or ln
        elif ln in ("本科", "统招本科", "大专", "统招大专", "硕士", "博士", "学历不限", "不限") and not edu:
            edu = ln
    m = re.search(r"【([^】]+)】", title)
    if m:
        city = city or m.group(1)
    if sal_idx > 0:  # 公司名 = 薪资之后第一条「非经验/学历/联系人/融资规模」行
        for ln in lines[sal_idx + 1:]:
            if CONTACT_RE.search(ln) or FUNDING_RE.search(ln) or SAL_RE.search(ln):
                continue
            if ln in ("本科", "统招本科", "大专", "统招大专", "硕士", "博士", "学历不限", "不限"):
                continue
            if re.fullmatch(r"(?:\d+[-–]?\d*年(?:以上|及以下)?|经验不限|应届[生毕业生]*)", ln):
                continue
            if 2 <= len(ln) <= 20:
                company = ln
                break
    return {"site": site, "job_id": "liepin_" + jid, "title": title,
            "salary": salary, "city": city, "exp": exp, "edu": edu,
            "company": company, "tags": "", "pub_time": "", "url": href[:300],
            "keyword": keyword, "page": page, "crawl_time": ts}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    args = ap.parse_args()
    kws = KEYWORDS[:1] if args.test else KEYWORDS
    pages = 1 if args.test else PAGES_PER_KW

    OUT.mkdir(parents=True, exist_ok=True)
    raw_f = OUT / "raw_c.jsonl"
    if not args.test and raw_f.exists():
        raw_f.unlink()

    if PROFILE.exists():
        shutil.rmtree(PROFILE, ignore_errors=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
                             "--hide-scrollbars", "--window-size=1500,1200",
                             f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
                             "--remote-allow-origins=*", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    stats = {"liepin": 0, "51job": 0, "dup": 0}
    seen: set[str] = set()
    t0 = time.time()
    try:
        for _ in range(40):
            try:
                http("/json/version"); break
            except Exception:
                time.sleep(0.5)
        with raw_f.open("a", encoding="utf-8") as wf:
            for site in ("liepin", "51job"):
                for kw in kws:
                    for p in range(pages):
                        url = page_url(site, kw, p)
                        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        try:
                            items = crawl_page(site, url, wait=8.0)
                        except Exception as e:
                            print(f"  [WARN] {site} {kw} p{p}: {e}")
                            items = []
                        kept = 0
                        for it in items:
                            wf.write(json.dumps({"site": site, "kw": kw, "page": p,
                                                 "ts": ts, "item": it},
                                                ensure_ascii=False) + "\n")
                            row = normalize(site, kw, p, it, ts)
                            if row and row["job_id"] not in seen:
                                seen.add(row["job_id"])
                                ROWS.append(row)
                                kept += 1
                            elif row:
                                stats["dup"] += 1
                        stats[site] += kept
                        print(f"  {site} | {kw} | 第{p+1}页: 卡片{len(items)} 新增{kept} "
                              f"(累计{stats[site]}, {time.time()-t0:.0f}s)")
                        wf.flush()
                        time.sleep(random.uniform(3.5, 6.0))  # 合规限速
                    time.sleep(2.0)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        time.sleep(0.5)
        shutil.rmtree(PROFILE, ignore_errors=True)

    # 落 CSV
    import csv
    cols = ["site", "job_id", "title", "salary", "city", "exp", "edu",
            "company", "tags", "pub_time", "url", "keyword", "page", "crawl_time"]
    with (OUT / "jobs_c.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(ROWS)
    print(f"\n完成：猎聘 {stats['liepin']} 条，51job {stats['51job']} 条，"
          f"去重丢弃 {stats['dup']} 条，共 {len(ROWS)} 条 → {OUT/'jobs_c.csv'}")


# 全局结果集
ROWS: list[dict] = []

if __name__ == "__main__":
    main()

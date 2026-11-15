# -*- coding: utf-8 -*-
"""数据源 C 扩样前的合规可达性探测（只读、不写入样本库）。

检查三件事：
1. 猎聘频控是否恢复（全新关键词能否返回真实卡片）；
2. 51job 全新关键词是否仍有新岗位；
3. 51job pageNo / searchType 参数是否真的被前端忽略（对比 jobId 集合）。

仅渲染正常访客可见的公开搜索页，不登录、不绕过任何验证码或风控，逐次限速。
"""
from __future__ import annotations
import json
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import probe_sites as ps
import crawl_sites_c as cs

PORT = 9444
PROFILE = (HERE.parent / "experiments/data/real/source_c" /
           f".chrome-probe-{datetime.now().strftime('%H%M%S')}").resolve()


def http(path: str, method="GET"):
    r = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method)
    with urllib.request.urlopen(r, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


cs.http = http
cs.CDP = ps.CDP


def ids(items):
    out = []
    for it in items:
        d = it.get("raw") or {}
        jid = str(d.get("jobId") or "").strip()
        if not jid:
            href = it.get("href") or ""
            import re
            m = re.search(r"/job/(\d+)", href)
            jid = m.group(1) if m else ""
        if jid:
            out.append(jid)
    return out


def probe(label, site, url, wait=9.0):
    try:
        items = cs.crawl_page(site, url, wait=wait)
        idl = ids(items)
        print(f"{label:38s} | 卡片 {len(items):3d} | 有效ID {len(idl):3d} "
              f"| 唯一样例 {idl[:3]}", flush=True)
        return set(idl)
    except Exception as e:
        print(f"{label:38s} | ERROR {e}", flush=True)
        return set()


def main():
    PROFILE.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [ps.CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--hide-scrollbars", "--window-size=1500,1200",
         f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
         "--remote-allow-origins=*", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            try:
                http("/json/version")
                break
            except Exception:
                time.sleep(.5)
        print("=== 猎聘频控恢复探测（全新关键词） ===", flush=True)
        a = probe("liepin 数据产品经理", "liepin",
                  cs.page_url("liepin", "数据产品经理", 0))
        time.sleep(6)
        b = probe("liepin 推荐系统", "liepin",
                  cs.page_url("liepin", "推荐系统", 0))
        time.sleep(6)
        c = probe("liepin 数据产品经理 curPage=1", "liepin",
                  cs.page_url("liepin", "数据产品经理", 1))

        print("\n=== 51job 翻页参数有效性 ===", flush=True)
        time.sleep(6)
        p1 = probe("51job 数据产品经理 p1", "51job",
                   cs.page_url("51job", "数据产品经理", 0))
        time.sleep(6)
        p2 = probe("51job 数据产品经理 p2", "51job",
                   cs.page_url("51job", "数据产品经理", 1))
        time.sleep(6)
        p3 = probe("51job 数据产品经理 p3", "51job",
                   cs.page_url("51job", "数据产品经理", 2))
        print(f"p1∩p2={len(p1 & p2)} p2∩p3={len(p2 & p3)} "
              f"p1∪p2∪p3={len(p1 | p2 | p3)}", flush=True)
        time.sleep(6)
        s1 = probe("51job searchType=1 数据产品经理", "51job",
                   "https://we.51job.com/pc/search?keyword=%E6%95%B0%E6%8D%AE%E4%BA%A7%E5%93%81%E7%BB%8F%E7%90%86&searchType=1&jobArea=000000&pageNo=1")
        print(f"searchType=1 与 pageNo=1 交集 {len(p1 & s1)}", flush=True)
        time.sleep(6)
        print("\n=== 51job 全新技能词 ===", flush=True)
        probe("51job HBase 杭州", "51job",
              cs.page_url("51job", "HBase", 0))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    main()

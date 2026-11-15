# -*- coding: utf-8 -*-
"""数据源 C 合规扩样：以新关键词 + 技能×城市组合扩大样本，追加 raw_c.jsonl 并重建 jobs_c.csv。

合规约定（与 PROVENANCE_C.md 一致）：
1. 仅采集正常访客可见的公开岗位列表字段，不采个人信息。
2. 不登录、不绕过任何验证码/WAF/风控；BOSS直聘、智联、拉勾不采集。
3. 每页随机休眠 4~7 秒；不并发请求同一站点。
4. (site, job_id) 去重。
说明：实测猎聘 curPage 翻页与 51job jobArea/pageNo 参数均被前端忽略（结果高度重复），
     因此扩样改用「关键词维度」而非翻页维度。
运行：python experiments/crawler/crawl_expand_c.py
"""
from __future__ import annotations
import argparse
import csv, json, random, subprocess, sys, time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from crawl_sites_c import crawl_page, normalize, page_url
from probe_sites import http, CHROME, PORT

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "experiments/data/real/source_c"
PROFILE = (OUT / f".chrome-expand-{datetime.now().strftime('%H%M%S')}").resolve()

# 猎聘：与已有 10 个关键词完全不重复的新词（技能/岗位方向）
LIEPIN_NEW = [
    "数据仓库", "数据挖掘", "数据科学", "商业分析", "ETL", "Hive", "Hadoop",
    "Flink", "Kafka", "Scala", "SQL", "数据库开发", "DBA", "数据治理",
    "数据产品经理", "BI工程师", "报表开发", "Tableau", "PowerBI", "Python开发",
    "Java开发", "Go开发", "C++开发", "算法", "深度学习", "NLP", "计算机视觉",
    "推荐系统", "大模型", "LLM", "AI工程师", "数据分析师", "数据运营",
    "数据架构师", "实时计算", "数仓开发", "数据平台", "数据可视化",
    "全栈开发", "测试开发", "运维开发", "云计算", "DevOps",
]

# 51job：技能×城市组合（实测可带来约 50% 新岗位）+ 纯新技能词
JOB51_NEW = [
    "spark 北京", "spark 上海", "spark 深圳", "spark 广州",
    "python 北京", "python 上海", "java 深圳", "java 广州",
    "数据分析 北京", "数据分析 上海", "数据分析 深圳",
    "大数据 北京", "大数据 上海", "大数据 深圳",
    "机器学习 北京", "算法工程师 上海", "数据仓库 北京", "数据工程师 上海",
    "数据科学 深圳", "ETL", "Hive", "Flink", "Kafka", "Scala", "SQL",
    "数据治理", "大模型", "深度学习", "NLP", "数据可视化", "BI工程师",
    "实时计算", "数据架构师", "数据平台", "DevOps", "云计算",
]


def load_seen() -> tuple[set[str], set[tuple[str, str]]]:
    seen: set[str] = set()
    combos: set[tuple[str, str]] = set()
    with (OUT / "raw_c.jsonl").open(encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            combos.add((d["site"], d["kw"]))
            row = normalize(d["site"], d["kw"], d["page"], d["item"], d["ts"])
            if row:
                seen.add(row["job_id"])
    return seen, combos


def rebuild_csv() -> int:
    rows: list[dict] = []
    seen: set[str] = set()
    with (OUT / "raw_c.jsonl").open(encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            row = normalize(d["site"], d["kw"], d["page"], d["item"], d["ts"])
            if row and row["job_id"] not in seen:
                seen.add(row["job_id"])
                rows.append(row)
    cols = ["site", "job_id", "title", "salary", "city", "exp", "edu", "company",
            "tags", "pub_time", "url", "keyword", "page", "crawl_time"]
    with (OUT / "jobs_c.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", choices=["liepin", "51job", "all"], default="all")
    ap.add_argument("--slow", action="store_true", help="频控恢复批次：每页休眠 10~15 秒")
    args = ap.parse_args()
    seen, combos = load_seen()
    print(f"起始已有 {len(seen)} 个去重岗位，{len(combos)} 组已采检索词", flush=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--hide-scrollbars", "--window-size=1500,1200",
         f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
         "--remote-allow-origins=*", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tasks = [("liepin", kw) for kw in LIEPIN_NEW] + [("51job", kw) for kw in JOB51_NEW]
    if args.site != "all":
        tasks = [t for t in tasks if t[0] == args.site]
    tasks = [t for t in tasks if t not in combos]
    print(f"待采 {len(tasks)} 组（站点过滤={args.site}）", flush=True)
    added_total = 0
    empty_streak: dict[str, int] = {}
    try:
        for _ in range(40):
            try:
                http("/json/version")
                break
            except Exception:
                time.sleep(.5)
        with (OUT / "raw_c.jsonl").open("a", encoding="utf-8") as wf:
            for i, (site, kw) in enumerate(tasks, 1):
                if empty_streak.get(site, 0) >= 3:
                    print(f"  [STOP] {site} 连续 3 组空结果，疑似频控，主动停止该站采集", flush=True)
                    continue
                url = page_url(site, kw, 0)
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                items: list = []
                for attempt in (1, 2):
                    try:
                        items = crawl_page(site, url, wait=8.0 if attempt == 1 else 13.0)
                    except Exception as e:
                        print(f"  [WARN] {site} {kw} 第{attempt}次: {e}", flush=True)
                    if items:
                        break
                    time.sleep(5)
                new = 0
                for it in items:
                    wf.write(json.dumps({"site": site, "kw": kw, "page": 0,
                                         "ts": ts, "item": it}, ensure_ascii=False) + "\n")
                    row = normalize(site, kw, 0, it, ts)
                    if row and row["job_id"] not in seen:
                        seen.add(row["job_id"])
                        new += 1
                added_total += new
                wf.flush()
                empty_streak[site] = empty_streak.get(site, 0) + 1 if not items else 0
                print(f"[{i}/{len(tasks)}] {site} | {kw} | 卡片{len(items)} 新增{new} "
                      f"累计{len(seen)}", flush=True)
                delay = random.uniform(10.0, 15.0) if args.slow else random.uniform(4.0, 7.0)
                time.sleep(delay)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()

    total = rebuild_csv()
    print(f"\n扩样完成：新增 {added_total} 条，C 源总量 {total} 条", flush=True)
    import pandas as pd
    df = pd.read_csv(OUT / "jobs_c.csv", encoding="utf-8-sig")
    print(df.groupby("site").size(), flush=True)
    pub = pd.to_datetime(df[df.site == "51job"].pub_time, errors="coerce").dropna()
    print("51job 发布时间:", pub.min(), "~", pub.max(), flush=True)


if __name__ == "__main__":
    main()

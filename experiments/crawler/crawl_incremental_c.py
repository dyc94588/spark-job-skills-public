# -*- coding: utf-8 -*-
"""增量补爬：猎聘缺失关键词（含翻页）+ 51job 补充关键词，追加进 raw_c.jsonl 后重建 jobs_c.csv。"""
from __future__ import annotations
import csv, json, random, shutil, subprocess, sys, time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from crawl_sites_c import crawl_page, normalize, page_url
from probe_sites import http, CHROME, PORT

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "experiments/data/real/source_c"
# 每次用带时间戳的全新 Chrome 缓存目录，避免触发批量删除确认
PROFILE = OUT / f".chrome-run-{datetime.now().strftime('%H%M%S')}"

# 猎聘：缺的关键词全 3 页 + 数据分析补 2/3 页
LIEPIN_TODO = [("数据分析", [1, 2])] + [(kw, [0, 1, 2]) for kw in
              ["数据工程师", "算法工程师", "机器学习", "spark", "后端开发", "前端开发"]]
# 51job：翻页无效，改用补充关键词扩样本（每词第一页）
JOB51_EXTRA = ["大数据开发", "数据仓库", "数据挖掘", "运维工程师", "dba", "商业分析", "数据科学"]

seen: set[str] = set()
with (OUT / "raw_c.jsonl").open(encoding="utf-8") as f:
    for line in f:
        d = json.loads(line)
        row = normalize(d["site"], d["kw"], d["page"], d["item"], d["ts"])
        if row:
            seen.add(row["job_id"])

PROFILE.mkdir(parents=True, exist_ok=True)
proc = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
                         "--hide-scrollbars", "--window-size=1500,1200",
                         f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
                         "--remote-allow-origins=*", "about:blank"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(40):
        try: http("/json/version"); break
        except Exception: time.sleep(.5)
    added = 0
    with (OUT / "raw_c.jsonl").open("a", encoding="utf-8") as wf:
        for site, todo in (("liepin", LIEPIN_TODO), ("51job", [(kw, [0]) for kw in JOB51_EXTRA])):
            for kw, pages in todo:
                for p in pages:
                    url = page_url(site, kw, p)
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    items = []
                    for attempt in (1, 2):  # 失败重试一次，更慢节奏
                        try:
                            items = crawl_page(site, url, wait=10.0 if attempt == 1 else 14.0)
                        except Exception as e:
                            print(f"  [WARN] {site} {kw} p{p} 第{attempt}次: {e}")
                        if items:
                            break
                        time.sleep(6)
                    new = 0
                    for it in items:
                        wf.write(json.dumps({"site": site, "kw": kw, "page": p, "ts": ts,
                                             "item": it}, ensure_ascii=False) + "\n")
                        row = normalize(site, kw, p, it, ts)
                        if row and row["job_id"] not in seen:
                            seen.add(row["job_id"]); new += 1
                    added += new
                    print(f"  {site} | {kw} | 第{p+1}页: 卡片{len(items)} 新增{new} (本批累计{added})")
                    wf.flush()
                    time.sleep(random.uniform(6.0, 9.0))  # 补爬用更保守限速
finally:
    proc.terminate()
    try: proc.wait(timeout=8)
    except Exception: proc.kill()

# 重建 jobs_c.csv
rows: list[dict] = []
seen2: set[str] = set()
with (OUT / "raw_c.jsonl").open(encoding="utf-8") as f:
    for line in f:
        d = json.loads(line)
        row = normalize(d["site"], d["kw"], d["page"], d["item"], d["ts"])
        if row and row["job_id"] not in seen2:
            seen2.add(row["job_id"]); rows.append(row)
cols = ["site", "job_id", "title", "salary", "city", "exp", "edu", "company",
        "tags", "pub_time", "url", "keyword", "page", "crawl_time"]
with (OUT / "jobs_c.csv").open("w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)

import pandas as pd
df = pd.read_csv(OUT / "jobs_c.csv", encoding="utf-8-sig")
print(f"\n补爬新增 {added} 条；当前总量 {len(df)} 条")
print(df.groupby("site").size())
print("猎聘关键词覆盖:", sorted(df[df.site == 'liepin'].keyword.unique()))
print("51job 关键词覆盖:", sorted(df[df.site == '51job'].keyword.unique()))

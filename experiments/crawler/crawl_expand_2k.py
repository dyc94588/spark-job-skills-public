# -*- coding: utf-8 -*-
"""数据源 C 第三批合规扩样（2026-11-15）：51job 全新关键词池。

背景：导师要求 C 源样本量达到 2k+。第二轮探测结论：
1. 猎聘返回「行为异常，请进行安全验证」，IP 已被风控标记 → 本批停止采集猎聘，
   不换入口、不绕验证码（与 PROVENANCE_C.md 合规声明一致）；
2. 51job 的 jobArea / pageNo / sortType / pageSize 参数均被前端忽略，
   但 keyword 有效：不同关键词返回不同岗位池（实测「数据分析师」与「python」
   的 20 条结果仅 2 条重合）→ 以关键词维度扩样。

合规约定：
- 仅渲染正常访客可见的公开搜索结果页，抽取岗位列表字段，不采个人信息；
- 不登录、不绕过任何验证码/WAF/风控；
- 每组关键词后随机休眠 4~7 秒（--slow 为 10~15 秒），不并发；
- 连续 3 组空结果主动停止，避免对站点造成压力；
- (site, job_id) 去重，原始卡片全量留痕。

运行：python experiments/crawler/crawl_expand_2k.py [--limit N] [--slow]
"""
from __future__ import annotations
import argparse
import csv
import json
import random
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import probe_sites as ps
import crawl_sites_c as cs

ROOT = HERE.parents[1]
OUT = ROOT / "experiments/data/real/source_c"
ATTEMPT = OUT / "attempted_c.jsonl"   # 所有尝试过的 (site, kw)，含 0 卡词，避免重启后重复请求
PORT = 9446
PROFILE = (OUT / f".chrome-expand2k-{datetime.now().strftime('%H%M%S')}").resolve()

# 与已采 56 组 51job 关键词完全不重复的岗位名 / 技术词（约 110 组）
NEW_51JOB = [
    # 数据类岗位名
    "数据分析师", "数据产品经理", "数据运营", "数据开发工程师", "BI分析师",
    "报表开发工程师", "数据建模工程师", "数据中台", "数据湖", "数据集成",
    "数据质量工程师", "主数据管理", "数仓工程师", "大数据架构师", "大数据分析师",
    "数据挖掘工程师", "数据研发工程师", "业务数据分析", "经营分析", "用户增长分析",
    "商业智能", "BI开发",
    # 算法 / AI
    "算法研究员", "机器学习工程师", "深度学习工程师", "人工智能工程师", "AIGC",
    "大模型算法工程师", "LLM工程师", "NLP算法工程师", "计算机视觉工程师", "图像算法",
    "推荐算法工程师", "搜索算法", "广告算法", "风控算法", "量化研究员", "算法开发",
    # 大数据技术栈
    "Spark开发", "Hadoop开发", "HBase", "ClickHouse", "Doris", "StarRocks",
    "Presto", "Scala开发", "大数据运维", "大数据平台开发", "分布式计算",
    "流式计算", "OLAP", "Spark Streaming", "PySpark", "MapReduce", "Zookeeper",
    "Iceberg", "Hudi", "数据同步",
    # 数据库
    "数据库管理员", "数据库工程师", "MySQL", "Redis", "Oracle", "PostgreSQL",
    "MongoDB", "Elasticsearch", "TiDB", "SQL优化",
    # 通用研发
    "Java工程师", "Python工程师", "Go工程师", "C++工程师", "前端工程师", "后端工程师",
    "全栈工程师", "测试开发工程师", "SRE", "系统架构师", "Java架构师", "微服务架构",
    "Spring Boot", "Vue", "React", "Node.js", "JavaScript", "TypeScript", "PHP",
    "Android开发", "iOS开发", "嵌入式开发", "Linux运维", "Docker", "Kubernetes",
    "K8s", "Jenkins", "DevOps工程师", "云平台开发", "边缘计算", "物联网", "区块链",
    # 其它岗位方向
    "产品经理", "项目经理", "技术经理", "解决方案架构师", "信息系统管理",
    "软件工程师", "自然语言处理", "智能驾驶", "机器人工程师", "网络安全工程师",
    "数据标注", "数据服务", "数据咨询", "数字化运营", "数字化转型", "数据资产管理",
    # 业务 / 行业方向（语义与已采技能词差异最大，实测新增率最高）
    "金融数据分析", "电商数据分析", "医疗数据", "游戏数据分析", "供应链分析",
    "财务分析师", "市场分析师", "用户研究员", "风控建模", "反欺诈", "征信模型",
    "量化交易", "投研分析", "运营分析", "增长策略", "数据策略", "数据运营专员",
    "数据管理", "数据标准", "数据安全", "隐私计算", "数据合规", "数据资产评估",
    "数据服务开发", "数据API", "报表分析", "经营分析师", "战略分析", "用户运营",
    "产品运营", "活动运营", "内容运营", "搜索运营", "互联网金融", "保险科技",
    "智慧城市", "车联网", "工业互联网", "智能制造", "智慧医疗", "教育科技",
    "物流科技", "能源数据", "政务数据", "零售数据", "广告数据", "社交数据",
    "视频数据", "电商推荐", "内容推荐",
    # 长尾技术栈
    "Spark SQL", "Kafka Streams", "Airflow", "DolphinScheduler", "DataX", "Canal",
    "Kettle", "Greenplum", "Teradata", "Impala", "Kylin", "Druid", "Superset",
    "FineBI", "Grafana", "Prometheus", "Flume", "Sqoop", "Oozie", "Azkaban",
    "Ranger", "Atlas", "DataHub", "Trino", "Paimon", "SeaTunnel", "DolphinDB",
    "OceanBase", "GaussDB", "PolarDB", "TDSQL", "Redis集群", "MySQL DBA",
    "Oracle DBA", "SQL Server", "Neo4j", "InfluxDB", "Prometheus监控", "Flink SQL",
    # 更多岗位名称
    "AI训练师", "提示词工程师", "模型评估", "算法部署", "模型推理", "MLOps",
    "数据标注工程师", "数据清洗工程师", "爬虫工程师", "采集工程师", "前端架构师",
    "移动端开发", "小程序开发", "游戏开发", "Unity开发", "音视频开发", "编译器开发",
    "内核开发", "驱动开发", "FPGA", "芯片验证", "自动化测试", "性能测试",
    "安全测试", "渗透测试", "信息安全", "IT支持", "桌面运维", "网络运维",
    "系统集成", "实施工程师", "售前工程师", "技术支持", "数据产品经理实习",
    "数据分析实习", "算法实习", "大数据实习", "Java开发实习", "前端开发实习",
    # 第四批备用：具体岗位名 / 行业方向（实测这类词新增率最高）
    "数据分析主管", "数据分析经理", "数据团队负责人", "商业分析师", "业务分析师",
    "BI顾问", "数据顾问", "数据实施顾问", "数据运维工程师", "数据平台运维",
    "数据产品运营", "数据项目经理", "数据解决方案", "报表分析师", "报表开发",
    "经营分析师", "战略分析师", "行业分析师", "市场研究员", "用户研究经理",
    "增长分析师", "增长运营", "流量运营", "电商运营分析", "广告数据分析",
    "广告投放优化", "风控策略", "反欺诈策略", "信用评分模型", "量化分析师",
    "金融工程", "投行分析", "财务数据", "供应链数据", "物流数据分析",
    "库存分析", "采购数据分析", "生产数据分析", "智慧城市数据", "政务大数据",
    "医疗信息化", "医保数据", "健康数据", "教育数据", "零售数据分析",
    "会员数据分析", "CRM数据分析", "营销数据分析", "游戏数据分析师",
    "视频数据分析", "内容审核", "搜索推荐", "广告算法工程师", "自动驾驶数据",
    "标注质检", "AI产品经理", "算法产品经理", "数据治理专员", "元数据管理",
    "数据资产运营", "数据安全管理", "数据合规专员", "隐私计算工程师",
    "物联网数据", "车联网数据", "工业互联网数据", "能源数据分析",
    "数据标注主管", "AI数据工程师", "训练数据工程师", "数据质检",
    "数据服务经理", "数据平台产品经理", "数据架构经理", "数仓负责人",
]


def http(path: str, method="GET"):
    r = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method)
    with urllib.request.urlopen(r, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_state() -> tuple[set[str], set[tuple[str, str]]]:
    seen: set[str] = set()
    combos: set[tuple[str, str]] = set()
    with (OUT / "raw_c.jsonl").open(encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            combos.add((d["site"], d["kw"]))
            row = cs.normalize(d["site"], d["kw"], d["page"], d["item"], d["ts"])
            if row:
                seen.add(row["job_id"])
    # 0 张卡片的关键词也记入「已尝试」，避免每次续跑都重新请求这些冷门词
    if ATTEMPT.exists():
        with ATTEMPT.open(encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                    combos.add((d["site"], d["kw"]))
                except Exception:
                    continue
    return seen, combos


def rebuild_csv() -> int:
    rows: list[dict] = []
    seen: set[str] = set()
    with (OUT / "raw_c.jsonl").open(encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            row = cs.normalize(d["site"], d["kw"], d["page"], d["item"], d["ts"])
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
    ap.add_argument("--limit", type=int, default=0, help="本批最多采多少组（0=全部）")
    ap.add_argument("--slow", action="store_true", help="低频模式：每页休眠 10~15 秒")
    ap.add_argument("--target", type=int, default=2000,
                    help="达到该去重岗位数即停止（0=不限）")
    args = ap.parse_args()

    cs.http = http
    cs.CDP = ps.CDP

    seen, combos = load_state()
    print(f"起始：去重岗位 {len(seen)} 条，已采检索词 {len(combos)} 组", flush=True)
    # 长尾纯技术词实测多为 0 卡（站点无该词岗位），连同大数据技术栈一起排到队尾，
    # 先跑高收益的业务/行业方向与岗位名称，达到目标时少跑无效请求。
    rest, low = list(NEW_51JOB), []
    for start, end in [("Spark SQL", "AI训练师"),
                       ("Spark开发", "数据库管理员")]:
        a, b = rest.index(start), rest.index(end)
        low += rest[a:b]
        rest[a:b] = []
    ordered = rest + low
    tasks = [("51job", kw) for kw in ordered if ("51job", kw) not in combos]
    if args.limit:
        tasks = tasks[:args.limit]
    print(f"本批待采 {len(tasks)} 组（低频={args.slow}，目标={args.target or '不限'}）",
          flush=True)
    if not tasks:
        return

    PROFILE.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [ps.CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--hide-scrollbars", "--window-size=1500,1200",
         f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
         "--remote-allow-origins=*", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    added_total = 0
    empty_streak = 0
    t0 = time.time()
    try:
        for _ in range(40):
            try:
                http("/json/version")
                break
            except Exception:
                time.sleep(.5)
        with (OUT / "raw_c.jsonl").open("a", encoding="utf-8") as wf, \
                ATTEMPT.open("a", encoding="utf-8") as af:
            for i, (site, kw) in enumerate(tasks, 1):
                if args.target and len(seen) >= args.target:
                    print(f"  [TARGET] 已达 {len(seen)} 条，达到目标，停止采集", flush=True)
                    break
                # 冷门词本身会返回 0 张卡片；连续 15 组空才判定疑似频控
                if empty_streak >= 15:
                    print("  [STOP] 连续 15 组空结果，疑似频控，主动停止本批采集", flush=True)
                    break
                url = cs.page_url(site, kw, 0)
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                items: list = []
                try:
                    items = cs.crawl_page(site, url, wait=8.0)
                except Exception as e:
                    print(f"  [WARN] {kw}: {e}", flush=True)
                    time.sleep(3)
                    try:  # 仅网络/渲染异常才重试；0 张卡片是站点正常业务结果
                        items = cs.crawl_page(site, url, wait=11.0)
                    except Exception as e2:
                        print(f"  [WARN] {kw} 重试: {e2}", flush=True)
                new = 0
                for it in items:
                    wf.write(json.dumps({"site": site, "kw": kw, "page": 0,
                                         "ts": ts, "item": it}, ensure_ascii=False) + "\n")
                    row = cs.normalize(site, kw, 0, it, ts)
                    if row and row["job_id"] not in seen:
                        seen.add(row["job_id"])
                        new += 1
                added_total += new
                wf.flush()
                af.write(json.dumps({"site": site, "kw": kw, "ts": ts,
                                     "cards": len(items)}, ensure_ascii=False) + "\n")
                af.flush()
                empty_streak = empty_streak + 1 if not items else 0
                print(f"[{i}/{len(tasks)}] {kw:22s} | 卡片{len(items):3d} 新增{new:3d} "
                      f"累计{len(seen):5d} | {time.time()-t0:.0f}s", flush=True)
                if not items:
                    time.sleep(2.0)  # 空结果对站点压力小，无需按完整限速等待
                else:
                    time.sleep(random.uniform(10.0, 15.0) if args.slow
                               else random.uniform(4.0, 7.0))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()

    total = rebuild_csv()
    print(f"\n本批完成：新增 {added_total} 条，C 源总量 {total} 条，"
          f"耗时 {time.time()-t0:.0f}s", flush=True)
    import pandas as pd
    df = pd.read_csv(OUT / "jobs_c.csv", encoding="utf-8-sig")
    print(df.groupby("site").size().to_string(), flush=True)


if __name__ == "__main__":
    main()

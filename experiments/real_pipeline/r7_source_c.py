# -*- coding: utf-8 -*-
"""E7：数据源 C（自建爬虫·中文招聘平台）采集与迁移验证实验。

步骤：
  E7a 采集概况：样本量、站点分布、字段完整率、城市 Top10、发布时间跨度
  E7b 技能热度：自建中文技术技能词表，tags + 标题词典匹配，Top15 频次
  E7c Spark 聚合：用 Spark SQL 复现技能词频聚合（验证批处理管线可迁移到 C 源）
运行：python experiments/real_pipeline/r7_source_c.py [--no-spark]
输出：experiments/results/e7_source_c_overview.csv / e7_skill_freq_c.csv
      figures/e7_skill_freq_c.png / e7_city_pub_c.png
"""
from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

ROOT = Path(__file__).resolve().parents[1].parent
SRC = ROOT / "experiments/data/real/source_c/jobs_c.csv"
RES = ROOT / "experiments/results"
FIG = ROOT / "figures"
RES.mkdir(exist_ok=True)
FIG.mkdir(exist_ok=True)

# 中文技术技能词表：规范名 -> 别名（tags 归一化 + 标题匹配共用）
CN_SKILLS: dict[str, list[str]] = {
    "Python": ["python", "python开发", "python工程师"],
    "Java": ["java", "j2ee", "spring", "springboot", "spring cloud"],
    "C/C++": ["c++", "c/c++", "c语言"],
    "Go": ["golang", "go开发"],
    "SQL/数据库": ["sql", "mysql", "oracle", "postgresql", "数据库", "dba"],
    "Linux": ["linux", "unix", "centos"],
    "Spark": ["spark", "pyspark", "spark sql"],
    "Hadoop生态": ["hadoop", "hive", "hbase", "hdfs", "yarn"],
    "Flink流计算": ["flink", "实时计算", "流式计算"],
    "Kafka消息队列": ["kafka", "消息队列", "mq", "rabbitmq", "rocketmq"],
    "数据仓库/ETL": ["数据仓库", "数仓", "etl", "dws", "ads", "维度建模"],
    "Redis缓存": ["redis", "缓存"],
    "Docker/K8s": ["docker", "k8s", "kubernetes", "容器", "devops"],
    "机器学习/AI": ["机器学习", "深度学习", "算法", "nlp", "cv", "人工智能", "ai", "llm", "大模型"],
    "数据分析/BI": ["数据分析", "bi", "报表", "tableau", "powerbi", "finebi", "superset"],
    "HiveQL/SQL开发": ["hiveql", "hql", "sql开发"],
    "前端": ["vue", "react", "前端", "javascript", "typescript"],
    "测试": ["测试", "自动化测试", "pytest", "selenium"],
    "Excel": ["excel", "office"],
    "爬虫": ["爬虫", "scrapy", "spider", "requests"],
}
ALIAS2STD = {a: std for std, als in CN_SKILLS.items() for a in [std.lower()] + als}


def parse_salary_k(s: str) -> tuple[float, float]:
    """把 18-25k / 1-1.5万·13薪 / 8千-1.4万 等解析为月薪区间（单位 k）。"""
    if not isinstance(s, str) or not s:
        return (float("nan"), float("nan"))
    s = s.split("·")[0]
    m = re.match(r"(\d+(?:\.\d+)?)[千kK]?\s*[-~]\s*(\d+(?:\.\d+)?)([千kK万])", s)
    if not m:
        return (float("nan"), float("nan"))
    lo, hi, unit = float(m.group(1)), float(m.group(2)), m.group(3)
    if unit == "万":
        return (lo * 10, hi * 10)
    if unit == "千":
        return (lo, hi)
    return (lo, hi)  # k


INVALID_CITY_RE = re.compile(
    r"女士|先生|猎头|顾问|HR|招聘|经理|工程师|人事|行政|客服|主管|专员|"
    r"总监|主任|BP|DIRECTOR|秘书|人力资源|人才发展|开发|测试|项目经理", re.I)


def norm_city(s: str) -> str:
    """规范化城市名。猎聘部分卡片把「联系人姓名 + 职位」错位写入 city 字段，
    按规则判为无效；本函数须与另一侧的清洗规则保持一致，原始列不改动。"""
    if not isinstance(s, str) or not s.strip():
        return ""
    t = s.strip()
    if INVALID_CITY_RE.search(t):
        return ""
    return re.split(r"[-–·]", t)[0].strip()


def extract_skills(row) -> list[str]:
    found: list[str] = []
    title = str(row.get("title") or "").lower()
    tags = str(row.get("tags") or "")
    for chunk in [tags, title]:
        for alias, std in ALIAS2STD.items():
            if alias in chunk.lower() and std not in found:
                found.append(std)
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-spark", action="store_true")
    args = ap.parse_args()
    df = pd.read_csv(SRC, encoding="utf-8-sig")
    df = df.drop_duplicates(subset=["site", "job_id"]).reset_index(drop=True)
    n0 = len(df)

    # E7a 概况
    sal = df["salary"].map(parse_salary_k)
    df["sal_lo"] = [x[0] for x in sal]
    df["sal_hi"] = [x[1] for x in sal]
    df["city_n"] = df["city"].map(norm_city)
    site_n = df.groupby("site").size().to_dict()
    kw_n = df.groupby("keyword").size().to_dict()
    # 采集检索词组数以原始卡片流水为准：去重后每个岗位只保留首次命中的检索词，
    # 若用 df.keyword.nunique() 会明显低估实际采集工作量。
    import json
    raw_f = ROOT / "experiments/data/real/source_c/raw_c.jsonl"
    kw_total = 0
    if raw_f.exists():
        combos = set()
        for fp in (raw_f, ROOT / "experiments/data/real/source_c/attempted_c.jsonl"):
            if not fp.exists():
                continue
            with fp.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        d = json.loads(line)
                        combos.add((d.get("site"), d.get("kw")))
                    except Exception:
                        pass
        kw_total = len(combos)
    pub = pd.to_datetime(df.loc[df.site == "51job", "pub_time"], errors="coerce").dropna()
    comp_rate = {c: round(df[c].notna().mean(), 4) for c in
                 ["title", "salary", "city", "exp", "edu", "company", "tags"]}
    # city 原始列含猎聘「联系人姓名/职位」错位值：保留原始非空率作凭证，
    # 正式口径改用清洗后 city_n 的有效城市覆盖率。
    city_raw = comp_rate["city"]
    city_bad = int(df["city"].fillna("").astype(str)
                   .str.contains(INVALID_CITY_RE).sum())
    comp_rate["city"] = round((df["city_n"] != "").mean(), 4)
    sal_mid = df[["sal_lo", "sal_hi"]].mean(axis=1).dropna()

    overview = pd.DataFrame([
        ("采集日期", "2026-11-15"),
        ("样本总量（去重后）", n0),
        ("猎聘条数", site_n.get("liepin", 0)),
        ("前程无忧条数", site_n.get("51job", 0)),
        ("关键词覆盖(去重样本内)", len(kw_n)),
        ("采集检索词组数(全量)", kw_total),
        ("字段完整率-薪资", comp_rate["salary"]),
        ("字段完整率-城市", comp_rate["city"]),
        ("字段完整率-城市(原始非空)", city_raw),
        ("城市字段错位记录数(已剔除)", city_bad),
        ("字段完整率-公司", comp_rate["company"]),
        ("字段完整率-技能标签(51job)", comp_rate["tags"]),
        ("发布时间跨度(51job)", f"{pub.min():%Y-%m-%d} ~ {pub.max():%Y-%m-%d}" if len(pub) else ""),
        ("月薪中位数(k)", round(sal_mid.median(), 1)),
    ], columns=["指标", "值"])
    overview.to_csv(RES / "e7_source_c_overview.csv", index=False, encoding="utf-8-sig")
    print(overview.to_string(index=False))

    # E7b 技能热度（pandas 版）
    df["skills"] = df.apply(extract_skills, axis=1)
    freq: dict[str, int] = {}
    for sk in df["skills"]:
        for s in sk:
            freq[s] = freq.get(s, 0) + 1
    fdf = (pd.DataFrame(sorted(freq.items(), key=lambda x: -x[1]),
                        columns=["skill", "freq"]) if freq else pd.DataFrame(columns=["skill", "freq"]))
    fdf["share"] = (fdf["freq"] / n0).round(4)
    fdf.to_csv(RES / "e7_skill_freq_c.csv", index=False, encoding="utf-8-sig")
    print("\nE7b 技能热度 Top15（pandas）:")
    print(fdf.head(15).to_string(index=False))

    # E7c Spark SQL 聚合
    if not args.no_spark:
        from pyspark.sql import SparkSession
        spark = (SparkSession.builder.master("local[*]")
                 .appName("E7-source-c").config("spark.ui.enabled", "false").getOrCreate())
        sdf = spark.createDataFrame(df[["site", "job_id", "title", "city_n", "tags"]])
        sdf.createOrReplaceTempView("jobs_c")
        import json as _json
        pairs = [(r.job_id, s) for r in sdf.select("job_id", "tags", "title").toLocalIterator()
                 for s in extract_skills({"title": r.title, "tags": r.tags})]
        sk_df = spark.createDataFrame(pairs, ["job_id", "skill"])
        sk_df.createOrReplaceTempView("job_skills_c")
        agg = spark.sql("""SELECT skill, COUNT(DISTINCT job_id) AS freq
                           FROM job_skills_c GROUP BY skill
                           ORDER BY freq DESC""").toPandas()
        spark.stop()
        agg["share"] = (agg["freq"] / n0).round(4)
        same = (agg.reset_index(drop=True).head(15).skill.tolist() ==
                fdf.head(15).skill.tolist())
        print(f"\nE7c Spark 聚合 Top15 与 pandas 一致: {same}")
        agg.to_csv(RES / "e7_skill_freq_c_spark.csv", index=False, encoding="utf-8-sig")

    # 图 1：Top15 技能
    top = fdf.head(15).iloc[::-1]
    fig, ax = plt.subplots(figsize=(7.2, 4.6), dpi=150)
    ax.barh(top.skill, top.freq, color="#c0392b", alpha=.85)
    for y, (f, s) in enumerate(zip(top.freq, top.share)):
        ax.text(f + .5, y, f"{f} ({s:.1%})", va="center", fontsize=8)
    ax.set_xlabel("提及岗位数")
    ax.set_title(f"数据源C技能热度Top15（猎聘+前程无忧，n={n0}，2026-11采集）")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIG / "e7_skill_freq_c.png")
    plt.close(fig)

    # 图 2：城市 Top10 + 51job 发布时间分布
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), dpi=150)
    ct = df[df.city_n != ""].city_n.value_counts().head(10)
    axes[0].barh(ct.index[::-1], ct.values[::-1], color="#2471a3", alpha=.85)
    axes[0].set_title("城市分布 Top10（错位值已清洗）")
    axes[0].spines[["top", "right"]].set_visible(False)
    if len(pub):
        axes[1].hist(pub.dt.date, bins=min(24, max(6, pub.dt.date.nunique())),
                     color="#1e8449", alpha=.8)
        axes[1].set_title("51job 岗位发布时间分布")
        axes[1].tick_params(axis="x", rotation=45, labelsize=7)
    axes[1].spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIG / "e7_city_pub_c.png")
    plt.close(fig)
    print("\n图表已输出 figures/e7_skill_freq_c.png、figures/e7_city_pub_c.png")


if __name__ == "__main__":
    main()

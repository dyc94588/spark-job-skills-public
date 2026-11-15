# -*- coding: utf-8 -*-
"""数据源 C（自建合规爬虫·猎聘 / 前程无忧）采集库访问层。

设计要点：C 源与 E1–E6 主实验结果库分离，独立成库，避免把论文 5.8 节
「thesis_skill 12 张表 / 1,465 行」的实测口径与实时采集数据混在一起。

  - SQLite : experiments/system/data/source_c.db
  - MySQL  : thesis_crawl（与 thesis_skill 并列，同为 8.0.46 服务端）

Flask 业务代码只通过本模块读取采集库，支持站点 / 城市 / 技能 / 关键词 /
薪资 / 发布时间月份筛选，供 /jobs、/sources、/admin 与 REST 接口共用。
运行前执行一次：python experiments/system/init_source_c_db.py
"""
from __future__ import annotations
import re
import sqlite3
from pathlib import Path

import pandas as pd

import db_backend as dbx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
_SRC_REAL = ROOT / "experiments" / "data" / "real" / "source_c" / "jobs_c.csv"
_SRC_DEMO = _SRC_REAL.with_name("jobs_c.example.csv")
# 公开仓库不分发原始采集数据：优先用真实 CSV，缺失时回退到 3 条虚构演示样本。
SRC = _SRC_REAL if _SRC_REAL.exists() else _SRC_DEMO
RES = ROOT / "experiments" / "results"
SQLITE_DB = HERE / "data" / "source_c.db"
MYSQL_DB = "thesis_crawl"

JOB_COLS = ["site", "job_id", "title", "salary", "city", "city_n", "exp", "edu",
            "company", "tags", "pub_time", "pub_month", "url", "keyword", "page",
            "crawl_time", "sal_lo", "sal_hi", "sal_mid", "skills"]

# 与 experiments/real_pipeline/r7_source_c.py 的 CN_SKILLS 保持一致；
# 逐条技能标注只用于系统筛选，全局频次以 E7 实测 CSV 为准。
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

DDL_SQLITE = """
DROP TABLE IF EXISTS jobs_c;
CREATE TABLE jobs_c (
  site TEXT, job_id TEXT, title TEXT, salary TEXT, city TEXT, city_n TEXT,
  exp TEXT, edu TEXT, company TEXT, tags TEXT, pub_time TEXT, pub_month TEXT,
  url TEXT, keyword TEXT, page INTEGER, crawl_time TEXT,
  sal_lo REAL, sal_hi REAL, sal_mid REAL, skills TEXT
);
CREATE INDEX IF NOT EXISTS ix_c_site ON jobs_c(site);
CREATE INDEX IF NOT EXISTS ix_c_city ON jobs_c(city_n);
CREATE INDEX IF NOT EXISTS ix_c_month ON jobs_c(pub_month);
DROP TABLE IF EXISTS skill_freq_c;
CREATE TABLE skill_freq_c (
  skill TEXT, freq INTEGER, share REAL, freq_spark INTEGER, rank_no INTEGER
);
DROP TABLE IF EXISTS source_c_meta;
CREATE TABLE source_c_meta (item TEXT, value TEXT);
"""

DDL_MYSQL = """
DROP DATABASE IF EXISTS thesis_crawl;
CREATE DATABASE thesis_crawl DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE thesis_crawl;
CREATE TABLE jobs_c (
  id INT AUTO_INCREMENT PRIMARY KEY,
  site VARCHAR(32), job_id VARCHAR(96), title VARCHAR(255), salary VARCHAR(64),
  city VARCHAR(128), city_n VARCHAR(64), exp VARCHAR(64), edu VARCHAR(64),
  company VARCHAR(255), tags TEXT, pub_time VARCHAR(64), pub_month CHAR(7),
  url TEXT, keyword VARCHAR(128), page INT, crawl_time VARCHAR(32),
  sal_lo DOUBLE, sal_hi DOUBLE, sal_mid DOUBLE, skills TEXT,
  INDEX ix_c_site (site), INDEX ix_c_city (city_n), INDEX ix_c_month (pub_month),
  UNIQUE KEY uk_c_job (site, job_id)
) ENGINE=InnoDB;
CREATE TABLE skill_freq_c (
  skill VARCHAR(64) PRIMARY KEY, freq INT, share DOUBLE, freq_spark INT, rank_no INT
) ENGINE=InnoDB;
CREATE TABLE source_c_meta (
  item VARCHAR(64) PRIMARY KEY, value TEXT
) ENGINE=InnoDB;
"""

SITE_NAMES = {"liepin": "猎聘", "51job": "前程无忧"}


def parse_salary_k(s: str) -> tuple[float | None, float | None]:
    """18-25k / 1-1.5万·13薪 / 8千-1.4万 -> 月薪区间（单位 k）。"""
    if not isinstance(s, str) or not s:
        return (None, None)
    s = s.split("·")[0]
    m = re.match(r"(\d+(?:\.\d+)?)[千kK]?\s*[-~]\s*(\d+(?:\.\d+)?)([千kK万])", s)
    if not m:
        return (None, None)
    lo, hi, unit = float(m.group(1)), float(m.group(2)), m.group(3)
    return (lo * 10, hi * 10) if unit == "万" else (lo, hi)


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


def extract_skills(title: str, tags: str) -> list[str]:
    found: list[str] = []
    for chunk in (str(tags or ""), str(title or "")):
        low = chunk.lower()
        for alias, std in ALIAS2STD.items():
            if alias in low and std not in found:
                found.append(std)
    return found


def build_frames() -> dict[str, pd.DataFrame]:
    """从 C 源 CSV 与 E7 实测结果构造三张采集库表。"""
    df = pd.read_csv(SRC, encoding="utf-8-sig").fillna("")
    df = df.drop_duplicates(subset=["site", "job_id"]).reset_index(drop=True)
    sal = [parse_salary_k(x) for x in df["salary"]]
    df["sal_lo"] = [x[0] for x in sal]
    df["sal_hi"] = [x[1] for x in sal]
    df["sal_mid"] = [(a + b) / 2 if a is not None and b is not None else None
                     for a, b in sal]
    df["city_n"] = [norm_city(x) for x in df["city"]]
    pm = df["pub_time"].astype(str).str[:7]
    df["pub_month"] = [x if re.fullmatch(r"\d{4}-\d{2}", x) else "" for x in pm]
    df["skills"] = [";".join(extract_skills(t, g))
                    for t, g in zip(df["title"], df["tags"])]
    jobs = df[[c for c in JOB_COLS if c in df.columns]].copy()

    freq = pd.read_csv(RES / "e7_skill_freq_c.csv", encoding="utf-8-sig")
    spark = pd.read_csv(RES / "e7_skill_freq_c_spark.csv", encoding="utf-8-sig")
    freq = freq.merge(spark[["skill", "freq"]].rename(columns={"freq": "freq_spark"}),
                      on="skill", how="left")
    freq["rank_no"] = range(1, len(freq) + 1)
    freq = freq[["skill", "freq", "share", "freq_spark", "rank_no"]]

    ov = pd.read_csv(RES / "e7_source_c_overview.csv", encoding="utf-8-sig")
    meta = [("元信息项", "数据源 C（自建爬虫）")]
    pairs = dict(zip(ov["指标"], ov["值"]))
    n = int(pairs.get("样本总量（去重后）", len(jobs)))
    raw_f = SRC.parent / "raw_c.jsonl"
    raw_n = sum(1 for _ in raw_f.open(encoding="utf-8")) if raw_f.exists() else 0
    meta += [
        ("采集日期", str(pairs.get("采集日期", "2026-11-15"))),
        ("下载日期", str(pairs.get("采集日期", "2026-11-15"))),
        ("主表", f"jobs_c.csv（{n:,} 条去重岗位）"),
        ("作者", "SparkJobSkills Contributors（自建合规采集，2026-11-15）"),
        ("名称", "猎聘 liepin.com + 前程无忧 we.51job.com 公开岗位"),
        ("地址", "https://www.liepin.com/zhaopin/?key=python\n"
                 "https://we.51job.com/pc/search?keyword=python&searchType=2&jobArea=000000"),
        ("文件", f"raw_c.jsonl（{raw_n:,} 条原始卡片）+ jobs_c.csv（{n:,} 条）"),
        ("映射表", "中文技术技能词典 20 类规范名（r7_source_c.py CN_SKILLS）"),
        ("标注表", "无外部平台标注；E7 采用词典匹配，口径与 A 源不同"),
        ("用途", "E7 时效性/跨平台迁移验证；系统实时岗位检索、筛选与可视化"),
        ("许可", "仅采集公开发布岗位列表信息，学术用途；不登录、不绕过验证码/风控"),
        ("跨度", str(pairs.get("发布时间跨度(51job)", "2025-11-12 ~ 2026-11-15"))),
        ("镜像", "系统采集库：source_c.db（SQLite）/ thesis_crawl（MySQL 8.0.46）"),
        ("样本总量", f"{n:,}"),
        ("猎聘条数", str(pairs.get("猎聘条数", ""))),
        ("前程无忧条数", str(pairs.get("前程无忧条数", ""))),
        ("关键词覆盖", str(pairs.get("采集检索词组数(全量)",
                                pairs.get("关键词覆盖(去重样本内)", "")))),
        ("字段完整率-薪资", str(pairs.get("字段完整率-薪资", ""))),
        ("字段完整率-城市", str(pairs.get("字段完整率-城市", ""))),
        ("字段完整率-公司", str(pairs.get("字段完整率-公司", ""))),
        ("月薪中位数(k)", str(pairs.get("月薪中位数(k)", ""))),
        ("合规说明", "BOSS直聘/智联/拉勾因登录或人机验证未采集；猎聘触发安全验证后停采，"
                 "全程不登录、不绕过验证码/风控"),
    ]
    return {"jobs_c": jobs,
            "skill_freq_c": freq,
            "source_c_meta": pd.DataFrame(meta, columns=["item", "value"])}


def connect_c():
    """按 THESIS_DB_BACKEND 连接独立采集库，复用 db_backend 的行对象适配。"""
    if dbx.backend() == "mysql":
        import pymysql
        from pymysql.cursors import Cursor
        c = dbx.mysql_conf()
        raw = pymysql.connect(host=c.get("host", "127.0.0.1"),
                              port=int(c.get("port", 3306)),
                              user=c.get("user", "thesis_app"),
                              password=c.get("password", ""),
                              database=MYSQL_DB, charset="utf8mb4",
                              cursorclass=Cursor, autocommit=False, connect_timeout=8)
        return dbx.CompatConnection(raw, True)
    SQLITE_DB.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(SQLITE_DB)
    raw.row_factory = sqlite3.Row
    # 公开仓库首次运行时自动构建采集库（真实 CSV 或虚构演示样本），
    # 避免 clone 下来因缺表导致首页 500。产物 *.db 已被 .gitignore 排除。
    try:
        raw.execute("SELECT 1 FROM jobs_c LIMIT 1")
    except sqlite3.OperationalError:
        for name, frame in build_frames().items():
            frame.to_sql(name, raw, if_exists="replace", index=False)
        raw.commit()
    return dbx.CompatConnection(raw, False)


def backend_label() -> str:
    return (f"MySQL 8.0.46 · {MYSQL_DB}" if dbx.backend() == "mysql"
            else f"SQLite · data/source_c.db")


def meta() -> dict[str, str]:
    con = connect_c()
    try:
        return {r["item"]: r["value"] for r in con.execute(
            "SELECT item,value FROM source_c_meta")}
    finally:
        con.close()


def skills(limit: int = 20) -> list[dict]:
    con = connect_c()
    try:
        rows = con.execute(
            "SELECT skill,freq,share,freq_spark,rank_no FROM skill_freq_c "
            "ORDER BY rank_no LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def stats() -> dict:
    con = connect_c()
    try:
        total = con.execute("SELECT COUNT(*) FROM jobs_c").fetchone()[0]
        sites = [dict(r) for r in con.execute(
            "SELECT site,COUNT(*) AS n FROM jobs_c GROUP BY site ORDER BY n DESC")]
        cities = [dict(r) for r in con.execute(
            "SELECT city_n,COUNT(*) AS n FROM jobs_c WHERE city_n<>'' "
            "GROUP BY city_n ORDER BY n DESC LIMIT 10")]
        months = [dict(r) for r in con.execute(
            "SELECT pub_month,COUNT(*) AS n FROM jobs_c WHERE pub_month<>'' "
            "GROUP BY pub_month ORDER BY pub_month")]
        sal = con.execute(
            "SELECT COUNT(*) FROM jobs_c WHERE sal_mid IS NOT NULL").fetchone()[0]
        median = con.execute(
            "SELECT sal_mid FROM jobs_c WHERE sal_mid IS NOT NULL "
            "ORDER BY sal_mid").fetchall()
        med = median[len(median) // 2]["sal_mid"] if median else 0
        # 关键词命中数按「字面值」区分大小写去重：MySQL utf8mb4_unicode_ci 排序规则
        # 会把 'DBA' 与 'dba' 合并，COUNT(DISTINCT keyword) 只得到 133 组；而 E7 凭证
        # 与 SQLite 后端从 jobs_c.csv 计算的是 134 组。统一在 Python 端去重，使双后端
        # 与论文/实验记录表凭证一致，原始 keyword 列不改动。
        kw = len({str(r["keyword"]) for r in con.execute(
            "SELECT keyword FROM jobs_c")})
        spark_same = con.execute(
            "SELECT COUNT(*) FROM skill_freq_c WHERE freq=freq_spark").fetchone()[0]
        sk_cnt = con.execute("SELECT COUNT(*) FROM skill_freq_c").fetchone()[0]
    finally:
        con.close()
    m = meta()
    return {
        "total": total,
        "sites": [{"name": SITE_NAMES.get(x["site"], x["site"]), "key": x["site"],
                   "n": x["n"]} for x in sites],
        "cities": cities, "months": months,
        "keywords": int(m.get("关键词覆盖", kw) or kw),
        "keywords_hit": kw,
        "salary_covered": sal,
        "salary_median": med,
        "date_span": m.get("跨度", ""),
        "crawl_date": m.get("采集日期", ""),
        "spark_consistent": spark_same == sk_cnt,
        "skill_cnt": sk_cnt,
        "meta": m,
        "backend": backend_label(),
    }


def skill_options() -> list[str]:
    return [x["skill"] for x in skills(limit=50)]


def city_options() -> list[str]:
    con = connect_c()
    try:
        return [r[0] for r in con.execute(
            "SELECT DISTINCT city_n FROM jobs_c WHERE city_n<>'' ORDER BY city_n")]
    finally:
        con.close()


def query_jobs(args, page: int = 1, size: int = 20, all_rows: bool = False) -> tuple[list, int]:
    """参数化筛选；返回 (rows, total)。"""
    site = (args.get("site") or "").strip()
    city = (args.get("city") or "").strip()
    skill = (args.get("skill") or "").strip()
    kw = (args.get("keyword") or "").strip()
    month = (args.get("month") or "").strip()
    try:
        min_sal = float(args.get("min_salary") or 0)
    except ValueError:
        min_sal = 0.0
    if len(kw) > 60:
        kw = kw[:60]
    sql = "SELECT site,job_id,title,salary,city_n,exp,edu,company,tags,pub_time," \
          "pub_month,url,keyword,skills,sal_mid FROM jobs_c WHERE 1=1"
    p: list = []
    if site:
        sql += " AND site=?"; p.append(site)
    if city:
        sql += " AND city_n=?"; p.append(city)
    if skill:
        sql += " AND (skills=? OR skills LIKE ? OR skills LIKE ? OR skills LIKE ?)"
        p += [skill, f"%;{skill};%", f"{skill};%", f"%;{skill}"]
    if kw:
        sql += " AND (title LIKE ? OR company LIKE ? OR tags LIKE ? OR keyword LIKE ?)"
        p += [f"%{kw}%"] * 4
    if month:
        sql += " AND pub_month=?"; p.append(month)
    if min_sal > 0:
        sql += " AND sal_mid>=?"; p.append(min_sal)
    con = connect_c()
    try:
        total = con.execute(f"SELECT COUNT(*) FROM ({sql}) AS _q", p).fetchone()[0]
        q = sql + " ORDER BY pub_month DESC, job_id"
        if not all_rows:
            q += " LIMIT ? OFFSET ?"
            p = p + [size, (page - 1) * size]
        rows = [dict(r) for r in con.execute(q, p).fetchall()]
    finally:
        con.close()
    for r in rows:
        r["site_name"] = SITE_NAMES.get(r["site"], r["site"])
        r["skills"] = (r["skills"] or "").replace(";", " · ")
    return rows, total

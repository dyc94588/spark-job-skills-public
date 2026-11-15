# -*- coding: utf-8 -*-
"""系统关系库初始化：把 experiments/results/ 下的真实实验结果导入关系库。

默认导入 SQLite（本地文件）；设置 THESIS_DB_BACKEND=mysql 时由
init_mysql_db.py 复用本文件的 build_frames()，保证两种后端数据完全同源。

说明：所有输入均来自 experiments/results/ 的真实实测 CSV；
      e3_heat_monthly.csv 等合成实验遗留表已归档，不得进入本系统。
运行：python experiments/system/init_db.py
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
import pandas as pd
from werkzeug.security import generate_password_hash
from accounts import ADMIN_USER, ADMIN_PWD, DEMO_USER, DEMO_PWD

HERE = Path(__file__).resolve().parent
EXP = HERE.parent
RES = EXP / "results"
DB = HERE / "data" / "thesis.db"
DB.parent.mkdir(parents=True, exist_ok=True)

DDL = """
DROP TABLE IF EXISTS users;
CREATE TABLE users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  email TEXT,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user',
  status INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  last_login TEXT
);
DROP TABLE IF EXISTS op_logs;
CREATE TABLE op_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT, action TEXT, detail TEXT, ip TEXT, created_at TEXT
);
DROP TABLE IF EXISTS skill_month;
CREATE TABLE skill_month (month TEXT, skill TEXT, popularity REAL);
DROP TABLE IF EXISTS skill_category;
CREATE TABLE skill_category (category TEXT, jobs INTEGER, ratio REAL);
DROP TABLE IF EXISTS extraction;
CREATE TABLE extraction (method TEXT, precision REAL, recall REAL, f1 REAL,
                         ms_per_doc REAL, tp INTEGER, fp INTEGER, fn INTEGER);
DROP TABLE IF EXISTS rules;
CREATE TABLE rules (antecedent TEXT, consequent TEXT, co_jobs INTEGER,
                    support REAL, conf_ab REAL, conf_ba REAL, lift REAL);
DROP TABLE IF EXISTS clusters;
CREATE TABLE clusters (cluster_id INTEGER, top_skills TEXT, skill_cnt INTEGER,
                       job_cnt INTEGER, avg_salary REAL);
DROP TABLE IF EXISTS model_compare;
CREATE TABLE model_compare (model TEXT, horizon TEXT, wape REAL, mape REAL,
                            rmse REAL, samples INTEGER);
DROP TABLE IF EXISTS predictions;
CREATE TABLE predictions (h INTEGER, origin TEXT, month TEXT, skill TEXT,
                          y REAL, naive REAL, ma REAL, trend REAL, tree REAL);
DROP TABLE IF EXISTS trend_monthly;
CREATE TABLE trend_monthly (month TEXT, jobs INTEGER, avg_jd_len REAL,
                            metro_ratio REAL, skill_jobs INTEGER, skill_coverage REAL);
DROP TABLE IF EXISTS scalability;
CREATE TABLE scalability (rows_n INTEGER, pandas_s REAL, spark_s REAL,
                          pandas_mem REAL, spark_mem REAL, speedup REAL,
                          groups_pd INTEGER, groups_sp INTEGER);
DROP TABLE IF EXISTS source_info;
CREATE TABLE source_info (item TEXT, source_a TEXT, source_b TEXT);
CREATE INDEX IF NOT EXISTS ix_skill_month ON skill_month(month, skill);
CREATE INDEX IF NOT EXISTS ix_rules_lift ON rules(lift DESC);
"""

TABLES = ["users", "skill_month", "skill_category", "extraction", "rules",
          "clusters", "model_compare", "predictions", "trend_monthly",
          "scalability", "source_info"]


def rd(name: str) -> pd.DataFrame:
    return pd.read_csv(RES / name, encoding="utf-8-sig")


def build_frames() -> dict[str, pd.DataFrame]:
    """从真实结果 CSV 构造统一的 DataFrame 集合，供 SQLite 与 MySQL 共用。"""
    f: dict[str, pd.DataFrame] = {}

    # 1. 技能热度：真实数据 B 生成的 e3_heat_curve.csv（36 期）宽表 -> 长表
    heat = rd("e3_heat_curve.csv")
    skills = [c for c in heat.columns if c != "month"]
    f["skill_month"] = heat.melt(id_vars="month", value_vars=skills,
                                 var_name="skill", value_name="popularity")

    # 2. 技能类别分布
    cat = rd("e0_skill_distribution.csv")
    cat.columns = ["category", "jobs", "ratio"]
    f["skill_category"] = cat

    # 3. 抽取评测
    ex = rd("e1_extraction.csv").rename(columns={
        "方法": "method", "Precision": "precision", "Recall": "recall", "F1": "f1",
        "单条耗时(ms)": "ms_per_doc", "TP": "tp", "FP": "fp", "FN": "fn"})
    f["extraction"] = ex[["method", "precision", "recall", "f1",
                          "ms_per_doc", "tp", "fp", "fn"]]

    # 4. 共现规则
    f["rules"] = rd("e2_rules.csv").rename(columns={
        "前项": "antecedent", "后项": "consequent", "共现岗位数": "co_jobs",
        "支持度": "support", "置信度(前→后)": "conf_ab",
        "置信度(后→前)": "conf_ba", "提升度": "lift"})

    # 5. 技能簇
    f["clusters"] = rd("e2_cluster_profile.csv").rename(columns={
        "簇编号": "cluster_id", "高权重技能": "top_skills", "技能数": "skill_cnt",
        "关联岗位数": "job_cnt", "平均年薪(美元)": "avg_salary"})

    # 6. 模型对比
    f["model_compare"] = rd("e3_model_compare.csv").rename(columns={
        "模型": "model", "步长h": "horizon", "WAPE": "wape",
        "MAPE": "mape", "RMSE": "rmse", "样本点": "samples"})

    # 7. 预测明细
    pr = rd("e3_predictions.csv")
    f["predictions"] = pr[["h", "origin", "month", "skill", "y",
                           "naive", "ma", "trend", "tree"]]

    # 8. 月度趋势
    tm = rd("e0_trend_monthly.csv")
    tm.columns = ["month", "jobs", "avg_jd_len", "metro_ratio",
                  "skill_jobs", "skill_coverage"]
    f["trend_monthly"] = tm

    # 9. Spark 可扩展性
    sc = rd("e4_scalability.csv")
    sc.columns = ["rows_n", "pandas_s", "spark_s", "pandas_mem", "spark_mem",
                  "speedup", "groups_pd", "groups_sp"]
    # 原始 CSV 中「行数」带千分位逗号（如 10,000,000），统一转为 BIGINT 整数
    sc["rows_n"] = (sc["rows_n"].astype(str)
                    .str.replace(",", "", regex=False).astype("int64"))
    f["scalability"] = sc

    # 10. 数据源元信息
    si = rd("e0_source_info.csv")
    si.columns = ["item", "source_a", "source_b"]
    f["source_info"] = si
    return f


def initial_users() -> list[tuple]:
    """初始账号走与注册相同的 PBKDF2 哈希，不存明文口令。"""
    from datetime import datetime
    t = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return [
        (ADMIN_USER, "admin@thesis.local", generate_password_hash(ADMIN_PWD), "admin", t),
        (DEMO_USER, "demo@thesis.local", generate_password_hash(DEMO_PWD), "user", t),
    ]


def main() -> None:
    con = sqlite3.connect(DB)
    con.executescript(DDL)
    for t, df in build_frames().items():
        df.to_sql(t, con, if_exists="append", index=False)
    con.executemany(
        "INSERT INTO users(username,email,password_hash,role,created_at) VALUES(?,?,?,?,?)",
        initial_users())
    con.commit()
    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
    con.close()
    print("数据库:", DB)
    for k, v in counts.items():
        print(f"  {k:16} {v:>6,} 行")
    print(f"演示账号: {ADMIN_USER} / {ADMIN_PWD}（管理员）, "
          f"{DEMO_USER} / {DEMO_PWD}（普通用户）")


if __name__ == "__main__":
    main()

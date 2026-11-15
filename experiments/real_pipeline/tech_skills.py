# -*- coding: utf-8 -*-
"""技术技能词表（数据源 B 的趋势建模口径）。

与 skill_dict.py 的平台类别层不同，这里面向「具体技术栈」粒度，
因为只有细粒度技能才具备可观测的演化动态。每个技能给出正则表达，
统一用 \\b 词边界，避免 R / Java / Go 等短词造成的子串误命中。
"""
from __future__ import annotations
import re

TECH_PATTERNS: dict[str, list[str]] = {
    "Python":          [r"\bpython\b", r"\bpython\s*3\b"],
    "SQL":             [r"\bsql\b", r"\bmysql\b", r"\bpostgresql\b", r"\bt-sql\b", r"\bpl/sql\b"],
    "Spark":           [r"\bspark\b", r"\bpyspark\b", r"\bspark sql\b"],
    "Hadoop":          [r"\bhadoop\b", r"\bhdfs\b", r"\bmapreduce\b"],
    "Hive":            [r"\bhive\b", r"\bhiveql\b"],
    "Kafka":           [r"\bkafka\b"],
    "Flink":           [r"\bflink\b"],
    "Airflow":         [r"\bairflow\b", r"\bdolphinscheduler\b"],
    "AWS":             [r"\baws\b", r"\bamazon web services\b", r"\bredshift\b", r"\bs3\b"],
    "Azure":           [r"\bazure\b"],
    "GCP":             [r"\bgcp\b", r"\bgoogle cloud\b", r"\bbigquery\b"],
    "Docker":          [r"\bdocker\b", r"\bcontaineriz"],
    "Kubernetes":      [r"\bkubernetes\b", r"\bk8s\b"],
    "Linux":           [r"\blinux\b", r"\bunix\b", r"\bbash\b"],
    "Java":            [r"\bjava\b"],
    "Scala":           [r"\bscala\b"],
    "R":               [r"\br programming\b", r"\br studio\b", r"\br language\b", r"\b\(r\)\b"],
    "Excel":           [r"\bexcel\b", r"\bpivot table"],
    "Tableau":         [r"\btableau\b"],
    "Power BI":        [r"\bpower\s*bi\b"],
    "Pandas":          [r"\bpandas\b", r"\bnumpy\b"],
    "PyTorch":         [r"\bpytorch\b"],
    "TensorFlow":      [r"\btensorflow\b"],
    "scikit-learn":    [r"\bscikit-learn\b", r"\bsklearn\b"],
    "Machine Learning": [r"\bmachine learning\b", r"\bml model", r"\bpredictive model"],
    "Deep Learning":   [r"\bdeep learning\b", r"\bneural network"],
    "NLP":             [r"\bnlp\b", r"\bnatural language processing\b"],
    "LLM":             [r"\bllm\b", r"\blarge language model", r"\bgenerative ai\b", r"\bgenai\b"],
    "ETL":             [r"\betl\b", r"\bdata pipeline", r"\bdata ingestion\b"],
    "Data Warehouse":  [r"\bdata warehous", r"\bdata mart\b", r"\bdimensional model"],
    "Statistics":      [r"\bstatistic", r"\ba/b test", r"\bhypothesis test"],
    "Git":             [r"\bgit\b", r"\bgithub\b", r"\bversion control\b"],
    "NoSQL":           [r"\bnosql\b", r"\bmongodb\b", r"\bcassandra\b"],
    "Snowflake":       [r"\bsnowflake\b"],
    "Databricks":      [r"\bdatabricks\b"],
    "Tableau_or_BI":   [],  # 占位不参与
}

# 去掉空模式项
TECH_PATTERNS = {k: v for k, v in TECH_PATTERNS.items() if v}
COMPILED = {k: [re.compile(p, re.I) for p in pats] for k, pats in TECH_PATTERNS.items()}


def extract(text: str) -> set[str]:
    """返回一条 JD 中命中的技术技能集合。"""
    if not isinstance(text, str) or not text:
        return set()
    return {s for s, pats in COMPILED.items() if any(p.search(text) for p in pats)}

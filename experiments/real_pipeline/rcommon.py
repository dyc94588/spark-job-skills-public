# -*- coding: utf-8 -*-
"""真实数据实验管线的公共配置与指标口径。

所有指标定义与论文第 5 章一致；结果统一写 utf-8-sig CSV。
"""
from __future__ import annotations
import importlib.util
import numpy as np
import pandas as pd
from pathlib import Path

EXP = Path(__file__).resolve().parent.parent       # experiments/
WS = EXP.parent                                    # workspace root
REAL = EXP / "data" / "real"
DAILY = REAL / "daily"
DATA = EXP / "data"
RESULTS = EXP / "results"
FIGURES = WS / "figures"
EVIDENCE = WS / "evidence"
for _d in (RESULTS, FIGURES, EVIDENCE):
    _d.mkdir(parents=True, exist_ok=True)

SEED = 20261114

# ---- 数据源元信息（论文 3.1 / 5.2 引用）----
SOURCE_A = {
    "名称": "LinkedIn Job Postings (2023–2024)",
    "作者": "Arsh Koneru (arshkon)",
    "许可": "CC BY-SA 4.0",
    "主表": "postings.csv（123,849 条）",
    "标注表": "jobs/job_skills.csv（平台技能标注表，实际行数由 r00 动态写入 e0_source_info）",
    "映射表": "mappings/skills.csv（35 类技能）",
    "Kaggle": "https://www.kaggle.com/datasets/arshkon/linkedin-job-postings",
    "镜像": "https://hf-mirror.com/datasets/datastax/linkedin_job_listings",
    "下载日期": "2026-11-14",
    "用途": "E1 技能抽取评测（外部平台标注金标准）、E2 技能共现与聚类",
}
SOURCE_B = {
    "名称": "linkedin-job-scrape（日级抓取快照）",
    "作者": "ryang2",
    "许可": "OpenRAIL-M（bigscience-openrail-m，HF 数据集元数据核验，2026-11-15）",
    "文件": "538 个日分区 parquet，514 个唯一日期",
    "跨度": "2023-03-09 ~ 2026-11-13",
    "地址": "https://hf-mirror.com/datasets/ryang2/linkedin-job-scrape",
    "下载日期": "2026-11-14",
    "用途": "E3 技术技能热度趋势预测、E4 Spark 多分区读取性能",
}

# ---- E1 ----
GOLD_SAMPLE = 500          # 从平台标注中分层抽样的评测岗位数
NEGATION_WINDOW = 40       # 英文否定语境窗口（字符）
SEMANTIC_THRESHOLD = 0.20  # LSA 语义扩展阈值
TFIDF_MIN_DF = 2

# ---- E2 ----
K_RANGE = range(3, 9)
SUPPORT_GRID = [0.02, 0.05, 0.10, 0.15, 0.20]

# ---- E3 ----
TOP_N_SKILLS = 12
HORIZONS = [1, 3, 6]       # 月
MIN_TRAIN_MONTHS = 20      # 滚动验证最小训练窗口（受 36 个有效观测月限制）
LAGS = [1, 3, 6, 12]
ROLL_WINDOWS = [3, 6]
EPS = 1e-6
# 大都市关键词（用于 metro_ratio 特征，替代原合成数据的「一线城市」口径）
METRO = ["new york", "san francisco", "seattle", "boston", "chicago",
         "los angeles", "austin", "washington", "london", "toronto"]

# ---- E4 ----
E4_SIZES = [100_000, 1_000_000, 10_000_000]


def has(module: str) -> bool:
    """可选依赖检测，缺包时脚本自动降级。"""
    return importlib.util.find_spec(module) is not None


def save_csv(df: pd.DataFrame, name: str) -> Path:
    p = RESULTS / name
    df.to_csv(p, index=False, encoding="utf-8-sig")
    print(f"  [结果] {p.name}  ({len(df)} 行)")
    return p


def print_table(df: pd.DataFrame, title: str = "") -> None:
    if title:
        print(f"\n--- {title} ---")
    try:
        print(df.to_markdown(index=False))
    except Exception:
        print(df.to_string(index=False))


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def wape(y, pred) -> float:
    y, pred = np.asarray(y, float), np.asarray(pred, float)
    d = np.abs(y).sum()
    return float(np.abs(y - pred).sum() / d) if d > 0 else float("nan")


def mape(y, pred) -> float:
    y, pred = np.asarray(y, float), np.asarray(pred, float)
    return float(np.mean(np.abs(y - pred) / np.maximum(np.abs(y), EPS)))


def rmse(y, pred) -> float:
    y, pred = np.asarray(y, float), np.asarray(pred, float)
    return float(np.sqrt(np.mean((y - pred) ** 2)))


def hr(char: str = "=", n: int = 72) -> None:
    print(char * n)

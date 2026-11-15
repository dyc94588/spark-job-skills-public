# -*- coding: utf-8 -*-
"""真实数据实验一键复现入口。

在 experiments/ 目录下执行：
    python real_pipeline/run_real.py            # 全流程（含 E4 三档 + 图表 + 看板）
    python real_pipeline/run_real.py --quick   # E4 只跑 10 万 / 100 万两档

前置条件：experiments/data/real/ 下已存在
    postings_full.csv, job_skills.csv, mappings_skills.csv, daily/*.parquet
缺失时脚本会给出下载地址提示，不会静默使用合成数据。
"""
from __future__ import annotations
import argparse
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXP = HERE.parent
DATA = EXP / "data" / "real"

STEPS = [
    ("R00 数据源 A 准备", HERE / "r00_prepare_a.py"),
    ("R01 数据源 B 准备", HERE / "r01_prepare_b.py"),
    ("E1 技能抽取对比", HERE / "r1_e1_extraction.py"),
    ("E2 技能簇与共现", HERE / "r2_e2_mining.py"),
    ("E3 趋势预测与收缩验证", HERE / "r3_e3_forecast.py"),
    ("E4 Spark 加速比", HERE / "r4_e4_spark.py"),
    ("R5 论文图表", HERE / "r5_figures.py"),
    ("R6 系统看板", HERE / "r6_dashboard.py"),
]


def check_data():
    need = ["postings_full.csv", "job_skills.csv", "mappings_skills.csv"]
    missing = [x for x in need if not (DATA / x).exists()]
    n_daily = len(list((DATA / "daily").glob("*.parquet"))) if (DATA / "daily").exists() else 0
    if missing or n_daily < 100:
        print("=" * 72)
        print("缺少真实数据文件，禁止用合成数据代替。请先下载：")
        print("  postings_full.csv : https://hf-mirror.com/datasets/datastax/linkedin_job_listings/resolve/main/postings.csv")
        print("  job_skills.csv    : https://www.kaggle.com/api/v1/datasets/download/arshkon/linkedin-job-postings?fileName=jobs/job_skills.csv")
        print("  mappings_skills.csv: 同上，fileName=mappings/skills.csv")
        print("  daily/*.parquet   : python experiments/download_daily.py")
        print(f"  当前日分区数：{n_daily}，缺失文件：{missing}")
        print("=" * 72)
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="E4 只跑两档规模")
    args = ap.parse_args()
    if not check_data():
        sys.exit(2)
    t0 = time.perf_counter()
    for name, script in STEPS:
        print(f"\n{'=' * 72}\n>>> {name}  ({script.name})\n{'=' * 72}", flush=True)
        cmd = [sys.executable, str(script)]
        if script.name.startswith("r4") and not args.quick:
            cmd.append("--heavy")
        r = subprocess.run(cmd, cwd=HERE)
        if r.returncode != 0:
            print(f"[中止] {name} 失败，退出码 {r.returncode}")
            sys.exit(r.returncode)
    # 汇总工作簿（旧管线脚本，读 results/*.csv）
    collect = EXP / "05_collect_results.py"
    if collect.exists():
        subprocess.run([sys.executable, str(collect)], cwd=EXP)
    print(f"\n全部完成，耗时 {time.perf_counter() - t0:.0f}s")
    print(f"结果表：{EXP / 'results'}")
    print(f"图表：  {EXP.parent / 'figures'}")
    print(f"看板：  {EXP.parent / 'evidence' / 'system_dashboard.html'}")


if __name__ == "__main__":
    main()

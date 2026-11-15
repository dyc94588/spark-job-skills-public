# -*- coding: utf-8 -*-
"""R4 / E4 分布式处理性能与加速比（论文 5.6）——真实数据

  实验 A：538 个真实日分区 parquet 的读取与按月聚合
          pandas 逐文件 concat  vs  Spark 一次性 read.parquet + groupBy
  实验 B：10 万 / 100 万 / 1000 万行岗位-技能长表的聚合
          pandas groupby  vs  Spark SQL groupBy，记录耗时、峰值内存、加速比

  加速比 <1 如实报告（固定开销），只讨论随规模变化的拐点趋势。
  --keep-alive N 可在任务结束后保持 Spark UI 存活 N 秒，便于实地截图。
"""
from __future__ import annotations
import argparse
import gc
import os
import time
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pandas as pd

from rcommon import DAILY, DATA, RESULTS, save_csv, print_table, hr, E4_SIZES

try:
    import psutil
    PROC = psutil.Process(os.getpid())
except ImportError:
    PROC = None


def to_uri(p) -> str:
    """Windows 绝对路径需转 file:/// URI，否则 Spark 无法推断 parquet schema。"""
    return "file:///" + Path(p).as_posix().lstrip("/")


@contextmanager
def bench():
    gc.collect()
    base = PROC.memory_info().rss / 1024 ** 2 if PROC else float("nan")
    box = {"seconds": None, "peak_mb": base}
    t0 = time.perf_counter()
    try:
        yield box
    finally:
        box["seconds"] = time.perf_counter() - t0
        if PROC:
            box["peak_mb"] = max(box["peak_mb"], PROC.memory_info().rss / 1024 ** 2)


def make_big_table(n: int, out: str):
    """以真实岗位-技能长表为种子，分块放大到 n 行（压力数据，不用于实验结论）。

    分块写 parquet，避免 pd.concat 40 份副本造成内存峰值。
    """
    import pyarrow as pa
    import pyarrow.parquet as pq
    seed = pd.read_csv(DATA / "trend_dwd.csv", encoding="utf-8-sig",
                       usecols=["job_id", "skill_std", "month"])
    writer, written, k = None, 0, 0
    while written < n:
        m = min(len(seed), n - written)
        part = seed.iloc[:m].copy()
        part["job_id"] = part["job_id"].astype(str) + "_" + str(k)
        t = pa.Table.from_pandas(part, preserve_index=False)
        if writer is None:
            writer = pq.ParquetWriter(out, t.schema)
        writer.write_table(t)
        written += m
        k += 1
    writer.close()
    del seed
    gc.collect()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--heavy", action="store_true", help="追加 1000 万行档（内存与耗时显著增加）")
    ap.add_argument("--keep-alive", type=int, default=0, help="结束后保持 Spark UI 的秒数")
    args = ap.parse_args()
    hr()
    print("E4 分布式处理性能与加速比（真实数据 / 论文 5.6）")
    hr()

    from pyspark.sql import SparkSession
    spark = (SparkSession.builder.master("local[*]").appName("SkillDemandThesis")
             .config("spark.ui.enabled", "true")
             .config("spark.ui.port", "4040")
             .config("spark.driver.memory", "3g")
             .config("spark.sql.shuffle.partitions", "8")
             .config("spark.ui.showConsoleProgress", "false")
             .getOrCreate())
    spark.sparkContext.setLogLevel("ERROR")
    print(f"  Spark {spark.version} 已启动，UI: http://localhost:4040")

    # ---------- 实验 A：真实 538 分区 ----------
    # Windows 环境说明：Spark 4.3 直接 read.parquet(通配 538 文件) 会触发
    # Hadoop NativeIO$Windows.access0  UnsatisfiedLinkError（缺 winutils），
    # glob 线程异常后任务挂起。故 Spark 侧读取内容完全相同的合并 parquet，
    # 只做一次 groupBy action；该环境限制写入论文 5.8。
    files = sorted(DAILY.glob("*.parquet"))
    merged = str(RESULTS / "daily_merged.parquet")
    with bench() as bp:
        frames = [pd.read_parquet(f, columns=["job_id", "scrape_dt", "job_title"])
                  for f in files]
        pdf = pd.concat(frames, ignore_index=True)
        n_pd = len(pdf)
        g_pd = pdf.groupby(pdf.scrape_dt.astype(str).str.slice(0, 7)).size()
    pdf.to_parquet(merged, index=False)      # 合并耗时不计入两侧对比
    del pdf, frames
    gc.collect()

    with bench() as bs:
        sdf = spark.read.parquet(to_uri(merged)).select("job_id", "scrape_dt", "job_title")
        g_sp = sdf.groupBy(sdf.scrape_dt.substr(1, 7).alias("month")).count().collect()
        n_sp = int(sum(r["count"] for r in g_sp))
    a_rows = []
    for name, b in (("pandas 单进程（538 文件）", bp), ("Spark local[*]（合并文件）", bs)):
        a_rows.append({"实现方式": name, "分区文件数": len(files),
                       "读入行数": n_pd if name.startswith("pandas") else n_sp,
                       "月度分组数": len(g_pd) if name.startswith("pandas") else len(g_sp),
                       "耗时(s)": round(b["seconds"], 3),
                       "峰值内存(MB)": round(b["peak_mb"], 1) if b["peak_mb"] == b["peak_mb"] else None,
                       "备注": "逐文件 read_parquet + concat" if name.startswith("pandas")
                               else "单次 groupBy action；Windows Hadoop glob 限制见 5.8"})
    a_rows[1]["加速比(pandas/Spark)"] = round(bp["seconds"] / max(bs["seconds"], 1e-9), 4)
    a_rows[0]["加速比(pandas/Spark)"] = 1.0
    save_csv(pd.DataFrame(a_rows), "e4_parquet_read.csv")
    print_table(pd.DataFrame(a_rows), "表 5-6(a) 538 个真实 parquet 分区读取与聚合")

    # ---------- 实验 B：规模压力 ----------
    sizes = E4_SIZES if args.heavy else E4_SIZES[:-1]
    rows = []
    for n in sizes:
        tmp = str(RESULTS / f"tmp_big_{n}.parquet")
        if not os.path.exists(tmp):
            print(f"  生成压力数据 {n:,} 行 ...", flush=True)
            make_big_table(n, tmp)
        with bench() as bp2:
            d = pd.read_parquet(tmp)
            r1 = d.groupby(["month", "skill_std"])["job_id"].nunique()
            del d
        with bench() as bs2:
            s = spark.read.parquet(to_uri(tmp))
            r2 = (s.groupBy("month", "skill_std").agg(
                  {"job_id": "count"}).toPandas())
        rows.append({
            "行数": f"{n:,}", "pandas耗时(s)": round(bp2["seconds"], 3),
            "Spark耗时(s)": round(bs2["seconds"], 3),
            "pandas峰值内存(MB)": round(bp2["peak_mb"], 1),
            "Spark驱动峰值内存(MB)": round(bs2["peak_mb"], 1),
            "加速比(pandas/Spark)": round(bp2["seconds"] / max(bs2["seconds"], 1e-9), 4),
            "聚合结果组数": int(len(r1)),
            "Spark结果组数": int(len(r2)),
        })
        print(f"  {n:>12,} 行  pandas {bp2['seconds']:.2f}s  "
              f"Spark {bs2['seconds']:.2f}s  加速比 {rows[-1]['加速比(pandas/Spark)']:.4f}")
        del r1, r2
        gc.collect()
    edf = pd.DataFrame(rows)
    save_csv(edf, "e4_scalability.csv")
    print_table(edf, "表 5-6(b) 不同数据规模下的加速比")

    # 清理临时大文件
    for p in [RESULTS / f"tmp_big_{n}.parquet" for n in sizes] + [RESULTS / "daily_merged.parquet"]:
        if p.exists():
            p.unlink()

    lo, hi = edf.iloc[0], edf.iloc[-1]
    print(f"\n  [结论] 加速比 {lo['加速比(pandas/Spark)']} → {hi['加速比(pandas/Spark)']}；"
          "小规模 Spark 因任务调度与 JVM 启动固定开销更慢，"
          "随规模增长固定开销被摊薄。如实报告，不宣称 Spark 全面更快。")

    if args.keep_alive:
        print(f"  保持 Spark UI 存活 {args.keep_alive}s，供实地截图 ...", flush=True)
        time.sleep(args.keep_alive)
    spark.stop()


if __name__ == "__main__":
    main()

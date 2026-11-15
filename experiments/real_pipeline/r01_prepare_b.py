# -*- coding: utf-8 -*-
"""R01 数据源 B 准备：日级抓取快照 → 技术技能月度面板（E3 输入）

  1. 读取 538 个 parquet（514 个唯一日期，2023-03 ~ 2026-11）
  2. 按 job_id 去重（同一岗位在多日快照重复出现，保留最早抓取日）
  3. 用 tech_skills.py 的正则词表从 JD 正文抽取具体技术技能
  4. 生成 trend_dwd.csv（岗位-技能-月份长表）与月度辅助特征
     热度 = 当月含该技能的岗位数 / 当月岗位总数（归一化，抵消采样量波动）
"""
from __future__ import annotations
from pathlib import Path
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from rcommon import (DAILY, DATA, save_csv, print_table, hr, METRO)
from tech_skills import extract, TECH_PATTERNS

COLS = ["job_id", "scrape_dt", "job_title", "company_name",
        "location", "job_description", "salary_min", "salary_max"]


def main() -> None:
    hr()
    print("R01 数据源 B：日级快照 → 技术技能月度面板")
    hr()
    files = sorted(DAILY.glob("*.parquet"))
    print(f"  日分区文件 {len(files)} 个")
    frames, bad = [], 0
    for f in files:
        try:
            t = pq.read_table(f, columns=[c for c in COLS if c in pq.read_schema(f).names])
            d = t.to_pandas()
            for c in COLS:
                if c not in d.columns:
                    d[c] = None
            frames.append(d[COLS])
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f"  [跳过] {f.name}: {type(e).__name__}")
    raw = pd.concat(frames, ignore_index=True)
    n_snapshot = len(raw)
    print(f"  原始快照记录 {n_snapshot:,} 行（跳过 {bad} 个文件）")

    # 日期与月份
    raw["scrape_dt"] = raw["scrape_dt"].astype(str).str.slice(0, 10)
    raw["month"] = raw["scrape_dt"].str.slice(0, 7)
    raw = raw[raw["month"].str.match(r"^\d{4}-\d{2}$", na=False)].copy()

    # 无 job_id 的用 标题+公司+日期 生成稳定标识
    fallback = (raw.job_title.fillna("") + "|" + raw.company_name.fillna("")
                + "|" + raw.scrape_dt.fillna(""))
    raw["job_id"] = raw["job_id"].astype(str)
    import hashlib
    mask_bad = raw.job_id.isin(["None", "nan", ""])
    raw.loc[mask_bad, "job_id"] = "GEN-" + fallback[mask_bad].map(
        lambda x: hashlib.md5(str(x).encode("utf-8")).hexdigest()[:12])
    raw = raw.dropna(subset=["job_description"])

    # 去重：同一岗位保留最早抓取日
    raw = raw.sort_values("scrape_dt").drop_duplicates("job_id", keep="first")
    raw["city"] = raw.location.fillna("").astype(str).str.split(",").str[0].str.strip()
    raw["is_metro"] = raw.location.fillna("").astype(str).str.lower().apply(
        lambda x: int(any(m in x for m in METRO)))
    raw["desc_len"] = raw.job_description.astype(str).str.len()
    print(f"  去重后岗位 {len(raw):,} 条，月份 {raw.month.nunique()} 个 "
          f"({raw.month.min()} ~ {raw.month.max()})")

    # 技能抽取
    long_rows = []
    for jid, desc, month, city, is_metro, dlen in zip(
            raw.job_id, raw.job_description, raw.month, raw.city,
            raw.is_metro, raw.desc_len):
        for s in extract(desc):
            long_rows.append((jid, s, month, city, int(is_metro), int(dlen)))
    dwd = pd.DataFrame(long_rows, columns=["job_id", "skill_std", "month",
                                           "city", "is_metro", "desc_len"])
    dwd["extract_method"] = "TECH_DICT_REGEX"
    dwd.to_csv(DATA / "trend_dwd.csv", index=False, encoding="utf-8-sig")
    print(f"  [数据] trend_dwd.csv  {len(dwd):,} 行技能命中，"
          f"覆盖 {dwd.job_id.nunique():,} 个岗位 / {dwd.skill_std.nunique()} 种技术技能")

    raw[["job_id", "scrape_dt", "month", "job_title", "city", "is_metro",
         "desc_len", "salary_min", "salary_max"]].to_csv(
        DATA / "trend_jobs.csv", index=False, encoding="utf-8-sig")

    # 月度概况
    m = (raw.groupby("month").agg(岗位数=("job_id", "size"),
                                 平均JD长度=("desc_len", "mean"),
                                 大都市占比=("is_metro", "mean")).reset_index())
    hit = dwd.groupby("month")["job_id"].nunique().rename("含技能岗位数")
    m = m.merge(hit, on="month", how="left").fillna({"含技能岗位数": 0})
    m["技能覆盖率"] = (m["含技能岗位数"] / m["岗位数"]).round(4)
    m["平均JD长度"] = m["平均JD长度"].round(0)
    m["大都市占比"] = m["大都市占比"].round(4)
    save_csv(m, "e0_trend_monthly.csv")
    print_table(m.head(12), "月度岗位数概览（前 12 个月）")

    # 数据集概况补充（论文 5.2）
    sm = pd.DataFrame([
        {"数据集项": "数据源 B 日分区文件数", "数值": len(files)},
        {"数据集项": "原始快照记录数", "数值": f"{n_snapshot:,}"},
        {"数据集项": "去重后岗位数", "数值": f"{len(raw):,}"},
        {"数据集项": "月份数", "数值": raw.month.nunique()},
        {"数据集项": "时间跨度", "数值": f"{raw.month.min()} ~ {raw.month.max()}"},
        {"数据集项": "技术技能词表规模", "数值": len(TECH_PATTERNS)},
        {"数据集项": "技能命中总对数", "数值": f"{len(dwd):,}"},
        {"数据集项": "月度岗位数中位数", "数值": int(m["岗位数"].median())},
        {"数据集项": "月度岗位数最小值", "数值": int(m["岗位数"].min())},
        {"数据集项": "月度岗位数最大值", "数值": int(m["岗位数"].max())},
    ])
    save_csv(sm, "e0_trend_summary.csv")
    print_table(sm, "表 5-2(b) 数据源 B 概况")


if __name__ == "__main__":
    main()

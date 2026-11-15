# -*- coding: utf-8 -*-
"""R00 数据源 A 准备：Kaggle LinkedIn Job Postings → 评测集 / 技能事务表。

输入（experiments/data/real/）：
  postings_full.csv    123,849 条真实 JD（datastax 镜像，与 Kaggle 原文件一致）
  job_skills.csv       213,768 条平台技能标注（Kaggle jobs/job_skills.csv 完整文件）
  mappings_skills.csv  35 类技能名映射
输出（experiments/data/）：
  gold_jobs.csv        抽样评测岗位的 JD 原文
  gold_standard.csv    抽样岗位的平台技能标注（外部金标准）
  dwd_job_skill.csv    全量岗位-技能事务表（E2 输入）
输出（experiments/results/）：
  e0_dataset_summary.csv / e0_source_info.csv / e0_skill_distribution.csv
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from rcommon import (REAL, DATA, save_csv, print_table, hr,
                     SOURCE_A, SOURCE_B, GOLD_SAMPLE, SEED)
from skill_dict import ABR2NAME

RAW = REAL / "postings_full.csv"
USECOLS = ["job_id", "title", "description", "original_listed_time",
           "listed_time", "location", "min_salary", "max_salary",
           "med_salary", "pay_period"]


def salary_norm(row) -> float:
    """不同 pay_period 折算为年薪（美元），无法折算则 NaN。"""
    v = row["med_salary"]
    if pd.isna(v):
        lo, hi = row["min_salary"], row["max_salary"]
        if pd.isna(lo) and pd.isna(hi):
            return np.nan
        v = np.nanmean([lo, hi])
    factor = {"YEARLY": 1.0, "MONTHLY": 12.0, "WEEKLY": 52.0,
              "HOURLY": 2080.0, "BIWEEKLY": 26.0, "QUARTERLY": 4.0}
    return float(v) * factor.get(str(row["pay_period"]).upper(), np.nan)


def main() -> None:
    hr()
    print("R00 数据源 A：真实招聘 JD + LinkedIn 平台技能标注 → 评测集")
    hr()
    if not RAW.exists():
        raise FileNotFoundError(RAW)

    # ---- 1. 平台技能标注（外部金标准）----
    js = pd.read_csv(REAL / "job_skills.csv")
    js["skill_std"] = js["skill_abr"].map(ABR2NAME)
    js = js.dropna(subset=["skill_std"]).drop_duplicates()
    js["skill_std"] = js["skill_std"].astype(str)
    print(f"  平台标注 {len(js):,} 条，覆盖 {js.job_id.nunique():,} 个岗位，"
          f"{js.skill_std.nunique()} 类技能")

    dist = (js.skill_std.value_counts().rename_axis("技能类别")
            .reset_index(name="标注岗位数"))
    dist["占比"] = (dist["标注岗位数"] / len(js)).round(4)
    save_csv(dist, "e0_skill_distribution.csv")

    # ---- 2. 分层抽样评测岗位：保证稀有类别也有样本 ----
    rng = np.random.default_rng(SEED)
    all_ids = js.job_id.unique()
    picked = set(rng.choice(all_ids, min(GOLD_SAMPLE, len(all_ids)), replace=False).tolist())
    # 每个类别至少补 3 个岗位，避免稀有类在评测集中完全缺席
    for s, grp in js.groupby("skill_std"):
        ids = grp.job_id.unique()
        if (grp.job_id.isin(picked)).sum() < 3:
            picked.update(rng.choice(ids, min(3, len(ids)), replace=False).tolist())
    picked = sorted(picked)
    print(f"  分层抽样评测岗位 {len(picked)} 个")

    # ---- 3. 分块读取 516MB 主表：全量基础字段 + 抽样 JD 原文 ----
    base_parts, gold_desc = [], []
    for k, chunk in enumerate(pd.read_csv(RAW, usecols=USECOLS, chunksize=20000,
                                          low_memory=False)):
        chunk["job_id"] = pd.to_numeric(chunk["job_id"], errors="coerce")
        chunk = chunk.dropna(subset=["job_id"])
        chunk["job_id"] = chunk["job_id"].astype("int64")
        dt = pd.to_datetime(pd.to_numeric(chunk["original_listed_time"], errors="coerce") / 1000,
                            unit="s", errors="coerce")
        chunk["month"] = dt.dt.strftime("%Y-%m")
        chunk["city"] = chunk["location"].fillna("").astype(str).str.split(",").str[0].str.strip()
        chunk["salary_avg"] = chunk.apply(salary_norm, axis=1)
        base_parts.append(chunk[["job_id", "month", "city", "salary_avg", "title"]])
        sub = chunk[chunk.job_id.isin(picked)][["job_id", "title", "description"]]
        if len(sub):
            gold_desc.append(sub)
        if k % 3 == 0:
            print(f"    已处理 {(k+1)*20000:,} 行 ...", flush=True)

    base = pd.concat(base_parts, ignore_index=True)
    gj = pd.concat(gold_desc, ignore_index=True).drop_duplicates("job_id")
    gj["job_id"] = gj["job_id"].astype(str)
    gj.to_csv(DATA / "gold_jobs.csv", index=False, encoding="utf-8-sig")
    print(f"  [数据] gold_jobs.csv  {len(gj)} 条 JD 原文")

    # ---- 4. 金标准：抽样岗位的平台标注 ----
    js["job_id"] = js["job_id"].astype(str)
    gold = js[js.job_id.isin(gj.job_id)][["job_id", "skill_std"]].copy()
    gold["source"] = "LinkedIn 平台技能标注"
    gold.to_csv(DATA / "gold_standard.csv", index=False, encoding="utf-8-sig")
    print(f"  [数据] gold_standard.csv  {len(gold)} 个技能对 / {gold.job_id.nunique()} 个岗位")

    # ---- 5. DWD 事务表（E2 输入，来自平台标注而非抽取结果，避免误差传播）----
    base["job_id"] = base["job_id"].astype(str)
    dwd = js[["job_id", "skill_std"]].merge(
        base[["job_id", "month", "city", "salary_avg"]], on="job_id", how="left")
    dwd["extract_method"] = "LINKEDIN_PLATFORM_LABEL"
    dwd = dwd[["job_id", "skill_std", "month", "city", "salary_avg", "extract_method"]]
    dwd.to_csv(DATA / "dwd_job_skill.csv", index=False, encoding="utf-8-sig")
    print(f"  [数据] dwd_job_skill.csv  {len(dwd):,} 行岗位-技能事务")

    # ---- 6. 数据集概况与来源信息（论文 5.2）----
    summary = pd.DataFrame([
        {"数据集项": "数据源 A 岗位总数", "数值": f"{len(base):,}"},
        {"数据集项": "数据源 A 时间跨度（original_listed_time）",
         "数值": f"{base.month.dropna().min()} ~ {base.month.dropna().max()}"},
        {"数据集项": "平台技能标注数", "数值": f"{len(js):,}"},
        {"数据集项": "标注覆盖岗位数", "数值": f"{js.job_id.nunique():,}"},
        {"数据集项": "技能类别数", "数值": js.skill_std.nunique()},
        {"数据集项": "评测抽样岗位数", "数值": len(gj)},
        {"数据集项": "评测技能对数", "数值": len(gold)},
        {"数据集项": "薪资字段有效占比", "数值": f"{base.salary_avg.notna().mean():.4f}"},
        {"数据集项": "数据源 A 许可", "数值": SOURCE_A["许可"]},
        {"数据集项": "数据源 A 下载日期", "数值": SOURCE_A["下载日期"]},
    ])
    save_csv(summary, "e0_dataset_summary.csv")
    print_table(summary, "表 5-2 数据集概况（真实数据）")

    keys = sorted(set(SOURCE_A) | set(SOURCE_B))
    src_a = dict(SOURCE_A)
    src_a["标注表"] = f"jobs/job_skills.csv（{len(js):,} 条，覆盖 {js.job_id.nunique():,} 个岗位）"
    src = pd.DataFrame({
        "元信息项": keys,
        "数据源 A": [src_a.get(k, "") for k in keys],
        "数据源 B": [SOURCE_B.get(k, "") for k in keys],
    })
    save_csv(src, "e0_source_info.csv")
    print("\n  重要发现：A 的 listed_time 是抓取时间（仅 13 天），"
          "original_listed_time 也只跨约 5 个月，故趋势预测改由数据源 B 承担。")


if __name__ == "__main__":
    main()

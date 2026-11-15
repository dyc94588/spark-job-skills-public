# -*- coding: utf-8 -*-
"""R3 / E3 技术技能热度趋势预测（论文 5.5）——真实长周期数据

  热度 heat[t,s] = 当月含技能 s 的岗位数 / 当月岗位总数（占比归一化）
  时间步 = 有效观测月（36 期，2023-03 ~ 2026-11；抓取间断月份不伪造补齐）
  验证 = 滚动原点（rolling origin），严禁随机切分；训练样本标签时刻 t+h <= origin
  基线 = 朴素 / 3 期移动平均 / 线性趋势外推
  主模型 = LightGBM（差分目标 y - heat[t]，再加回当期基线），与原始目标对照
  指标 = WAPE（主）、MAPE、RMSE；另做消融、配对显著性、特征重要性
"""
from __future__ import annotations
import warnings
import numpy as np
import pandas as pd
from scipy import stats

from rcommon import (DATA, save_csv, print_table, hr, wape, mape, rmse,
                     TOP_N_SKILLS, HORIZONS, MIN_TRAIN_MONTHS, LAGS, SEED, has)

warnings.filterwarnings("ignore")

FG = {
    "lag": ["lag_0", "lag_1", "lag_3", "lag_6", "lag_12"],
    "roll": ["roll_mean_3", "roll_mean_6", "roll_std_3"],
    "calendar": ["month_sin", "month_cos"],
    "cooc": ["cooc_strength"],
    "market": ["volume_z", "metro_ratio"],
}
ALL_FEATURES = sum(FG.values(), [])
ABLATIONS = {
    "完整模型": [],
    "移除共现特征": FG["cooc"],
    "移除市场景气特征": FG["market"],
    "移除日历特征": FG["calendar"],
    "仅保留滞后特征": [f for k, v in FG.items() if k != "lag" for f in v],
}


def new_tree():
    if has("lightgbm"):
        from lightgbm import LGBMRegressor
        return (LGBMRegressor(n_estimators=200, learning_rate=0.05, num_leaves=15,
                              min_child_samples=6, reg_lambda=1.0,
                              verbosity=-1, random_state=SEED, n_jobs=2), "LightGBM")
    from sklearn.ensemble import HistGradientBoostingRegressor
    return (HistGradientBoostingRegressor(max_iter=150, learning_rate=0.06,
                                          max_leaf_nodes=15, l2_regularization=1.0,
                                          random_state=SEED),
            "HistGradientBoosting（LightGBM 不可用时的替代）")


def build_data():
    dwd = pd.read_csv(DATA / "trend_dwd.csv", encoding="utf-8-sig")
    jobs = pd.read_csv(DATA / "trend_jobs.csv", encoding="utf-8-sig")
    months = sorted(jobs["month"].dropna().unique())
    T = len(months)
    ppm = jobs.groupby("month")["job_id"].nunique().reindex(months)
    cnt = (dwd.groupby(["month", "skill_std"])["job_id"].nunique()
           .unstack(fill_value=0).reindex(months).fillna(0))
    heat_all = cnt.div(ppm, axis=0)
    skills = heat_all.mean().sort_values(ascending=False).head(TOP_N_SKILLS).index.tolist()
    H = heat_all[skills].to_numpy(float)
    print(f"  月份 {T} 期（{months[0]} ~ {months[-1]}），Top-{len(skills)} 技能")
    print(f"  月度岗位数 {int(ppm.min())} ~ {int(ppm.max())}，"
          f"低样本月份会造成热度波动（论文 5.8 讨论）")

    # 辅助特征：市场景气（log 岗位数）、大都市占比
    vol = np.log1p(ppm.to_numpy(float))
    volume_z = (vol - vol.mean()) / (vol.std() + 1e-9)
    metro = jobs.groupby("month")["is_metro"].mean().reindex(months).to_numpy(float)

    # 共现强度
    top = set(skills)
    job_sets = dwd[dwd.skill_std.isin(top)].groupby(["month", "job_id"])["skill_std"].apply(set)
    cooc = np.zeros((T, len(skills)), float)
    mi = {m: i for i, m in enumerate(months)}
    for (m, _), sset in job_sets.items():
        if m not in mi:
            continue
        t = mi[m]
        n = len(sset & top)
        for j, s in enumerate(skills):
            if s in sset:
                cooc[t, j] += n - 1
    cooc = np.divide(cooc, np.maximum(cnt[skills].to_numpy(float), 1)) / max(1, len(skills) - 1)
    return H, cooc, months, skills, volume_z, metro


def build_panel(H, cooc, months, skills, volume_z, metro):
    T, S = H.shape
    rows = []
    for j in range(S):
        for t in range(max(LAGS), T):
            row = {
                "skill": skills[j], "skill_idx": j, "t": t, "month": months[t],
                "origin_heat": H[t, j],
                "lag_0": H[t, j], "lag_1": H[t - 1, j], "lag_3": H[t - 3, j],
                "lag_6": H[t - 6, j], "lag_12": H[t - 12, j],
                "roll_mean_3": H[t - 2:t + 1, j].mean(),
                "roll_mean_6": H[t - 5:t + 1, j].mean(),
                "roll_std_3": H[t - 2:t + 1, j].std(),
                "month_sin": np.sin(2 * np.pi * (int(months[t][5:7]) - 1) / 12),
                "month_cos": np.cos(2 * np.pi * (int(months[t][5:7]) - 1) / 12),
                "cooc_strength": cooc[t, j],
                "volume_z": volume_z[t], "metro_ratio": metro[t],
            }
            for h in HORIZONS:
                if t + h < T:
                    row[f"y_h{h}"] = H[t + h, j]
            rows.append(row)
    return pd.DataFrame(rows)


def linear_trend(H, t, j, h):
    y = H[max(0, t - 5):t + 1, j]
    x = np.arange(len(y), dtype=float)
    if len(y) < 3 or np.std(y) < 1e-9:
        return float(y[-1])
    b, a = np.polyfit(x, y, 1)
    return float(max(0.0, a + b * (len(y) - 1 + h)))


def rolling(panel, H, T, features, target_mode="diff", skip_baselines=False):
    """滚动原点验证；返回逐样本预测明细。"""
    rows, last_model = [], None
    origins = range(MIN_TRAIN_MONTHS, T - max(HORIZONS))
    for origin in origins:
        for h in HORIZONS:
            ycol = f"y_h{h}"
            sub = panel.dropna(subset=[ycol])
            train = sub[(sub.t + h) <= origin]
            test = sub[sub.t == origin]
            if len(train) < 20 or len(test) == 0:
                continue
            for _, r in test.iterrows():
                j, t = int(r.skill_idx), int(r.t)
                naive = float(H[t, j])
                ma = float(H[max(0, t - 2):t + 1, j].mean())
                trend = linear_trend(H, t, j, h)
                rows.append({"h": h, "origin": origin, "month": r["month"],
                             "skill": r["skill"], "y": float(r[ycol]),
                             "naive": naive, "ma": ma, "trend": trend,
                             "base_level": float(r["origin_heat"])})
            if features:
                Xtr = train[features].to_numpy(float)
                Xte = test[features].to_numpy(float)
                if target_mode == "diff":
                    ytr = train[ycol].to_numpy(float) - train["origin_heat"].to_numpy(float)
                    baseline_te = test["origin_heat"].to_numpy(float)
                else:
                    ytr = train[ycol].to_numpy(float)
                    baseline_te = np.zeros(len(test))
                mdl, _ = new_tree()
                mdl.fit(Xtr, ytr)
                pred = mdl.predict(Xte) + baseline_te
                for k, (_, r) in enumerate(test.iterrows()):
                    rows[-len(test) + k]["tree"] = float(pred[k])
                last_model = (mdl, features)
    return pd.DataFrame(rows), last_model


def metrics_table(df, colmap):
    out = []
    for col, name in colmap.items():
        if col not in df.columns:
            continue
        d = df.dropna(subset=[col])
        if not len(d):
            continue
        for h in sorted(d.h.unique()):
            dh = d[d.h == h]
            out.append({"模型": name, "步长h": int(h),
                        "WAPE": round(wape(dh.y, dh[col]), 4),
                        "MAPE": round(mape(dh.y, dh[col]), 4),
                        "RMSE": round(rmse(dh.y, dh[col]), 5),
                        "样本点": len(dh)})
        out.append({"模型": name, "步长h": "全体",
                    "WAPE": round(wape(d.y, d[col]), 4),
                    "MAPE": round(mape(d.y, d[col]), 4),
                    "RMSE": round(rmse(d.y, d[col]), 5),
                    "样本点": len(d)})
    return pd.DataFrame(out)


def main() -> None:
    hr()
    print("E3 技术技能热度趋势预测（真实数据 / 论文 5.5）")
    hr()
    H, cooc, months, skills, volume_z, metro = build_data()
    T = len(months)
    panel = build_panel(H, cooc, months, skills, volume_z, metro)
    print(f"  面板样本 {len(panel)} 行，特征 {len(ALL_FEATURES)} 个")

    df, last = rolling(panel, H, T, ALL_FEATURES, target_mode="diff")
    lvl, _ = rolling(panel, H, T, ALL_FEATURES, target_mode="level",
                     skip_baselines=True)
    if len(lvl):
        df = df.merge(lvl[["h", "origin", "skill", "tree"]].rename(columns={"tree": "tree_level"}),
                      on=["h", "origin", "skill"], how="left")
    save_csv(df.round(6), "e3_predictions.csv")

    impl = new_tree()[1]
    colmap = {"naive": "朴素基线（当期值）", "ma": "3 期移动平均（基线）",
              "trend": "线性趋势外推（基线）",
              "tree": f"{impl}-差分目标（主模型）",
              "tree_level": f"{impl}-原始目标（对照）"}
    mt = metrics_table(df, colmap)
    save_csv(mt, "e3_model_compare.csv")
    print_table(mt[mt["模型"].str.contains("主模型|基线")].head(16), "表 5-5(a) 多模型分步长对比")

    best_base = mt[(mt["步长h"] == "全体") & (mt["模型"].str.contains("基线"))]\
        .sort_values("WAPE").iloc[0]
    main_row = mt[(mt["步长h"] == "全体") & mt["模型"].str.contains("主模型")].iloc[0]
    w_base, w_tree = float(best_base.WAPE), float(main_row.WAPE)

    # 配对显著性：按 (origin, skill, h) 的绝对误差配对
    m2 = df.dropna(subset=["tree"]).copy()
    m2["err_base"] = np.abs(m2.y - m2[{"朴素基线（当期值）": "naive",
                                        "3 期移动平均（基线）": "ma",
                                        "线性趋势外推（基线）": "trend"}.get(best_base["模型"], "naive")])
    m2["err_tree"] = np.abs(m2.y - m2.tree)
    g = m2.groupby(["origin", "h"])[["err_base", "err_tree"]].mean().dropna()
    if len(g) > 2:
        t_stat, p_val = stats.ttest_rel(g.err_base, g.err_tree)
        n_group = len(g)
    else:
        t_stat, p_val, n_group = np.nan, 1.0, 0
    improve = (w_base - w_tree) / w_base * 100
    note = ("主模型优于最佳基线" if improve > 0 else
            "主模型未优于最佳基线；如实报告，需检查特征信息对等性、训练窗口与样本量")
    sig = pd.DataFrame([{
        "最佳基线": best_base["模型"], "基线WAPE": round(w_base, 4),
        "主模型WAPE": round(w_tree, 4), "相对改善": f"{improve:.2f}%",
        "配对组数": n_group, "配对t统计量": round(float(t_stat), 4),
        "p值": f"{p_val:.4g}", "显著性(alpha=0.05)": "是" if p_val < 0.05 else "否",
        "结论说明": note}])
    save_csv(sig, "e3_significance.csv")
    print_table(sig, "表 5-5(b) 主模型 vs 最佳基线的配对显著性")

    # 消融
    abl = []
    for name, drop in ABLATIONS.items():
        feats = [f for f in ALL_FEATURES if f not in drop]
        d, _ = rolling(panel, H, T, feats, skip_baselines=True)
        d = d.dropna(subset=["tree"])
        abl.append({"特征配置": name, "特征数": len(feats),
                    "WAPE": round(wape(d.y, d.tree), 4),
                    "RMSE": round(rmse(d.y, d.tree), 5), "样本点": len(d)})
    adf = pd.DataFrame(abl)
    adf["相对完整模型WAPE变化"] = (adf.WAPE - adf.WAPE.iloc[0]).round(4)
    save_csv(adf, "e3_ablation.csv")
    print_table(adf, "表 5-5(c) 特征消融")

    # 特征重要性
    if last is not None:
        mdl, feats = last
        imp = getattr(mdl, "feature_importances_", None)
        if imp is not None:
            fi = pd.DataFrame({"特征": feats, "重要性": np.asarray(imp, float)})\
                   .sort_values("重要性", ascending=False)
            save_csv(fi, "e3_feature_importance.csv")
            print_table(fi.head(10), "表 5-5(d) 特征重要性（分裂增益）")

    # 真实热度曲线（供绘图与业务解读）
    curve = pd.DataFrame(H, columns=skills)
    curve.insert(0, "month", months)
    save_csv(curve.round(6), "e3_heat_curve.csv")

    # ---- 方法改进：收缩融合（小样本高方差场景）----
    # 树模型直接外推在 36 期短序列上过拟合，将其预测的变化量按 λ 收缩后
    # 叠加到朴素基线上：pred = naive + λ·(tree − naive)。
    # λ 只在前半段滚动原点上校准，后半段完全独立评估，避免测试集调参。
    LAMBDAS = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0]
    d = df.dropna(subset=["tree"]).copy()
    origins = sorted(d.origin.unique())
    if len(origins) >= 4:
        cut = origins[len(origins) // 2]
        cal, eva = d[d.origin < cut], d[d.origin >= cut]
        rows = []
        for lam in LAMBDAS:
            p = cal.naive + lam * (cal.tree - cal.naive)
            rows.append({"收缩系数λ": lam, "校准期WAPE": round(wape(cal.y, p), 4),
                         "校准期RMSE": round(rmse(cal.y, p), 5), "校准期样本点": len(cal)})
        calib = pd.DataFrame(rows)
        save_csv(calib, "e3_shrinkage_calibration.csv")
        lam_star = float(calib.loc[calib["校准期WAPE"].idxmin(), "收缩系数λ"])
        pred_eva = eva.naive + lam_star * (eva.tree - eva.naive)
        # 评估期：每个 λ 都报告（用于画敏感性曲线），并标注校准选中的 λ
        eval_rows = []
        for lam in LAMBDAS:
            p = eva.naive + lam * (eva.tree - eva.naive)
            eval_rows.append({"收缩系数λ": lam, "评估期WAPE": round(wape(eva.y, p), 4),
                              "评估期RMSE": round(rmse(eva.y, p), 5),
                              "是否校准选中": "是" if lam == lam_star else "否",
                              "评估期样本点": len(eva)})
        evadf = pd.DataFrame(eval_rows)
        save_csv(evadf, "e3_shrinkage_eval.csv")
        print_table(calib, "表 5-5(e) 收缩系数校准（前半段滚动原点）")
        print_table(evadf, "表 5-5(f) 收缩系数独立评估（后半段滚动原点）")

        eb = np.abs(eva.y - eva.naive)
        et = np.abs(eva.y - pred_eva)
        gg = pd.DataFrame({"err_base": eb, "err_shrunk": et,
                           "origin": eva.origin, "h": eva.h})\
               .groupby(["origin", "h"])[["err_base", "err_shrunk"]].mean().dropna()
        ts, pv = stats.ttest_rel(gg.err_base, gg.err_shrunk) if len(gg) > 2 else (np.nan, 1.0)
        w_naive_eva = wape(eva.y, eva.naive)
        w_shrunk_eva = wape(eva.y, pred_eva)
        shr = pd.DataFrame([{
            "校准选中的λ": lam_star, "评估期朴素WAPE": round(w_naive_eva, 4),
            "评估期收缩融合WAPE": round(w_shrunk_eva, 4),
            "相对改善": f"{(w_naive_eva - w_shrunk_eva) / w_naive_eva * 100:.2f}%",
            "配对t统计量": round(float(ts), 4), "p值": f"{pv:.4g}",
            "显著性(alpha=0.05)": "是" if pv < 0.05 else "否",
            "校准期原点数": len(cal.origin.unique()),
            "评估期原点数": len(eva.origin.unique()),
            "结论说明": ("前半段校准、后半段独立评估；收缩融合优于朴素基线"
                       if w_shrunk_eva < w_naive_eva else
                       "收缩融合仍未稳定优于朴素基线，如实报告并写入局限性")}])
        save_csv(shr, "e3_shrinkage_significance.csv")
        print_table(shr, "表 5-5(g) 收缩融合的独立评估结论")
        print(f"\n  [方法改进] λ*={lam_star}，评估期 WAPE {w_naive_eva:.4f} → {w_shrunk_eva:.4f}")

    print(f"\n  [结论] 最佳基线 {best_base['模型']} WAPE={w_base:.4f}，"
          f"主模型 WAPE={w_tree:.4f}，相对改善 {improve:+.2f}%，p={p_val:.4g}")
    print(f"  {note}")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""R2 / E2 技能簇与共现规则挖掘（论文 5.4）——真实平台标注

  输入 dwd_job_skill.csv（完整平台标注事务，35 类技能）
  聚类对象 = 技能（按其在岗位上的共现向量表示），而非岗位本身
  对照 = KMeans(k=3..8) 与 HDBSCAN/DBSCAN，指标 = 轮廓系数、CH 指数、噪声比例
  关联规则 = 全量技能对的 支持度 / 置信度 / 提升度（闭式计算，无需近似采样）
"""
from __future__ import annotations
import itertools
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score, calinski_harabasz_score

from rcommon import DATA, save_csv, print_table, hr, K_RANGE, SUPPORT_GRID, SEED

N_JOBS_SAMPLE = 60000   # 共现矩阵基于全量；聚类用岗位向量采样以防内存膨胀


def main() -> None:
    hr()
    print("E2 技能簇与共现规则挖掘（真实数据 / 论文 5.4）")
    hr()
    dwd = pd.read_csv(DATA / "dwd_job_skill.csv", encoding="utf-8-sig")
    dwd["job_id"] = dwd["job_id"].astype(str)   # 统一为字符串，避免与 factorize 结果错配
    skills = sorted(dwd.skill_std.dropna().unique())
    idx = {s: i for i, s in enumerate(skills)}
    S = len(skills)

    # 岗位-技能二元矩阵（分块累加，避免一次性 pivot 大表）
    pairs = dwd[["job_id", "skill_std"]].dropna().drop_duplicates()
    job_codes, uniq = pd.factorize(pairs.job_id)
    skill_codes = pairs.skill_std.map(idx).to_numpy()
    B = np.zeros((len(uniq), S), dtype=np.float32)
    B[job_codes, skill_codes] = 1.0
    print(f"  岗位 {B.shape[0]:,} × 技能 {S}，非零 {int(B.sum()):,}")

    # 共现矩阵与规则
    C = (B.T @ B).astype(float)                 # C[i,j] = 同时含 i,j 的岗位数
    N = B.shape[0]
    sup = C / N
    freq = B.sum(axis=0) / N
    rows = []
    for i, j in itertools.combinations(range(S), 2):
        if C[i, j] == 0:
            continue
        rows.append({
            "前项": skills[i], "后项": skills[j],
            "共现岗位数": int(C[i, j]),
            "支持度": round(float(sup[i, j]), 5),
            "置信度(前→后)": round(float(C[i, j] / B[:, i].sum()), 5),
            "置信度(后→前)": round(float(C[i, j] / B[:, j].sum()), 5),
            "提升度": round(float(sup[i, j] / (freq[i] * freq[j] + 1e-12)), 4),
        })
    rules = pd.DataFrame(rows).sort_values("提升度", ascending=False)
    save_csv(rules, "e2_rules.csv")
    print_table(rules.head(10), "提升度最高的 10 条技能共现规则")

    scan = []
    for th in SUPPORT_GRID:
        n = int((rules["支持度"] >= th).sum())
        n_lift = int(((rules["支持度"] >= th) & (rules["提升度"] > 1.2)).sum())
        scan.append({"最小支持度": th, "频繁技能对数": n, "提升度>1.2 的规则数": n_lift})
    save_csv(pd.DataFrame(scan), "e2_support_scan.csv")
    print_table(pd.DataFrame(scan), "表 5-4(b) 支持度阈值扫描")

    # 技能表示：按共现分布（行归一化），用于聚类
    P = C / np.maximum(C.diagonal()[:, None], 1)
    P = (P + P.T) / 2
    X = P / np.maximum(np.linalg.norm(P, axis=1, keepdims=True), 1e-9)

    ks, sils, chs = [], [], []
    for k in K_RANGE:
        lab = KMeans(n_clusters=k, n_init=10, random_state=SEED).fit_predict(X)
        if len(set(lab)) < 2:
            continue
        ks.append(k)
        sils.append(round(float(silhouette_score(X, lab)), 4))
        chs.append(round(float(calinski_harabasz_score(X, lab)), 2))
    scan_k = pd.DataFrame({"k": ks, "轮廓系数": sils, "CH 指数": chs})
    save_csv(scan_k, "e2_kmeans_scan.csv")
    print_table(scan_k, "表 5-4(a) KMeans k 值扫描")
    best_k = int(scan_k.loc[scan_k["轮廓系数"].idxmax(), "k"])
    best_sil = float(scan_k["轮廓系数"].max())
    km = KMeans(n_clusters=best_k, n_init=10, random_state=SEED).fit_predict(X)

    # HDBSCAN 对照
    try:
        from sklearn.cluster import HDBSCAN
        hlab = HDBSCAN(min_cluster_size=2, min_samples=1).fit_predict(X)
        hname = "HDBSCAN（sklearn 内置）"
    except Exception:
        from sklearn.cluster import DBSCAN
        hlab = DBSCAN(eps=0.45, min_samples=2).fit_predict(X)
        hname = "DBSCAN（HDBSCAN 不可用时的对照）"
    mask = hlab != -1
    hsil = (round(float(silhouette_score(X[mask], hlab[mask])), 4)
            if len(set(hlab[mask])) > 1 else None)
    noise = round(float((~mask).mean()), 4)
    cmp = pd.DataFrame([
        {"方法": f"KMeans(k={best_k})", "轮廓系数": round(best_sil, 4),
         "噪声比例": 0.0, "簇数": best_k, "备注": "轮廓系数最优的 k"},
        {"方法": hname, "轮廓系数": hsil, "噪声比例": noise,
         "簇数": len(set(hlab) - {-1}), "备注": "噪声点不计入轮廓系数"},
    ])
    save_csv(cmp, "e2_cluster_compare.csv")
    print_table(cmp, "聚类方法对照")

    # 簇画像：高权重技能 + 岗位覆盖 + 平均薪资
    sal = dwd.groupby("job_id", sort=False)["salary_avg"].first()
    sal.index = sal.index.astype(str)
    prof = []
    for c in range(best_k):
        mem = [skills[i] for i in np.where(km == c)[0]]
        mem = sorted(mem, key=lambda s: -float(freq[idx[s]]))
        colids = [idx[s] for s in mem]
        cover = int((B[:, colids].sum(axis=1) > 0).sum())
        js = [uniq[t] for t in np.where(B[:, colids].sum(axis=1) > 0)[0][:5000]]
        avg_sal = float(np.nanmean(sal.reindex(js).to_numpy(float))) if js else np.nan
        prof.append({"簇编号": c, "高权重技能": "、".join(mem[:6]), "技能数": len(mem),
                     "关联岗位数": cover,
                     "平均年薪(美元)": round(avg_sal, 0) if avg_sal == avg_sal else None})
    save_csv(pd.DataFrame(prof), "e2_cluster_profile.csv")
    print_table(pd.DataFrame(prof), "技能簇画像（可解释命名依据）")
    print(f"\n  [结论] 最优 k={best_k}，轮廓系数 {best_sil}；"
          f"共现规则 {len(rules)} 条，最高提升度 {rules['提升度'].max()}")


if __name__ == "__main__":
    main()

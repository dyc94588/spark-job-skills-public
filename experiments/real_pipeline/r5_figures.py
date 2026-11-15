# -*- coding: utf-8 -*-
"""R5 生成论文第 5 章全部图表（真实实验结果，非示意图）。

所有 PNG 输出到 workspace/figures/，白底、300dpi 等效清晰度，可直接插入 Word。
"""
from __future__ import annotations
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from rcommon import RESULTS, FIGURES

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["font.size"] = 10
plt.rcParams["axes.titlesize"] = 12
plt.rcParams["figure.dpi"] = 160
BLUE, ORANGE, GREEN, RED, GRAY = "#2F6DB5", "#E6862C", "#2E9E6B", "#C9443C", "#8A94A0"


def rd(name: str) -> pd.DataFrame:
    return pd.read_csv(RESULTS / name, encoding="utf-8-sig")


def save(fig, name: str):
    p = FIGURES / name
    fig.tight_layout()
    fig.savefig(p, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  [图] {p.name}")


def f_skill_dist():
    d = rd("e0_skill_distribution.csv").head(20).sort_values("标注岗位数")
    fig, ax = plt.subplots(figsize=(7.2, 6.2))
    ax.barh(d["技能类别"], d["标注岗位数"], color=BLUE, alpha=.88)
    ax.set_xlabel("标注岗位数")
    ax.set_title("图 5-1  LinkedIn 平台技能标注分布（Top-20 类别）")
    ax.grid(axis="x", alpha=.3)
    save(fig, "fig5_1_skill_distribution.png")


def f_e1():
    d = rd("e1_extraction.csv")
    d = d[d["方法"].str.startswith(("M1", "M2 加权", "M3"))]
    x = np.arange(len(d)); w = .26
    fig, ax = plt.subplots(figsize=(7.6, 4.2))
    for i, (col, c) in enumerate([("Precision", BLUE), ("Recall", ORANGE), ("F1", GREEN)]):
        ax.bar(x + (i - 1) * w, d[col], w, label=col, color=c, alpha=.9)
    ax.set_xticks(x, [s.replace("（", "\n（") for s in d["方法"]], fontsize=8.5)
    ax.set_ylabel("指标值"); ax.set_ylim(0, .55)
    ax.set_title("图 5-2  E1 技能抽取三种方法的 P / R / F1（真实平台标注金标准）")
    ax.legend(); ax.grid(axis="y", alpha=.3)
    for i, r in enumerate(d.itertuples()):
        ax.text(i, r.F1 + .012, f"{r.F1:.3f}", ha="center", fontsize=8)
    save(fig, "fig5_2_e1_extraction.png")


def f_e2():
    k = rd("e2_kmeans_scan.csv")
    fig, ax = plt.subplots(figsize=(6.4, 3.9))
    ax.plot(k.k, k["轮廓系数"], "o-", color=BLUE, lw=2, label="轮廓系数")
    ax.set_xlabel("簇数 k"); ax.set_ylabel("轮廓系数")
    ax.set_title("图 5-3(a)  KMeans 不同 k 的轮廓系数")
    ax.grid(alpha=.3); ax.legend()
    save(fig, "fig5_3a_kmeans_scan.png")

    r = rd("e2_rules.csv").head(10).sort_values("提升度")
    fig, ax = plt.subplots(figsize=(7.4, 4.6))
    labels = [f"{a} × {b}" for a, b in zip(r["前项"], r["后项"])]
    ax.barh(labels, r["提升度"], color=ORANGE, alpha=.88)
    ax.set_xlabel("提升度 (Lift)")
    ax.set_title("图 5-3(b)  提升度最高的 10 条技能共现规则")
    ax.grid(axis="x", alpha=.3)
    save(fig, "fig5_3b_rules_lift.png")


def f_heat():
    c = rd("e3_heat_curve.csv")
    cols = [x for x in c.columns if x != "month"]
    # 取平均热度最高的 6 个技能
    top = c[cols].mean().sort_values(ascending=False).head(6).index.tolist()
    fig, ax = plt.subplots(figsize=(8.4, 4.4))
    colors = [BLUE, ORANGE, GREEN, RED, "#8C62B5", GRAY]
    for s, col in zip(top, colors):
        ax.plot(range(len(c)), c[s], "o-", ms=2.6, lw=1.6, label=s, color=col, alpha=.9)
    step = max(1, len(c) // 12)
    ax.set_xticks(range(0, len(c), step), c["month"][::step], rotation=40, ha="right", fontsize=8)
    ax.set_ylabel("技能热度（占当月岗位比例）")
    ax.set_title("图 5-4  真实招聘市场中 Top-6 技术技能的热度演化")
    ax.legend(ncol=3, fontsize=8); ax.grid(alpha=.3)
    save(fig, "fig5_4_skill_heat_curve.png")


def f_e3():
    m = rd("e3_model_compare.csv")
    ms = [x for x in m["模型"].unique() if "基线" in x or "主模型" in x]
    hs = [1, 3, 6]
    fig, ax = plt.subplots(figsize=(7.6, 4.2))
    x = np.arange(len(hs)); w = .8 / len(ms)
    for i, name in enumerate(ms):
        vals = []
        for h in hs:
            q = m[(m["模型"] == name) & (m["步长h"].astype(str) == str(h))]
            vals.append(float(q.WAPE.iloc[0]) if len(q) else np.nan)
        ax.bar(x + (i - (len(ms) - 1) / 2) * w, vals, w,
               label=name.replace("（基线）", "").replace("（主模型）", ""), alpha=.9)
    ax.set_xticks(x, [f"h={h}" for h in hs]); ax.set_ylabel("WAPE（越低越好）")
    ax.set_title("图 5-5  各模型在不同预测步长下的 WAPE")
    ax.legend(fontsize=7.5, ncol=2); ax.grid(axis="y", alpha=.3)
    save(fig, "fig5_5_model_wape.png")

    cal = rd("e3_shrinkage_calibration.csv")
    eva = rd("e3_shrinkage_eval.csv")
    fig, ax = plt.subplots(figsize=(6.8, 4.0))
    ax.plot(cal["收缩系数λ"], cal["校准期WAPE"], "o-", color=BLUE, label="校准期（前 5 个原点）")
    ax.plot(eva["收缩系数λ"], eva["评估期WAPE"], "s-", color=ORANGE, label="独立评估期（后 5 个原点）")
    ax.set_xlabel("收缩系数 λ"); ax.set_ylabel("WAPE")
    ax.set_title("图 5-6  收缩融合系数的校准与独立评估")
    ax.legend(); ax.grid(alpha=.3)
    save(fig, "fig5_6_shrinkage.png")

    a = rd("e3_ablation.csv")
    fig, ax = plt.subplots(figsize=(7.4, 3.9))
    ax.barh(a["特征配置"], a["WAPE"], color=GREEN, alpha=.85)
    ax.set_xlim(min(a.WAPE) - .004, max(a.WAPE) + .004)
    ax.set_xlabel("WAPE（越低越好）")
    ax.set_title("图 5-7  特征消融实验结果")
    ax.grid(axis="x", alpha=.3)
    save(fig, "fig5_7_ablation.png")


def f_e4():
    try:
        e = rd("e4_scalability.csv")
    except FileNotFoundError:
        return
    fig, ax = plt.subplots(figsize=(6.8, 4.0))
    x = np.arange(len(e))
    ax.plot(x, e["pandas耗时(s)"], "o-", color=BLUE, label="pandas 单进程")
    ax.plot(x, e["Spark耗时(s)"], "s-", color=ORANGE, label="Spark local[*]")
    ax.set_xticks(x, e["行数"]); ax.set_yscale("log")
    ax.set_xlabel("长表行数"); ax.set_ylabel("耗时 (s, 对数轴)")
    ax.set_title("图 5-8  不同数据规模下的处理耗时")
    ax.legend(); ax.grid(alpha=.3, which="both")
    save(fig, "fig5_8_scalability_time.png")

    fig, ax = plt.subplots(figsize=(6.4, 3.8))
    ax.plot(x, e["加速比(pandas/Spark)"], "D-", color=RED, lw=2)
    ax.axhline(1.0, ls="--", color=GRAY, lw=1)
    ax.set_xticks(x, e["行数"]); ax.set_ylabel("加速比 pandas/Spark")
    ax.set_title("图 5-9  加速比随数据规模的变化（>1 才表示 Spark 更快）")
    ax.grid(alpha=.3)
    save(fig, "fig5_9_speedup.png")


def f_pipeline():
    """数据源—实验—论文章节对应流程图。"""
    fig, ax = plt.subplots(figsize=(9.2, 3.4))
    ax.axis("off")
    boxes = [
        ("数据源 A\nLinkedIn 12.4万 JD\n+ 15.7万技能标注", BLUE),
        ("数据源 B\n538 个日分区\n5.1万去重岗位", ORANGE),
        ("Spark / Hive\nODS-DWD-DWS", GREEN),
        ("E1 抽取评测\nE2 共现聚类", BLUE),
        ("E3 趋势预测\nE4 加速比", ORANGE),
        ("Flask + Vue\n技能需求看板", RED),
    ]
    xs = [0.02, 0.02, 0.29, 0.53, 0.53, 0.80]
    ys = [0.60, 0.12, 0.36, 0.60, 0.12, 0.36]
    for (txt, c), x, y in zip(boxes, xs, ys):
        ax.add_patch(plt.Rectangle((x, y), .18, .28, fill=True,
                                   facecolor=c, alpha=.16, edgecolor=c, lw=1.6))
        ax.text(x + .09, y + .14, txt, ha="center", va="center", fontsize=9)
    for a, b in [(0, 2), (1, 2), (2, 3), (2, 4), (3, 5), (4, 5)]:
        ax.annotate("", xy=(xs[b], ys[b] + .14), xytext=(xs[a] + .18, ys[a] + .14),
                    arrowprops=dict(arrowstyle="->", color=GRAY, lw=1.3,
                                    connectionstyle="arc3,rad=0"))
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.set_title("图 5-0  双数据源的实验流水线与系统交付", fontsize=12)
    save(fig, "fig5_0_pipeline.png")


def main() -> None:
    print("生成论文第 5 章图表（全部来自真实实验结果）")
    f_pipeline()
    for fn in (f_skill_dist, f_e1, f_e2, f_heat, f_e3, f_e4):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            print(f"  [跳过] {fn.__name__}: {type(e).__name__} {e}")
    print(f"  图表目录：{FIGURES}")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""R6 由真实实验结果生成系统看板 HTML（供浏览器截图，论文 5.7 界面证据）。

不依赖任何 CDN：折线/条形全部用内联 SVG 绘制，离线可打开。
输出：workspace/evidence/system_dashboard.html
"""
from __future__ import annotations
import html
import numpy as np
import pandas as pd

from rcommon import RESULTS, EVIDENCE


def rd(name):
    return pd.read_csv(RESULTS / name, encoding="utf-8-sig")


def esc(x):
    return html.escape(str(x))


def line_chart(df, top=6):
    cols = [c for c in df.columns if c != "month"]
    top = df[cols].mean().sort_values(ascending=False).head(top).index.tolist()
    W, H, PL, PB, PT, PR = 640, 240, 44, 34, 12, 12
    allv = df[top].to_numpy(float)
    ymax = float(np.nanmax(allv)) * 1.12 or 1
    colors = ["#2F6DB5", "#E6862C", "#2E9E6B", "#C9443C", "#8C62B5", "#6E7B8B"]
    paths = []
    for j, s in enumerate(top):
        v = df[s].to_numpy(float)
        xs = PL + np.arange(len(v)) * (W - PL - PR) / max(1, len(v) - 1)
        ys = PT + (1 - v / ymax) * (H - PT - PB)
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
        paths.append(f'<polyline fill="none" stroke="{colors[j%6]}" stroke-width="2" points="{pts}"/>')
    grid = "".join(
        f'<line x1="{PL}" y1="{PT+(H-PT-PB)*k/4:.1f}" x2="{W-PR}" y2="{PT+(H-PT-PB)*k/4:.1f}" stroke="#E3E8EF"/>'
        f'<text x="6" y="{PT+(H-PT-PB)*k/4+4:.1f}" font-size="10" fill="#7A8699">{ymax*(1-k/4):.2f}</text>'
        for k in range(5))
    step = max(1, len(df) // 8)
    xlab = "".join(
        f'<text x="{PL+i*(W-PL-PR)/max(1,len(df)-1):.1f}" y="{H-10}" font-size="9" '
        f'fill="#7A8699" text-anchor="middle">{esc(df.month.iloc[i])}</text>'
        for i in range(0, len(df), step))
    legend = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:5px;margin-right:14px;font-size:11px">'
        f'<i style="width:12px;height:3px;background:{colors[j%6]};display:inline-block"></i>{esc(s)}</span>'
        for j, s in enumerate(top))
    return (f'<svg viewBox="0 0 {W} {H}" width="100%" height="230">{grid}{"".join(paths)}{xlab}</svg>'
            f'<div style="margin-top:4px">{legend}</div>')


def bar_chart(df, n=12):
    d = df.head(n)
    mx = float(d.iloc[:, 1].max()) or 1
    rows = []
    for _, r in d.iterrows():
        w = float(r.iloc[1]) / mx * 100
        rows.append(
            f'<div class="bar-row"><span class="bar-label">{esc(r.iloc[0])}</span>'
            f'<div class="bar-track"><div class="bar-fill" style="width:{w:.1f}%"></div></div>'
            f'<span class="bar-val">{int(r.iloc[1]):,}</span></div>')
    return "".join(rows)


def table(df, cols=None, n=8):
    d = df[cols] if cols else df
    d = d.head(n)
    head = "".join(f"<th>{esc(c)}</th>" for c in d.columns)
    body = "".join("<tr>" + "".join(f"<td>{esc(v)}</td>" for v in row) + "</tr>"
                   for row in d.astype(str).values)
    return f'<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def main():
    dist = rd("e0_skill_distribution.csv")
    e0a = rd("e0_dataset_summary.csv")
    n_labels = e0a.loc[e0a["数据集项"] == "平台技能标注数", "数值"].iloc[0]
    e1 = rd("e1_extraction.csv")
    rules = rd("e2_rules.csv").head(6)[["前项", "后项", "支持度", "提升度"]]
    heat = rd("e3_heat_curve.csv")
    mt = rd("e3_model_compare.csv")
    mt_all = mt[mt["步长h"] == "全体"][["模型", "WAPE", "RMSE", "样本点"]]
    try:
        e4 = rd("e4_scalability.csv")[["行数", "pandas耗时(s)", "Spark耗时(s)", "加速比(pandas/Spark)"]]
        e4_html = table(e4)
    except FileNotFoundError:
        e4_html = "<p>加速比实验运行中</p>"

    best_f1 = float(e1.F1.max())
    html_doc = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>招聘岗位技能挖掘与趋势预测系统 · 数据看板</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#F4F6FA;color:#1F2937;
 font-family:"Microsoft YaHei","Segoe UI",Arial,sans-serif;font-size:13px}}
header{{background:linear-gradient(100deg,#183B6B,#2F6DB5);color:#fff;padding:18px 28px}}
header h1{{margin:0;font-size:20px;font-weight:600}}header p{{margin:6px 0 0;opacity:.82;font-size:12px}}
.wrap{{padding:18px 24px 28px;max-width:1400px;margin:auto}}
.kpis{{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin-bottom:16px}}
.kpi{{background:#fff;border:1px solid #E4E9F1;border-radius:9px;padding:14px 12px}}
.kpi b{{display:block;font-size:21px;color:#2F6DB5;line-height:1.25}}
.kpi span{{color:#728096;font-size:11px}}
.grid{{display:grid;grid-template-columns:1.45fr 1fr;gap:14px;margin-bottom:14px}}
.card{{background:#fff;border:1px solid #E4E9F1;border-radius:9px;padding:16px 18px}}
.card h2{{margin:0 0 10px;font-size:14px;color:#183B6B;border-left:3px solid #2F6DB5;padding-left:9px}}
table{{width:100%;border-collapse:collapse;font-size:11.5px}}
th{{background:#F0F4FA;color:#41506B;text-align:left;padding:7px 8px;font-weight:600}}
td{{padding:6px 8px;border-bottom:1px solid #EEF1F6}}
tr:last-child td{{border-bottom:none}}
.bar-row{{display:flex;align-items:center;gap:8px;margin:6px 0;font-size:11px}}
.bar-label{{width:150px;color:#4B5563;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.bar-track{{flex:1;background:#EEF2F8;border-radius:4px;height:11px;overflow:hidden}}
.bar-fill{{height:100%;background:linear-gradient(90deg,#4C86CC,#2F6DB5);border-radius:4px}}
.bar-val{{width:52px;text-align:right;color:#728096}}
.note{{background:#FFF8EC;border:1px solid #F2DDB4;color:#7A5A1E;padding:9px 12px;
 border-radius:7px;font-size:11.5px;margin-top:12px;line-height:1.7}}
footer{{color:#8A94A0;font-size:10.5px;text-align:center;padding:8px}}
</style></head><body>
<header><h1>基于 Spark 的招聘岗位技能挖掘与趋势预测系统</h1>
<p>数据看板 · 数据源：Kaggle LinkedIn Job Postings (2023–2024) + 日级抓取快照 2023-03 ~ 2026-11 · 更新于 2026-11-14</p></header>
<div class="wrap">
<div class="kpis">
<div class="kpi"><b>123,849</b><span>LinkedIn 真实岗位</span></div>
<div class="kpi"><b>{n_labels}</b><span>平台技能标注</span></div>
<div class="kpi"><b>50,867</b><span>去重后日级岗位</span></div>
<div class="kpi"><b>36</b><span>有效观测月</span></div>
<div class="kpi"><b>{best_f1:.4f}</b><span>E1 最优 F1</span></div>
<div class="kpi"><b>Spark 4.3</b><span>分布式计算引擎</span></div>
</div>
<div class="grid">
<div class="card"><h2>技术技能热度演化（真实月度序列）</h2>{line_chart(heat)}</div>
<div class="card"><h2>平台技能标注分布 Top-12</h2>{bar_chart(dist, 12)}</div>
</div>
<div class="grid">
<div class="card"><h2>E1 技能抽取方法对比</h2>{table(e1[['方法','Precision','Recall','F1','单条耗时(ms)']])}
<h2 style="margin-top:18px">E3 趋势预测模型对比（全体样本）</h2>{table(mt_all)}</div>
<div class="card"><h2>技能共现规则（提升度 Top-6）</h2>{table(rules)}
<h2 style="margin-top:18px">E4 分布式加速比</h2>{e4_html}
<div class="note">说明：所有数值均由本机真实公开数据实跑产出。数据源 A 的时间字段仅跨约 5 个月，
故趋势预测由长周期数据源 B 承担；抓取密度不均导致部分月份样本较少，结论适用范围见论文 5.8。</div></div>
</div>
<footer>本科毕业设计 · 数据科学与大数据技术 · 实验环境 Python 3.13 / PySpark 4.3 / JDK 17</footer>
</div></body></html>"""
    out = EVIDENCE / "system_dashboard.html"
    out.write_text(html_doc, encoding="utf-8")
    print(f"  [页面] {out}")
    return out


if __name__ == "__main__":
    main()

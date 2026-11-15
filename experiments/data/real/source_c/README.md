# 数据源 C 目录

`jobs_c.example.csv` 是 **3 条完全虚构的演示岗位**，仅用于让 C 源看板、岗位检索、
采集库初始化等链路可以在没有真实数据的情况下跑通。

真实采集产物（不公开）：

| 文件 | 说明 |
|---|---|
| `raw_c.jsonl` | 5,432 条原始岗位卡片流水，逐次抓取留痕 |
| `jobs_c.csv` | 按 `(site, job_id)` 去重后的 2,001 条归一化岗位 |
| `attempted_c.jsonl` | 已尝试检索词（含 0 卡冷门词），用于断点续跑 |

字段与清洗规则见 `experiments/system/source_c.py`；E7 统计口径见
`experiments/results/e7_source_c_overview.csv`。合规边界见根目录 `NOTICE.md` 第 3 节。

# 原始数据目录

本目录**不含任何原始数据**。A / B 源受各自许可约束、C 源含第三方内容与个人信息，
均不在公开仓库分发，详见根目录 `NOTICE.md`。

仓库内保留目录结构是为了让实验脚本的相对路径可直接工作：

- `experiments/data/real/` → A 源 CSV、B 源 `daily/*.parquet`（自行按许可下载）
- `experiments/data/real/source_c/` → C 源采集产物；已提供 `jobs_c.example.csv` 虚构示例

聚合后的实验结果在 `experiments/results/`，图表在 `figures/`。

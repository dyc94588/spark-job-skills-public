# 数据来源、许可与合规声明

本仓库**只分发代码、聚合统计结果与自产图表/截图**，不分发任何原始岗位数据集。
各数据源的权利归原作者与平台所有，使用请自行遵守其许可与网站条款。

## 1. 数据源 A（主实验 E1 / E2）

- 名称：LinkedIn Job Postings (2023–2024)，作者 Arsh Koneru (arshkon)
- 来源：<https://www.kaggle.com/datasets/arshkon/linkedin-job-postings>
- 许可：**CC BY-SA 4.0**（署名 + 相同方式共享，具传染性）
- 本仓库内容：仅 `experiments/results/e0_*.csv`、`e1_*.csv`、`e2_*.csv` 等聚合/评测结果；**不含 postings.csv、job_skills.csv、mappings 原始文件**

## 2. 数据源 B（趋势预测 E3 / 批处理 E4）

- 名称：linkedin-job-scrape（日级抓取快照），作者 ryang2
- 来源：<https://hf-mirror.com/datasets/ryang2/linkedin-job-scrape>
- 许可：**OpenRAIL-M**（bigscience-openrail-m，含使用限制）
- 本仓库内容：仅 `e3_*.csv`、`e4_*.csv` 等结果；**不含日级 parquet / 原始 CSV**

## 3. 数据源 C（时效性验证 E7 / 系统实时看板）

- 来源：猎聘 <https://www.liepin.com/>、前程无忧 <https://we.51job.com/> 的公开岗位列表页
- 规模：2026-11-15 采集，去重后 2,001 条（猎聘 953 / 前程无忧 1,048），原始卡片 5,432 条
- 本仓库内容：仅 E7 聚合结果、图表、界面截图，以及 3 条**完全虚构**的 `jobs_c.example.csv`
- **不公开**：`raw_c.jsonl` 原始卡片、`jobs_c.csv` 全量岗位表。原因：包含第三方平台内容、企业名称、岗位 URL 与猎头顾问姓名等个人信息，公开再分发既无授权也无必要。
- 字段偏差已如实披露：`city` 约 300 条为「姓名+职位」错位；`jobArea` 为访问端默认地区标签（前程无忧 1,037/1,048 为「佛山」），不代表岗位真实工作地，不作地区差异推断。

### 采集合规边界

- 仅渲染正常访客可见的公开搜索结果页，只采岗位列表字段，**不采个人信息**；
- **不登录、不处理验证码、不绕过任何 WAF / 风控 / 反爬机制**；BOSS 直聘（需登录）、智联招聘（人机验证）、拉勾（滑块验证）均未采集；
- 逐次限速、单线程、不并发；猎聘返回「行为异常，请进行安全验证」后**立即停止该站采集**，不换入口、不重试绕过；
- 连续多组空结果主动停止；所有尝试词与原始卡片留痕用于学术溯源；
- 用途限于本科毕业设计的学术可行性验证，**不得用于商业抓取或任何违反目标站点条款的用途**。

## 4. 仓库自产内容

- `figures/*.png`：本项目脚本生成，随代码按 MIT 许可使用；
- `screenshots/*.png`：本系统真实运行截图，含 ECharts 渲染的聚合图表；
- 截图与图表中不展示真实账号口令；演示账号口令仅为占位值。

## 5. 第三方组件

| 组件 | 许可 | 用途 |
|---|---|---|
| Apache ECharts（`experiments/system/static/echarts.min.js`） | Apache-2.0 | 前端可视化 |
| Flask / PySpark / pandas / scikit-learn / LightGBM / PyMySQL | 各自开源许可 | 见 `experiments/requirements.txt` |
| Google Chrome / Chromium | 各自许可 | CDP 渲染与截图（不随仓库分发） |

## 6. 论文相关

本仓库**不含论文正文（Word/PDF）、答辩 PPT 与实验记录表**。论文著作权与公开时机由作者及所在院系规定决定；如需公开请先征得指导教师同意。

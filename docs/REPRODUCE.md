# 完整复现步骤

## 0. 环境

- Python 3.13（3.10+ 应可用）、JDK 17（PySpark 需要）
- 可选：MySQL 8.0.x；不装也能跑（默认 SQLite）
- Chrome / Chromium（仅 C 源采集与截图脚本需要）

```bash
pip install -r experiments/requirements.txt
```

## 1. 取得 A / B 原始数据（自行按许可获取，本仓库不分发）

| 源 | 地址 | 放置位置 |
|---|---|---|
| A | <https://www.kaggle.com/datasets/arshkon/linkedin-job-postings> | `experiments/data/real/`：`postings_full.csv`、`job_skills.csv`、`mappings_skills.csv` |
| B | <https://hf-mirror.com/datasets/ryang2/linkedin-job-scrape> | `experiments/data/real/daily/` 下日级 parquet |

文件名与字段口径见 `experiments/real_pipeline/rcommon.py` 的 `SOURCE_A` / `SOURCE_B`。
缺文件时脚本会给出下载提示，**不会静默回退到合成数据**。

## 2. 跑实验管线

```bash
python experiments/real_pipeline/run_real.py            # 全流程（E4 含 1000 万行）
python experiments/real_pipeline/run_real.py --quick   # E4 只跑 10 万 / 100 万两档
```

输出：`experiments/results/*.csv`、`figures/*.png`。
单独重跑 C 源统计：`python experiments/real_pipeline/r7_source_c.py`
（仓库已含 2,001 条口径的 E7 结果 CSV，无需原始数据即可查看）。

## 3. 初始化并启动系统

```bash
python experiments/system/init_db.py
python experiments/system/app.py
# http://127.0.0.1:5000
```

C 源看板（演示样本，公开仓库已自动自举，无需手动执行）：

```bash
# 可选：换成自己的合规采集数据时才需要
cp experiments/data/real/source_c/jobs_c.example.csv experiments/data/real/source_c/jobs_c.csv
rm experiments/system/data/source_c.db         # 下次访问自动重建
python experiments/system/init_source_c_db.py # 仅 MySQL 后端需要
```

## 4. 可选：切换到 MySQL 8

```bash
cp experiments/system/mysql_config.example.json experiments/system/mysql_config.json
# 编辑上面的文件，填入本机账号与口令；或改用环境变量 THESIS_MYSQL_PASSWORD
python experiments/system/init_mysql_db.py
python experiments/system/mysql_experiment.py
THESIS_DB_BACKEND=mysql python experiments/system/app.py
```

E6 的导入 / 完整性 / 索引 / 并发 / 安全五组凭证见 `experiments/results/e6_*.csv`。

## 5. 可选：自建合规采集 C 源

见 `NOTICE.md` 第 3 节的合规边界。主脚本：

```bash
python experiments/crawler/probe_sites.py          # 可达性探测
python experiments/crawler/crawl_sites_c.py        # 主采集
python experiments/crawler/crawl_expand_2k.py --target 2000   # 关键词维度扩样
```

**不要修改脚本去绕过登录、验证码或风控**；遇到安全验证请停止该站采集。

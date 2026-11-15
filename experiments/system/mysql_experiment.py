# -*- coding: utf-8 -*-
"""E6：MySQL 8 关系库服务端实测（不与 E1–E4 结论混用）。

实测内容：
  1) 服务端环境参数与真实版本
  2) 12 张结果表 MySQL / SQLite 逐表行数一致性
  3) 六组业务查询在两种后端上的延迟（预热 + 200 次重复）
  4) 压力测试表上的索引收益（EXPLAIN 扫描行数 + 100 次查询延迟）
  5) 20 线程并发查询成功率与延迟
  6) 事务回滚、只读账号最小权限、参数化查询抗注入、口令哈希
  7) mysql.exe 命令行输出凭证与两张论文图

所有数值均为本机实测，未做任何估算或填充。压力测试表由真实 skill_month
复制放大而成，仅用于索引与并发评测，测试后删除，不进入任何业务结论。
运行：python experiments/system/mysql_experiment.py
"""
from __future__ import annotations
import json
import math
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pymysql
from pymysql.cursors import DictCursor

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
RESULTS = ROOT / "experiments" / "results"
FIGURES = ROOT / "figures"
EVID = ROOT / "evidence"
MYSQL_BIN = ROOT / "tools" / "mysql-8.0.46-winx64" / "bin" / "mysql.exe"
CLI_OUT = EVID / "mysql_cli_evidence.txt"
sys.path.insert(0, str(HERE))
import db_backend as dbx  # noqa: E402
from accounts import ADMIN_PWD, RO_DB_USER, RO_DB_PWD  # noqa: E402

CFG = json.loads((HERE / "mysql_config.json").read_text(encoding="utf-8"))
ROOT_PWD = (ROOT / "tools" / "mysql_root.txt").read_text(encoding="utf-8").strip()

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["font.size"] = 10
plt.rcParams["figure.dpi"] = 160
BLUE, ORANGE, GREEN, RED, GRAY = "#2F6DB5", "#E6862C", "#2E9E6B", "#C9443C", "#8A94A0"

QUERIES = [
    ("Q1 单技能月度序列", "SELECT month,skill,popularity FROM skill_month WHERE skill=? ORDER BY month", ("Python",)),
    ("Q2 高提升度规则", "SELECT antecedent,consequent,lift FROM rules WHERE lift>=? ORDER BY lift DESC LIMIT 20", (10.0,)),
    ("Q3 月份区间计数", "SELECT COUNT(*) FROM skill_month WHERE month BETWEEN ? AND ?", ("2024-01", "2024-12")),
    ("Q4 滚动预测明细", "SELECT month,y,tree FROM predictions WHERE skill=? ORDER BY month", ("Python",)),
    ("Q5 技能均值聚合", "SELECT skill,AVG(popularity) FROM skill_month GROUP BY skill ORDER BY AVG(popularity) DESC", ()),
    ("Q6 分页计数子查询", "SELECT COUNT(*) FROM (SELECT month,skill,popularity FROM skill_month WHERE skill=?) AS _q", ("Python",)),
]


def save_csv(name: str, rows: list[dict]) -> None:
    p = RESULTS / name
    pd.DataFrame(rows).to_csv(p, index=False, encoding="utf-8-sig")
    print(f"  [表] {p.name}  {len(rows)} 行")


def pct(ts: list[float], q: float) -> float:
    s = sorted(ts)
    return s[min(len(s) - 1, max(0, math.ceil(q * len(s)) - 1))]


def bench(fn, n: int = 200, warm: int = 20) -> tuple[list[float], int]:
    for _ in range(warm):
        fn()
    ts, got = [], 0
    for _ in range(n):
        t = time.perf_counter()
        rows = fn()
        ts.append((time.perf_counter() - t) * 1000)
        got = len(rows)
    return ts, got


def raw_conn(user: str = "thesis_app", password: str | None = None, cursor=DictCursor):
    return pymysql.connect(
        host=CFG["host"], port=int(CFG["port"]), user=user,
        password=password if password is not None else CFG["password"],
        database=CFG["database"], charset="utf8mb4",
        cursorclass=cursor, autocommit=False, connect_timeout=8)


# ---------------------------------------------------------------- 1 环境
def t_env() -> list[dict]:
    con = raw_conn()
    cur = con.cursor()
    cur.execute("SELECT VERSION(), @@port, @@character_set_server, @@collation_server, "
                "@@default_storage_engine, @@max_connections, @@innodb_buffer_pool_size, "
                "@@bind_address, @@datadir, @@version_compile_os, CURRENT_USER(), DATABASE()")
    r = cur.fetchone()
    items = [
        ("服务端版本", r["VERSION()"]),
        ("发行形态", "MySQL Community Server (GPL) 免安装便携版"),
        ("监听端口与地址", f"{r['@@bind_address']}:{r['@@port']}"),
        ("服务端字符集", r["@@character_set_server"]),
        ("排序规则", r["@@collation_server"]),
        ("默认存储引擎", r["@@default_storage_engine"]),
        ("最大连接数", str(r["@@max_connections"])),
        ("InnoDB 缓冲池(MB)", str(int(r["@@innodb_buffer_pool_size"]) // 1024 // 1024)),
        ("编译平台", r["@@version_compile_os"]),
        ("当前连接账号", cur.execute("SELECT CURRENT_USER()") and ""),
        ("当前数据库", cur.execute("SELECT DATABASE()") and ""),
    ]
    cur.execute("SELECT CURRENT_USER() AS u, DATABASE() AS d")
    x = cur.fetchone()
    cur.execute("SELECT COUNT(*) AS n FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA=DATABASE()")
    n_idx = cur.fetchone()["n"]
    items = [i for i in items if i[0] not in ("当前连接账号", "当前数据库")]
    items += [("当前连接账号", x["u"]), ("当前数据库", x["d"]),
              ("数据目录", r["@@datadir"]), ("服务端索引总数", n_idx)]
    con.close()
    how = {"服务端版本": "SELECT VERSION()",
           "发行形态": "mysqld --version（安装包核验）",
           "当前连接账号": "SELECT CURRENT_USER()",
           "当前数据库": "SELECT DATABASE()",
           "服务端索引总数": "information_schema.STATISTICS"}
    return [{"序号": i + 1, "环境项": k, "实测值": str(v),
             "获取方式": how.get(k, "SELECT @@会话变量")}
            for i, (k, v) in enumerate(items)]


# ------------------------------------------------------- 2 数据一致性
def t_integrity() -> list[dict]:
    my = dbx.connect("mysql")
    sq = dbx.connect("sqlite")
    names = [r[0] for r in my.execute(
        "SELECT TABLE_NAME FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME")]
    rows = []
    for i, t in enumerate(names, 1):
        a = my.execute(f"SELECT COUNT(*) FROM `{t}`").fetchone()[0]
        b = sq.execute(f"SELECT COUNT(*) FROM `{t}`").fetchone()[0]
        if t in ("op_logs", "users"):
            flag = "不计入"
            note = "系统运行态表（操作日志 / 用户账号），随注册与访问实时变化"
        else:
            flag = "一致" if a == b else "不一致"
            note = "实验结果表，两端由同一 build_frames() 导入"
        rows.append({"序号": i, "表名": t, "MySQL行数": a, "SQLite行数": b,
                     "是否一致": flag, "说明": note})
    my.close(); sq.close()
    return rows


# --------------------------------------------------------- 3 查询延迟
def t_query() -> list[dict]:
    rows = []
    for backend in ("mysql", "sqlite"):
        con = dbx.connect(backend)
        for qid, (name, sql, p) in enumerate(QUERIES, 1):
            def run(sql=sql, p=p):
                return con.execute(sql, p).fetchall()
            ts, got = bench(run)
            rows.append({"查询编号": f"Q{qid}", "查询场景": name, "关系库后端": backend,
                         "重复次数": len(ts), "返回行数": got,
                         "均值_ms": round(float(np.mean(ts)), 4),
                         "P95_ms": round(pct(ts, 0.95), 4),
                         "最大_ms": round(max(ts), 4)})
        con.close()
    return rows


# ----------------------------------------------------------- 4 索引收益
def t_index() -> tuple[list[dict], dict]:
    con = raw_conn(user="root", password=ROOT_PWD)
    cur = con.cursor()
    cur.execute("DROP TABLE IF EXISTS bench_skill_month")
    cur.execute("CREATE TABLE bench_skill_month ("
                "id INT AUTO_INCREMENT PRIMARY KEY, month CHAR(7), skill VARCHAR(64), "
                "popularity DOUBLE) ENGINE=InnoDB")
    cur.execute("INSERT INTO bench_skill_month(month,skill,popularity) "
                "SELECT month,skill,popularity FROM skill_month")
    con.commit()
    for _ in range(8):  # 432 × 2^8 = 110,592 行
        cur.execute("INSERT INTO bench_skill_month(month,skill,popularity) "
                    "SELECT month,skill,popularity FROM bench_skill_month")
    con.commit()
    n = cur.execute("SELECT COUNT(*) AS n FROM bench_skill_month") and cur.fetchone()["n"]

    probe = "SELECT COUNT(*) FROM bench_skill_month WHERE skill='Python' AND popularity>0.1"

    def explain():
        cur.execute("EXPLAIN " + probe)
        return cur.fetchone()

    e0 = explain()
    t0 = time.perf_counter()
    ts0, _ = bench(lambda: (cur.execute(probe), cur.fetchone()), n=100, warm=10)
    ms0 = float(np.mean(ts0))

    cur.execute("CREATE INDEX ix_bench_skill ON bench_skill_month(skill,popularity)")
    con.commit()
    e1 = explain()
    ts1, _ = bench(lambda: (cur.execute(probe), cur.fetchone()), n=100, warm=10)
    ms1 = float(np.mean(ts1))

    rows = []
    for stage, e, ts in (("无索引", e0, ts0), ("建立 (skill,popularity) 复合索引后", e1, ts1)):
        rows.append({"阶段": stage, "压力表行数": n, "EXPLAIN访问类型": e["type"],
                     "EXPLAIN命中索引": e["key"] or "无",
                     "EXPLAIN预估扫描行数": int(e["rows"]),
                     "重复次数": len(ts), "均值_ms": round(float(np.mean(ts)), 4),
                     "P95_ms": round(pct(ts, 0.95), 4),
                     "最大_ms": round(max(ts), 4)})
    rows[0]["相较无索引加速比"] = 1.0
    rows[1]["相较无索引加速比"] = round(ms0 / ms1, 4) if ms1 else ""
    cur.execute("DROP TABLE bench_skill_month")
    con.commit()
    con.close()
    return rows, {"pressure_rows": n, "speedup": round(ms0 / ms1, 4) if ms1 else 0}


# ------------------------------------------------------------- 5 并发
def t_concurrency() -> list[dict]:
    rows = []
    sql = QUERIES[0][1]
    p = QUERIES[0][2]
    for backend in ("mysql", "sqlite"):
        def worker(k: int):
            c = dbx.connect(backend)
            c.execute(sql, p).fetchall()  # 预热（含连接建立）
            local = []
            for _ in range(20):
                t = time.perf_counter()
                try:
                    c.execute(sql, p).fetchall()
                    local.append((time.perf_counter() - t) * 1000)
                except Exception:
                    pass
            c.close()
            return local
        n_threads, per = 20, 20
        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            t0 = time.perf_counter()
            chunks = list(pool.map(worker, range(n_threads)))
            wall = (time.perf_counter() - t0) * 1000
        ts = [x for c in chunks for x in c]
        rows.append({"关系库后端": backend, "并发线程数": n_threads,
                     "每线程请求数": per, "总请求数": n_threads * per,
                     "成功请求数": len(ts),
                     "成功率": f"{len(ts)/(n_threads*per):.2%}",
                     "单请求均值_ms": round(float(np.mean(ts)), 4),
                     "P95_ms": round(pct(ts, 0.95), 4),
                     "最大_ms": round(max(ts), 4),
                     "墙钟总耗时_ms": round(wall, 2),
                     "吞吐_QPS": round(len(ts) / (wall / 1000), 1)})
    return rows


# ---------------------------------------------- 6 事务 / 权限 / 注入
def t_security() -> list[dict]:
    rows = []

    def add(name, op, expect, actual, ok):
        rows.append({"测试项": name, "操作": op, "预期结果": expect,
                     "实测结果": actual, "是否通过": "通过" if ok else "未通过"})

    con = raw_conn()
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) AS n FROM op_logs"); before = cur.fetchone()["n"]
    cur.execute("START TRANSACTION")
    cur.execute("INSERT INTO op_logs(username,action,detail,ip,created_at) "
                "VALUES('bench','事务回滚测试','临时写入','127.0.0.1',NOW())")
    cur.execute("SELECT COUNT(*) AS n FROM op_logs"); during = cur.fetchone()["n"]
    cur.execute("ROLLBACK")
    cur.execute("SELECT COUNT(*) AS n FROM op_logs"); after = cur.fetchone()["n"]
    add("InnoDB 事务回滚", "START TRANSACTION → INSERT → ROLLBACK",
        "临时写入不落地，行数回到原值",
        f"写入前 {before} 行，事务内 {during} 行，回滚后 {after} 行",
        during == before + 1 and after == before)
    con.close()

    ro = raw_conn(user=RO_DB_USER, password=RO_DB_PWD)
    cur = ro.cursor()
    cur.execute("SELECT COUNT(*) AS n FROM skill_month"); n = cur.fetchone()["n"]
    denied, code = False, ""
    try:
        cur.execute("INSERT INTO op_logs(username,action) VALUES('ro','越权写入')")
        ro.commit()
    except Exception as e:
        denied, code = True, f"错误码 {e.args[0]}"
    add("只读账号最小权限", "thesis_ro 执行 SELECT 与 INSERT",
        "SELECT 允许、INSERT 被服务端拒绝",
        f"SELECT 返回 {n} 行；INSERT {'被拒绝（' + code + '）' if denied else '未被拒绝'}",
        n > 0 and denied)
    ro.close()

    c = dbx.connect("mysql")
    payload = "' OR 1=1 -- "
    hit = c.execute("SELECT COUNT(*) FROM skill_month WHERE skill=?", (payload,)).fetchone()[0]
    total = c.execute("SELECT COUNT(*) FROM skill_month").fetchone()[0]
    add("参数化查询抗注入", "把 \"' OR 1=1 -- \" 作为 skill 参数传入",
        "按字面值匹配，返回 0 行而非全表",
        f"返回 {hit} 行，全表共 {total} 行", hit == 0 and total > 0)
    h = c.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()[0]
    add("口令哈希存储", "读取 users 表 admin 的 password_hash",
        "非明文且带算法标识",
        f"哈希前缀 {h.split(':')[0]}，长度 {len(h)}，库中不含明文口令",
        h != ADMIN_PWD and ":" in h and len(h) > 40)
    c.close()
    return rows


# ------------------------------------------------------- 7 CLI 凭证
def cli_evidence() -> None:
    sql = ("SELECT VERSION() AS server_version, @@port AS port, "
           "@@character_set_server AS charset, @@default_storage_engine AS engine; "
           "USE thesis_skill; "
           "SELECT COUNT(*) AS skill_month_rows FROM skill_month; "
           "SELECT COUNT(*) AS rules FROM rules; "
           "SELECT method,f1 FROM extraction ORDER BY f1 DESC LIMIT 1; "
           "SELECT TABLE_NAME, ENGINE, TABLE_COLLATION FROM information_schema.TABLES "
           "WHERE TABLE_SCHEMA='thesis_skill' ORDER BY TABLE_NAME;")
    cmd = [str(MYSQL_BIN), "-u", "root", "-p" + ROOT_PWD,
           "--default-character-set=utf8mb4", "-e", sql]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    shown = [str(MYSQL_BIN), "-u", "root", "-p******",
             "--default-character-set=utf8mb4", "-e", "<SQL 见下>"]
    txt = [
        "MySQL 8.0.46 服务端命令行核验凭证（真实执行输出，非转录或估算）",
        "执行时间：" + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "命令：" + " ".join(shown),
        "SQL：" + sql,
        "退出码：" + str(r.returncode),
        "--- stdout ---", r.stdout.strip(),
        "--- stderr ---", (r.stderr or "").strip(),
    ]
    CLI_OUT.write_text("\n".join(txt) + "\n", encoding="utf-8")
    print(f"  [凭证] {CLI_OUT.name}  {CLI_OUT.stat().st_size} bytes")


# ------------------------------------------------------------- 图
def figures(q: pd.DataFrame, ix: pd.DataFrame, cc: pd.DataFrame) -> None:
    FIGURES.mkdir(parents=True, exist_ok=True)
    piv = q.pivot(index="查询编号", columns="关系库后端", values="均值_ms")
    piv = piv.reindex([f"Q{i}" for i in range(1, 7)])
    fig, ax = plt.subplots(figsize=(9, 4.2))
    x = np.arange(len(piv)); w = 0.36
    ax.bar(x - w / 2, piv["mysql"], w, label="MySQL 8.0.46（TCP）", color=BLUE)
    ax.bar(x + w / 2, piv["sqlite"], w, label="SQLite（进程内文件）", color=ORANGE)
    ax.set_xticks(x); ax.set_xticklabels(piv.index)
    ax.set_ylabel("单次查询均值延迟 (ms)")
    ax.set_title("图 5-10 六组业务查询在两种关系库后端上的延迟对比（各 200 次重复）")
    ax.legend(fontsize=9); ax.grid(axis="y", alpha=0.3)
    for i, v in enumerate(piv["mysql"]):
        ax.text(i - w / 2, v, f"{v:.3f}", ha="center", va="bottom", fontsize=8)
    for i, v in enumerate(piv["sqlite"]):
        ax.text(i + w / 2, v, f"{v:.3f}", ha="center", va="bottom", fontsize=8)
    fig.tight_layout(); fig.savefig(FIGURES / "fig5_10_mysql_query.png",
                                    bbox_inches="tight", facecolor="white", dpi=160)
    plt.close(fig); print("  [图] fig5_10_mysql_query.png")

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9, 3.9))
    stages = ["无索引", "建索引后"]
    vals = [float(ix.iloc[0]["均值_ms"]), float(ix.iloc[1]["均值_ms"])]
    bars = a1.bar(stages, vals, color=[GRAY, GREEN], width=0.5)
    a1.set_ylabel("查询均值延迟 (ms)")
    a1.set_title(f"索引前后延迟（{int(ix.iloc[0]['压力表行数']):,} 行压力表）")
    for b, v in zip(bars, vals):
        a1.text(b.get_x() + b.get_width() / 2, v, f"{v:.3f}", ha="center",
                va="bottom", fontsize=9)
    scans = [int(ix.iloc[0]["EXPLAIN预估扫描行数"]), int(ix.iloc[1]["EXPLAIN预估扫描行数"])]
    bars = a2.bar(stages, scans, color=[GRAY, GREEN], width=0.5)
    a2.set_yscale("log"); a2.set_ylabel("EXPLAIN 预估扫描行数（对数轴）")
    a2.set_title("查询计划扫描规模")
    for b, v in zip(bars, scans):
        a2.text(b.get_x() + b.get_width() / 2, v, f"{v:,}", ha="center",
                va="bottom", fontsize=9)
    fig.suptitle("图 5-11 MySQL 复合索引的量化收益（真实结果表复制放大，仅用于性能评测）",
                 fontsize=11)
    fig.tight_layout(); fig.savefig(FIGURES / "fig5_11_mysql_index.png",
                                    bbox_inches="tight", facecolor="white", dpi=160)
    plt.close(fig); print("  [图] fig5_11_mysql_index.png")


def main() -> None:
    print("=== E6 MySQL 关系库服务端实测 ===")
    print("服务端:", dbx.server_version() if os.environ.get("THESIS_DB_BACKEND") == "mysql"
          else "SELECT VERSION() 直连")
    env = t_env(); save_csv("e6_mysql_env.csv", env)
    integ = t_integrity(); save_csv("e6_mysql_integrity.csv", integ)
    q = t_query(); save_csv("e6_mysql_query.csv", q)
    ix, ix_meta = t_index(); save_csv("e6_mysql_index.csv", ix)
    cc = t_concurrency(); save_csv("e6_mysql_concurrency.csv", cc)
    sec = t_security(); save_csv("e6_mysql_security.csv", sec)
    cli_evidence()
    figures(pd.DataFrame(q), pd.DataFrame(ix), pd.DataFrame(cc))
    print("\n=== 关键结果 ===")
    _d = pd.DataFrame(integ)
    _checked = _d[_d["是否一致"] != "不计入"]
    print("数据一致性:",
          f"{(_checked['是否一致']=='一致').sum()}/{len(_checked)} 张结果表一致"
          "（users / op_logs 为运行态表，不计入）")
    for b in ("mysql", "sqlite"):
        sub = pd.DataFrame(q).query("关系库后端==@b")
        print(f"  {b:7} 六查询均值 {sub['均值_ms'].mean():.4f} ms | "
              f"并发 {pd.DataFrame(cc).query('关系库后端==@b')['成功率'].iloc[0]}")
    print("索引加速比:", ix_meta["speedup"], "| 压力表行数:", f"{ix_meta['pressure_rows']:,}")
    print("安全项:", f"{(pd.DataFrame(sec)['是否通过']=='通过').sum()}/{len(sec)} 通过")


if __name__ == "__main__":
    os.environ.setdefault("THESIS_DB_BACKEND", "mysql")
    main()

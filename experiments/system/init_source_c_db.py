# -*- coding: utf-8 -*-
"""把数据源 C（2,001 条真实爬取岗位）导入独立采集库，SQLite / MySQL 双后端。

结果库 thesis_skill（12 表 / 1,465 行）不受影响；本脚本只操作
  - experiments/system/data/source_c.db
  - MySQL 8.0.46 的 thesis_crawl 库
输出导入凭证：experiments/results/e7_source_c_db.csv
运行：python experiments/system/init_source_c_db.py
"""
from __future__ import annotations
import csv
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RES = ROOT / "experiments" / "results"

import source_c as sc


def pyval(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            pass
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def split_sql(text: str) -> list[str]:
    out, buf = [], []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("--"):
            continue
        buf.append(line)
        if s.endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
    return out


def root_pwd() -> str:
    p = os.environ.get("MYSQL_ROOT_PWD")
    if p:
        return p
    f = ROOT / "tools" / "mysql_root.txt"
    if f.exists():
        return f.read_text(encoding="utf-8").strip()
    raise SystemExit("缺少 MySQL root 口令：设置 MYSQL_ROOT_PWD 或 tools/mysql_root.txt")


def init_sqlite(frames: dict[str, pd.DataFrame]) -> list[dict]:
    import sqlite3
    sc.SQLITE_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(sc.SQLITE_DB)
    con.executescript(sc.DDL_SQLITE)
    out = []
    for t, df in frames.items():
        df.to_sql(t, con, if_exists="append", index=False)
        got = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        assert got == len(df), f"{t}: 写入 {len(df)} 行，回读 {got} 行"
        out.append({"关系库后端": "SQLite", "采集库": "data/source_c.db",
                    "表名": t, "导入行数": got, "库内校验行数": got})
    con.commit()
    con.close()
    print("SQLite 采集库:", sc.SQLITE_DB)
    return out


def init_mysql(frames: dict[str, pd.DataFrame]) -> list[dict]:
    import pymysql
    from pymysql.cursors import Cursor
    cfg = sc.dbx.mysql_conf()
    con = pymysql.connect(host=cfg["host"], port=int(cfg["port"]), user="root",
                          password=root_pwd(), charset="utf8mb4", autocommit=True)
    cur = con.cursor()
    print("MySQL 服务端:", cur.execute("SELECT VERSION()") and cur.fetchone()[0])
    for s in split_sql(sc.DDL_MYSQL):
        cur.execute(s)
    cur.execute("USE thesis_crawl")
    out = []
    for t, df in frames.items():
        data = [tuple(pyval(v) for v in row)
                for row in df.itertuples(index=False, name=None)]
        cols = list(df.columns)
        sql = (f"INSERT INTO `{t}` (" + ",".join(f"`{c}`" for c in cols)
               + ") VALUES (" + ",".join(["%s"] * len(cols)) + ")")
        if data:
            cur.executemany(sql, data)
        con.commit()
        got = cur.execute(f"SELECT COUNT(*) FROM `{t}`") and cur.fetchone()[0]
        assert got == len(data), f"{t}: 写入 {len(data)} 行，回读 {got} 行"
        out.append({"关系库后端": "MySQL 8.0.46", "采集库": "thesis_crawl",
                    "表名": t, "导入行数": got, "库内校验行数": got})
        print(f"  {t:16} {got:>6,} 行")
    # 采集库只读授权：应用账号与只读账号均可查询，写入权限仅限初始化脚本。
    for host in ("localhost", "127.0.0.1"):
        cur.execute(f"GRANT SELECT ON thesis_crawl.* TO 'thesis_app'@'{host}'")
        cur.execute(f"GRANT SELECT ON thesis_crawl.* TO 'thesis_ro'@'{host}'")
    cur.execute("FLUSH PRIVILEGES")
    cur.execute("SELECT COUNT(*) FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA='thesis_crawl'")
    print("采集库索引数:", cur.fetchone()[0])
    con.close()
    return out


def main() -> None:
    frames = sc.build_frames()
    print("C 源岗位:", len(frames["jobs_c"]), "| 技能:", len(frames["skill_freq_c"]),
          "| 元信息:", len(frames["source_c_meta"]))
    rows = init_sqlite(frames)
    try:
        rows += init_mysql(frames)
    except Exception as e:
        print("[WARN] MySQL 采集库导入失败（SQLite 已可用）:", e)
        rows.append({"关系库后端": "MySQL 8.0.46", "采集库": "thesis_crawl",
                     "表名": "-", "导入行数": 0, "库内校验行数": 0})
    for r in rows:
        r["导入时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out = RES / "e7_source_c_db.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("导入凭证:", out)


if __name__ == "__main__":
    sys.path.insert(0, str(HERE))
    main()

# -*- coding: utf-8 -*-
"""在 MySQL 8 服务端建库、建表并导入真实实验结果（init_db.py 的 MySQL 版本）。

前置：MySQL 8.0.46 便携版已启动（见 tools/README_mysql.md），
     root 口令来自环境变量 MYSQL_ROOT_PWD 或 tools/mysql_root.txt。
输入：schema_mysql.sql（DDL）+ init_db.build_frames()（真实结果 CSV）。
输出：thesis_skill 库 11 张表 + experiments/results/e6_mysql_import.csv 导入凭证。
运行：python experiments/system/init_mysql_db.py
"""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd
import pymysql

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
RES = ROOT / "experiments" / "results"
CFG = json.loads((HERE / "mysql_config.json").read_text(encoding="utf-8"))
DDL_FILE = HERE / "schema_mysql.sql"
ROOT_PWD_FILE = ROOT / "tools" / "mysql_root.txt"


def root_pwd() -> str:
    p = os.environ.get("MYSQL_ROOT_PWD")
    if p:
        return p
    if ROOT_PWD_FILE.exists():
        return ROOT_PWD_FILE.read_text(encoding="utf-8").strip()
    raise SystemExit("缺少 root 口令：设置 MYSQL_ROOT_PWD 或创建 tools/mysql_root.txt")


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


def pyval(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            pass
    if pd.isna(v):
        return None
    return v


def main() -> None:
    from init_db import build_frames, initial_users, TABLES
    t0 = time.perf_counter()
    con = pymysql.connect(
        host=CFG["host"], port=int(CFG["port"]), user="root",
        password=root_pwd(), charset="utf8mb4", autocommit=True)
    cur = con.cursor()
    print("服务端版本:", cur.execute("SELECT VERSION()") and cur.fetchone()[0])

    cur.execute("DROP DATABASE IF EXISTS thesis_skill")
    stmts = split_sql(DDL_FILE.read_text(encoding="utf-8"))
    for s in stmts:
        cur.execute(s)
    print("DDL 执行:", len(stmts), "条语句")
    cur.execute("USE thesis_skill")

    frames = build_frames()
    import_rows = []
    for table, df in frames.items():
        df = df.astype(object).where(pd.notna(df), None)
        cols = list(df.columns)
        col_sql = ",".join(f"`{c}`" for c in cols)
        ph = ",".join(["%s"] * len(cols))
        sql = f"INSERT INTO `{table}` ({col_sql}) VALUES ({ph})"
        data = [tuple(pyval(v) for v in row) for row in df.itertuples(index=False, name=None)]
        t = time.perf_counter()
        if data:
            cur.executemany(sql, data)
        con.commit()
        ms = (time.perf_counter() - t) * 1000
        got = cur.execute(f"SELECT COUNT(*) FROM `{table}`") and cur.fetchone()[0]
        assert got == len(data), f"{table}: 导入 {len(data)} 行但读到 {got} 行"
        import_rows.append({"表名": table, "字段数": len(cols), "导入行数": got,
                            "库内校验行数": got, "导入耗时_ms": round(ms, 3)})
        print(f"  {table:16} {got:>6,} 行  {ms:8.2f} ms")

    t = time.perf_counter()
    cur.executemany(
        "INSERT INTO users(username,email,password_hash,role,created_at) VALUES(%s,%s,%s,%s,%s)",
        initial_users())
    con.commit()
    n_user = cur.execute("SELECT COUNT(*) FROM users") and cur.fetchone()[0]
    import_rows.append({"表名": "users", "字段数": 5, "导入行数": n_user,
                        "库内校验行数": n_user,
                        "导入耗时_ms": round((time.perf_counter() - t) * 1000, 3)})
    import_rows.append({"表名": "op_logs", "字段数": 6, "导入行数": 0,
                        "库内校验行数": 0, "导入耗时_ms": 0.0})

    # 最小权限：应用账号可读写，报表账号只读；仅授权本库，仅本机回环。
    for host in ("localhost", "127.0.0.1"):
        cur.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON thesis_skill.* "
                    f"TO 'thesis_app'@'{host}'")
        cur.execute(f"GRANT SELECT ON thesis_skill.* TO 'thesis_ro'@'{host}'")
    cur.execute("FLUSH PRIVILEGES")

    total = sum(r["导入行数"] for r in import_rows)
    total_ms = (time.perf_counter() - t0) * 1000
    out = RES / "e6_mysql_import.csv"
    pd.DataFrame(import_rows).to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n导入完成：{len(import_rows)} 张表 / {total:,} 行 / 全程 {total_ms:.1f} ms")
    print("导入凭证:", out)
    con.close()


if __name__ == "__main__":
    sys.path.insert(0, str(HERE))
    main()

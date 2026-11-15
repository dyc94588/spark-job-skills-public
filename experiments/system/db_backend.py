# -*- coding: utf-8 -*-
"""关系库双后端适配层：让同一套 Flask 业务代码可跑 SQLite 或 MySQL 8。

通过环境变量 THESIS_DB_BACKEND 切换（sqlite / mysql，默认 sqlite）。
MySQL 连接参数读取同目录 mysql_config.json；本机配置文件中的口令仅用于
127.0.0.1 回环地址的课程实验，不用于任何生产环境。

设计原因：SQLite 用 ? 占位符与 sqlite3.Row，MySQL 用 %s 与 DictCursor。
本模块提供 CompatConnection / CompatRow，使上层 SQL 无需改写即可复用，
同时保证所有用户输入仍走参数化查询（防 SQL 注入）。
"""
from __future__ import annotations
import json
import os
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
SQLITE_DB = HERE / "data" / "thesis.db"
CONFIG = HERE / "mysql_config.json"


def backend() -> str:
    return os.environ.get("THESIS_DB_BACKEND", "sqlite").strip().lower()


def mysql_conf() -> dict:
    """优先读本机配置；未配置时回退到仓库内的示例文件。

    真实口令请通过环境变量 THESIS_MYSQL_PASSWORD 注入，不要写进任何被提交的文件。
    """
    src = CONFIG if CONFIG.exists() else HERE / "mysql_config.example.json"
    if not src.exists():
        raise SystemExit(f"缺少 MySQL 配置文件：{CONFIG}")
    conf = json.loads(src.read_text(encoding="utf-8"))
    pwd = os.environ.get("THESIS_MYSQL_PASSWORD")
    if pwd:
        conf["password"] = pwd
    return conf


class CompatRow:
    """同时支持 r[0] 与 r['col']，并可直接 dict(r)。"""

    def __init__(self, cols: list[str], vals: tuple):
        self._cols = cols
        self._vals = vals
        self._map = dict(zip(cols, vals))

    def __getitem__(self, k):
        return self._vals[k] if isinstance(k, int) else self._map[k]

    def __iter__(self):
        return iter(self._vals)

    def __len__(self):
        return len(self._vals)

    def keys(self):
        return self._cols

    def get(self, k, default=None):
        return self._map.get(k, default)


class CompatCursor:
    def __init__(self, cur):
        self._cur = cur

    @staticmethod
    def _wrap(cur, row):
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return CompatRow(cols, tuple(row))

    def fetchone(self):
        return self._wrap(self._cur, self._cur.fetchone())

    def fetchall(self):
        cur = self._cur
        cols = [d[0] for d in cur.description] if cur.description else []
        return [CompatRow(cols, tuple(r)) for r in cur.fetchall()]

    def __iter__(self):
        return self

    def __next__(self):
        row = self._cur.fetchone()
        if row is None:
            raise StopIteration
        return self._wrap(self._cur, row)

    def __getattr__(self, name):
        return getattr(self._cur, name)


class CompatConnection:
    """把 ? 占位符改写为 %s（MySQL），SQLite 原样透传。"""

    def __init__(self, raw, is_mysql: bool):
        self._raw = raw
        self.is_mysql = is_mysql

    def execute(self, sql: str, params=()):
        if self.is_mysql:
            sql = sql.replace("?", "%s")
        cur = self._raw.cursor()
        cur.execute(sql, tuple(params) if params else ())
        return CompatCursor(cur) if self.is_mysql else cur

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()

    def __getattr__(self, name):
        return getattr(self._raw, name)


def connect(backend_override: str | None = None) -> CompatConnection:
    """backend_override 仅用于实验脚本在同一进程内对比两种后端。"""
    b = (backend_override or os.environ.get("THESIS_DB_BACKEND", "sqlite")).strip().lower()
    if b == "mysql":
        import pymysql
        from pymysql.cursors import Cursor
        c = mysql_conf()
        raw = pymysql.connect(
            host=c.get("host", "127.0.0.1"),
            port=int(c.get("port", 3306)),
            user=c.get("user", "thesis_app"),
            password=c.get("password", ""),
            database=c.get("database", "thesis_skill"),
            charset=c.get("charset", "utf8mb4"),
            cursorclass=Cursor,
            autocommit=False,
            connect_timeout=8,
        )
        return CompatConnection(raw, True)
    SQLITE_DB.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(SQLITE_DB)
    raw.row_factory = sqlite3.Row
    return CompatConnection(raw, False)


def concat_expr() -> str:
    """字符串拼接表达式：MySQL 默认 || 是逻辑或，必须改 CONCAT。"""
    return "CONCAT(antecedent,' × ',consequent)" if backend() == "mysql" \
        else "antecedent||' × '||consequent"


def backend_label() -> str:
    if backend() != "mysql":
        return "SQLite（本地文件）"
    c = mysql_conf()
    return f"MySQL {c.get('server_version', '8.0')} · {c.get('database', 'thesis_skill')}"


def server_version() -> str:
    """读取服务端真实版本号，用于健康检查和论文环境表。"""
    if backend() != "mysql":
        return "SQLite " + sqlite3.sqlite_version
    con = connect()
    try:
        return con.execute("SELECT VERSION()").fetchone()[0]
    finally:
        con.close()

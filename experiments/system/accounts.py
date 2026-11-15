# -*- coding: utf-8 -*-
"""演示账号与数据库口令：统一从环境变量读取，仓库内不含任何真实凭据。

复制根目录 .env.example 为 .env 并按需修改；或在启动前导出对应变量。
未设置时使用仅适用于本机演示的占位值，切勿用于任何可公网访问的环境。
所有脚本一律从这里导入，保证「初始化哈希」与「登录测试」口径一致。
"""
import os

ADMIN_USER = os.environ.get("THESIS_ADMIN_USER", "admin")
ADMIN_PWD = os.environ.get("THESIS_ADMIN_PWD", "ChangeMe_Admin")
DEMO_USER = os.environ.get("THESIS_DEMO_USER", "demo")
DEMO_PWD = os.environ.get("THESIS_DEMO_PWD", "ChangeMe_Demo")

APP_DB_USER = os.environ.get("THESIS_MYSQL_USER", "thesis_app")
APP_DB_PWD = os.environ.get("THESIS_MYSQL_PASSWORD", "ChangeMe_DB")
RO_DB_USER = os.environ.get("THESIS_MYSQL_RO_USER", "thesis_ro")
RO_DB_PWD = os.environ.get("THESIS_MYSQL_RO_PASSWORD", "ChangeMe_RO")

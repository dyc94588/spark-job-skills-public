# -*- coding: utf-8 -*-
"""招聘岗位技能挖掘与趋势预测系统 · Flask 服务端

功能模块：注册/登录/登出、角色权限、多条件数据筛选、ECharts 看板、
          管理员后台（用户管理与操作日志）、CSV 报表导出、RESTful 接口。
数据来源：A/B 源 E1–E6 真实实验结果（关系库 thesis_skill / thesis.db），
          C 源 2,001 条自建爬虫实时岗位（独立采集库 thesis_crawl / source_c.db）。
          C 源由 source_c.py 访问，与结果库分离，避免混淆主实验口径。
后端切换：THESIS_DB_BACKEND=sqlite（默认）或 mysql；MySQL 8 参数见 mysql_config.json。
         两种后端共用同一套参数化 SQL，由 db_backend.py 完成占位符与行对象适配。
运行：python experiments/system/app.py  -> http://127.0.0.1:5000
"""
from __future__ import annotations
import csv
import io
import os
from datetime import datetime
from functools import wraps
from pathlib import Path
from urllib.parse import urlencode

from flask import (Flask, g, jsonify, redirect, render_template, request,
                   Response, session, url_for, flash)
from werkzeug.security import check_password_hash, generate_password_hash

import db_backend as dbx
import source_c as sc

HERE = Path(__file__).resolve().parent
DB = HERE / "data" / "thesis.db"

app = Flask(__name__)
app.secret_key = os.environ.get("THESIS_SECRET_KEY", "dev-only-change-me")
app.config["JSON_AS_ASCII"] = False

VIEWS = {
    "heat": "技能热度月度序列",
    "rules": "技能共现规则",
    "models": "预测模型对比",
    "extraction": "技能抽取评测",
    "clusters": "技能簇画像",
    "predictions": "滚动预测明细",
}
COLS = {
    "heat": [("month", "月份"), ("skill", "技能"), ("popularity", "热度")],
    "rules": [("antecedent", "前项"), ("consequent", "后项"), ("co_jobs", "共现岗位数"),
              ("support", "支持度"), ("conf_ab", "置信度(前→后)"),
              ("conf_ba", "置信度(后→前)"), ("lift", "提升度")],
    "models": [("model", "模型"), ("horizon", "步长h"), ("wape", "WAPE"),
               ("mape", "MAPE"), ("rmse", "RMSE"), ("samples", "样本点")],
    "extraction": [("method", "方法"), ("precision", "Precision"), ("recall", "Recall"),
                   ("f1", "F1"), ("ms_per_doc", "单条耗时(ms)"),
                   ("tp", "TP"), ("fp", "FP"), ("fn", "FN")],
    "clusters": [("cluster_id", "簇编号"), ("top_skills", "高权重技能"),
                 ("skill_cnt", "技能数"), ("job_cnt", "关联岗位数"),
                 ("avg_salary", "平均年薪(美元)")],
    "predictions": [("h", "步长h"), ("origin", "滚动原点"), ("month", "预测月份"),
                    ("skill", "技能"), ("y", "真实值"), ("naive", "朴素"),
                    ("ma", "移动平均"), ("trend", "Holt"), ("tree", "LightGBM")],
}


def get_db():
    if "db" not in g:
        g.db = dbx.connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(action: str, detail: str = "") -> None:
    db = get_db()
    db.execute("INSERT INTO op_logs(username,action,detail,ip,created_at) VALUES(?,?,?,?,?)",
               (session.get("username", "anonymous"), action, detail,
                request.remote_addr or "-", now()))
    db.commit()


def current_user():
    if "user" not in g:
        g.user = None
        uid = session.get("uid")
        if uid:
            g.user = get_db().execute(
                "SELECT * FROM users WHERE id=? AND status=1", (uid,)).fetchone()
    return g.user


@app.context_processor
def inject_user():
    return {"user": current_user(), "views": VIEWS,
            "db_backend": dbx.backend_label(),
            "db_is_mysql": dbx.backend() == "mysql"}


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if current_user() is None:
            if request.path.startswith("/api/"):
                return jsonify({"error": "未登录"}), 401
            return redirect(url_for("login", next=request.path))
        return fn(*a, **kw)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapper(*a, **kw):
        if current_user()["role"] != "admin":
            return "403 权限不足：该页面仅管理员可访问", 403
        return fn(*a, **kw)
    return wrapper


# ------------------------------ 筛选查询 ------------------------------
def build_query(view: str, args) -> tuple[str, list, str | None]:
    """按视图与筛选条件构造参数化 SQL；返回 (sql, params, 错误提示)。"""
    skill = (args.get("skill") or "").strip()
    kw = (args.get("keyword") or "").strip()
    m1 = (args.get("month_from") or "").strip()
    m2 = (args.get("month_to") or "").strip()
    if len(kw) > 60 or len(skill) > 60:
        return "", [], "查询关键词过长（上限 60 字符），请缩短后重试"
    if m1 and m2 and m1 > m2:
        return "", [], "起始月份不能晚于结束月份，请重新选择"
    try:
        float(args.get("min_lift") or 0)
        float(args.get("min_sup") or 0)
        float(args.get("min_pop") or 0)
    except ValueError:
        return "", [], "数值筛选条件不是合法数字，请重新输入"
    min_lift = float(args.get("min_lift") or 0)
    min_sup = float(args.get("min_sup") or 0)
    min_pop = float(args.get("min_pop") or 0)
    p: list = []
    if view == "heat":
        sql = "SELECT month,skill,popularity FROM skill_month WHERE 1=1"
        if skill:
            sql += " AND skill=?"; p.append(skill)
        if m1:
            sql += " AND month>=?"; p.append(m1)
        if m2:
            sql += " AND month<=?"; p.append(m2)
        if min_pop > 0:
            sql += " AND popularity>=?"; p.append(min_pop)
        sql += " ORDER BY month DESC, popularity DESC"
    elif view == "rules":
        sql = ("SELECT antecedent,consequent,co_jobs,support,conf_ab,conf_ba,lift "
               "FROM rules WHERE lift>=? AND support>=?")
        p += [min_lift, min_sup]
        if kw:
            sql += " AND (antecedent LIKE ? OR consequent LIKE ?)"
            p += [f"%{kw}%", f"%{kw}%"]
        sql += " ORDER BY lift DESC"
    elif view == "models":
        sql = "SELECT model,horizon,wape,mape,rmse,samples FROM model_compare WHERE 1=1"
        if kw:
            sql += " AND model LIKE ?"; p.append(f"%{kw}%")
        sql += " ORDER BY wape ASC"
    elif view == "extraction":
        sql = ("SELECT method,`precision`,recall,f1,ms_per_doc,tp,fp,fn "
               "FROM extraction ORDER BY f1 DESC")
    elif view == "clusters":
        sql = ("SELECT cluster_id,top_skills,skill_cnt,job_cnt,avg_salary "
               "FROM clusters ORDER BY cluster_id")
    else:
        sql = ("SELECT h,origin,month,skill,y,naive,ma,trend,tree "
               "FROM predictions WHERE 1=1")
        if skill:
            sql += " AND skill=?"; p.append(skill)
        if m1:
            sql += " AND month>=?"; p.append(m1)
        if m2:
            sql += " AND month<=?"; p.append(m2)
        sql += " ORDER BY month, skill"
    return sql, p, None


def page_args(args) -> tuple[int, int]:
    try:
        page = max(1, int(args.get("page") or 1))
    except ValueError:
        page = 1
    try:
        size = min(200, max(5, int(args.get("page_size") or 20)))
    except ValueError:
        size = 20
    return page, size


# ------------------------------ 页面路由 ------------------------------
@app.route("/")
def dashboard():
    db = get_db()
    kpi = {
        "cells": db.execute("SELECT COUNT(*) FROM skill_month").fetchone()[0],
        "rules": db.execute("SELECT COUNT(*) FROM rules").fetchone()[0],
        "months": db.execute("SELECT COUNT(DISTINCT month) FROM skill_month").fetchone()[0],
        "skills": db.execute("SELECT COUNT(DISTINCT skill) FROM skill_month").fetchone()[0],
        "labels": db.execute("SELECT COUNT(*) FROM source_info").fetchone()[0],
    }
    months = [r[0] for r in db.execute(
        "SELECT DISTINCT month FROM skill_month ORDER BY month")]
    skills = [r[0] for r in db.execute(
        "SELECT DISTINCT skill FROM skill_month ORDER BY skill")]
    series = {}
    for s in skills:
        series[s] = [r[0] for r in db.execute(
            "SELECT popularity FROM skill_month WHERE skill=? ORDER BY month", (s,))]
    cats = [list(r) for r in db.execute(
        "SELECT category,jobs FROM skill_category ORDER BY jobs DESC LIMIT 10")]
    models = [list(r) for r in db.execute(
        "SELECT model,wape FROM model_compare WHERE horizon='全体' ORDER BY wape")]
    rules = [list(r) for r in db.execute(
        f"SELECT {dbx.concat_expr()}, lift FROM rules ORDER BY lift DESC LIMIT 10")]
    # 数据源 C：从独立采集库读取实时岗位统计（默认展示，A/B 基准图保留在下）
    c_stats = sc.stats()
    return render_template("dashboard.html", kpi=kpi, months=months, skills=skills,
                           series=series, cats=cats, models=models, rules=rules,
                           c=c_stats, c_skills=sc.skills(10),
                           c_cities=c_stats["cities"], c_months=c_stats["months"])


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = (request.form.get("username") or "").strip()
        p = request.form.get("password") or ""
        row = get_db().execute("SELECT * FROM users WHERE username=?", (u,)).fetchone()
        if row is None or not check_password_hash(row["password_hash"], p):
            flash("用户名或密码错误", "err"); log("登录失败", u)
        elif row["status"] != 1:
            flash("该账号已被停用，请联系管理员", "err")
        else:
            session.clear()
            session.update(uid=row["id"], username=row["username"], role=row["role"])
            get_db().execute("UPDATE users SET last_login=? WHERE id=?", (now(), row["id"]))
            get_db().commit(); log("登录成功")
            return redirect(request.args.get("next") or url_for("dashboard"))
    return render_template("auth.html", mode="login")


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    row = get_db().execute("SELECT * FROM users WHERE username=?",
                           (data.get("username"),)).fetchone()
    if row is None or not check_password_hash(row["password_hash"], data.get("password", "")) \
            or row["status"] != 1:
        return jsonify({"ok": False, "error": "用户名或密码错误"}), 401
    session.clear()
    session.update(uid=row["id"], username=row["username"], role=row["role"])
    return jsonify({"ok": True, "username": row["username"], "role": row["role"]})


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        u = (request.form.get("username") or "").strip()
        e = (request.form.get("email") or "").strip()
        p = request.form.get("password") or ""
        p2 = request.form.get("password2") or ""
        if len(u) < 3 or len(u) > 32:
            flash("用户名长度须为 3–32 个字符", "err")
        elif not p or len(p) < 6:
            flash("密码长度不得少于 6 位", "err")
        elif p != p2:
            flash("两次输入的密码不一致", "err")
        elif get_db().execute("SELECT 1 FROM users WHERE username=?", (u,)).fetchone():
            flash("该用户名已被注册", "err")
        else:
            get_db().execute(
                "INSERT INTO users(username,email,password_hash,role,created_at) "
                "VALUES(?,?,?,?,?)",
                (u, e, generate_password_hash(p), "user", now()))
            get_db().commit(); log("注册新用户", u)
            flash("注册成功，请登录", "ok")
            return redirect(url_for("login"))
    return render_template("auth.html", mode="register")


@app.route("/logout")
def logout():
    log("退出登录")
    session.clear()
    return redirect(url_for("dashboard"))


@app.route("/jobs")
def jobs():
    """数据源 C 实时岗位检索：站点/城市/技能/关键词/月薪/发布月份多条件筛选。"""
    c = sc.stats()
    page, size = page_args(request.args)
    rows, total = sc.query_jobs(request.args, page, size)
    base_qs = urlencode([(k, v) for k, v in request.args.items() if k != "page"])
    return render_template("jobs.html", rows=rows, total=total, page=page,
                           size=size, args=request.args, base_qs=base_qs, c=c,
                           sites=c["sites"], cities=sc.city_options(),
                           skill_options=sc.skill_options(),
                           months=[x["pub_month"] for x in c["months"]])


@app.route("/export/jobs")
@login_required
def export_jobs():
    rows, _ = sc.query_jobs(request.args, all_rows=True)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["平台", "岗位ID", "职位名称", "薪资", "城市", "经验", "学历",
                "公司", "技能标签", "发布时间", "发布月份", "检索词",
                "词典匹配技能", "月薪中位数(k)", "岗位链接"])
    for r in rows:
        w.writerow([r["site_name"], r["job_id"], r["title"], r["salary"],
                    r["city_n"], r["exp"], r["edu"], r["company"], r["tags"],
                    r["pub_time"], r["pub_month"], r["keyword"],
                    (r["skills"] or "").replace(" · ", ";"),
                    r["sal_mid"] if r["sal_mid"] is not None else "", r["url"]])
    log("导出实时岗位", f"数据源 C / {len(rows)} 行")
    return Response("\ufeff" + buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             'attachment; filename="source_c_jobs.csv"'})


@app.route("/sources")
def sources_page():
    """三源并列的数据源总览：A/B 来自结果库元信息表，C 来自独立采集库。"""
    rows = get_db().execute("SELECT * FROM source_info").fetchall()
    return render_template("sources.html", sources=rows,
                           c_meta=sc.meta(), c=sc.stats())


@app.route("/explore")
def explore():
    view = request.args.get("view") if request.args.get("view") in VIEWS else "heat"
    sql, p, err = build_query(view, request.args)
    rows, total = [], 0
    if err is None:
        db = get_db()
        total = db.execute(f"SELECT COUNT(*) FROM ({sql}) AS _q", p).fetchone()[0]
        page, size = page_args(request.args)
        rows = db.execute(sql + " LIMIT ? OFFSET ?", p + [size, (page - 1) * size]).fetchall()
    else:
        page, size = page_args(request.args)
    skills = [r[0] for r in get_db().execute(
        "SELECT DISTINCT skill FROM skill_month ORDER BY skill")]
    def fmt(v):
        return f"{v:.4f}" if isinstance(v, float) else v
    shown = [[fmt(v) for v in r] for r in rows]
    base_qs = urlencode([(k, v) for k, v in request.args.items() if k != "page"])
    return render_template("explore.html", view=view, cols=COLS[view], rows=shown,
                           total=total, page=page, size=size, err=err,
                           skills=skills, args=request.args, base_qs=base_qs)


@app.route("/export")
@login_required
def export():
    view = request.args.get("view") if request.args.get("view") in VIEWS else "heat"
    sql, p, err = build_query(view, request.args)
    if err:
        return Response(err, mimetype="text/plain; charset=utf-8")
    rows = get_db().execute(sql, p).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([label for _, label in COLS[view]])
    for r in rows:
        w.writerow(list(r))
    log("导出报表", f"{view} / {len(rows)} 行")
    return Response("\ufeff" + buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             f'attachment; filename="{view}_report.csv"'})


@app.route("/admin")
@admin_required
def admin():
    db = get_db()
    users = db.execute("SELECT * FROM users ORDER BY id").fetchall()
    logs = db.execute("SELECT * FROM op_logs ORDER BY id DESC LIMIT 20").fetchall()
    tables = ["users", "skill_month", "rules", "model_compare", "predictions", "op_logs"]
    counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    sources = db.execute("SELECT * FROM source_info").fetchall()
    return render_template("admin.html", users=users, logs=logs,
                           counts=counts, sources=sources,
                           c_meta=sc.meta(), c=sc.stats())


@app.route("/admin/db")
@admin_required
def admin_db():
    """数据库状态页：实时查询服务端变量、账号权限、表行数与索引数。"""
    db = get_db()
    is_mysql = dbx.backend() == "mysql"
    info, grants, table_rows, index_cnt = {}, [], [], 0
    if is_mysql:
        r = db.execute(
            "SELECT VERSION(), @@port, @@character_set_server, @@collation_server, "
            "@@default_storage_engine, @@max_connections, @@innodb_buffer_pool_size, "
            "CURRENT_USER(), DATABASE()").fetchone()
        info = {
            "服务端版本": r[0], "监听端口": r[1], "字符集": r[2], "排序规则": r[3],
            "默认存储引擎": r[4], "最大连接数": r[5],
            "InnoDB 缓冲池": f"{int(r[6]) // 1024 // 1024} MB",
            "当前连接账号": r[7], "当前数据库": r[8],
        }
        grants = [x[0] for x in db.execute("SHOW GRANTS")]
        index_cnt = db.execute(
            "SELECT COUNT(*) FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA=DATABASE()").fetchone()[0]
        names = [x[0] for x in db.execute(
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME")]
    else:
        info = {"引擎": "SQLite（本地文件）", "版本": dbx.server_version(),
                "数据库文件": str(DB), "当前连接账号": "无服务端账号体系"}
        grants = ["SQLite 为进程内文件库，权限由操作系统文件权限控制"]
        names = [x[0] for x in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        index_cnt = db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='index'").fetchone()[0]
    for t in names:
        table_rows.append((t, db.execute(f"SELECT COUNT(*) FROM `{t}`").fetchone()[0]))
    # 独立采集库（数据源 C）：与结果库并列展示，不计入 12 张结果表
    crawl_tables = []
    try:
        cc = sc.connect_c()
        try:
            if is_mysql:
                cn = [x[0] for x in cc.execute(
                    "SELECT TABLE_NAME FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA=? ORDER BY TABLE_NAME", (sc.MYSQL_DB,))]
            else:
                cn = [x[0] for x in cc.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            for t in cn:
                crawl_tables.append(
                    (t, cc.execute(f"SELECT COUNT(*) FROM `{t}`").fetchone()[0]))
        finally:
            cc.close()
    except Exception as e:
        crawl_tables = [("采集库连接失败", str(e))]
    return render_template("db_status.html", info=info, grants=grants,
                           table_rows=table_rows, index_cnt=index_cnt,
                           is_mysql=is_mysql, crawl_tables=crawl_tables,
                           crawl_backend=sc.backend_label())


@app.route("/admin/user/<int:uid>/<action>", methods=["POST"])
@admin_required
def admin_user(uid: int, action: str):
    if action == "toggle":
        get_db().execute("UPDATE users SET status=CASE status WHEN 1 THEN 0 ELSE 1 END "
                         "WHERE id=? AND username!='admin'", (uid,))
        detail = f"切换用户 {uid} 启用状态"
    elif action == "role":
        get_db().execute("UPDATE users SET role=CASE role WHEN 'admin' THEN 'user' "
                         "ELSE 'admin' END WHERE id=? AND username!='admin'", (uid,))
        detail = f"切换用户 {uid} 角色"
    else:
        return "未知操作", 400
    get_db().commit(); log("管理员操作", detail)
    return redirect(url_for("admin"))


# ------------------------------ RESTful 接口 ------------------------------
@app.route("/api/source_c/overview")
def api_source_c_overview():
    c = sc.stats()
    c.pop("meta", None)
    return jsonify({"source": "C", "backend": c["backend"], "overview": c})


@app.route("/api/source_c/skills")
def api_source_c_skills():
    try:
        limit = min(50, max(1, int(request.args.get("limit") or 20)))
    except ValueError:
        limit = 20
    return jsonify({"source": "C", "count": len(sc.skills(limit)),
                    "data": sc.skills(limit)})


@app.route("/api/source_c/jobs")
def api_source_c_jobs():
    try:
        limit = min(200, max(1, int(request.args.get("limit") or 20)))
    except ValueError:
        limit = 20
    rows, total = sc.query_jobs(request.args, 1, limit)
    return jsonify({"source": "C", "total": total, "count": len(rows),
                    "data": rows})


@app.route("/api/overview")
def api_overview():
    db = get_db()
    return jsonify({
        "skill_month_rows": db.execute("SELECT COUNT(*) FROM skill_month").fetchone()[0],
        "rules": db.execute("SELECT COUNT(*) FROM rules").fetchone()[0],
        "clusters": db.execute("SELECT COUNT(*) FROM clusters").fetchone()[0],
        "models": db.execute("SELECT COUNT(*) FROM model_compare").fetchone()[0],
    })


@app.route("/api/<view>")
def api_view(view: str):
    if view not in ("heat", "rules", "models", "extraction", "clusters", "predictions"):
        return jsonify({"error": "未知接口"}), 404
    sql, p, err = build_query(view, request.args)
    if err:
        return jsonify({"error": err}), 400
    try:
        limit = min(500, max(1, int(request.args.get("limit") or 50)))
    except ValueError:
        limit = 50
    rows = get_db().execute(sql + " LIMIT ?", p + [limit]).fetchall()
    return jsonify({"view": view, "count": len(rows),
                    "data": [dict(r) for r in rows]})


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok", "time": now(),
                    "db_backend": dbx.backend(),
                    "db_label": dbx.backend_label(),
                    "source_c_backend": sc.backend_label(),
                    "source_c_jobs": sc.stats()["total"]})


if __name__ == "__main__":
    if dbx.backend() == "sqlite" and not DB.exists():
        raise SystemExit("请先运行 python experiments/system/init_db.py")
    if dbx.backend() == "mysql":
        print("关系库后端：MySQL", dbx.server_version())
    else:
        print("关系库后端：SQLite", DB)
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)

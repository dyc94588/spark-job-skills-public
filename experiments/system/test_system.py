# -*- coding: utf-8 -*-
"""系统功能测试（T01–T09）：对真实运行的 Flask 服务发起 HTTP 请求并留存结果。

默认输出：experiments/results/e5_system_test.csv
指定输出：python experiments/system/test_system.py <输出csv路径>
结果表带「关系库后端」列，可分别在 SQLite 与 MySQL 后端复测并对比。
说明：T10（YARN 集群部署）本机未部署，如实标注「未实测」，不填写「通过」。
运行：先启动 app.py，再运行本脚本。
"""
from __future__ import annotations
import csv
import http.cookiejar
import json
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from accounts import ADMIN_PWD, DEMO_PWD

BASE = "http://127.0.0.1:5000"
DEFAULT_OUT = Path(__file__).resolve().parents[1] / "results" / "e5_system_test.csv"


def opener():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def req(op, path, data=None, timeout=10):
    url = BASE + path
    payload = urllib.parse.urlencode(data).encode() if data is not None else None
    r = urllib.request.Request(url, data=payload)
    t0 = time.perf_counter()
    try:
        with op.open(r, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, body, (time.perf_counter() - t0) * 1000
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), (time.perf_counter() - t0) * 1000


def wait_ready():
    for _ in range(20):
        try:
            with urllib.request.urlopen(BASE + "/healthz", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    assert wait_ready(), "Flask 服务未就绪，请先运行 app.py"
    try:
        hb = json.loads(urllib.request.urlopen(BASE + "/healthz", timeout=3).read())
        backend = hb.get("db_label", hb.get("db_backend", "unknown"))
    except Exception:
        backend = "unknown"
    print("当前关系库后端:", backend)
    rows = []

    def add(tid, name, op_desc, expect, actual, ok, ms=""):
        rows.append({"用例编号": tid, "功能点": name, "操作": op_desc,
                     "预期结果": expect, "实际结果": actual,
                     "是否通过": "通过" if ok else "未通过", "耗时(ms)": ms,
                     "关系库后端": backend})

    # T01 注册
    op = opener()
    uname = f"stu{int(time.time()) % 100000}"
    st, body, ms = req(op, "/register", {"username": uname, "email": "",
                                         "password": "Test@2026", "password2": "Test@2026"})
    ok = st == 200 and "注册成功" in body
    add("T01", "用户注册", f"注册新用户 {uname} 并自动跳转登录页",
        "写入用户表并提示注册成功", f"HTTP {st}，提示注册成功" if ok else f"HTTP {st}",
        ok, f"{ms:.1f}")

    # T02 登录（管理员）
    adm = opener()
    st, body, ms = req(adm, "/login", {"username": "admin", "password": ADMIN_PWD})
    ok = st == 200 and "管理后台" in body
    add("T02", "用户登录", "使用管理员账号登录",
        "建立会话并显示管理员入口", f"HTTP {st}，出现管理后台入口" if ok else f"HTTP {st}",
        ok, f"{ms:.1f}")

    # T03 错误口令
    bad = opener()
    st, body, _ = req(bad, "/login", {"username": "admin", "password": "wrong"})
    ok = "用户名或密码错误" in body
    add("T03", "口令校验", "使用错误密码登录",
        "拒绝登录并给出提示", "提示「用户名或密码错误」" if ok else "未拦截", ok)

    # T04 权限控制
    anon = opener()
    st_a, body_a, _ = req(anon, "/admin")
    dem = opener()
    req(dem, "/login", {"username": "demo", "password": DEMO_PWD})
    st_d, body_d, _ = req(dem, "/admin")
    ok = (st_a in (302, 401) or "用户登录" in body_a) and st_d == 403 and "权限不足" in body_d
    add("T04", "角色权限控制", "未登录 / 普通用户分别访问 /admin",
        "重定向登录页 / 返回 403", f"未登录 HTTP {st_a}，普通用户 HTTP {st_d}", ok)

    # T05 数据筛选
    st, body, ms = req(anon, "/explore?view=heat&skill=Python&month_from=2024-01&month_to=2024-12")
    ok = st == 200 and "共 " in body and "Python" in body
    add("T05", "多条件数据筛选", "技能=Python 且月份 2024-01~2024-12",
        "返回筛选后的技能—月记录", f"HTTP {st}，结果表含 Python 记录" if ok else f"HTTP {st}",
        ok, f"{ms:.1f}")

    # T06 异常输入
    _, b1, _ = req(anon, "/explore?view=heat&month_from=2026-01&month_to=2023-01")
    _, b2, _ = req(anon, "/explore?view=rules&min_lift=abc")
    _, b3, _ = req(anon, "/explore?view=rules&keyword=" + "x" * 80)
    ok = ("起始月份不能晚于结束月份" in b1 and "不是合法数字" in b2
          and "关键词过长" in b3)
    add("T06", "异常输入容错", "月份倒置 / 非数值 / 超长关键词",
        "返回友好提示且不抛服务端异常",
        "三类输入均返回中文提示" if ok else "存在未处理输入", ok)

    # T07 RESTful 接口
    _, j1, _ = req(anon, "/api/overview")
    _, j2, _ = req(anon, "/api/rules?min_lift=50&limit=5")
    try:
        d1, d2 = json.loads(j1), json.loads(j2)
        ok = d1["rules"] > 0 and d2["count"] > 0
        detail = f"overview.rules={d1['rules']}，rules 返回 {d2['count']} 条"
    except Exception as e:
        ok, detail = False, str(e)
    add("T07", "RESTful 数据接口", "GET /api/overview 与 /api/rules?min_lift=50",
        "返回结构化 JSON", detail, ok)

    # T08 报表导出
    st, body, ms = req(adm, "/export?view=rules&min_lift=50")
    lines = [x for x in body.splitlines() if x.strip()]
    ok = st == 200 and len(lines) > 1 and "提升度" in lines[0]
    add("T08", "报表导出", "登录态导出提升度≥50 的规则",
        "生成含表头的 CSV 文件", f"HTTP {st}，{len(lines)-1} 条数据行" if ok else f"HTTP {st}",
        ok, f"{ms:.1f}")

    # T09 并发访问
    def hit(_):
        o = opener()
        return req(o, "/api/overview")[0:3:2]
    with ThreadPoolExecutor(max_workers=20) as pool:
        t0 = time.perf_counter()
        res = list(pool.map(hit, range(20)))
        wall = (time.perf_counter() - t0) * 1000
    times = [x[1] for x in res]
    ok = all(x[0] == 200 for x in res)
    add("T09", "并发访问", "20 线程并发请求 /api/overview",
        "全部 200 且响应在可接受范围",
        f"成功 {sum(1 for x in res if x[0]==200)}/20，单请求均值 {sum(times)/len(times):.1f} ms，"
        f"最大 {max(times):.1f} ms，总耗时 {wall:.1f} ms", ok)

    # T10 如实标注
    add("T10", "集群部署", "提交 YARN 集群执行并与 local[*] 比对",
        "与本机结果一致", "本机未部署 YARN 集群，未实测，列入后续工作", False, "")

    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print("已写入", out)
    for r in rows:
        print(f"  {r['用例编号']} {r['功能点']:14} {r['是否通过']:4}  {r['实际结果'][:70]}")
    print(f"通过 {sum(1 for r in rows if r['是否通过']=='通过')}/{len(rows)}")


if __name__ == "__main__":
    main()

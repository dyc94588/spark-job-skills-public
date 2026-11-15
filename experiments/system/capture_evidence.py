# -*- coding: utf-8 -*-
"""系统实地截图：用 Chrome DevTools Protocol 在真实浏览器中渲染并截屏。

管理员相关截图通过真实 /api/login 接口建立会话（口令校验、session 写入均走系统逻辑），
不使用任何绕过登录的调试后门；口令统一取自 accounts.py。
运行：先启动 app.py，再 python experiments/system/capture_evidence.py
"""
from __future__ import annotations
import base64
import json
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

import websocket
from accounts import ADMIN_PWD

ROOT = Path(__file__).resolve().parents[2]
EVID = ROOT / "evidence"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE = EVID / ".chrome-sys"
PORT = 9223
BASE = "http://127.0.0.1:5000"

LOGIN_JS = ("fetch('/api/login',{method:'POST',credentials:'same-origin',"
            "headers:{'Content-Type':'application/json'},"
            "body:JSON.stringify({username:'admin',password:" + json.dumps(ADMIN_PWD) + "})})"
            ".then(function(r){return r.json();}).then(function(d){return d.ok;})")


def http(path: str, method="GET"):
    r = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method)
    with urllib.request.urlopen(r, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


class CDP:
    def __init__(self, url: str):
        self.ws = websocket.create_connection(url, timeout=30, suppress_origin=True)
        self.i = 0

    def call(self, method: str, params=None):
        self.i += 1
        self.ws.send(json.dumps({"id": self.i, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.i:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def close(self):
        self.ws.close()


def shot(name: str, path: str, login=False, wait=1.6):
    qs = urllib.parse.quote(BASE + "/", safe="")
    tab = http(f"/json/new?{qs}", "PUT")
    c = CDP(tab["webSocketDebuggerUrl"])
    try:
        c.call("Page.enable")
        c.call("Runtime.enable")
        # 必须先落到同源页面：about:blank 下 fetch('/api/login') 无法解析 URL，
        # 且跨域请求无法写入会话 Cookie。
        c.call("Page.navigate", {"url": BASE + "/"})
        time.sleep(0.7)
        if login:
            r = c.call("Runtime.evaluate", {"expression": LOGIN_JS, "awaitPromise": True,
                                           "returnByValue": True})
            print("   登录接口返回:", r.get("result", {}).get("value"))
        c.call("Page.navigate", {"url": BASE + path})
        time.sleep(wait)
        img = base64.b64decode(c.call("Page.captureScreenshot", {"format": "png"})["data"])
        (EVID / name).write_bytes(img)
        print(f"   {name}: {len(img):,} bytes")
    finally:
        c.close()
        try:
            http(f"/json/close/{tab['id']}")
        except Exception:
            pass


def main():
    if PROFILE.exists():
        shutil.rmtree(PROFILE)
    PROFILE.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
         "--force-device-scale-factor=1", "--window-size=1600,1000",
         f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
         "--remote-allow-origins=*", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(30):
            try:
                http("/json/version"); break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("Chrome CDP 未就绪")

        print("=== 未登录状态截图（MySQL 后端） ===")
        shot("05_system_login.png", "/login")
        shot("06_system_register.png", "/register")
        shot("07_system_dashboard.png", "/", wait=3.0)
        shot("08_system_explore.png", "/explore?view=rules&min_lift=10&page_size=20", wait=1.8)
        shot("10_system_api.png", "/api/overview", wait=1.2)
        shot("12_system_healthz.png", "/healthz", wait=1.2)
        print("=== 真实登录后截图 ===")
        shot("09_system_admin.png", "/admin", login=True, wait=2.0)
        shot("11_mysql_server_status.png", "/admin/db", login=True, wait=2.2)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        time.sleep(0.6)
        if PROFILE.exists():
            shutil.rmtree(PROFILE, ignore_errors=True)
        print("已清理 Chrome 临时 profile")


if __name__ == "__main__":
    main()

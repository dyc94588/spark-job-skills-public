# -*- coding: utf-8 -*-
"""数据源 C 接入后的系统界面实跑截图（Chrome DevTools Protocol 真实渲染）。

管理员页通过真实 /api/login 接口建立会话，口令取自 accounts.py，无调试后门。
运行：先启动 app.py，再 python experiments/system/capture_source_c.py
"""
from __future__ import annotations
import base64
import json
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

import websocket
from accounts import ADMIN_PWD

ROOT = Path(__file__).resolve().parents[2]
EVID = ROOT / "evidence"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9224
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


def shot(name: str, path: str, login=False, wait=2.0):
    qs = urllib.parse.quote(BASE + "/", safe="")
    tab = http(f"/json/new?{qs}", "PUT")
    c = CDP(tab["webSocketDebuggerUrl"])
    try:
        c.call("Page.enable")
        c.call("Runtime.enable")
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
    EVID.mkdir(exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix="thesis-chrome-c-"))
    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
         "--force-device-scale-factor=1", "--window-size=1600,1100",
         f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
         "--remote-allow-origins=*", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(30):
            try:
                http("/json/version")
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("Chrome CDP 未就绪")
        print("=== 数据源 C 新界面截图 ===")
        shot("15_source_c_dashboard.png", "/", wait=3.2)
        shot("16_source_c_jobs.png",
             "/jobs?site=liepin&skill=Spark&min_salary=20", wait=2.0)
        shot("17_source_c_sources.png", "/sources", wait=2.0)
        shot("18_source_c_admin.png", "/admin", login=True, wait=2.2)
        shot("19_source_c_db_status.png", "/admin/db", login=True, wait=2.2)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        print("Chrome 临时 profile:", profile, "（系统 temp，未删除以免误删用户目录）")


if __name__ == "__main__":
    main()

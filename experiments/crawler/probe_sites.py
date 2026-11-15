# -*- coding: utf-8 -*-
"""主流招聘站点可达性探测（真 Chrome 渲染）。

用 Chrome DevTools Protocol 打开各站公开搜索页，等待 JS 渲染，
截图并抽取页面文本，判断是否出现真实岗位内容（薪资/职位特征）。
仅访问公开列表页，不做任何登录或验证码绕过；结果仅用于学术数据采集可行性评估。
"""
from __future__ import annotations
import json
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

import websocket

ROOT = Path(__file__).resolve().parents[2]
EVID = ROOT / "evidence" / "site_probe"
EVID.mkdir(parents=True, exist_ok=True)
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE = EVID / ".chrome-probe"
PORT = 9333

SITES = [
    ("zhipin",   "https://www.zhipin.com/web/geek/job?query=Python&city=100010000"),
    ("liepin",   "https://www.liepin.com/zhaopin/?key=python"),
    ("zhaopin",  "https://sou.zhaopin.com/?jl=530&kw=python"),
    ("51job",    "https://we.51job.com/pc/search?keyword=python&searchType=2&jobArea=030200"),
    ("lagou",    "https://www.lagou.com/jobs/list_Python"),
]

# 判定特征：出现任一即认为页面含真实岗位列表
HINTS = ["万/月", "万/年", "K", "k", "薪资", "月薪", "招聘", "经验", "本科", "大专", "职位", "岗位"]


def http(path: str, method="GET"):
    r = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method)
    with urllib.request.urlopen(r, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


class CDP:
    def __init__(self, url: str):
        self.ws = websocket.create_connection(url, timeout=60, suppress_origin=True)
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


def probe(name: str, url: str, wait: float = 8.0) -> dict:
    tab = http("/json/new?" + url, "PUT")
    c = CDP(tab["webSocketDebuggerUrl"])
    try:
        c.call("Page.enable")
        c.call("Runtime.enable")
        c.call("Emulation.setDeviceMetricsOverride",
               {"width": 1500, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
        c.call("Page.navigate", {"url": url})
        time.sleep(wait)
        r = c.call("Runtime.evaluate", {
            "expression": "document.body ? document.body.innerText.slice(0, 40000) : ''",
            "returnByValue": True})
        text = r.get("result", {}).get("value", "") or ""
        title = c.call("Runtime.evaluate", {
            "expression": "document.title", "returnByValue": True
        }).get("result", {}).get("value", "")
        shot = c.call("Page.captureScreenshot", {"format": "png"})
        import base64
        (EVID / f"probe_{name}.png").write_bytes(base64.b64decode(shot["data"]))
        hints = [h for h in HINTS if h in text]
        return {"name": name, "url": url, "title": title[:80],
                "text_len": len(text), "hints": hints,
                "sample": text[:400].replace("\n", " | ")}
    finally:
        c.close()
        try:
            http(f"/json/close/{tab['id']}", "PUT")
        except Exception:
            pass


def main():
    if PROFILE.exists():
        shutil.rmtree(PROFILE, ignore_errors=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
                             "--hide-scrollbars", "--window-size=1500,1000",
                             f"--remote-debugging-port={PORT}",
                             f"--user-data-dir={PROFILE}",
                             "--remote-allow-origins=*", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            try:
                http("/json/version")
                break
            except Exception:
                time.sleep(0.5)
        results = []
        for name, url in SITES:
            print(f"--- 探测 {name}: {url}")
            try:
                results.append(probe(name, url))
            except Exception as e:
                results.append({"name": name, "url": url, "error": str(e)[:200]})
            time.sleep(3)  # 站点间隔，礼貌限速
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        time.sleep(0.5)
        shutil.rmtree(PROFILE, ignore_errors=True)

    print("\n===== 探测汇总 =====")
    for r in results:
        print(json.dumps(r, ensure_ascii=False)[:500])
    (EVID / "probe_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

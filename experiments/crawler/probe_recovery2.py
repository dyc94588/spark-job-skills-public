# -*- coding: utf-8 -*-
"""第二轮合规探测：51job 地区/排序参数与第三方公开招聘站可达性。

只读探测，不写入样本库，不登录、不绕过任何风控，逐次限速。
"""
from __future__ import annotations
import json
import re
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import probe_sites as ps
import crawl_sites_c as cs

PORT = 9445
PROFILE = (HERE.parent / "experiments/data/real/source_c" /
           f".chrome-probe2-{datetime.now().strftime('%H%M%S')}").resolve()


def http(path: str, method="GET"):
    r = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method)
    with urllib.request.urlopen(r, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


class CDP(ps.CDP):
    pass


def open_page(url: str, wait: float = 8.0):
    """打开公开页面，返回 (岗位卡片数, 页面文本片段)。"""
    tab = http("/json/new?" + url, "PUT")
    try:
        c = CDP(tab["webSocketDebuggerUrl"])
        try:
            c.call("Page.enable")
            c.call("Runtime.enable")
            time.sleep(wait)
            c.call("Runtime.evaluate", {"expression":
                    "window.scrollTo(0, document.body.scrollHeight)",
                    "returnByValue": True})
            time.sleep(1.2)
            js = r"""(function(){
              var n1 = document.querySelectorAll('.joblist-item').length;
              var n2 = document.querySelectorAll('[class*="job-card"],[class*="jobCard"],[class*="job-list"] li').length;
              var t = (document.body.innerText||'').replace(/\s+/g,' ').slice(0,260);
              return JSON.stringify({n1:n1,n2:n2,text:t,url:location.href});
            })()"""
            r = c.call("Runtime.evaluate", {"expression": js, "returnByValue": True})
            return json.loads(r.get("result", {}).get("value") or "{}")
        finally:
            c.close()
    finally:
        try:
            http(f"/json/close/{tab['id']}", "PUT")
        except Exception:
            pass


def ids(url, wait=8.0):
    try:
        items = cs.crawl_page("51job", url, wait=wait)
        return set(str((it.get("raw") or {}).get("jobId") or "") for it in items
                   if (it.get("raw") or {}).get("jobId"))
    except Exception as e:
        print("   ERROR", e, flush=True)
        return set()


def main():
    PROFILE.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [ps.CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--hide-scrollbars", "--window-size=1500,1200",
         f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
         "--remote-allow-origins=*", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            try:
                http("/json/version")
                break
            except Exception:
                time.sleep(.5)
        cs.http = http
        cs.CDP = CDP

        print("=== A. 51job 地区参数 jobArea 是否影响结果池 ===", flush=True)
        base = "https://we.51job.com/pc/search?keyword=python&searchType=2&pageNo=1&jobArea={}"
        pools = {}
        for code, name in [("000000", "全国"), ("010000", "北京"),
                           ("020000", "上海"), ("040000", "深圳")]:
            s = ids(base.format(code))
            pools[name] = s
            print(f"  jobArea={code} {name}: {len(s)} 个ID", flush=True)
            time.sleep(5)
        print("  全国∩北京", len(pools["全国"] & pools["北京"]),
              "北京∩上海", len(pools["北京"] & pools["上海"]),
              "并集", len(set().union(*pools.values())), flush=True)

        print("\n=== B. 51job 排序/来源参数 ===", flush=True)
        alts = [
            ("sortType=1", "https://we.51job.com/pc/search?keyword=python&searchType=2&jobArea=000000&pageNo=1&sortType=1"),
            ("searchType=2&pageSize=50", "https://we.51job.com/pc/search?keyword=python&searchType=2&jobArea=000000&pageNo=1&pageSize=50"),
            ("keyword=数据分析师", "https://we.51job.com/pc/search?keyword=%E6%95%B0%E6%8D%AE%E5%88%86%E6%9E%90%E5%B8%88&searchType=2&jobArea=000000&pageNo=1"),
        ]
        for label, u in alts:
            s = ids(u)
            print(f"  {label:26s}: {len(s)} 个ID, 与全国python交集 {len(s & pools['全国'])}", flush=True)
            time.sleep(5)

        print("\n=== C. 其它公开招聘站可达性（仅判断能否渲染岗位列表） ===", flush=True)
        sites = [
            ("职友集 jobui", "https://www.jobui.com/jobs/?keyword=%E6%95%B0%E6%8D%AE%E5%88%86%E6%9E%90"),
            ("牛客 jobs", "https://www.nowcoder.com/jobs/detail?keyword=python"),
            ("中华英才网", "https://www.chinahr.com/sou/?keyword=python"),
            ("实习僧", "https://www.shixiseng.com/interns?keyword=python"),
            ("51job 老版", "https://search.51job.com/list/000000,000000,0000,00,9,99,python,2,1.html"),
            ("猎聘 移动版", "https://m.liepin.com/zhaopin/?key=python"),
        ]
        for label, u in sites:
            try:
                d = open_page(u, wait=7.0)
                txt = d.get("text", "")
                flag = "岗位特征" if any(k in txt for k in ["万/月", "K", "薪资", "招聘", "本科"]) else "无明显岗位"
                print(f"  {label:14s} | 卡片n1={d.get('n1',0)} n2={d.get('n2',0)} | {flag} | {txt[:90]}", flush=True)
            except Exception as e:
                print(f"  {label:14s} | ERROR {e}", flush=True)
            time.sleep(5)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    main()

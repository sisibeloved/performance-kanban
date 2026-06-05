#!/usr/bin/env python3
"""浏览器控制台冒烟测试 — 捕获前端渲染期的 JS/Vega 报错。

单元测试(test_data_layer.py)只覆盖数据层，**测不到前端渲染错误**
(如把元组字段名传给 st.bar_chart 触发的 Vega-Lite 报错，见 issue #4)。
本脚本启动应用 + 无头浏览器，渲染对比页后断言浏览器控制台**零报错**。

用法:
    python smoke_test.py                    # 自动启动 streamlit + 无头 chrome
    CHROME=/path/to/chrome python smoke_test.py

依赖: streamlit、pandas、requests、websockets，以及一个 Chrome/Chromium 可执行文件
(通过 $CHROME 指定，或在 PATH 中名为 chrome/chromium/chromium-browser/google-chrome)。
若找不到浏览器或缺依赖，脚本会跳过(退出码 0)并提示，不阻塞无浏览器的环境。
"""
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _find_chrome() -> str | None:
    if os.environ.get("CHROME") and os.path.exists(os.environ["CHROME"]):
        return os.environ["CHROME"]
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _wait_http(url: str, timeout: float = 40) -> bool:
    import requests
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if requests.get(url, timeout=2).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


async def _collect_console_errors(cdp_json_url: str, app_url: str, settle: float = 9.0) -> list:
    import requests
    import websockets

    target = next(
        t for t in requests.get(cdp_json_url).json()
        if t["type"] == "page"
    )
    ws_url = target["webSocketDebuggerUrl"]
    errors: list[str] = []

    async with websockets.connect(ws_url, max_size=None) as ws:
        mid = 0

        async def send(method, params=None):
            nonlocal mid
            mid += 1
            await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))

        def collect(m):
            meth = m.get("method", "")
            if meth == "Runtime.exceptionThrown":
                d = m["params"]["exceptionDetails"]
                errors.append((d.get("exception", {}) or {}).get("description") or d.get("text", ""))
            elif meth == "Runtime.consoleAPICalled" and m["params"]["type"] == "error":
                errors.append("".join(
                    str(a.get("value", a.get("description", ""))) for a in m["params"]["args"]
                ))

        await send("Runtime.enable")
        await send("Page.enable")
        await send("Page.navigate", {"url": app_url})

        # 等待渲染并收集报错；并确认对比表确实渲染了(否则没覆盖到图表)
        rendered = False
        t0 = time.time()
        while time.time() - t0 < max(settle, 12):
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
                collect(m)
            except asyncio.TimeoutError:
                pass
            # 轮询是否渲染出表格
            mid += 1
            check_id = mid
            await ws.send(json.dumps({"id": check_id, "method": "Runtime.evaluate",
                "params": {"expression": "!!document.querySelector('table')", "returnByValue": True}}))
            try:
                while True:
                    r = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
                    if r.get("id") == check_id:
                        rendered = rendered or bool(r.get("result", {}).get("result", {}).get("value"))
                        break
                    collect(r)
            except asyncio.TimeoutError:
                pass
            if rendered and time.time() - t0 > settle:
                break

        if not rendered:
            errors.append("SMOKE-HARNESS: 对比表未渲染(无 <table>)，未能覆盖图表路径")

    return errors


def main() -> int:
    chrome = _find_chrome()
    if chrome is None:
        print("[skip] 未找到 Chrome/Chromium(设置 $CHROME 或装到 PATH)。跳过冒烟测试。")
        return 0
    try:
        import requests  # noqa
        import websockets  # noqa
    except Exception:
        print("[skip] 缺少 requests/websockets。跳过冒烟测试。")
        return 0

    sample = os.path.join(HERE, "sample_data")
    if not os.path.isdir(sample) or not [f for f in os.listdir(sample) if f.endswith(".json")]:
        subprocess.run([sys.executable, os.path.join(HERE, "generate_sample_data.py"), sample], check=True)

    app_port = _free_port()
    cdp_port = _free_port()
    env = dict(os.environ, PERFKANBAN_PRESELECT="all")
    st_proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", os.path.join(HERE, "perf_kanban.py"), sample,
         "--server.headless", "true", "--server.port", str(app_port),
         "--server.address", "127.0.0.1", "--browser.gatherUsageStats", "false",
         "--server.fileWatcherType", "none"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
    )
    profile = os.path.join("/tmp", f"perfkanban_smoke_{cdp_port}")
    cr_proc = subprocess.Popen(
        [chrome, "--headless=new", f"--remote-debugging-port={cdp_port}", "--no-sandbox",
         "--disable-gpu", f"--user-data-dir={profile}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        if not _wait_http(f"http://127.0.0.1:{app_port}/"):
            print("[fail] streamlit 未在限定时间内启动")
            return 1
        if not _wait_http(f"http://127.0.0.1:{cdp_port}/json"):
            print("[fail] chrome 调试端口未就绪")
            return 1
        errors = asyncio.run(_collect_console_errors(
            f"http://127.0.0.1:{cdp_port}/json", f"http://127.0.0.1:{app_port}/"))
    finally:
        for p in (cr_proc, st_proc):
            p.terminate()
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()
        shutil.rmtree(profile, ignore_errors=True)

    if errors:
        print(f"[FAIL] 浏览器控制台有 {len(errors)} 条报错:")
        for e in errors[:10]:
            print("  -", e.splitlines()[0][:160])
        return 1
    print("[PASS] 浏览器控制台零报错(对比页/图表渲染正常)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

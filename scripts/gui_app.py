#!/usr/bin/env python3
"""桌面客户端入口：原生窗口承载 GUI，不打开浏览器。

实现：
  - 后台线程跑 gui_server 的 Flask 应用（127.0.0.1，端口被占则自动换）
  - pywebview 起原生窗口（Windows 下为 Edge WebView2 内核），加载本地服务
  - 关闭窗口即退出整个程序（Flask 线程为 daemon）
  - WebView2 运行时缺失等异常时回退到系统浏览器，功能不受影响

用法：
  python scripts/gui_app.py             # 桌面窗口
  python scripts/gui_app.py --server    # 仅起 HTTP 服务不开窗口（CI 冒烟用）
"""

from __future__ import annotations

import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gui_server  # noqa: E402

WINDOW_TITLE = "公众号助手"
WINDOW_SIZE = (1180, 800)
MIN_SIZE = (980, 640)


def pick_port(preferred: int) -> int:
    """优先用配置端口；被占用则让系统分配空闲端口（桌面模式端口无需固定）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(port: int) -> None:
    threading.Thread(
        target=lambda: gui_server.app.run(
            host="127.0.0.1", port=port, debug=False, threaded=True
        ),
        daemon=True,
    ).start()


def wait_ready(url: str, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    import os

    server_only = "--server" in sys.argv
    port = pick_port(int(os.getenv("GUI_PORT", "8093") or 8093))
    url = f"http://127.0.0.1:{port}"

    start_server(port)
    if not wait_ready(url):
        print("[错误] 本地服务启动失败")
        return 1

    if server_only:
        print(f"服务已就绪：{url}（--server 模式，Ctrl+C 退出）")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0

    try:
        import webview

        window = webview.create_window(
            WINDOW_TITLE,
            url,
            width=WINDOW_SIZE[0],
            height=WINDOW_SIZE[1],
            min_size=MIN_SIZE,
            background_color="#f5f5f7",   # 与页面底色一致，加载时不闪白
        )
        # 拦截窗口关闭：编辑器有未保存修改时先确认（复用前端 MD_DIRTY 状态）
        def on_closing():
            try:
                dirty = window.evaluate_js("window.MD_DIRTY === true")
                if dirty:
                    return window.evaluate_js(
                        "window.confirm('文章有未保存的修改，确定退出吗？')"
                    )
            except Exception:
                pass
            return True

        window.events.closing += on_closing
        webview.start()
        return 0
    except Exception as e:
        # WebView2 运行时缺失等场景：回退浏览器，程序保持可用
        print(f"[提示] 原生窗口不可用（{e}），回退到浏览器模式")
        import webbrowser

        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())

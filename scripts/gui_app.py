#!/usr/bin/env python3
"""桌面客户端入口：原生窗口承载 GUI，不打开浏览器。

实现：
  - 后台线程跑 gui_server 的 Flask 应用（127.0.0.1，端口被占则自动换）
  - pywebview 起原生窗口（Windows 下为 Edge WebView2 内核），加载本地服务
  - 关闭窗口即退出整个程序（Flask 线程为 daemon）
  - 未保存确认：前端通过 js_api 桥把脏状态推给 Python 侧变量，
    关窗事件里只读变量 + 原生 MessageBox —— 严禁在 closing 事件里
    evaluate_js（UI 线程互等会死锁，表现为关窗卡住）
  - 窗口化 exe 无控制台，stdout/stderr 重定向到数据目录 app.log
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

from app_paths import app_root  # noqa: E402

# 窗口化 exe（console=False）里 stdout/stderr 为 None，任何 print 都会抛
# AttributeError；重定向到日志文件，业务模块的 print 原样保留
if sys.stdout is None or sys.stderr is None:
    _log_path = app_root() / "app.log"
    # 简单轮转：超 2MB 保留上一份为 app.log.1，防止无限增长
    try:
        if _log_path.exists() and _log_path.stat().st_size > 2 * 1024 * 1024:
            _log_path.replace(_log_path.with_suffix(".log.1"))
    except OSError:
        pass
    _log = open(_log_path, "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = _log
    if sys.stderr is None:
        sys.stderr = _log

import gui_server  # noqa: E402

WINDOW_TITLE = "公众号助手"
WINDOW_SIZE = (1180, 800)
MIN_SIZE = (980, 640)


class Bridge:
    """暴露给页面 JS 的桥（window.pywebview.api.*）。"""

    def __init__(self) -> None:
        self.dirty = False

    def set_dirty(self, value: bool) -> None:
        """前端编辑器脏状态变化时同步过来，供关窗确认使用。"""
        self.dirty = bool(value)


def confirm_native(text: str) -> bool:
    """原生确认框（Win32 MessageBox），可安全运行在 UI 线程。"""
    try:
        import ctypes

        idyes = 6
        # MB_YESNO | MB_ICONWARNING | MB_TOPMOST
        return (
            ctypes.windll.user32.MessageBoxW(
                None, text, WINDOW_TITLE, 0x04 | 0x30 | 0x40000
            )
            == idyes
        )
    except Exception:
        return True  # 非 Windows 或调用失败：不阻塞退出


# 实例锁：最多允许 2 个实例同时运行
_instance_locks: list[socket.socket] = []
LOCK_PORTS = [18093, 18094]  # 两个锁端口，对应 2 个实例槽位


def acquire_instance_slot() -> bool:
    """尝试占用一个实例槽位（绑定锁端口），成功返回 True。

    返回 False 表示已有 2 个实例在运行，应拒绝启动。
    全局 _instance_locks 持有 socket 对象，进程退出时自动释放端口。
    """
    for lock_port in LOCK_PORTS:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)  # 禁止复用
            sock.bind(("127.0.0.1", lock_port))
            _instance_locks.append(sock)  # 持有引用，保持端口占用
            return True
        except OSError:
            continue  # 该槽位已被占用，尝试下一个
    return False  # 所有槽位都满了


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
    def _run() -> None:
        try:
            # HTTPServer.server_bind 会调 socket.getfqdn 做 DNS 反查，
            # 部分环境（如 mac CI runner）反查超时可卡 30-75 秒导致启动失败；
            # 该值仅用于日志与响应头，补丁掉无功能影响
            socket.getfqdn = lambda name="": name or "127.0.0.1"
            from werkzeug.serving import make_server

            srv = make_server("127.0.0.1", port, gui_server.app, threaded=True)
            print(f" * 本地服务已监听 http://127.0.0.1:{port}")
            srv.serve_forever()
        except BaseException:
            # daemon 线程异常默认无声无息，冒烟/诊断时必须能看到根因
            import traceback

            print("[错误] Flask 服务线程崩溃：", file=sys.stderr)
            traceback.print_exc()

    threading.Thread(target=_run, daemon=True).start()


def wait_ready(url: str, timeout: float = 30.0) -> bool:
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

    # 实例数量检查：最多 2 个
    if not acquire_instance_slot():
        msg = "已有 2 个实例正在运行，无法启动更多实例。\n\n请关闭其中一个窗口后重试。"
        try:
            confirm_native(msg)  # 用原生对话框提示（桌面环境）
        except Exception:
            print(f"[错误] {msg}", file=sys.stderr)
        return 1

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

        bridge = Bridge()
        window = webview.create_window(
            WINDOW_TITLE,
            url,
            js_api=bridge,
            width=WINDOW_SIZE[0],
            height=WINDOW_SIZE[1],
            min_size=MIN_SIZE,
            background_color="#f5f5f7",   # 与页面底色一致，加载时不闪白
        )

        def on_closing():
            # 只读 Python 侧状态，绝不在此调 evaluate_js（会死锁）
            running = gui_server._running_task()
            if running:
                return confirm_native(
                    f"「{running['name']}」仍在进行中，关闭会中断它。确定退出吗？"
                )
            if bridge.dirty:
                return confirm_native("文章有未保存的修改，确定退出吗？")
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

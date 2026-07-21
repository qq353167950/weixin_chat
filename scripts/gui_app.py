#!/usr/bin/env python3
"""桌面客户端入口：原生窗口承载 GUI，不打开浏览器。

实现：
  - 后台线程跑 gui_server 的 Flask 应用（127.0.0.1，端口被占则自动换）
  - pywebview 起原生窗口（Windows 下为 Edge WebView2 内核），加载本地服务
  - 点 X 首次弹「退出 / 最小化到托盘」选择层（可勾选记住，存 ui_state.json）；
    最小化走 pystray 托盘图标，托盘菜单可恢复窗口或退出
  - 未保存/任务运行中退出前有原生确认；closing 事件里严禁 evaluate_js
    （UI 线程互等死锁），弹选择层由子线程延迟触发
  - 窗口化 exe 无控制台，stdout/stderr 重定向到数据目录 app.log（2MB 轮转）
  - WebView2 运行时缺失等异常时回退到系统浏览器，功能不受影响

用法：
  python scripts/gui_app.py             # 桌面窗口
  python scripts/gui_app.py --server    # 仅起 HTTP 服务不开窗口（CI 冒烟用）
"""

from __future__ import annotations

import json
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

# 关窗行为偏好：ask=每次询问 / exit=直接退出 / tray=最小化到托盘
_UI_STATE_FILE = app_root() / "ui_state.json"


def load_close_action() -> str:
    try:
        data = json.loads(_UI_STATE_FILE.read_text(encoding="utf-8"))
        v = str(data.get("close_action", "ask"))
        return v if v in {"ask", "exit", "tray"} else "ask"
    except Exception:
        return "ask"


def save_close_action(action: str) -> None:
    try:
        data = {}
        if _UI_STATE_FILE.exists():
            data = json.loads(_UI_STATE_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        data["close_action"] = action
        _UI_STATE_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


class AppState:
    """窗口/托盘共享状态。"""

    def __init__(self) -> None:
        self.window = None
        self.tray = None            # pystray.Icon | None
        self.quitting = False       # 走真正退出流程（跳过 closing 拦截）


APP = AppState()


class Bridge:
    """暴露给页面 JS 的桥（window.pywebview.api.*）。"""

    def __init__(self) -> None:
        self.dirty = False

    def set_dirty(self, value: bool) -> None:
        """前端编辑器脏状态变化时同步过来，供关窗确认使用。"""
        self.dirty = bool(value)

    def close_choice(self, action: str, remember: bool) -> None:
        """关窗选择层的回调：action = exit / tray。"""
        action = action if action in {"exit", "tray"} else "exit"
        if remember:
            save_close_action(action)
        if action == "tray":
            minimize_to_tray()
        else:
            request_quit()


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


def _tray_image():
    """托盘图标：蓝底「稿」字（与应用图标一致）。"""
    from PIL import Image, ImageDraw, ImageFont

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=14, fill=(0, 113, 227, 255))
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 38)
        bbox = d.textbbox((0, 0), "稿", font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.text(((size - w) / 2 - bbox[0], (size - h) / 2 - bbox[1]), "稿",
               font=font, fill=(255, 255, 255, 255))
    except Exception:
        pass
    return img


def show_window() -> None:
    if APP.window is not None:
        try:
            APP.window.show()
            APP.window.restore()
        except Exception:
            pass
    stop_tray()


def stop_tray() -> None:
    if APP.tray is not None:
        try:
            APP.tray.stop()
        except Exception:
            pass
        APP.tray = None


def minimize_to_tray() -> None:
    """隐藏窗口并挂托盘图标（pystray 跑独立线程）。"""
    if APP.window is None:
        return
    try:
        APP.window.hide()
    except Exception:
        return
    if APP.tray is not None:
        return
    try:
        import pystray

        menu = pystray.Menu(
            pystray.MenuItem("打开 公众号助手", lambda: show_window(), default=True),
            pystray.MenuItem("退出", lambda: request_quit()),
        )
        APP.tray = pystray.Icon("weixin_chat", _tray_image(), WINDOW_TITLE, menu)
        threading.Thread(target=APP.tray.run, daemon=True).start()
    except Exception as e:
        # 托盘不可用（如缺依赖）：恢复窗口，退化为普通关闭询问
        print(f"[提示] 托盘不可用（{e}），已恢复窗口")
        try:
            APP.window.show()
        except Exception:
            pass


def request_quit() -> None:
    """真正退出：置标志后销毁窗口（closing 拦截会放行）。"""
    APP.quitting = True
    stop_tray()
    if APP.window is not None:
        try:
            APP.window.show()      # destroy 需要窗口存在于前台线程
        except Exception:
            pass
        try:
            APP.window.destroy()
        except Exception:
            import os

            os._exit(0)


def _show_close_dialog() -> None:
    """子线程里调 evaluate_js 弹页面内选择层（不能在 closing 事件里做）。"""
    if APP.window is None:
        return
    try:
        APP.window.evaluate_js("window.showCloseDialog && window.showCloseDialog()")
    except Exception:
        # 页面未就绪等异常：退化为直接退出确认
        if confirm_native("确定退出程序吗？"):
            request_quit()


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
        APP.window = window

        def on_closing():
            # 只读 Python 侧状态，绝不在此调 evaluate_js（会死锁）
            if APP.quitting:
                return True    # request_quit 发起的销毁，放行

            action = load_close_action()
            if action == "tray":
                threading.Timer(0.05, minimize_to_tray).start()
                return False   # 拦下关闭，转为隐藏

            # exit / ask 共同的退出前保护
            running = gui_server._running_task()
            if running:
                return confirm_native(
                    f"「{running['name']}」仍在进行中，关闭会中断它。确定退出吗？"
                )
            if action == "exit":
                if bridge.dirty:
                    return confirm_native("文章有未保存的修改，确定退出吗？")
                return True

            # ask：拦下本次关闭，弹页面内选择层（勾选可记住）
            threading.Timer(0.05, _show_close_dialog).start()
            return False

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

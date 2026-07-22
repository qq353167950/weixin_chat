#!/usr/bin/env python3
"""图形界面本地服务：浏览器操作全流程（选题 → 文章 → 封面 → 预览发布 → 设置）。

设计：
  - Flask 提供静态单页（gui/index.html）+ JSON API
  - 耗时操作（搜索/写作/生图/上传）跑后台线程，前端轮询 /api/task/<id> 拿日志与结果
  - 业务逻辑全部复用 scripts/ 既有模块，本文件只做编排与状态管理
  - 产出目录沿用 runs/<时间戳>，与命令行 pipeline 完全一致

启动：
  python scripts/gui_server.py   或双击 GUI.bat
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app_paths import app_root, asset_dir, ensure_env_file  # noqa: E402

ROOT = app_root()          # 可写：.env / runs
ASSETS = asset_dir()       # 只读：gui 页面（打包后位于 _MEIPASS）

ensure_env_file()
load_dotenv(ROOT / ".env")
load_dotenv()

from article_writer import (  # noqa: E402
    build_article_prompt,
    detect_ai_phrases,
    fetch_reference_articles,
    illustrate_article,
    llm_rank_topics,
    local_fallback_article,
    refs_to_prompt_block,
    scrub_citations,
)
from content_images import count_external_images, replace_content_images  # noqa: E402
from env_store import read_env_values, update_env_file  # noqa: E402
from generate_cover import generate_cover as template_cover  # noqa: E402
from generate_cover_ai import generate_ai_cover  # noqa: E402
from llm_client import llm_chat, llm_config  # noqa: E402
from markdown_to_wechat_html import (  # noqa: E402
    THEMES,
    _strip_outer_fence,
    build_preview_html,
    extract_title_and_body,
    make_digest,
    markdown_to_wechat_html,
)
from topic_search import resolve_provider, search_hot_materials  # noqa: E402
from task_hooks import TaskCancelled as TaskCancelledHook  # noqa: E402
from task_hooks import clear_hooks, set_hooks  # noqa: E402
from version import __version__, check_update  # noqa: E402
from wechat_client import WeChatClient  # noqa: E402

app = Flask(__name__)

# ---------------- 会话状态 ----------------
_LOCK = threading.Lock()
STATE: dict = {
    "work_dir": None,          # Path | None
    "topics": [],              # 候选选题
    "topic": None,             # 已选选题
    "cover": False,
    "theme": os.getenv("DEFAULT_THEME", "default"),
    "publish": None,           # {"media_id": ...}
}

TASKS: dict[str, dict] = {}
# 已结束任务最多保留条数，超出按创建顺序淘汰（防止长期挂机内存膨胀）
TASKS_KEEP_MAX = 50


def _evict_finished_tasks() -> None:
    """淘汰最旧的已结束任务，运行中的任务永不淘汰。调用方需持有 _LOCK。"""
    finished = [tid for tid, t in TASKS.items() if t["status"] != "running"]
    for tid in finished[: max(0, len(finished) - TASKS_KEEP_MAX)]:
        TASKS.pop(tid, None)


class TaskCancelled(BaseException):
    """已废弃：为兼容保留名字，实际使用 task_hooks.TaskCancelled。"""


# 任务快照给前端时隐藏内部字段
def _task_view(task: dict) -> dict:
    return {k: v for k, v in task.items() if k != "cancel"}


def _running_task() -> dict | None:
    """当前运行中的任务（单用户顺序流，同一时刻只允许一个后台任务）。"""
    with _LOCK:
        for t in TASKS.values():
            if t["status"] == "running":
                return t
    return None


def _reject_if_busy():
    """有任务在跑时拒绝启动新任务：防止并发写作/配图写坏同一 article.md。"""
    t = _running_task()
    if t:
        return (
            jsonify({"error": f"「{t['name']}」正在进行中，请等它完成或先取消", "task": t["id"]}),
            409,
        )
    return None


class TaskBusyError(RuntimeError):
    """已有任务运行时试图启动新任务（互斥由 _new_task 在锁内原子保证）。"""

    def __init__(self, running: dict):
        super().__init__(f"「{running['name']}」正在进行中")
        self.running = running


def _new_task(name: str) -> dict:
    task = {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "status": "running",   # running / done / error / cancelled
        "cancel": False,       # 置 True 后任务在下一个探针点退出
        "log": [],
        "progress": "",        # 单行实时进度（界面原地刷新，不入日志）
        "result": None,
        "error": "",
    }
    with _LOCK:
        # 互斥检查必须与登记同锁完成：_reject_if_busy 的预检存在
        # 两请求同时通过的竞态，这里是最终防线
        for t in TASKS.values():
            if t["status"] == "running":
                raise TaskBusyError(t)
        _evict_finished_tasks()
        TASKS[task["id"]] = task
    return task


def _run_task(name: str, fn) -> dict:
    """后台线程执行 fn(task_log)；异常写入 task.error，取消置为 cancelled。

    通过 task_hooks 把取消探针与进度回调下沉到耗时循环内部
    （LLM 流式分块、逐条检索、逐图上传），取消秒级生效。
    并发启动时抛 TaskBusyError（由 Flask errorhandler 统一转 409）。
    """
    task = _new_task(name)

    def log(msg: str) -> None:
        with _LOCK:
            if task["cancel"]:
                raise TaskCancelledHook("已取消")
            task["log"].append(str(msg))
            task["progress"] = ""   # 新日志行意味着上一段进度已完结

    def _cancelled() -> bool:
        with _LOCK:
            return bool(task["cancel"])

    def _progress(msg: str) -> None:
        with _LOCK:
            task["progress"] = msg

    def worker() -> None:
        set_hooks(cancel_check=_cancelled, progress=_progress)
        try:
            result = fn(log)
            with _LOCK:
                task["result"] = result
                task["status"] = "done"
                task["progress"] = ""
        except TaskCancelledHook:
            with _LOCK:
                task["status"] = "cancelled"
                task["error"] = "已取消"
                task["progress"] = ""
        except Exception as e:  # 面向 UI：所有异常转文字
            with _LOCK:
                if task["cancel"]:
                    task["status"] = "cancelled"
                    task["error"] = "已取消"
                else:
                    task["error"] = str(e)
                    task["status"] = "error"
                task["progress"] = ""
        finally:
            clear_hooks()

    threading.Thread(target=worker, daemon=True).start()
    return task


def _prune_runs(keep: int | None = None) -> None:
    """按目录名清理 runs/，保留最近 keep 次产出。RUNS_KEEP=0 不清理。

    保护规则（防误删正在编辑的内容）：
      - 当前实例的 work_dir 永不删
      - 目录内 .inuse 心跳 10 分钟内的不删（另一实例可能正在编辑）
      - 24 小时内修改过的目录不删
    """
    if keep is None:
        keep = int(os.getenv("RUNS_KEEP", "10") or 10)
    if keep <= 0:
        return
    runs_dir = ROOT / "runs"
    if not runs_dir.exists():
        return
    with _LOCK:
        current = Path(STATE["work_dir"]).resolve() if STATE["work_dir"] else None
    import time as _time

    now = _time.time()
    dirs = sorted(
        (d for d in runs_dir.iterdir() if d.is_dir()),
        key=lambda d: d.name,
        reverse=True,
    )
    for old in dirs[keep:]:
        try:
            if current and old.resolve() == current:
                continue
            inuse = old / ".inuse"
            if inuse.exists() and now - inuse.stat().st_mtime < 600:
                continue
            if now - old.stat().st_mtime < 86400:
                continue
            shutil.rmtree(old)
        except OSError:
            pass


def _touch_inuse() -> None:
    """标记当前 work_dir 正在使用（跨实例的清理保护心跳）。"""
    with _LOCK:
        wd = STATE["work_dir"]
    if wd:
        try:
            (Path(wd) / ".inuse").write_text("", encoding="utf-8")
        except OSError:
            pass


def _ensure_run() -> Path:
    """确保存在本次产出目录（与命令行 pipeline 的 runs/<stamp> 一致）。

    同一秒内连续开新 run 时时间戳会撞名，追加序号避免落回旧目录。
    注意：_prune_runs 内部会抢 _LOCK（读 work_dir），必须在锁外调用，
    否则不可重入锁直接死锁（v1.6.2 曾因此让「搜索选题」全线卡死）。
    """
    created = False
    with _LOCK:
        if STATE["work_dir"] is None:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            wd = ROOT / "runs" / stamp
            n = 1
            while wd.exists():
                n += 1
                wd = ROOT / "runs" / f"{stamp}-{n}"
            wd.mkdir(parents=True, exist_ok=True)
            STATE["work_dir"] = wd
            created = True
        result = STATE["work_dir"]
    if created:
        _prune_runs()   # 锁外：内部自行短暂取锁读当前目录
    return result


def _article_path() -> Path | None:
    with _LOCK:
        wd = STATE["work_dir"]
    if wd is None:
        return None
    p = Path(wd) / "article.md"
    return p if p.exists() else None


def _env_summary() -> dict:
    cfg = llm_config()
    image_provider = (
        os.getenv("IMAGE_PROVIDER", "") or os.getenv("COVER_PROVIDER", "openai")
    ).strip().lower()
    return {
        "wechat": bool(os.getenv("WECHAT_APPID") and os.getenv("WECHAT_APPSECRET")),
        "llm": bool(cfg["api_key"]),
        "llm_model": cfg["model"],
        "search_provider": resolve_provider(),
        "image_provider": image_provider,
        "image_key": bool(
            os.getenv("IMAGE_API_KEY", "").strip()
            or os.getenv("DASHSCOPE_API_KEY", "").strip()
        )
        or image_provider in {"template", "local", "pil"},
        "author": os.getenv("WECHAT_AUTHOR", ""),
    }


# ---------------- 页面与运行文件 ----------------
@app.errorhandler(TaskBusyError)
def _on_task_busy(e: TaskBusyError):
    """互斥兜底：并发穿过预检时在 _new_task 锁内拦下，统一 409。"""
    return (
        jsonify({"error": f"{e}，请等它完成或先取消", "task": e.running["id"]}),
        409,
    )


@app.get("/")
def index():
    return send_from_directory(ASSETS / "gui", "index.html")


@app.get("/favicon.ico")
def favicon():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        '<rect width="64" height="64" rx="14" fill="#0071e3"/>'
        '<text x="32" y="43" font-size="30" text-anchor="middle" fill="#fff" '
        'font-family="-apple-system,PingFang SC,sans-serif" font-weight="700">稿</text>'
        "</svg>"
    )
    return app.response_class(svg, mimetype="image/svg+xml")


@app.get("/runfile/<path:relpath>")
def runfile(relpath: str):
    with _LOCK:
        wd = STATE["work_dir"]
    if wd is None:
        return jsonify({"error": "尚未开始"}), 404
    return send_from_directory(Path(wd), relpath)


# ---------------- 状态 ----------------
@app.get("/api/state")
def api_state():
    with _LOCK:
        wd = STATE["work_dir"]
        topics = list(STATE["topics"])
        topic = STATE["topic"]
        theme = STATE["theme"]
        publish = STATE["publish"]
        running = [
            {"id": t["id"], "name": t["name"]}
            for t in TASKS.values()
            if t["status"] == "running"
        ]
    md = _article_path()
    md_text = md.read_text(encoding="utf-8") if md else ""
    title = extract_title_and_body(md_text)[0] if md_text else ""
    cover = bool(wd and (Path(wd) / "cover.jpg").exists())
    return jsonify(
        {
            "run": Path(wd).name if wd else None,
            "topics": topics,
            "topic": topic,
            "has_article": bool(md_text),
            "article_title": title,
            "article_chars": len(md_text),
            "ai_hits": detect_ai_phrases(md_text) if md_text else [],
            "has_cover": cover,
            "theme": theme,
            "themes": {k: v["label"] for k, v in THEMES.items()},
            "publish": publish,
            "tasks": running,
            "env": _env_summary(),
        }
    )


@app.get("/api/version")
def api_version():
    """返回当前版本号。"""
    return jsonify({"version": __version__})


_UPDATE_CHECK_CACHE = {"at": 0.0, "payload": None}


@app.get("/api/check_update")
def api_check_update():
    """检查是否有新版本可用。

    结果缓存 1 小时（GitHub 未认证 API 限流 60 次/小时/IP，
    共享出口 IP 的环境频繁启动会被限流）；?force=1 跳过缓存。
    """
    import time as _time

    force = request.args.get("force") == "1"
    with _LOCK:
        cached = _UPDATE_CHECK_CACHE["payload"]
        fresh = _time.time() - _UPDATE_CHECK_CACHE["at"] < 3600
    if cached and fresh and not force:
        return jsonify(cached)

    has, remote_ver, download_url, changelog = check_update()
    payload = {
        "has_update": has,
        "current_version": __version__,
        "remote_version": remote_ver if has else __version__,
        "download_url": download_url,
        "changelog": changelog,
        "changelog_items": _friendly_changelog(changelog) if has else [],
        "error": changelog if not has and changelog else "",
    }
    if not payload["error"]:   # 失败结果不缓存，便于网络恢复后重试
        with _LOCK:
            _UPDATE_CHECK_CACHE.update({"at": _time.time(), "payload": payload})
    return jsonify(payload)


def _friendly_changelog(raw: str, limit: int = 8) -> list[str]:
    """把 Release Notes 提炼成 1. 2. 3. 条目文案。

    只取 markdown 列表行（- / *）——Release 正文由 RELEASE_NOTES.md 生成，
    条目行即更新内容；表格/说明段落一律忽略。无任何条目时给通用文案，
    绝不把杂项文本编号显示。
    """
    items: list[str] = []
    for ln in (raw or "").splitlines():
        s = ln.strip()
        m = re.match(r"^[-*]\s+(.+)$", s)
        if not m:
            continue
        text = m.group(1)
        if text.startswith("|") or "---" in text[:6]:            # 表格行防御
            continue
        text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)     # 链接→文字
        text = re.sub(r"[*_`#]+", "", text).strip()              # 去格式符
        text = re.sub(r"\s+by\s+@\S+.*$", "", text)              # 去 PR 署名尾巴
        text = re.sub(r"\s+in\s+https?://\S+$", "", text)
        if text and len(text) > 3:
            items.append(text)
    if not items:
        items = ["其他修复与优化"]
    return items[:limit]


UPDATE_STATE = {"status": "idle", "percent": 0, "error": "", "file": ""}


@app.post("/api/update/download")
def api_update_download():
    """内置更新：后台下载安装包 → 就绪后由 /api/update/install 启动安装。

    仅 Windows 安装包（setup.exe）支持静默接管；其余平台仍走浏览器下载。
    """
    body = request.get_json(force=True) or {}
    url = str(body.get("url") or "").strip()
    if not url.startswith("https://github.com/"):
        return jsonify({"error": "仅支持 GitHub Releases 下载地址"}), 400
    with _LOCK:
        if UPDATE_STATE["status"] == "downloading":
            return jsonify({"error": "已在下载中"}), 409
        UPDATE_STATE.update({"status": "downloading", "percent": 0, "error": "", "file": ""})

    def worker():
        import urllib.request as _ur

        try:
            dest = ROOT / "update"
            dest.mkdir(parents=True, exist_ok=True)
            # URL 解码后净化文件名：百分号序列进 bat 会被 cmd 当变量展开导致安装失败
            from urllib.parse import unquote

            raw_name = unquote(url.rsplit("/", 1)[-1])
            safe_name = re.sub(r'[\\/:*?"<>|%]', "_", raw_name) or "update-setup.exe"
            fname = dest / safe_name
            req = _ur.Request(url, headers={"User-Agent": "weixin-chat-updater"})
            with _ur.urlopen(req, timeout=30) as resp, open(fname, "wb") as f:
                total = int(resp.headers.get("Content-Length") or 0)
                got = 0
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    if total:
                        with _LOCK:
                            UPDATE_STATE["percent"] = int(got * 100 / total)
            with _LOCK:
                UPDATE_STATE.update({"status": "ready", "percent": 100, "file": str(fname)})
        except Exception as e:
            with _LOCK:
                UPDATE_STATE.update({"status": "error", "error": str(e)})

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True})


@app.get("/api/update/progress")
def api_update_progress():
    with _LOCK:
        return jsonify(dict(UPDATE_STATE))


@app.post("/api/update/install")
def api_update_install():
    """程序内自替换更新（Chrome 模式，彻底不碰安装器/cmd/UAC）。

    Windows 允许重命名正在运行的 exe：
      1. 当前 exe 改名为 公众号助手.exe.old（运行中的进程不受影响）
      2. 下载好的新版 exe 移动到当前 exe 路径
      3. 启动新版进程，本进程退出
      4. 新版启动时自动清理 .old（见 gui_app 启动逻辑）
    全程纯文件操作，写自己所在目录（用户安装本来就可写），零弹窗。
    """
    with _LOCK:
        if UPDATE_STATE["status"] != "ready" or not UPDATE_STATE["file"]:
            return jsonify({"error": "更新文件未就绪"}), 400
        new_file = Path(UPDATE_STATE["file"])
    import subprocess

    cur_exe = Path(sys.executable).resolve()
    if not getattr(sys, "frozen", False):
        return jsonify({"error": "源码运行模式不支持自更新"}), 400
    old_bak = cur_exe.with_suffix(".exe.old")
    try:
        # 清掉可能残留的上次备份
        if old_bak.exists():
            old_bak.unlink()
        cur_exe.rename(old_bak)          # 运行中的 exe 可以改名
        try:
            shutil.move(str(new_file), str(cur_exe))
        except Exception:
            old_bak.rename(cur_exe)      # 回滚：恢复原名，程序继续可用
            raise
    except Exception as e:
        return jsonify({"error": f"替换失败：{e}"}), 500

    # 衔接顺序：先隐藏旧窗口 → 再启动新版 → 本进程退出。
    # 否则旧窗口要等 os._exit 才消失，会出现新旧两个窗口同屏的尴尬瞬间
    import time as _time

    def _handover() -> None:
        try:
            import gui_app

            if gui_app.APP.window is not None:
                gui_app.APP.quitting = True     # closing 拦截放行
                gui_app.APP.window.hide()
        except Exception:
            pass
        _time.sleep(0.3)
        subprocess.Popen(
            [str(cur_exe)],
            cwd=str(cur_exe.parent),
            close_fds=True,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        )
        _time.sleep(0.7)
        os._exit(0)

    threading.Thread(target=_handover, daemon=True).start()
    return jsonify({"ok": True})


@app.post("/api/new_run")
def api_new_run():
    with _LOCK:
        STATE.update(
            {"work_dir": None, "topics": [], "topic": None, "cover": False, "publish": None}
        )
    _ensure_run()
    return api_state()


# ---------------- 历史产出 ----------------
def _run_brief(d: Path) -> dict:
    """汇总单个 runs/<stamp> 目录的产出概况。"""
    md = d / "article.md"
    title = ""
    if md.exists():
        try:
            title = extract_title_and_body(md.read_text(encoding="utf-8"))[0]
        except Exception:
            title = ""
    media_id = ""
    result = d / "result.txt"
    if result.exists():
        try:
            m = re.search(r"media_id=(\S+)", result.read_text(encoding="utf-8"))
            media_id = m.group(1) if m else ""
        except Exception:
            media_id = ""
    return {
        "run": d.name,
        "title": title,
        "has_article": md.exists(),
        "has_cover": (d / "cover.jpg").exists(),
        "media_id": media_id,
    }


@app.get("/api/runs")
def api_runs():
    runs_dir = ROOT / "runs"
    if not runs_dir.exists():
        return jsonify({"runs": []})
    with _LOCK:
        current = Path(STATE["work_dir"]).name if STATE["work_dir"] else None
    dirs = sorted(
        (d for d in runs_dir.iterdir() if d.is_dir()),
        key=lambda d: d.name,
        reverse=True,
    )
    out = []
    for d in dirs:
        brief = _run_brief(d)
        brief["current"] = d.name == current
        out.append(brief)
    return jsonify({"runs": out})


@app.get("/api/runs/cover/<name>")
def api_runs_cover(name: str):
    """历史列表的封面缩略图（仅限时间戳目录名，防路径穿越）。"""
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}(?:-[0-9]{1,3})?", name):
        return jsonify({"error": "无效的记录名"}), 400
    d = ROOT / "runs" / name
    if not (d / "cover.jpg").exists():
        return jsonify({"error": "无封面"}), 404
    return send_from_directory(d, "cover.jpg")


@app.post("/api/runs/delete")
def api_runs_delete():
    """删除一条历史产出（整个 runs/<stamp> 目录）。当前正在编辑的不允许删。"""
    name = str((request.get_json(force=True) or {}).get("run") or "").strip()
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}(?:-[0-9]{1,3})?", name):
        return jsonify({"error": "无效的记录名"}), 400
    wd = ROOT / "runs" / name
    if not wd.is_dir():
        return jsonify({"error": "记录不存在"}), 404
    with _LOCK:
        current = STATE["work_dir"]
    if current is not None and Path(current).name == name:
        return jsonify({"error": "该记录正在编辑中，请先「再写一篇」后再删除"}), 400
    try:
        shutil.rmtree(wd)
    except OSError as e:
        return jsonify({"error": f"删除失败（文件可能被占用）：{e}"}), 500
    return jsonify({"ok": True})


@app.post("/api/runs/open")
def api_runs_open():
    """重新打开历史产出：恢复文章/封面/选题到当前会话，可继续编辑或重新发布。"""
    name = str((request.get_json(force=True) or {}).get("run") or "").strip()
    # 目录名只允许时间戳格式，防止路径穿越
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}(?:-[0-9]{1,3})?", name):
        return jsonify({"error": "无效的记录名"}), 400
    wd = ROOT / "runs" / name
    if not wd.is_dir():
        return jsonify({"error": "记录不存在"}), 404
    topic = None
    topic_file = wd / "topic.json"
    if topic_file.exists():
        try:
            topic = json.loads(topic_file.read_text(encoding="utf-8"))
        except Exception:
            topic = None
    topics = []
    topics_file = wd / "topics.json"
    if topics_file.exists():
        try:
            topics = json.loads(topics_file.read_text(encoding="utf-8"))
        except Exception:
            topics = []
    with _LOCK:
        STATE.update(
            {"work_dir": wd, "topics": topics, "topic": topic, "publish": None}
        )
    _touch_inuse()   # 打开旧目录即打心跳，防另一实例清理误删
    return api_state()


# ---------------- 选题 ----------------
@app.post("/api/topics/search")
def api_topics_search():
    busy = _reject_if_busy()
    if busy:
        return busy
    body = request.get_json(force=True) or {}
    domain = str(body.get("domain") or os.getenv("SEARCH_DOMAIN", "")).strip()
    extra = str(body.get("extra") or "").strip()
    want_n = max(3, min(int(body.get("want_n") or 5), 10))
    wd = _ensure_run()

    def job(log):
        log(f"搜索源：{resolve_provider()}")
        log("正在真实联网搜索热点素材…")
        meta, materials = search_hot_materials(domain=domain, extra_query=extra)
        (wd / "search_raw.json").write_text(
            json.dumps({"meta": meta, "materials": materials}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log(f"搜索完成：{meta}")
        if not materials:
            raise RuntimeError("没有搜到任何素材，请换关键词或检查搜索配置")
        log("正在用大模型整理候选选题…")
        topics = llm_rank_topics(materials, domain=domain, want_n=want_n)
        if not topics:
            raise RuntimeError("大模型未返回有效选题")
        (wd / "topics.json").write_text(
            json.dumps(topics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with _LOCK:
            STATE["topics"] = topics
        log(f"整理出 {len(topics)} 个候选选题")
        return {"topics": topics}

    task = _run_task("搜索选题", job)
    return jsonify({"task": task["id"]})


@app.post("/api/topics/select")
def api_topics_select():
    body = request.get_json(force=True) or {}
    title = str(body.get("title") or "").strip()
    if not title:
        return jsonify({"error": "标题不能为空"}), 400
    topic = {
        "title": title,
        "type": str(body.get("type") or "干货文"),
        "angle": str(body.get("angle") or ""),
        "why": str(body.get("why") or "用户自定义"),
        "audience": str(body.get("audience") or ""),
        "refs": str(body.get("refs") or ""),
        "user_extra": str(body.get("user_extra") or ""),
    }
    # 换了选题且当前 run 已有产出（文章/封面）→ 开新 run，
    # 旧产出原样留在历史里；避免新选题却显示旧文章旧封面
    switched = False
    with _LOCK:
        old = STATE["topic"]
        wd0 = STATE["work_dir"]
        has_output = bool(
            wd0 and ((Path(wd0) / "article.md").exists() or (Path(wd0) / "cover.jpg").exists())
        )
        if old and old.get("title") != title and has_output:
            # 保留候选选题列表，其余会话状态重置
            STATE.update({"work_dir": None, "cover": False, "publish": None})
            switched = True
    wd = _ensure_run()
    with _LOCK:
        STATE["topic"] = topic
    # 落盘：历史记录重新打开时可恢复选题
    (wd / "topic.json").write_text(
        json.dumps(topic, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return jsonify({"topic": topic, "new_run": switched})


# ---------------- 文章 ----------------
@app.post("/api/article/write")
def api_article_write():
    busy = _reject_if_busy()
    if busy:
        return busy
    body = request.get_json(force=True) or {}
    mode = str(body.get("mode") or "llm")
    extra = str(body.get("user_extra") or "").strip()
    with _LOCK:
        src = STATE["topic"]
        if src and extra:
            src["user_extra"] = extra
        topic = dict(src) if src else None   # job 线程用副本，避免锁外共享
    if not topic:
        return jsonify({"error": "请先选定选题"}), 400
    wd = _ensure_run()
    if extra:
        # 补充要求同步落盘：历史记录重新打开时不丢
        (wd / "topic.json").write_text(
            json.dumps(topic, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def job(log):
        if mode == "fallback":
            log("生成本地提纲稿（不调用写作模型）")
            md_text = local_fallback_article(topic)
        else:
            refs_block = ""
            if os.getenv("ARTICLE_FETCH_REFS", "1") != "0":
                raw = wd / "search_raw.json"
                materials = []
                if raw.exists():
                    try:
                        materials = json.loads(raw.read_text(encoding="utf-8")).get(
                            "materials"
                        ) or []
                    except Exception:
                        materials = []
                if materials:
                    log("抓取参考文章原文（学语感，不抄袭）…")
                    refs = fetch_reference_articles(materials, topic, log=log)
                    if refs:
                        refs_block = refs_to_prompt_block(refs)
                        (wd / "reference_articles.json").write_text(
                            json.dumps(refs, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        log(f"抓到 {len(refs)} 篇参考原文")
            log("正在写稿（可能需要 1-2 分钟）…")
            md_text = llm_chat(
                build_article_prompt(topic, topic.get("user_extra", ""), refs_block)
            )
        # LLM 偶尔把全文包在 ```markdown 围栏里 → 渲染成一整个代码框
        md_text = _strip_outer_fence(md_text)
        if not md_text.lstrip().startswith("#"):
            md_text = f"# {topic['title']}\n\n{md_text}"
        # 兜底：清掉模型塞进正文的引用角标链接
        md_text = scrub_citations(md_text)
        (wd / "article.md").write_text(md_text, encoding="utf-8")
        hits = detect_ai_phrases(md_text)
        if hits:
            log(f"AI腔检测：命中 {len(hits)} 个（{'、'.join(hits[:6])}）")
        else:
            log("AI腔检测：干净")
        log(f"完成，共 {len(md_text)} 字")
        return {"chars": len(md_text), "ai_hits": hits}

    task = _run_task("生成文章", job)
    return jsonify({"task": task["id"]})


@app.get("/api/article")
def api_article_get():
    md = _article_path()
    return jsonify({"md": md.read_text(encoding="utf-8") if md else ""})


@app.post("/api/article")
def api_article_save():
    body = request.get_json(force=True) or {}
    md_text = str(body.get("md") or "")
    if not md_text.strip():
        return jsonify({"error": "内容为空"}), 400
    wd = _ensure_run()
    (wd / "article.md").write_text(md_text, encoding="utf-8")
    _touch_inuse()   # 心跳：保护本目录不被另一实例的清理误删
    return jsonify({"ok": True, "ai_hits": detect_ai_phrases(md_text)})


@app.post("/api/article/check_ai")
def api_article_check_ai():
    """手动触发 AI 味检测（对编辑器当前内容，不落盘）。"""
    md_text = str((request.get_json(force=True) or {}).get("md") or "")
    if not md_text.strip():
        return jsonify({"error": "内容为空"}), 400
    return jsonify({"ai_hits": detect_ai_phrases(md_text)})


@app.post("/api/article/deai")
def api_article_deai():
    """一键去 AI 味：让写作模型重写命中 AI 腔的表达，保持内容与结构不变。"""
    busy = _reject_if_busy()
    if busy:
        return busy
    md = _article_path()
    if not md:
        return jsonify({"error": "还没有文章"}), 400
    wd = _ensure_run()

    def job(log):
        md_text = md.read_text(encoding="utf-8")
        hits = detect_ai_phrases(md_text)
        if not hits:
            log("当前没有检测到 AI 腔用词，无需处理")
            return {"ai_hits": [], "changed": False}
        log(f"检测到 {len(hits)} 个 AI 腔用词：{'、'.join(hits[:8])}")
        log("正在让写作模型润色去味（保持内容不变）…")
        system = (
            "你是资深编辑，任务是把文章里的 AI 腔表达改写得像真人写的。\n"
            "【只做这一件事】替换下列 AI 腔用词及其所在句子的表达方式，"
            "其余内容一个字都不要改：不要增删段落，不要改标题，"
            "不要改数字与事实，不要改 Markdown 结构与图片链接。\n"
            f"需要处理的用词：{'、'.join(hits)}\n"
            "改写原则：口语自然、有具体感；禁止用其他 AI 腔词替代。\n"
            "只输出改写后的完整 Markdown 全文，不要任何解释。"
        )
        new_md = llm_chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": md_text}],
            temperature=0.4,
        )
        new_md = _strip_outer_fence(new_md)
        new_md = scrub_citations(new_md)
        # 安全阀：长度骤降说明模型丢内容，拒绝采用
        if len(new_md) < len(md_text) * 0.7:
            raise RuntimeError(
                f"改写后长度异常（{len(md_text)}→{len(new_md)} 字），已放弃本次结果，原文未动"
            )
        md.write_text(new_md, encoding="utf-8")
        left = detect_ai_phrases(new_md)
        if left:
            log(f"完成。残留 {len(left)} 个：{'、'.join(left[:6])}（可再点一次）")
        else:
            log("完成，AI 腔已清零 ✓")
        return {"ai_hits": left, "changed": True}

    task = _run_task("去除AI味", job)
    return jsonify({"task": task["id"]})


@app.post("/api/article/illustrate")
def api_article_illustrate():
    busy = _reject_if_busy()
    if busy:
        return busy
    md = _article_path()
    if not md:
        return jsonify({"error": "还没有文章"}), 400
    wd = _ensure_run()

    def job(log):
        md_text = md.read_text(encoding="utf-8")
        new_md, report = illustrate_article(md_text, wd, log=log)
        for line in report:
            log(line)
        if new_md != md_text:
            md.write_text(new_md, encoding="utf-8")
        return {"report": report}

    task = _run_task("生成配图", job)
    return jsonify({"task": task["id"]})


# ---------------- 封面 ----------------
@app.post("/api/cover/generate")
def api_cover_generate():
    busy = _reject_if_busy()
    if busy:
        return busy
    md = _article_path()
    if not md:
        return jsonify({"error": "还没有文章"}), 400
    wd = _ensure_run()
    body = request.get_json(force=True) or {}
    style = str(body.get("style") or os.getenv("IMAGE_STYLE", "editorial"))
    use_template = bool(body.get("template"))

    def job(log):
        title, _ = extract_title_and_body(md.read_text(encoding="utf-8"))
        out = wd / "cover.jpg"
        if use_template:
            log("使用本地文字模板封面（不调生图 API）")
            template_cover(title, out, theme="default")
        else:
            provider = (
                os.getenv("IMAGE_PROVIDER", "") or os.getenv("COVER_PROVIDER", "openai")
            ).strip()
            log(f"生图 provider={provider} style={style}，可能需要几十秒…")
            overlay = os.getenv(
                "IMAGE_OVERLAY_TITLE", os.getenv("COVER_OVERLAY_TITLE", "1")
            ) != "0"
            generate_ai_cover(
                title, out, abstract="", provider=provider, style=style, overlay=overlay
            )
        log("封面已生成")
        return {"cover": True}

    task = _run_task("生成封面", job)
    return jsonify({"task": task["id"]})


@app.post("/api/cover/upload")
def api_cover_upload():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "没有收到文件"}), 400
    raw = f.read()
    # 微信永久素材图片上限 10MB，留出余量提前拦截
    if len(raw) > 10 * 1024 * 1024:
        return jsonify({"error": "图片超过 10MB，请压缩后再上传"}), 400
    # 校验确为图片并统一转 JPG（微信封面接口按 image/jpeg 上传）
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(raw)) as img:
            img.load()
            rgb = img.convert("RGB")
    except Exception:
        return jsonify({"error": "文件不是有效图片，请上传 JPG/PNG"}), 400
    wd = _ensure_run()
    rgb.save(wd / "cover.jpg", format="JPEG", quality=92)
    return jsonify({"ok": True})


# ---------------- 预览与发布 ----------------
def _preview_payload(theme: str) -> dict:
    md = _article_path()
    if not md:
        raise RuntimeError("还没有文章")
    md_text = md.read_text(encoding="utf-8")
    title, body = extract_title_and_body(md_text)
    html = markdown_to_wechat_html(body, theme=theme)
    # 相对图片路径 → GUI 静态路由，浏览器里才能显示
    html_view = re.sub(r'src="(images/[^"]+)"', r'src="/runfile/\1"', html)
    page = build_preview_html(
        title,
        html_view,
        author=os.getenv("WECHAT_AUTHOR", ""),
        theme_label=f"主题：{theme}（{THEMES[theme]['label']}）",
    )
    return {
        "title": title,
        "digest": make_digest(body),
        "author": os.getenv("WECHAT_AUTHOR", ""),
        "preview": page,
        "theme": theme,
    }


@app.post("/api/render_text")
def api_render_text():
    """把请求体里的 Markdown 直接渲染为微信排版 HTML（编辑器实时预览用，不落盘）。"""
    body = request.get_json(force=True) or {}
    md_text = str(body.get("md") or "")
    if len(md_text) > 200_000:
        return jsonify({"error": "文章过长，实时预览已暂停（不影响保存与发布）"}), 413
    theme = str(body.get("theme") or STATE["theme"])
    if theme not in THEMES:
        theme = "default"
    _, body_md = extract_title_and_body(md_text) if md_text.lstrip().startswith("#") else ("", md_text)
    html = markdown_to_wechat_html(body_md, theme=theme)
    # 相对图片路径 → GUI 静态路由
    html = re.sub(r'src="(images/[^"]+)"', r'src="/runfile/\1"', html)
    return jsonify({"html": html, "theme": theme})


@app.post("/api/render")
def api_render():
    body = request.get_json(force=True) or {}
    with _LOCK:
        theme = str(body.get("theme") or STATE["theme"])
        if theme not in THEMES:
            theme = "default"
        STATE["theme"] = theme
    try:
        return jsonify(_preview_payload(theme))
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/publish")
def api_publish():
    busy = _reject_if_busy()
    if busy:
        return busy
    body = request.get_json(force=True) or {}
    with _LOCK:
        theme = str(body.get("theme") or STATE["theme"])
        wd = STATE["work_dir"]
    if theme not in THEMES:
        theme = "default"
    # 发布前可在界面上覆盖标题/摘要/作者（不落回 article.md，只影响本次草稿）
    override_title = str(body.get("title") or "").strip()
    override_digest = str(body.get("digest") or "").strip()
    override_author = str(body.get("author") or "").strip()
    md = _article_path()
    if not md or wd is None:
        return jsonify({"error": "还没有文章"}), 400
    wd = Path(wd)
    if not (wd / "cover.jpg").exists():
        return jsonify({"error": "还没有封面，公众号草稿必须有封面"}), 400
    if not (os.getenv("WECHAT_APPID") and os.getenv("WECHAT_APPSECRET")):
        return jsonify({"error": "未配置微信 AppID/AppSecret，请到设置页填写"}), 400

    def job(log):
        md_text = md.read_text(encoding="utf-8")
        title, body_md = extract_title_and_body(md_text)
        if override_title:
            title = override_title
        html = markdown_to_wechat_html(body_md, theme=theme)
        (wd / "article.wechat.html").write_text(html, encoding="utf-8")
        client = WeChatClient(
            os.environ["WECHAT_APPID"].strip(), os.environ["WECHAT_APPSECRET"].strip()
        )
        log("1/4 获取 access_token …")
        client.get_access_token()
        n_ext = count_external_images(html)
        if n_ext:
            log(f"2/4 正文 {n_ext} 张站外/本地图片转微信图床 …")
            html, report = replace_content_images(
                html, client, base_dir=wd, cache_dir=wd / "content_images"
            )
            for line in report:
                log(line)
            (wd / "article.wechat.html").write_text(html, encoding="utf-8")
        else:
            log("2/4 正文无需转存图片")
        log("3/4 上传封面 …")
        thumb = client.upload_permanent_image(wd / "cover.jpg")
        log("4/4 写入草稿 …")
        media_id = client.add_draft(
            title=title,
            content_html=html,
            thumb_media_id=thumb,
            author=override_author or os.getenv("WECHAT_AUTHOR", ""),
            digest=override_digest or make_digest(body_md),
            need_open_comment=int(os.getenv("WECHAT_NEED_COMMENT", "0") or 0),
        )
        (wd / "result.txt").write_text(
            f"media_id={media_id}\ntitle={title}\ntheme={theme}\n"
            f"time={datetime.now().isoformat()}\n",
            encoding="utf-8",
        )
        with _LOCK:
            STATE["publish"] = {"media_id": media_id, "title": title}
        log(f"成功！media_id={media_id}")
        log("请打开 mp.weixin.qq.com → 草稿箱 预览后手动发布")
        return {"media_id": media_id}

    task = _run_task("上传草稿箱", job)
    return jsonify({"task": task["id"]})


# ---------------- 任务轮询 ----------------
@app.get("/api/task/<tid>")
def api_task(tid: str):
    """任务状态。?since=N 时 log 只回第 N 行之后的增量（长任务省流）。"""
    since = request.args.get("since", type=int)
    with _LOCK:
        task = TASKS.get(tid)
        if not task:
            return jsonify({"error": "任务不存在"}), 404
        view = _task_view(task)
        if since is not None and since >= 0:
            view["log"] = task["log"][since:]
            view["log_base"] = min(since, len(task["log"]))
        return jsonify(view)


@app.post("/api/task/<tid>/cancel")
def api_task_cancel(tid: str):
    """协作式取消：置标志，任务在下一个探针点退出（LLM 流式分块也是探针）。"""
    with _LOCK:
        task = TASKS.get(tid)
        if not task:
            return jsonify({"error": "任务不存在"}), 404
        if task["status"] != "running":
            return jsonify({"error": "任务已结束"}), 400
        task["cancel"] = True
    return jsonify({"ok": True})


@app.post("/api/open_url")
def api_open_url():
    """用系统默认浏览器打开链接（桌面窗口内 window.open 行为不可靠）。"""
    url = str((request.get_json(force=True) or {}).get("url") or "").strip()
    if not url.startswith(("https://mp.weixin.qq.com", "https://github.com/qq353167950/")):
        return jsonify({"error": "不允许的链接"}), 400
    webbrowser.open(url)
    return jsonify({"ok": True})


@app.post("/api/open_folder")
def api_open_folder():
    """在资源管理器中打开产出目录。body.run 指定历史目录，缺省为当前。"""
    name = str((request.get_json(silent=True) or {}).get("run") or "").strip()
    if name:
        if not re.fullmatch(r"[0-9]{8}-[0-9]{6}(?:-[0-9]{1,3})?", name):
            return jsonify({"error": "无效的记录名"}), 400
        target = ROOT / "runs" / name
    else:
        with _LOCK:
            wd = STATE["work_dir"]
        target = Path(wd) if wd else ROOT / "runs"
    if not target.exists():
        return jsonify({"error": "目录不存在"}), 404
    import subprocess

    if sys.platform == "win32":
        subprocess.Popen(["explorer", str(target)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])
    return jsonify({"ok": True})


@app.get("/api/article/export")
def api_article_export():
    """把当前文章作为 .md 附件下载（导出备份/迁移用）。"""
    md = _article_path()
    if not md:
        return jsonify({"error": "还没有文章"}), 404
    title, _ = extract_title_and_body(md.read_text(encoding="utf-8"))
    safe = re.sub(r'[\\/:*?"<>|]', "_", title or "文章")[:40]
    resp = send_from_directory(md.parent, md.name, as_attachment=True,
                               download_name=f"{safe}.md")
    return resp


@app.post("/api/quit")
def api_quit():
    """退出程序（浏览器回退模式专用：关标签页杀不掉进程，给个明确出口）。

    桌面窗口模式下由关窗退出，此接口同样有效。
    延迟半秒退出，让响应先送达前端。
    """
    t = _running_task()
    force = bool((request.get_json(silent=True) or {}).get("force"))
    if t and not force:
        return jsonify({"error": f"「{t['name']}」仍在进行中", "task": t["id"]}), 409
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return jsonify({"ok": True})


# ---------------- 设置 ----------------
SETTINGS_SCHEMA = [
    {
        "group": "写作大模型",
        "desc": "选题整理与文章写作（LLM_*）",
        "fields": [
            {"key": "LLM_API_KEY", "label": "API Key", "secret": True},
            {"key": "LLM_BASE_URL", "label": "接口地址（OpenAI 兼容 /v1）"},
            {"key": "LLM_MODEL", "label": "模型名"},
            {"key": "LLM_TEMPERATURE", "label": "温度 0~1"},
            {"key": "LLM_TIMEOUT_SEC", "label": "请求超时秒数"},
            {"key": "LLM_STREAM", "label": "流式输出（防中转超时，推荐开）", "type": "toggle"},
        ],
    },
    {
        "group": "联网搜索",
        "desc": "真实搜索热点素材（SEARCH_*）；开关关闭的源即使配了 Key 也不参与",
        "fields": [
            {
                "key": "SEARCH_PROVIDER",
                "label": "搜索服务",
                "type": "select",
                "options": [
                    ["auto", "自动（多Key全搜/单Key用该源）"],
                    ["multi", "全部一起搜（已配Key的源并发）"],
                    ["tavily", "Tavily"],
                    ["bocha", "博查（国内）"],
                    ["bing", "必应 Bing"],
                    ["serper", "Serper（谷歌）"],
                    ["duckduckgo", "DuckDuckGo（免费）"],
                ],
            },
            {"key": "SEARCH_DOMAIN", "label": "账号领域关键词"},
            {"key": "TAVILY_API_KEY", "label": "Tavily Key", "secret": True},
            {"key": "SEARCH_ENABLE_TAVILY", "label": "启用 Tavily", "type": "toggle"},
            {"key": "BOCHA_API_KEY", "label": "博查 Key", "secret": True},
            {"key": "SEARCH_ENABLE_BOCHA", "label": "启用 博查", "type": "toggle"},
            {"key": "BING_API_KEY", "label": "Bing Key", "secret": True},
            {"key": "SEARCH_ENABLE_BING", "label": "启用 Bing", "type": "toggle"},
            {"key": "SERPER_API_KEY", "label": "Serper Key", "secret": True},
            {"key": "SEARCH_ENABLE_SERPER", "label": "启用 Serper", "type": "toggle"},
            {"key": "SEARCH_ENABLE_DUCKDUCKGO", "label": "启用 DuckDuckGo（免费兜底）", "type": "toggle"},
        ],
    },
    {
        "group": "生图模型",
        "desc": "封面与文中配图（IMAGE_*）",
        "fields": [
            {
                "key": "IMAGE_PROVIDER",
                "label": "生图方式",
                "type": "select",
                "options": [
                    ["openai", "OpenAI 兼容（DALL·E 等）"],
                    ["dashscope", "阿里通义万相"],
                    ["template", "本地文字模板（免 Key）"],
                ],
            },
            {"key": "IMAGE_API_KEY", "label": "API Key", "secret": True},
            {"key": "IMAGE_BASE_URL", "label": "接口根地址"},
            {"key": "IMAGE_MODEL", "label": "生图模型名"},
            {"key": "IMAGE_SIZE", "label": "生成尺寸"},
            {
                "key": "IMAGE_STYLE",
                "label": "画面风格",
                "type": "select",
                "options": [
                    ["editorial", "杂志编辑风"],
                    ["tech", "科技感"],
                    ["warm", "温暖生活"],
                    ["business", "商务简约"],
                    ["nature", "自然氛围"],
                ],
            },
            {"key": "IMAGE_OVERLAY_TITLE", "label": "封面叠中文标题", "type": "toggle"},
            {"key": "IMAGE_FALLBACK_TEMPLATE", "label": "生图失败自动用文字模板", "type": "toggle"},
        ],
    },
    {
        "group": "微信公众号",
        "desc": "草稿箱上传（WECHAT_*），IP 需自行加白名单",
        "fields": [
            {"key": "WECHAT_APPID", "label": "AppID"},
            {"key": "WECHAT_APPSECRET", "label": "AppSecret", "secret": True},
            {"key": "WECHAT_AUTHOR", "label": "作者名（≤8字）"},
            {"key": "WECHAT_NEED_COMMENT", "label": "开放评论", "type": "toggle"},
        ],
    },
    {
        "group": "文章增强",
        "desc": "降 AI 味与自动配图",
        "fields": [
            {"key": "ARTICLE_FETCH_REFS", "label": "写作前抓取参考原文", "type": "toggle"},
            {"key": "ARTICLE_REF_MAX", "label": "最多抓几篇"},
            {"key": "ARTICLE_ILLUSTRATE", "label": "允许文中自动配图", "type": "toggle"},
            {"key": "ARTICLE_MAX_IMAGES", "label": "每篇最多配图数"},
        ],
    },
    {
        "group": "通用",
        "desc": "默认值与界面",
        "fields": [
            {
                "key": "DEFAULT_THEME",
                "label": "默认排版主题",
                "type": "select",
                "options": [[k, v["label"]] for k, v in THEMES.items()],
            },
            {"key": "GUI_PORT", "label": "界面端口（重启生效）"},
            {"key": "RUNS_KEEP", "label": "runs 产出保留次数（0=不清理）"},
        ],
    },
]


@app.get("/api/close_action")
def api_close_action_get():
    """桌面端点 X 行为偏好（存 ui_state.json，非 .env）。"""
    try:
        import gui_app

        return jsonify({"action": gui_app.load_close_action()})
    except Exception:
        return jsonify({"action": "ask"})


@app.post("/api/close_action")
def api_close_action_set():
    action = str((request.get_json(force=True) or {}).get("action") or "ask")
    if action not in {"ask", "exit", "tray"}:
        return jsonify({"error": "无效选项"}), 400
    try:
        import gui_app

        gui_app.save_close_action(action)
        return jsonify({"ok": True, "action": action})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/api/settings")
def api_settings_get():
    values = read_env_values(ROOT / ".env")
    # 搜索源开关默认开：.env 未写该键时，界面开关应显示为开启（与后端行为一致）
    for f in [f for g in SETTINGS_SCHEMA for f in g["fields"] if f.get("type") == "toggle"]:
        if f["key"].startswith("SEARCH_ENABLE_") and f["key"] not in values:
            values[f["key"]] = "1"
    return jsonify({"schema": SETTINGS_SCHEMA, "values": values})


@app.post("/api/test/llm")
def api_test_llm():
    """测试写作模型连通性：发一条最小请求，即时告知 Key/地址/模型名对不对。"""
    try:
        reply = llm_chat(
            [{"role": "user", "content": "回复「连接成功」四个字即可"}],
            max_tokens=20,
        )
        return jsonify({"ok": True, "message": f"连接成功，模型已响应：{reply[:40]}"})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)[:300]})


@app.post("/api/test/wechat")
def api_test_wechat():
    """测试公众号配置：尝试获取 access_token（能拿到即 AppID/Secret/IP 白名单全通）。"""
    appid = os.getenv("WECHAT_APPID", "").strip()
    secret = os.getenv("WECHAT_APPSECRET", "").strip()
    if not appid or not secret:
        return jsonify({"ok": False, "message": "请先填写 AppID 与 AppSecret"})
    try:
        WeChatClient(appid, secret).get_access_token(force=True)
        return jsonify({"ok": True, "message": "连接成功：凭证有效，IP 已在白名单"})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)[:300]})


@app.get("/api/my_ip")
def api_my_ip():
    """查本机公网出口 IP（配公众号白名单用）。"""
    try:
        from http_util import request_bytes

        ip = request_bytes("GET", "https://api.ipify.org", timeout=10).decode("ascii").strip()
        return jsonify({"ip": ip})
    except Exception as e:
        return jsonify({"error": f"查询失败：{e}"}), 502


@app.post("/api/settings")
def api_settings_save():
    body = request.get_json(force=True) or {}
    updates = {str(k): str(v) for k, v in (body.get("values") or {}).items()}
    if not updates:
        return jsonify({"error": "没有要保存的内容"}), 400
    update_env_file(ROOT / ".env", updates)
    # 立即生效，无需重启
    load_dotenv(ROOT / ".env", override=True)
    return jsonify({"ok": True, "env": _env_summary()})


# ---------------- 启动 ----------------
def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    port = int(os.getenv("GUI_PORT", "8093") or 8093)
    url = f"http://127.0.0.1:{port}"
    print("=" * 56)
    print("  公众号助手 · 图形界面")
    print(f"  {url}")
    print("  关闭本窗口即退出（Ctrl+C 停止）")
    print("=" * 56)
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
    except OSError as e:
        print(f"[错误] 端口 {port} 启动失败：{e}")
        print("可在 .env 修改 GUI_PORT 后重试")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

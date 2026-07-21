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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

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
    build_preview_html,
    extract_title_and_body,
    make_digest,
    markdown_to_wechat_html,
)
from topic_search import resolve_provider, search_hot_materials  # noqa: E402
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
    """任务被用户取消。

    继承 BaseException 而非 Exception：业务模块里的宽泛 except Exception
    不会把取消信号吞掉，能一路抛到 worker 顶层。取消是协作式的，
    在任务下一次调用 log() 时生效。
    """


def _new_task(name: str) -> dict:
    task = {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "status": "running",   # running / done / error / cancelled
        "cancel": False,       # 置 True 后任务在下一个日志点退出
        "log": [],
        "result": None,
        "error": "",
    }
    with _LOCK:
        _evict_finished_tasks()
        TASKS[task["id"]] = task
    return task


def _run_task(name: str, fn) -> dict:
    """后台线程执行 fn(task_log)；异常写入 task.error，取消置为 cancelled。"""
    task = _new_task(name)

    def log(msg: str) -> None:
        with _LOCK:
            if task["cancel"]:
                raise TaskCancelled("已取消")
            task["log"].append(str(msg))

    def worker() -> None:
        try:
            result = fn(log)
            with _LOCK:
                task["result"] = result
                task["status"] = "done"
        except TaskCancelled:
            with _LOCK:
                task["status"] = "cancelled"
                task["error"] = "已取消"
        except Exception as e:  # 面向 UI：所有异常转文字
            with _LOCK:
                if task["cancel"]:
                    task["status"] = "cancelled"
                    task["error"] = "已取消"
                else:
                    task["error"] = str(e)
                    task["status"] = "error"

    threading.Thread(target=worker, daemon=True).start()
    return task


def _prune_runs(keep: int | None = None) -> None:
    """按时间戳目录名清理 runs/，保留最近 keep 次产出（含当前）。

    RUNS_KEEP=0 表示不清理。删除失败静默跳过（文件可能被占用）。
    """
    if keep is None:
        keep = int(os.getenv("RUNS_KEEP", "10") or 10)
    if keep <= 0:
        return
    runs_dir = ROOT / "runs"
    if not runs_dir.exists():
        return
    dirs = sorted(
        (d for d in runs_dir.iterdir() if d.is_dir()),
        key=lambda d: d.name,
        reverse=True,
    )
    for old in dirs[keep:]:
        try:
            shutil.rmtree(old)
        except OSError:
            pass


def _ensure_run() -> Path:
    """确保存在本次产出目录（与命令行 pipeline 的 runs/<stamp> 一致）。"""
    with _LOCK:
        if STATE["work_dir"] is None:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            wd = ROOT / "runs" / stamp
            wd.mkdir(parents=True, exist_ok=True)
            STATE["work_dir"] = wd
            _prune_runs()
        return STATE["work_dir"]


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
@app.get("/")
def index():
    return send_from_directory(ROOT / "gui", "index.html")


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
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}", name):
        return jsonify({"error": "无效的记录名"}), 400
    d = ROOT / "runs" / name
    if not (d / "cover.jpg").exists():
        return jsonify({"error": "无封面"}), 404
    return send_from_directory(d, "cover.jpg")


@app.post("/api/runs/open")
def api_runs_open():
    """重新打开历史产出：恢复文章/封面/选题到当前会话，可继续编辑或重新发布。"""
    name = str((request.get_json(force=True) or {}).get("run") or "").strip()
    # 目录名只允许时间戳格式，防止路径穿越
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}", name):
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
    return api_state()


# ---------------- 选题 ----------------
@app.post("/api/topics/search")
def api_topics_search():
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
    wd = _ensure_run()
    with _LOCK:
        STATE["topic"] = topic
    # 落盘：历史记录重新打开时可恢复选题
    (wd / "topic.json").write_text(
        json.dumps(topic, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return jsonify({"topic": topic})


# ---------------- 文章 ----------------
@app.post("/api/article/write")
def api_article_write():
    body = request.get_json(force=True) or {}
    mode = str(body.get("mode") or "llm")
    extra = str(body.get("user_extra") or "").strip()
    with _LOCK:
        topic = STATE["topic"]
        if topic and extra:
            topic["user_extra"] = extra
    if not topic:
        return jsonify({"error": "请先选定选题"}), 400
    wd = _ensure_run()

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
    return jsonify({"ok": True, "ai_hits": detect_ai_phrases(md_text)})


@app.post("/api/article/illustrate")
def api_article_illustrate():
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
    with _LOCK:
        task = TASKS.get(tid)
        if not task:
            return jsonify({"error": "任务不存在"}), 404
        return jsonify(dict(task))


@app.post("/api/task/<tid>/cancel")
def api_task_cancel(tid: str):
    """协作式取消：置标志，任务在下一个日志点退出（无法中断阻塞中的 HTTP 请求）。"""
    with _LOCK:
        task = TASKS.get(tid)
        if not task:
            return jsonify({"error": "任务不存在"}), 404
        if task["status"] != "running":
            return jsonify({"error": "任务已结束"}), 400
        task["cancel"] = True
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
        ],
    },
    {
        "group": "联网搜索",
        "desc": "真实搜索热点素材（SEARCH_*）",
        "fields": [
            {
                "key": "SEARCH_PROVIDER",
                "label": "搜索服务",
                "type": "select",
                "options": ["auto", "tavily", "bocha", "bing", "serper", "duckduckgo"],
            },
            {"key": "SEARCH_DOMAIN", "label": "账号领域关键词"},
            {"key": "TAVILY_API_KEY", "label": "Tavily Key", "secret": True},
            {"key": "BOCHA_API_KEY", "label": "博查 Key", "secret": True},
            {"key": "BING_API_KEY", "label": "Bing Key", "secret": True},
            {"key": "SERPER_API_KEY", "label": "Serper Key", "secret": True},
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
                "options": ["openai", "dashscope", "template"],
            },
            {"key": "IMAGE_API_KEY", "label": "API Key", "secret": True},
            {"key": "IMAGE_BASE_URL", "label": "接口根地址"},
            {"key": "IMAGE_MODEL", "label": "生图模型名"},
            {"key": "IMAGE_SIZE", "label": "生成尺寸"},
            {
                "key": "IMAGE_STYLE",
                "label": "画面风格",
                "type": "select",
                "options": ["editorial", "tech", "warm", "business", "nature"],
            },
            {"key": "IMAGE_OVERLAY_TITLE", "label": "封面叠中文标题", "type": "toggle"},
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
                "options": list(THEMES.keys()),
            },
            {"key": "GUI_PORT", "label": "界面端口（重启生效）"},
            {"key": "RUNS_KEEP", "label": "runs 产出保留次数（0=不清理）"},
        ],
    },
]


@app.get("/api/settings")
def api_settings_get():
    values = read_env_values(ROOT / ".env")
    return jsonify({"schema": SETTINGS_SCHEMA, "values": values})


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

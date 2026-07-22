#!/usr/bin/env python3
"""
公众号一键全流程（唯一入口）

步骤：
  1) 真实联网搜索热门素材 → 大模型整理候选选题 → 你挑选
  2) 生成文章（大模型写作，可改）
  3) 生成封面（AI 生图）
  4) 上传微信草稿箱

运行：
  python scripts/pipeline.py
  或双击 START_HERE.bat
"""

from __future__ import annotations

import json
import os
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from content_images import count_external_images, replace_content_images  # noqa: E402
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
from generate_cover_ai import generate_ai_cover  # noqa: E402
from http_util import close_all_sessions  # noqa: E402
from llm_client import llm_chat, llm_config  # noqa: E402
from markdown_to_wechat_html import (  # noqa: E402
    THEMES,
    build_preview_html,
    extract_title_and_body,
    make_digest,
    markdown_to_wechat_html,
)
from topic_search import materials_to_prompt_block, search_hot_materials  # noqa: E402
from wechat_client import WeChatAPIError, WeChatClient  # noqa: E402


# ---------------- UI helpers ----------------
def banner() -> None:
    print("=" * 56)
    print("  公众号全流程助手（唯一入口）")
    print("  真实搜索选题 → 写文章 → 生封面 → 进草稿箱")
    print("=" * 56)
    print()


def pause(msg: str = "按回车继续...") -> None:
    try:
        input(msg)
    except EOFError:
        pass


def ask(prompt: str, default: str = "") -> str:
    tip = f"{prompt}"
    if default:
        tip += f" [{default}]"
    tip += ": "
    try:
        s = input(tip).strip()
    except EOFError:
        s = ""
    return s if s else default


def ask_choice(prompt: str, options: list[str], default: str = "1") -> str:
    print(prompt)
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    while True:
        s = ask("请输入序号", default)
        if s.isdigit() and 1 <= int(s) <= len(options):
            return s
        print(f"请输入 1~{len(options)} 之间的数字")


def yes_no(prompt: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    s = ask(f"{prompt} ({d})", "y" if default else "n").lower()
    if not s:
        return default
    return s in {"y", "yes", "1", "是", "好", "ok"}


# 写作/选题的具体实现在 article_writer.py（与 GUI 共用）


# ---------------- steps ----------------
def step_check_env() -> dict:
    load_dotenv(ROOT / ".env")
    load_dotenv()
    cfg = llm_config()
    info = {
        "wechat": bool(os.getenv("WECHAT_APPID") and os.getenv("WECHAT_APPSECRET")),
        "llm": bool(cfg["api_key"]),
        "search_provider": os.getenv("SEARCH_PROVIDER", "auto"),
        "image_provider": (
            os.getenv("IMAGE_PROVIDER", "") or os.getenv("COVER_PROVIDER", "openai")
        ).strip().lower(),
        "image_key": bool(
            os.getenv("IMAGE_API_KEY", "").strip() or os.getenv("DASHSCOPE_API_KEY", "").strip()
        ),
        "domain": os.getenv("SEARCH_DOMAIN", "").strip(),
    }
    print("[环境检查] 三套配置已分开：写作LLM / 搜索SEARCH / 生图IMAGE")
    print(f"  [微信] AppID/Secret     : {'已配置' if info['wechat'] else '未配置（上传草稿会跳过）'}")
    print(f"  [写作] LLM_API_KEY      : {'已配置' if info['llm'] else '未配置（选题整理/写文章会失败）'}")
    print(f"  [写作] LLM_BASE/MODEL   : {cfg['base']} / {cfg['model']}")
    print(f"  [搜索] SEARCH_PROVIDER  : {info['search_provider']}")
    print(f"  [搜索] SEARCH_DOMAIN    : {info['domain'] or '（未设则用默认领域词）'}")
    print(f"  [生图] IMAGE_PROVIDER   : {info['image_provider']}")
    print(f"  [生图] IMAGE_API_KEY    : {'已配置' if info['image_key'] else '未配置（AI封面会失败，可改 template）'}")
    print()
    if not (ROOT / ".env").exists():
        print("提示：未找到 .env。请把 .env.example 复制为 .env 并填写。")
        print(f"路径：{ROOT / '.env.example'}")
        print()
    return info


def step_select_topic(work_dir: Path) -> dict:
    print("-" * 56)
    print("步骤 1/4  真实搜索选题（无内置假热门）")
    print("-" * 56)

    while True:
        mode = ask_choice(
            "选题方式？",
            [
                "联网搜索热门素材 → 大模型整理候选 → 我来选（推荐）",
                "我自己输入主题（不搜索）",
            ],
            "1",
        )

        if mode == "2":
            title = ask("请输入你的主题/标题方向")
            angle = ask("补充角度（可空）", "")
            atype = ask("文章类型（干货文/观点文/案例文）", "干货文")
            topic = {
                "title": title or "未命名选题",
                "type": atype,
                "angle": angle,
                "why": "用户自定义",
                "audience": "",
                "refs": "",
            }
            if yes_no("确认用这个选题？", True):
                return topic
            continue

        domain = ask(
            "你的账号领域/关键词（影响搜索方向）",
            os.getenv("SEARCH_DOMAIN", "").strip() or "公众号 个人成长 职场 副业",
        )
        extra = ask("额外搜索词（可空，例如：AI副业 裁员）", "")
        raw_n = ask("希望给出几个候选选题", os.getenv("TOPIC_CANDIDATE_N", "5") or "5")
        want_n = int(raw_n) if raw_n.strip().isdigit() else 5   # 非数字回退默认，防崩溃
        want_n = max(3, min(want_n, 10))

        print()
        print("正在真实联网搜索，请稍候…")
        try:
            meta, materials = search_hot_materials(domain=domain, extra_query=extra)
        except Exception as e:
            print(f"[搜索失败] {e}")
            print("可在 .env 配置 SEARCH_PROVIDER 与对应 API Key，例如：")
            print("  SEARCH_PROVIDER=tavily + TAVILY_API_KEY=...")
            print("  SEARCH_PROVIDER=bocha  + BOCHA_API_KEY=...")
            print("  SEARCH_PROVIDER=duckduckgo  （免费，无需 Key）")
            if yes_no("改成自己输入主题？", True):
                title = ask("请输入你的主题/标题方向")
                if title.strip():
                    return {
                        "title": title, "type": ask("文章类型", "干货文"),
                        "angle": ask("角度（可空）", ""), "why": "用户自定义",
                        "audience": "", "refs": "",
                    }
            continue

        (work_dir / "search_raw.json").write_text(
            json.dumps({"meta": meta, "materials": materials}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[搜索完成] {meta}")
        print(f"[原始素材] 已保存 {work_dir / 'search_raw.json'} （共 {len(materials)} 条）")
        print()

        if not materials:
            print("没有搜到素材。")
            if yes_no("重新搜索？", True):
                continue
            continue

        print("正在用大模型根据「真实搜索结果」整理候选选题…")
        try:
            topics = llm_rank_topics(materials, domain=domain, want_n=want_n)
        except Exception as e:
            print(f"[大模型整理选题失败] {e}")
            print("请检查 .env 的 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL（写作专用，与生图无关）")
            if yes_no("改为自己输入主题？", True):
                title = ask("请输入你的主题/标题方向")
                if title.strip():
                    return {
                        "title": title, "type": ask("文章类型", "干货文"),
                        "angle": ask("角度（可空）", ""), "why": "用户自定义",
                        "audience": "", "refs": "",
                    }
            continue

        if not topics:
            print("大模型未返回有效选题。")
            continue

        (work_dir / "topics.json").write_text(
            json.dumps(topics, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print()
        print("根据实时搜索整理的候选选题：")
        print()
        labels = []
        for i, t in enumerate(topics, 1):
            score = t.get("score", "")
            score_s = f" 潜力{score}" if score != "" else ""
            print(f"  {i}. {t['title']}{score_s}")
            print(f"     类型：{t.get('type','')} | 人群：{t.get('audience','')}")
            print(f"     角度：{t.get('angle','')}")
            print(f"     理由：{t.get('why','')}")
            if t.get("refs"):
                print(f"     参考：{t.get('refs')}")
            print()
            labels.append(t["title"][:40])

        labels.append("重新搜索")
        labels.append("我自己输入主题")
        choice = ask_choice("请选择一个选题", labels, "1")
        ci = int(choice)
        if ci == len(labels) - 1:
            continue  # 重新搜索
        if ci == len(labels):
            # 自己输入
            title = ask("请输入你的主题/标题方向")
            topic = {
                "title": title or "未命名选题",
                "type": ask("文章类型", "干货文"),
                "angle": ask("角度（可空）", ""),
                "why": "用户自定义",
                "audience": "",
                "refs": "",
            }
        else:
            topic = dict(topics[ci - 1])

        if yes_no("是否微调标题？", False):
            topic["title"] = ask("新标题", topic["title"])
        extra_req = ask("写作补充要求（语气/行业/禁忌，可空）", "")
        if extra_req:
            topic["user_extra"] = extra_req

        print()
        print("已选定：")
        print(f"  标题：{topic['title']}")
        print(f"  类型：{topic.get('type')}")
        print(f"  角度：{topic.get('angle')}")
        print()
        if yes_no("确认用这个选题写文章？", True):
            return topic


def step_write_article(topic: dict, work_dir: Path) -> Path:
    print("-" * 56)
    print("步骤 2/4  生成文章")
    print("-" * 56)

    while True:
        mode = ask_choice(
            "怎么生成文章？",
            [
                "大模型自动写爆文（推荐）",
                "生成本地提纲稿（不调用写作模型）",
                "我自己粘贴已有 Markdown 文章",
            ],
            "1",
        )
        md_text = ""
        if mode == "1":
            # 抓取参考文章原文，让模型学真人语感（可用 .env ARTICLE_FETCH_REFS=0 关掉）
            refs_block = ""
            if os.getenv("ARTICLE_FETCH_REFS", "1") != "0":
                materials = _load_search_materials(work_dir)
                if materials:
                    print("正在抓取参考文章原文（学习真实表达，不抄袭）…")
                    refs = fetch_reference_articles(materials, topic)
                    if refs:
                        refs_block = refs_to_prompt_block(refs)
                        (work_dir / "reference_articles.json").write_text(
                            json.dumps(refs, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        print(f"[参考] 抓到 {len(refs)} 篇原文，已保存 reference_articles.json")
                    else:
                        print("[参考] 未抓到可用原文，直接写作")
            try:
                print("正在写稿，请稍候…")
                md_text = llm_chat(
                    build_article_prompt(topic, topic.get("user_extra", ""), refs_block)
                )
            except Exception as e:
                print(f"[写作失败] {e}")
                if yes_no("改用本地提纲稿？", True):
                    md_text = local_fallback_article(topic)
                else:
                    continue
        elif mode == "2":
            md_text = local_fallback_article(topic)
        else:
            print("请粘贴完整 Markdown（第一行建议 # 标题）。")
            print("粘贴结束后，在新的一行输入只有 END 三个字母并回车：")
            lines = []
            while True:
                try:
                    line = input()
                except EOFError:
                    break
                if line.strip() == "END":
                    break
                lines.append(line)
            md_text = "\n".join(lines).strip()
            if not md_text:
                print("没有读到内容，请重试")
                continue

        # LLM 偶尔把全文包在 ```markdown 围栏里 → 整篇被渲染成代码框
        from markdown_to_wechat_html import _strip_outer_fence
        md_text = _strip_outer_fence(md_text)
        if not md_text.lstrip().startswith("#"):
            md_text = f"# {topic['title']}\n\n{md_text}"

        # 兜底：删掉模型偶尔塞进正文的 [[1]](url) 引用角标（公众号里显示成链接文本）
        md_text = scrub_citations(md_text)

        # AI 腔检测：命中过多时提示重写
        hits = detect_ai_phrases(md_text)
        if hits:
            print(f"[AI腔检测] 命中 {len(hits)} 个套话：{'、'.join(hits[:8])}")

        out = work_dir / "article.md"
        out.write_text(md_text, encoding="utf-8")
        title, _ = extract_title_and_body(md_text)
        print()
        print(f"[已保存] {out}")
        print(f"[标题] {title}")
        print()
        print("----- 文章预览（前 800 字）-----")
        print(md_text[:800])
        print("----- 预览结束 -----")
        print()
        print("完整文件可用记事本打开修改：")
        print(f"  {out}")
        print()
        act = ask_choice(
            "下一步？",
            [
                "满意，进入配图/封面",
                "大模型重写一版",
                "我改完文件后继续（改完回车）",
                "重新选题",
            ],
            "1",
        )
        if act == "1":
            _maybe_illustrate(out, work_dir)
            return out
        if act == "2":
            hint = "；请换个开头和案例重写，更口语。"
            extra = topic.get("user_extra") or ""
            if hint not in extra:
                topic["user_extra"] = extra + hint
            continue
        if act == "3":
            pause("请先编辑 article.md，保存后按回车继续…")
            if out.exists():
                _maybe_illustrate(out, work_dir)
                return out
            print(f"[提示] 未找到 {out}，将重新进入生成流程")
        if act == "4":
            # 整体替换：残留旧字段（尤其 user_extra 里追加的重写指令）
            # 会污染新选题的写作提示词
            topic.clear()
            topic.update(step_select_topic(work_dir))
            continue


def _load_search_materials(work_dir: Path) -> list[dict]:
    """读取本轮搜索原始素材（自定义主题模式下可能不存在）。"""
    f = work_dir / "search_raw.json"
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data.get("materials") or []
    except Exception:
        return []


def _maybe_illustrate(md_path: Path, work_dir: Path) -> None:
    """询问并执行文中教程配图（需要 IMAGE_* 生图配置）。"""
    if os.getenv("ARTICLE_ILLUSTRATE", "1") == "0":
        return
    provider = (
        os.getenv("IMAGE_PROVIDER", "") or os.getenv("COVER_PROVIDER", "")
    ).strip().lower()
    if provider in {"", "template", "local", "pil"}:
        return  # 模板封面模式画不了示意图
    if not yes_no("要智能配图吗？（技术文出示意图，情感/生活文出暖色氛围图）", False):
        return
    md_text = md_path.read_text(encoding="utf-8")
    new_md, report = illustrate_article(md_text, work_dir)
    for line in report:
        print(f"  {line}")
    if new_md != md_text:
        md_path.write_text(new_md, encoding="utf-8")
        print(f"[配图] 已写回 {md_path}")


def step_cover(md_path: Path, work_dir: Path) -> Path:
    print("-" * 56)
    print("步骤 3/4  生成封面（生图模型）")
    print("-" * 56)
    cover_path = work_dir / "cover.jpg"
    title, _ = extract_title_and_body(md_path.read_text(encoding="utf-8"))

    def _show_cover() -> None:
        """尽力用系统默认看图工具打开封面，方便直接确认效果。"""
        try:
            os.startfile(cover_path)  # noqa: S606  Windows 专用
        except Exception:
            print(f"（请手动打开查看：{cover_path}）")

    while True:
        mode = ask_choice(
            "封面怎么处理？",
            [
                "AI 生图模型生成封面（推荐）",
                "使用已有图片路径",
                "跳过封面（无封面不能上传草稿）",
            ],
            "1",
        )
        if mode == "1":
            provider = (
                os.getenv("IMAGE_PROVIDER", "") or os.getenv("COVER_PROVIDER", "openai")
            ).strip() or "openai"
            style = (
                os.getenv("IMAGE_STYLE", "") or os.getenv("COVER_STYLE", "editorial")
            ).strip() or "editorial"
            print(f"将使用【生图】IMAGE_PROVIDER={provider}，风格={style}")
            print("（不会使用 LLM_* 写作密钥）")
            print("正在生图，可能需要几十秒…")
            try:
                overlay_flag = os.getenv(
                    "IMAGE_OVERLAY_TITLE", os.getenv("COVER_OVERLAY_TITLE", "1")
                )
                generate_ai_cover(
                    title,
                    cover_path,
                    abstract="",
                    provider=provider,
                    style=style,
                    overlay=overlay_flag != "0",
                )
                print(f"[已生成封面] {cover_path}")
                _show_cover()
                if yes_no("封面可以用吗？", True):
                    return cover_path
                print("将重新生成…")
                continue
            except Exception as e:
                print(f"[生图失败] {e}")
                if yes_no("改用本地文字模板封面？", True):
                    from generate_cover import generate_cover as template_cover

                    template_cover(title, cover_path, theme="default")
                    print(f"[模板封面] {cover_path}")
                    _show_cover()
                    if yes_no("用这个模板封面吗？", True):
                        return cover_path
                continue
        if mode == "2":
            p = ask("输入封面图片完整路径（jpg/png）")
            src = Path(p)
            if not src.exists():
                print("文件不存在")
                continue
            cover_path.write_bytes(src.read_bytes())
            print(f"[已复制封面] {cover_path}")
            return cover_path
        print("已跳过封面。没有封面将无法上传到微信草稿箱。")
        return cover_path


def _pick_theme(default_theme: str) -> str:
    """交互选择排版主题，展示每套主题的中文说明。"""
    names = list(THEMES.keys())
    if default_theme not in names:
        default_theme = names[0]
    labels = [f"{n} —— {THEMES[n]['label']}" for n in names]
    idx = ask_choice("选择排版主题？", labels, str(names.index(default_theme) + 1))
    return names[int(idx) - 1]


def _render_and_preview(
    md_path: Path, work_dir: Path, theme: str
) -> tuple[str, str, str]:
    """排版 + 写预览文件 + 打开浏览器。返回 (title, body_md, content_html)。"""
    md_text = md_path.read_text(encoding="utf-8")
    title, body = extract_title_and_body(md_text)
    html = markdown_to_wechat_html(body, theme=theme)

    html_path = work_dir / "article.wechat.html"
    html_path.write_text(html, encoding="utf-8")
    preview_path = work_dir / "preview.html"
    preview_path.write_text(
        build_preview_html(
            title,
            html,
            author=os.getenv("WECHAT_AUTHOR", ""),
            theme_label=f"主题：{theme}（{THEMES[theme]['label']}）",
        ),
        encoding="utf-8",
    )
    print(f"[排版HTML] {html_path}")
    print(f"[手机预览] {preview_path}")
    try:
        webbrowser.open(preview_path.as_uri())
        print("已在浏览器打开手机模拟预览，请查看排版效果。")
    except Exception:
        print("（浏览器打开失败，请手动双击 preview.html 查看）")
    return title, body, html


def step_publish(md_path: Path, cover_path: Path, work_dir: Path) -> None:
    print("-" * 56)
    print("步骤 4/4  排版预览 + 上传微信草稿箱")
    print("-" * 56)

    theme = os.getenv("DEFAULT_THEME", "default")
    if theme not in THEMES:
        theme = "default"

    # 先预览，满意再上传；可反复换主题
    while True:
        title, body, html = _render_and_preview(md_path, work_dir, theme)
        print(f"[标题] {title}")
        act = ask_choice(
            "排版效果满意吗？",
            ["满意，继续上传", "换一个排版主题再看", "不上传，只保留本地文件"],
            "1",
        )
        if act == "1":
            break
        if act == "2":
            theme = _pick_theme(theme)
            continue
        print("已跳过上传。本地文件已保留。")
        return

    if not cover_path.exists():
        print("没有封面图，无法调用微信草稿接口。")
        print(f"请把图片放到：{cover_path}")
        return

    if not (os.getenv("WECHAT_APPID") and os.getenv("WECHAT_APPSECRET")):
        print("未配置 WECHAT_APPID / WECHAT_APPSECRET，跳过上传。")
        return

    if not yes_no("确认上传到公众号【草稿箱】？（不会自动群发）", True):
        print("已取消上传。本地文件已保留。")
        return

    try:
        client = WeChatClient(os.environ["WECHAT_APPID"].strip(), os.environ["WECHAT_APPSECRET"].strip())
        print("1/4 获取 access_token …")
        client.get_access_token()

        # 正文里的外链/本地图片必须转微信图床，否则读者看到的是裂图
        n_ext = count_external_images(html)
        if n_ext:
            print(f"2/4 正文有 {n_ext} 张站外图片，转存微信图床 …")
            html, img_report = replace_content_images(
                html, client, base_dir=md_path.parent, cache_dir=work_dir / "content_images"
            )
            for line in img_report:
                print(f"   {line}")
            (work_dir / "article.wechat.html").write_text(html, encoding="utf-8")
        else:
            print("2/4 正文无站外图片，跳过转存")

        print("3/4 上传封面 …")
        thumb = client.upload_permanent_image(cover_path)
        print(f"   thumb_media_id={thumb}")
        print("4/4 写入草稿 …")
        media_id = client.add_draft(
            title=title,
            content_html=html,
            thumb_media_id=thumb,
            author=os.getenv("WECHAT_AUTHOR", ""),
            digest=make_digest(body),
            need_open_comment=int(os.getenv("WECHAT_NEED_COMMENT", "0") or 0),
        )
        print()
        print("=" * 56)
        print("  成功！已进入公众号草稿箱")
        print(f"  media_id: {media_id}")
        print("  请打开 mp.weixin.qq.com → 草稿箱 预览后手动发布")
        print("=" * 56)
        (work_dir / "result.txt").write_text(
            f"media_id={media_id}\ntitle={title}\ntheme={theme}\ntime={datetime.now().isoformat()}\n",
            encoding="utf-8",
        )
    except WeChatAPIError as e:
        print(f"[微信API错误] {e}")
        print("常见原因：IP未加白名单 / AppSecret错误 / 封面不合格")
    except Exception as e:
        print(f"[上传失败] {e}")


def _fix_stdio() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def main() -> int:
    _fix_stdio()
    # 启动时先清掉可能残留的会话
    close_all_sessions()
    os.chdir(ROOT)
    banner()
    step_check_env()
    pause("准备好了就按回车开始全流程…")

    while True:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        work_dir = ROOT / "runs" / stamp
        work_dir.mkdir(parents=True, exist_ok=True)
        print(f"本次产出目录：{work_dir}")
        print()

        try:
            topic = step_select_topic(work_dir)
            md_path = step_write_article(topic, work_dir)
            cover_path = step_cover(md_path, work_dir)
            step_publish(md_path, cover_path, work_dir)
        finally:
            # 每轮结束 / 异常 / 中途退出前都关闭连接
            close_all_sessions()
            print("[网络] 已关闭所有 HTTP 连接")

        print()
        print("流程结束。")
        print(f"文件都在：{work_dir}")
        print("  search_raw.json / topics.json / article.md / cover.jpg")
        print("  article.wechat.html / preview.html / result.txt")
        print()
        if not yes_no("要再跑一篇吗？", False):
            break

    pause("按回车退出…")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已中断，正在关闭连接…")
        try:
            close_all_sessions()
        except Exception:
            pass
        raise SystemExit(130)
    except SystemExit:
        close_all_sessions()
        raise
    except Exception:
        close_all_sessions()
        raise

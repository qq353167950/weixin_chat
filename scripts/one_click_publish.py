#!/usr/bin/env python3
"""命令行一键发布：Markdown → 排版 HTML → 上传封面/正文图 → 草稿箱。

用法：
  python one_click_publish.py --md samples/demo.md --cover samples/cover.jpg
  python one_click_publish.py --md samples/demo.md --auto-cover
  python one_click_publish.py --md samples/demo.md --dry-run --out-html preview.html
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from content_images import count_external_images, replace_content_images
from generate_cover import THEMES as COVER_THEMES
from generate_cover import generate_cover
from markdown_to_wechat_html import (
    THEMES,
    build_preview_html,
    extract_title_and_body,
    make_digest,
    markdown_to_wechat_html,
)
from wechat_client import WeChatAPIError, client_from_env


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="发布 Markdown 到公众号草稿箱")
    parser.add_argument("--md", required=True, help="Markdown 文件路径")
    parser.add_argument("--cover", default="", help="封面图路径 jpg/png")
    parser.add_argument(
        "--auto-cover",
        action="store_true",
        help="封面缺失时用标题自动生成模板封面",
    )
    parser.add_argument("--title", default="", help="覆盖标题")
    parser.add_argument("--author", default=os.getenv("WECHAT_AUTHOR", ""), help="作者")
    parser.add_argument("--digest", default="", help="摘要，留空自动生成")
    parser.add_argument(
        "--theme",
        default="default",
        choices=sorted(set(THEMES) | set(COVER_THEMES)),
        help="排版/封面主题",
    )
    parser.add_argument(
        "--comment",
        type=int,
        default=int(os.getenv("WECHAT_NEED_COMMENT", "0")),
        help="开放评论 0/1",
    )
    parser.add_argument("--source-url", default="", help="阅读原文链接")
    parser.add_argument("--dry-run", action="store_true", help="只排版，不调微信接口")
    parser.add_argument("--out-html", default="", help="保存正文 HTML 到该路径")
    parser.add_argument(
        "--open-preview",
        action="store_true",
        help="dry-run 后在浏览器打开手机模拟预览",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    load_dotenv()

    md_path = Path(args.md)
    if not md_path.exists():
        print(f"[错误] 找不到 Markdown：{md_path}")
        return 1

    md_text = md_path.read_text(encoding="utf-8")
    auto_title, body_md = extract_title_and_body(md_text)
    title = (args.title or auto_title or "").strip()
    if not title:
        print("[错误] 无法识别标题，请用 --title 指定")
        return 1

    # 封面：解析路径，缺失时按需生成模板封面
    cover_path = Path(args.cover) if args.cover else root / "samples" / "cover.jpg"
    auto_cover = args.auto_cover or os.getenv("WECHAT_AUTO_COVER", "0") == "1"
    cover_theme = args.theme if args.theme in COVER_THEMES else "default"
    if not cover_path.exists():
        if auto_cover:
            print(f"[自动封面] 生成：{cover_path}")
            generate_cover(title, cover_path, theme=cover_theme)
        elif not args.dry_run:
            print(f"[错误] 找不到封面：{cover_path}")
            print("放一张 jpg/png 到该路径，或加 --auto-cover 自动生成")
            print("或运行：python scripts/generate_cover_ai.py --md 你的.md --out samples/cover.jpg")
            return 1

    # 排版
    html_theme = args.theme if args.theme in THEMES else "default"
    content_html = markdown_to_wechat_html(body_md, theme=html_theme)
    digest = args.digest.strip() or make_digest(body_md)

    if args.out_html:
        Path(args.out_html).write_text(content_html, encoding="utf-8")
        print(f"[OK] 正文 HTML 已保存：{args.out_html}")

    if args.dry_run:
        preview_path = md_path.with_suffix(".preview.html")
        preview_path.write_text(
            build_preview_html(
                title,
                content_html,
                author=args.author,
                theme_label=f"主题：{html_theme}（{THEMES[html_theme]['label']}）",
            ),
            encoding="utf-8",
        )
        info = {
            "title": title[:64],
            "author": args.author,
            "digest": digest[:120],
            "theme": html_theme,
            "cover": str(cover_path) if cover_path.exists() else "",
            "html_length": len(content_html),
            "external_images": count_external_images(content_html),
            "preview": str(preview_path),
        }
        print(json.dumps(info, ensure_ascii=False, indent=2))
        print("[OK] dry-run 完成（未调用微信接口）")
        if args.open_preview:
            webbrowser.open(preview_path.as_uri())
        return 0

    try:
        client = client_from_env()
        print("1/4 获取 access_token …")
        client.get_access_token()

        n_ext = count_external_images(content_html)
        if n_ext:
            print(f"2/4 正文 {n_ext} 张站外图片转存微信图床 …")
            content_html, report = replace_content_images(
                content_html,
                client,
                base_dir=md_path.parent,
                cache_dir=md_path.parent / "content_images",
            )
            for line in report:
                print(f"   {line}")
        else:
            print("2/4 正文无站外图片，跳过转存")

        print("3/4 上传封面 …")
        thumb_media_id = client.upload_permanent_image(cover_path)
        print(f"   thumb_media_id = {thumb_media_id}")
        print("4/4 写入草稿 …")
        media_id = client.add_draft(
            title=title,
            content_html=content_html,
            thumb_media_id=thumb_media_id,
            author=args.author,
            digest=digest,
            content_source_url=args.source_url,
            need_open_comment=args.comment,
        )
        print("\n[成功] 草稿已创建")
        print(f"media_id: {media_id}")
        print("打开 mp.weixin.qq.com → 草稿箱，预览后手动发布。")
        return 0
    except WeChatAPIError as e:
        print(f"\n[错误] 微信API：{e}")
        return 2
    except Exception as e:
        print(f"\n[错误] {e}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""Assemble gui/index.html from CSS + body + frozen app logic.

Sources:
  _redesign.css      visual system
  _redesign_body.html structure (no <html>/<head>)
  _app_logic.js      application JS (starts with <script>...)
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CSS = (ROOT / "_redesign.css").read_text(encoding="utf-8")
BODY = (ROOT / "_redesign_body.html").read_text(encoding="utf-8")
LOGIC = ROOT / "_app_logic.js"
if not LOGIC.exists():
    raise SystemExit("missing gui/_app_logic.js — restore app script source first")
script = LOGIC.read_text(encoding="utf-8")
if not script.lstrip().startswith("<script"):
    raise SystemExit("_app_logic.js must start with <script>")

HEAD = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>公众号助手</title>
<link rel="icon" href="/assets/app.png" type="image/png">
<!-- 离线：Lucide 与字体均不走 CDN；中文用系统字体栈（微软雅黑/苹方等） -->
<script src="/vendor/lucide.min.js"></script>
<style>
"""

out = HEAD + CSS + "\n</style>\n</head>\n" + BODY + "\n" + script
out_path = ROOT / "index.html"
out_path.write_text(out, encoding="utf-8")
print("WROTE", out_path, "bytes", len(out.encode("utf-8")))
print("lines", out.count("\n") + 1)

# 公众号助手 · 一键写作 → 排版 → 草稿箱

Windows 桌面客户端：真实联网搜索热点选题 → 大模型写作（反 AI 腔）→ AI 封面 → 四套主题排版 → 手机预览 → 一键进公众号草稿箱。

> 默认只进**草稿箱**，不自动群发/发布，避免误发。

---

## ✨ 功能亮点

- **桌面客户端**：独立原生窗口（Edge WebView2），苹果风格界面，不依赖浏览器
- **真实联网选题**：Tavily / 博查 / Bing / Serper / DuckDuckGo 搜索热点，大模型整理候选选题由你拍板
- **反 AI 腔写作**：抓取参考文章原文学语感，AI 腔词汇自动检测提示，可编辑可重写
- **智能配图**：技术文出示意图、情感文出氛围图，自动插入文中
- **AI 封面**：OpenAI / DashScope 生图，可换可重生成，也可上传自己的图
- **四套排版主题**：微信绿·干货 / 暖橙红·爆文 / 静谧蓝·杂志 / 黑白灰·极简
- **排版细节**：盘古之白、两端对齐、Mac 风代码块、金句卡片、表格斑马纹、外链自动转文末脚注
- **稳妥上传**：正文站外图片自动转微信图床（不裂图），标题/摘要/作者上传前可改
- **历史记录**：每次产出独立保存，可随时重新打开继续编辑或发布

## 🚀 快速开始

### 方式一：下载 exe（推荐，免装 Python）

1. 到 [Releases](../../releases) 下载 `公众号助手.exe`
2. 放到任意文件夹，双击运行
3. 首次运行自动生成 `.env`，在「设置」页填好四组 Key
4. 产出保存在同目录 `runs/` 下

> 需要 Windows 10/11 自带的 Edge WebView2（绝大多数系统已内置；缺失时自动回退浏览器模式）。exe 未做代码签名，SmartScreen 提示时选「仍要运行」。

### 方式二：源码运行

双击 `GUI.bat`（首次自动建 venv 装依赖），同样打开桌面窗口。

命令行全流程可用 `START_HERE.bat`，或：

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env    # 编辑填 Key
python scripts/gui_app.py       # 桌面窗口
python scripts/pipeline.py      # 或终端交互式全流程
```

## ⚙️ 配置（.env 四组独立）

| 组 | 前缀 | 用途 | 必填 |
|----|------|------|------|
| 写作大模型 | `LLM_*` | 选题整理与文章写作（OpenAI 兼容接口） | ✅ |
| 联网搜索 | `SEARCH_*` + 各家 Key | 热点素材搜索 | 建议（无 Key 时走 DuckDuckGo） |
| 生图模型 | `IMAGE_*` | 封面与文中配图 | 可选（有本地文字模板兜底） |
| 微信公众号 | `WECHAT_*` | 草稿箱上传 | ✅ |

所有配置都可以在客户端「设置」页填写，保存即生效。

### 微信侧准备

1. 已认证公众号（订阅号/服务号均可），后台拿到 **AppID + AppSecret**
2. **IP 白名单**：运行机器的出口 IP 必须加白（双击 `show_my_ip.bat` 查询，或 `curl https://api.ipify.org`）

## 📖 使用流程

```text
① 选题   联网搜索热点 → 候选选题卡片 → 点选（或直接输入自己的主题）
② 文章   大模型写作 → AI 腔检测 → Markdown 编辑器修改（Ctrl+S 保存）→ 可选智能配图
③ 封面   AI 生成 / 文字模板 / 上传自己的图（900×383）
④ 发布   四主题切换 + 手机模拟预览 → 改标题/摘要/作者 → 确认进草稿箱
⑤ 后台   打开 mp.weixin.qq.com → 草稿箱 → 预览满意后手动群发
```

## 🎨 排版主题

| 主题 | 风格 | 适合 |
|------|------|------|
| `default` | 微信绿·清爽干货 | 干货文、教程 |
| `hot` | 暖橙红·情绪爆文 | 情感文、观点文 |
| `elegant` | 静谧蓝·杂志深度 | 深度长文、行业分析 |
| `minimal` | 黑白灰·极简高级 | 极简审美、随笔 |

支持：小标题序号装饰、金句卡片、有序/无序列表、Mac 风代码块、斑马纹表格、图片图注、删除线、分隔符。**外部链接自动转文末「参考链接」脚注**（微信会过滤外链 `<a>`），微信域名链接保留可点击。

## 📁 目录结构

```text
weixin_chat/
├── GUI.bat                         # 桌面客户端入口（源码运行）
├── START_HERE.bat                  # 命令行全流程入口
├── build_exe.bat                   # 本地打包单文件 exe
├── build.spec                      # PyInstaller 配置
├── .env.example                    # 配置模板（四组独立）
├── show_my_ip.bat                  # 查出口 IP（加白名单用）
├── gui/index.html                  # 苹果风格前端单页
├── samples/demo.md                 # 示例文章
├── runs/<时间戳>/                  # 每次流程的产出（不入库）
└── scripts/
    ├── gui_app.py                  # 桌面客户端入口（pywebview 原生窗口）
    ├── gui_server.py               # 本地服务（Flask JSON API）
    ├── app_paths.py                # 源码/exe 两种形态的路径出口
    ├── pipeline.py                 # 命令行全流程
    ├── article_writer.py           # 选题整理/反AI腔写作/参考抓取/配图
    ├── one_click_publish.py        # 命令行发布已有 Markdown
    ├── markdown_to_wechat_html.py  # 排版引擎（四套主题）
    ├── content_images.py           # 正文图片转微信图床（含压缩）
    ├── env_store.py                # 设置页的 .env 读写
    ├── wechat_client.py            # 微信官方 API 客户端
    ├── topic_search.py             # 联网搜索热点
    ├── llm_client.py               # 写作大模型客户端
    ├── generate_cover_ai.py        # AI 生图封面
    ├── generate_cover.py           # 本地模板封面（兜底）
    ├── http_util.py                # 短连接 HTTP（防 Windows 10053）
    └── selftest.py                 # 离线冒烟自测
```

## 🔧 开发与验证

```bash
# 离线冒烟自测（排版/摘要/封面/预览，不调任何外部 API）
python scripts/selftest.py

# 排版 dry-run（不调微信接口）
python scripts/one_click_publish.py --md samples/demo.md --dry-run --open-preview

# 本地打包 exe（产物 dist/公众号助手.exe）
build_exe.bat
```

**自动打包**：推送后 GitHub Actions 自动构建 Windows exe——
- push 到 `main` → Actions 页面下载构建产物（保留 30 天）
- push 标签 `v*`（如 `v1.0.0`）→ 自动创建 Release，exe 作为附件

```bash
git tag v1.0.0 && git push origin main --tags
```

## ❓ 常见错误

| 现象 | 原因 | 处理 |
|------|------|------|
| 40164 / 无效 IP | 出口 IP 未加白 | 公众号后台 IP 白名单加入运行机 IP |
| 40001 / 42001 | token 错或过期 | 检查 AppSecret |
| 40007 | 封面 media_id 无效 | 封面必须走永久素材接口（脚本已处理） |
| 正文图片空白 | 外链图被微信过滤 | 已自动转微信图床；失败看上传日志 |
| SmartScreen 拦截 | exe 无代码签名 | 「更多信息」→「仍要运行」 |

## 📄 微信官方接口要点

1. 新增草稿：`POST /cgi-bin/draft/add`，正文仅支持内联样式 HTML
2. 封面必须是永久素材 `media_id`（`material/add_material`）
3. 正文图片走 `media/uploadimg` 转微信图床（jpg/png、单张 ≤1MB，超限自动压缩）
4. 中文 JSON 必须 `ensure_ascii=False`，否则 45003 等错误
5. 标题 ≤64 字、摘要 ≤120 字、作者 ≤8 字（均自动截断）
6. 草稿发布后从草稿箱移除属正常行为

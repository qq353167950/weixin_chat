# Windows 一键进草稿箱（方案 2）

IP 白名单由你自己在公众号后台填写；本目录只提供查 IP 工具和 `.env` 备忘字段。

---

## 你要自己填的两处

### A. 本地配置文件 `.env`（脚本用）

| 字段 | 谁填 | 说明 |
|------|------|------|
| `WECHAT_APPID=` | 你 | 公众号 AppID |
| `WECHAT_APPSECRET=` | 你 | 公众号 AppSecret |
| `WECHAT_IP_WHITELIST=` | 你（可选备忘） | 只是记在本地，**不会自动写入微信** |
| `WECHAT_AUTHOR=` | 你（可选） | 作者名 |
| `DEFAULT_MD=` | 你（可选） | 默认文章路径 |
| `DEFAULT_COVER=` | 你（可选） | 默认封面路径 |

### B. 微信公众号后台 IP 白名单（必须你手动）

路径：

```text
mp.weixin.qq.com
  → 设置与开发
  → 基本配置
  → IP白名单
  → 添加「你的公网出口 IP」
```

**脚本无法代替你点后台。** 先双击 `show_my_ip.bat` 看 IP，再自己粘贴进白名单。

---

## 五步上手

1. 把整个 `wechat-auto-publish` 文件夹拷到 Windows（如 `D:\wechat-auto-publish`）
2. 安装 [Python 3.10+](https://www.python.org/downloads/)，安装时勾选 **Add python.exe to PATH**
3. 双击 **`setup_windows.bat`**
4. 用记事本打开 **`.env`**，填写：
   ```env
   WECHAT_APPID=你的AppID
   WECHAT_APPSECRET=你的AppSecret
   WECHAT_IP_WHITELIST=
   ```
   `WECHAT_IP_WHITELIST` 可先空着；或填上 `show_my_ip.bat` 显示的 IP 作备忘。
5. 双击 **`show_my_ip.bat`** → 复制 IP → **自己**加到微信 IP 白名单

准备封面：

- 默认路径：`samples\cover.jpg`（自己放一张图进去）
- 或改 `.env` 里 `DEFAULT_COVER=你的路径`

文章：

- 默认：`samples\demo.md`
- 或改 `DEFAULT_MD=`，或把 md 拖到 bat 上（见下）

---

## 日常使用

| 操作 | 怎么做 |
|------|--------|
| 只预览排版，不推微信 | 双击 `dry_run.bat` |
| 推送到草稿箱 | 双击 `publish_draft.bat` |
| 指定某篇文章 | 命令行见下方 |

### 命令行指定文件

在资源管理器地址栏输入 `cmd` 回车，或 PowerShell：

```bat
cd /d D:\wechat-auto-publish
dry_run.bat D:\articles\我的文章.md D:\articles\cover.jpg
publish_draft.bat D:\articles\我的文章.md D:\articles\cover.jpg
```

---

## 和 ima 爆文写作怎么配合

1. 在 ima 用「公众号爆文写作」生成成稿  
2. 复制为 Markdown，第一行：`# 标题`  
3. 存到本机，例如 `D:\articles\xxx.md`  
4. 准备封面 jpg  
5. 运行：
   ```bat
   publish_draft.bat D:\articles\xxx.md D:\articles\cover.jpg
   ```
6. 打开公众号后台 → 草稿箱 → 预览 → 你手动点发布

---

## 文件一览

```text
wechat-auto-publish/
├── setup_windows.bat      ← 第一次运行：装环境、生成 .env
├── show_my_ip.bat          ← 显示公网 IP（你自己去后台加白）
├── dry_run.bat             ← 预览排版
├── publish_draft.bat       ← 推草稿箱
├── .env.example            ← 配置模板（IP 字段留空）
├── .env                    ← 运行 setup 后生成，你自己填
├── WINDOWS_SETUP.md        ← 本文
├── scripts\...
└── samples\demo.md
```

---

## 常见问题

**Q：报 IP 相关错误（如 40164）**  
A：运行 `show_my_ip.bat`，把显示的 IP **手动**加到微信白名单。家用宽带 IP 可能变，变了要再加一次。

**Q：`.env` 里填了 `WECHAT_IP_WHITELIST` 为什么还失败？**  
A：该字段只是备忘，**不会**自动同步到微信后台。白名单只能你在 mp 后台添加。

**Q：封面报错**  
A：确认 `cover.jpg` 存在；尽量用 jpg；文件别过大。

**Q：想固定 IP 一劳永逸**  
A：把脚本放到有固定公网 IP 的云服务器上跑，白名单只加那一台。

---

## 安全提醒

- 不要把 `.env`（含 AppSecret）发到群里或公开仓库  
- 默认只进草稿箱，不自动群发  
- 发布前务必在后台人工预览

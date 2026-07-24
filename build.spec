# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置：公众号助手 桌面客户端（跨平台）
#
#   Windows → dist/公众号助手.exe（单文件，pywebview + Edge WebView2）
#   macOS   → dist/公众号助手.app（BUNDLE，pywebview + WKWebView）
#
# 数据文件约定（对应 scripts/app_paths.py）：
#   gui/index.html、.env.example 打包进应用（运行时从 _MEIPASS 读取）
#   可写数据：Windows = exe 所在目录（不可写落 %APPDATA%）；mac = ~/Library/Application Support
#
# 本地构建：build_exe.bat（Windows）/ pyinstaller build.spec（mac）
# CI 构建：.github/workflows/build.yml

import sys
from pathlib import Path

sys.path.insert(0, "scripts")
from version import __version__ as APP_VERSION  # noqa: E402

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

# Windows 版本资源：先跑 python scripts/gen_version_info.py（CI/本地 build 脚本会调用）
_VERSION_INFO = "version_info.txt" if (IS_WIN and Path("version_info.txt").is_file()) else None

datas = [
    ("gui/index.html", "gui"),
    (".env.example", "."),
    ("assets/app.png", "assets"),
    ("assets/app.ico", "assets"),
    # 离线前端依赖（Lucide 等），运行时由 /vendor/* 提供
    ("assets/vendor", "assets/vendor"),
]
hiddenimports = [
    # ddgs 兜底搜索为动态导入，静态分析发现不了
    "ddgs",
]

if IS_WIN:
    from PyInstaller.utils.hooks import collect_data_files

    # pythonnet / clr_loader 的运行时 DLL（WebView2 桥接需要）
    datas += collect_data_files("clr_loader")
    datas += collect_data_files("pythonnet")
    datas += collect_data_files("webview", includes=["**/*.dll", "**/*.json"])
    hiddenimports += [
        "webview.platforms.winforms",
        "webview.platforms.edgechromium",
        "clr_loader",
        "clr",
    ]
elif IS_MAC:
    hiddenimports += ["webview.platforms.cocoa"]

a = Analysis(
    ["scripts/gui_app.py"],
    pathex=["scripts"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc_data", "sqlite3", "lib2to3",
              "doctest", "test", "_distutils_hack"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="公众号助手",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,      # 桌面程序：无控制台黑窗，日志落数据目录 app.log
    icon="assets/app.icns" if IS_MAC else "assets\\app.ico",
    version=_VERSION_INFO,  # 嵌入 FileVersion，利于覆盖安装后刷新壳图标
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

if IS_MAC:
    app = BUNDLE(
        exe,
        name="公众号助手.app",
        icon="assets/app.icns",
        bundle_identifier="io.github.qq353167950.weixinchat",
        info_plist={
            "CFBundleDisplayName": "公众号助手",
            "CFBundleShortVersionString": APP_VERSION,
            "NSHighResolutionCapable": True,
            # WKWebView 访问本机 Flask 服务
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        },
    )

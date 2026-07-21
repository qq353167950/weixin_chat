# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置：公众号助手 桌面客户端（原生窗口，pywebview + WebView2）
#
# 数据文件约定（对应 scripts/app_paths.py）：
#   gui/index.html、.env.example 打包进 exe（运行时从 _MEIPASS 读取）
#   .env 与 runs/ 在 exe 所在目录生成（可写数据不进包）
#
# 本地构建：build_exe.bat（产物 dist/公众号助手.exe）
# CI 构建：.github/workflows/build.yml

from PyInstaller.utils.hooks import collect_data_files

a = Analysis(
    ["scripts\\gui_app.py"],
    pathex=["scripts"],
    binaries=[],
    datas=[
        ("gui\\index.html", "gui"),
        (".env.example", "."),
        # pythonnet / clr_loader 的运行时 DLL（WebView2 桥接需要）
        *collect_data_files("clr_loader"),
        *collect_data_files("pythonnet"),
        *collect_data_files("webview", includes=["**/*.dll", "**/*.json"]),
    ],
    hiddenimports=[
        # ddgs 兜底搜索为动态导入，静态分析发现不了
        "ddgs",
        # pywebview Windows 后端为运行时按平台导入
        "webview.platforms.winforms",
        "webview.platforms.edgechromium",
        "clr_loader",
        "clr",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc_data"],
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
    console=False,      # 桌面程序：无控制台黑窗，日志不落终端
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

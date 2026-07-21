# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置：公众号助手 GUI 单文件 exe
#
# 数据文件约定（对应 scripts/app_paths.py）：
#   gui/index.html、.env.example 打包进 exe（运行时从 _MEIPASS 读取）
#   .env 与 runs/ 在 exe 所在目录生成（可写数据不进包）
#
# 本地构建：build_exe.bat（产物 dist/公众号助手.exe）
# CI 构建：.github/workflows/build.yml

a = Analysis(
    ["scripts\\gui_server.py"],
    pathex=["scripts"],
    binaries=[],
    datas=[
        ("gui\\index.html", "gui"),
        (".env.example", "."),
    ],
    hiddenimports=[
        # ddgs 兜底搜索为动态导入，静态分析发现不了
        "ddgs",
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
    name="wechat-assistant",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,       # 保留控制台：启动日志与报错直接可见，双击关窗即退出
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

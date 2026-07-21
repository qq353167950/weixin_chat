@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title WeChat Assistant GUI

if not exist ".venv\Scripts\python.exe" (
  echo [setup] creating environment, first time only...
  where python >nul 2>&1
  if errorlevel 1 (
    echo [ERROR] Python not found.
    echo Install Python 3.10+ and CHECK "Add python.exe to PATH"
    pause
    exit /b 1
  )
  python -m venv .venv
  if errorlevel 1 (
    echo [ERROR] venv failed
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install -U pip -q
)

if not exist ".env" (
  if exist ".env.example" (
    copy /Y ".env.example" ".env" >nul
    echo Created .env from .env.example
    echo You can fill keys later in the GUI Settings page.
  )
)

rem 依赖未变化则跳过安装（标记文件为上次成功安装时的 requirements.txt 快照）
set NEED_INSTALL=1
if exist ".venv-requirements.hash" (
  fc /b "requirements.txt" ".venv-requirements.hash" >nul 2>&1
  if not errorlevel 1 set NEED_INSTALL=0
)
if %NEED_INSTALL%==1 (
  echo [setup] installing dependencies...
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
  if errorlevel 1 (
    echo [ERROR] pip install failed
    pause
    exit /b 1
  )
  copy /Y "requirements.txt" ".venv-requirements.hash" >nul
)

".venv\Scripts\python.exe" scripts\gui_app.py
set ERR=%ERRORLEVEL%
echo.
if not %ERR%==0 (
  echo [exit code %ERR%]
  pause
)

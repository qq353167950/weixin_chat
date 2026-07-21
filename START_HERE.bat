@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title WeChat Full Pipeline - ONE ENTRY
echo ============================================
echo   ONLY CLICK THIS BAT
echo   topic -^> write -^> AI cover -^> draft
echo ============================================
echo.

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
    echo.
    echo Please fill .env (3 separate blocks), then save and run again:
    echo   [Write]  LLM_API_KEY + LLM_BASE_URL + LLM_MODEL
    echo   [Search] SEARCH_PROVIDER + TAVILY_API_KEY (or other search key)
    echo   [Image]  IMAGE_PROVIDER + IMAGE_API_KEY + IMAGE_BASE_URL + IMAGE_MODEL
    echo   [WeChat] WECHAT_APPID + WECHAT_APPSECRET
    echo.
    notepad ".env"
    pause
    exit /b 0
  )
  echo [ERROR] missing .env.example
  pause
  exit /b 1
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

".venv\Scripts\python.exe" scripts\pipeline.py
set ERR=%ERRORLEVEL%
echo.
if not %ERR%==0 echo [exit code %ERR%]
pause

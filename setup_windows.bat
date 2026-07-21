@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================
echo  WeChat Draft Setup (Windows)
echo ============================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo [ERROR] python not found.
  echo Install Python 3.10+ from https://www.python.org/downloads/
  echo IMPORTANT: check "Add python.exe to PATH" during install.
  echo Then close this window, open a NEW cmd, run this bat again.
  echo.
  pause
  exit /b 1
)

python --version
echo.

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Creating venv ...
  python -m venv .venv
  if errorlevel 1 (
    echo [ERROR] venv failed
    pause
    exit /b 1
  )
) else (
  echo [1/3] venv exists, skip
)

echo [2/3] Installing deps ...
".venv\Scripts\python.exe" -m pip install -U pip -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] pip install failed
  pause
  exit /b 1
)

if not exist ".env" (
  echo [3/3] Creating .env from .env.example ...
  copy /Y ".env.example" ".env" >nul
  echo.
  echo Next: open .env with Notepad and fill:
  echo   WECHAT_APPID=
  echo   WECHAT_APPSECRET=
  echo Then add your public IP to WeChat IP whitelist.
) else (
  echo [3/3] .env exists, not overwritten
)

echo.
echo Done.
echo 1. Edit .env
echo 2. Run show_my_ip.bat, add IP in mp.weixin.qq.com
echo 3. Put cover at samples\cover.jpg
echo 4. Run dry_run.bat then publish_draft.bat
echo.
pause

@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================
echo  Your public IP for WeChat whitelist
echo ============================================
echo.
echo Add the IP into:
echo   mp.weixin.qq.com - Settings - Basic Config - IP whitelist
echo.

set "GOT_IP="

REM --- Method 1: curl ---
where curl >nul 2>&1
if not errorlevel 1 (
  echo [try] curl ...
  for /f "usebackq delims=" %%I in (`curl -s --max-time 10 https://api.ipify.org 2^>nul`) do set "GOT_IP=%%I"
  if not defined GOT_IP (
    for /f "usebackq delims=" %%I in (`curl -s --max-time 10 https://ifconfig.me/ip 2^>nul`) do set "GOT_IP=%%I"
  )
)

REM --- Method 2: venv python ---
if not defined GOT_IP if exist ".venv\Scripts\python.exe" (
  echo [try] .venv python ...
  for /f "usebackq delims=" %%I in (`".venv\Scripts\python.exe" -c "import urllib.request; print(urllib.request.urlopen('https://api.ipify.org', timeout=10).read().decode().strip())" 2^>nul`) do set "GOT_IP=%%I"
)

REM --- Method 3: system python ---
if not defined GOT_IP (
  where python >nul 2>&1
  if not errorlevel 1 (
    echo [try] python ...
    for /f "usebackq delims=" %%I in (`python -c "import urllib.request; print(urllib.request.urlopen('https://api.ipify.org', timeout=10).read().decode().strip())" 2^>nul`) do set "GOT_IP=%%I"
  )
)

REM --- Method 4: PowerShell ---
if not defined GOT_IP (
  echo [try] powershell ...
  for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri 'https://api.ipify.org' -UseBasicParsing -TimeoutSec 10).Content.Trim() } catch { '' }" 2^>nul`) do set "GOT_IP=%%I"
)

echo.
if defined GOT_IP (
  echo ============================================
  echo  Public IP:
  echo.
  echo     %GOT_IP%
  echo.
  echo ============================================
  echo Copy the line above into WeChat IP whitelist.
) else (
  echo [FAILED] Could not detect public IP automatically.
  echo.
  echo Do this instead - open browser and visit ONE of:
  echo   https://api.ipify.org
  echo   https://ifconfig.me/ip
  echo   https://www.ip.cn
  echo   or search Baidu for: IP
  echo.
  echo The number shown, e.g. 123.45.67.89
  echo is what you paste into WeChat IP whitelist.
)

echo.
echo Notes:
echo - Home broadband IP may change later
echo - Turn OFF VPN when checking IP and when publishing
echo - Use the SAME PC/network for publish_draft.bat
echo.
pause

@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Run setup_windows.bat first.
  pause
  exit /b 1
)

if not exist ".env" (
  echo [ERROR] Missing .env. Run setup_windows.bat and fill keys.
  pause
  exit /b 1
)

set "MD=samples\demo.md"
set "COVER=samples\cover.jpg"
set "THEME=default"
set "AUTHOR="
set "AUTO_COVER=1"
set "PROVIDER="
set "STYLE="

if exist ".env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
    if /I "%%A"=="DEFAULT_MD" set "MD=%%B"
    if /I "%%A"=="DEFAULT_COVER" set "COVER=%%B"
    if /I "%%A"=="DEFAULT_THEME" set "THEME=%%B"
    if /I "%%A"=="WECHAT_AUTHOR" set "AUTHOR=%%B"
    if /I "%%A"=="WECHAT_AUTO_COVER" set "AUTO_COVER=%%B"
    if /I "%%A"=="IMAGE_PROVIDER" set "PROVIDER=%%B"
    if /I "%%A"=="COVER_PROVIDER" if "%PROVIDER%"=="" set "PROVIDER=%%B"
    if /I "%%A"=="IMAGE_STYLE" set "STYLE=%%B"
    if /I "%%A"=="COVER_STYLE" if "%STYLE%"=="" set "STYLE=%%B"
  )
)

if not "%~1"=="" set "MD=%~1"
if not "%~2"=="" set "COVER=%~2"

echo ============================================
echo  Push to WeChat draft box
echo  MD:    %MD%
echo  COVER: %COVER%
echo ============================================
echo.

if not exist "%MD%" (
  echo [ERROR] Markdown not found: %MD%
  pause
  exit /b 1
)

if not exist "%COVER%" (
  if "%AUTO_COVER%"=="1" (
    echo Cover missing, AI generating...
    ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
    set "EXTRA="
    if not "%PROVIDER%"=="" set "EXTRA=--provider %PROVIDER%"
    if not "%STYLE%"=="" set "EXTRA=%EXTRA% --style %STYLE%"
    ".venv\Scripts\python.exe" scripts\generate_cover_ai.py --md "%MD%" --out "%COVER%" %EXTRA%
    if errorlevel 1 (
      echo [ERROR] AI cover failed. Check API keys in .env
      pause
      exit /b 1
    )
  ) else (
    echo [ERROR] Cover not found: %COVER%
    echo Run make_cover.bat or put your own jpg there.
    pause
    exit /b 1
  )
)

if "%AUTHOR%"=="" (
  ".venv\Scripts\python.exe" scripts\one_click_publish.py --md "%MD%" --cover "%COVER%" --theme %THEME%
) else (
  ".venv\Scripts\python.exe" scripts\one_click_publish.py --md "%MD%" --cover "%COVER%" --theme %THEME% --author "%AUTHOR%"
)
set ERR=%ERRORLEVEL%
echo.
if %ERR%==0 (
  echo SUCCESS. Open mp.weixin.qq.com - Drafts
) else (
  echo FAILED. Check IP whitelist / AppID / AppSecret / cover
)
echo.
pause

@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Run setup_windows.bat first.
  pause
  exit /b 1
)

set "MD=samples\demo.md"
set "COVER=samples\cover.jpg"
set "THEME=default"

if exist ".env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
    if /I "%%A"=="DEFAULT_MD" set "MD=%%B"
    if /I "%%A"=="DEFAULT_COVER" set "COVER=%%B"
    if /I "%%A"=="DEFAULT_THEME" set "THEME=%%B"
  )
)

if not "%~1"=="" set "MD=%~1"
if not "%~2"=="" set "COVER=%~2"

echo ============================================
echo  Dry-run layout preview only
echo  MD:    %MD%
echo  COVER: %COVER%
echo  THEME: %THEME%
echo ============================================
echo.

if not exist "%MD%" (
  echo [ERROR] Markdown not found: %MD%
  pause
  exit /b 1
)

".venv\Scripts\python.exe" scripts\one_click_publish.py --md "%MD%" --cover "%COVER%" --theme %THEME% --dry-run --out-html samples\last_preview.wechat.html
echo.
echo Preview HTML: samples\last_preview.wechat.html
echo.
pause

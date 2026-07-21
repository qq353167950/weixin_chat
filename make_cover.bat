@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Run setup_windows.bat first.
  pause
  exit /b 1
)

set "MD=samples\demo.md"
set "OUT=samples\cover.jpg"
set "PROVIDER="
set "STYLE="

if exist ".env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
    if /I "%%A"=="DEFAULT_MD" set "MD=%%B"
    if /I "%%A"=="DEFAULT_COVER" set "OUT=%%B"
    if /I "%%A"=="IMAGE_PROVIDER" set "PROVIDER=%%B"
    if /I "%%A"=="COVER_PROVIDER" if "%PROVIDER%"=="" set "PROVIDER=%%B"
    if /I "%%A"=="IMAGE_STYLE" set "STYLE=%%B"
    if /I "%%A"=="COVER_STYLE" if "%STYLE%"=="" set "STYLE=%%B"
  )
)

if not "%~1"=="" set "MD=%~1"
if not "%~2"=="" set "OUT=%~2"

echo ============================================
echo  AI generate cover image
echo  MD:       %MD%
echo  OUT:      %OUT%
echo  PROVIDER: %PROVIDER%
echo  STYLE:    %STYLE%
echo ============================================
echo.

if not exist "%MD%" (
  echo [ERROR] Markdown not found: %MD%
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m pip install -q -r requirements.txt

set "EXTRA="
if not "%PROVIDER%"=="" set "EXTRA=%EXTRA% --provider %PROVIDER%"
if not "%STYLE%"=="" set "EXTRA=%EXTRA% --style %STYLE%"

".venv\Scripts\python.exe" scripts\generate_cover_ai.py --md "%MD%" --out "%OUT%" %EXTRA%
set ERR=%ERRORLEVEL%
echo.
if %ERR%==0 (
  echo Done. Open %OUT% to check, then publish_draft.bat
) else (
  echo Failed. Check API keys in .env:
  echo   COVER_PROVIDER=openai  + OPENAI_API_KEY=...
  echo   COVER_PROVIDER=dashscope + DASHSCOPE_API_KEY=...
)
echo.
pause

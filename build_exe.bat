@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Build EXE

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] run GUI.bat first to create .venv
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m pip install -q -r requirements.txt pyinstaller
".venv\Scripts\python.exe" -m PyInstaller build.spec --noconfirm --clean
if errorlevel 1 (
  echo [ERROR] build failed
  pause
  exit /b 1
)
echo.
echo Done: dist\wechat-assistant.exe
pause

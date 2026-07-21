@echo off
setlocal EnableExtensions
cd /d "%~dp0.."
title Build Installer

rem 先确保 exe 是最新的
if not exist "dist\公众号助手.exe" (
  echo [ERROR] dist\公众号助手.exe not found. Run build_exe.bat first.
  pause
  exit /b 1
)

set ISCC="%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist %ISCC% set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist %ISCC% (
  echo [ERROR] Inno Setup 6 not found. Install: winget install JRSoftware.InnoSetup
  pause
  exit /b 1
)

%ISCC% installer\installer.iss
if errorlevel 1 (
  echo [ERROR] installer build failed
  pause
  exit /b 1
)
echo.
echo Done: dist\公众号助手-安装包-v*.exe
pause

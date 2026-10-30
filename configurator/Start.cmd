@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "%~dp0Starbridge-Assistant.ps1" (
  echo Please extract the entire ZIP archive before starting.
  pause
  exit /b 1
)
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0Starbridge-Assistant.ps1"
if errorlevel 1 pause

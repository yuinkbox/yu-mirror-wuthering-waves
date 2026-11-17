@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo === Yu-Mirror core update ===
echo.

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found. Run start.bat first.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" "tools\upstream_sync.py" --apply --paths all --yes
echo.
pause

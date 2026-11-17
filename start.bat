@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto deps

echo [setup] 未找到 .venv，正在创建 Python 3.12 虚拟环境...
py -3.12 -m venv .venv 2>nul
if exist ".venv\Scripts\python.exe" goto deps
python -m venv .venv 2>nul
if not exist ".venv\Scripts\python.exe" (
  echo [error] 没有找到 Python 3.12，请先安装 Python 3.12 并勾选 Add to PATH。
  pause
  exit /b 1
)

:deps
".venv\Scripts\python.exe" -c "import ok, cv2, PySide6, openvino" >nul 2>nul
if errorlevel 1 (
  echo [setup] 首次运行，正在安装依赖，约 1GB，请耐心等待...
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --progress-bar off -i https://mirrors.ustc.edu.cn/pypi/simple/ -r requirements.txt
  if errorlevel 1 (
    echo [error] 依赖安装失败，请检查网络或代理。
    pause
    exit /b 1
  )
)

:run
".venv\Scripts\python.exe" main.py %*
if errorlevel 1 (
  echo.
  echo [error] 程序异常退出，错误信息见上方或 logs\ok-script.log。
  pause
)
exit /b %errorlevel%

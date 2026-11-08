# Yu-Mingchao Pro 启动脚本（带参数版）
#
# 例：
#   .\start.ps1                       # 启动 GUI
#   .\start.ps1 -Task 1 -Exit         # 启动后自动跑第 1 个任务，完成即退出
#   .\start.ps1 -Debug                # 调试模式
#   .\start.ps1 -ExtraArg "-c config" # 透传额外参数
param(
    [int]$Task = 0,
    [switch]$Exit,
    [switch]$Debug,
    [string]$ExtraArg = ""
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$venvPy = Join-Path $root '.venv\Scripts\python.exe'

function New-Venv {
    Write-Host '[setup] 未找到 .venv，正在创建 Python 3.12 虚拟环境...' -ForegroundColor Cyan
    $candidates = @(
        @{ Exe = 'py';      Args = @('-3.12') },
        @{ Exe = 'python';  Args = @() }
    )
    foreach ($c in $candidates) {
        if (Get-Command $c.Exe -ErrorAction SilentlyContinue) {
            $probe = & $c.Exe @($c.Args + @('-c', 'import sys; assert sys.version_info[:2]==(3,12), sys.version'))
            if ($LASTEXITCODE -eq 0) {
                & $c.Exe @($c.Args + @('-m', 'venv', '.venv'))
                if ($LASTEXITCODE -eq 0) { return }
            }
        }
    }
    throw '没有找到可用的 Python 3.12，请先安装（安装时勾选 Add to PATH）。'
}

if (-not (Test-Path $venvPy)) { New-Venv }

# 依赖自检：缺了才装
& $venvPy -c 'import ok, cv2, PySide6, openvino' 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host '[setup] 首次运行，正在安装依赖，约 1GB，请耐心等待...' -ForegroundColor Cyan
    & $venvPy -m pip install --disable-pip-version-check --timeout 60 --retries 5 -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw '依赖安装失败，请检查网络或代理。' }
}

$script = if ($Debug) { 'main_debug.py' } else { 'main.py' }
$argv = @()
if ($Task -gt 0) { $argv += @('-t', "$Task") }
if ($Exit) { $argv += '-e' }
if ($ExtraArg) { $argv += $ExtraArg.Split(' ') }

Write-Host ("[run] {0} {1}" -f $script, ($argv -join ' ')) -ForegroundColor Green
& $venvPy $script @argv
exit $LASTEXITCODE

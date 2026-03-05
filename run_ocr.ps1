# Samsung OCR Launcher (PowerShell 版)
# 用於解決 Windows BAT 路徑亂碼問題

# 1. 取得管理員權限
if (-NOT ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Warning "正在以管理員權限重新啟動..."
    Start-Process powershell.exe "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    exit
}

# 2. 切換到腳本所在目錄
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location -Path $ScriptDir

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "   Samsung OCR Launcher (PS Unicode 修復版)"
Write-Host "==========================================" -ForegroundColor Cyan

# 3. 終止舊程序
Write-Host "[1/4] 正在清理舊的 Python 程序..."
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process node -ErrorAction SilentlyContinue | Stop-Process -Force

# 4. 釋放 Port 5000
Write-Host "[2/4] 釋放 Port 5000..."
$p = Get-NetTCPConnection -LocalPort 5000 -ErrorAction SilentlyContinue
if ($p) {
    Stop-Process -Id $p.OwningProcess -Force
}

# 5. 清除快取
Write-Host "[3/4] 清除 Python 快取..."
Get-ChildItem -Path . -Filter "__pycache__" -Recurse -Directory | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# 6. 啟動系統
Write-Host "[4/4] 啟動 OCR 系統..." -ForegroundColor Green
python samsung_ocr_batch_processor.py

Write-Host "`n系統已停止。請按任意鍵退出..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")

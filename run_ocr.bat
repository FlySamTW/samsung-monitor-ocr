@echo off
setlocal enabledelayedexpansion
set "PATH=C:\Windows\System32;C:\Windows;C:\Windows\System32\Wbem;C:\Windows\System32\WindowsPowerShell\v1.0\;%PATH%"
chcp 65001 >nul

echo ==========================================
echo      Samsung OCR Launcher v7.9 [STABLE]
echo ==========================================
echo.

echo [1/5] 執行暴力進程清理 (Aggressive Cleanup)...
:: 第一波：強制殺除所有 Python 與 Node 進程
taskkill /F /IM python.exe /T 2>nul
taskkill /F /IM node.exe /T 2>nul
taskkill /F /FI "WINDOWTITLE eq OCR Backend*" /T 2>nul

:: 等待一秒讓系統釋放資源
timeout /t 1 /nobreak >nul

:: 第二波：再次確認，確保無殘留
taskkill /F /IM python.exe /T 2>nul

echo.
echo [2/5] 釋放 Port 5000 (強力解鎖模式)...
:: 使用 PowerShell 強制尋找並殺死佔用 Port 5000 的所有 Process ID
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -Command "Write-Host '檢查 Port 5000...'; $procs = Get-NetTCPConnection -LocalPort 5000 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique; if ($procs) { foreach($pid in $procs) { Write-Host '>> 正在殺死 PID '$pid'...'; Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue } } else { Write-Host '>> Port 5000 乾淨無佔用。' }"

echo.
echo [3/5] 清除 Python 快取...
if exist "__pycache__" rmdir /s /q "__pycache__"

echo.
echo [4/5] 啟動核心引擎 (v7.9)...
cd /d "%~dp0"
start "OCR Backend Server (v7.9)" cmd /k "python samsung_ocr_batch_processor.py --model qwen/qwen3-vl-4b"

echo.
echo [5/5] 初始化控制面板...
ping 127.0.0.1 -n 8 >nul
start http://localhost:5000/

echo.
echo ==========================================
echo      🚀 啟動完畢！ (Version 7.9)
echo      - 遠景/近景判定精確化 🎯🔍
echo      - 降低遠景誤判率，提高數據提取率
echo ==========================================
pause

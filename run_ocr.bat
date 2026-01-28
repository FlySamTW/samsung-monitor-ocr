@echo off
setlocal enabledelayedexpansion
set "PATH=C:\Windows\System32;C:\Windows;C:\Windows\System32\Wbem;C:\Windows\System32\WindowsPowerShell\v1.0\;%PATH%"
chcp 65001 >nul

echo ==========================================
echo      Samsung OCR Launcher (Latest)
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
echo.
echo [4/5] 啟動核心引擎 (Auto-Load Latest)...
cd /d "%~dp0"

:: 設定視窗標題 (這會讓下一次執行時能識別並殺死此視窗)
title OCR Backend Server

echo.
echo [5/5] 準備開啟控制面板 (8秒後)...
:: 背景執行：開啟瀏覽器 (最小化視窗執行等待)
start /min "" cmd /c "timeout /t 8 /nobreak >nul && start http://localhost:5000"

echo ---------------------------------------------------
echo  OCR 核心已啟動，請勿關閉此視窗 (單一視窗模式)
echo ---------------------------------------------------
:: 直接在當前視窗執行 Python (會卡住視窗直到結束)
python samsung_ocr_batch_processor.py --model qwen/qwen3-vl-4b --api_base http://192.168.0.234:1234/v1

:: 當 Python 結束後才會執行到這裡
echo.
echo 伺服器已停止。
pause

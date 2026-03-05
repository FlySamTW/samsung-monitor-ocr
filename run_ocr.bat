@echo off
setlocal enabledelayedexpansion
:: 強制使用 UTF-8 編碼解析
chcp 65001 >nul

:: [重要] 強制修復 PATH 變數，避免找不到 taskkill
set "PATH=C:\Windows\System32;C:\Windows;C:\Windows\System32\Wbem;C:\Windows\System32\WindowsPowerShell\v1.0\;%PATH%"

echo ==========================================
echo    Samsung OCR Launcher (v19.3 嘗試執行版)
echo ==========================================
echo.

:: ==========================================
:: [0/5] 權限警告 (但不強制停止)
:: ==========================================
>nul 2>&1 "%SYSTEMROOT%\system32\cacls.exe" "%SYSTEMROOT%\system32\config\system"
if '%errorlevel%' NEQ '0' (
    echo [! 提示] 目前非管理員權限執行，清理舊程序可能會失敗。
    echo        (若程式跑不動，請再改用「按右鍵 -> 以管理員身分執行」)
) else (
    echo [OK] 已取得管理員權限。
)
echo.

:: 核心邏輯：切換目錄
pushd "%~dp0"

echo [1/5] 正在嘗試清理舊的 Python 程序...
powershell -NoProfile -Command "Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue" 2>nul

echo [2/5] 釋放 Port 5000...
C:\Windows\System32\taskkill.exe /F /IM python.exe /T 2>nul
C:\Windows\System32\taskkill.exe /F /IM node.exe /T 2>nul
powershell -NoProfile -Command "$p=Get-NetTCPConnection -LocalPort 5000 -ErrorAction SilentlyContinue; if($p){Stop-Process -Id $p.OwningProcess -Force}" 2>nul

echo [3/5] 清除 Python 快取...
for /d /r . %%d in (__pycache__) do (
    if exist "%%d" rmdir /s /q "%%d" 2>nul
)

echo [4/5] 啟動 OCR 後端系統...
set PYTHON_CMD=python
%PYTHON_CMD% samsung_ocr_batch_processor.py

echo.
echo [⚠️ 警告] 系統已停止。如果是非正常結束，請檢查上方錯誤訊息。
pause

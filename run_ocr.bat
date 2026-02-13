@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

:: [重要] 強制修復 PATH 變數，避免找不到 taskkill
set "PATH=C:\Windows\System32;C:\Windows;C:\Windows\System32\Wbem;C:\Windows\System32\WindowsPowerShell\v1.0\;%PATH%"

:: ==========================================
:: [0/5] 請求管理員權限 (Self-Elevation)
:: ==========================================
>nul 2>&1 "%SYSTEMROOT%\system32\cacls.exe" "%SYSTEMROOT%\system32\config\system"
if '%errorlevel%' NEQ '0' (
    echo [警告] 未取得管理員權限！正在請求權限...
    goto UACPrompt
) else ( goto gotAdmin )

:UACPrompt
    echo Set UAC = CreateObject^("Shell.Application"^) > "%temp%\getadmin.vbs"
    echo UAC.ShellExecute "%~s0", "", "", "runas", 1 >> "%temp%\getadmin.vbs"
    "%temp%\getadmin.vbs"
    del "%temp%\getadmin.vbs"
    exit /B

:gotAdmin
    pushd "%CD%"
    CD /D "%~dp0"

echo ==========================================
echo    Samsung OCR Launcher (v19.1 PATH修復版)
echo    已取得管理員權限 - 準備獵殺殭屍程序
echo ==========================================
echo.

:: ==========================================
:: [1/5] 終極獵殺 (Multi-Method Kill)
:: ==========================================
echo [1/5] 正在強制終止 Python 程序...

:: 方法 1: PowerShell Stop-Process (最強力)
echo    >> 嘗試 PowerShell Stop-Process...
powershell -NoProfile -Command "Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue"

:: 方法 2: Taskkill (使用絕對路徑，避免 PATH 問題)
echo    >> 嘗試 C:\Windows\System32\taskkill.exe...
C:\Windows\System32\taskkill.exe /F /IM python.exe /T 2>nul
C:\Windows\System32\taskkill.exe /F /IM node.exe /T 2>nul
C:\Windows\System32\taskkill.exe /F /FI "WINDOWTITLE eq OCR Backend*" /T 2>nul

:: 方法 3: WMIC (備用)
echo    >> 嘗試 WMIC Call Terminate...
wmic process where name="python.exe" call terminate >nul 2>&1

:: 確認是否還有殘留
timeout /t 2 /nobreak >nul
tasklist | findstr /i "python.exe" >nul
if %errorlevel%==0 (
    echo [❌ 失敗] Python 殭屍程序仍然存活！請手動重開機！
    tasklist | findstr /i "python.exe"
    pause
    exit
) else (
    echo [✅ 成功] 所有 Python 程序已清除。
)

echo.
echo [2/5] 釋放 Port 5000...
powershell -NoProfile -Command "$p=Get-NetTCPConnection -LocalPort 5000 -ErrorAction SilentlyContinue; if($p){Stop-Process -Id $p.OwningProcess -Force}"

echo.
echo [3/5] 清除 Python 快取...
for /d /r . %%d in (__pycache__) do (
    if exist "%%d" rmdir /s /q "%%d" 2>nul
)

echo.
echo [4/5] 啟動 OCR 後端系統 (v19.0)...
echo ------------------------------------------
set PYTHON_CMD=python
where python >nul 2>nul
if %errorlevel% NEQ 0 (
    echo [❌ 錯誤] 找不到 python 指令！請確認已安裝 Python。
    pause
    exit
)

:: 啟動並保持視窗開啟 (交由 Python 控制)
echo.
echo [5/5] 自動開啟 Dashboard... (交由 Python 控制)
echo.
%PYTHON_CMD% samsung_ocr_batch_processor.py

echo.
echo [⚠️ 警告] 系統已停止。如果是非正常結束，請檢查上方錯誤訊息。
pause

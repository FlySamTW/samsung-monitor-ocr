@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

echo ==========================================
echo    Prompt 部署工具 v1.0
echo    自動化配置更新與服務重啟
echo ==========================================
echo.

echo 📋 檢查清單:
echo   ✓ 備份現有 Prompt 檔案
echo   ✓ 遷移到 Bundle 系統
echo   ✓ 清除 Python 快取
echo   ✓ 重啟 OCR 伺服器
echo.
pause

echo.
echo [1/5] 停止現有伺服器...
C:\Windows\System32\taskkill.exe /F /IM python.exe /T 2>nul
timeout /t 2 /nobreak >nul
echo ✅ 伺服器已停止

echo.
echo [2/5] 建立備份資料夾...
if not exist "backup" mkdir backup
echo ✅ 備份資料夾就緒

echo.
echo [3/5] 遷移 Prompt 到 Bundle 系統...
python migrate_prompt_to_bundle.py
if errorlevel 1 (
    echo ❌ 遷移失敗！請檢查錯誤訊息。
    pause
    exit /b 1
)
echo ✅ Prompt Bundle 建立成功

echo.
echo [4/5] 清除所有快取...
:: 清除 __pycache__
for /d /r . %%d in (__pycache__) do (
    if exist "%%d" (
        rmdir /s /q "%%d" 2>nul
    )
)

:: 清除 .pyc 檔案
for /r . %%f in (*.pyc) do (
    if exist "%%f" del /f /q "%%f" 2>nul
)

echo ✅ 快取已清除

echo.
echo [5/5] 重啟 OCR 伺服器...
start "" powershell -NoExit -Command "cd '%CD%'; Write-Host '🚀 啟動 OCR 伺服器...' -ForegroundColor Cyan; python samsung_ocr_batch_processor.py --server"

echo.
echo ==========================================
echo ✅ 部署完成！
echo ==========================================
echo.
echo 📦 Prompt Bundle 已更新並啟用
echo 🔄 伺服器正在重新啟動...
echo.
echo 💡 提示：
echo   - 新的 PowerShell 視窗將開啟並執行伺服器
echo   - 請檢查伺服器啟動訊息中的 Prompt 版本號
echo   - 備份檔案位於 backup\ 資料夾
echo.
pause

@echo off
setlocal
chcp 65001 >nul

set "PYTHONIOENCODING=utf-8"
set "PATH=C:\Windows\System32;C:\Windows;C:\Windows\System32\Wbem;C:\Windows\System32\WindowsPowerShell\v1.0\;%PATH%"
pushd "%~dp0"
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

if not defined LOCAL_LLM_API_BASE set "LOCAL_LLM_API_BASE=http://127.0.0.1:1234/v1"
if not defined LOCAL_LLM_MODEL set "LOCAL_LLM_MODEL=qwen/qwen3-vl-8b"
if not defined LOCAL_LLM_MODEL_KEY set "LOCAL_LLM_MODEL_KEY=qwen/qwen3-vl-8b"
if not defined LOCAL_LLM_FALLBACK_MODEL set "LOCAL_LLM_FALLBACK_MODEL=qwen/qwen3-vl-4b"
if not defined LOCAL_LLM_FALLBACK_MODEL_KEY set "LOCAL_LLM_FALLBACK_MODEL_KEY=qwen/qwen3-vl-4b"

if not defined OCR_SOURCE_ROOT (
    set /p "OCR_SOURCE_ROOT=請輸入照片來源根資料夾："
)
if not defined OCR_OUTPUT_DIR (
    set "OCR_OUTPUT_DIR=%OCR_SOURCE_ROOT%_OCR整理"
)

echo ==========================================
echo   Samsung OCR Recursive Flat Export
echo ==========================================
echo Source: %OCR_SOURCE_ROOT%
echo Output: %OCR_OUTPUT_DIR%
echo Model:  %LOCAL_LLM_MODEL%
echo Python: %PY%
echo.

"%PY%" tools\validate_recursive_ocr_inputs.py --source-root "%OCR_SOURCE_ROOT%" --output-dir "%OCR_OUTPUT_DIR%"
if "%errorlevel%" NEQ "0" (
    echo.
    echo [錯誤] 來源或輸出路徑預檢失敗；尚未啟動 LLM 或 OCR 後端。
    if not "%OCR_NO_PAUSE%"=="1" pause
    exit /b 1
)

"%PY%" tools\stop_ocr_server.py
if "%errorlevel%" NEQ "0" (
    echo.
    echo [錯誤] 清理既有 OCR 後端失敗，請關閉舊的 Python 後再重試。
    if not "%OCR_NO_PAUSE%"=="1" pause
    exit /b 1
)

"%PY%" tools\local_llm_manager.py ensure
if "%errorlevel%" NEQ "0" (
    echo.
    echo [錯誤] 本機 LLM 啟動失敗，請確認 LM Studio CLI 與模型。
    if not "%OCR_NO_PAUSE%"=="1" pause
    exit /b 1
)

start "Samsung OCR Server" /min "%PY%" samsung_ocr_batch_processor.py --api_base "%LOCAL_LLM_API_BASE%" --api_key "lm-studio" --model "%LOCAL_LLM_MODEL%" --dir "%OCR_SOURCE_ROOT%"

set "OCR_WATCH_ARGS="
if "%OCR_WATCH%"=="1" set "OCR_WATCH_ARGS=--watch"

"%PY%" tools\recursive_ocr_flat_export.py --source-root "%OCR_SOURCE_ROOT%" --output-dir "%OCR_OUTPUT_DIR%" --backend-url "http://127.0.0.1:5000" --api-base "%LOCAL_LLM_API_BASE%" --api-key "lm-studio" --model "%LOCAL_LLM_MODEL%" %OCR_WATCH_ARGS%
if "%errorlevel%" NEQ "0" (
    echo.
    echo [錯誤] 接力批次未完成，請查看輸出資料夾中的 _ocr_audit。
    call :cleanup_server
    if not "%OCR_NO_PAUSE%"=="1" pause
    exit /b 1
)

"%PY%" tools\recursive_ocr_audit_report.py --output-dir "%OCR_OUTPUT_DIR%"
if "%errorlevel%" NEQ "0" (
    echo.
    echo [錯誤] 輸出驗收未通過，請查看 %OCR_OUTPUT_DIR%\_ocr_audit\audit_report.csv。
    call :cleanup_server
    if not "%OCR_NO_PAUSE%"=="1" pause
    exit /b 1
)

echo.
echo [完成] 接力批次與輸出驗收都已通過：%OCR_OUTPUT_DIR%
call :cleanup_server
if not "%OCR_NO_PAUSE%"=="1" pause
exit /b 0

:cleanup_server
if "%OCR_KEEP_SERVER%"=="1" (
    echo [收尾] 已依 OCR_KEEP_SERVER=1 保留 OCR 後端。
    exit /b 0
)
"%PY%" tools\stop_ocr_server.py
if errorlevel 1 (
    echo [警告] OCR 後端收尾清理失敗；下次啟動前仍會再次清理。
)
exit /b 0

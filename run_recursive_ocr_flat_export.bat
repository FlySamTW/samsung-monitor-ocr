@echo off
setlocal
chcp 65001 >nul

set "PYTHONIOENCODING=utf-8"
set "PATH=C:\Windows\System32;C:\Windows;C:\Windows\System32\Wbem;C:\Windows\System32\WindowsPowerShell\v1.0\;%PATH%"
pushd "%~dp0"

if not defined LOCAL_LLM_API_BASE set "LOCAL_LLM_API_BASE=http://127.0.0.1:1234/v1"
if not defined LOCAL_LLM_MODEL set "LOCAL_LLM_MODEL=qwen3vl8b-ocr"
if not defined LOCAL_LLM_MODEL_KEY set "LOCAL_LLM_MODEL_KEY=qwen/qwen3-vl-8b"
if not defined LOCAL_LLM_FALLBACK_MODEL set "LOCAL_LLM_FALLBACK_MODEL=qwen3vl4b-ocr"
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
echo.

python tools\local_llm_manager.py ensure
if "%errorlevel%" NEQ "0" (
    echo.
    echo [錯誤] 本機 LLM 啟動失敗，請確認 LM Studio CLI 與模型。
    pause
    exit /b 1
)

start "Samsung OCR Server" /min python samsung_ocr_batch_processor.py --api_base "%LOCAL_LLM_API_BASE%" --api_key "lm-studio" --model "%LOCAL_LLM_MODEL%" --dir "%OCR_SOURCE_ROOT%"

python tools\recursive_ocr_flat_export.py --source-root "%OCR_SOURCE_ROOT%" --output-dir "%OCR_OUTPUT_DIR%" --backend-url "http://127.0.0.1:5000" --api-base "%LOCAL_LLM_API_BASE%" --api-key "lm-studio" --model "%LOCAL_LLM_MODEL%"
if "%errorlevel%" NEQ "0" (
    echo.
    echo [錯誤] 接力批次未完成，請查看輸出資料夾中的 _ocr_audit。
    pause
    exit /b 1
)

echo.
echo [完成] 請查看：%OCR_OUTPUT_DIR%
pause

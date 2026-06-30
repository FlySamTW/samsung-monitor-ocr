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
if not defined OCR_IMAGE_DIR set "OCR_IMAGE_DIR=%~dp0"

echo ==========================================
echo   Samsung OCR Launcher - Local LM Studio
echo ==========================================
echo API:   %LOCAL_LLM_API_BASE%
echo Model: %LOCAL_LLM_MODEL%
echo Dir:   %OCR_IMAGE_DIR%
echo.

python tools\local_llm_manager.py ensure
if "%errorlevel%" NEQ "0" (
    echo.
    echo [錯誤] 本機 LLM 啟動失敗，請確認 LM Studio CLI 與模型。
    pause
    exit /b 1
)

start http://localhost:5000
python samsung_ocr_batch_processor.py --api_base "%LOCAL_LLM_API_BASE%" --api_key "lm-studio" --model "%LOCAL_LLM_MODEL%" --dir "%OCR_IMAGE_DIR%"
pause

@echo off
setlocal
chcp 65001 >nul

set "PATH=C:\Windows\System32;C:\Windows;C:\Windows\System32\Wbem;C:\Windows\System32\WindowsPowerShell\v1.0\;%PATH%"
pushd "%~dp0"

if not defined LOCAL_LLM_API_BASE set "LOCAL_LLM_API_BASE=http://127.0.0.1:1234/v1"
if not defined LOCAL_LLM_MODEL set "LOCAL_LLM_MODEL=qwen3vl8b-ocr"
if not defined LOCAL_LLM_MODEL_KEY set "LOCAL_LLM_MODEL_KEY=qwen/qwen3-vl-8b"
if not defined LOCAL_LLM_FALLBACK_MODEL set "LOCAL_LLM_FALLBACK_MODEL=qwen3vl4b-ocr"
if not defined LOCAL_LLM_FALLBACK_MODEL_KEY set "LOCAL_LLM_FALLBACK_MODEL_KEY=qwen/qwen3-vl-4b"
if not defined LOCAL_LLM_CONTEXT_LENGTH set "LOCAL_LLM_CONTEXT_LENGTH=16384"
if not defined LOCAL_LLM_GPU set "LOCAL_LLM_GPU=max"
if not defined LOCAL_LLM_PARALLEL set "LOCAL_LLM_PARALLEL=1"

echo ==========================================
echo    Local LLM 啟動器 - LM Studio CLI
echo ==========================================
echo API:   %LOCAL_LLM_API_BASE%
echo Model: %LOCAL_LLM_MODEL%  ^(fallback: %LOCAL_LLM_FALLBACK_MODEL%^)
echo Context: %LOCAL_LLM_CONTEXT_LENGTH%
echo Parallel: %LOCAL_LLM_PARALLEL%
echo.

python tools\local_llm_manager.py ensure
if "%errorlevel%" NEQ "0" (
    echo.
    echo [錯誤] 本機 LLM 啟動失敗，請確認已安裝 LM Studio / lms，且模型已下載。
    pause
    exit /b 1
)

echo.
echo [OK] 本機 LLM 已就緒。
pause

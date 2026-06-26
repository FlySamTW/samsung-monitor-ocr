@echo off
setlocal
set PYTHONIOENCODING=utf-8
D:
cd "D:\00_程式\20260120_商化自動OCR圖片_HITL實驗"
echo ==========================================
echo   Samsung OCR Launcher
echo ==========================================
echo.
echo   [1] Local Qwen3-VL 8B
echo   [2] OpenCode Go: qwen3.7-plus
echo.
choice /c 12 /n /m "Choose (1-2): "
set "M=%ERRORLEVEL%"
set "AB=http://127.0.0.1:1234/v1"
set "AK=lm-studio"
set "MN=qwen3vl8b-ocr"
set "LM=1"
if "%M%"=="1" set "AB=http://127.0.0.1:1234/v1"
if "%M%"=="1" set "MN=qwen3vl8b-ocr"
if "%M%"=="2" set "AB=https://opencode.ai/zen/go/v1"
if "%M%"=="2" set "MN=qwen3.7-plus"
if "%M%"=="2" set "LM=0"
if "%LM%"=="0" set "AK=sk-q7q8oOd7BNUNAMOJetlWC9EKklaZl6qCnjwSvetclnnWea876r8ylLHmodzi5JXb"
echo API: %AB%
echo Model: %MN%
taskkill /F /IM python.exe /T >nul 2>&1
start http://localhost:5000
python samsung_ocr_batch_processor.py --api_base "%AB%" --api_key "%AK%" --model "%MN%" --dir "商化照片-202602"
pause

@echo off
chcp 65001
echo 🔄 快速重啟三星OCR儀表板...

cd /d "%~dp0"

rem 強制關閉所有相關進程
taskkill /f /im python.exe 2>nul
timeout /t 1 /nobreak >nul

rem 重啟服務
start "OCR" /min python samsung_ocr_batch_processor.py --dir photos --limit 10 --api_base http://localhost:1234/v1 --model glm-4.6v-flash
timeout /t 2 /nobreak >nul

start "HTTP" /min python -m http.server 8080
timeout /t 2 /nobreak >nul

rem 開啟儀表板
start "" "http://localhost:8080/integrated_dashboard.html"

echo ✅ 重啟完成
exit
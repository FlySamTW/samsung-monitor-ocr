@echo off
chcp 65001
echo 🛑 停止三星OCR相關服務...

rem 關閉所有Python進程（包括OCR處理器和HTTP伺服器）
taskkill /f /im python.exe 2>nul
taskkill /f /im pythonw.exe 2>nul

rem 特定端口的進程關閉
for /f "tokens=5" %%a in ('netstat -aon ^| find ":5000"') do taskkill /f /pid %%a 2>nul
for /f "tokens=5" %%a in ('netstat -aon ^| find ":8080"') do taskkill /f /pid %%a 2>nul

echo ✅ 所有服務已停止
timeout /t 2 /nobreak >nul
exit
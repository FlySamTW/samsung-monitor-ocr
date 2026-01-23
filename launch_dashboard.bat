@echo off
echo ===============================================
echo 三星OCR整合儀表板啟動器  
echo ===============================================
echo.

cd /d "%~dp0"

echo [1/5] 關閉舊進程...
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process pythonw -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2

echo [2/5] 檢查LM Studio API狀態...
try { Invoke-WebRequest -Uri "http://localhost:1234/v1/models" -UseBasicParsing -TimeoutSec 3 | Out-Null } catch { echo "⚠️  注意: LM Studio API (端口1234) 無法連接"; echo "   如需完整功能，請先啟動LM Studio並載入模型"; echo "" }

echo [3/5] 啟動OCR批次處理器 (端口5000)...
Start-Process -WindowStyle Minimized powershell -ArgumentList "-Command", "cd '$PWD'; python samsung_ocr_batch_processor.py --dir photos --limit 10 --api_base http://localhost:1234/v1 --model glm-4.6v-flash"
Start-Sleep -Seconds 3

echo [4/5] 啟動HTTP檔案伺服器 (端口8080)...
Start-Process -WindowStyle Minimized powershell -ArgumentList "-Command", "cd '$PWD'; python -m http.server 8080" 
Start-Sleep -Seconds 2

echo [5/5] 開啟整合儀表板...
Start-Sleep -Seconds 3
Start-Process "http://localhost:8080/integrated_dashboard.html"

echo.
echo ✅ 啟動完成！
echo.
echo 📋 服務狀態:
echo   • OCR處理器: http://localhost:5000
echo   • 檔案伺服器: http://localhost:8080  
echo   • 整合儀表板: http://localhost:8080/integrated_dashboard.html
echo.
echo 💡 提示:
echo   • 如看到API錯誤，請確認LM Studio已啟動
echo   • 可透過工作管理員或此視窗關閉所有服務
echo   • 按任意鍵關閉此視窗
echo.
pause
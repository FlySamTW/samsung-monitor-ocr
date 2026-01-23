# 三星OCR整合儀表板啟動器
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "三星OCR整合儀表板啟動器" -ForegroundColor Cyan  
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host ""

# 切換到腳本目錄
Set-Location $PSScriptRoot

Write-Host "[1/5] 關閉舊進程..." -ForegroundColor Yellow
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process pythonw -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2

Write-Host "[2/5] 檢查LM Studio API狀態..." -ForegroundColor Yellow
try {
    Invoke-WebRequest -Uri "http://localhost:1234/v1/models" -UseBasicParsing -TimeoutSec 3 | Out-Null
    Write-Host "    ✅ LM Studio API 可用" -ForegroundColor Green
} catch {
    Write-Host "    ⚠️  LM Studio API (端口1234) 無法連接" -ForegroundColor Yellow
    Write-Host "    如需完整功能，請先啟動LM Studio並載入模型" -ForegroundColor Yellow
}

Write-Host "[3/5] 啟動OCR批次處理器 (端口5000)..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-WindowStyle", "Minimized", "-Command", "cd '$PWD'; python samsung_ocr_batch_processor.py --dir photos --limit 10 --api_base http://172.24.32.1:1234/v1 --model zai-org/glm-4.6v-flash"
Start-Sleep -Seconds 3

Write-Host "[4/5] 啟動HTTP檔案伺服器 (端口8080)..." -ForegroundColor Yellow  
Start-Process powershell -ArgumentList "-WindowStyle", "Minimized", "-Command", "cd '$PWD'; python -m http.server 8080"
Start-Sleep -Seconds 2

Write-Host "[5/5] 開啟整合儀表板..." -ForegroundColor Yellow
Start-Sleep -Seconds 3
Start-Process "http://localhost:8080/integrated_dashboard.html"

Write-Host ""
Write-Host "✅ 啟動完成！" -ForegroundColor Green
Write-Host ""
Write-Host "📋 服務狀態:" -ForegroundColor Cyan
Write-Host "  • OCR處理器: http://localhost:5000" -ForegroundColor White
Write-Host "  • 檔案伺服器: http://localhost:8080" -ForegroundColor White
Write-Host "  • 整合儀表板: http://localhost:8080/integrated_dashboard.html" -ForegroundColor White
Write-Host ""
Write-Host "💡 提示:" -ForegroundColor Cyan
Write-Host "  • 如看到API錯誤，請確認LM Studio已啟動" -ForegroundColor Gray
Write-Host "  • 可透過工作管理員關閉所有Python進程" -ForegroundColor Gray
Write-Host "  • 按任意鍵關閉此視窗" -ForegroundColor Gray
Write-Host ""
Read-Host "按 Enter 鍵關閉此視窗"
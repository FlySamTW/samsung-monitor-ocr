#!/usr/bin/env pwsh
# 三星 OCR 系統啟動腳本

Write-Host "🚀 三星 OCR 系統啟動腳本" -ForegroundColor Green
Write-Host "="*50 -ForegroundColor Blue

# 啟動 OCR 系統
Write-Host "📱 啟動 OCR 處理系統..." -ForegroundColor Yellow
Start-Process -FilePath "python" -ArgumentList "samsung_ocr_batch_processor.py", "--api_base", "http://172.24.32.1:1234/v1", "--model", "zai-org/glm-4.6v-flash" -WindowStyle Normal

# 等待系統啟動
Write-Host "⏳ 等待系統啟動..." -ForegroundColor Yellow
Start-Sleep -Seconds 3

# 打開瀏覽器
Write-Host "🌐 打開優化界面..." -ForegroundColor Yellow
Start-Process "http://localhost:5000/dashboard/optimized"

Write-Host ""
Write-Host "✅ 系統已啟動完成！" -ForegroundColor Green
Write-Host "📊 優化界面: http://localhost:5000/dashboard/optimized" -ForegroundColor Cyan
Write-Host "📋 系統狀態: http://localhost:5000/api/status" -ForegroundColor Cyan
Write-Host ""
Write-Host "💡 使用說明:" -ForegroundColor Yellow
Write-Host "  1. 點擊 '開始批次處理' 開始 OCR 識別" -ForegroundColor White
Write-Host "  2. 左側檢視照片，右側檢視 LLM 思考過程" -ForegroundColor White
Write-Host "  3. 使用 Ctrl+C 停止系統" -ForegroundColor White
Write-Host ""
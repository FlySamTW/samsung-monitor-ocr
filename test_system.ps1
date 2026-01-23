#!/usr/bin/env pwsh
# 系統功能測試腳本

Write-Host "🧪 三星 OCR 系統功能測試" -ForegroundColor Green
Write-Host "="*50 -ForegroundColor Blue

# 等待服務完全啟動
Write-Host "⏳ 等待系統完全啟動..." -ForegroundColor Yellow
Start-Sleep -Seconds 5

# 測試 API 端點
$baseUrl = "http://localhost:5000"

Write-Host "📊 測試系統狀態..." -ForegroundColor Yellow
try {
    $response = Invoke-RestMethod -Uri "$baseUrl/api/status" -Method GET -TimeoutSec 10
    Write-Host "✅ 系統狀態 API 正常" -ForegroundColor Green
    Write-Host "   - 版本: $($response.version)" -ForegroundColor Gray
    Write-Host "   - 運行狀態: $($response.is_running)" -ForegroundColor Gray
} catch {
    Write-Host "❌ 系統狀態 API 失敗: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host "🎨 測試優化界面..." -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "$baseUrl/dashboard/optimized" -Method GET -UseBasicParsing -TimeoutSec 10
    if ($response.StatusCode -eq 200) {
        Write-Host "✅ 優化界面正常" -ForegroundColor Green
    }
} catch {
    Write-Host "❌ 優化界面失敗: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host "📝 測試日誌 API..." -ForegroundColor Yellow
try {
    $response = Invoke-RestMethod -Uri "$baseUrl/api/logs" -Method GET -TimeoutSec 10
    Write-Host "✅ 日誌 API 正常" -ForegroundColor Green
    Write-Host "   - 日誌總數: $($response.total)" -ForegroundColor Gray
} catch {
    Write-Host "❌ 日誌 API 失敗: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host "🖼️ 測試圖片 API..." -ForegroundColor Yellow
try {
    $photoDir = "photos"
    if (Test-Path $photoDir) {
        $photos = Get-ChildItem $photoDir -Filter "*.jpg" | Select-Object -First 1
        if ($photos) {
            $testPhoto = $photos[0].Name
            $response = Invoke-WebRequest -Uri "$baseUrl/api/image/$testPhoto" -Method GET -UseBasicParsing -TimeoutSec 10
            if ($response.StatusCode -eq 200) {
                Write-Host "✅ 圖片 API 正常 (測試: $testPhoto)" -ForegroundColor Green
            }
        } else {
            Write-Host "⚠️  photos目錄中沒有找到jpg圖片" -ForegroundColor Yellow
        }
    } else {
        Write-Host "⚠️  photos目錄不存在" -ForegroundColor Yellow
    }
} catch {
    Write-Host "❌ 圖片 API 失敗: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host ""
Write-Host "✅ 系統測試完成！" -ForegroundColor Green
Write-Host "🌐 優化界面：$baseUrl/dashboard/optimized" -ForegroundColor Cyan
Write-Host "📊 系統狀態：$baseUrl/api/status" -ForegroundColor Cyan
Write-Host "📝 系統日誌：$baseUrl/api/logs" -ForegroundColor Cyan

Write-Host ""
Write-Host "💡 下一步：" -ForegroundColor Yellow
Write-Host "  - 打開優化界面開始使用" -ForegroundColor White
Write-Host "  - 點擊「開始批次處理」開始OCR分析" -ForegroundColor White
Write-Host "  - 觀看左側照片和右側LLM思考過程" -ForegroundColor White
Write-Host ""
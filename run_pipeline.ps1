# 三星 OCR 自動化批次處理腳本
# 依序執行：
# 1. 訓練資料集驗證 (確保準確度)
# 2. 正式資料集辨識 (隔日驗收成果)

$ScriptDir = "d:\00_程式\20260120_商化自動OCR圖片"
$PythonScript = "$ScriptDir\samsung_ocr_batch_processor.py"
$TrainingData = "$ScriptDir\商化照片-202512-訓練數據"
$MainData = "$ScriptDir\商化照片-202512"
$JsonFewShot = "project-1-at-2026-01-20-09-01-f1ed471e.json"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "🚀 開始 Samsung OCR 自動化批次任務" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# 1. 驗證訓練集
Write-Host "`n[1/2] 正在執行訓練資料集驗證..." -ForegroundColor Yellow
$ValidationOutput = "$ScriptDir\validation_results_full.json"
python $PythonScript --images $TrainingData --output $ValidationOutput --few_shot $JsonFewShot --max_size 99999

if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ 訓練集驗證完成！結果已儲存至: $ValidationOutput" -ForegroundColor Green
}
else {
    Write-Host "❌ 訓練集驗證失敗，請檢查日誌。" -ForegroundColor Red
    exit
}

# 2. 執行正式資料集
Write-Host "`n[2/2] 正在執行正式資料集 (Batch Processing)..." -ForegroundColor Yellow
$FinalOutput = "$ScriptDir\final_results_202512.json"
python $PythonScript --images $MainData --output $FinalOutput --few_shot $JsonFewShot --max_size 99999

if ($LASTEXITCODE -eq 0) {
    Write-Host "🎉 全部任務完成！正式結果已儲存至: $FinalOutput" -ForegroundColor Green
}
else {
    Write-Host "❌ 正式資料集處理發生錯誤。" -ForegroundColor Red
}

Write-Host "`n任務結束時間: $(Get-Date)" -ForegroundColor Gray

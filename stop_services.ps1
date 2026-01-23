# 停止三星OCR相關服務
Write-Host "🛑 停止三星OCR相關服務..." -ForegroundColor Red

# 關閉所有Python進程
Write-Host "關閉Python進程..." -ForegroundColor Yellow
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process pythonw -ErrorAction SilentlyContinue | Stop-Process -Force

# 關閉特定端口的進程
Write-Host "關閉端口5000和8080的進程..." -ForegroundColor Yellow
$port5000 = Get-NetTCPConnection -LocalPort 5000 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess
$port8080 = Get-NetTCPConnection -LocalPort 8080 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess

if ($port5000) { Stop-Process -Id $port5000 -Force -ErrorAction SilentlyContinue }
if ($port8080) { Stop-Process -Id $port8080 -Force -ErrorAction SilentlyContinue }

Write-Host "✅ 所有服務已停止" -ForegroundColor Green
Start-Sleep -Seconds 2
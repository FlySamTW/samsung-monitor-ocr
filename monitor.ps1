while ($true) {
    try {
        $status = Invoke-RestMethod -Uri "http://localhost:5000/api/status" -ErrorAction Stop
        Clear-Host
        Write-Host "=== OCR System Monitor (Port 5000) ===" -ForegroundColor Cyan
        Write-Host "Time: $(Get-Date)"
        Write-Host "Total:     $($status.stats.total)"
        Write-Host "Processed: $($status.stats.processed)"
        Write-Host "Success:   $($status.stats.success)" -ForegroundColor Green
        Write-Host "Failed:    $($status.stats.failed)" -ForegroundColor Red
        Write-Host "Current:   $($status.current_file)"
        
        if ($status.stats.failed -gt 0) {
            Write-Host "WARNING: Failures detected!" -ForegroundColor Red
        }

        Write-Host "`n[Latest Logs]" -ForegroundColor Yellow
        $status.lm_logs | Select-Object -Last 8 | ForEach-Object { 
            if ($_ -match "失敗" -or $_ -match "Error") {
                Write-Host $_ -ForegroundColor Red
            }
            elseif ($_ -match "成功") {
                Write-Host $_ -ForegroundColor Green
            }
            else {
                Write-Host $_
            }
        }
    }
    catch {
        Write-Host "Waiting for API (System starting or offline)..." -ForegroundColor DarkGray
    }
    Start-Sleep -Seconds 3
}

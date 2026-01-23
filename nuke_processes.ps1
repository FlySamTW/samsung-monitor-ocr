Write-Host "Killing processes..."
Get-Process | Where-Object { $_.Name -eq "python" -or $_.Name -eq "node" } | Stop-Process -Force -ErrorAction SilentlyContinue
Write-Host "Checking Port 5000..."
$conns = Get-NetTCPConnection -LocalPort 5000 -ErrorAction SilentlyContinue
if ($conns) {
    foreach ($c in $conns) {
        $p = $c.OwningProcess
        Write-Host "Killing PID $p on Port 5000"
        Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
    }
}
Write-Host "Done."

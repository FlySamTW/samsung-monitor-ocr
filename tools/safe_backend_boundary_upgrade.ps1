param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$OutputDir = "D:\00_商化\00_已OCR照片",
    [string]$BackendUrl = "http://127.0.0.1:5000",
    [string]$ExpectedFolder = "",
    [int]$PollSeconds = 30,
    [int]$WaitTimeoutSeconds = 21600,
    [int]$VerifyTimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path $RepoRoot).Path
$OutputDir = (Resolve-Path $OutputDir).Path
$auditDir = Join-Path $OutputDir "_ocr_audit"
$lockPath = Join-Path $auditDir "model_benchmark.lock"
$logDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $auditDir,$logDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logPath = Join-Path $logDir "safe_backend_boundary_upgrade_$stamp.jsonl"
$script:lockOwned = $false

function Log([string]$Event, [hashtable]$Data = @{}) {
    $row = [ordered]@{ timestamp=(Get-Date).ToString("o"); event=$Event; repo_root=$RepoRoot }
    foreach ($key in $Data.Keys) { $row[$key] = $Data[$key] }
    ($row | ConvertTo-Json -Compress -Depth 8) | Add-Content -LiteralPath $logPath -Encoding UTF8
}
function Get-Status {
    try { return Invoke-RestMethod -Uri "$BackendUrl/api/status" -TimeoutSec 8 } catch { return $null }
}
function Owned([string]$Pattern) {
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and $_.CommandLine -match $Pattern -and $_.CommandLine -match [regex]::Escape($RepoRoot) -and $_.CommandLine -notmatch "safe_backend_boundary_upgrade"
    })
}
function Acquire-Lock {
    try {
        $null = New-Item -ItemType File -Path $lockPath -ErrorAction Stop
        $payload = [ordered]@{ purpose="backend_upgrade_v1945"; pid=$PID; started_at=(Get-Date).ToString("o"); repo_root=$RepoRoot; expected_folder=$ExpectedFolder }
        $payload | ConvertTo-Json -Compress | Set-Content -LiteralPath $lockPath -Encoding UTF8
        $script:lockOwned = $true
        Log "lock_acquired" @{ lock=$lockPath; purpose="backend_upgrade_v1945" }
    } catch {
        Log "lock_busy_or_failed" @{ lock=$lockPath; error=$_.Exception.Message }
        exit 2
    }
}
function Test-QuietBoundary($status) {
    if (-not $status) { return $false }
    $stats = $status.stats
    if ([int]$stats.processed -ne [int]$stats.total) { return $false }
    # Backend can advance immediately after the final item; the expected
    # expected folder complete snapshot is the handoff boundary.
    if ($ExpectedFolder -and [string]$status.current_relative_dir -notlike "*$ExpectedFolder*") { return $false }
    $staged = @(Owned "rerun_staged_candidates\.py|recursive_ocr_flat_export\.py|rerun_questionable_records\.py")
    $uploader = @(Owned "rclone_drive_upload\.py|rclone\.exe")
    if ($staged.Count -gt 0 -or $uploader.Count -gt 0) { return $false }
    # Require the audit/output area to exist; detailed folder proof is delegated
    # to the existing summaries and the idle processed==total gate above.
    return (Test-Path $auditDir) -and (Test-Path $OutputDir)
}
function Stop-BackendGracefully {
    $procs = @(Owned "samsung_ocr_batch_processor\.py")
    if ($procs.Count -ne 1) { throw "expected exactly one owned backend, found $($procs.Count)" }
    $pid = [int]$procs[0].ProcessId
    Log "backend_stop_requested" @{ pid=$pid }
    Stop-Process -Id $pid -ErrorAction Stop
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-Process -Id $pid -ErrorAction SilentlyContinue)) { Log "backend_stopped" @{ pid=$pid }; return }
        Start-Sleep -Seconds 1
    }
    throw "backend did not exit gracefully; pid=$pid retained for manual recovery"
}
function Start-And-Verify {
    $launcher = Join-Path $RepoRoot "tools\windows_user_launcher.ps1"
    $out = Join-Path $logDir "boundary_launcher_$stamp.out.log"
    $err = Join-Path $logDir "boundary_launcher_$stamp.err.log"
    Start-Process powershell.exe -WindowStyle Hidden -WorkingDirectory $RepoRoot -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-File",$launcher,"start") -RedirectStandardOutput $out -RedirectStandardError $err | Out-Null
    $deadline=(Get-Date).AddSeconds($VerifyTimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $s=Get-Status
        if ($s -and -not [bool]$s.is_running -and [string]$s.version -like "v19.45*" -and [string]$s.accuracy_profile -eq "strict" -and $s.frontend_asset_fingerprint -and $null -ne $s.presentation_queue) {
            Log "upgrade_verified" @{ version=$s.version; fingerprint=$s.frontend_asset_fingerprint; queue_count=@($s.presentation_queue).Count }
            return
        }
        Start-Sleep -Seconds 2
    }
    throw "new backend verification failed; lock retained"
}

try {
    Acquire-Lock
    $started=(Get-Date)
    while (((Get-Date)-$started).TotalSeconds -lt $WaitTimeoutSeconds) {
        $s=Get-Status
        if (Test-QuietBoundary $s) { Log "boundary_proven" @{ folder=$s.current_relative_dir; processed=$s.stats.processed; total=$s.stats.total }; break }
        Log "waiting_for_boundary" @{ running=if($s){[bool]$s.is_running}else{$null}; processed=if($s){$s.stats.processed}else{$null}; total=if($s){$s.stats.total}else{$null}; folder=if($s){$s.current_relative_dir}else{$null} }
        Start-Sleep -Seconds ([math]::Max(5,$PollSeconds))
    }
    $s=Get-Status
    if (-not (Test-QuietBoundary $s)) { throw "boundary proof timeout or incomplete staging" }
    Stop-BackendGracefully
    Start-And-Verify
    Remove-Item -LiteralPath $lockPath -Force
    $script:lockOwned=$false
    Log "lock_released" @{ reason="upgrade_verified" }
} catch {
    Log "upgrade_failed_lock_retained" @{ error=$_.Exception.Message; lock=$lockPath }
    exit 1
}

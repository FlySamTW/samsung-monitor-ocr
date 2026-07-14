param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$SourceRoot = "",
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
function Get-StatusPayloadBytes {
    try {
        $json = Invoke-RestMethod -Uri "$BackendUrl/api/status" -TimeoutSec 8 | ConvertTo-Json -Compress -Depth 15
        return [Text.Encoding]::UTF8.GetByteCount($json)
    } catch { return [int64]::MaxValue }
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
    if ([bool]$status.is_running) { return $false }
    $stats = $status.stats
    if ([int]$stats.processed -ne [int]$stats.total) { return $false }
    # Backend can advance immediately after the final item; the expected
    # expected folder complete snapshot is the handoff boundary.
    if ($ExpectedFolder -and [string]$status.current_relative_dir -notlike "*$ExpectedFolder*") { return $false }
    $staged = @(Owned "rerun_staged_candidates\.py|recursive_ocr_flat_export\.py|rerun_questionable_records\.py|auto_rerun_questionable_after_recursive\.ps1")
    $uploader = @(Owned "rclone_drive_upload\.py|rclone\.exe")
    if ($staged.Count -gt 0 -or $uploader.Count -gt 0) { return $false }
    # Require the audit/output area to exist; detailed folder proof is delegated
    # to the existing summaries and the idle processed==total gate above.
    return (Test-Path $auditDir) -and (Test-Path $OutputDir)
}
function Get-BackendProcessTree {
    $all = @(Get-CimInstance Win32_Process)
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 5000 -ErrorAction Stop)
    if ($listeners.Count -ne 1) { throw "expected exactly one port 5000 listener, found $($listeners.Count)" }
    $listenerId = [int]$listeners[0].OwningProcess
    $byId = @{}
    foreach ($proc in $all) { $byId[[int]$proc.ProcessId] = $proc }
    if (-not $byId.ContainsKey($listenerId)) { throw "port 5000 listener process was not found" }
    if ([string]$byId[$listenerId].CommandLine -notmatch "samsung_ocr_batch_processor\.py") {
        throw "port 5000 is not owned by the Samsung OCR backend"
    }

    $tree = @()
    $current = $byId[$listenerId]
    $repoProven = $false
    while ($current) {
        if ([string]$current.CommandLine -match "samsung_ocr_batch_processor\.py") { $tree += $current }
        if ([string]$current.CommandLine -match [regex]::Escape($RepoRoot)) { $repoProven = $true }
        $parentId = [int]$current.ParentProcessId
        if ($parentId -le 0 -or -not $byId.ContainsKey($parentId)) { break }
        $current = $byId[$parentId]
        if ([string]$current.CommandLine -notmatch "samsung_ocr_batch_processor\.py" -and $repoProven) { break }
    }
    if (-not $repoProven) { throw "backend listener ancestry is not owned by repo $RepoRoot" }
    return @($tree | Sort-Object ProcessId -Unique)
}
function Stop-BackendGracefully {
    $procs = @(Get-BackendProcessTree)
    $listenerId = [int](Get-NetTCPConnection -State Listen -LocalPort 5000 -ErrorAction Stop | Select-Object -First 1 -ExpandProperty OwningProcess)
    $orderedIds = @($listenerId) + @($procs | ForEach-Object { [int]$_.ProcessId } | Where-Object { $_ -ne $listenerId })
    Log "backend_stop_requested" @{ listener_pid=$listenerId; process_ids=$orderedIds }
    foreach ($processId in $orderedIds) {
        Stop-Process -Id $processId -ErrorAction SilentlyContinue
    }
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        $remaining = @($orderedIds | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
        if ($remaining.Count -eq 0 -and -not (Get-NetTCPConnection -State Listen -LocalPort 5000 -ErrorAction SilentlyContinue)) {
            Log "backend_stopped" @{ process_ids=$orderedIds }
            return
        }
        Start-Sleep -Seconds 1
    }
    throw "backend process tree did not exit cleanly; manual recovery required"
}
function Invoke-LegacyTraceMigration {
    $python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    $migrationTool = Join-Path $RepoRoot "tools\migrate_legacy_v1945_trace.py"
    $sourceTrace = Join-Path $RepoRoot "v1945_evidence_trace.jsonl"
    $destinationTrace = Join-Path $auditDir "v1945_evidence_trace.jsonl"
    $currentCandidate = Get-ChildItem -LiteralPath $auditDir -Filter "questionable_rerun_candidates_current_year_first_pass_*.csv" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    $recoveryCandidate = Join-Path $auditDir "questionable_rerun_candidates_202603_v1945_recovery_20260714.csv"

    if (-not (Test-Path -LiteralPath $python)) { throw "trace migration Python runtime missing: $python" }
    if (-not (Test-Path -LiteralPath $migrationTool)) { throw "trace migration tool missing: $migrationTool" }
    if (-not (Test-Path -LiteralPath $sourceTrace)) { throw "legacy trace missing: $sourceTrace" }
    if (-not $currentCandidate) { throw "current-year first-pass candidate CSV missing" }
    if (-not (Test-Path -LiteralPath $recoveryCandidate)) { throw "202603 recovery candidate CSV missing: $recoveryCandidate" }

    Log "legacy_trace_migration_started" @{
        source=$sourceTrace
        destination=$destinationTrace
        current_candidate=$currentCandidate.FullName
        recovery_candidate=$recoveryCandidate
    }
    $output = & $python $migrationTool `
        --source $sourceTrace `
        --destination $destinationTrace `
        --candidate-csv $currentCandidate.FullName `
        --candidate-csv $recoveryCandidate `
        --execute
    $exitCode = $LASTEXITCODE
    $outputText = $output -join [Environment]::NewLine
    if ($exitCode -ne 0) { throw "legacy trace migration failed closed (exit $exitCode): $outputText" }
    try { $summary = $outputText | ConvertFrom-Json } catch { throw "legacy trace migration returned invalid JSON: $outputText" }
    if (-not [bool]$summary.executed -or [int]$summary.unresolved_rows -ne 0 -or -not (Test-Path -LiteralPath $destinationTrace)) {
        throw "legacy trace migration did not produce a complete durable trace: $outputText"
    }
    Log "legacy_trace_migration_verified" @{
        source_rows=[int]$summary.source_rows
        destination_rows=[int]$summary.destination_rows_after
        unresolved_rows=[int]$summary.unresolved_rows
        destination=$destinationTrace
    }
}
function Start-And-Verify {
    $launcher = Join-Path $RepoRoot "tools\windows_user_launcher.ps1"
    $out = Join-Path $logDir "boundary_launcher_$stamp.out.log"
    $err = Join-Path $logDir "boundary_launcher_$stamp.err.log"
    Start-Process powershell.exe -WindowStyle Hidden -WorkingDirectory $RepoRoot -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-File",$launcher,"start") -RedirectStandardOutput $out -RedirectStandardError $err | Out-Null
    $deadline=(Get-Date).AddSeconds($VerifyTimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $s=Get-Status
        $payloadBytes = Get-StatusPayloadBytes
        if ($s -and -not [bool]$s.is_running -and [string]$s.version -like "v19.45*" -and [string]$s.status_contract_version -eq "compact-v2" -and [string]$s.accuracy_profile -eq "strict" -and $s.frontend_asset_fingerprint -and $null -ne $s.presentation_queue -and @($s.presentation_queue).Count -le 24 -and $payloadBytes -lt 500000) {
            $historyProbe = Invoke-RestMethod -Uri "$BackendUrl/api/presentation_history/$('0' * 64)?limit=1" -TimeoutSec 8
            if ($null -eq $historyProbe.items) { throw "presentation history API contract missing" }
            Log "upgrade_verified" @{ version=$s.version; status_contract=$s.status_contract_version; fingerprint=$s.frontend_asset_fingerprint; queue_count=@($s.presentation_queue).Count; payload_bytes=$payloadBytes }
            return
        }
        Start-Sleep -Seconds 2
    }
    throw "new backend verification failed; lock retained"
}
function Start-EvidenceBackfill {
    $python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    $builder = Join-Path $RepoRoot "tools\build_v1945_evidence_backfill.py"
    $runner = Join-Path $RepoRoot "tools\rerun_staged_candidates.py"
    $candidateCsv = Join-Path $auditDir "v1945_evidence_backfill_2026.csv"
    $resultCsv = Join-Path $auditDir "v1945_evidence_backfill_2026_results.csv"
    $summaryCsv = Join-Path $auditDir "v1945_evidence_backfill_2026_run_summary.csv"
    if (-not (Test-Path -LiteralPath $SourceRoot)) { throw "evidence backfill source root missing: $SourceRoot" }
    foreach ($required in @($python,$builder,$runner)) {
        if (-not (Test-Path -LiteralPath $required)) { throw "evidence backfill dependency missing: $required" }
    }

    $output = & $python $builder `
        --audit-dir $auditDir `
        --year 2026 `
        --output $candidateCsv `
        --execute
    $exitCode = $LASTEXITCODE
    $outputText = $output -join [Environment]::NewLine
    if ($exitCode -ne 0) { throw "evidence backfill candidate build failed closed (exit $exitCode): $outputText" }
    try { $summary = $outputText | ConvertFrom-Json } catch { throw "evidence backfill builder returned invalid JSON: $outputText" }
    if (-not [bool]$summary.executed) { throw "evidence backfill candidate CSV was not written: $outputText" }
    $candidateCount = [int]$summary.candidate_rows
    Log "evidence_backfill_candidates_verified" @{
        candidates=$candidateCount
        sources=[int]$summary.unique_year_sources
        already_verified=[int]$summary.already_verified_year_sources
        candidate_csv=$candidateCsv
    }
    if ($candidateCount -eq 0) {
        Log "evidence_backfill_not_required" @{ reason="all_current_year_sources_verified" }
        return
    }

    $stdout = Join-Path $logDir "v1945_evidence_backfill_$stamp.out.log"
    $stderr = Join-Path $logDir "v1945_evidence_backfill_$stamp.err.log"
    $args = @(
        $runner,
        "--source-root",$SourceRoot,
        "--output-dir",$OutputDir,
        "--backend-url",$BackendUrl,
        "--input-csv",$candidateCsv,
        "--output-csv",$resultCsv,
        "--run-summary-csv",$summaryCsv,
        "--execute",
        "--poll-seconds","10",
        "--timeout-minutes","360"
    )
    $process = Start-Process -FilePath $python -WorkingDirectory $RepoRoot -WindowStyle Hidden `
        -ArgumentList $args -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        if ($process.HasExited) { throw "evidence backfill runner exited early with code $($process.ExitCode); inspect $stderr" }
        $status = Get-Status
        $ownedRunner = @(Owned "rerun_staged_candidates\.py")
        if (($status -and [bool]$status.is_running) -or $ownedRunner.Count -gt 0) {
            Log "evidence_backfill_started" @{ pid=$process.Id; candidates=$candidateCount; stdout=$stdout; stderr=$stderr }
            return
        }
        Start-Sleep -Seconds 2
    }
    throw "evidence backfill runner did not start within 60 seconds"
}

try {
    Acquire-Lock
    $started=(Get-Date)
    $quietCount=0
    while (((Get-Date)-$started).TotalSeconds -lt $WaitTimeoutSeconds) {
        $s=Get-Status
        if (Test-QuietBoundary $s) {
            $quietCount += 1
            Log "boundary_observed" @{ observation=$quietCount; folder=$s.current_relative_dir; processed=$s.stats.processed; total=$s.stats.total }
            if ($quietCount -ge 2) { Log "boundary_proven" @{ folder=$s.current_relative_dir; processed=$s.stats.processed; total=$s.stats.total }; break }
        } else {
            $quietCount=0
        }
        Log "waiting_for_boundary" @{ running=if($s){[bool]$s.is_running}else{$null}; processed=if($s){$s.stats.processed}else{$null}; total=if($s){$s.stats.total}else{$null}; folder=if($s){$s.current_relative_dir}else{$null} }
        Start-Sleep -Seconds ([math]::Max(5,$PollSeconds))
    }
    $s=Get-Status
    if (-not (Test-QuietBoundary $s)) { throw "boundary proof timeout or incomplete staging" }
    Invoke-LegacyTraceMigration
    Stop-BackendGracefully
    Start-And-Verify
    Start-EvidenceBackfill
    Remove-Item -LiteralPath $lockPath -Force
    $script:lockOwned=$false
    Log "lock_released" @{ reason="upgrade_verified_and_evidence_backfill_started" }
} catch {
    Log "upgrade_failed_lock_retained" @{ error=$_.Exception.Message; lock=$lockPath }
    exit 1
}

param(
    [Parameter(Mandatory=$true)][string]$RepoRoot,
    [Parameter(Mandatory=$true)][string]$SourceRoot,
    [Parameter(Mandatory=$true)][string]$OutputDir,
    [string]$BackendUrl = "http://127.0.0.1:5002",
    [string]$ApiBase = "http://127.0.0.1:1234/v1",
    [string]$Model = "qwen/qwen3-vl-8b",
    [int]$VerifyTimeoutSeconds = 60,
    [switch]$AllowIncompleteStoppedBatch
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$SourceRoot = (Resolve-Path -LiteralPath $SourceRoot).Path
$OutputDir = (Resolve-Path -LiteralPath $OutputDir).Path
$audit = Join-Path $OutputDir "_ocr_audit"
$logDir = Join-Path $RepoRoot "logs"
$lockPath = Join-Path $audit "backend_safe_idle_reload.lock"
$logPath = Join-Path $logDir ("backend_safe_idle_reload_{0}.jsonl" -f (Get-Date -Format "yyyyMMdd"))
$python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$backendScript = Join-Path $RepoRoot "samsung_ocr_batch_processor.py"
$runtimeHealthFuse = Join-Path $audit "runtime_health_fuse.json"
$benchmarkLock = Join-Path $audit "model_benchmark.lock"
try { $backendPort = ([uri]$BackendUrl).Port } catch { throw "invalid BackendUrl: $BackendUrl" }
if ($backendPort -le 0) { throw "BackendUrl must include a valid port" }
New-Item -ItemType Directory -Force -Path $audit,$logDir | Out-Null

function Log-Reload([string]$Event, [hashtable]$Data = @{}) {
    $row = [ordered]@{timestamp=(Get-Date).ToString("o");event=$Event;pid=$PID}
    foreach ($key in $Data.Keys) { $row[$key] = $Data[$key] }
    $row | ConvertTo-Json -Compress -Depth 6 | Add-Content -LiteralPath $logPath -Encoding UTF8
}
function Get-Status {
    try { return Invoke-RestMethod -Uri "$BackendUrl/api/status" -TimeoutSec 8 } catch { return $null }
}
function Get-Owned([string]$Pattern) {
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match $Pattern -and
        $_.CommandLine -match [regex]::Escape($RepoRoot) -and
        $_.CommandLine -notmatch "Get-CimInstance|reload_backend_at_safe_idle"
    })
}
function Get-BackendProcessTree {
    $all = @(Get-CimInstance Win32_Process)
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $backendPort -ErrorAction Stop)
    if ($listeners.Count -ne 1) { throw "expected one backend listener, found $($listeners.Count)" }
    $listenerPid = [int]$listeners[0].OwningProcess
    $byId = @{}
    foreach ($proc in $all) { $byId[[int]$proc.ProcessId] = $proc }
    if (-not $byId.ContainsKey($listenerPid)) { throw "backend listener process missing" }
    if ([string]$byId[$listenerPid].CommandLine -notmatch "samsung_ocr_batch_processor\.py") {
        throw "port is not owned by the OCR backend"
    }
    $tree = @()
    $current = $byId[$listenerPid]
    while ($current -and [string]$current.CommandLine -match "samsung_ocr_batch_processor\.py") {
        if ([string]$current.CommandLine -notmatch [regex]::Escape($RepoRoot)) {
            throw "backend process tree is not repo-owned"
        }
        $tree += $current
        $parentPid = [int]$current.ParentProcessId
        if (-not $byId.ContainsKey($parentPid)) { break }
        $current = $byId[$parentPid]
    }
    return @($tree | Sort-Object ProcessId -Unique)
}

$lockOwned = $false
try {
    $null = New-Item -ItemType File -Path $lockPath -ErrorAction Stop
    $lockOwned = $true
    [ordered]@{
        schema="samsung-ocr-safe-idle-reload/v1"
        pid=$PID
        started_at=(Get-Date).ToString("o")
        repo_root=$RepoRoot
    } | ConvertTo-Json -Compress | Set-Content -LiteralPath $lockPath -Encoding UTF8

    if (Test-Path -LiteralPath $runtimeHealthFuse) { throw "runtime health fuse is active" }
    if (Test-Path -LiteralPath $benchmarkLock) { throw "model benchmark/upgrade lock is active" }
    $status = Get-Status
    if (-not $status -or [bool]$status.is_running) { throw "backend is not at an idle boundary" }
    if (
        [int]$status.stats.processed -ne [int]$status.stats.total -and
        -not $AllowIncompleteStoppedBatch
    ) {
        throw "idle backend has incomplete current work"
    }
    $runners = @(Get-Owned "rerun_staged_candidates\.py|recursive_ocr_flat_export\.py|rerun_questionable_records\.py|auto_rerun_questionable_after_recursive\.ps1")
    if ($runners.Count -gt 0) { throw "owned OCR runner still exists" }
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "venv Python missing" }
    if (-not (Test-Path -LiteralPath $backendScript -PathType Leaf)) { throw "backend script missing" }

    $tree = @(Get-BackendProcessTree)
    if ($tree.Count -lt 1) { throw "backend process tree is empty" }
    $listenerPid = [int](Get-NetTCPConnection -State Listen -LocalPort $backendPort | Select-Object -First 1 -ExpandProperty OwningProcess)
    $orderedPids = @($listenerPid) + @($tree | ForEach-Object {[int]$_.ProcessId} | Where-Object {$_ -ne $listenerPid})
    Log-Reload "idle_boundary_proven" @{
        folder=$status.current_relative_dir
        processed=$status.stats.processed
        total=$status.stats.total
        process_ids=$orderedPids
        incomplete_stopped_batch_recovery=[bool]$AllowIncompleteStoppedBatch
    }
    foreach ($processId in $orderedPids) {
        Stop-Process -Id $processId -ErrorAction SilentlyContinue
    }
    $stopDeadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $stopDeadline) {
        if (-not (Get-NetTCPConnection -State Listen -LocalPort $backendPort -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 250
    }
    if (Get-NetTCPConnection -State Listen -LocalPort $backendPort -ErrorAction SilentlyContinue) {
        throw "old backend listener did not stop"
    }

    $env:SAMSUNG_OCR_NO_BROWSER = "1"
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $outLog = Join-Path $logDir "safe_idle_backend_$stamp.out.log"
    $errLog = Join-Path $logDir "safe_idle_backend_$stamp.err.log"
    Start-Process -FilePath $python -WorkingDirectory $RepoRoot -WindowStyle Hidden `
        -ArgumentList @(
            $backendScript,
            "--api_base",$ApiBase,
            "--api_key","lm-studio",
            "--model",$Model,
            "--dir",$SourceRoot,
            "--port",[string]$backendPort,
            "--no_followme_auto_update"
        ) -RedirectStandardOutput $outLog -RedirectStandardError $errLog | Out-Null

    $deadline = (Get-Date).AddSeconds($VerifyTimeoutSeconds)
    $fresh = $null
    while ((Get-Date) -lt $deadline -and -not $fresh) {
        Start-Sleep -Milliseconds 500
        $fresh = Get-Status
    }
    if (
        -not $fresh -or
        [bool]$fresh.is_running -or
        [string]$fresh.version -notlike "v19.45*" -or
        [string]$fresh.status_contract_version -ne "compact-v2" -or
        [string]$fresh.evidence_guard_revision -notlike "20260720.*"
    ) {
        throw "fresh backend verification failed"
    }
    Log-Reload "fresh_backend_verified" @{
        version=$fresh.version
        evidence_guard_revision=$fresh.evidence_guard_revision
        frontend_asset_fingerprint=$fresh.frontend_asset_fingerprint
    }
} catch {
    Log-Reload "safe_idle_reload_failed" @{error=$_.Exception.Message}
    exit 1
} finally {
    if ($lockOwned -and (Test-Path -LiteralPath $lockPath)) {
        Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
    }
}

param(
    [Parameter(Mandatory=$true)][string]$RepoRoot,
    [Parameter(Mandatory=$true)][string]$SourceRoot,
    [Parameter(Mandatory=$true)][string]$OutputDir,
    [string]$BackendUrl = "http://127.0.0.1:5002",
    [string]$ApiBase = "http://127.0.0.1:1234/v1",
    [string]$Model = "qwen/qwen3-vl-8b",
    [int]$VerifyTimeoutSeconds = 60,
    [switch]$AllowIncompleteStoppedBatch,
    [switch]$RuntimeHealthTrialReload
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
function Test-SameExecutable([string]$Left, [string]$Right) {
    if (-not $Left -or -not $Right) { return $false }
    try {
        return [System.IO.Path]::GetFullPath($Left).Equals(
            [System.IO.Path]::GetFullPath($Right),
            [System.StringComparison]::OrdinalIgnoreCase
        )
    } catch { return $false }
}
function Get-VenvRuntimePython {
    $config = Join-Path $RepoRoot ".venv\pyvenv.cfg"
    if (-not (Test-Path -LiteralPath $config -PathType Leaf)) { return "" }
    foreach ($line in Get-Content -LiteralPath $config) {
        if ([string]$line -match "^\s*executable\s*=\s*(.+?)\s*$") {
            $candidate = [string]$Matches[1]
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                return [System.IO.Path]::GetFullPath($candidate)
            }
        }
    }
    return ""
}
function Get-BackendProcessTree {
    $all = @(Get-CimInstance Win32_Process)
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $backendPort -ErrorAction Stop)
    if ($listeners.Count -ne 1) { throw "expected one backend listener, found $($listeners.Count)" }
    $listenerPid = [int]$listeners[0].OwningProcess
    $byId = @{}
    foreach ($proc in $all) { $byId[[int]$proc.ProcessId] = $proc }
    if (-not $byId.ContainsKey($listenerPid)) { throw "backend listener process missing" }
    $listener = $byId[$listenerPid]
    $listenerCommand = [string]$listener.CommandLine
    $parent = if ($byId.ContainsKey([int]$listener.ParentProcessId)) {
        $byId[[int]$listener.ParentProcessId]
    } else { $null }
    $directRepoListener = (
        $listenerCommand -match "samsung_ocr_batch_processor\.py" -and
        $listenerCommand -match [regex]::Escape($RepoRoot)
    )
    # The project venv launcher can remain as the repo-owned parent while the
    # bundled runtime Python child owns port 5002.  Accept only that exact,
    # pyvenv.cfg-bound delegation; an arbitrary Python listener or a parent
    # from another checkout still fails closed.
    $runtimePython = Get-VenvRuntimePython
    $delegatedRuntimeListener = (
        $parent -and
        [string]$parent.CommandLine -match "samsung_ocr_batch_processor\.py" -and
        [string]$parent.CommandLine -match [regex]::Escape($RepoRoot) -and
        (Test-SameExecutable ([string]$parent.ExecutablePath) $python) -and
        (Test-SameExecutable ([string]$listener.ExecutablePath) $runtimePython)
    )
    if (-not $directRepoListener -and -not $delegatedRuntimeListener) {
        throw "port is not owned by the OCR backend or approved venv runtime delegate"
    }
    $tree = @($listener)
    if ($delegatedRuntimeListener) {
        $tree += $parent
        return @($tree | Sort-Object ProcessId -Unique)
    }
    $current = $parent
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

    if ($RuntimeHealthTrialReload) {
        if (-not (Test-Path -LiteralPath $runtimeHealthFuse)) {
            throw "runtime-health trial reload requires an active fuse"
        }
        if (-not (Test-Path -LiteralPath $benchmarkLock)) {
            throw "runtime-health trial reload requires the benchmark lock"
        }
    } else {
        if (Test-Path -LiteralPath $runtimeHealthFuse) { throw "runtime health fuse is active" }
        if (Test-Path -LiteralPath $benchmarkLock) { throw "model benchmark/upgrade lock is active" }
    }
    $status = Get-Status
    if (-not $status -or [bool]$status.is_running) { throw "backend is not at an idle boundary" }
    if (
        [int]$status.stats.processed -ne [int]$status.stats.total -and
        -not $AllowIncompleteStoppedBatch
    ) {
        throw "idle backend has incomplete current work"
    }
    $resumeIncompleteDir = ""
    if (
        $AllowIncompleteStoppedBatch -and
        [int]$status.stats.processed -lt [int]$status.stats.total
    ) {
        $candidate = [System.IO.Path]::GetFullPath([string]$status.image_dir)
        $stagingRoot = [System.IO.Path]::GetFullPath((Join-Path $OutputDir "_ocr_staging"))
        $sourceRootFull = [System.IO.Path]::GetFullPath($SourceRoot)
        $insideStaging = $candidate.StartsWith(
            $stagingRoot + [System.IO.Path]::DirectorySeparatorChar,
            [System.StringComparison]::OrdinalIgnoreCase
        )
        $insideSource = $candidate.StartsWith(
            $sourceRootFull + [System.IO.Path]::DirectorySeparatorChar,
            [System.StringComparison]::OrdinalIgnoreCase
        )
        if (
            (-not $insideStaging -and -not $insideSource) -or
            -not (Test-Path -LiteralPath $candidate -PathType Container)
        ) {
            throw "incomplete recovery directory is outside the approved source/staging roots"
        }
        $resumeIncompleteDir = $candidate
    }
    $runners = @(Get-Owned "rerun_staged_candidates\.py|recursive_ocr_flat_export\.py|rerun_questionable_records\.py|auto_rerun_questionable_after_recursive\.ps1")
    if ($runners.Count -gt 0) { throw "owned OCR runner still exists" }
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "venv Python missing" }
    if (-not (Test-Path -LiteralPath $backendScript -PathType Leaf)) { throw "backend script missing" }
    # The project venv launcher delegates to the bundled isolated Python, whose
    # ``-c`` mode does not automatically add the working directory to sys.path.
    # Pass the resolved repository explicitly so revision verification works
    # from both the canonical Unicode path and an ASCII maintenance junction.
    $expectedRevision = (& $python -c "import sys; sys.path.insert(0, sys.argv[1]); from skills.audit_fields import EVIDENCE_GUARD_REVISION; print(EVIDENCE_GUARD_REVISION)" $RepoRoot).Trim()
    if ($expectedRevision -notmatch "^\d{8}\.\d+$") {
        throw "repository evidence guard revision is invalid"
    }

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
        runtime_health_trial_reload=[bool]$RuntimeHealthTrialReload
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
        [string]$fresh.evidence_guard_revision -ne $expectedRevision -or
        ($RuntimeHealthTrialReload -and -not $fresh.runtime_health_fuse)
    ) {
        throw "fresh backend verification failed"
    }
    Log-Reload "fresh_backend_verified" @{
        version=$fresh.version
        evidence_guard_revision=$fresh.evidence_guard_revision
        frontend_asset_fingerprint=$fresh.frontend_asset_fingerprint
        runtime_health_trial_reload=[bool]$RuntimeHealthTrialReload
    }
    # Keep the reload helper alive while restoring an explicitly allowed
    # incomplete staging batch.  The continuity supervisor sees this helper's
    # interlock and therefore cannot create a second staging run in the gap
    # between backend reload and batch resume.
    if ($resumeIncompleteDir -and -not $RuntimeHealthTrialReload) {
        $resumeBody = @{
            dir=$resumeIncompleteDir
            restart=$false
            confirmed=$true
            reprocess_last_n=0
        } | ConvertTo-Json
        $resume = Invoke-RestMethod -Uri "$BackendUrl/api/start_batch" -Method Post `
            -ContentType "application/json; charset=utf-8" -Body $resumeBody -TimeoutSec 30
        if ([string]$resume.status -ne "started") {
            throw "fresh backend did not resume the preserved incomplete staging batch"
        }
        $resumed = Get-Status
        $resumedProcessed = if ($resumed -and $resumed.stats) { [int]$resumed.stats.processed } else { 0 }
        $resumedTotal = if ($resumed -and $resumed.stats) { [int]$resumed.stats.total } else { 0 }
        $resumedCapped = if ($resumed -and $resumed.capped_adjudication) { [int]$resumed.capped_adjudication.count } else { 0 }
        # A restored batch whose only unfinished photos are already protected
        # by the durable three-call capped queue can settle back to the photo
        # boundary before this immediate verification poll. That is a valid
        # resume, not a failure: the zero-model finalizer owns those photos.
        $resumedSettledAtBoundary = (
            -not [bool]$resumed.is_running -and
            $resumedTotal -gt 0 -and
            ($resumedProcessed + $resumedCapped) -eq $resumedTotal
        )
        if (
            -not $resumed -or
            (-not [bool]$resumed.is_running -and -not $resumedSettledAtBoundary) -or
            [System.IO.Path]::GetFullPath([string]$resumed.image_dir) -ne $resumeIncompleteDir
        ) {
            throw "preserved incomplete staging batch resume verification failed"
        }
        Log-Reload "incomplete_checkpoint_resumed" @{
            image_dir=$resumeIncompleteDir
            processed=$resumedProcessed
            capped=$resumedCapped
            total=$resumedTotal
            running=[bool]$resumed.is_running
            settled_at_boundary=$resumedSettledAtBoundary
        }
    }
} catch {
    Log-Reload "safe_idle_reload_failed" @{error=$_.Exception.Message}
    exit 1
} finally {
    if ($lockOwned -and (Test-Path -LiteralPath $lockPath)) {
        Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
    }
}

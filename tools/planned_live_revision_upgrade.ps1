param(
    [Parameter(Mandatory=$true)][string]$RepoRoot,
    [Parameter(Mandatory=$true)][string]$SourceRoot,
    [Parameter(Mandatory=$true)][string]$OutputDir,
    [string]$BackendUrl = "http://127.0.0.1:5002",
    [string]$ApiBase = "http://127.0.0.1:1234/v1",
    [string]$Model = "qwen/qwen3-vl-8b"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$SourceRoot = (Resolve-Path -LiteralPath $SourceRoot).Path
$OutputDir = (Resolve-Path -LiteralPath $OutputDir).Path
$python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$audit = Join-Path $OutputDir "_ocr_audit"
$logs = Join-Path $RepoRoot "logs"
$lockPath = Join-Path $audit "planned_live_revision_upgrade.lock"
$logPath = Join-Path $logs ("planned_live_revision_upgrade_{0}.jsonl" -f (Get-Date -Format "yyyyMMdd"))
$receipt = Join-Path $audit "historical_continuation_receipt.json"
$reload = Join-Path $RepoRoot "tools\reload_backend_at_safe_idle.ps1"
New-Item -ItemType Directory -Force -Path $audit,$logs | Out-Null

function Log-Upgrade([string]$Event, [hashtable]$Data=@{}) {
    $row = [ordered]@{timestamp=(Get-Date).ToString("o");event=$Event;pid=$PID}
    foreach ($key in $Data.Keys) { $row[$key] = $Data[$key] }
    $row | ConvertTo-Json -Compress -Depth 5 | Add-Content -LiteralPath $logPath -Encoding UTF8
}
function Get-Status {
    try { Invoke-RestMethod -Uri "$BackendUrl/api/status" -TimeoutSec 8 } catch { $null }
}
function Get-Owned([string]$Pattern) {
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match $Pattern -and
        $_.CommandLine -match [regex]::Escape($RepoRoot) -and
        $_.CommandLine -notmatch "Get-CimInstance|planned_live_revision_upgrade"
    })
}
function Stop-Owned([string]$Pattern) {
    foreach ($proc in @(Get-Owned $Pattern | Sort-Object ProcessId -Descending)) {
        Stop-Process -Id ([int]$proc.ProcessId) -ErrorAction SilentlyContinue
    }
}
function Start-Hidden([string]$File, [string[]]$ProcessArgs, [string]$Name) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    Start-Process -FilePath $File -WorkingDirectory $RepoRoot -WindowStyle Hidden `
        -ArgumentList $ProcessArgs `
        -RedirectStandardOutput (Join-Path $logs "$Name`_$stamp.out.log") `
        -RedirectStandardError (Join-Path $logs "$Name`_$stamp.err.log") | Out-Null
}
function Ensure-Auxiliaries {
    if (@(Get-Owned "stream_drive_upload\.py").Count -eq 0) {
        Start-Hidden $python @(
            (Join-Path $RepoRoot "tools\stream_drive_upload.py"),
            "--output-dir",$OutputDir,"--poll-seconds","5","--timeout-seconds","600"
        ) "stream_drive_upload"
    }
    if (@(Get-Owned "recursive_ocr_flat_export\.py").Count -eq 0) {
        Start-Hidden $python @(
            (Join-Path $RepoRoot "tools\recursive_ocr_flat_export.py"),
            "--source-root",$SourceRoot,"--output-dir",$OutputDir,
            "--backend-url",$BackendUrl,"--api-base",$ApiBase,
            "--api-key","lm-studio","--model",$Model,
            "--poll-seconds","20","--timeout-minutes","10080",
            "--historical-continuation-receipt",$receipt
        ) "recursive_resume"
    }
    if (@(Get-Owned "auto_rerun_questionable_after_recursive\.ps1").Count -eq 0) {
        Start-Hidden "powershell.exe" @(
            "-NoProfile","-ExecutionPolicy","Bypass","-File",
            (Join-Path $RepoRoot "tools\auto_rerun_questionable_after_recursive.ps1"),
            "-RepoRoot",$RepoRoot,"-SourceRoot",$SourceRoot,"-OutputDir",$OutputDir,
            "-BackendUrl",$BackendUrl,"-PollSeconds","300","-PrimaryPasses","2",
            "-SkipCurrentYearPhases","-SkipRecursiveResume"
        ) "questionable_watcher"
    }
    if (@(Get-Owned "ocr_continuity_daemon\.ps1").Count -eq 0) {
        Start-Hidden "powershell.exe" @(
            "-NoProfile","-ExecutionPolicy","Bypass","-File",
            (Join-Path $RepoRoot "tools\ocr_continuity_daemon.ps1"),
            "-RepoRoot",$RepoRoot,"-SourceRoot",$SourceRoot,"-OutputDir",$OutputDir,
            "-BackendUrl",$BackendUrl,"-IntervalSeconds","60"
        ) "continuity_daemon"
    }
}

$lockOwned = $false
$checkpoint = ""
try {
    $null = New-Item -ItemType File -Path $lockPath -ErrorAction Stop
    $lockOwned = $true
    $before = Get-Status
    if (-not $before -or -not [bool]$before.is_running) { throw "live OCR batch is not running" }
    if ($before.runtime_health_fuse -or $before.pipeline_pause) { throw "runtime interlock is already active" }
    $checkpoint = [System.IO.Path]::GetFullPath([string]$before.image_dir)
    if (-not $checkpoint.StartsWith(
        [System.IO.Path]::GetFullPath($SourceRoot) + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase
    )) { throw "active checkpoint is outside source root" }
    $expectedRevision = (& $python -c "import sys; sys.path.insert(0, sys.argv[1]); from skills.audit_fields import EVIDENCE_GUARD_REVISION; print(EVIDENCE_GUARD_REVISION)" $RepoRoot).Trim()
    if ($expectedRevision -notmatch "^\d{8}\.\d+$") { throw "repository revision is invalid" }
    $gateScript = Join-Path $RepoRoot "tools\historical_continuation_gate.py"
    $null = & $python $gateScript --source-root $SourceRoot --output-dir $OutputDir `
        --backend-url $BackendUrl --validate-receipt 2>&1
    $receiptValid = ($LASTEXITCODE -eq 0)
    if ([string]$before.evidence_guard_revision -eq $expectedRevision -and $receiptValid) {
        Ensure-Auxiliaries
        Log-Upgrade "already_current" @{revision=$expectedRevision;checkpoint=$checkpoint}
        return
    }
    if (@(Get-Owned "recursive_ocr_flat_export\.py").Count -eq 0 -and $receiptValid) {
        throw "recursive coordinator is missing"
    }

    Log-Upgrade "photo_boundary_upgrade_requested" @{
        from_revision=[string]$before.evidence_guard_revision
        to_revision=$expectedRevision
        checkpoint=$checkpoint
        processed=[int]$before.stats.processed
        total=[int]$before.stats.total
    }
    Stop-Owned "ocr_continuity_daemon\.ps1"
    Stop-Owned "auto_rerun_questionable_after_recursive\.ps1"
    Stop-Owned "recursive_ocr_flat_export\.py"
    Stop-Owned "stream_drive_upload\.py"

    $body = @{reason="planned_guard_revision_$expectedRevision"} | ConvertTo-Json
    Invoke-RestMethod -Uri "$BackendUrl/api/stop" -Method Post `
        -ContentType "application/json; charset=utf-8" -Body $body -TimeoutSec 10 | Out-Null
    $deadline = (Get-Date).AddSeconds(120)
    do {
        Start-Sleep -Milliseconds 500
        $idle = Get-Status
    } while ($idle -and [bool]$idle.is_running -and (Get-Date) -lt $deadline)
    if (-not $idle -or [bool]$idle.is_running) { throw "photo-boundary stop timed out" }
    if ([System.IO.Path]::GetFullPath([string]$idle.image_dir) -ne $checkpoint) {
        throw "checkpoint changed while stopping"
    }

    # The continuation authority is content-bound and revision-bound.  Keep
    # the original user request time, then recreate its receipt only while the
    # backend is provably idle.  This prevents a valid code upgrade from
    # stranding the historical coordinator or reprocessing the sealed year.
    $null = & $python $gateScript --source-root $SourceRoot --output-dir $OutputDir `
        --backend-url $BackendUrl --migrate-existing-request 2>&1
    if ($LASTEXITCODE -ne 0) { throw "continuation request migration failed" }
    $null = & $python $gateScript --source-root $SourceRoot --output-dir $OutputDir `
        --backend-url $BackendUrl --write-receipt 2>&1
    if ($LASTEXITCODE -ne 0) { throw "continuation receipt refresh failed" }

    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $reload `
        -RepoRoot $RepoRoot -SourceRoot $SourceRoot -OutputDir $OutputDir `
        -BackendUrl $BackendUrl -ApiBase $ApiBase -Model $Model `
        -AllowIncompleteStoppedBatch
    if ($LASTEXITCODE -ne 0) { throw "safe backend reload failed: $LASTEXITCODE" }

    Ensure-Auxiliaries
    $verifyDeadline = (Get-Date).AddSeconds(60)
    do {
        Start-Sleep -Seconds 1
        $after = Get-Status
    } while (
        ( -not $after -or -not [bool]$after.is_running -or
          [string]$after.evidence_guard_revision -ne $expectedRevision ) -and
        (Get-Date) -lt $verifyDeadline
    )
    if (-not $after -or -not [bool]$after.is_running) { throw "OCR did not resume" }
    if ([string]$after.evidence_guard_revision -ne $expectedRevision) { throw "new revision is not active" }
    if ([System.IO.Path]::GetFullPath([string]$after.image_dir) -ne $checkpoint) {
        throw "resumed checkpoint differs from saved checkpoint"
    }
    if (@(Get-Owned "recursive_ocr_flat_export\.py").Count -eq 0) { throw "recursive coordinator did not reattach" }
    if (@(Get-Owned "stream_drive_upload\.py").Count -eq 0) { throw "stream uploader did not restart" }
    Log-Upgrade "live_revision_upgrade_verified" @{
        revision=$expectedRevision
        checkpoint=$checkpoint
        processed=[int]$after.stats.processed
        total=[int]$after.stats.total
        running=[bool]$after.is_running
    }
} catch {
    Log-Upgrade "live_revision_upgrade_failed" @{error=$_.Exception.Message;checkpoint=$checkpoint}
    try { Ensure-Auxiliaries } catch {}
    throw
} finally {
    if ($lockOwned) { Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue }
}

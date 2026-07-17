param(
    [string]$RepoRoot = "D:\00_商化\samsung-monitor-ocr",
    [string]$SourceRoot = "D:\00_商化\00_未整理商化照片",
    [string]$OutputDir = "D:\00_商化\00_已OCR照片",
    [string]$BackendUrl = "http://127.0.0.1:5002",
    [string]$ApiBase = "http://127.0.0.1:1234/v1",
    [string]$Model = "qwen/qwen3-vl-8b",
    [int]$PollSeconds = 60
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
} catch {
}
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogPath = Join-Path $LogDir "continue_after_missing_rerun_$Stamp.log"
$BackendOut = Join-Path $LogDir "continue_after_missing_rerun_backend_$Stamp.out.log"
$BackendErr = Join-Path $LogDir "continue_after_missing_rerun_backend_$Stamp.err.log"

function Write-RunLog {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "s"), $Message
    Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value $line
}

function Get-MatchingProcess {
    param([string]$Pattern)
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -match $Pattern -and $_.CommandLine -notmatch "Get-CimInstance"
    })
}

function Test-BackendRunning {
    try {
        $status = Invoke-RestMethod -Uri "$BackendUrl/api/status" -TimeoutSec 10
        return $status
    } catch {
        return $null
    }
}

function Start-Backend {
    Write-RunLog "starting backend with current repo code"
    Start-Process -FilePath $Python `
        -WorkingDirectory $RepoRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $BackendOut `
        -RedirectStandardError $BackendErr `
        -ArgumentList @(
            "samsung_ocr_batch_processor.py",
            "--api_base", $ApiBase,
            "--api_key", "lm-studio",
            "--model", $Model,
            "--dir", $SourceRoot
        ) | Out-Null

    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 2
        if (Test-BackendRunning) {
            Write-RunLog "backend is ready"
            return
        }
    }
    throw "backend did not become ready"
}

Push-Location $RepoRoot
try {
    Write-RunLog "watcher started"

    while ($true) {
        $rerun = Get-MatchingProcess "rerun_questionable_records.py"
        if ($rerun.Count -eq 0) {
            Write-RunLog "missing-result rerun process is gone"
            break
        }
        $status = Test-BackendRunning
        if ($status) {
            $stats = $status.stats
            Write-RunLog ("rerun alive; backend {0}; {1}/{2}; success={3}; failed={4}" -f `
                $status.current_relative_dir, $stats.processed, $stats.total, $stats.success, $stats.failed)
        } else {
            Write-RunLog "rerun alive; backend status unavailable"
        }
        Start-Sleep -Seconds $PollSeconds
    }

    while ($true) {
        $status = Test-BackendRunning
        if (-not $status) {
            Write-RunLog "backend unavailable after rerun; will start a fresh backend"
            break
        }
        if (-not [bool]$status.is_running) {
            Write-RunLog "backend idle after rerun"
            break
        }
        $stats = $status.stats
        Write-RunLog ("backend still finishing {0}; {1}/{2}" -f $status.current_relative_dir, $stats.processed, $stats.total)
        Start-Sleep -Seconds $PollSeconds
    }

    $recursive = Get-MatchingProcess "recursive_ocr_flat_export.py"
    if ($recursive.Count -gt 0) {
        Write-RunLog "recursive runner already active; not starting another"
        exit 0
    }

    $backend = Get-MatchingProcess "samsung_ocr_batch_processor.py"
    foreach ($proc in $backend) {
        Write-RunLog "stopping old backend pid=$($proc.ProcessId) to load current code"
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 3
    Start-Backend

    Write-RunLog "starting recursive OCR flat export in resume mode"
    & $Python "tools\recursive_ocr_flat_export.py" `
        --source-root $SourceRoot `
        --output-dir $OutputDir `
        --backend-url $BackendUrl `
        --api-base $ApiBase `
        --api-key "lm-studio" `
        --model $Model *>> $LogPath
    $recursiveExit = $LASTEXITCODE
    Write-RunLog "recursive exit=$recursiveExit"
    if ($recursiveExit -ne 0) {
        exit $recursiveExit
    }

    Write-RunLog "running recursive audit report"
    & $Python "tools\recursive_ocr_audit_report.py" --output-dir $OutputDir *>> $LogPath
    $auditExit = $LASTEXITCODE
    Write-RunLog "audit exit=$auditExit"
    if ($auditExit -ne 0) {
        exit $auditExit
    }

    $rclone = Get-MatchingProcess "rclone_drive_upload.py|rclone.exe"
    if ($rclone.Count -gt 0) {
        Write-RunLog "rclone upload already active; not starting another"
        exit 0
    }

    Write-RunLog "starting Google Drive upload for ready rows"
    & $Python "tools\rclone_drive_upload.py" `
        --output-dir $OutputDir `
        --execute `
        --repeat `
        --limit 100 `
        --rclone-timeout-seconds 1200 *>> $LogPath
    $uploadExit = $LASTEXITCODE
    Write-RunLog "upload exit=$uploadExit"
    exit $uploadExit
} catch {
    Write-RunLog "fatal: $($_.Exception.Message)"
    exit 1
} finally {
    Pop-Location
}


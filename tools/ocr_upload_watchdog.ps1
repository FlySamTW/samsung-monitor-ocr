param(
    [string]$RepoRoot = "",
    [string]$SourceRoot = "",
    [string]$OutputDir = "",
    [string]$BackendUrl = "http://127.0.0.1:5000",
    [string]$ApiBase = "http://127.0.0.1:1234/v1",
    [string]$Model = "qwen/qwen3-vl-8b",
    [int]$RcloneTimeoutSeconds = 1200
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
}
if (-not $SourceRoot) {
    throw "SourceRoot is required"
}
if (-not $OutputDir) {
    throw "OutputDir is required"
}

$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "python"
}

$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogPath = Join-Path $LogDir ("ocr_upload_watchdog_{0}.log" -f (Get-Date -Format "yyyyMMdd"))
$LockPath = Join-Path $OutputDir "_ocr_audit\ocr_upload_watchdog.lock"

function Write-RunLog([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "s"), $Message
    Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value $line
    Write-Output $line
}

function Get-MatchingProcess([string]$Pattern) {
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -match $Pattern -and $_.CommandLine -notmatch "Get-CimInstance"
    })
}

function Get-BackendStatus {
    try {
        return Invoke-RestMethod -Uri "$BackendUrl/api/status" -TimeoutSec 12
    } catch {
        return $null
    }
}

function Start-BackendIfNeeded {
    $status = Get-BackendStatus
    $backend = Get-MatchingProcess "samsung_ocr_batch_processor.py"
    if ($status -or $backend.Count -gt 0) {
        Write-RunLog "backend ok process_count=$($backend.Count)"
        return
    }

    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $outLog = Join-Path $LogDir "watchdog_backend_$stamp.out.log"
    $errLog = Join-Path $LogDir "watchdog_backend_$stamp.err.log"
    Write-RunLog "starting backend model=$Model"
    Start-Process -FilePath $Python `
        -WorkingDirectory $RepoRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -ArgumentList @(
            "samsung_ocr_batch_processor.py",
            "--api_base", $ApiBase,
            "--api_key", "lm-studio",
            "--model", $Model,
            "--dir", $SourceRoot
        ) | Out-Null

    for ($i = 0; $i -lt 45; $i++) {
        Start-Sleep -Seconds 2
        if (Get-BackendStatus) {
            Write-RunLog "backend started"
            return
        }
    }
    Write-RunLog "backend did not become ready; see $outLog $errLog"
}

function Get-CsvRowCount([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return 0 }
    try {
        return @((Import-Csv -LiteralPath $Path)).Count
    } catch {
        return 0
    }
}

function Repair-FolderSummaryIfShrunk {
    $auditDir = Join-Path $OutputDir "_ocr_audit"
    $summary = Join-Path $auditDir "folder_summary.csv"
    if (-not (Test-Path -LiteralPath $auditDir)) { return }
    $summaryRows = Get-CsvRowCount $summary
    $auditFolders = @(Get-ChildItem -LiteralPath $auditDir -Directory -ErrorAction SilentlyContinue | Where-Object {
        Test-Path -LiteralPath (Join-Path $_.FullName "rename_plan.csv")
    }).Count
    if ($auditFolders -ge 10 -and $summaryRows -lt [math]::Min(10, $auditFolders)) {
        Write-RunLog "folder_summary appears shrunk rows=$summaryRows audit_folders=$auditFolders; rebuilding"
        & $Python "tools\rebuild_recursive_folder_summary.py" --output-dir $OutputDir *>> $LogPath
        Write-RunLog "rebuild summary exit=$LASTEXITCODE"
    } else {
        Write-RunLog "folder_summary ok rows=$summaryRows audit_folders=$auditFolders"
    }
}

function Start-RecursiveIfNeeded {
    $recursive = Get-MatchingProcess "recursive_ocr_flat_export.py"
    if ($recursive.Count -gt 0) {
        Write-RunLog "recursive runner ok process_count=$($recursive.Count)"
        return
    }

    $status = Get-BackendStatus
    if ($status -and [bool]$status.is_running) {
        Write-RunLog "backend is already running a batch; not starting recursive"
        return
    }

    $overall = $status.overall_progress
    $remaining = 1
    if ($overall -and $null -ne $overall.remaining_images) {
        $remaining = [int]$overall.remaining_images
    }
    if ($remaining -le 0) {
        Write-RunLog "overall OCR appears complete; not starting recursive"
        return
    }

    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $outLog = Join-Path $LogDir "watchdog_recursive_$stamp.out.log"
    $errLog = Join-Path $LogDir "watchdog_recursive_$stamp.err.log"
    Write-RunLog "starting recursive OCR resume remaining=$remaining"
    Start-Process -FilePath $Python `
        -WorkingDirectory $RepoRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -ArgumentList @(
            "tools\recursive_ocr_flat_export.py",
            "--source-root", $SourceRoot,
            "--output-dir", $OutputDir,
            "--backend-url", $BackendUrl,
            "--api-base", $ApiBase,
            "--api-key", "lm-studio",
            "--model", $Model,
            "--poll-seconds", "20",
            "--timeout-minutes", "360",
            "--watch",
            "--watch-sleep-seconds", "300"
        ) | Out-Null
}

function Start-AutoRerunWatcherIfNeeded {
    $watcher = @(Get-MatchingProcess "auto_rerun_questionable_after_recursive\.ps1" | Sort-Object CreationDate)
    if ($watcher.Count -gt 1) {
        $extras = @($watcher | Select-Object -Skip 1)
        foreach ($proc in $extras) {
            Write-RunLog "stopping duplicate questionable watcher pid=$($proc.ProcessId)"
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
        }
        $watcher = @($watcher | Select-Object -First 1)
    }
    if ($watcher.Count -gt 0) {
        Write-RunLog "questionable watcher ok process_count=$($watcher.Count)"
        return
    }

    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $outLog = Join-Path $LogDir "watchdog_auto_rerun_$stamp.out.log"
    $errLog = Join-Path $LogDir "watchdog_auto_rerun_$stamp.err.log"
    Write-RunLog "starting questionable watcher"
    Start-Process -FilePath "powershell.exe" `
        -WorkingDirectory $RepoRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -ArgumentList @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", "tools\auto_rerun_questionable_after_recursive.ps1",
            "-RepoRoot", $RepoRoot,
            "-SourceRoot", $SourceRoot,
            "-OutputDir", $OutputDir,
            "-BackendUrl", $BackendUrl,
            "-PollSeconds", "300"
        ) | Out-Null
}

function Remove-StaleUploadLockIfNeeded {
    $lock = Join-Path $OutputDir "_drive_upload\rclone_drive_upload.lock"
    if (-not (Test-Path -LiteralPath $lock)) { return }
    $text = Get-Content -LiteralPath $lock -Raw -ErrorAction SilentlyContinue
    $pidMatch = [regex]::Match($text, "pid=(\d+)")
    if (-not $pidMatch.Success) { return }
    $pid = [int]$pidMatch.Groups[1].Value
    $alive = Get-Process -Id $pid -ErrorAction SilentlyContinue
    if (-not $alive) {
        Write-RunLog "removing stale upload lock pid=$pid"
        Remove-Item -LiteralPath $lock -Force -ErrorAction SilentlyContinue
    }
}

function Start-UploaderIfNeeded {
    $pendingCsv = Join-Path $OutputDir "_drive_upload\drive_upload_ready_pending.csv"
    $pending = Get-CsvRowCount $pendingCsv
    if ($pending -le 0) {
        Write-RunLog "no ready upload pending rows"
        return
    }

    $uploader = Get-MatchingProcess "rclone_drive_upload.py|rclone.exe"
    if ($uploader.Count -gt 0) {
        Write-RunLog "uploader ok process_count=$($uploader.Count) pending=$pending"
        return
    }

    Remove-StaleUploadLockIfNeeded
    $outLog = Join-Path $OutputDir "_drive_upload\rclone_drive_upload_stdout.log"
    $errLog = Join-Path $OutputDir "_drive_upload\rclone_drive_upload_stderr.log"
    Write-RunLog "starting uploader pending=$pending"
    Start-Process -FilePath $Python `
        -WorkingDirectory $RepoRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -ArgumentList @(
            "tools\rclone_drive_upload.py",
            "--output-dir", $OutputDir,
            "--execute",
            "--repeat",
            "--limit", "500",
            "--transfers", "4",
            "--checkers", "8",
            "--rclone-timeout-seconds", "$RcloneTimeoutSeconds"
        ) | Out-Null
}

function Log-Progress {
    $status = Get-BackendStatus
    if ($status) {
        $overall = $status.overall_progress
        Write-RunLog ("status running={0} folder={1} file={2} processed={3}/{4} percent={5}" -f `
            [bool]$status.is_running,
            $status.current_relative_dir,
            $status.current_file,
            $overall.processed_images,
            $overall.total_images,
            $overall.percent)
    } else {
        Write-RunLog "status unavailable"
    }

    $summaryPath = Join-Path $OutputDir "_drive_upload\drive_upload_summary.json"
    if (Test-Path -LiteralPath $summaryPath) {
        try {
            $summary = Get-Content -LiteralPath $summaryPath -Raw | ConvertFrom-Json
            Write-RunLog ("upload ready={0} uploaded_or_skipped={1} pending={2} review={3}" -f `
                $summary.ready, $summary.uploaded_skipped, $summary.ready_pending, $summary.review_required)
        } catch {
            Write-RunLog "upload summary unreadable"
        }
    }
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LockPath) | Out-Null
if (Test-Path -LiteralPath $LockPath) {
    $ageMinutes = ((Get-Date) - (Get-Item -LiteralPath $LockPath).LastWriteTime).TotalMinutes
    if ($ageMinutes -lt 180) {
        Write-RunLog "watchdog lock exists age_minutes=$([math]::Round($ageMinutes,1)); exiting"
        exit 0
    }
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
}

try {
    Set-Content -LiteralPath $LockPath -Encoding UTF8 -Value ("pid={0}`nstarted={1}" -f $PID, (Get-Date -Format "s"))
    Write-RunLog "watchdog start"
    Repair-FolderSummaryIfShrunk
    Start-BackendIfNeeded
    Start-RecursiveIfNeeded
    Start-AutoRerunWatcherIfNeeded
    Start-UploaderIfNeeded
    Log-Progress
    Write-RunLog "watchdog done"
} finally {
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
}

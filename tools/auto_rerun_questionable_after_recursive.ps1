param(
    [string]$RepoRoot = "",
    [string]$SourceRoot = "",
    [string]$OutputDir = "",
    [string]$BackendUrl = "http://127.0.0.1:5000",
    [int]$PollSeconds = 300,
    [int]$PrimaryPasses = 3,
    [string]$PrimaryModel = "qwen/qwen3-vl-8b",
    [string[]]$FinalModels = @("qwen3.5-9b-vlm", "qwen/qwen2.5-vl-7b", "gemma-4-12b-it-qat")
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
} catch {
}

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path ".").Path
}
if (-not $SourceRoot) {
    throw "SourceRoot is required"
}
if (-not $OutputDir) {
    throw "OutputDir is required"
}

$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogPath = Join-Path $LogDir "auto_rerun_questionable_after_recursive_$Stamp.log"
$BackendOut = Join-Path $LogDir "auto_questionable_backend_$Stamp.out.log"
$BackendErr = Join-Path $LogDir "auto_questionable_backend_$Stamp.err.log"

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

function Get-BackendStatus {
    try {
        return Invoke-RestMethod -Uri "$BackendUrl/api/status" -TimeoutSec 10
    } catch {
        return $null
    }
}

function Stop-Backend {
    $backend = Get-MatchingProcess "samsung_ocr_batch_processor.py"
    foreach ($proc in $backend) {
        Write-RunLog "stopping backend pid=$($proc.ProcessId)"
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 3
}

function Start-Backend {
    param([string]$Model)
    Write-RunLog "starting backend model=$Model"
    Start-Process -FilePath $Python `
        -WorkingDirectory $RepoRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $BackendOut `
        -RedirectStandardError $BackendErr `
        -ArgumentList @(
            "samsung_ocr_batch_processor.py",
            "--api_base", "http://127.0.0.1:1234/v1",
            "--api_key", "lm-studio",
            "--model", $Model,
            "--dir", $SourceRoot
        ) | Out-Null

    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 2
        if (Get-BackendStatus) {
            Write-RunLog "backend is ready"
            return
        }
    }
    throw "backend did not become ready"
}

function Ensure-BackendModel {
    param([string]$Model)
    $status = Get-BackendStatus
    if ($status -and [bool]$status.is_running) {
        throw "backend is still running; refusing to switch model"
    }
    if ($status -and $status.current_model -eq $Model) {
        Write-RunLog "backend already ready with model=$Model"
        return
    }
    Stop-Backend
    Start-Backend -Model $Model
}

function Get-AvailableModelIds {
    try {
        $models = Invoke-RestMethod -Uri "http://127.0.0.1:1234/v1/models" -TimeoutSec 10
        return @($models.data | ForEach-Object { $_.id })
    } catch {
        Write-RunLog "unable to query LM Studio models: $($_.Exception.Message)"
        return @()
    }
}

function Refresh-UploadAndReviewSplit {
    Write-RunLog "refreshing upload manifest"
    & $Python "tools\prepare_drive_upload_manifest.py" --output-dir $OutputDir --no-stage *>> $LogPath
    Write-RunLog "manifest refresh exit=$LASTEXITCODE"
    & $Python "tools\split_drive_review_required.py" --output-dir $OutputDir *>> $LogPath
    Write-RunLog "review split exit=$LASTEXITCODE"
}

function Start-Uploader-IfNeeded {
    $uploader = Get-MatchingProcess "rclone_drive_upload.py|rclone.exe"
    if ($uploader.Count -eq 0) {
        Write-RunLog "starting rclone uploader"
        $uploadOut = Join-Path $OutputDir "_drive_upload\rclone_drive_upload_stdout.log"
        $uploadErr = Join-Path $OutputDir "_drive_upload\rclone_drive_upload_stderr.log"
        Start-Process -FilePath $Python `
            -ArgumentList @(
                "tools\rclone_drive_upload.py",
                "--output-dir", $OutputDir,
                "--execute",
                "--repeat",
                "--limit", "500",
                "--transfers", "4",
                "--checkers", "8",
                "--rclone-timeout-seconds", "1200"
            ) `
            -WorkingDirectory $RepoRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $uploadOut `
            -RedirectStandardError $uploadErr | Out-Null
    } else {
        Write-RunLog "uploader already active; not starting another"
    }
}

function Invoke-QuestionablePass {
    param(
        [string]$Label,
        [string]$Model
    )
    Ensure-BackendModel -Model $Model
    $safeLabel = $Label -replace "[^A-Za-z0-9_-]", "_"
    $candidateCsv = Join-Path $OutputDir ("_ocr_audit\questionable_rerun_candidates_{0}_{1}.csv" -f $safeLabel, $Stamp)
    $summaryCsv = Join-Path $OutputDir ("_ocr_audit\questionable_rerun_summary_{0}_{1}.csv" -f $safeLabel, $Stamp)
    Write-RunLog "starting questionable rerun label=$Label model=$Model"
    & $Python "tools\rerun_questionable_records.py" `
        --source-root $SourceRoot `
        --output-dir $OutputDir `
        --backend-url $BackendUrl `
        --include-older `
        --execute `
        --output-csv $candidateCsv `
        --run-summary-csv $summaryCsv *>> $LogPath
    $rerunExit = $LASTEXITCODE
    Write-RunLog "questionable rerun label=$Label exit=$rerunExit"
    if ($rerunExit -ne 0) {
        exit $rerunExit
    }
    Refresh-UploadAndReviewSplit
    Start-Uploader-IfNeeded
}

Push-Location $RepoRoot
try {
    Write-RunLog "watcher started; waiting for recursive OCR runner to finish"

    while ($true) {
        $recursive = Get-MatchingProcess "recursive_ocr_flat_export.py"
        $status = Get-BackendStatus
        if ($recursive.Count -eq 0) {
            if (-not $status) {
                Start-Backend -Model $PrimaryModel
                $status = Get-BackendStatus
            }
            if ($status -and -not [bool]$status.is_running) {
                Write-RunLog "recursive runner is gone and backend is idle"
                break
            }
        }

        if ($status) {
            $stats = $status.stats
            Write-RunLog ("waiting; recursive={0}; backend_running={1}; folder={2}; {3}/{4}" -f `
                $recursive.Count, [bool]$status.is_running, $status.current_relative_dir, $stats.processed, $stats.total)
        } else {
            Write-RunLog ("waiting; recursive={0}; backend unavailable" -f $recursive.Count)
        }
        Start-Uploader-IfNeeded
        Start-Sleep -Seconds $PollSeconds
    }

    for ($pass = 1; $pass -le $PrimaryPasses; $pass++) {
        Invoke-QuestionablePass -Label ("qwen_pass_{0}" -f $pass) -Model $PrimaryModel
    }

    $availableModels = Get-AvailableModelIds
    foreach ($model in $FinalModels) {
        if ($availableModels -contains $model) {
            Invoke-QuestionablePass -Label ("final_{0}" -f $model) -Model $model
        } else {
            Write-RunLog "final model unavailable; skipped model=$model"
        }
    }

    Write-RunLog "watcher finished"
} finally {
    Pop-Location
}

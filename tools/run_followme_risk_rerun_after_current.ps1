param(
    [string]$RepoRoot = "",
    [string]$SourceRoot = "",
    [string]$OutputDir = "",
    [string]$BackendUrl = "http://127.0.0.1:5001",
    [string]$ApiBase = "http://127.0.0.1:1234/v1",
    [string]$Model = "qwen/qwen3-vl-8b",
    [int]$PollSeconds = 60
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:SAMSUNG_OCR_NO_BROWSER = "1"
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
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogPath = Join-Path $LogDir "followme_risk_after_current_$Stamp.log"
$BackendOut = Join-Path $LogDir "followme_risk_backend5001_$Stamp.out.log"
$BackendErr = Join-Path $LogDir "followme_risk_backend5001_$Stamp.err.log"

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

function Invoke-JsonGet([string]$Url) {
    try {
        return Invoke-RestMethod -Uri $Url -TimeoutSec 12
    } catch {
        return $null
    }
}

function Get-BackendStatus {
    return Invoke-JsonGet "$BackendUrl/api/status"
}

function Wait-For-ExistingStagedRerun {
    while ($true) {
        $running = Get-MatchingProcess "rerun_staged_candidates.py"
        if ($running.Count -eq 0) {
            Write-RunLog "no existing staged rerun process; continuing"
            return
        }
        Write-RunLog "waiting for existing staged rerun process_count=$($running.Count)"
        Start-Sleep -Seconds $PollSeconds
    }
}

function Stop-Backend5001 {
    $backend = Get-MatchingProcess "samsung_ocr_batch_processor.py.*--port 5001"
    foreach ($proc in $backend) {
        Write-RunLog "stopping backend5001 pid=$($proc.ProcessId)"
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if ($backend.Count -gt 0) {
        Start-Sleep -Seconds 5
    }
}

function Start-Backend5001 {
    Write-RunLog "starting backend5001 with refreshed code model=$Model"
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
            "--dir", $SourceRoot,
            "--port", "5001",
            "--no_followme_auto_update"
        ) | Out-Null

    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 2
        $status = Get-BackendStatus
        if ($status) {
            Write-RunLog "backend5001 ready"
            return
        }
    }
    throw "backend5001 did not become ready; see $BackendOut $BackendErr"
}

function Wait-BackendIdle {
    while ($true) {
        $status = Get-BackendStatus
        if (-not $status -or -not [bool]$status.is_running) {
            return
        }
        Write-RunLog "backend5001 still running folder=$($status.current_relative_dir) file=$($status.current_file)"
        Start-Sleep -Seconds $PollSeconds
    }
}

function Refresh-RiskAudit {
    $auditDir = Join-Path $OutputDir "_ocr_audit"
    $riskCsv = Join-Path $auditDir "distant_followme_risk_2026_latest.csv"
    $riskJson = Join-Path $auditDir "distant_followme_risk_2026_latest.json"
    & $Python "tools\audit_distant_followme_risk.py" `
        --output-dir $OutputDir `
        --year 2026 `
        --include-medium `
        --output-csv $riskCsv `
        --summary-json $riskJson *>> $LogPath
    if ($LASTEXITCODE -ne 0) {
        throw "risk audit failed with exit=$LASTEXITCODE"
    }
    $summary = Get-Content -LiteralPath $riskJson -Raw | ConvertFrom-Json
    Write-RunLog "risk audit rows=$($summary.risk_rows) csv=$riskCsv"
    return [pscustomobject]@{ Csv = $riskCsv; Json = $riskJson; Rows = [int]$summary.risk_rows }
}

function Start-UploaderIfNeeded {
    & $Python "tools\prepare_drive_upload_manifest.py" --output-dir $OutputDir --no-stage *>> $LogPath
    & $Python "tools\split_drive_review_required.py" --output-dir $OutputDir *>> $LogPath
    $pendingCsv = Join-Path $OutputDir "_drive_upload\drive_upload_ready_pending.csv"
    $pending = 0
    if (Test-Path -LiteralPath $pendingCsv) {
        $pending = @((Import-Csv -LiteralPath $pendingCsv)).Count
    }
    $uploader = Get-MatchingProcess "rclone_drive_upload.py|rclone.exe"
    if ($pending -gt 0 -and $uploader.Count -eq 0) {
        Write-RunLog "starting uploader pending=$pending"
        $uploadOut = Join-Path $OutputDir "_drive_upload\rclone_drive_upload_stdout.log"
        $uploadErr = Join-Path $OutputDir "_drive_upload\rclone_drive_upload_stderr.log"
        Start-Process -FilePath $Python `
            -WorkingDirectory $RepoRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $uploadOut `
            -RedirectStandardError $uploadErr `
            -ArgumentList @(
                "tools\rclone_drive_upload.py",
                "--output-dir", $OutputDir,
                "--execute",
                "--repeat",
                "--limit", "100",
                "--transfers", "4",
                "--checkers", "8",
                "--rclone-timeout-seconds", "1200"
            ) | Out-Null
    } else {
        Write-RunLog "uploader ok_or_not_needed process_count=$($uploader.Count) pending=$pending"
    }
}

Push-Location $RepoRoot
try {
    Write-RunLog "followme risk rerun waiter started"
    Wait-For-ExistingStagedRerun
    Wait-BackendIdle

    $risk = Refresh-RiskAudit
    if ($risk.Rows -le 0) {
        Write-RunLog "no distant FollowMe risk rows; refreshing upload manifest only"
        Start-UploaderIfNeeded
        exit 0
    }

    Stop-Backend5001
    Start-Backend5001

    $rerunStamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $candidateCsv = Join-Path $OutputDir "_ocr_audit\followme_risk_rerun_candidates_$rerunStamp.csv"
    $summaryCsv = Join-Path $OutputDir "_ocr_audit\followme_risk_rerun_summary_$rerunStamp.csv"
    Write-RunLog "starting FollowMe risk staged rerun rows=$($risk.Rows)"
    & $Python "tools\rerun_staged_candidates.py" `
        --source-root $SourceRoot `
        --output-dir $OutputDir `
        --backend-url $BackendUrl `
        --input-csv $risk.Csv `
        --output-csv $candidateCsv `
        --run-summary-csv $summaryCsv `
        --execute `
        --poll-seconds 20 `
        --timeout-minutes 360 `
        --min-completion-ratio 0.98 `
        --max-single-missing-ratio 0.65 *>> $LogPath
    $rerunExit = $LASTEXITCODE
    Write-RunLog "FollowMe risk staged rerun exit=$rerunExit summary=$summaryCsv"
    if ($rerunExit -ne 0) {
        exit $rerunExit
    }

    $after = Refresh-RiskAudit
    Write-RunLog "after rerun risk rows=$($after.Rows)"
    Start-UploaderIfNeeded
    Write-RunLog "followme risk rerun waiter done"
} finally {
    Pop-Location
}

param(
    [string]$RepoRoot = "",
    [string]$SourceRoot = "",
    [string]$OutputDir = "",
    [string]$BackendUrl = "http://127.0.0.1:5000",
    [int]$PollSeconds = 300,
    [int]$PrimaryPasses = 3,
    [string]$PrimaryModel = "qwen/qwen3-vl-8b",
    [string[]]$FinalModels = @("qwen3.5-9b-vlm", "qwen/qwen2.5-vl-7b", "gemma-4-12b-it-qat"),
    [bool]$CurrentYearFirst = $true,
    [bool]$RunAllYearsAfterCurrentYear = $true,
    [switch]$CurrentYearOnly,
    [switch]$SkipCurrentYearPhases,
    [switch]$SkipCurrentYearFirstPass,
    [switch]$AllowPlannedBackendUpgradeInterlock,
    [switch]$SkipRecursiveResume
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'
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
$BenchmarkLockPath = Join-Path $OutputDir "_ocr_audit\model_benchmark.lock"

function Write-RunLog {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "s"), $Message
    Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value $line
}

function Wait-ForBenchmarkLock {
    param([string]$Action)
    while (Test-Path -LiteralPath $BenchmarkLockPath) {
        if ($AllowPlannedBackendUpgradeInterlock) {
            try {
                $lock = Get-Content -LiteralPath $BenchmarkLockPath -Raw | ConvertFrom-Json
                $owner = Get-Process -Id ([int]$lock.pid) -ErrorAction SilentlyContinue
                if ($lock.purpose -eq "backend_upgrade_v1945" -and $owner) {
                    Write-RunLog "planned backend-upgrade interlock acknowledged; continuing recovery action=$Action owner=$($lock.pid)"
                    return
                }
            } catch {
                # Invalid or racing lock content remains fail-closed below.
            }
        }
        Write-RunLog "benchmark lock present; waiting before $Action path=$BenchmarkLockPath"
        Start-Sleep -Seconds ([math]::Max(1, [math]::Min($PollSeconds, 30)))
    }
}

function Get-MatchingProcess {
    param([string]$Pattern)
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -match $Pattern -and $_.CommandLine -notmatch "Get-CimInstance"
    })
}

function Get-OwnedMatchingProcess {
    param([string]$Pattern)
    @(Get-MatchingProcess $Pattern | Where-Object {
        $_.CommandLine -match [regex]::Escape($RepoRoot)
    })
}

function Stop-ExtraOwnedProcesses {
    param([string]$Pattern, [string]$Role)
    $owned = @(Get-OwnedMatchingProcess $Pattern | Sort-Object CreationDate)
    if ($owned.Count -le 1) { return $owned }
    foreach ($proc in @($owned | Select-Object -Skip 1)) {
        Write-RunLog "stopping duplicate owned $Role pid=$($proc.ProcessId)"
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
    return @(Get-OwnedMatchingProcess $Pattern | Select-Object -First 1)
}

function Get-BackendStatus {
    try {
        return Invoke-RestMethod -Uri "$BackendUrl/api/status" -TimeoutSec 10
    } catch {
        return $null
    }
}

function Stop-Backend {
    $backend = Get-OwnedMatchingProcess "samsung_ocr_batch_processor.py"
    foreach ($proc in $backend) {
        Write-RunLog "stopping backend pid=$($proc.ProcessId)"
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 3
}

function Start-Backend {
    param([string]$Model)
    Wait-ForBenchmarkLock "backend launch"
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
    $year = (Get-Date).Year
    $auditDir = Join-Path $OutputDir "_ocr_audit"
    $riskCsv = Join-Path $auditDir ("distant_followme_risk_{0}_latest.csv" -f $year)
    $riskJson = Join-Path $auditDir ("distant_followme_risk_{0}_latest.json" -f $year)
    $riskSample = Join-Path $auditDir ("distant_followme_risk_{0}_latest_sample.csv" -f $year)
    Write-RunLog "refreshing current-year FollowMe/distant risk audit"
    & $Python "tools\audit_distant_followme_risk.py" `
        --output-dir $OutputDir `
        --year $year `
        --include-medium `
        --output-csv $riskCsv `
        --summary-json $riskJson `
        --sample-csv $riskSample *>> $LogPath
    if ($LASTEXITCODE -ne 0) {
        throw "current-year risk audit failed; refusing to build upload manifest"
    }
    Write-RunLog "refreshing upload manifest"
    & $Python "tools\prepare_drive_upload_manifest.py" --output-dir $OutputDir --no-stage *>> $LogPath
    Write-RunLog "manifest refresh exit=$LASTEXITCODE"
    & $Python "tools\split_drive_review_required.py" --output-dir $OutputDir *>> $LogPath
    Write-RunLog "review split exit=$LASTEXITCODE"
}

function Rebuild-DriveCorrectionLedgerIfSafe {
    $builder = Join-Path $RepoRoot "tools\build_drive_correction_reconciliation.py"
    if (-not (Test-Path -LiteralPath $builder)) {
        Write-RunLog "drive correction ledger rebuild skipped; builder missing path=$builder"
        return
    }
    $year = (Get-Date).Year
    Write-RunLog "rebuilding Drive correction ledger from fresh manifest year=$year"
    $builderOutput = @(& $Python $builder --output-dir $OutputDir --year $year --execute 2>&1)
    $builderExit = $LASTEXITCODE
    $builderText = ($builderOutput -join "`n")
    if ($builderExit -ne 0) {
        Write-RunLog "drive correction ledger rebuild failed closed exit=$builderExit detail=$builderText"
        return
    }
    try {
        $summary = $builderText | ConvertFrom-Json
        Write-RunLog ("drive correction ledger rebuilt rows={0} ready={1} blocked={2} discover_old={3}" -f `
            $summary.ledger_rows, $summary.new_ready, $summary.gate_blocked, $summary.old_drive_id_discovery_required)
    } catch {
        Write-RunLog "drive correction ledger rebuild returned unreadable summary; ledger remains local-only detail=$builderText"
    }
}

function Start-Uploader-IfNeeded {
    Wait-ForBenchmarkLock "uploader launch/check"
    $pendingCsv = Join-Path $OutputDir "_drive_upload\drive_upload_ready_pending.csv"
    $pendingCount = 0
    if (Test-Path -LiteralPath $pendingCsv) {
        try { $pendingCount = @(Import-Csv -LiteralPath $pendingCsv).Count } catch { $pendingCount = 0 }
    }
    if ($pendingCount -le 0) {
        Write-RunLog "no ready upload pending rows"
        return
    }
    $uploader = Stop-ExtraOwnedProcesses "rclone_drive_upload.py|rclone.exe" "uploader"
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
                "--limit", "100",
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

function Start-Recursive-IfNeeded {
    Wait-ForBenchmarkLock "recursive launch/check"
    $recursive = Stop-ExtraOwnedProcesses "recursive_ocr_flat_export.py" "runner"
    if ($recursive.Count -gt 0) {
        Write-RunLog "recursive runner already active; not starting another"
        return
    }
    $recursiveOut = Join-Path $LogDir ("recursive_resume_after_questionable_{0}.out.log" -f $Stamp)
    $recursiveErr = Join-Path $LogDir ("recursive_resume_after_questionable_{0}.err.log" -f $Stamp)
    Write-RunLog "starting recursive OCR resume"
    Start-Process -FilePath $Python `
        -ArgumentList @(
            "tools\recursive_ocr_flat_export.py",
            "--source-root", $SourceRoot,
            "--output-dir", $OutputDir,
            "--backend-url", $BackendUrl,
            "--api-base", "http://127.0.0.1:1234/v1",
            "--api-key", "lm-studio",
            "--model", $PrimaryModel,
            "--poll-seconds", "20",
            "--timeout-minutes", "360"
        ) `
        -WorkingDirectory $RepoRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $recursiveOut `
        -RedirectStandardError $recursiveErr | Out-Null
}

function Invoke-QuestionablePass {
    param(
        [string]$Label,
        [string]$Model,
        [bool]$IncludeOlder = $false
    )
    Wait-ForBenchmarkLock "questionable scan"
    Ensure-BackendModel -Model $Model
    $safeLabel = $Label -replace "[^A-Za-z0-9_-]", "_"
    $candidateCsv = Join-Path $OutputDir ("_ocr_audit\questionable_rerun_candidates_{0}_{1}.csv" -f $safeLabel, $Stamp)
    $resultCsv = Join-Path $OutputDir ("_ocr_audit\questionable_rerun_results_{0}_{1}.csv" -f $safeLabel, $Stamp)
    $summaryCsv = Join-Path $OutputDir ("_ocr_audit\questionable_rerun_summary_{0}_{1}.csv" -f $safeLabel, $Stamp)
    Write-RunLog "scanning questionable rows label=$Label model=$Model includeOlder=$IncludeOlder"
    $scanArgs = @(
        "tools\rerun_questionable_records.py",
        "--source-root", $SourceRoot,
        "--output-dir", $OutputDir,
        "--backend-url", $BackendUrl,
        "--dry-run",
        "--output-csv", $candidateCsv,
        "--run-summary-csv", $summaryCsv
    )
    if ($IncludeOlder) {
        $scanArgs += "--include-older"
    }
    & $Python @scanArgs *>> $LogPath
    $scanExit = $LASTEXITCODE
    if ($scanExit -ne 0) {
        Write-RunLog "questionable scan label=$Label exit=$scanExit"
        exit $scanExit
    }

    $candidateCount = 0
    if (Test-Path -LiteralPath $candidateCsv) {
        $candidateCount = @(Import-Csv -LiteralPath $candidateCsv).Count
    }
    Write-RunLog "questionable scan label=$Label candidates=$candidateCount"
    if ($candidateCount -eq 0) {
        Refresh-UploadAndReviewSplit
        Start-Uploader-IfNeeded
        return
    }

    Wait-ForBenchmarkLock "staged rerun launch"

    # Folder-scope priority reruns can accidentally process non-candidates and
    # filename-only queues can leak across folders.  Staging gives every pass
    # an isolated directory containing only the audited candidate images.
    $stagedArgs = @(
        "tools\rerun_staged_candidates.py",
        "--source-root", $SourceRoot,
        "--output-dir", $OutputDir,
        "--backend-url", $BackendUrl,
        "--input-csv", $candidateCsv,
        "--output-csv", $resultCsv,
        "--run-summary-csv", $summaryCsv,
        "--execute",
        "--poll-seconds", "10",
        "--timeout-minutes", "360"
    )
    Write-RunLog "starting isolated staged rerun label=$Label candidates=$candidateCount"
    & $Python @stagedArgs *>> $LogPath
    $rerunExit = $LASTEXITCODE
    Write-RunLog "isolated staged rerun label=$Label exit=$rerunExit"
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
        Wait-ForBenchmarkLock "main loop"
        $recursive = Get-MatchingProcess "recursive_ocr_flat_export.py"
        $staged = Get-MatchingProcess "rerun_staged_candidates.py"
        $status = Get-BackendStatus
        if ($CurrentYearOnly -and $recursive.Count -gt 0 -and $status -and -not [bool]$status.is_running) {
            $watchProcesses = @(Get-OwnedMatchingProcess "recursive_ocr_flat_export.py" | Where-Object { $_.CommandLine -match "--watch" })
            if ($watchProcesses.Count -gt 0) {
                foreach ($proc in $watchProcesses) {
                    Write-RunLog "stopping idle recursive watch pid=$($proc.ProcessId) so current-year rerun can proceed"
                    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
                }
                Start-Sleep -Seconds 2
                continue
            }
        }
        if ($recursive.Count -eq 0 -and $staged.Count -eq 0) {
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
            Write-RunLog ("waiting; recursive={0}; staged={1}; backend_running={2}; folder={3}; {4}/{5}" -f `
                $recursive.Count, $staged.Count, [bool]$status.is_running, $status.current_relative_dir, $stats.processed, $stats.total)
        } else {
            Write-RunLog ("waiting; recursive={0}; staged={1}; backend unavailable" -f $recursive.Count, $staged.Count)
        }
        Start-Uploader-IfNeeded
        Start-Sleep -Seconds $PollSeconds
    }

    if ($CurrentYearFirst -and -not $SkipCurrentYearPhases) {
        if ($SkipCurrentYearFirstPass) {
            Write-RunLog "phase=current_year_first_pass skipped_by_recovery"
        } else {
            Write-RunLog "phase=current_year_first_pass"
            Invoke-QuestionablePass -Label "current_year_first_pass" -Model $PrimaryModel -IncludeOlder $false
        }
        Write-RunLog "phase=current_year_immediate_pass_2"
        Invoke-QuestionablePass -Label "current_year_immediate_pass_2" -Model $PrimaryModel -IncludeOlder $false
        Write-RunLog "phase=current_year_immediate_pass_3"
        Invoke-QuestionablePass -Label "current_year_immediate_pass_3" -Model $PrimaryModel -IncludeOlder $false
        Write-RunLog "phase=current_year_distant_followme_review"
        Invoke-QuestionablePass -Label "current_year_distant_followme_review" -Model $PrimaryModel -IncludeOlder $false
        Write-RunLog "phase=current_year_complete"
    }

    if ($RunAllYearsAfterCurrentYear -and -not $CurrentYearOnly) {
        Write-RunLog "all-year questionable rerun starts"
        for ($pass = 1; $pass -le $PrimaryPasses; $pass++) {
            Invoke-QuestionablePass -Label ("all_year_qwen_pass_{0}" -f $pass) -Model $PrimaryModel -IncludeOlder $true
        }
        Write-RunLog "all-year questionable rerun finished"
    }

    if (-not $CurrentYearOnly) {
        $availableModels = Get-AvailableModelIds
        foreach ($model in $FinalModels) {
            if ($availableModels -contains $model) {
                Invoke-QuestionablePass -Label ("final_{0}" -f $model) -Model $model -IncludeOlder $true
            } else {
                Write-RunLog "final model unavailable; skipped model=$model"
            }
        }
    }

    Refresh-UploadAndReviewSplit
    Rebuild-DriveCorrectionLedgerIfSafe
    Start-Uploader-IfNeeded
    if ($CurrentYearFirst -and -not $SkipCurrentYearPhases) {
        $markerPath = Join-Path $OutputDir "_ocr_audit\current_year_rerun_cycle_complete.json"
        [pscustomobject]@{
            completed_at = (Get-Date -Format "s")
            primary_model = $PrimaryModel
            primary_passes = $PrimaryPasses
            current_year_only = [bool]$CurrentYearOnly
        } | ConvertTo-Json | Set-Content -LiteralPath $markerPath -Encoding UTF8
        Write-RunLog "current-year rerun completion marker written path=$markerPath"
    }
    if (-not $CurrentYearOnly -and $SkipCurrentYearPhases) {
        $fullMarkerPath = Join-Path $OutputDir "_ocr_audit\full_project_rerun_cycle_complete.json"
        [pscustomobject]@{
            completed_at = (Get-Date -Format "s")
            primary_model = $PrimaryModel
            primary_passes = $PrimaryPasses
            all_year_questionable_review = $true
            final_model_review = $true
        } | ConvertTo-Json | Set-Content -LiteralPath $fullMarkerPath -Encoding UTF8
        Write-RunLog "full-project rerun completion marker written path=$fullMarkerPath"
    }
    if ($SkipRecursiveResume) {
        Write-RunLog "recursive OCR resume skipped; planned backend upgrade/backfill owns the next boundary"
    } else {
        Start-Recursive-IfNeeded
    }
    Write-RunLog "watcher finished"
} finally {
    Pop-Location
}

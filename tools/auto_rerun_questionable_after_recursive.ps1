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
$UploadGateProofBuilder = Join-Path $RepoRoot "tools\build_upload_gate_proof.py"
$UploadGateProofPath = Join-Path $OutputDir "_drive_upload\upload_gate_proof.json"
$HistoricalUploadAuthorizationPath = Join-Path $OutputDir "_ocr_audit\historical_upload_authorization.json"
$script:UploadCompleted = $false

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
    $manifestArgs = @("tools\prepare_drive_upload_manifest.py", "--output-dir", $OutputDir, "--no-stage")
    if ($CurrentYearFirst -and -not $SkipCurrentYearPhases) {
        $manifestArgs += @("--years", [string]$year)
    }
    & $Python @manifestArgs *>> $LogPath
    $manifestExit = $LASTEXITCODE
    Write-RunLog "manifest refresh exit=$manifestExit"
    if ($manifestExit -ne 0) {
        throw "upload manifest refresh failed; completion and upload remain blocked"
    }
    & $Python "tools\split_drive_review_required.py" --output-dir $OutputDir *>> $LogPath
    $splitExit = $LASTEXITCODE
    Write-RunLog "review split exit=$splitExit"
    if ($splitExit -ne 0) {
        throw "review split failed; completion and upload remain blocked"
    }
}

function Update-UploadGateProof {
    param([switch]$Required)
    if (-not (Test-Path -LiteralPath $UploadGateProofBuilder)) {
        if ($Required) { throw "upload gate proof builder missing: $UploadGateProofBuilder" }
        Write-RunLog "upload gate proof builder missing; uploader remains blocked"
        return $false
    }
    $proofOutput = @(& $Python $UploadGateProofBuilder --output-dir $OutputDir --execute 2>&1)
    $proofExit = $LASTEXITCODE
    $proofText = $proofOutput -join "`n"
    if ($proofExit -ne 0) {
        Write-RunLog "upload gate proof closed exit=$proofExit detail=$proofText"
        if ($Required) { throw "content-bound upload gate proof is not valid" }
        return $false
    }
    try {
        $summary = $proofText | ConvertFrom-Json
        if ($summary.valid -ne $true -or $summary.executed -ne $true) { throw "proof summary is not valid/executed" }
    } catch {
        if ($Required) { throw "upload gate proof summary unreadable: $proofText" }
        Write-RunLog "upload gate proof summary unreadable; uploader remains blocked"
        return $false
    }
    Write-RunLog "upload gate proof verified pending=$($summary.pending_count) audit=$($summary.audit_input_sha256)"
    return $true
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
    param([switch]$WaitForCompletion)
    Wait-ForBenchmarkLock "uploader launch/check"
    if (-not $WaitForCompletion) {
        Write-RunLog "uploader deferred until all configured review phases finish"
        return
    }
    if (-not (Update-UploadGateProof -Required:$WaitForCompletion)) {
        Write-RunLog "uploader blocked because exact content-bound proof is unavailable"
        return
    }
    $pendingCsv = Join-Path $OutputDir "_drive_upload\drive_upload_ready_pending.csv"
    $pendingCount = 0
    if (Test-Path -LiteralPath $pendingCsv) {
        try { $pendingCount = @(Import-Csv -LiteralPath $pendingCsv).Count } catch { $pendingCount = 0 }
    }
    if ($pendingCount -le 0) {
        Write-RunLog "no ready upload pending rows"
        $script:UploadCompleted = $true
        return
    }
    $uploader = Stop-ExtraOwnedProcesses "rclone_drive_upload.py|rclone.exe" "uploader"
    $process = $null
    if ($uploader.Count -eq 0) {
        Write-RunLog "starting content-bound rclone uploader"
        $uploadOut = Join-Path $OutputDir "_drive_upload\rclone_drive_upload_stdout.log"
        $uploadErr = Join-Path $OutputDir "_drive_upload\rclone_drive_upload_stderr.log"
        $process = Start-Process -FilePath $Python `
            -ArgumentList (@(
                "tools\rclone_drive_upload.py",
                "--output-dir", $OutputDir,
                "--execute",
                "--repeat",
                "--limit", "100",
                "--transfers", "4",
                "--checkers", "8",
                "--rclone-timeout-seconds", "1200"
            ) + $(if ($CurrentYearFirst -and -not $SkipCurrentYearPhases) { @("--years", [string](Get-Date).Year) } else { @() })) `
            -WorkingDirectory $RepoRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $uploadOut `
            -RedirectStandardError $uploadErr -PassThru
    } elseif ($WaitForCompletion) {
        $process = Get-Process -Id ([int]$uploader[0].ProcessId) -ErrorAction SilentlyContinue
    } else {
        Write-RunLog "uploader already active; not starting another"
    }
    if ($WaitForCompletion) {
        if (-not $process) { throw "uploader process could not be observed" }
        Write-RunLog "waiting for verified uploader completion pid=$($process.Id)"
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) { throw "uploader exited with code $($process.ExitCode)" }
        Refresh-UploadAndReviewSplit
        if (-not (Update-UploadGateProof -Required)) { throw "post-upload gate proof invalid" }
        $remaining = 0
        if (Test-Path -LiteralPath $pendingCsv) { $remaining = @(Import-Csv -LiteralPath $pendingCsv).Count }
        if ($remaining -ne 0) { throw "uploader stopped with $remaining verified rows still pending" }
        $script:UploadCompleted = $true
        Write-RunLog "verified uploader completed and pending manifest is empty"
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
            "--timeout-minutes", "10080"
        ) `
        -WorkingDirectory $RepoRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $recursiveOut `
        -RedirectStandardError $recursiveErr | Out-Null
}

function Get-FileSha256 {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return "" }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-FullProjectRecursiveComplete {
    $auditDir = Join-Path $OutputDir "_ocr_audit"
    $discoveryPath = Join-Path $auditDir "folder_discovery.csv"
    $summaryPath = Join-Path $auditDir "folder_summary.csv"
    if (-not (Test-Path -LiteralPath $discoveryPath) -or -not (Test-Path -LiteralPath $summaryPath)) {
        throw "full-project inventory or folder summary is missing"
    }
    $discovered = @(Import-Csv -LiteralPath $discoveryPath)
    $summaries = @(Import-Csv -LiteralPath $summaryPath)
    if ($discovered.Count -eq 0) { throw "full-project discovery inventory is empty" }
    $summaryByFolder = @{}
    foreach ($row in $summaries) { if ($row.folder) { $summaryByFolder[[string]$row.folder] = $row } }
    $errors = @()
    $discoveryKeys = @($discovered | ForEach-Object { [string]$_.folder })
    $summaryKeys = @($summaries | ForEach-Object { [string]$_.folder })
    if (@($discoveryKeys | Sort-Object -Unique).Count -ne $discoveryKeys.Count) { $errors += "duplicate_discovery_folder" }
    if (@($summaryKeys | Sort-Object -Unique).Count -ne $summaryKeys.Count) { $errors += "duplicate_summary_folder" }
    foreach ($key in $summaryKeys) {
        if ($key -and $key -notin $discoveryKeys) { $errors += "extra_summary:$key" }
    }
    foreach ($folder in $discovered) {
        $key = [string]$folder.folder
        if (-not $summaryByFolder.ContainsKey($key)) {
            $errors += "missing:$key"
            continue
        }
        $row = $summaryByFolder[$key]
        if ([string]$row.status -notin @("copied", "skipped_existing")) { $errors += "incomplete_$($row.status):$key" }
        if ([string]$row.image_count -ne [string]$folder.image_count) { $errors += "image_count_changed:$key" }
        if ([string]$row.source_latest_mtime -ne [string]$folder.latest_mtime) { $errors += "source_changed:$key" }
    }
    if ($errors.Count -gt 0) {
        throw "full-project recursive evidence incomplete: $($errors[0..([math]::Min(9,$errors.Count-1))] -join '; ')"
    }
    return [pscustomobject]@{
        discovered_folder_count = $discovered.Count
        completed_folder_count = $discovered.Count
        error_count = 0
        folder_discovery_sha256 = Get-FileSha256 $discoveryPath
        folder_summary_sha256 = Get-FileSha256 $summaryPath
    }
}

function Write-HistoricalUploadAuthorization {
    $currentMarkerPath = Join-Path $OutputDir "_ocr_audit\current_year_rerun_cycle_complete.json"
    if (-not (Test-Path -LiteralPath $currentMarkerPath -PathType Leaf)) {
        throw "historical upload blocked: current-year completion marker is missing"
    }
    try { $currentMarker = Get-Content -LiteralPath $currentMarkerPath -Raw | ConvertFrom-Json } catch {
        throw "historical upload blocked: current-year completion marker is unreadable"
    }
    if ([int]$currentMarker.pending_count -ne 0 -or -not [string]$currentMarker.audit_input_sha256 -or -not [string]$currentMarker.backfill_run_id) {
        throw "historical upload blocked: current-year marker lacks exact zero-pending authority"
    }
    $recursiveProof = Assert-FullProjectRecursiveComplete
    $auditDir = Join-Path $OutputDir "_ocr_audit"
    $discoveryPath = Join-Path $auditDir "folder_discovery.csv"
    $summaryPath = Join-Path $auditDir "folder_summary.csv"
    [pscustomobject]@{
        schema = "samsung-ocr-historical-upload-authorization/v1"
        generated_at = (Get-Date).ToString("o")
        all_year_questionable_review = $true
        final_model_review = $true
        current_year_marker_path = $currentMarkerPath
        current_year_marker_sha256 = Get-FileSha256 $currentMarkerPath
        folder_discovery_path = $discoveryPath
        folder_discovery_sha256 = $recursiveProof.folder_discovery_sha256
        folder_summary_path = $summaryPath
        folder_summary_sha256 = $recursiveProof.folder_summary_sha256
        discovered_folder_count = $recursiveProof.discovered_folder_count
        completed_folder_count = $recursiveProof.completed_folder_count
        error_count = $recursiveProof.error_count
    } | ConvertTo-Json | Set-Content -LiteralPath $HistoricalUploadAuthorizationPath -Encoding UTF8
    Write-RunLog "historical upload authorization written path=$HistoricalUploadAuthorizationPath"
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
        "--timeout-minutes", "10080"
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
    if ($CurrentYearFirst -and -not $SkipCurrentYearPhases) {
        Remove-Item -LiteralPath $HistoricalUploadAuthorizationPath -Force -ErrorAction SilentlyContinue
    }
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

    if (-not $CurrentYearOnly -and $SkipCurrentYearPhases) {
        Write-HistoricalUploadAuthorization
    }
    Refresh-UploadAndReviewSplit
    Rebuild-DriveCorrectionLedgerIfSafe
    if (-not (Update-UploadGateProof -Required)) { throw "current-year upload proof missing after final review" }
    Start-Uploader-IfNeeded -WaitForCompletion
    if ($CurrentYearFirst -and -not $SkipCurrentYearPhases) {
        if (-not $script:UploadCompleted) { throw "current-year verified uploads are not complete" }
        $gate = Get-Content -LiteralPath $UploadGateProofPath -Raw | ConvertFrom-Json
        $markerPath = Join-Path $OutputDir "_ocr_audit\current_year_rerun_cycle_complete.json"
        [pscustomobject]@{
            completed_at = (Get-Date -Format "s")
            primary_model = $PrimaryModel
            primary_passes = $PrimaryPasses
            current_year_only = [bool]$CurrentYearOnly
            upload_gate_schema = [string]$gate.schema
            audit_input_sha256 = [string]$gate.audit_input_sha256
            manifest_summary_sha256 = [string]$gate.manifest_summary_sha256
            pending_sha256 = [string]$gate.pending_sha256
            pending_count = [int]$gate.pending_count
            backfill_run_id = [string]$gate.backfill_run_id
        } | ConvertTo-Json | Set-Content -LiteralPath $markerPath -Encoding UTF8
        Write-RunLog "current-year rerun completion marker written path=$markerPath"
    }
    if (-not $CurrentYearOnly -and $SkipCurrentYearPhases) {
        $recursiveProof = Assert-FullProjectRecursiveComplete
        $fullMarkerPath = Join-Path $OutputDir "_ocr_audit\full_project_rerun_cycle_complete.json"
        [pscustomobject]@{
            completed_at = (Get-Date -Format "s")
            primary_model = $PrimaryModel
            primary_passes = $PrimaryPasses
            all_year_questionable_review = $true
            final_model_review = $true
            discovered_folder_count = $recursiveProof.discovered_folder_count
            completed_folder_count = $recursiveProof.completed_folder_count
            error_count = $recursiveProof.error_count
            folder_discovery_sha256 = $recursiveProof.folder_discovery_sha256
            folder_summary_sha256 = $recursiveProof.folder_summary_sha256
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

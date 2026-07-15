param(
    [string]$RepoRoot = "",
    [string]$SourceRoot = "",
    [string]$OutputDir = "",
    [string]$BackendUrl = "http://127.0.0.1:5000",
    [string]$ApiBase = "http://127.0.0.1:1234/v1",
    [string]$Model = "qwen/qwen3-vl-8b",
    [int]$RcloneTimeoutSeconds = 1200,
    [int]$OcrStallMinutes = 120,
    [int]$UploadGateProofMaxAgeMinutes = 30
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
$GateProofPath = Join-Path $OutputDir "_drive_upload\upload_gate_proof.json"
$RuntimeHealthFusePath = Join-Path $OutputDir "_ocr_audit\runtime_health_fuse.json"
$script:LastAuditExit = -1

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

function Get-StagedRerunProcesses {
    @(Get-MatchingProcess "rerun_staged_candidates.py")
}

function Get-CurrentYearReviewCount {
    $reviewCsv = Join-Path $OutputDir "_drive_upload\drive_upload_review_required.csv"
    if (-not (Test-Path -LiteralPath $reviewCsv)) { return 0 }
    $markerPath = Join-Path $OutputDir "_ocr_audit\current_year_rerun_cycle_complete.json"
    if (Test-Path -LiteralPath $markerPath) {
        $currentYearText = (Get-Date).Year.ToString()
        $newestAuditWrite = [datetime]::MinValue
        $auditDir = Join-Path $OutputDir "_ocr_audit"
        foreach ($folder in @(Get-ChildItem -LiteralPath $auditDir -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -match $currentYearText })) {
            foreach ($name in @("success_records.csv", "rename_plan.csv")) {
                $path = Join-Path $folder.FullName $name
                if (Test-Path -LiteralPath $path) {
                    $writeTime = (Get-Item -LiteralPath $path).LastWriteTime
                    if ($writeTime -gt $newestAuditWrite) { $newestAuditWrite = $writeTime }
                }
            }
        }
        if ((Get-Item -LiteralPath $markerPath).LastWriteTime -ge $newestAuditWrite) {
            return 0
        }
    }
    $currentYear = [int](Get-Date).Year
    try {
        $rows = @(Import-Csv -LiteralPath $reviewCsv)
    } catch {
        return 0
    }
    $count = 0
    foreach ($row in $rows) {
        $yearText = [string]$row.year
        $periodText = [string]$row.period
        $rowYear = 0
        if ($yearText -match "^\d{4}$") {
            $rowYear = [int]$yearText
        } elseif ($periodText -match "^(20\d{2})") {
            $rowYear = [int]$Matches[1]
        }
        $reasons = [string]$row.reasons
        $needsOcrRerun = (
            $reasons -match "current_year_distant_view_needs_rerun" -or
            $reasons -match "current_year_followme_or_distant_risk_needs_rerun" -or
            $reasons -match "current_year_risk_audit_missing_or_stale" -or
            $reasons -match "current_year_missing_price" -or
            $reasons -match "name_contains_"
        )
        if ($rowYear -ge $currentYear -and $needsOcrRerun) {
            $count++
        }
    }
    return $count
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

function Write-JsonFile($Path, $Value) {
    $Value | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Restart-OcrIfStalled {
    $staged = Get-StagedRerunProcesses
    if ($staged.Count -gt 0) {
        Write-RunLog "staged rerun active process_count=$($staged.Count); not applying OCR stall restart"
        return
    }

    $status = Get-BackendStatus
    if (-not $status -or -not [bool]$status.is_running) {
        return
    }

    $auditDir = Join-Path $OutputDir "_ocr_audit"
    New-Item -ItemType Directory -Force -Path $auditDir | Out-Null
    $statePath = Join-Path $auditDir "watchdog_ocr_progress_state.json"
    $overall = $status.overall_progress
    $processed = if ($overall -and $null -ne $overall.processed_images) { [int]$overall.processed_images } else { -1 }
    $ready = if ($overall -and $null -ne $overall.ready_images) { [int]$overall.ready_images } else { -1 }
    $key = "{0}|{1}|{2}|{3}" -f $processed, $ready, $status.current_relative_dir, $status.current_file
    $now = Get-Date

    $previous = $null
    if (Test-Path -LiteralPath $statePath) {
        try {
            $previous = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        } catch {
            $previous = $null
        }
    }

    if (-not $previous -or $previous.key -ne $key) {
        Write-JsonFile $statePath ([pscustomobject]@{
            key = $key
            first_seen = $now.ToString("s")
            last_seen = $now.ToString("s")
            processed = $processed
            ready = $ready
            folder = $status.current_relative_dir
            file = $status.current_file
        })
        Write-RunLog "ocr progress heartbeat updated processed=$processed ready=$ready"
        return
    }

    $firstSeen = [datetime]$previous.first_seen
    $staleMinutes = ($now - $firstSeen).TotalMinutes
    Write-JsonFile $statePath ([pscustomobject]@{
        key = $key
        first_seen = $previous.first_seen
        last_seen = $now.ToString("s")
        processed = $processed
        ready = $ready
        folder = $status.current_relative_dir
        file = $status.current_file
        stale_minutes = [math]::Round($staleMinutes, 1)
    })

    if ($staleMinutes -lt $OcrStallMinutes) {
        Write-RunLog "ocr progress unchanged stale_minutes=$([math]::Round($staleMinutes,1)) threshold=$OcrStallMinutes"
        return
    }

    Write-RunLog "ocr appears stalled stale_minutes=$([math]::Round($staleMinutes,1)); restarting backend and recursive runner"
    $targets = @(Get-MatchingProcess "recursive_ocr_flat_export.py|samsung_ocr_batch_processor.py")
    foreach ($proc in $targets) {
        Write-RunLog "stopping stalled OCR process pid=$($proc.ProcessId)"
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $statePath -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 5
}

function Get-CsvRowCount([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return 0 }
    try {
        return @((Import-Csv -LiteralPath $Path)).Count
    } catch {
        return 0
    }
}

function Get-FileSha256([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return "" }
    try { return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() } catch { return "" }
}

function Get-TextSha256([string]$Text) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::ASCII.GetBytes($Text)
        return ([System.BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Test-CurrentAuditProof([string]$RiskJson, [string]$RiskCsv) {
    if (-not (Test-Path -LiteralPath $RiskJson -PathType Leaf) -or -not (Test-Path -LiteralPath $RiskCsv -PathType Leaf)) { return $false }
    try { $risk = Get-Content -LiteralPath $RiskJson -Raw | ConvertFrom-Json } catch { return $false }
    $proof = $risk.finalization_proof
    if ($risk.audit_complete -ne $true -or -not $proof -or $proof.audit_complete -ne $true -or $proof.complete -ne $true) { return $false }
    if (-not $risk.audit_input_sha256 -or $risk.audit_input_sha256 -ne $proof.audit_input_sha256) { return $false }
    if ((Get-FileSha256 $RiskCsv) -ne ([string]$risk.risk_output_sha256).ToLowerInvariant()) { return $false }
    try {
        $expected = [int]$proof.expected_candidate_count
        $scanned = [int]$proof.scanned_result_count
        $duplicates = [int]$proof.duplicate_source_identity
    } catch { return $false }
    if ($expected -le 0 -or $scanned -ne $expected -or $duplicates -ne 0 -or @($proof.missing_or_invalid).Count -ne 0) { return $false }
    $inputPaths = @([string]$proof.candidate_csv, [string]$proof.candidate_summary_json)
    if ([int]$proof.candidate_rows -gt 0) {
        $inputPaths += @([string]$proof.result_csv, [string]$proof.run_summary_csv)
    }
    foreach ($path in $inputPaths) {
        if (-not (Get-FileSha256 $path)) { return $false }
    }
    if (-not $proof.backfill_run_id -or [string]$proof.backfill_run_id -ne [string]$risk.backfill_run_id) { return $false }
    return $true
}

function Write-UploadGateProof($Proof) {
    $parent = Split-Path -Parent $GateProofPath
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $temp = "$GateProofPath.tmp.$PID"
    $Proof | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temp -Encoding UTF8
    Move-Item -LiteralPath $temp -Destination $GateProofPath -Force
}

function Test-UploadGateProof {
    if (Test-Path -LiteralPath $RuntimeHealthFusePath) { return $false }
    if (-not (Test-Path -LiteralPath $GateProofPath -PathType Leaf)) { return $false }
    try { $gate = Get-Content -LiteralPath $GateProofPath -Raw | ConvertFrom-Json } catch { return $false }
    if ($gate.schema -ne "samsung-ocr-upload-gate/v1" -or $gate.gate_open -ne $true) { return $false }
    try { $gateAge = ((Get-Date) - [datetime]$gate.generated_at).TotalMinutes } catch { return $false }
    if ($gateAge -lt 0 -or $gateAge -gt $UploadGateProofMaxAgeMinutes) { return $false }

    $riskJson = [string]$gate.audit_summary_path
    $riskCsv = [string]$gate.risk_csv_path
    $manifestSummaryPath = [string]$gate.manifest_summary_path
    $pendingCsv = [string]$gate.pending_csv_path
    $nextBatchCsv = [string]$gate.next_batch_csv_path
    foreach ($path in @($riskJson, $riskCsv, $manifestSummaryPath, $pendingCsv, $nextBatchCsv)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $false }
    }
    if ((Get-FileSha256 $riskJson) -ne ([string]$gate.audit_summary_sha256).ToLowerInvariant()) { return $false }
    if ((Get-FileSha256 $riskCsv) -ne ([string]$gate.risk_output_sha256).ToLowerInvariant()) { return $false }
    if ((Get-FileSha256 $manifestSummaryPath) -ne ([string]$gate.manifest_summary_sha256).ToLowerInvariant()) { return $false }
    if ((Get-FileSha256 $pendingCsv) -ne ([string]$gate.pending_sha256).ToLowerInvariant()) { return $false }
    if ((Get-FileSha256 $nextBatchCsv) -ne ([string]$gate.next_batch_sha256).ToLowerInvariant()) { return $false }
    foreach ($input in @($gate.audit_inputs)) {
        if (-not $input.path -or (Get-FileSha256 ([string]$input.path)) -ne ([string]$input.sha256).ToLowerInvariant()) { return $false }
    }
    if (-not (Test-CurrentAuditProof $riskJson $riskCsv)) { return $false }
    try { $summary = Get-Content -LiteralPath $manifestSummaryPath -Raw | ConvertFrom-Json } catch { return $false }
    if ($summary.current_year_risk_audit_fresh -ne $true -or $summary.current_year_upload_gate_open -ne $true) { return $false }
    if ([string]$summary.current_audit_input_sha256 -ne [string]$gate.audit_input_sha256) { return $false }
    if ([string]$summary.next_batch_sha256 -ne (Get-FileSha256 $nextBatchCsv)) { return $false }
    $pendingRows = @(Import-Csv -LiteralPath $pendingCsv)
    $blocked = @($pendingRows | Where-Object { $_.status -ne "ready" -or -not [string]::IsNullOrWhiteSpace([string]$_.reasons) })
    if ($blocked.Count -gt 0) { return $false }
    if ([int]$summary.ready_pending -ne $pendingRows.Count -or [int]$summary.next_batch -ne $pendingRows.Count) { return $false }
    if ([int]$gate.pending_count -ne $pendingRows.Count) { return $false }
    if ((Get-Item -LiteralPath $manifestSummaryPath).LastWriteTimeUtc -lt (Get-Item -LiteralPath $riskJson).LastWriteTimeUtc) { return $false }
    return $true
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
    $staged = Get-StagedRerunProcesses
    if ($staged.Count -gt 0) {
        Write-RunLog "staged rerun active process_count=$($staged.Count); not starting recursive"
        return
    }

    $currentYearReview = Get-CurrentYearReviewCount
    if ($currentYearReview -gt 0) {
        $recursive = Get-MatchingProcess "recursive_ocr_flat_export.py"
        $status = Get-BackendStatus
        if ($recursive.Count -gt 0 -and $status -and -not [bool]$status.is_running) {
            foreach ($proc in @($recursive | Where-Object { $_.CommandLine -match "--watch" })) {
                Write-RunLog "stopping idle recursive watch pid=$($proc.ProcessId); current-year rerun has priority"
                Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
            }
        }
        Write-RunLog "current-year review gate active rows=$currentYearReview; not starting recursive"
        return
    }

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
            "--timeout-minutes", "10080",
            "--watch",
            "--watch-sleep-seconds", "300"
        ) | Out-Null
}

function Start-AutoRerunWatcherIfNeeded {
    $staged = Get-StagedRerunProcesses
    if ($staged.Count -gt 0) {
        Write-RunLog "staged rerun active process_count=$($staged.Count); not starting questionable watcher"
        return
    }

    $currentYearReview = Get-CurrentYearReviewCount

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
    if ($currentYearReview -gt 0) {
        Write-RunLog "starting current-year priority questionable watcher rows=$currentYearReview"
    } else {
        Write-RunLog "starting post-recursive questionable watcher"
    }
    $watcherArgs = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "tools\auto_rerun_questionable_after_recursive.ps1",
        "-RepoRoot", $RepoRoot,
        "-SourceRoot", $SourceRoot,
        "-OutputDir", $OutputDir,
        "-BackendUrl", $BackendUrl,
        "-PollSeconds", "300",
        "-PrimaryPasses", "2"
    )
    if ($currentYearReview -gt 0) {
        $watcherArgs += "-CurrentYearOnly"
    }
    Start-Process -FilePath "powershell.exe" `
        -WorkingDirectory $RepoRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -ArgumentList $watcherArgs | Out-Null
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
    if (-not (Test-UploadGateProof)) {
        Write-RunLog "upload gate closed or proof/hash validation failed; uploader skipped"
        return
    }
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
            "--limit", "100",
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

function Run-DistantFollowMeAudit {
    $year = (Get-Date).Year
    $auditDir = Join-Path $OutputDir "_ocr_audit"
    New-Item -ItemType Directory -Force -Path $auditDir | Out-Null
    $riskCsv = Join-Path $auditDir ("distant_followme_risk_{0}_latest.csv" -f $year)
    $riskJson = Join-Path $auditDir ("distant_followme_risk_{0}_latest.json" -f $year)
    $sampleCsv = Join-Path $auditDir ("distant_followme_risk_{0}_latest_sample.csv" -f $year)
    & $Python "tools\audit_distant_followme_risk.py" `
        --output-dir $OutputDir `
        --year $year `
        --include-medium `
        --output-csv $riskCsv `
        --summary-json $riskJson `
        --sample-csv $sampleCsv *>> $LogPath
    $script:LastAuditExit = $LASTEXITCODE
    Write-RunLog "distant FollowMe risk audit exit=$script:LastAuditExit"
    if (Test-Path -LiteralPath $riskJson) {
        try {
            $risk = Get-Content -LiteralPath $riskJson -Raw | ConvertFrom-Json
            $uploadedRisk = 0
            foreach ($property in $risk.counts.PSObject.Properties) {
                if ($property.Name -like "*_uploaded") {
                    $uploadedRisk += [int]$property.Value
                }
            }
            Write-RunLog ("distant quality risk rows={0} distant_total={1} risk_rate={2} uploaded_risk={3} csv={4} sample={5}" -f `
                $risk.risk_rows, $risk.counts.distant_total, $risk.risk_rate, $uploadedRisk, $risk.output_csv, $risk.sample_csv)
        } catch {
            Write-RunLog "distant FollowMe risk summary unreadable"
        }
    }
}

function Update-UploadGateProof {
    # Invalidate any prior approval before refreshing its authorities.  A
    # failed audit or manifest rebuild must leave no reusable open gate.
    Remove-Item -LiteralPath $GateProofPath -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $RuntimeHealthFusePath) {
        Write-RunLog "upload gate closed: runtime health fuse active"
        return
    }
    Run-DistantFollowMeAudit
    $year = (Get-Date).Year
    $auditDir = Join-Path $OutputDir "_ocr_audit"
    $manifestDir = Join-Path $OutputDir "_drive_upload"
    $riskCsv = Join-Path $auditDir ("distant_followme_risk_{0}_latest.csv" -f $year)
    $riskJson = Join-Path $auditDir ("distant_followme_risk_{0}_latest.json" -f $year)
    if ($script:LastAuditExit -ne 0 -or -not (Test-CurrentAuditProof $riskJson $riskCsv)) {
        Write-RunLog "upload gate closed: audit/finalization proof incomplete"
        return
    }

    Write-RunLog "upload gate audit proof verified; rebuilding manifest"
    & $Python "tools\prepare_drive_upload_manifest.py" --output-dir $OutputDir --no-stage *>> $LogPath
    $manifestExit = $LASTEXITCODE
    if ($manifestExit -ne 0) {
        Write-RunLog "upload gate closed: manifest rebuild failed exit=$manifestExit"
        return
    }

    $manifestSummaryPath = Join-Path $manifestDir "drive_upload_summary.json"
    $pendingCsv = Join-Path $manifestDir "drive_upload_ready_pending.csv"
    $nextBatchCsv = Join-Path $manifestDir "drive_upload_next_batch.csv"
    if (-not (Test-Path -LiteralPath $manifestSummaryPath -PathType Leaf) -or -not (Test-Path -LiteralPath $pendingCsv -PathType Leaf) -or -not (Test-Path -LiteralPath $nextBatchCsv -PathType Leaf)) {
        Write-RunLog "upload gate closed: manifest summary or pending CSV missing"
        return
    }
    try {
        $risk = Get-Content -LiteralPath $riskJson -Raw | ConvertFrom-Json
        $summary = Get-Content -LiteralPath $manifestSummaryPath -Raw | ConvertFrom-Json
        $pendingRows = @(Import-Csv -LiteralPath $pendingCsv)
    } catch {
        Write-RunLog "upload gate closed: audit/summary/manifest unreadable"
        return
    }
    $blocked = @($pendingRows | Where-Object { $_.status -ne "ready" -or -not [string]::IsNullOrWhiteSpace([string]$_.reasons) })
    if ($summary.current_year_risk_audit_fresh -ne $true -or $summary.current_year_upload_gate_open -ne $true -or [string]$summary.current_audit_input_sha256 -ne [string]$risk.audit_input_sha256 -or [string]$summary.next_batch_sha256 -ne (Get-FileSha256 $nextBatchCsv) -or $blocked.Count -gt 0 -or [int]$summary.ready_pending -ne $pendingRows.Count -or [int]$summary.next_batch -ne $pendingRows.Count) {
        Write-RunLog "upload gate closed: manifest gate/count validation failed pending=$($pendingRows.Count) blocked=$($blocked.Count)"
        return
    }
    if ((Get-Item -LiteralPath $manifestSummaryPath).LastWriteTimeUtc -lt (Get-Item -LiteralPath $riskJson).LastWriteTimeUtc) {
        Write-RunLog "upload gate closed: manifest summary predates audit proof"
        return
    }

    $proof = $risk.finalization_proof
    $auditInputs = @()
    $proofInputPaths = @([string]$proof.candidate_csv, [string]$proof.candidate_summary_json)
    if ([int]$proof.candidate_rows -gt 0) {
        $proofInputPaths += @([string]$proof.result_csv, [string]$proof.run_summary_csv)
    }
    foreach ($path in $proofInputPaths) {
        $hash = Get-FileSha256 $path
        if (-not $hash) {
            Write-RunLog "upload gate closed: finalization input missing"
            return
        }
        $auditInputs += [pscustomobject]@{ path=$path; sha256=$hash }
    }
    $gate = [ordered]@{
        schema = "samsung-ocr-upload-gate/v1"
        generated_at = (Get-Date).ToString("o")
        gate_open = $true
        audit_summary_path = $riskJson
        audit_summary_sha256 = Get-FileSha256 $riskJson
        risk_csv_path = $riskCsv
        risk_output_sha256 = Get-FileSha256 $riskCsv
        audit_input_sha256 = [string]$risk.audit_input_sha256
        audit_inputs = $auditInputs
        manifest_summary_path = $manifestSummaryPath
        manifest_summary_sha256 = Get-FileSha256 $manifestSummaryPath
        pending_csv_path = $pendingCsv
        pending_sha256 = Get-FileSha256 $pendingCsv
        pending_count = $pendingRows.Count
        next_batch_csv_path = $nextBatchCsv
        next_batch_sha256 = Get-FileSha256 $nextBatchCsv
    }
    Write-UploadGateProof $gate
    if (-not (Test-UploadGateProof)) {
        Remove-Item -LiteralPath $GateProofPath -Force -ErrorAction SilentlyContinue
        Write-RunLog "upload gate closed: post-write proof/hash validation failed"
        return
    }
    Write-RunLog "upload gate open: audit proof, manifest gate, and pending hash verified pending=$($pendingRows.Count)"
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LockPath) | Out-Null
if (Test-Path -LiteralPath $RuntimeHealthFusePath) {
    Remove-Item -LiteralPath $GateProofPath -Force -ErrorAction SilentlyContinue
    Write-RunLog "watchdog fail-closed: runtime health fuse active"
    exit 9
}
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
    Restart-OcrIfStalled
    Start-BackendIfNeeded
    Start-RecursiveIfNeeded
    Start-AutoRerunWatcherIfNeeded
    Update-UploadGateProof
    Start-UploaderIfNeeded
    Log-Progress
    Write-RunLog "watchdog done"
} finally {
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
}

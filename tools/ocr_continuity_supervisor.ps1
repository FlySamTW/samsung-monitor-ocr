param(
    [string]$RepoRoot,
    [string]$SourceRoot,
    [string]$OutputDir,
    [string]$BackendUrl = "http://127.0.0.1:5000",
    [string]$ApiBase = "http://127.0.0.1:1234/v1",
    [string]$Model = "qwen/qwen3-vl-8b",
    [int]$ContextLength = 32768,
    [int]$UploadGateProofMaxAgeMinutes = 30
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$audit = Join-Path $OutputDir "_ocr_audit"
$BenchmarkLockPath = Join-Path $audit "model_benchmark.lock"
$RuntimeHealthFusePath = Join-Path $audit "runtime_health_fuse.json"
$logDir = Join-Path $RepoRoot "logs"
$lockPath = Join-Path $audit "ocr_continuity_supervisor.lock"
$alertPath = Join-Path $audit "ocr_continuity_supervisor_alert.json"
$fullProjectRequestPath = Join-Path $audit "full_project_continuation_requested.json"
$currentYearCompletePath = Join-Path $audit "current_year_rerun_cycle_complete.json"
$fullProjectCompletePath = Join-Path $audit "full_project_rerun_cycle_complete.json"
$uploadGateProofPath = Join-Path $OutputDir "_drive_upload\upload_gate_proof.json"
$logPath = Join-Path $logDir ("ocr_continuity_supervisor_{0}.jsonl" -f (Get-Date -Format "yyyyMMdd"))
$python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
New-Item -ItemType Directory -Force -Path $audit,$logDir | Out-Null

function Log-Event([string]$Event, [hashtable]$Data = @{}) {
    $payload = [ordered]@{ timestamp=(Get-Date).ToString("o"); event=$Event; pid=$PID }
    foreach ($k in $Data.Keys) { $payload[$k] = $Data[$k] }
    $payload | ConvertTo-Json -Compress | Add-Content -LiteralPath $logPath -Encoding UTF8
}
function Alert([string]$Reason, [hashtable]$Data = @{}) {
    $payload = [ordered]@{ timestamp=(Get-Date).ToString("o"); status="fail_closed"; reason=$Reason; repo_root=$RepoRoot }
    foreach ($k in $Data.Keys) { $payload[$k] = $Data[$k] }
    $payload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $alertPath -Encoding UTF8
    Log-Event "alert" (@{reason=$Reason} + $Data)
}
function Owned([string]$Pattern) {
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and $_.CommandLine -match $Pattern -and
        $_.CommandLine -match [regex]::Escape($RepoRoot) -and
        $_.CommandLine -notmatch "Get-CimInstance|ocr_continuity_supervisor"
    })
}
function Get-BackendStatus {
    try { return Invoke-RestMethod -Uri "$BackendUrl/api/status" -TimeoutSec 8 } catch { return $null }
}
function Get-LmModels {
    try { return Invoke-RestMethod -Uri ($ApiBase.TrimEnd('/') + "/models") -TimeoutSec 5 } catch { return $null }
}
function Invoke-Lms([string[]]$Args) {
    $lms = Join-Path $env:USERPROFILE ".lmstudio\bin\lms.exe"
    if (-not (Test-Path $lms)) { return $null }
    $out = & $lms @Args 2>&1
    return [pscustomobject]@{ exit=$LASTEXITCODE; output=($out -join "`n") }
}
function Start-Hidden([string]$File, [string[]]$ProcessArgs, [string]$OutFile, [string]$ErrFile) {
    if (-not $File -or -not $ProcessArgs -or $ProcessArgs.Count -eq 0 -or $ProcessArgs.Where({ $null -eq $_ -or [string]$_ -eq "" }).Count -gt 0) {
        throw "hidden process launch contains an empty executable or argument"
    }
    if (-not $OutFile -or -not $ErrFile) { throw "hidden process launch requires output paths" }
    Start-Process -FilePath $File -ArgumentList $ProcessArgs -WorkingDirectory $RepoRoot -WindowStyle Hidden `
        -RedirectStandardOutput $OutFile -RedirectStandardError $ErrFile | Out-Null
}
function Read-JsonFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try { return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json } catch { return $null }
}
function Get-FileSha256([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return "" }
    try { return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() } catch { return "" }
}
function Get-CsvRowCount([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return -1 }
    try { return @((Import-Csv -LiteralPath $Path)).Count } catch { return -1 }
}
function Test-UploadGateProof {
    $gate = Read-JsonFile $uploadGateProofPath
    if (-not $gate -or $gate.schema -ne "samsung-ocr-upload-gate/v1" -or $gate.gate_open -ne $true) { return $false }
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

    $risk = Read-JsonFile $riskJson
    $summary = Read-JsonFile $manifestSummaryPath
    if (-not $risk -or $risk.audit_complete -ne $true -or $risk.finalization_proof.audit_complete -ne $true -or $risk.finalization_proof.complete -ne $true) { return $false }
    if ([string]$risk.audit_input_sha256 -ne [string]$gate.audit_input_sha256 -or [string]$risk.audit_input_sha256 -ne [string]$risk.finalization_proof.audit_input_sha256) { return $false }
    if ($summary.current_year_risk_audit_fresh -ne $true -or $summary.current_year_upload_gate_open -ne $true) { return $false }
    if ([string]$summary.current_audit_input_sha256 -ne [string]$gate.audit_input_sha256) { return $false }
    if ([string]$summary.next_batch_sha256 -ne (Get-FileSha256 $nextBatchCsv)) { return $false }
    try {
        $pendingRows = @(Import-Csv -LiteralPath $pendingCsv)
        $nextBatchRows = @(Import-Csv -LiteralPath $nextBatchCsv)
    } catch { return $false }
    $blocked = @($pendingRows | Where-Object { $_.status -ne "ready" -or -not [string]::IsNullOrWhiteSpace([string]$_.reasons) })
    if ($blocked.Count -gt 0) { return $false }
    if ([int]$summary.ready_pending -ne $pendingRows.Count -or [int]$summary.next_batch -ne $nextBatchRows.Count) { return $false }
    if ([int]$gate.pending_count -ne $pendingRows.Count) { return $false }
    if ($null -eq $gate.next_batch_count -or [int]$gate.next_batch_count -ne $nextBatchRows.Count -or $nextBatchRows.Count -gt $pendingRows.Count) { return $false }
    if ($pendingRows.Count -gt 0 -and $nextBatchRows.Count -eq 0) { return $false }
    $trustedBatchFields = @("source_path", "file_name", "year", "period", "drive_folder", "size_bytes", "content_sha256", "status", "reasons")
    for ($index = 0; $index -lt $nextBatchRows.Count; $index++) {
        foreach ($field in $trustedBatchFields) {
            if ([string]$nextBatchRows[$index].$field -ne [string]$pendingRows[$index].$field) { return $false }
        }
    }
    if ((Get-Item -LiteralPath $manifestSummaryPath).LastWriteTimeUtc -lt (Get-Item -LiteralPath $riskJson).LastWriteTimeUtc) { return $false }
    return $true
}
function Full-Project-ContinuationReady {
    $request = Read-JsonFile $fullProjectRequestPath
    $currentYear = Read-JsonFile $currentYearCompletePath
    if (-not $request -or -not $currentYear) { return $false }
    if (-not (Test-UploadGateProof)) { return $false }
    $gate = Read-JsonFile $uploadGateProofPath
    if (-not $gate -or [int]$gate.pending_count -ne 0) { return $false }
    if (
        [string]$currentYear.upload_gate_schema -ne [string]$gate.schema -or
        [string]$currentYear.audit_input_sha256 -ne [string]$gate.audit_input_sha256 -or
        [string]$currentYear.manifest_summary_sha256 -ne [string]$gate.manifest_summary_sha256 -or
        [string]$currentYear.pending_sha256 -ne [string]$gate.pending_sha256 -or
        [string]$currentYear.backfill_run_id -ne [string]$gate.backfill_run_id -or
        [int]$currentYear.pending_count -ne 0
    ) { return $false }
    try {
        return ([datetime]$currentYear.completed_at) -ge ([datetime]$request.requested_at)
    } catch {
        return $false
    }
}

function Test-FullProjectCompletionMarker {
    $marker = Read-JsonFile $fullProjectCompletePath
    if (-not $marker -or [int]$marker.error_count -ne 0) { return $false }
    $discovery = Join-Path $audit "folder_discovery.csv"
    $summary = Join-Path $audit "folder_summary.csv"
    if (
        (Get-FileSha256 $discovery) -ne [string]$marker.folder_discovery_sha256 -or
        (Get-FileSha256 $summary) -ne [string]$marker.folder_summary_sha256
    ) { return $false }
    $discoveredCount = Get-CsvRowCount $discovery
    if ($discoveredCount -lt 0 -or $discoveredCount -ne [int]$marker.discovered_folder_count) { return $false }
    try {
        $rows = @(Import-Csv -LiteralPath $summary)
        $bad = @($rows | Where-Object { $_.status -notin @("copied", "skipped_existing") })
    } catch { return $false }
    return $bad.Count -eq 0 -and [int]$marker.completed_folder_count -eq $discoveredCount
}

function Start-EvidenceBackfillIfNeeded {
    $builder = Join-Path $RepoRoot "tools\build_v1945_evidence_backfill.py"
    $candidate = Join-Path $audit "v1945_evidence_backfill_2026.csv"
    $result = Join-Path $audit "v1945_evidence_backfill_2026_results.csv"
    $summaryCsv = Join-Path $audit "v1945_evidence_backfill_2026_run_summary.csv"
    $builderOutput = @(& $python $builder --audit-dir $audit --year "2026" --output $candidate --execute 2>&1)
    $builderExit = $LASTEXITCODE
    $builderText = $builderOutput -join "`n"
    if ($builderExit -ne 0) {
        Alert "evidence_backfill_builder_failed" @{detail=$builderText}
        throw "evidence backfill builder failed closed"
    }
    try { $proof = $builderText | ConvertFrom-Json } catch {
        Alert "evidence_backfill_builder_unreadable" @{detail=$builderText}
        throw "evidence backfill builder returned unreadable output"
    }
    if ($proof.executed -ne $true) { throw "evidence backfill builder did not atomically write candidates" }
    if ([int]$proof.candidate_rows -eq 0) {
        if (
            [int]$proof.missing_sources -ne 0 -or
            [int]$proof.conflicting_sources -ne 0 -or
            [int]$proof.invalid_rows -ne 0 -or
            [int]$proof.unique_year_sources -le 0 -or
            [int]$proof.already_verified_year_sources -ne [int]$proof.unique_year_sources
        ) {
            throw "evidence backfill zero-candidate proof is incomplete"
        }
        Log-Event "evidence_backfill_complete" @{sources=[int]$proof.unique_year_sources;verified=[int]$proof.already_verified_year_sources}
        return $false
    }
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    Start-Hidden -File $python -ProcessArgs @(
        "tools\rerun_staged_candidates.py",
        "--source-root",$SourceRoot,
        "--output-dir",$OutputDir,
        "--backend-url",$BackendUrl,
        "--input-csv",$candidate,
        "--output-csv",$result,
        "--run-summary-csv",$summaryCsv,
        "--execute","--resume-existing-then-continue",
        "--poll-seconds","10","--timeout-minutes","10080"
    ) -OutFile (Join-Path $logDir "supervisor_evidence_backfill_$stamp.out.log") -ErrFile (Join-Path $logDir "supervisor_evidence_backfill_$stamp.err.log")
    Log-Event "evidence_backfill_restarted" @{remaining=[int]$proof.candidate_rows;sources=[int]$proof.unique_year_sources}
    return $true
}

try {
    try {
        $null = New-Item -ItemType File -Path $lockPath -ErrorAction Stop
        Set-Content -LiteralPath $lockPath -Value (([ordered]@{pid=$PID;started_at=(Get-Date).ToString("o");repo=$RepoRoot})|ConvertTo-Json) -Encoding UTF8
    } catch { Log-Event "duplicate_or_locked"; exit 0 }

    $status = Get-BackendStatus
    if (Test-Path -LiteralPath $RuntimeHealthFusePath) {
        Alert "runtime_health_fuse_active" @{fuse=$RuntimeHealthFusePath}
        exit 9
    }
    if (Test-Path -LiteralPath $BenchmarkLockPath) {
        try {
            $planned = Get-Content -LiteralPath $BenchmarkLockPath -Raw | ConvertFrom-Json
        } catch {
            Log-Event "benchmark_lock_unreadable" @{ lock=$BenchmarkLockPath }
            exit 0
        }
        if ($planned.purpose -eq "backend_upgrade_v1945") {
            $plannedOwner = Get-Process -Id ([int]$planned.pid) -ErrorAction SilentlyContinue
            if ($plannedOwner) {
                Log-Event "planned_backend_upgrade_interlock" @{ lock=$BenchmarkLockPath; owner=$planned.pid }
                exit 0
            }
            $activeBackfill = @(Owned "rerun_staged_candidates\.py")
            if ($activeBackfill.Count -gt 0 -or ($status -and [bool]$status.is_running)) {
                Log-Event "planned_backend_upgrade_recovery_active" @{
                    lock=$BenchmarkLockPath
                    owner=$planned.pid
                    runners=$activeBackfill.Count
                    backend_running=if($status){[bool]$status.is_running}else{$false}
                }
                exit 0
            }
            if (
                -not $status -or
                [string]$status.version -notlike "v19.45*" -or
                [string]$status.status_contract_version -ne "compact-v2" -or
                [string]$status.accuracy_profile -ne "strict"
            ) {
                Alert "planned_backend_upgrade_recovery_contract_failed" @{lock=$BenchmarkLockPath;owner=$planned.pid}
                exit 10
            }
            try {
                $backfillStarted = Start-EvidenceBackfillIfNeeded
            } catch {
                Alert "planned_backend_upgrade_recovery_failed" @{lock=$BenchmarkLockPath;owner=$planned.pid;detail=$_.Exception.Message}
                exit 11
            }
            if ($backfillStarted) {
                Log-Event "planned_backend_upgrade_recovery_started" @{lock=$BenchmarkLockPath;owner=$planned.pid}
                exit 0
            }
            Remove-Item -LiteralPath $BenchmarkLockPath -Force
            Log-Event "planned_backend_upgrade_recovery_completed" @{lock=$BenchmarkLockPath;owner=$planned.pid}
        }
        if (Test-Path -LiteralPath $BenchmarkLockPath) {
            Log-Event "model_benchmark_interlock" @{lock=$BenchmarkLockPath;purpose=$planned.purpose;owner=$planned.pid}
            exit 0
        }
    }
    $backend = @(Owned "samsung_ocr_batch_processor\.py")
    $watcher = @(Owned "auto_rerun_questionable_after_recursive\.ps1")
    $staged = @(Owned "rerun_staged_candidates\.py")
    $recursive = @(Owned "recursive_ocr_flat_export\.py")
    $uploader = @(Owned "rclone_drive_upload\.py|rclone\.exe")
    $pipelineTransitionStarted = $false

    if ($status -and [bool]$status.is_running) {
        Log-Event "healthy_noop" @{backend=$backend.Count;watcher=$watcher.Count;staged=$staged.Count;recursive=$recursive.Count;uploader=$uploader.Count;folder=$status.current_relative_dir;file=$status.current_file}
        exit 0
    }

    if ($backend.Count -gt 0 -and -not $status) {
        Alert "backend_process_exists_but_api_unhealthy" @{backend_pids=@($backend.ProcessId)}
        exit 3
    }

    $lm = Get-LmModels
    if (-not $lm) {
        Log-Event "lm_server_recovery_attempt"
        $r = Invoke-Lms @("server","start","--bind","127.0.0.1")
        Start-Sleep -Seconds 5
        $lm = Get-LmModels
        if (-not $lm) { Alert "lm_server_unavailable"; exit 4 }
    }
    $loaded = @($lm.data | Where-Object { $_.loaded -eq $true -or $_.status -eq "loaded" })
    if ($loaded.Count -gt 0 -and @($loaded | Where-Object { $_.id -ne $Model -and $_.modelKey -ne $Model }).Count -gt 0) {
        Alert "different_model_already_loaded" @{loaded=$loaded}
        exit 5
    }
    if ($loaded.Count -eq 0) {
        $inventory = Invoke-Lms @("ls","--json")
        if (-not $inventory -or $inventory.output -notmatch [regex]::Escape($Model)) {
            Alert "required_local_model_unavailable" @{model=$Model}
            exit 6
        }
        $load = Invoke-Lms @("load",$Model,"--context-length",$ContextLength,"--yes")
        if (-not $load -or $load.exit -ne 0) { Alert "qwen_load_failed" @{model=$Model;context=$ContextLength}; exit 7 }
    }

    if (-not $status -and $backend.Count -eq 0) {
        $stamp=Get-Date -Format "yyyyMMdd_HHmmss"
        Start-Hidden -File $python -ProcessArgs @("samsung_ocr_batch_processor.py","--api_base",$ApiBase,"--api_key","lm-studio","--model",$Model,"--dir",$SourceRoot) -OutFile (Join-Path $logDir "supervisor_backend_$stamp.out.log") -ErrFile (Join-Path $logDir "supervisor_backend_$stamp.err.log")
        Log-Event "backend_started" @{model=$Model}
    }

    if ($staged.Count -gt 0 -or $recursive.Count -gt 0) {
        Alert "staged_or_recursive_state_ambiguous" @{staged=$staged.Count;recursive=$recursive.Count}
        exit 8
    }
    $fullProjectDone = Test-FullProjectCompletionMarker
    if (-not $fullProjectDone -and (Start-EvidenceBackfillIfNeeded)) {
        $pipelineTransitionStarted = $true
        exit 0
    }
    $fullProjectReady = Full-Project-ContinuationReady
    if ($fullProjectDone) {
        Log-Event "full_project_complete_noop" @{marker=$fullProjectCompletePath}
    } elseif ($fullProjectReady -and $watcher.Count -eq 0) {
        $stamp=Get-Date -Format "yyyyMMdd_HHmmss"
        Start-Hidden -File $python -ProcessArgs @(
            "tools\recursive_ocr_flat_export.py",
            "--source-root",$SourceRoot,
            "--output-dir",$OutputDir,
            "--backend-url",$BackendUrl,
            "--api-base",$ApiBase,
            "--api-key","lm-studio",
            "--model",$Model,
            "--poll-seconds","20",
            "--timeout-minutes","10080",
            "--ignore-current-year-review-gate"
        ) -OutFile (Join-Path $logDir "supervisor_full_recursive_$stamp.out.log") -ErrFile (Join-Path $logDir "supervisor_full_recursive_$stamp.err.log")
        Start-Sleep -Seconds 1
        Start-Hidden -File "powershell.exe" -ProcessArgs @(
            "-NoProfile","-ExecutionPolicy","Bypass","-File","tools\auto_rerun_questionable_after_recursive.ps1",
            "-RepoRoot",$RepoRoot,"-SourceRoot",$SourceRoot,"-OutputDir",$OutputDir,
            "-BackendUrl",$BackendUrl,"-PollSeconds","300","-PrimaryPasses","2",
            "-SkipCurrentYearPhases","-SkipRecursiveResume"
        ) -OutFile (Join-Path $logDir "supervisor_full_watcher_$stamp.out.log") -ErrFile (Join-Path $logDir "supervisor_full_watcher_$stamp.err.log")
        $pipelineTransitionStarted = $true
        Log-Event "full_project_pipeline_started" @{request=$fullProjectRequestPath;current_year_marker=$currentYearCompletePath}
    } elseif ($watcher.Count -eq 0) {
        $stamp=Get-Date -Format "yyyyMMdd_HHmmss"
        Start-Hidden -File "powershell.exe" -ProcessArgs @("-NoProfile","-ExecutionPolicy","Bypass","-File","tools\auto_rerun_questionable_after_recursive.ps1","-RepoRoot",$RepoRoot,"-SourceRoot",$SourceRoot,"-OutputDir",$OutputDir,"-BackendUrl",$BackendUrl,"-PollSeconds","300","-PrimaryPasses","2","-CurrentYearOnly") -OutFile (Join-Path $logDir "supervisor_watcher_$stamp.out.log") -ErrFile (Join-Path $logDir "supervisor_watcher_$stamp.err.log")
        $pipelineTransitionStarted = $true
        Log-Event "current_year_watcher_started"
    }
    $pending = Join-Path $OutputDir "_drive_upload\drive_upload_ready_pending.csv"
    $pendingCount = Get-CsvRowCount $pending
    if ($pendingCount -gt 0 -and $uploader.Count -eq 0) {
        if ($pipelineTransitionStarted -or $watcher.Count -gt 0) {
            Log-Event "uploader_deferred_pipeline_transition" @{pending=$pendingCount;watcher=$watcher.Count;transition_started=$pipelineTransitionStarted}
        } elseif (-not (Test-UploadGateProof)) {
            Log-Event "uploader_gate_closed" @{pending=$pendingCount;proof=$uploadGateProofPath}
        } else {
            $stamp=Get-Date -Format "yyyyMMdd_HHmmss"
            Start-Hidden -File $python -ProcessArgs @("tools\rclone_drive_upload.py","--output-dir",$OutputDir,"--execute","--repeat","--limit","100","--transfers","4","--checkers","8","--rclone-timeout-seconds","1200") -OutFile (Join-Path $logDir "supervisor_uploader_$stamp.out.log") -ErrFile (Join-Path $logDir "supervisor_uploader_$stamp.err.log")
            Log-Event "ready_uploader_started" @{pending=$pendingCount;proof=$uploadGateProofPath}
        }
    }
} finally {
    Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
}

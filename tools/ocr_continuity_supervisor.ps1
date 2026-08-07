param(
    [string]$RepoRoot,
    [string]$SourceRoot,
    [string]$OutputDir,
    [string]$BackendUrl = "http://127.0.0.1:5002",
    [string]$ApiBase = "http://127.0.0.1:1234/v1",
    [string]$Model = "qwen/qwen3-vl-8b",
    [int]$ContextLength = 32768,
    [int]$Parallel = 1,
    [int]$UploadGateProofMaxAgeMinutes = 30
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$audit = Join-Path $OutputDir "_ocr_audit"
$BenchmarkLockPath = Join-Path $audit "model_benchmark.lock"
$RuntimeHealthFusePath = Join-Path $audit "runtime_health_fuse.json"
$PipelinePausePath = Join-Path $audit "pipeline_pause.json"
$logDir = Join-Path $RepoRoot "logs"
$lockPath = Join-Path $audit "ocr_continuity_supervisor.lock"
$alertPath = Join-Path $audit "ocr_continuity_supervisor_alert.json"
$fullProjectRequestPath = Join-Path $audit "full_project_continuation_requested.json"
$historicalContinuationGate = Join-Path $RepoRoot "tools\historical_continuation_gate.py"
$historicalContinuationReceipt = Join-Path $audit "historical_continuation_receipt.json"
$pipelineStatusPath = Join-Path $RepoRoot "dashboard\dist\pipeline-status.json"
$currentYearCompletePath = Join-Path $audit "current_year_rerun_cycle_complete.json"
$fullProjectCompletePath = Join-Path $audit "full_project_rerun_cycle_complete.json"
$evidenceDeferredSnapshotPath = Join-Path $audit "evidence_backfill_deferred_snapshot.json"
$uploadGateProofPath = Join-Path $OutputDir "_drive_upload\upload_gate_proof.json"
$logPath = Join-Path $logDir ("ocr_continuity_supervisor_{0}.jsonl" -f (Get-Date -Format "yyyyMMdd"))
$python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
$backendScript = Join-Path $RepoRoot "samsung_ocr_batch_processor.py"
$streamUploaderScript = Join-Path $RepoRoot "tools\stream_drive_upload.py"
$recursiveScript = Join-Path $RepoRoot "tools\recursive_ocr_flat_export.py"
$stagedScript = Join-Path $RepoRoot "tools\rerun_staged_candidates.py"
$watcherScript = Join-Path $RepoRoot "tools\auto_rerun_questionable_after_recursive.ps1"
$bulkUploaderScript = Join-Path $RepoRoot "tools\rclone_drive_upload.py"
$safeIdleReloadScript = Join-Path $RepoRoot "tools\reload_backend_at_safe_idle.ps1"
$recoverReviewMetadataScript = Join-Path $RepoRoot "tools\recover_review_metadata_false_fuse.py"
$recoverFirstPassPhotoLocalScript = Join-Path $RepoRoot "tools\recover_first_pass_photo_local_fuse.py"
$recoverContainedRequestBindingScript = Join-Path $RepoRoot "tools\recover_contained_request_binding_fuse.py"
$evidenceTracePath = Join-Path $audit "v1945_evidence_trace.jsonl"
$streamPendingDir = Join-Path $OutputDir "_drive_upload_stream\pending"
$streamWorkingDir = Join-Path $OutputDir "_drive_upload_stream\working"
$script:CurrentYearEvidenceComplete = $false
try { $backendPort = ([uri]$BackendUrl).Port } catch { throw "invalid BackendUrl: $BackendUrl" }
if ($backendPort -le 0) { throw "BackendUrl must include a valid port: $BackendUrl" }
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
    Write-PipelineStatus -Active $false -Phase "blocked" -WorkerPid 0 -Reason $Reason
    Log-Event "alert" (@{reason=$Reason} + $Data)
}
function Clear-Alert {
    Remove-Item -LiteralPath $alertPath -Force -ErrorAction SilentlyContinue
}
function Owned([string]$Pattern) {
    $matches = @(Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and $_.CommandLine -match $Pattern -and
        $_.CommandLine -match [regex]::Escape($RepoRoot) -and
        $_.CommandLine -notmatch "Get-CimInstance|ocr_continuity_supervisor"
    })
    # A Windows venv launcher remains as the parent of the real interpreter,
    # and both carry the same command line. Count that parent/child pair as one
    # logical worker while still detecting two independently launched roots.
    $matchedPids = @{}
    foreach ($process in $matches) { $matchedPids[[int]$process.ProcessId] = $true }
    return @($matches | Where-Object { -not $matchedPids.ContainsKey([int]$_.ParentProcessId) })
}
function Get-BackendStatus {
    try { return Invoke-RestMethod -Uri "$BackendUrl/api/status" -TimeoutSec 8 } catch { return $null }
}
function Get-LmModels {
    try { return Invoke-RestMethod -Uri ($ApiBase.TrimEnd('/') + "/models") -TimeoutSec 5 } catch { return $null }
}
function Invoke-Lms([string[]]$CommandArgs) {
    $lms = Join-Path $env:USERPROFILE ".lmstudio\bin\lms.exe"
    if (-not (Test-Path $lms)) { return $null }
    $previousErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $out = & $lms @CommandArgs 2>&1
        $exitCode = $LASTEXITCODE
    } catch {
        return [pscustomobject]@{ exit=1; output=$_.Exception.Message }
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
    # `lms` emits ANSI colour sequences even when Windows PowerShell captures
    # its output. They can split identifiers or make numeric columns fail to
    # parse, so runtime discovery must compare undecorated text.
    $plainOutput = (($out -join "`n") -replace '\x1B\[[0-?]*[ -/]*[@-~]', '')
    return [pscustomobject]@{ exit=$exitCode; output=$plainOutput }
}
function Get-LmProcessInfo {
    $result = Invoke-Lms @("ps")
    if (-not $result -or $result.exit -ne 0) { return @() }
    $rows = @()
    foreach ($line in @($result.output -split "`r?`n")) {
        $parts = @($line.Trim() -split "\s{2,}")
        if ($parts.Count -lt 7 -or $parts[0] -eq "IDENTIFIER") { continue }
        $context = 0
        $parallel = 0
        if (
            -not [int]::TryParse($parts[4], [ref]$context) -or
            -not [int]::TryParse($parts[5], [ref]$parallel)
        ) { continue }
        $rows += [pscustomobject]@{
            identifier=$parts[0]
            model=$parts[1]
            status=$parts[2]
            context=$context
            parallel=$parallel
        }
    }
    return @($rows)
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
    # Windows PowerShell 5.1 otherwise decodes UTF-8 without BOM through the
    # active ANSI code page.  A Chinese staging path then becomes unreadable,
    # falls back to SourceRoot, and creates a false checkpoint mismatch loop.
    try {
        return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch { return $null }
}

function Write-JsonAtomic([string]$Path, [object]$Payload) {
    $temp = "$Path.tmp.$PID"
    try {
        $json = $Payload | ConvertTo-Json -Depth 8
        [System.IO.File]::WriteAllText($temp, $json, [System.Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temp -Destination $Path -Force
    } finally {
        if (Test-Path -LiteralPath $temp) {
            Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
        }
    }
}
function Write-PipelineStatus(
    [bool]$Active,
    [string]$Phase,
    [int]$WorkerPid = 0,
    [string]$Reason = ""
) {
    $payload = [ordered]@{
        schema = "samsung-ocr-pipeline-status/v1"
        updated_at = (Get-Date).ToUniversalTime().ToString("o")
        active = $Active
        phase = $Phase
        worker_pid = $WorkerPid
        reason = $Reason
    }
    Write-JsonAtomic -Path $pipelineStatusPath -Payload $payload
}
function Get-StreamPendingCount {
    if (-not (Test-Path -LiteralPath $streamPendingDir)) { return 0 }
    return @(Get-ChildItem -LiteralPath $streamPendingDir -Filter "*.json" -File -ErrorAction SilentlyContinue).Count
}
function Get-StreamWorkingCount {
    if (-not (Test-Path -LiteralPath $streamWorkingDir)) { return 0 }
    return @(Get-ChildItem -LiteralPath $streamWorkingDir -Filter "*.json" -File -ErrorAction SilentlyContinue).Count
}
function Ensure-StreamUploaderOnline {
    $workers = @(Owned "stream_drive_upload\.py")
    if ($workers.Count -gt 1) {
        throw "multiple repo-owned stream upload workers exist"
    }
    if ($workers.Count -eq 1) { return $workers }
    $stamp=Get-Date -Format "yyyyMMdd_HHmmss"
    Start-Hidden -File $python -ProcessArgs @(
        $streamUploaderScript,
        "--output-dir",$OutputDir,
        "--poll-seconds","5",
        "--timeout-seconds","600"
    ) -OutFile (Join-Path $logDir "supervisor_stream_uploader_$stamp.out.log") -ErrFile (Join-Path $logDir "supervisor_stream_uploader_$stamp.err.log")
    Start-Sleep -Seconds 1
    $workers = @(Owned "stream_drive_upload\.py")
    if ($workers.Count -ne 1) {
        throw "stream uploader status worker did not become unique"
    }
    Log-Event "stream_uploader_started" @{
        pending=(Get-StreamPendingCount)
        pid=$workers[0].ProcessId
    }
    return $workers
}
function Start-BackendService([string]$ImageDir) {
    $live = Get-BackendStatus
    $workers = @(Owned "samsung_ocr_batch_processor\.py")
    if ($live) {
        if ($workers.Count -ne 1) {
            throw "backend status API is not owned by one logical repo process"
        }
        return $live
    }
    if ($workers.Count -gt 0) {
        throw "repo-owned backend exists but its status API is unavailable"
    }
    if (-not (Test-Path -LiteralPath $ImageDir -PathType Container)) {
        throw "backend continuity directory is unavailable: $ImageDir"
    }
    $env:SAMSUNG_OCR_NO_BROWSER = "1"
    $stamp=Get-Date -Format "yyyyMMdd_HHmmss"
    Start-Hidden -File $python -ProcessArgs @(
        $backendScript,
        "--api_base",$ApiBase,
        "--api_key","lm-studio",
        "--model",$Model,
        "--dir",$ImageDir,
        "--port",[string]$backendPort,
        "--no_followme_auto_update"
    ) -OutFile (Join-Path $logDir "supervisor_backend_$stamp.out.log") -ErrFile (Join-Path $logDir "supervisor_backend_$stamp.err.log")
    for ($attempt = 0; $attempt -lt 45 -and -not $live; $attempt++) {
        Start-Sleep -Seconds 1
        $live = Get-BackendStatus
    }
    if (-not $live) {
        throw "backend status API did not become available"
    }
    $workers = @(Owned "samsung_ocr_batch_processor\.py")
    if ($workers.Count -ne 1) {
        throw "started backend is not one logical repo process"
    }
    Log-Event "interface_backend_recovered" @{model=$Model;port=$backendPort;backend=@(Owned "samsung_ocr_batch_processor\.py").Count;image_dir=$ImageDir}
    return $live
}
function Get-PausedContinuityDir($Pause) {
    $fallback = [System.IO.Path]::GetFullPath($SourceRoot)
    $candidateText = if ($Pause) { [string]$Pause.current_dir } else { "" }
    if (-not $candidateText) { return $fallback }
    try {
        $candidate = [System.IO.Path]::GetFullPath($candidateText)
        $stagingRoot = [System.IO.Path]::GetFullPath((Join-Path $OutputDir "_ocr_staging"))
        $sourceRootPath = [System.IO.Path]::GetFullPath($SourceRoot)
    } catch { return $fallback }
    $insideStaging = (
        $candidate.Equals($stagingRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        $candidate.StartsWith(
            $stagingRoot.TrimEnd([char[]]@('\','/')) + [System.IO.Path]::DirectorySeparatorChar,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    )
    $insideSource = (
        $candidate.Equals($sourceRootPath, [System.StringComparison]::OrdinalIgnoreCase) -or
        $candidate.StartsWith(
            $sourceRootPath.TrimEnd([char[]]@('\','/')) + [System.IO.Path]::DirectorySeparatorChar,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    )
    if (
        ($insideStaging -or $insideSource) -and
        (Test-Path -LiteralPath $candidate -PathType Container)
    ) {
        return $candidate
    }
    return $fallback
}
function Resume-RepairedPausedCheckpoint([string]$Checkpoint) {
    if (-not $Checkpoint -or -not (Test-Path -LiteralPath $Checkpoint -PathType Container)) {
        throw "saved paused checkpoint is unavailable"
    }
    $resumeBody = @{
        dir=$Checkpoint
        restart=$false
        confirmed=$true
        reprocess_last_n=0
    } | ConvertTo-Json
    $resume = Invoke-RestMethod -Uri "$BackendUrl/api/start_batch" -Method Post `
        -ContentType "application/json; charset=utf-8" -Body $resumeBody -TimeoutSec 30
    if ([string]$resume.status -ne "started") {
        throw "backend did not accept the repaired checkpoint resume"
    }
    $resumed = $null
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Seconds 1
        $candidate = Get-BackendStatus
        if (
            $candidate -and
            (
                [bool]$candidate.is_running -or
                [int]$candidate.stats.processed -eq [int]$candidate.stats.total
            )
        ) {
            $resumed = $candidate
            break
        }
    }
    if (-not $resumed) {
        throw "repaired checkpoint resume did not become observable"
    }
    try {
        $liveDir = [System.IO.Path]::GetFullPath([string]$resumed.image_dir)
    } catch { $liveDir = "" }
    if (
        -not $liveDir.Equals($Checkpoint, [System.StringComparison]::OrdinalIgnoreCase) -or
        $resumed.pipeline_pause -or
        (Test-Path -LiteralPath $PipelinePausePath)
    ) {
        throw "repaired checkpoint resume verification failed"
    }
    Log-Event "pipeline_pause_checkpoint_auto_resumed" @{
        checkpoint=$Checkpoint
        processed=[int]$resumed.stats.processed
        total=[int]$resumed.stats.total
    }
    return $resumed
}
function Get-FileSha256([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return "" }
    try { return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() } catch { return "" }
}
function Get-NormalizedSourceIdSetProof([object[]]$Ids) {
    $normalized = @()
    foreach ($raw in @($Ids)) {
        $id = ([string]$raw).Trim().ToLowerInvariant()
        if ($id -notmatch '^[0-9a-f]{64}$') { return $null }
        $normalized += $id
    }
    if ($normalized.Count -le 0) { return $null }
    $unique = @($normalized | Sort-Object -Unique)
    if ($unique.Count -ne $normalized.Count) { return $null }
    $joined = [string]::Join("`n", $unique)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($joined)
        $hash = ([System.BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
    return [pscustomobject]@{ count=$unique.Count; sha256=$hash }
}
function Get-LatestBackendRuntimeWrite {
    $runtimePaths = @(
        $backendScript,
        (Join-Path $RepoRoot "samsung_ocr_prompt.txt"),
        (Join-Path $RepoRoot "skills\audit_fields.py"),
        (Join-Path $RepoRoot "skills\model_validation.py"),
        (Join-Path $RepoRoot "skills\runtime_health_gate.py"),
        (Join-Path $RepoRoot "skills\batch_orchestrator.py")
    )
    $items = @($runtimePaths | ForEach-Object {
        if (Test-Path -LiteralPath $_ -PathType Leaf) { Get-Item -LiteralPath $_ }
    })
    if ($items.Count -ne $runtimePaths.Count) { return $null }
    return ($items | Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime
}
function Try-AutoRecoverKnownReviewMetadataFuse {
    $fuse = Read-JsonFile $RuntimeHealthFusePath
    if (
        -not $fuse -or
        $fuse.schema -ne "samsung-ocr-runtime-health-fuse/v1" -or
        $fuse.active -ne $true -or
        @($fuse.reasons).Count -ne 1 -or
        [string]$fuse.reasons[0] -ne "review_prior_value_present" -or
        [int]$fuse.attempt -notin @(2,3) -or
        [string]$fuse.record_snapshot.view_type -ne "失敗" -or
        $null -ne $fuse.record_snapshot.model -or
        $null -ne $fuse.record_snapshot.price -or
        -not [string]::IsNullOrEmpty([string]$fuse.record_snapshot.raw_model_output)
    ) { return $false }
    $live = Get-BackendStatus
    $stagingDir = if ($live) { [string]$live.image_dir } else { "" }
    if (
        -not $stagingDir -or
        -not (Test-Path -LiteralPath $stagingDir -PathType Container) -or
        -not (Test-Path -LiteralPath $evidenceTracePath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $recoverReviewMetadataScript -PathType Leaf)
    ) { return $false }
    $dryOutput = @(& $python $recoverReviewMetadataScript `
        --staging-dir $stagingDir --trace $evidenceTracePath `
        --fuse-file $RuntimeHealthFusePath 2>&1)
    if ($LASTEXITCODE -ne 0) {
        Log-Event "known_metadata_fuse_recovery_refused" @{detail=($dryOutput -join "`n")}
        return $false
    }
    $applyOutput = @(& $python $recoverReviewMetadataScript `
        --staging-dir $stagingDir --trace $evidenceTracePath `
        --fuse-file $RuntimeHealthFusePath --apply 2>&1)
    if ($LASTEXITCODE -ne 0 -or (Test-Path -LiteralPath $RuntimeHealthFusePath)) {
        Log-Event "known_metadata_fuse_recovery_failed" @{detail=($applyOutput -join "`n")}
        return $false
    }
    Log-Event "known_metadata_fuse_auto_recovered" @{
        source=[string]$fuse.source_file
        attempt=[int]$fuse.attempt
        staging=$stagingDir
    }
    return $true
}
function Try-AutoRecoverFirstPassPhotoLocalFuse {
    $fuse = Read-JsonFile $RuntimeHealthFusePath
    if (
        -not $fuse -or
        $fuse.schema -ne "samsung-ocr-runtime-health-fuse/v1" -or
        $fuse.active -ne $true -or
        [int]$fuse.attempt -ne 1 -or
        @($fuse.reasons).Count -ne 1 -or
        [string]$fuse.reasons[0] -ne "structured_authority_material_conflict:model" -or
        @($fuse.record_snapshot.structured_authority_blocked_fields).Count -ne 1 -or
        [string]$fuse.record_snapshot.structured_authority_blocked_fields[0] -ne "model"
    ) { return $false }
    $live = Get-BackendStatus
    $stagingDir = if ($live) { [string]$live.image_dir } else { "" }
    if (
        -not $stagingDir -or
        -not (Test-Path -LiteralPath $stagingDir -PathType Container) -or
        -not (Test-Path -LiteralPath $recoverFirstPassPhotoLocalScript -PathType Leaf)
    ) { return $false }
    $dryOutput = @(& $python $recoverFirstPassPhotoLocalScript `
        --staging-dir $stagingDir --fuse-file $RuntimeHealthFusePath 2>&1)
    if ($LASTEXITCODE -ne 0) {
        Log-Event "first_pass_photo_local_fuse_recovery_refused" @{detail=($dryOutput -join "`n")}
        return $false
    }
    $applyOutput = @(& $python $recoverFirstPassPhotoLocalScript `
        --staging-dir $stagingDir --fuse-file $RuntimeHealthFusePath --apply 2>&1)
    if ($LASTEXITCODE -ne 0 -or (Test-Path -LiteralPath $RuntimeHealthFusePath)) {
        Log-Event "first_pass_photo_local_fuse_recovery_failed" @{detail=($applyOutput -join "`n")}
        return $false
    }
    Log-Event "first_pass_photo_local_fuse_auto_recovered" @{
        source=[string]$fuse.source_file
        attempt=[int]$fuse.attempt
        staging=$stagingDir
    }
    return $true
}
function Get-CsvRowCount([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return -1 }
    try { return @((Import-Csv -LiteralPath $Path)).Count } catch { return -1 }
}
function Try-AutoRecoverContainedRequestBindingFuse {
    $fuse = Read-JsonFile $RuntimeHealthFusePath
    if (
        -not $fuse -or
        $fuse.schema -ne "samsung-ocr-runtime-health-fuse/v1" -or
        $fuse.active -ne $true -or
        [int]$fuse.attempt -notin @(1,2) -or
        -not (Test-Path -LiteralPath $recoverContainedRequestBindingScript -PathType Leaf)
    ) { return $false }
    $reasons = @($fuse.reasons | ForEach-Object { [string]$_ })
    if (
        "request_binding_unverified" -notin $reasons -and
        "request_id_missing" -notin $reasons -and
        "request_id_mismatch" -notin $reasons
    ) { return $false }
    $live = Get-BackendStatus
    $stagingDir = if ($live) { [string]$live.image_dir } else { "" }
    if (-not $stagingDir -or -not (Test-Path -LiteralPath $stagingDir -PathType Container)) {
        return $false
    }
    $dryOutput = @(& $python $recoverContainedRequestBindingScript `
        --staging-dir $stagingDir --fuse-file $RuntimeHealthFusePath 2>&1)
    if ($LASTEXITCODE -ne 0) {
        Log-Event "contained_request_binding_recovery_refused" @{detail=($dryOutput -join "`n")}
        return $false
    }
    $applyOutput = @(& $python $recoverContainedRequestBindingScript `
        --staging-dir $stagingDir --fuse-file $RuntimeHealthFusePath --apply 2>&1)
    if ($LASTEXITCODE -ne 0 -or (Test-Path -LiteralPath $RuntimeHealthFusePath)) {
        Log-Event "contained_request_binding_recovery_failed" @{detail=($applyOutput -join "`n")}
        return $false
    }
    Log-Event "contained_request_binding_auto_recovered" @{
        source=[string]$fuse.source_file
        attempt=[int]$fuse.attempt
        staging=$stagingDir
    }
    return $true
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
    if (-not (Test-Path -LiteralPath $historicalContinuationGate)) { return $false }
    # User intent survives evidence-revision upgrades.  Refresh only the
    # revision field while preserving the original request time/objective;
    # the Python gate remains the sole authority for every content binding.
    $migrationOutput = @(& $python $historicalContinuationGate `
        --source-root $SourceRoot --output-dir $OutputDir --backend-url $BackendUrl --migrate-existing-request 2>&1)
    $migrationExit = $LASTEXITCODE
    try { $migration = ($migrationOutput -join "`n") | ConvertFrom-Json } catch { return $false }
    if ($migrationExit -ne 0 -or $migration.valid -ne $true) {
        Log-Event "historical_continuation_request_migration_blocked" @{detail=($migrationOutput -join ";")}
        return $false
    }
    $validatorOutput = @(& $python $historicalContinuationGate `
        --source-root $SourceRoot --output-dir $OutputDir --backend-url $BackendUrl --write-receipt 2>&1)
    $validatorExit = $LASTEXITCODE
    try { $validator = ($validatorOutput -join "`n") | ConvertFrom-Json } catch { return $false }
    if ($validatorExit -ne 0 -or $validator.valid -ne $true) {
        Log-Event "historical_continuation_gate_blocked" @{errors=($validator.errors -join ";")}
        return $false
    }
    return (Test-Path -LiteralPath $historicalContinuationReceipt)
}

function Test-FullProjectCompletionMarker {
    $marker = Read-JsonFile $fullProjectCompletePath
    if (-not $marker -or [int]$marker.error_count -ne 0) { return $false }
    $discovery = Join-Path $audit "folder_discovery.csv"
    $summary = Join-Path $audit "folder_summary.csv"
    $inventoryCsv = Join-Path $audit "source_inventory_v1.csv"
    $inventoryJson = Join-Path $audit "source_inventory_v1.json"
    if (
        (Get-FileSha256 $discovery) -ne [string]$marker.folder_discovery_sha256 -or
        (Get-FileSha256 $summary) -ne [string]$marker.folder_summary_sha256 -or
        (Get-FileSha256 $inventoryCsv) -ne [string]$marker.source_inventory_csv_sha256 -or
        (Get-FileSha256 $inventoryJson) -ne [string]$marker.source_inventory_summary_sha256
    ) { return $false }
    $discoveredCount = Get-CsvRowCount $discovery
    if ($discoveredCount -lt 0 -or $discoveredCount -ne [int]$marker.discovered_folder_count) { return $false }
    try {
        $rows = @(Import-Csv -LiteralPath $summary)
        $bad = @($rows | Where-Object { $_.status -notin @("copied", "skipped_existing") })
    } catch { return $false }
    return $bad.Count -eq 0 -and [int]$marker.completed_folder_count -eq $discoveredCount -and
        [int]$marker.source_inventory_folder_count -eq $discoveredCount
}

function Start-EvidenceBackfillIfNeeded {
    $builder = Join-Path $RepoRoot "tools\build_v1945_evidence_backfill.py"
    $candidate = Join-Path $audit "v1945_evidence_backfill_2026.csv"
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
    if (
        ($streamPendingCount -gt 0 -or $streamWorkingCount -gt 0) -and
        $null -eq $proof.current_upload_queue_source_ids
    ) {
        throw "evidence backfill builder has no durable upload-queue authority"
    }
    if ([int]$proof.invalid_upload_queue_jobs -gt 0) {
        Alert "evidence_backfill_invalid_upload_queue_jobs" @{
            invalid=[int]$proof.invalid_upload_queue_jobs
            samples=($proof.upload_queue_error_samples -join ";")
        }
    }
    if ([int]$proof.candidate_rows -eq 0) {
        if (
            [int]$proof.missing_sources -ne 0 -or
            [int]$proof.conflicting_sources -ne 0 -or
            [int]$proof.invalid_rows -ne 0 -or
            [int]$proof.unique_year_sources -le 0 -or
            $null -eq $proof.terminal_authorized_year_sources -or
            [int]$proof.terminal_authorized_year_sources -ne [int]$proof.unique_year_sources
        ) {
            throw "evidence backfill zero-candidate proof is incomplete"
        }
        Log-Event "evidence_backfill_complete" @{
            sources=[int]$proof.unique_year_sources
            verified=[int]$proof.already_verified_year_sources
            human_audited=[int]$proof.human_audited_year_sources
            terminal_authorized=[int]$proof.terminal_authorized_year_sources
        }
        $script:CurrentYearEvidenceComplete = $true
        return $false
    }
    $candidateHash = Get-FileSha256 $candidate
    $resolverScript = Join-Path $RepoRoot "tools\resolve_capped_adjudication_queue.py"
    $resolverHash = Get-FileSha256 $resolverScript
    $traceInfo = Get-Item -LiteralPath $evidenceTracePath -ErrorAction Stop
    if (-not $candidateHash -or -not $resolverHash) {
        throw "evidence backfill authority hashes are unavailable"
    }

    # Never create another staging cycle after the prior cycle already inspected
    # the same authority.  The candidate file normally shrinks after safe rows are
    # finalized, so a whole-file hash alone is insufficient: the remaining IDs
    # must also be compared with the exact durable capped queues from that run.
    $snapshot = Read-JsonFile $evidenceDeferredSnapshotPath
    $snapshotAuthorityUnchanged = (
        $snapshot -and
        [string]$snapshot.resolver_sha256 -eq $resolverHash -and
        [int64]$snapshot.trace_length -eq [int64]$traceInfo.Length -and
        [int64]$snapshot.trace_last_write_utc_ticks -eq [int64]$traceInfo.LastWriteTimeUtc.Ticks -and
        (Test-Path -LiteralPath ([string]$snapshot.run_summary_csv))
    )
    if ($snapshotAuthorityUnchanged) {
        if (
            [string]$snapshot.residual_candidate_sha256 -eq $candidateHash -and
            [int]$snapshot.residual_candidate_rows -eq [int]$proof.candidate_rows
        ) {
            Log-Event "evidence_backfill_exact_residual_deferred" @{
                rows=[int]$proof.candidate_rows
                sha256=$candidateHash
                summary=[string]$snapshot.run_summary_csv
                fast_path=$true
            }
            return $true
        }
        try {
            $summaryRows = @(Import-Csv -LiteralPath ([string]$snapshot.run_summary_csv))
            $queued = 0
            $deferred = 0
            $aborted = 0
            foreach ($row in $summaryRows) {
                $queued += [int]($row.queued -as [int])
                $deferred += [int]($row.deferred_capped -as [int])
                if ([int]($row.aborted -as [int]) -ne 0) { $aborted++ }
            }
            if (
                [string]$snapshot.candidate_sha256 -eq $candidateHash -and
                [int]$snapshot.candidate_rows -eq [int]$proof.candidate_rows -and
                $summaryRows.Count -gt 0 -and
                $aborted -eq 0 -and
                $queued -eq [int]$proof.candidate_rows -and
                $deferred -gt 0
            ) {
                Log-Event "evidence_backfill_unchanged_deferred" @{
                    rows=[int]$proof.candidate_rows
                    deferred=$deferred
                    sha256=$candidateHash
                    summary=[string]$snapshot.run_summary_csv
                }
                return $true
            }

            if ($summaryRows.Count -gt 0 -and $aborted -eq 0 -and $deferred -gt 0) {
                $candidateRows = @(Import-Csv -LiteralPath $candidate)
                $candidateSet = Get-NormalizedSourceIdSetProof @($candidateRows | ForEach-Object { $_.source_item_id })
                $deferredIds = @()
                $queueProofValid = $true
                foreach ($row in $summaryRows) {
                    $rowDeferred = [int]($row.deferred_capped -as [int])
                    if ($rowDeferred -le 0) { continue }
                    $stagingDir = [System.IO.Path]::GetFullPath([string]$row.staging_dir)
                    $queuePath = Join-Path $stagingDir ".ocr_capped_adjudication_queue.json"
                    $queue = Read-JsonFile $queuePath
                    if (
                        -not $queue -or
                        [string]$queue.schema -ne "samsung-ocr-capped-adjudication-queue/v1" -or
                        -not ([System.IO.Path]::GetFullPath([string]$queue.image_dir)).Equals(
                            $stagingDir,
                            [System.StringComparison]::OrdinalIgnoreCase
                        ) -or
                        @($queue.items).Count -ne $rowDeferred
                    ) {
                        $queueProofValid = $false
                        break
                    }
                    foreach ($item in @($queue.items)) {
                        if (
                            [int]$item.consumed_calls -lt 3 -or
                            [string]$item.state -ne "awaiting_zero_model_adjudication" -or
                            $item.verified -eq $true -or
                            $item.uploaded -eq $true
                        ) {
                            $queueProofValid = $false
                            break
                        }
                        $deferredIds += [string]$item.source_item_id
                    }
                    if (-not $queueProofValid) { break }
                }
                $deferredSet = if ($queueProofValid) {
                    Get-NormalizedSourceIdSetProof $deferredIds
                } else { $null }
                if (
                    $candidateSet -and
                    $deferredSet -and
                    [int]$candidateSet.count -eq [int]$proof.candidate_rows -and
                    [int]$deferredSet.count -eq $deferred -and
                    [int]$candidateSet.count -eq [int]$deferredSet.count -and
                    [string]$candidateSet.sha256 -eq [string]$deferredSet.sha256
                ) {
                    $snapshot | Add-Member -NotePropertyName residual_candidate_sha256 -NotePropertyValue $candidateHash -Force
                    $snapshot | Add-Member -NotePropertyName residual_candidate_rows -NotePropertyValue ([int]$proof.candidate_rows) -Force
                    Write-JsonAtomic $evidenceDeferredSnapshotPath $snapshot
                    Log-Event "evidence_backfill_exact_residual_deferred" @{
                        rows=[int]$proof.candidate_rows
                        deferred=$deferred
                        sha256=$candidateHash
                        summary=[string]$snapshot.run_summary_csv
                        fast_path=$false
                    }
                    return $true
                }
                Alert "evidence_backfill_residual_proof_failed" @{
                    rows=[int]$proof.candidate_rows
                    deferred=$deferred
                    summary=[string]$snapshot.run_summary_csv
                    dashboard_kept_online=$true
                }
                return $true
            }
        } catch {
            Log-Event "evidence_backfill_deferred_snapshot_invalidated" @{
                detail=$_.Exception.Message
            }
        }
    }

    $driveRoot = [System.IO.Path]::GetPathRoot(
        [System.IO.Path]::GetFullPath($OutputDir)
    )
    $drive = [System.IO.DriveInfo]::new($driveRoot)
    $minimumFreeBytes = [int64](16GB)
    if ([int64]$drive.AvailableFreeSpace -lt $minimumFreeBytes) {
        Alert "evidence_backfill_disk_guard" @{
            available_bytes=[int64]$drive.AvailableFreeSpace
            required_bytes=$minimumFreeBytes
            dashboard_kept_online=$true
        }
        return $true
    }
    # The builder's canonical CSV is mutable and may change while a long runner is
    # active.  Give every runner an immutable, hash-verified snapshot so process
    # monitoring can never reinterpret a changed row list as a stopped batch.
    $freezeStamp = Get-Date -Format "yyyyMMdd_HHmmss_fffffff"
    $frozenCandidate = Join-Path $audit ("v1945_evidence_backfill_2026_frozen_{0}.csv" -f $freezeStamp)
    $frozenTemp = "$frozenCandidate.tmp.$PID"
    try {
        [System.IO.File]::Copy($candidate, $frozenTemp, $false)
        Move-Item -LiteralPath $frozenTemp -Destination $frozenCandidate -ErrorAction Stop
        $candidateHash = Get-FileSha256 $candidate
        $frozenHash = Get-FileSha256 $frozenCandidate
        if (-not $candidateHash -or $candidateHash -ne $frozenHash) {
            throw "evidence backfill frozen candidate hash mismatch"
        }
    } finally {
        if (Test-Path -LiteralPath $frozenTemp) {
            Remove-Item -LiteralPath $frozenTemp -Force -ErrorAction SilentlyContinue
        }
    }
    $candidate = $frozenCandidate
    $result = Join-Path $audit ("v1945_evidence_backfill_2026_frozen_{0}_results.csv" -f $freezeStamp)
    $summaryCsv = Join-Path $audit ("v1945_evidence_backfill_2026_frozen_{0}_run_summary.csv" -f $freezeStamp)
    Write-JsonAtomic $evidenceDeferredSnapshotPath ([ordered]@{
        schema="samsung-ocr-evidence-backfill-cycle/v1"
        started_at=(Get-Date).ToString("o")
        candidate_sha256=$candidateHash
        candidate_rows=[int]$proof.candidate_rows
        resolver_sha256=$resolverHash
        trace_length=[int64]$traceInfo.Length
        trace_last_write_utc_ticks=[int64]$traceInfo.LastWriteTimeUtc.Ticks
        frozen_candidate=$candidate
        run_summary_csv=$summaryCsv
    })
    Log-Event "evidence_backfill_candidate_frozen" @{
        rows=[int]$proof.candidate_rows
        candidate=$candidate
        sha256=$frozenHash
    }
    $stagingRoot = Join-Path $OutputDir "_ocr_staging"
    $live = Get-BackendStatus
    $liveImageDir = if ($live) { [string]$live.image_dir } else { "" }
    $runnerMode = "--execute"
    $runnerModeArgs = @("--execute")
    if (
        $liveImageDir -and
        (Test-Path -LiteralPath $liveImageDir -PathType Container) -and
        [System.IO.Path]::GetFullPath($liveImageDir).StartsWith(
            [System.IO.Path]::GetFullPath($stagingRoot),
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        $runnerMode = "--resume-existing-then-continue"
        $runnerModeArgs += "--resume-existing-then-continue"
    }
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $runnerArgs = @(
        $stagedScript,
        "--source-root",$SourceRoot,
        "--output-dir",$OutputDir,
        "--backend-url",$BackendUrl,
        "--input-csv",$candidate,
        "--output-csv",$result,
        "--run-summary-csv",$summaryCsv
    ) + $runnerModeArgs + @(
        "--poll-seconds","10","--timeout-minutes","10080"
    )
    Start-Hidden -File $python -ProcessArgs $runnerArgs `
        -OutFile (Join-Path $logDir "supervisor_evidence_backfill_$stamp.out.log") `
        -ErrFile (Join-Path $logDir "supervisor_evidence_backfill_$stamp.err.log")
    Log-Event "evidence_backfill_restarted" @{
        remaining=[int]$proof.candidate_rows
        sources=[int]$proof.unique_year_sources
        upload_queue_terminal=[int]$proof.current_upload_queue_source_ids
        runner_mode=$runnerMode
        live_image_dir=$liveImageDir
    }
    return $true
}

try {
    try {
        $null = New-Item -ItemType File -Path $lockPath -ErrorAction Stop
        Set-Content -LiteralPath $lockPath -Value (([ordered]@{pid=$PID;started_at=(Get-Date).ToString("o");repo=$RepoRoot})|ConvertTo-Json) -Encoding UTF8
    } catch { Log-Event "duplicate_or_locked"; exit 0 }

    $status = Get-BackendStatus
    $pipelinePaused = Test-Path -LiteralPath $PipelinePausePath
    $pause = $null
    $continuityDir = ""
    if ($pipelinePaused) {
        # pipeline_pause is a mutation interlock, not an interface shutdown.
        # On reboot restore the idle Dashboard/API against the saved staging
        # directory and keep the pause-aware upload status worker alive.  No
        # runner, model call, bulk upload or checkpoint reset is started here.
        $pause = Read-JsonFile $PipelinePausePath
        $continuityDir = Get-PausedContinuityDir $pause
        try {
            $status = Start-BackendService $continuityDir
            $streamUploader = @(Ensure-StreamUploaderOnline)
        } catch {
            Alert "pipeline_pause_interface_recovery_failed" @{
                pause=$PipelinePausePath
                detail=$_.Exception.Message
            }
            exit 16
        }
        if ([bool]$status.is_running) {
            Alert "pipeline_pause_backend_not_idle" @{
                pause=$PipelinePausePath
                image_dir=[string]$status.image_dir
            }
            exit 17
        }
        if ($pause -and [string]$pause.current_dir) {
            try {
                $liveDir = [System.IO.Path]::GetFullPath([string]$status.image_dir)
            } catch { $liveDir = "" }
            if (-not $liveDir.Equals($continuityDir, [System.StringComparison]::OrdinalIgnoreCase)) {
                Alert "pipeline_pause_checkpoint_mismatch" @{
                    pause=$PipelinePausePath
                    saved_checkpoint=$continuityDir
                    live_image_dir=$liveDir
                }
                exit 19
            }
        }
        if (-not $status.pipeline_pause) {
            Alert "pipeline_pause_not_visible_in_status" @{pause=$PipelinePausePath}
            exit 18
        }
        Log-Event "pipeline_pause_interface_maintained" @{
            pause=$PipelinePausePath
            reason=if($pause){[string]$pause.reason}else{"unreadable"}
            checkpoint=$continuityDir
            backend=@(Owned "samsung_ocr_batch_processor\.py").Count
            stream_uploader=$streamUploader.Count
            stream_pending=(Get-StreamPendingCount)
        }
    }
    $fuseRecovered = $false
    if (Test-Path -LiteralPath $RuntimeHealthFusePath) {
        $fuseRecovered = Try-AutoRecoverKnownReviewMetadataFuse
        if (-not $fuseRecovered) {
            $fuseRecovered = Try-AutoRecoverFirstPassPhotoLocalFuse
        }
        if (-not $fuseRecovered) {
            $fuseRecovered = Try-AutoRecoverContainedRequestBindingFuse
        }
        if ($fuseRecovered) {
            $status = Get-BackendStatus
        } else {
            Alert "runtime_health_fuse_active" @{fuse=$RuntimeHealthFusePath}
            exit 9
        }
    }
    if ($pipelinePaused) {
        if ($fuseRecovered) {
            if (-not $pause -or -not [string]$pause.current_dir) {
                Alert "pipeline_pause_resume_checkpoint_missing" @{pause=$PipelinePausePath}
                exit 20
            }
            try {
                $status = Resume-RepairedPausedCheckpoint $continuityDir
            } catch {
                Alert "pipeline_pause_auto_resume_failed" @{
                    pause=$PipelinePausePath
                    checkpoint=$continuityDir
                    detail=$_.Exception.Message
                }
                exit 21
            }
            # Resume only the exact repaired batch in this cycle.  Later
            # supervisor passes may advance periods after its normal gates.
            exit 0
        }
        if (
            $pause -and
            [string]$pause.reason -eq "capped_zero_model_adjudication_apply"
        ) {
            $activeBackfill = @(Owned "rerun_staged_candidates\.py")
            if ($activeBackfill.Count -gt 1) {
                Alert "capped_zero_model_resolver_not_unique" @{
                    workers=$activeBackfill.Count
                    checkpoint=$continuityDir
                }
                exit 22
            }
            if ($activeBackfill.Count -eq 1) {
                Log-Event "capped_zero_model_resolver_active" @{
                    checkpoint=$continuityDir
                    pid=$activeBackfill[0].ProcessId
                }
                exit 0
            }
            try {
                $started = Start-EvidenceBackfillIfNeeded
            } catch {
                Alert "capped_zero_model_resolver_start_failed" @{
                    checkpoint=$continuityDir
                    detail=$_.Exception.Message
                }
                exit 23
            }
            if (-not $started) {
                Alert "capped_zero_model_resolver_missing_work" @{
                    checkpoint=$continuityDir
                }
                exit 24
            }
            Log-Event "capped_zero_model_resolver_started" @{
                checkpoint=$continuityDir
            }
            exit 0
        }
        exit 0
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
    $streamUploader = @(Owned "stream_drive_upload\.py")
    $streamPendingCount = Get-StreamPendingCount
    $pipelineTransitionStarted = $false

    try {
        $streamUploader = @(Ensure-StreamUploaderOnline)
    } catch {
        Alert "stream_uploader_recovery_failed" @{
            pending=$streamPendingCount
            workers=$streamUploader.Count
            detail=$_.Exception.Message
        }
        exit 12
    }

    if ($status -and [bool]$status.is_running) {
        # A backend can be resumed directly against the saved historical
        # folder while the recursive coordinator is absent (for example after
        # a reboot or a contained fuse repair).  Do not treat that as a healthy
        # terminal state: attach the coordinator to the already-running exact
        # folder so it can rebuild the canonical progress floor and continue
        # with the next folder.  This does not restart the backend or browser.
        $runningHistoricalFolder = (
            -not [string]::IsNullOrWhiteSpace([string]$status.current_relative_dir) -and
            [string]$status.current_relative_dir -notmatch '(^|\\)商化照片-2026\d{2}$'
        )
        if (
            $runningHistoricalFolder -and
            $recursive.Count -eq 0 -and
            $staged.Count -eq 0 -and
            (Test-Path -LiteralPath $historicalContinuationReceipt -PathType Leaf)
        ) {
            $stamp=Get-Date -Format "yyyyMMdd_HHmmss"
            Start-Hidden -File $python -ProcessArgs @(
                $recursiveScript,
                "--source-root",$SourceRoot,
                "--output-dir",$OutputDir,
                "--backend-url",$BackendUrl,
                "--api-base",$ApiBase,
                "--api-key","lm-studio",
                "--model",$Model,
                "--poll-seconds","20",
                "--timeout-minutes","10080",
                "--historical-continuation-receipt",$historicalContinuationReceipt
            ) -OutFile (Join-Path $logDir "supervisor_attach_recursive_$stamp.out.log") -ErrFile (Join-Path $logDir "supervisor_attach_recursive_$stamp.err.log")
            Start-Sleep -Seconds 1
            $recursive = @(Owned "recursive_ocr_flat_export\.py")
            if ($recursive.Count -eq 1) {
                Log-Event "historical_coordinator_attached_to_running_backend" @{
                    recursive_pid=[int]$recursive[0].ProcessId
                    folder=[string]$status.current_relative_dir
                    processed=[int]$status.stats.processed
                    total=[int]$status.stats.total
                    dashboard_kept_online=$true
                }
            } else {
                Alert "historical_coordinator_attach_failed" @{
                    recursive=$recursive.Count
                    folder=[string]$status.current_relative_dir
                    dashboard_kept_online=$true
                }
            }
        }
        Write-PipelineStatus -Active $true -Phase "photo_ocr" -WorkerPid ([int]$backend[0].ProcessId)
        Log-Event "healthy_noop" @{backend=$backend.Count;watcher=$watcher.Count;staged=$staged.Count;recursive=$recursive.Count;uploader=$uploader.Count;stream_uploader=$streamUploader.Count;stream_pending=$streamPendingCount;folder=$status.current_relative_dir;file=$status.current_file}
        exit 0
    }

    if ($backend.Count -gt 0 -and -not $status) {
        Alert "backend_process_exists_but_api_unhealthy" @{backend_pids=@($backend.ProcessId)}
        exit 3
    }

    # Runtime source may be patched while a long staged run is active. Never
    # restart a running photo or an owned runner. At the first completely idle
    # boundary, hand the zero-work reload to a hidden helper and exit so the
    # next supervisor cycle observes only the freshly loaded backend.
    $safeReload = @(Owned "reload_backend_at_safe_idle\.ps1")
    if ($safeReload.Count -gt 1) {
        Alert "safe_idle_reload_duplicate" @{workers=@($safeReload.ProcessId)}
        exit 14
    }
    if ($safeReload.Count -eq 1) {
        Log-Event "safe_idle_reload_in_progress" @{pid=$safeReload[0].ProcessId}
        exit 0
    }
    if (
        $status -and
        $backend.Count -eq 1 -and
        $watcher.Count -eq 0 -and
        $staged.Count -eq 0 -and
        $recursive.Count -eq 0
    ) {
        $latestRuntimeWrite = Get-LatestBackendRuntimeWrite
        $backendCreated = [datetime]$backend[0].CreationDate
        if ($latestRuntimeWrite -and $latestRuntimeWrite -gt $backendCreated.AddSeconds(2)) {
            if (-not (Test-Path -LiteralPath $safeIdleReloadScript -PathType Leaf)) {
                Alert "safe_idle_reload_helper_missing" @{path=$safeIdleReloadScript}
                exit 15
            }
            $stamp=Get-Date -Format "yyyyMMdd_HHmmss"
            $reloadArgs = @(
                "-NoProfile","-ExecutionPolicy","Bypass","-File",$safeIdleReloadScript,
                "-RepoRoot",$RepoRoot,
                "-SourceRoot",$SourceRoot,
                "-OutputDir",$OutputDir,
                "-BackendUrl",$BackendUrl,
                "-ApiBase",$ApiBase,
                "-Model",$Model
            )
            $incompleteStoppedBatch = (
                $status.stats -and
                [int]$status.stats.total -gt 0 -and
                [int]$status.stats.processed -lt [int]$status.stats.total
            )
            if ($incompleteStoppedBatch) {
                # The helper still verifies a completely idle photo boundary,
                # exact staging ownership, one backend process and no active
                # runner. This flag permits it to reload current code and
                # resume that same saved checkpoint instead of failing every
                # supervisor cycle merely because the batch is incomplete.
                $reloadArgs += "-AllowIncompleteStoppedBatch"
            }
            Start-Hidden -File "powershell.exe" -ProcessArgs $reloadArgs -OutFile (Join-Path $logDir "safe_idle_reload_$stamp.out.log") -ErrFile (Join-Path $logDir "safe_idle_reload_$stamp.err.log")
            Log-Event "safe_idle_reload_started" @{
                backend_created=$backendCreated.ToString("o")
                latest_runtime_write=$latestRuntimeWrite.ToString("o")
                helper=$safeIdleReloadScript
                incomplete_stopped_batch=$incompleteStoppedBatch
                processed=[int]$status.stats.processed
                total=[int]$status.stats.total
            }
            exit 0
        }
    }

    $lm = Get-LmModels
    if (-not $lm) {
        Log-Event "lm_server_recovery_attempt"
        $r = Invoke-Lms @("server","start","--bind","127.0.0.1")
        Start-Sleep -Seconds 5
        $lm = Get-LmModels
        if (-not $lm) { Alert "lm_server_unavailable"; exit 4 }
    }
    $loaded = @(Get-LmProcessInfo)
    if ($loaded.Count -gt 0 -and @($loaded | Where-Object { $_.model -ne $Model }).Count -gt 0) {
        Alert "different_model_already_loaded" @{loaded=$loaded}
        exit 5
    }
    $requiredLoaded = @($loaded | Where-Object { $_.model -eq $Model })
    if (
        $requiredLoaded.Count -eq 1 -and
        (
            [int]$requiredLoaded[0].context -ne $ContextLength -or
            [int]$requiredLoaded[0].parallel -ne $Parallel
        )
    ) {
        $unload = Invoke-Lms @("unload",[string]$requiredLoaded[0].identifier)
        if (-not $unload -or $unload.exit -ne 0) {
            Alert "qwen_reload_unload_failed" @{
                identifier=$requiredLoaded[0].identifier
                context=$requiredLoaded[0].context
                parallel=$requiredLoaded[0].parallel
            }
            exit 7
        }
        Log-Event "qwen_reload_for_runtime_contract" @{
            old_context=$requiredLoaded[0].context
            old_parallel=$requiredLoaded[0].parallel
            new_context=$ContextLength
            new_parallel=$Parallel
        }
        $loaded = @()
    }
    if ($loaded.Count -eq 0) {
        $inventory = Invoke-Lms @("ls")
        if (-not $inventory -or $inventory.output -notmatch [regex]::Escape($Model)) {
            Alert "required_local_model_unavailable" @{model=$Model}
            exit 6
        }
        $load = Invoke-Lms @(
            "load",$Model,
            "--context-length",$ContextLength,
            "--parallel",$Parallel,
            "--gpu","max",
            "--yes"
        )
        if (-not $load -or $load.exit -ne 0) {
            Alert "qwen_load_failed" @{model=$Model;context=$ContextLength;parallel=$Parallel}
            exit 7
        }
    }

    if (-not $status -and $backend.Count -eq 0) {
        $stamp=Get-Date -Format "yyyyMMdd_HHmmss"
        Start-Hidden -File $python -ProcessArgs @(
            $backendScript,
            "--api_base",$ApiBase,
            "--api_key","lm-studio",
            "--model",$Model,
            "--dir",$SourceRoot,
            "--port",[string]$backendPort,
            "--no_followme_auto_update"
        ) -OutFile (Join-Path $logDir "supervisor_backend_$stamp.out.log") -ErrFile (Join-Path $logDir "supervisor_backend_$stamp.err.log")
        for ($attempt = 0; $attempt -lt 45 -and -not $status; $attempt++) {
            Start-Sleep -Seconds 1
            $status = Get-BackendStatus
        }
        if (-not $status) {
            Alert "backend_start_timeout" @{model=$Model;port=$backendPort}
            exit 13
        }
        $backend = @(Owned "samsung_ocr_batch_processor\.py")
        Log-Event "backend_started" @{model=$Model;port=$backendPort;backend=$backend.Count}
    }

    if ($recursive.Count -eq 1 -and $staged.Count -eq 0) {
        $recursiveCommand = [string]$recursive[0].CommandLine
        if (
            -not (Test-Path -LiteralPath $historicalContinuationReceipt -PathType Leaf) -or
            $recursiveCommand -notmatch [regex]::Escape("--historical-continuation-receipt") -or
            $recursiveCommand -notmatch [regex]::Escape($historicalContinuationReceipt)
        ) {
            Alert "historical_pipeline_authority_missing" @{
                recursive_pid=[int]$recursive[0].ProcessId
                receipt=$historicalContinuationReceipt
            }
            exit 8
        }
        Clear-Alert
        Write-PipelineStatus -Active $true -Phase "historical_continuation" -WorkerPid ([int]$recursive[0].ProcessId)
        Log-Event "historical_pipeline_active" @{
            recursive_pid=[int]$recursive[0].ProcessId
            watcher=$watcher.Count
            folder=$status.current_relative_dir
            processed=$status.overall_progress.processed_images
            total=$status.overall_progress.total_images
            dashboard_kept_online=$true
        }
        exit 0
    }
    if ($staged.Count -gt 0 -or $recursive.Count -gt 0) {
        Alert "staged_or_recursive_state_ambiguous" @{staged=$staged.Count;recursive=$recursive.Count}
        exit 8
    }
    $fullProjectDone = Test-FullProjectCompletionMarker
    # A marker/gate-bound handoff is stronger than mutable recursive folder
    # summaries. Check it before rebuilding current-year evidence so a later
    # interrupted historical runner can never send 2026 back through OCR.
    $fullProjectReady = if ($fullProjectDone) { $false } else { Full-Project-ContinuationReady }
    $streamPendingCount = Get-StreamPendingCount
    $streamWorkingCount = Get-StreamWorkingCount
    if ($streamPendingCount -gt 0 -or $streamWorkingCount -gt 0) {
        # Current-revision pending/working jobs are now source/hash/result-bound
        # terminal authorities consumed by the builder.  OCR may therefore
        # continue while the slower network queue drains without re-staging the
        # same photos.  A failed job leaves these directories and becomes
        # eligible again unless a confirmed receipt exists.
        Log-Event "evidence_backfill_concurrent_with_stream_upload" @{
            pending=$streamPendingCount
            working=$streamWorkingCount
        }
    }
    if (-not $fullProjectDone -and -not $fullProjectReady -and (Start-EvidenceBackfillIfNeeded)) {
        $pipelineTransitionStarted = $true
        exit 0
    }
    if (
        $script:CurrentYearEvidenceComplete -and
        ($streamPendingCount -gt 0 -or $streamWorkingCount -gt 0)
    ) {
        # Per-photo streaming is already the authoritative transport. Do not
        # start the legacy finalizer/bulk uploader against the same jobs.
        Log-Event "current_year_finalization_waiting_for_stream_upload" @{
            pending=$streamPendingCount
            working=$streamWorkingCount
        }
        exit 0
    }
    if (-not $fullProjectDone -and -not $fullProjectReady) {
        $fullProjectReady = Full-Project-ContinuationReady
    }
    if ($fullProjectDone) {
        Write-PipelineStatus -Active $false -Phase "completed" -WorkerPid 0
        Log-Event "full_project_complete_noop" @{marker=$fullProjectCompletePath}
    } elseif ($fullProjectReady -and $watcher.Count -eq 0) {
        $stamp=Get-Date -Format "yyyyMMdd_HHmmss"
        Start-Hidden -File $python -ProcessArgs @(
            $recursiveScript,
            "--source-root",$SourceRoot,
            "--output-dir",$OutputDir,
            "--backend-url",$BackendUrl,
            "--api-base",$ApiBase,
            "--api-key","lm-studio",
            "--model",$Model,
            "--poll-seconds","20",
            "--timeout-minutes","10080",
            "--historical-continuation-receipt",$historicalContinuationReceipt
        ) -OutFile (Join-Path $logDir "supervisor_full_recursive_$stamp.out.log") -ErrFile (Join-Path $logDir "supervisor_full_recursive_$stamp.err.log")
        Start-Sleep -Seconds 1
        Start-Hidden -File "powershell.exe" -ProcessArgs @(
            "-NoProfile","-ExecutionPolicy","Bypass","-File",$watcherScript,
            "-RepoRoot",$RepoRoot,"-SourceRoot",$SourceRoot,"-OutputDir",$OutputDir,
            "-BackendUrl",$BackendUrl,"-PollSeconds","300","-PrimaryPasses","2",
            "-SkipCurrentYearPhases","-SkipRecursiveResume"
        ) -OutFile (Join-Path $logDir "supervisor_full_watcher_$stamp.out.log") -ErrFile (Join-Path $logDir "supervisor_full_watcher_$stamp.err.log")
        $pipelineTransitionStarted = $true
        Log-Event "full_project_pipeline_started" @{request=$fullProjectRequestPath;current_year_marker=$currentYearCompletePath}
    } elseif ($watcher.Count -eq 0) {
        $stamp=Get-Date -Format "yyyyMMdd_HHmmss"
        $watcherArgs = @("-NoProfile","-ExecutionPolicy","Bypass","-File",$watcherScript,"-RepoRoot",$RepoRoot,"-SourceRoot",$SourceRoot,"-OutputDir",$OutputDir,"-BackendUrl",$BackendUrl,"-PollSeconds","300","-PrimaryPasses","2","-CurrentYearOnly")
        if ($script:CurrentYearEvidenceComplete) {
            $watcherArgs += "-FinalizeCurrentYearOnly"
        }
        Start-Hidden -File "powershell.exe" -ProcessArgs $watcherArgs -OutFile (Join-Path $logDir "supervisor_watcher_$stamp.out.log") -ErrFile (Join-Path $logDir "supervisor_watcher_$stamp.err.log")
        $pipelineTransitionStarted = $true
        Log-Event "current_year_watcher_started" @{
            finalize_only=[bool]$script:CurrentYearEvidenceComplete
        }
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
            Start-Hidden -File $python -ProcessArgs @($bulkUploaderScript,"--output-dir",$OutputDir,"--execute","--repeat","--limit","100","--transfers","4","--checkers","8","--rclone-timeout-seconds","1200") -OutFile (Join-Path $logDir "supervisor_uploader_$stamp.out.log") -ErrFile (Join-Path $logDir "supervisor_uploader_$stamp.err.log")
            Log-Event "ready_uploader_started" @{pending=$pendingCount;proof=$uploadGateProofPath}
        }
    }
} finally {
    Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
}

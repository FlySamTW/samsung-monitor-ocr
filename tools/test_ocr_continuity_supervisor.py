from pathlib import Path
import unittest


class ContinuitySupervisorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tools_dir = Path(__file__).resolve().parent
        cls.source = (tools_dir / "ocr_continuity_supervisor.ps1").read_text(encoding="utf-8")
        cls.historical_gate_source = (tools_dir / "historical_continuation_gate.py").read_text(encoding="utf-8")

    def test_lock_and_healthy_noop(self):
        self.assertIn("New-Item -ItemType File -Path $lockPath", self.source)
        self.assertIn('$BenchmarkLockPath = Join-Path $audit "model_benchmark.lock"', self.source)
        self.assertIn('planned_backend_upgrade_interlock', self.source)
        self.assertIn('"healthy_noop"', self.source)
        self.assertIn("finally", self.source)

    def test_dead_planned_upgrade_owner_resumes_backfill_fail_closed(self):
        self.assertIn("planned_backend_upgrade_recovery_active", self.source)
        self.assertIn("planned_backend_upgrade_recovery_contract_failed", self.source)
        self.assertIn("planned_backend_upgrade_recovery_failed", self.source)
        self.assertIn("planned_backend_upgrade_recovery_started", self.source)
        self.assertIn("planned_backend_upgrade_recovery_completed", self.source)
        self.assertIn("$backfillStarted = Start-EvidenceBackfillIfNeeded", self.source)
        self.assertIn("Get-Process -Id ([int]$planned.pid) -ErrorAction SilentlyContinue", self.source)
        self.assertIn("evidence backfill zero-candidate proof is incomplete", self.source)
        self.assertIn("terminal_authorized_year_sources", self.source)
        self.assertNotIn(
            "[int]$proof.already_verified_year_sources -ne [int]$proof.unique_year_sources",
            self.source,
        )
        recovery = self.source.index("$backfillStarted = Start-EvidenceBackfillIfNeeded")
        release = self.source.index("Remove-Item -LiteralPath $BenchmarkLockPath -Force", recovery)
        self.assertGreater(release, recovery)

    def test_backfill_resumes_only_when_backend_is_already_inside_staging(self):
        self.assertIn('$stagingRoot = Join-Path $OutputDir "_ocr_staging"', self.source)
        self.assertIn('$runnerMode = "--execute"', self.source)
        self.assertIn('$runnerMode = "--resume-existing-then-continue"', self.source)
        self.assertIn("[System.StringComparison]::OrdinalIgnoreCase", self.source)
        self.assertIn('$runnerModeArgs = @("--execute")', self.source)
        self.assertIn('$runnerModeArgs += "--resume-existing-then-continue"', self.source)
        self.assertIn(") + $runnerModeArgs + @(", self.source)

    def test_backfill_runner_uses_immutable_hash_verified_candidate_snapshot(self):
        function_start = self.source.index("function Start-EvidenceBackfillIfNeeded")
        function_end = self.source.index("\ntry {", function_start)
        backfill = self.source[function_start:function_end]

        self.assertIn("v1945_evidence_backfill_2026_frozen_", backfill)
        self.assertIn("[System.IO.File]::Copy($candidate, $frozenTemp, $false)", backfill)
        self.assertIn("Move-Item -LiteralPath $frozenTemp -Destination $frozenCandidate", backfill)
        self.assertIn("$candidateHash = Get-FileSha256 $candidate", backfill)
        self.assertIn("$frozenHash = Get-FileSha256 $frozenCandidate", backfill)
        self.assertIn("evidence backfill frozen candidate hash mismatch", backfill)
        self.assertIn("$candidate = $frozenCandidate", backfill)
        self.assertIn('"--input-csv",$candidate', backfill)
        self.assertIn('"evidence_backfill_candidate_frozen"', backfill)
        self.assertLess(
            backfill.index("$candidate = $frozenCandidate"),
            backfill.index('"--input-csv",$candidate'),
        )

    def test_unchanged_deferred_backfill_and_low_disk_never_restage(self):
        function_start = self.source.index("function Start-EvidenceBackfillIfNeeded")
        function_end = self.source.index("\ntry {", function_start)
        backfill = self.source[function_start:function_end]

        for token in (
            "evidence_backfill_deferred_snapshot.json",
            "candidate_sha256",
            "resolver_sha256",
            "trace_length",
            "trace_last_write_utc_ticks",
            "evidence_backfill_unchanged_deferred",
            "evidence_backfill_disk_guard",
            "dashboard_kept_online=$true",
        ):
            self.assertIn(token, self.source)
        self.assertLess(
            backfill.index('"evidence_backfill_unchanged_deferred"'),
            backfill.index("[System.IO.File]::Copy($candidate, $frozenTemp, $false)"),
        )
        self.assertLess(
            backfill.index('Alert "evidence_backfill_disk_guard"'),
            backfill.index("[System.IO.File]::Copy($candidate, $frozenTemp, $false)"),
        )

    def test_exact_residual_deferred_set_is_not_restaged(self):
        function_start = self.source.index("function Start-EvidenceBackfillIfNeeded")
        function_end = self.source.index("\ntry {", function_start)
        backfill = self.source[function_start:function_end]

        for token in (
            "Get-NormalizedSourceIdSetProof",
            ".ocr_capped_adjudication_queue.json",
            "samsung-ocr-capped-adjudication-queue/v1",
            "awaiting_zero_model_adjudication",
            "residual_candidate_sha256",
            "residual_candidate_rows",
            "evidence_backfill_exact_residual_deferred",
            "evidence_backfill_residual_proof_failed",
        ):
            self.assertIn(token, self.source)
        self.assertLess(
            backfill.index('Log-Event "evidence_backfill_exact_residual_deferred"'),
            backfill.index("[System.IO.File]::Copy($candidate, $frozenTemp, $false)"),
        )
        self.assertLess(
            backfill.index('Alert "evidence_backfill_residual_proof_failed"'),
            backfill.index("[System.IO.File]::Copy($candidate, $frozenTemp, $false)"),
        )

    def test_hidden_launches_use_named_nonempty_arguments(self):
        self.assertIn("[string[]]$ProcessArgs", self.source)
        self.assertIn("hidden process launch contains an empty executable or argument", self.source)
        self.assertIn("hidden process launch requires output paths", self.source)
        self.assertNotIn("Start-Hidden $python @(", self.source)
        self.assertNotIn('Start-Hidden "powershell.exe" @(', self.source)
        self.assertGreaterEqual(self.source.count("Start-Hidden -File"), 6)
        self.assertGreaterEqual(self.source.count("-ProcessArgs"), 6)
        self.assertGreaterEqual(self.source.count("-OutFile"), 6)
        self.assertGreaterEqual(self.source.count("-ErrFile"), 6)

    def test_exact_repo_owned_processes_and_fail_closed_hung(self):
        self.assertIn("[regex]::Escape($RepoRoot)", self.source)
        self.assertIn("$matchedPids[[int]$process.ProcessId] = $true", self.source)
        self.assertIn("-not $matchedPids.ContainsKey([int]$_.ParentProcessId)", self.source)
        self.assertIn('"backend_process_exists_but_api_unhealthy"', self.source)
        self.assertIn('"staged_or_recursive_state_ambiguous"', self.source)
        self.assertNotIn("Stop-Process", self.source)

    def test_pipeline_pause_keeps_interface_services_online_without_mutation_resume(self):
        flow_start = self.source.index(
            "    $pipelinePaused = Test-Path -LiteralPath $PipelinePausePath"
        )
        flow_end = self.source.index(
            "    if (Test-Path -LiteralPath $RuntimeHealthFusePath)",
            flow_start,
        )
        paused_flow = self.source[flow_start:flow_end]

        self.assertNotIn('"pipeline_pause_noop"', self.source)
        self.assertIn("Get-PausedContinuityDir $pause", paused_flow)
        self.assertIn("Start-BackendService $continuityDir", paused_flow)
        self.assertIn("Ensure-StreamUploaderOnline", paused_flow)
        self.assertIn('"pipeline_pause_interface_maintained"', paused_flow)
        self.assertIn('"pipeline_pause_backend_not_idle"', paused_flow)
        self.assertIn('"pipeline_pause_checkpoint_mismatch"', paused_flow)
        self.assertIn('"pipeline_pause_not_visible_in_status"', paused_flow)
        self.assertNotIn("Start-EvidenceBackfillIfNeeded", paused_flow)
        self.assertNotIn("$watcherScript", paused_flow)
        self.assertNotIn("$bulkUploaderScript", paused_flow)

    def test_utf8_pause_path_and_exact_capped_resolver_are_self_healing(self):
        self.assertIn(
            "Get-Content -LiteralPath $Path -Raw -Encoding UTF8",
            self.source,
        )
        flow_start = self.source.index(
            "    if ($pipelinePaused) {\n        if ($fuseRecovered)"
        )
        flow_end = self.source.index(
            "    if (Test-Path -LiteralPath $BenchmarkLockPath)",
            flow_start,
        )
        repair_flow = self.source[flow_start:flow_end]
        self.assertIn(
            '[string]$pause.reason -eq "capped_zero_model_adjudication_apply"',
            repair_flow,
        )
        self.assertIn('Owned "rerun_staged_candidates\\.py"', repair_flow)
        self.assertIn('"capped_zero_model_resolver_active"', repair_flow)
        self.assertIn("Start-EvidenceBackfillIfNeeded", repair_flow)
        self.assertIn('"capped_zero_model_resolver_started"', repair_flow)
        self.assertIn('"capped_zero_model_resolver_start_failed"', repair_flow)

    def test_paused_backend_restores_only_saved_staging_checkpoint(self):
        function_start = self.source.index("function Get-PausedContinuityDir")
        function_end = self.source.index("function Get-FileSha256", function_start)
        checkpoint = self.source[function_start:function_end]

        self.assertIn("[string]$Pause.current_dir", checkpoint)
        self.assertIn('Join-Path $OutputDir "_ocr_staging"', checkpoint)
        self.assertIn("[System.IO.Path]::GetFullPath($SourceRoot)", checkpoint)
        self.assertIn("[System.StringComparison]::OrdinalIgnoreCase", checkpoint)
        self.assertIn("Test-Path -LiteralPath $candidate -PathType Container", checkpoint)
        self.assertIn("return $fallback", checkpoint)

    def test_only_successful_exact_fuse_repair_auto_resumes_saved_checkpoint(self):
        function_start = self.source.index(
            "function Resume-RepairedPausedCheckpoint"
        )
        function_end = self.source.index("function Get-FileSha256", function_start)
        resume = self.source[function_start:function_end]
        flow_start = self.source.index("    if ($pipelinePaused) {\n        if ($fuseRecovered)")
        flow_end = self.source.index(
            "    if (Test-Path -LiteralPath $BenchmarkLockPath)",
            flow_start,
        )
        repaired_flow = self.source[flow_start:flow_end]

        self.assertIn('"$BackendUrl/api/start_batch"', resume)
        self.assertIn("dir=$Checkpoint", resume)
        self.assertIn("restart=$false", resume)
        self.assertIn("confirmed=$true", resume)
        self.assertIn("reprocess_last_n=0", resume)
        self.assertIn("[bool]$candidate.is_running", resume)
        self.assertIn(
            "[int]$candidate.stats.processed -eq [int]$candidate.stats.total",
            resume,
        )
        self.assertIn("$resumed.pipeline_pause", resume)
        self.assertIn("Test-Path -LiteralPath $PipelinePausePath", resume)
        self.assertIn('"pipeline_pause_checkpoint_auto_resumed"', resume)
        self.assertIn(
            "$status = Resume-RepairedPausedCheckpoint $continuityDir",
            repaired_flow,
        )
        self.assertIn('"pipeline_pause_resume_checkpoint_missing"', repaired_flow)
        self.assertIn('"pipeline_pause_auto_resume_failed"', repaired_flow)
        self.assertLess(
            self.source.index("Ensure-StreamUploaderOnline", flow_start - 4000),
            self.source.index(
                "$status = Resume-RepairedPausedCheckpoint $continuityDir",
                flow_start,
            ),
        )

    def test_recovery_order_and_safe_model_gate(self):
        self.assertLess(self.source.index("lm_server_recovery_attempt"), self.source.index("backend_started"))
        self.assertIn('"different_model_already_loaded"', self.source)
        self.assertIn("function Invoke-Lms([string[]]$CommandArgs)", self.source)
        self.assertIn("& $lms @CommandArgs", self.source)
        self.assertNotIn("function Invoke-Lms([string[]]$Args)", self.source)
        self.assertIn('Invoke-Lms @("ps")', self.source)
        self.assertIn('$inventory = Invoke-Lms @("ls")', self.source)
        self.assertNotIn('@("ls","--json")', self.source)
        self.assertIn(r"-replace '\x1B\[[0-?]*[ -/]*[@-~]', ''", self.source)
        self.assertIn('"qwen_reload_for_runtime_contract"', self.source)
        self.assertIn('"--context-length",$ContextLength', self.source)
        self.assertIn("[int]$Parallel = 1", self.source)
        self.assertIn('"--parallel",$Parallel', self.source)
        self.assertIn('"--gpu","max"', self.source)
        self.assertIn('"qwen/qwen3-vl-8b"', self.source)

    def test_backend_uses_configured_port_and_waits_for_api(self):
        self.assertIn('$backendPort = ([uri]$BackendUrl).Port', self.source)
        self.assertIn('"--port",[string]$backendPort', self.source)
        self.assertIn('"--no_followme_auto_update"', self.source)
        launch = self.source.index('$backendScript,')
        wait = self.source.index('for ($attempt = 0; $attempt -lt 45', launch)
        timeout = self.source.index('"backend_start_timeout"', wait)
        self.assertLess(launch, wait)
        self.assertLess(wait, timeout)

    def test_stream_uploader_is_repaired_before_healthy_noop(self):
        repair = self.source.index('"stream_uploader_started"')
        noop = self.source.index('"healthy_noop"')
        self.assertLess(repair, noop)
        self.assertIn('"tools\\stream_drive_upload.py"', self.source)
        self.assertIn('"stream_uploader_recovery_failed"', self.source)
        self.assertIn("stream_pending=$streamPendingCount", self.source)
        self.assertIn("$streamUploader = @(Ensure-StreamUploaderOnline)", self.source)
        self.assertNotIn(
            "$streamPendingCount -gt 0 -and $streamUploader.Count -eq 0",
            self.source,
        )

    def test_backfill_uses_durable_upload_queue_without_waiting_for_receipts(self):
        self.assertIn('$streamWorkingDir = Join-Path $OutputDir "_drive_upload_stream\\working"', self.source)
        self.assertIn("function Get-StreamWorkingCount", self.source)
        gate = self.source.index('"evidence_backfill_concurrent_with_stream_upload"')
        launch = self.source.index(
            "if (-not $fullProjectDone -and -not $fullProjectReady -and (Start-EvidenceBackfillIfNeeded))"
        )
        self.assertLess(gate, launch)
        self.assertIn("$streamPendingCount -gt 0 -or $streamWorkingCount -gt 0", self.source)
        self.assertIn("$proof.current_upload_queue_source_ids", self.source)
        self.assertNotIn('"evidence_backfill_deferred_for_stream_upload"', self.source)
        concurrent_block = self.source[gate:launch]
        self.assertNotIn("exit 0", concurrent_block)

    def test_bound_full_project_handoff_precedes_mutable_evidence_rebuild(self):
        ready = self.source.index(
            "$fullProjectReady = if ($fullProjectDone) { $false } else { Full-Project-ContinuationReady }"
        )
        backfill = self.source.index(
            "if (-not $fullProjectDone -and -not $fullProjectReady -and (Start-EvidenceBackfillIfNeeded))"
        )
        self.assertLess(ready, backfill)

    def test_zero_candidate_completion_finalizes_without_questionable_rerun(self):
        self.assertIn("$script:CurrentYearEvidenceComplete = $true", self.source)
        self.assertIn("current_year_finalization_waiting_for_stream_upload", self.source)
        self.assertIn('$watcherArgs += "-FinalizeCurrentYearOnly"', self.source)
        watcher = (
            Path(__file__).resolve().parent
            / "auto_rerun_questionable_after_recursive.ps1"
        ).read_text(encoding="utf-8-sig")
        self.assertIn("[switch]$FinalizeCurrentYearOnly", watcher)
        self.assertIn("skipped_terminal_evidence_complete", watcher)

    def test_deleted_staging_path_cannot_select_resume_mode(self):
        gate = self.source.index('$runnerMode = "--execute"')
        launch = self.source.index('Start-Hidden -File $python -ProcessArgs $runnerArgs', gate)
        block = self.source[gate:launch]
        self.assertIn(
            "Test-Path -LiteralPath $liveImageDir -PathType Container",
            block,
        )
        self.assertIn('$runnerMode = "--resume-existing-then-continue"', block)

    def test_runtime_edits_reload_only_at_a_fully_idle_boundary(self):
        self.assertIn("Get-LatestBackendRuntimeWrite", self.source)
        self.assertIn("reload_backend_at_safe_idle.ps1", self.source)
        self.assertIn('$watcher.Count -eq 0', self.source)
        self.assertIn('$staged.Count -eq 0', self.source)
        self.assertIn('$recursive.Count -eq 0', self.source)
        self.assertIn('"safe_idle_reload_started"', self.source)
        running_noop = self.source.index('if ($status -and [bool]$status.is_running)')
        reload_start = self.source.index('"safe_idle_reload_started"')
        self.assertLess(running_noop, reload_start)
        self.assertNotIn("Stop-Process", self.source)

    def test_idle_incomplete_checkpoint_is_resumed_by_safe_reload_helper(self):
        reload_start = self.source.index('$reloadArgs = @(')
        reload_launch = self.source.index(
            'Start-Hidden -File "powershell.exe" -ProcessArgs $reloadArgs',
            reload_start,
        )
        reload_block = self.source[reload_start:reload_launch]
        self.assertIn('$status.stats.processed -lt [int]$status.stats.total', reload_block)
        self.assertIn('$reloadArgs += "-AllowIncompleteStoppedBatch"', reload_block)
        self.assertIn('$watcher.Count -eq 0', self.source[:reload_start])
        self.assertIn('$staged.Count -eq 0', self.source[:reload_start])
        self.assertIn('$recursive.Count -eq 0', self.source[:reload_start])
        self.assertIn('incomplete_stopped_batch=$incompleteStoppedBatch', self.source)

    def test_known_pre_inference_metadata_false_fuse_recovers_once(self):
        self.assertIn("Try-AutoRecoverKnownReviewMetadataFuse", self.source)
        self.assertIn("recover_review_metadata_false_fuse.py", self.source)
        self.assertIn("known_metadata_fuse_auto_recovered", self.source)
        self.assertIn("known_metadata_fuse_recovery_refused", self.source)
        self.assertIn("known_metadata_fuse_recovery_failed", self.source)
        self.assertIn("--fuse-file $RuntimeHealthFusePath --apply", self.source)
        self.assertIn('"skills\\runtime_health_gate.py"', self.source)
        recovery = self.source.index("Try-AutoRecoverKnownReviewMetadataFuse")
        fail_closed = self.source.index('Alert "runtime_health_fuse_active"', recovery)
        self.assertLess(recovery, fail_closed)

    def test_first_pass_photo_local_fuse_is_dry_run_then_apply_before_fail_closed(self):
        function_start = self.source.index("function Try-AutoRecoverFirstPassPhotoLocalFuse")
        function_end = self.source.index("function Get-CsvRowCount", function_start)
        recovery = self.source[function_start:function_end]
        fuse_flow_start = self.source.index("if (Test-Path -LiteralPath $RuntimeHealthFusePath)")
        fuse_flow_end = self.source.index("if (Test-Path -LiteralPath $BenchmarkLockPath)", fuse_flow_start)
        fuse_flow = self.source[fuse_flow_start:fuse_flow_end]

        self.assertIn("recover_first_pass_photo_local_fuse.py", self.source)
        self.assertIn("$dryOutput = @(& $python $recoverFirstPassPhotoLocalScript", recovery)
        self.assertIn("$applyOutput = @(& $python $recoverFirstPassPhotoLocalScript", recovery)
        dry_start = recovery.index("$dryOutput =")
        apply_start = recovery.index("$applyOutput =")
        self.assertLess(dry_start, apply_start)
        self.assertNotIn("--apply", recovery[dry_start:apply_start])
        self.assertIn("--apply", recovery[apply_start:])
        self.assertIn('"first_pass_photo_local_fuse_recovery_refused"', recovery)
        self.assertIn('"first_pass_photo_local_fuse_recovery_failed"', recovery)
        self.assertIn("Try-AutoRecoverFirstPassPhotoLocalFuse", fuse_flow)
        self.assertIn('Alert "runtime_health_fuse_active"', fuse_flow)
        self.assertLess(
            fuse_flow.index("Try-AutoRecoverFirstPassPhotoLocalFuse"),
            fuse_flow.index('Alert "runtime_health_fuse_active"'),
        )
        self.assertIn("if ($fuseRecovered)", fuse_flow)
        self.assertIn("exit 9", fuse_flow)

    def test_current_year_and_upload_gates(self):
        self.assertIn('"-CurrentYearOnly"', self.source)
        self.assertIn("drive_upload_ready_pending.csv", self.source)
        self.assertIn("rclone_drive_upload.py", self.source)
        self.assertNotIn("--no-resume", self.source)

    def test_uploader_requires_fresh_content_bound_gate_proof(self):
        uploader = self.source[self.source.index('$pending = Join-Path $OutputDir "_drive_upload\\drive_upload_ready_pending.csv"'):]
        self.assertLess(uploader.index("Test-UploadGateProof"), uploader.index("Start-Hidden -File $python -ProcessArgs @($bulkUploaderScript"))
        for token in (
            "upload_gate_proof.json",
            "UploadGateProofMaxAgeMinutes",
            "current_year_risk_audit_fresh",
            "current_year_upload_gate_open",
            "current_audit_input_sha256",
            "pending_sha256",
            "next_batch_sha256",
            "manifest_summary_sha256",
            "audit_summary_sha256",
            '$_.' + 'status -ne "ready"',
            "uploader_gate_closed",
        ):
            self.assertIn(token, self.source)

    def test_uploader_is_deferred_when_pipeline_transition_or_watcher_is_active(self):
        self.assertIn("$pipelineTransitionStarted = $true", self.source)
        self.assertIn('$pipelineTransitionStarted -or $watcher.Count -gt 0', self.source)
        self.assertIn('"uploader_deferred_pipeline_transition"', self.source)

    def test_full_project_transition_waits_for_fresh_current_year_marker(self):
        self.assertIn("full_project_continuation_requested.json", self.source)
        self.assertIn("current_year_rerun_cycle_complete.json", self.source)
        self.assertIn("full_project_rerun_cycle_complete.json", self.source)
        self.assertIn("Full-Project-ContinuationReady", self.source)
        self.assertIn("historical_continuation_gate.py", self.source)
        self.assertIn("--migrate-existing-request", self.source)
        self.assertIn("--write-receipt", self.source)
        self.assertIn("historical_continuation_gate_blocked", self.source)
        # The Python gate is the single transition authority.  Keeping the
        # marker/proof checks there prevents the supervisor and gate from
        # drifting into contradictory definitions of readiness.
        self.assertIn('marker.get("completed_at")', self.historical_gate_source)
        self.assertIn('request.get("requested_at")', self.historical_gate_source)
        self.assertIn('proof.get("pending_count", -1)', self.historical_gate_source)
        self.assertIn('"manifest_summary_sha256"', self.historical_gate_source)
        self.assertIn('"backfill_run_id"', self.historical_gate_source)

    def test_full_project_starts_recursive_before_all_year_watcher(self):
        recursive = self.source.index("$recursiveScript,")
        watcher = self.source.index('"-SkipCurrentYearPhases"')
        self.assertLess(recursive, watcher)
        self.assertNotIn('"--ignore-current-year-review-gate"', self.source)

    def test_authorized_historical_runner_is_healthy_not_ambiguous(self):
        active = self.source.index('$recursive.Count -eq 1 -and $staged.Count -eq 0')
        ambiguous = self.source.index('"staged_or_recursive_state_ambiguous"')
        self.assertLess(active, ambiguous)
        self.assertIn('"--historical-continuation-receipt"', self.source[active:ambiguous])
        self.assertIn('"historical_pipeline_active"', self.source[active:ambiguous])
        self.assertIn('Write-PipelineStatus -Active $true -Phase "historical_continuation"', self.source[active:ambiguous])
        self.assertIn('dashboard_kept_online=$true', self.source[active:ambiguous])
        self.assertIn('"--historical-continuation-receipt"', self.source)
        self.assertIn('"-SkipRecursiveResume"', self.source)
        self.assertIn('"full_project_pipeline_started"', self.source)

    def test_running_historical_backend_attaches_missing_recursive_coordinator(self):
        running = self.source.index("if ($status -and [bool]$status.is_running)")
        noop = self.source.index('"healthy_noop"', running)
        block = self.source[running:noop]
        self.assertIn("$runningHistoricalFolder", block)
        self.assertIn("$recursive.Count -eq 0", block)
        self.assertNotIn("$watcher.Count -eq 0", block)
        self.assertIn("$historicalContinuationReceipt", block)
        self.assertIn("$recursiveScript,", block)
        self.assertIn('"historical_coordinator_attached_to_running_backend"', block)
        self.assertIn("dashboard_kept_online=$true", block)

    def test_pipeline_heartbeat_is_atomic_and_alerts_clear_visible_activity(self):
        self.assertIn('dashboard\\dist\\pipeline-status.json', self.source)
        self.assertIn('schema = "samsung-ocr-pipeline-status/v1"', self.source)
        self.assertIn('Write-JsonAtomic -Path $pipelineStatusPath', self.source)
        self.assertIn('Write-PipelineStatus -Active $false -Phase "blocked"', self.source)

    def test_full_project_folder_timeout_allows_accuracy_first_multiday_runs(self):
        self.assertIn('"--timeout-minutes","10080"', self.source)
        self.assertNotIn('"--timeout-minutes","360"', self.source)

    def test_full_project_marker_is_bound_to_current_inventory_and_zero_errors(self):
        self.assertIn("function Test-FullProjectCompletionMarker", self.source)
        self.assertIn("folder_discovery_sha256", self.source)
        self.assertIn("folder_summary_sha256", self.source)
        self.assertIn("source_inventory_csv_sha256", self.source)
        self.assertIn("source_inventory_summary_sha256", self.source)
        self.assertIn('$_.' + 'status -notin @("copied", "skipped_existing")', self.source)
        self.assertIn("$fullProjectDone = Test-FullProjectCompletionMarker", self.source)


if __name__ == "__main__":
    unittest.main()

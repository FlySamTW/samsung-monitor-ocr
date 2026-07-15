from pathlib import Path
import unittest


class ContinuitySupervisorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (Path(__file__).resolve().parent / "ocr_continuity_supervisor.ps1").read_text(encoding="utf-8")

    def test_lock_and_healthy_noop(self):
        self.assertIn("New-Item -ItemType File -Path $lockPath", self.source)
        self.assertIn('$BenchmarkLockPath = Join-Path $audit "model_benchmark.lock"', self.source)
        self.assertIn('planned_backend_upgrade_interlock', self.source)
        self.assertIn('"healthy_noop"', self.source)
        self.assertIn("finally", self.source)

    def test_dead_planned_upgrade_owner_resumes_backfill_fail_closed(self):
        self.assertIn("planned_backend_upgrade_recovery_active", self.source)
        self.assertIn("planned_backend_upgrade_recovery_contract_failed", self.source)
        self.assertIn("planned_backend_upgrade_recovery_started", self.source)
        self.assertIn("planned_backend_upgrade_recovery_completed", self.source)
        self.assertIn("$backfillStarted = Start-EvidenceBackfillIfNeeded", self.source)
        self.assertIn("Get-Process -Id ([int]$planned.pid) -ErrorAction SilentlyContinue", self.source)
        self.assertIn("evidence backfill zero-candidate proof is incomplete", self.source)
        recovery = self.source.index("$backfillStarted = Start-EvidenceBackfillIfNeeded")
        release = self.source.index("Remove-Item -LiteralPath $BenchmarkLockPath -Force", recovery)
        self.assertGreater(release, recovery)

    def test_exact_repo_owned_processes_and_fail_closed_hung(self):
        self.assertIn("[regex]::Escape($RepoRoot)", self.source)
        self.assertIn('"backend_process_exists_but_api_unhealthy"', self.source)
        self.assertIn('"staged_or_recursive_state_ambiguous"', self.source)
        self.assertNotIn("Stop-Process", self.source)

    def test_recovery_order_and_safe_model_gate(self):
        self.assertLess(self.source.index("lm_server_recovery_attempt"), self.source.index("backend_started"))
        self.assertIn('"different_model_already_loaded"', self.source)
        self.assertIn('"--context-length",$ContextLength', self.source)
        self.assertIn('"qwen/qwen3-vl-8b"', self.source)

    def test_current_year_and_upload_gates(self):
        self.assertIn('"-CurrentYearOnly"', self.source)
        self.assertIn("drive_upload_ready_pending.csv", self.source)
        self.assertIn("rclone_drive_upload.py", self.source)
        self.assertNotIn("--no-resume", self.source)

    def test_uploader_requires_fresh_content_bound_gate_proof(self):
        uploader = self.source[self.source.index('$pending = Join-Path $OutputDir "_drive_upload\\drive_upload_ready_pending.csv"'):]
        self.assertLess(uploader.index("Test-UploadGateProof"), uploader.index('Start-Hidden $python @("tools\\rclone_drive_upload.py"'))
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
        self.assertIn("currentYear.completed_at", self.source)
        self.assertIn("request.requested_at", self.source)
        self.assertIn("Test-UploadGateProof", self.source)
        self.assertIn("[int]$gate.pending_count -ne 0", self.source)
        self.assertIn("manifest_summary_sha256", self.source)
        self.assertIn("backfill_run_id", self.source)

    def test_full_project_starts_recursive_before_all_year_watcher(self):
        recursive = self.source.index('"tools\\recursive_ocr_flat_export.py"')
        watcher = self.source.index('"-SkipCurrentYearPhases"')
        self.assertLess(recursive, watcher)
        self.assertIn('"--ignore-current-year-review-gate"', self.source)
        self.assertIn('"-SkipRecursiveResume"', self.source)
        self.assertIn('"full_project_pipeline_started"', self.source)

    def test_full_project_folder_timeout_allows_accuracy_first_multiday_runs(self):
        self.assertIn('"--timeout-minutes","10080"', self.source)
        self.assertNotIn('"--timeout-minutes","360"', self.source)

    def test_full_project_marker_is_bound_to_current_inventory_and_zero_errors(self):
        self.assertIn("function Test-FullProjectCompletionMarker", self.source)
        self.assertIn("folder_discovery_sha256", self.source)
        self.assertIn("folder_summary_sha256", self.source)
        self.assertIn('$_.' + 'status -in @("error", "blocked")', self.source)
        self.assertIn("$fullProjectDone = Test-FullProjectCompletionMarker", self.source)


if __name__ == "__main__":
    unittest.main()

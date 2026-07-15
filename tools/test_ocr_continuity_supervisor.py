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

    def test_full_project_transition_waits_for_fresh_current_year_marker(self):
        self.assertIn("full_project_continuation_requested.json", self.source)
        self.assertIn("current_year_rerun_cycle_complete.json", self.source)
        self.assertIn("full_project_rerun_cycle_complete.json", self.source)
        self.assertIn("Full-Project-ContinuationReady", self.source)
        self.assertIn("currentYear.completed_at", self.source)
        self.assertIn("request.requested_at", self.source)

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


if __name__ == "__main__":
    unittest.main()

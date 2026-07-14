"""Static continuity contracts for the independent handoff script."""
from pathlib import Path
import unittest

SCRIPT = (Path(__file__).parent / "auto_rerun_questionable_after_recursive.ps1").read_text(encoding="utf-8")

class AutoRerunContinuityTests(unittest.TestCase):
    def test_current_year_phases_precede_older_years(self):
        phases = ["current_year_first_pass", "current_year_immediate_pass_2", "current_year_immediate_pass_3", "current_year_distant_followme_review", "all-year questionable rerun starts"]
        positions = [SCRIPT.index(item) for item in phases]
        self.assertEqual(positions, sorted(positions))

    def test_owned_process_guard_and_single_worker_contract(self):
        self.assertIn("Get-OwnedMatchingProcess", SCRIPT)
        self.assertIn('Stop-ExtraOwnedProcesses "rclone_drive_upload.py|rclone.exe" "uploader"', SCRIPT)
        self.assertIn('Stop-ExtraOwnedProcesses "recursive_ocr_flat_export.py" "runner"', SCRIPT)
        self.assertNotIn('Stop-Process -Name', SCRIPT)

    def test_no_history_clear_or_restart_resume_flags(self):
        self.assertNotIn("--restart", SCRIPT)
        self.assertNotIn("Remove-Item -Recurse", SCRIPT)
        self.assertIn("Start-Recursive-IfNeeded", SCRIPT)

    def test_v1944_single_pass_is_not_explicitly_requeued(self):
        self.assertNotIn("v19.44", SCRIPT)
        self.assertNotIn("reprocess_last_n", SCRIPT)

    def test_lock_is_rechecked_before_main_loop_decisions(self):
        self.assertIn('Wait-ForBenchmarkLock "main loop"', SCRIPT)

    def test_recovery_can_yield_next_boundary_to_planned_upgrade(self):
        self.assertIn("SkipRecursiveResume", SCRIPT)
        self.assertIn("planned backend upgrade/backfill owns the next boundary", SCRIPT)

    def test_full_project_mode_skips_current_year_and_writes_completion_marker(self):
        self.assertIn("SkipCurrentYearPhases", SCRIPT)
        self.assertIn("$CurrentYearFirst -and -not $SkipCurrentYearPhases", SCRIPT)
        self.assertIn("full_project_rerun_cycle_complete.json", SCRIPT)
        self.assertIn("all_year_questionable_review = $true", SCRIPT)

    def test_fresh_manifest_precedes_fail_closed_drive_ledger_rebuild(self):
        self.assertIn("build_drive_correction_reconciliation.py", SCRIPT)
        main_tail = SCRIPT.rsplit("Refresh-UploadAndReviewSplit", 1)[1]
        self.assertLess(main_tail.index("Rebuild-DriveCorrectionLedgerIfSafe"), main_tail.index("Start-Uploader-IfNeeded"))

if __name__ == "__main__":
    unittest.main()

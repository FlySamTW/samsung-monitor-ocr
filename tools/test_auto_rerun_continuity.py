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
        self.assertIn("Assert-FullProjectRecursiveComplete", SCRIPT)
        self.assertIn("folder_discovery_sha256", SCRIPT)
        self.assertIn("folder_summary_sha256", SCRIPT)
        self.assertIn("source_inventory_csv_sha256", SCRIPT)
        self.assertIn("source_inventory_summary_sha256", SCRIPT)
        self.assertIn("folder_count_contract_failed", SCRIPT)
        self.assertIn("error_count = $recursiveProof.error_count", SCRIPT)

    def test_fresh_manifest_precedes_fail_closed_drive_ledger_rebuild(self):
        self.assertIn("build_drive_correction_reconciliation.py", SCRIPT)
        self.assertIn("reconcile_drive_corrections.py", SCRIPT)
        main_tail = SCRIPT.rsplit("Refresh-UploadAndReviewSplit", 1)[1]
        self.assertLess(main_tail.index("Rebuild-DriveCorrectionLedgerIfSafe"), main_tail.index("Start-Uploader-IfNeeded"))

    def test_current_year_marker_requires_verified_new_and_disposed_old_drive_objects(self):
        self.assertIn("function Complete-DriveCorrectionReconciliation", SCRIPT)
        self.assertIn("--execute --phase discover-old", SCRIPT)
        self.assertIn("--execute --phase upload-new", SCRIPT)
        self.assertIn("--execute --phase trash-old", SCRIPT)
        self.assertIn('@("old_trashed_verified","unchanged_remote_verified")', SCRIPT)
        uploader = SCRIPT.rindex("Start-Uploader-IfNeeded -WaitForCompletion")
        reconciliation = SCRIPT.index("Complete-DriveCorrectionReconciliation", uploader)
        marker = SCRIPT.index(
            r'$markerPath = Join-Path $OutputDir "_ocr_audit\current_year_rerun_cycle_complete.json"',
            reconciliation,
        )
        self.assertLess(uploader, reconciliation)
        self.assertLess(reconciliation, marker)

    def test_manifest_review_split_and_exact_gate_proof_are_mandatory(self):
        self.assertIn('throw "upload manifest refresh failed; completion and upload remain blocked"', SCRIPT)
        self.assertIn('throw "review split failed; completion and upload remain blocked"', SCRIPT)
        self.assertIn("build_upload_gate_proof.py", SCRIPT)
        self.assertIn("Start-Uploader-IfNeeded -WaitForCompletion", SCRIPT)
        proof = SCRIPT.index("Update-UploadGateProof -Required")
        uploader = SCRIPT.rindex("Start-Uploader-IfNeeded -WaitForCompletion")
        marker = SCRIPT.index(r'$markerPath = Join-Path $OutputDir "_ocr_audit\current_year_rerun_cycle_complete.json"', uploader)
        self.assertLess(proof, uploader)
        self.assertLess(uploader, marker)
        self.assertIn("pending_count = [int]$gate.pending_count", SCRIPT)

    def test_historical_upload_requires_inventory_bound_authorization(self):
        self.assertIn("samsung-ocr-historical-upload-authorization/v1", SCRIPT)
        self.assertIn("Write-HistoricalUploadAuthorization", SCRIPT)
        self.assertIn("current_year_marker_sha256", SCRIPT)
        self.assertIn("folder_discovery_sha256", SCRIPT)
        self.assertIn("folder_summary_sha256", SCRIPT)
        authorization = SCRIPT.rindex("Write-HistoricalUploadAuthorization")
        uploader = SCRIPT.rindex("Start-Uploader-IfNeeded -WaitForCompletion")
        self.assertLess(authorization, uploader)

    def test_intermediate_review_phases_cannot_start_uploader(self):
        start = SCRIPT.index("function Start-Uploader-IfNeeded")
        end = SCRIPT.index("function Start-Recursive-IfNeeded", start)
        body = SCRIPT[start:end]
        self.assertIn('if (-not $WaitForCompletion)', body)
        self.assertIn('uploader deferred until all configured review phases finish', body)

    def test_accuracy_first_staged_and_recursive_runs_have_multiday_timeout(self):
        self.assertGreaterEqual(SCRIPT.count('"--timeout-minutes", "10080"'), 2)
        self.assertNotIn('"--timeout-minutes", "360"', SCRIPT)

if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import unittest


class OcrUploadWatchdogGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (Path(__file__).resolve().parent / "ocr_upload_watchdog.ps1").read_text(encoding="utf-8")

    def test_upload_authorities_are_refreshed_in_fail_closed_order(self):
        refresh = self.source[
            self.source.index("function Update-UploadGateProof"):
            self.source.index("New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LockPath)")
        ]
        self.assertLess(refresh.index("Run-DistantFollowMeAudit"), refresh.index("Test-CurrentAuditProof"))
        self.assertLess(refresh.index("Test-CurrentAuditProof"), refresh.index("prepare_drive_upload_manifest.py"))
        self.assertLess(refresh.index("prepare_drive_upload_manifest.py"), refresh.index("drive_upload_summary.json"))
        self.assertLess(refresh.index("drive_upload_summary.json"), refresh.index("Write-UploadGateProof"))
        self.assertIn("Remove-Item -LiteralPath $GateProofPath", refresh)
        final_interlock = refresh.rindex("Test-Path -LiteralPath $BenchmarkLockPath")
        self.assertLess(final_interlock, refresh.index("Write-UploadGateProof"))
        self.assertIn("trustedBatchFields", refresh)
        self.assertIn("pending ledger has an empty next batch", refresh)

    def test_manifest_gate_and_content_hashes_precede_uploader_start(self):
        uploader = self.source[
            self.source.index("function Start-UploaderIfNeeded"):
            self.source.index("function Log-Progress")
        ]
        self.assertLess(uploader.index("Test-UploadGateProof"), uploader.index("Start-Process"))
        for token in (
            "current_year_risk_audit_fresh",
            "current_year_upload_gate_open",
            "current_audit_input_sha256",
            "pending_sha256",
            "next_batch_sha256",
            "manifest_summary_sha256",
            "audit_summary_sha256",
            '$_.' + 'status -ne "ready"',
            "reasons",
            "UploadGateProofMaxAgeMinutes",
            "candidate_summary_json",
        ):
            self.assertIn(token, self.source)

    def test_main_order_never_runs_audit_after_uploader(self):
        main = self.source[self.source.rindex("try {"):]
        self.assertLess(main.index("Update-UploadGateProof"), main.index("Start-UploaderIfNeeded"))
        self.assertNotIn("Run-DistantFollowMeAudit", main)

    def test_all_spawned_children_are_hidden(self):
        self.assertIn("-WindowStyle Hidden", self.source)
        self.assertNotIn("-WindowStyle Normal", self.source)

    def test_model_benchmark_interlock_blocks_every_watchdog_action(self):
        self.assertIn('$BenchmarkLockPath = Join-Path $OutputDir "_ocr_audit\\model_benchmark.lock"', self.source)
        main = self.source[self.source.index("New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LockPath)"):]
        self.assertLess(main.index("Test-Path -LiteralPath $BenchmarkLockPath"), main.index("try {"))
        self.assertIn('Remove-Item -LiteralPath $GateProofPath', main)
        self.assertIn('exit 8', main)


if __name__ == "__main__":
    unittest.main()

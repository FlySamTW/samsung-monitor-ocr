from pathlib import Path
import unittest


class PlannedLiveRevisionUpgradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (
            Path(__file__).resolve().parent / "planned_live_revision_upgrade.ps1"
        ).read_text(encoding="utf-8")

    def test_stops_mutators_before_requesting_photo_boundary_stop(self):
        stop_daemon = self.source.index('Stop-Owned "ocr_continuity_daemon')
        stop_runner = self.source.index('Stop-Owned "recursive_ocr_flat_export')
        api_stop = self.source.index('"$BackendUrl/api/stop"')
        self.assertLess(stop_daemon, api_stop)
        self.assertLess(stop_runner, api_stop)
        self.assertIn("photo-boundary stop timed out", self.source)

    def test_preserves_and_verifies_exact_checkpoint(self):
        self.assertIn('$checkpoint = [System.IO.Path]::GetFullPath', self.source)
        self.assertIn("checkpoint changed while stopping", self.source)
        self.assertIn("resumed checkpoint differs from saved checkpoint", self.source)
        self.assertIn("-AllowIncompleteStoppedBatch", self.source)

    def test_restarts_hidden_services_without_browser(self):
        self.assertIn("-WindowStyle Hidden", self.source)
        self.assertIn("stream_drive_upload.py", self.source)
        self.assertIn("recursive_ocr_flat_export.py", self.source)
        self.assertIn("auto_rerun_questionable_after_recursive.ps1", self.source)
        self.assertIn("ocr_continuity_daemon.ps1", self.source)
        self.assertNotIn("start http", self.source.lower())

    def test_revision_and_live_resume_are_required(self):
        self.assertIn("EVIDENCE_GUARD_REVISION", self.source)
        self.assertIn("new revision is not active", self.source)
        self.assertIn("OCR did not resume", self.source)
        self.assertIn('"live_revision_upgrade_verified"', self.source)

    def test_revision_bound_continuation_receipt_is_refreshed_only_at_idle_boundary(self):
        self.assertIn("--validate-receipt", self.source)
        self.assertIn("--migrate-existing-request", self.source)
        self.assertIn("--write-receipt", self.source)
        idle = self.source.index("checkpoint changed while stopping")
        migrate = self.source.index("--migrate-existing-request")
        reload_backend = self.source.index("& powershell.exe", migrate)
        self.assertLess(idle, migrate)
        self.assertLess(migrate, reload_backend)


if __name__ == "__main__":
    unittest.main()

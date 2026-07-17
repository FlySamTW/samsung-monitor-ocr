"""Static contract for the formal OCR continuity endpoint.

The continuity launch chain must never silently fall back to the obsolete
port 5000: it would lose the live 5002 dashboard and may create a second
backend.  These source-level checks deliberately run without contacting OCR.
"""

from pathlib import Path
import unittest


TOOLS = Path(__file__).resolve().parent
FORMAL = "http://127.0.0.1:5002"


class ContinuityPortContractTests(unittest.TestCase):
    def read(self, name: str) -> str:
        return (TOOLS / name).read_text(encoding="utf-8")

    def test_supervisor_and_daemon_default_to_formal_endpoint(self):
        supervisor = self.read("ocr_continuity_supervisor.ps1")
        daemon = self.read("ocr_continuity_daemon.ps1")
        self.assertIn(f'[string]$BackendUrl = "{FORMAL}"', supervisor)
        self.assertIn(f'[string]$BackendUrl = "{FORMAL}"', daemon)
        self.assertIn("'-BackendUrl',$BackendUrl", daemon)

    def test_persistent_launchers_preserve_backend_url_and_stay_hidden(self):
        installer = self.read("install_ocr_continuity_daemon.ps1")
        vbs = self.read("ocr_continuity_ensure_hidden.vbs")
        watchdog = self.read("install_watchdog_task.ps1")
        self.assertIn('-BackendUrl "{4}"', installer)
        self.assertIn("'-BackendUrl',$BackendUrl", installer)
        self.assertIn('Arguments.Count <> 4', vbs)
        self.assertIn('" -BackendUrl " & Quote(backendUrl)', vbs)
        self.assertIn('"-BackendUrl", (\'"{0}"\' -f $BackendUrl)', watchdog)
        self.assertIn('"-WindowStyle Hidden " + $taskArgs', watchdog)

    def test_historical_gate_requires_formal_endpoint(self):
        gate = self.read("historical_continuation_gate.py")
        self.assertIn(f'CANONICAL_BACKEND_URL = "{FORMAL}"', gate)
        self.assertNotIn('CANONICAL_BACKEND_URL = "http://127.0.0.1:5000"', gate)

    def test_active_continuation_defaults_do_not_fall_back_to_5000(self):
        active = (
            "auto_rerun_questionable_after_recursive.ps1",
            "continue_after_missing_rerun.ps1",
            "protect_staged_conflict_handoff.ps1",
            "recursive_ocr_flat_export.py",
            "rerun_questionable_records.py",
            "model_benchmark_sidecar.py",
            "ocr_progress_threshold_notify.py",
            "safe_backend_boundary_upgrade.ps1",
            "ocr_upload_watchdog.ps1",
            "rclone_drive_upload.py",
            "windows_user_launcher.ps1",
        )
        for name in active:
            with self.subTest(name=name):
                text = self.read(name)
                self.assertIn("5002", text)
                self.assertNotIn("http://127.0.0.1:5000", text)

    def test_windows_launcher_passes_the_formal_port_to_backend_and_recursive_runner(self):
        launcher = self.read("windows_user_launcher.ps1")
        self.assertIn(f'[string]$BackendUrl = "{FORMAL}"', launcher)
        self.assertIn('"--port", "$BackendPort"', launcher)
        self.assertIn('"--backend-url" $BackendBase', launcher)
        self.assertIn('Start-Process "$BackendBase/"', launcher)


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import unittest

SCRIPT = (Path(__file__).parent / "windows_user_launcher.ps1").read_text(encoding="utf-8")
BACKEND = (Path(__file__).parents[1] / "samsung_ocr_batch_processor.py").read_text(encoding="utf-8")

class WindowsLauncherWindowStyleTests(unittest.TestCase):
    def test_backend_is_hidden_and_browser_is_explicit_opt_in(self):
        backend_lines = [line for line in SCRIPT.splitlines() if "Start-Process -FilePath $python" in line]
        self.assertTrue(backend_lines)
        self.assertTrue(all("-WindowStyle Hidden" in line for line in backend_lines))
        self.assertNotIn("-WindowStyle Minimized", SCRIPT)
        self.assertNotIn("-WindowStyle Normal", SCRIPT)
        self.assertEqual(SCRIPT.count('Start-Process "http://127.0.0.1:5000/"'), 1)
        self.assertIn('if ((Get-Setting "SAMSUNG_OCR_OPEN_BROWSER" "0") -eq "1")', SCRIPT)
        self.assertGreaterEqual(SCRIPT.count("Open-DashboardIfRequested"), 3)

    def test_recursive_uses_same_hidden_backend_launcher(self):
        self.assertIn('"recursive" {', SCRIPT)
        self.assertIn("Start-Backend", SCRIPT)
        self.assertIn('"tools\\recursive_ocr_flat_export.py"', SCRIPT)

    def test_dashboard_is_rebuilt_when_source_is_newer_than_dist(self):
        self.assertIn("$newestSource.LastWriteTimeUtc -gt (Get-Item -LiteralPath $distIndex).LastWriteTimeUtc", SCRIPT)
        self.assertIn('Dashboard build is present and current.', SCRIPT)
        self.assertIn('dashboard/dist is missing or stale', SCRIPT)
        self.assertLess(SCRIPT.index("Ensure-Dashboard"), SCRIPT.index("Start-Backend"))

    def test_backend_restart_is_headless_by_default(self):
        self.assertIn('os.environ.get("SAMSUNG_OCR_OPEN_BROWSER") == "1"', BACKEND)
        self.assertNotIn('os.environ.get("SAMSUNG_OCR_NO_BROWSER") != "1"', BACKEND)

    def test_lm_studio_cli_failure_has_native_api_fallback(self):
        self.assertIn("function Try-LoadModelApi", SCRIPT)
        self.assertIn('"/api/v1/models/load"', SCRIPT)
        self.assertIn("if ((Try-LoadModelApi $apiBase $model)", SCRIPT)

if __name__ == "__main__":
    unittest.main()

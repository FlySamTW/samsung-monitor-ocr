from pathlib import Path
import unittest

SCRIPT = (Path(__file__).parent / "windows_user_launcher.ps1").read_text(encoding="utf-8")

class WindowsLauncherWindowStyleTests(unittest.TestCase):
    def test_backend_is_hidden_and_browser_remains_visible(self):
        backend_lines = [line for line in SCRIPT.splitlines() if "Start-Process -FilePath $python" in line]
        self.assertTrue(backend_lines)
        self.assertTrue(all("-WindowStyle Hidden" in line for line in backend_lines))
        self.assertNotIn("-WindowStyle Minimized", SCRIPT)
        self.assertNotIn("-WindowStyle Normal", SCRIPT)
        self.assertIn('Start-Process "http://127.0.0.1:5000/"', SCRIPT)

    def test_recursive_uses_same_hidden_backend_launcher(self):
        self.assertIn('"recursive" {', SCRIPT)
        self.assertIn("Start-Backend", SCRIPT)
        self.assertIn('"tools\\recursive_ocr_flat_export.py"', SCRIPT)

    def test_dashboard_is_rebuilt_when_source_is_newer_than_dist(self):
        self.assertIn("$newestSource.LastWriteTimeUtc -gt (Get-Item -LiteralPath $distIndex).LastWriteTimeUtc", SCRIPT)
        self.assertIn('Dashboard build is present and current.', SCRIPT)
        self.assertIn('dashboard/dist is missing or stale', SCRIPT)
        self.assertLess(SCRIPT.index("Ensure-Dashboard"), SCRIPT.index("Start-Backend"))

if __name__ == "__main__":
    unittest.main()

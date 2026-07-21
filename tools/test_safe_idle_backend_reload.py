from pathlib import Path
import unittest


class SafeIdleBackendReloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (
            Path(__file__).resolve().parent / "reload_backend_at_safe_idle.ps1"
        ).read_text(encoding="utf-8")

    def test_requires_true_idle_and_no_owned_runner(self):
        self.assertIn("[bool]$status.is_running", self.source)
        self.assertIn(
            "[int]$status.stats.processed -ne [int]$status.stats.total",
            self.source,
        )
        self.assertIn("rerun_staged_candidates", self.source)
        self.assertIn("recursive_ocr_flat_export", self.source)
        self.assertIn("auto_rerun_questionable_after_recursive", self.source)
        self.assertIn("owned OCR runner still exists", self.source)

    def test_incomplete_recovery_is_explicit_and_keeps_other_interlocks(self):
        self.assertIn("[switch]$AllowIncompleteStoppedBatch", self.source)
        self.assertIn("-not $AllowIncompleteStoppedBatch", self.source)
        self.assertIn(
            "incomplete_stopped_batch_recovery=[bool]$AllowIncompleteStoppedBatch",
            self.source,
        )
        running_check = self.source.index("[bool]$status.is_running")
        incomplete_check = self.source.index("-not $AllowIncompleteStoppedBatch")
        runner_check = self.source.index("owned OCR runner still exists")
        self.assertLess(running_check, incomplete_check)
        self.assertLess(incomplete_check, runner_check)

    def test_runtime_health_trial_reload_requires_both_interlocks(self):
        self.assertIn("[switch]$RuntimeHealthTrialReload", self.source)
        self.assertIn(
            "runtime-health trial reload requires an active fuse", self.source
        )
        self.assertIn(
            "runtime-health trial reload requires the benchmark lock", self.source
        )
        self.assertIn(
            "$RuntimeHealthTrialReload -and -not $fresh.runtime_health_fuse",
            self.source,
        )

    def test_revision_probe_explicitly_imports_from_repo_root(self):
        self.assertIn("sys.path.insert(0, sys.argv[1])", self.source)
        self.assertIn('print(EVIDENCE_GUARD_REVISION)" $RepoRoot', self.source)

    def test_stops_only_repo_owned_backend_listener_tree(self):
        self.assertIn("Get-BackendProcessTree", self.source)
        self.assertIn("[regex]::Escape($RepoRoot)", self.source)
        self.assertIn("port is not owned by the OCR backend", self.source)
        self.assertIn("Stop-Process -Id $processId", self.source)
        self.assertNotIn("Stop-Process -Name", self.source)
        self.assertNotIn("Stop-Process -Id $processId -Force", self.source)

    def test_fresh_backend_is_hidden_and_never_opens_browser(self):
        self.assertIn('$env:SAMSUNG_OCR_NO_BROWSER = "1"', self.source)
        self.assertIn("-WindowStyle Hidden", self.source)
        self.assertIn('"--no_followme_auto_update"', self.source)
        self.assertNotIn("Start-Process \"$BackendUrl", self.source)
        self.assertNotIn("http://127.0.0.1:5002/\"", self.source)

    def test_verifies_contract_before_success(self):
        self.assertIn('"compact-v2"', self.source)
        self.assertIn('"v19.45*"', self.source)
        self.assertIn("EVIDENCE_GUARD_REVISION", self.source)
        self.assertIn("-ne $expectedRevision", self.source)
        self.assertIn('"fresh_backend_verified"', self.source)
        self.assertIn("runtime health fuse is active", self.source)
        self.assertIn("model benchmark/upgrade lock is active", self.source)


if __name__ == "__main__":
    unittest.main()

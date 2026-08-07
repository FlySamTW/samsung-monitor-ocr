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

    def test_incomplete_reload_resumes_original_approved_checkpoint_before_helper_exits(self):
        self.assertIn('$resumeIncompleteDir = ""', self.source)
        self.assertIn('Join-Path $OutputDir "_ocr_staging"', self.source)
        self.assertIn('$sourceRootFull = [System.IO.Path]::GetFullPath($SourceRoot)', self.source)
        self.assertIn('$insideStaging', self.source)
        self.assertIn('$insideSource', self.source)
        self.assertIn('outside the approved source/staging roots', self.source)
        self.assertIn('"$BackendUrl/api/start_batch"', self.source)
        self.assertIn('dir=$resumeIncompleteDir', self.source)
        self.assertIn('restart=$false', self.source)
        self.assertIn('confirmed=$true', self.source)
        self.assertIn('"incomplete_checkpoint_resumed"', self.source)
        verified = self.source.index('"fresh_backend_verified"')
        resume = self.source.index('"$BackendUrl/api/start_batch"')
        self.assertLess(verified, resume)

    def test_immediately_settled_capped_checkpoint_counts_as_a_successful_resume(self):
        self.assertIn("$resumedProcessed + $resumedCapped", self.source)
        self.assertIn("-eq $resumedTotal", self.source)
        self.assertIn("$resumedSettledAtBoundary", self.source)
        self.assertIn(
            "-not [bool]$resumed.is_running -and -not $resumedSettledAtBoundary",
            self.source,
        )
        self.assertIn("settled_at_boundary=$resumedSettledAtBoundary", self.source)

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

    def test_accepts_only_venv_bound_runtime_python_listener_child(self):
        self.assertIn("function Get-VenvRuntimePython", self.source)
        self.assertIn('Join-Path $RepoRoot ".venv\\pyvenv.cfg"', self.source)
        self.assertIn('"^\\s*executable\\s*=\\s*(.+?)\\s*$"', self.source)
        self.assertIn("$delegatedRuntimeListener", self.source)
        self.assertIn(
            "Test-SameExecutable ([string]$parent.ExecutablePath) $python",
            self.source,
        )
        self.assertIn(
            "Test-SameExecutable ([string]$listener.ExecutablePath) $runtimePython",
            self.source,
        )
        self.assertIn(
            '[string]$parent.CommandLine -match [regex]::Escape($RepoRoot)',
            self.source,
        )
        self.assertIn(
            "if (-not $directRepoListener -and -not $delegatedRuntimeListener)",
            self.source,
        )

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

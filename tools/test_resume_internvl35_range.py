from pathlib import Path
import unittest


class InternVLRangeResumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (Path(__file__).resolve().parent / "resume_internvl35_range.ps1").read_text(encoding="utf-8-sig")

    def test_proven_remote_contract_and_same_partial(self):
        self.assertIn("InternVL3_5-8B-Q4_K_M.gguf", self.source)
        self.assertIn("5027780512", self.source)
        self.assertIn("2809043479b8d3aab30378766c7a2a4bd93eedd97c86efc6d65d627fd680faba", self.source)
        self.assertIn('"--continue-at", "-"', self.source)
        self.assertIn("--fail", self.source)
        self.assertIn("--location", self.source)

    def test_lock_duplicate_and_no_shrink_guards(self):
        self.assertIn("New-Item -ItemType File -Path $LockPath", self.source)
        self.assertIn("another downloader already owns the partial", self.source)
        self.assertIn("partial shrank; refusing to continue", self.source)
        self.assertIn("Move-Item -LiteralPath $PartialPath", self.source)
        self.assertNotIn("Remove-Item -LiteralPath $PartialPath -Force -ErrorAction SilentlyContinue", self.source)

    def test_hash_gate_and_fail_closed_status(self):
        self.assertIn("Get-FileHash -LiteralPath $PartialPath -Algorithm SHA256", self.source)
        self.assertIn('Write-Status "failed_closed"', self.source)
        self.assertIn("finally", self.source)

    def test_native_stderr_is_buffered_and_exit_code_is_authoritative(self):
        self.assertIn('Start-Process -FilePath "curl.exe"', self.source)
        self.assertIn("-RedirectStandardOutput $curlStdout", self.source)
        self.assertIn("-RedirectStandardError $curlStderr", self.source)
        self.assertIn("-Wait -PassThru", self.source)
        self.assertIn("$curlProcess.ExitCode", self.source)
        self.assertNotIn("& curl.exe", self.source)

    def test_finalize_only_waits_for_lock_release_with_bounded_backoff(self):
        self.assertIn("[switch]$FinalizeOnly", self.source)
        self.assertIn("function Wait-ForExclusiveRead", self.source)
        self.assertIn("[System.IO.FileShare]::None", self.source)
        self.assertIn("$deadline = (Get-Date).AddSeconds($MaxWaitSeconds)", self.source)
        self.assertIn("Start-Sleep -Seconds $delay", self.source)
        self.assertIn("$delay = [Math]::Min($delay * 2, 30)", self.source)
        self.assertIn("if (-not $FinalizeOnly)", self.source)
        self.assertIn("Wait-ForExclusiveRead -Path $PartialPath", self.source)

    def test_finalize_only_keeps_hash_gate_and_atomic_rename(self):
        self.assertIn('Write-Status $state', self.source)
        self.assertIn('finalize_only=[bool]$FinalizeOnly', self.source)
        self.assertIn("Move-Item -LiteralPath $PartialPath -Destination $FinalPath", self.source)
        self.assertIn("SHA256 mismatch", self.source)

    def test_shared_read_finalize_is_read_only_and_rechecks_source(self):
        self.assertIn("[switch]$SharedReadFinalize", self.source)
        self.assertIn("FileShare]::ReadWrite", self.source)
        self.assertIn("Wait-ForStableSource", self.source)
        self.assertIn("$source.CopyTo($target)", self.source)
        self.assertIn("source changed after finalizing copy", self.source)
        self.assertIn("SHA256 mismatch in finalizing copy", self.source)
        self.assertNotIn("SetLength(0)", self.source)

    def test_shared_read_cleanup_is_conservative(self):
        self.assertIn('source_cleanup=$cleanup', self.source)
        self.assertIn('cleanup_pending', self.source)
        self.assertIn('Remove-Item -LiteralPath $PartialPath -Force -ErrorAction Stop', self.source)
        self.assertIn('final verified but source cleanup pending', self.source)
        self.assertIn('insufficient free space for safe finalizing copy', self.source)


if __name__ == "__main__":
    unittest.main()

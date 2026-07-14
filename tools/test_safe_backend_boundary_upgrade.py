import json
import re
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SafeBoundaryUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.script = (ROOT / "tools" / "safe_backend_boundary_upgrade.ps1").read_text(encoding="utf-8")
        self.supervisor = (ROOT / "tools" / "ocr_continuity_supervisor.ps1").read_text(encoding="utf-8")

    def test_active_work_waits_and_does_not_stop_backend(self):
        self.assertIn('if (-not $status) { return $false }', self.script)
        self.assertIn("expected folder complete snapshot", self.script)
        self.assertIn('Start-Sleep -Seconds ([math]::Max(5,$PollSeconds))', self.script)
        self.assertIn('purpose="backend_upgrade_v1945"', self.script)

    def test_boundary_proof_requires_idle_complete_and_no_workers(self):
        self.assertIn('if ([bool]$status.is_running) { return $false }', self.script)
        self.assertIn('[int]$stats.processed -ne [int]$stats.total', self.script)
        self.assertIn('rerun_staged_candidates\\.py|recursive_ocr_flat_export\\.py|rerun_questionable_records\\.py', self.script)
        self.assertIn('rclone_drive_upload\\.py|rclone\\.exe', self.script)
        self.assertIn('$quietCount -ge 2', self.script)

    def test_backend_stop_targets_verified_port_listener_and_process_tree(self):
        self.assertIn('Get-NetTCPConnection -State Listen -LocalPort 5000', self.script)
        self.assertIn('port 5000 is not owned by the Samsung OCR backend', self.script)
        self.assertIn('backend listener ancestry is not owned by repo', self.script)
        self.assertIn('listener_pid=$listenerId; process_ids=$orderedIds', self.script)

    def test_supervisor_interlock_is_fail_closed(self):
        self.assertIn('planned_backend_upgrade_interlock', self.supervisor)
        self.assertIn('if ($planned.purpose -eq "backend_upgrade_v1945")', self.supervisor)

    def test_failed_verification_retains_lock_and_success_removes_it(self):
        self.assertIn('upgrade_failed_lock_retained', self.script)
        self.assertIn('throw "new backend verification failed; lock retained"', self.script)
        self.assertIn('Remove-Item -LiteralPath $lockPath -Force', self.script)
        self.assertIn('status_contract_version -eq "compact-v2"', self.script)
        self.assertIn('$payloadBytes -lt 500000', self.script)
        self.assertIn('/api/presentation_history/', self.script)

    def test_lock_payload_is_atomic_and_recoverable(self):
        self.assertIn('New-Item -ItemType File -Path $lockPath', self.script)
        self.assertIn('pid=$PID', self.script)
        self.assertIn('started_at=', self.script)


if __name__ == "__main__":
    unittest.main()

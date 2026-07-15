from pathlib import Path
import unittest

class ContinuityDaemonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).resolve().parent
        cls.daemon=(root/'ocr_continuity_daemon.ps1').read_text(encoding='utf-8')
        cls.installer=(root/'install_ocr_continuity_daemon.ps1').read_text(encoding='utf-8')
        cls.hidden_launcher=(root/'ocr_continuity_ensure_hidden.vbs').read_text(encoding='utf-8')
    def test_single_instance_immediate_loop_timeout_shutdown(self):
        for text in (self.daemon,):
            self.assertIn('New-Item -ItemType File -Path $lock', text)
            self.assertIn('WaitForExit($ChildTimeoutSeconds*1000)', text)
            self.assertIn('Start-Sleep -Seconds', text)
            self.assertIn('ocr_continuity_daemon_shutdown.json', text)
            self.assertIn('WindowStyle Hidden', text)
    def test_user_registration_and_safe_uninstall(self):
        self.assertIn('HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run', self.installer)
        self.assertIn('SamsungOCRContinuityDaemon', self.installer)
        self.assertIn('Startup', self.installer)
        self.assertIn('uninstall', self.installer)
        self.assertNotIn('Register-ScheduledTask', self.installer)

    def test_ensure_is_exact_and_idempotent(self):
        self.assertIn('ValidateSet("install","uninstall","status","ensure")', self.installer)
        self.assertIn('if($Action -eq \'ensure\')', self.installer)
        self.assertIn('daemon already present; no-op', self.installer)
        self.assertIn('Start-Daemon', self.installer)
        self.assertIn('schtasks.exe /Create', self.installer)
        self.assertIn('/RL LIMITED', self.installer)
        self.assertIn('/SC MINUTE /MO 5', self.installer)
        self.assertIn('wscript.exe //B //Nologo', self.installer)
        self.assertIn('ocr_continuity_ensure_hidden.vbs', self.installer)
        self.assertIn('shell.Run(command, 0, True)', self.hidden_launcher)
        self.assertIn('-NonInteractive -WindowStyle Hidden', self.hidden_launcher)

    def test_stale_lock_requires_absent_owner_and_age(self):
        self.assertIn('$alive=Get-Process -Id $owner', self.installer)
        self.assertIn('$age -lt 30', self.installer)
        self.assertIn('fail closed', self.installer)
        self.assertIn('Remove-Item $lock -Force', self.installer)

if __name__ == '__main__': unittest.main()

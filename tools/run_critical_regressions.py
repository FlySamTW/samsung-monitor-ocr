"""Critical presentation regression entry point."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    commands = [
        [sys.executable, str(ROOT / "tools" / "test_presentation_soak.py")],
        [sys.executable, str(ROOT / "tools" / "test_presentation_history_api.py")],
        [sys.executable, str(ROOT / "tools" / "test_immediate_retry_queue.py")],
        [sys.executable, "-m", "unittest", "-v", "tools.test_v1945_evidence_contract"],
        [sys.executable, "-m", "unittest", "-v", "tools.test_review_pass_contract"],
        [sys.executable, str(ROOT / "tools" / "test_runtime_safety_guards.py")],
        [sys.executable, "-m", "unittest", "-v", "tools.test_runtime_health_gate"],
        [sys.executable, "-m", "unittest", "-v", "tools.test_recover_legacy_instruction_echo_fuse"],
        [sys.executable, "-m", "unittest", "-v", "tools.test_windows_user_launcher_window_style"],
        [sys.executable, "-m", "unittest", "-v", "tools.test_migrate_legacy_v1945_trace"],
        [sys.executable, "-m", "unittest", "-v", "tools.test_safe_backend_boundary_upgrade"],
        [sys.executable, "-m", "unittest", "-v", "tools.test_build_v1945_evidence_backfill"],
        [sys.executable, "-m", "unittest", "-v", "tools.test_current_year_upload_finalization"],
        [sys.executable, "-m", "unittest", "-v", "tools.test_questionable_upload_guards"],
        [sys.executable, str(ROOT / "test_rclone_upload_safety_unit.py")],
        [sys.executable, str(ROOT / "tools" / "test_ocr_upload_watchdog.py")],
        [sys.executable, str(ROOT / "tools" / "test_ocr_continuity_supervisor.py")],
        [sys.executable, "-m", "unittest", "-v", "tools.test_safe_idle_backend_reload"],
        [sys.executable, str(ROOT / "tools" / "test_auto_rerun_continuity.py")],
        [sys.executable, str(ROOT / "tools" / "test_recursive_completion_contract.py")],
        [sys.executable, "-m", "unittest", "-v", "tools.test_historical_continuation_gate"],
        [sys.executable, "-m", "unittest", "-v", "tools.test_source_inventory_snapshot"],
        [sys.executable, "-m", "unittest", "-v", "tools.test_build_drive_correction_reconciliation"],
        [sys.executable, "-m", "unittest", "-v", "tools.test_reconcile_drive_corrections"],
        [sys.executable, "-m", "unittest", "-v", "tools.test_stream_drive_upload"],
        [sys.executable, "-m", "unittest", "-v", "tools.test_record_period_priority_progress"],
        [sys.executable, "-m", "unittest", "-v", "tools.test_continue_after_period_priority"],
        [sys.executable, "-m", "unittest", "-v", "tools.test_recover_failed_fuse_uploads"],
        [sys.executable, "-m", "unittest", "-v", "tools.test_revalidate_frozen_guard_results"],
        [sys.executable, "-m", "unittest", "-v", "tools.test_revalidate_completed_source_results"],
        [sys.executable, "-m", "unittest", "-v", "tools.test_recover_consumed_cap_missing_result"],
        [sys.executable, "-m", "unittest", "-v", "tools.test_finalize_safe_consumed_cap_batch"],
        [sys.executable, "-m", "unittest", "-v", "tools.test_migrate_lifetime_cap_fuse_to_deferred"],
        [sys.executable, "-m", "unittest", "-v", "tools.test_build_visual_authority_manifest"],
    ]
    for command in commands:
        print("[critical]", " ".join(command), flush=True)
        result = subprocess.run(command, cwd=ROOT)
        if result.returncode:
            return result.returncode
    print("[critical] live 3-transition check is browser-gated; inspect logs/ui_sync_v1944_live.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

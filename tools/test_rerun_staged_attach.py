import tempfile
import unittest
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rerun_staged_candidates as mod
import rerun_questionable_records as questionable


class AttachExistingTests(unittest.TestCase):
    def test_wait_tolerates_transient_status_failures(self):
        done = {"is_running": False, "stats": {"processed": 1, "total": 1, "success": 1, "failed": 0}}
        with patch.object(questionable, "json_request", side_effect=[OSError("temporary"), done]) as request:
            result = questionable.wait_for_folder_done(
                "http://mock", Path("group"), 1, 0, max_consecutive_status_errors=2, retry_sleep_seconds=0
            )
        self.assertEqual(result, done)
        self.assertEqual(request.call_count, 2)

    def test_resume_selects_active_group_and_only_later_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging_root = root / "staging"; staging_root.mkdir()
            groups = {}
            for period in ("202605", "202604", "202603"):
                source = root / period; source.mkdir()
                digest = __import__("hashlib").sha1(str(source.resolve()).encode()).hexdigest()[:8]
                groups[(str(source), str(root / f"audit-{period}"), period)] = [{"period": period}]
                if period == "202604":
                    current = staging_root / f"{period}_demo_{digest}"; current.mkdir()
            active, remaining = mod.split_groups_at_current_staging(
                {"current_relative_dir": str(current)}, groups, staging_root
            )
        self.assertEqual([key[2] for key in active], ["202604"])
        self.assertEqual([key[2] for key, _rows in remaining], ["202603"])

    def test_resume_restores_dashboard_before_cleaning_active_staging_and_skips_prior_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging_root = root / "staging"; staging_root.mkdir()
            groups = {}
            sources = {}
            current = None
            for period in ("202605", "202604", "202603"):
                source = root / period; source.mkdir(); sources[period] = source
                digest = __import__("hashlib").sha1(str(source.resolve()).encode()).hexdigest()[:8]
                groups[(str(source), str(root / f"audit-{period}"), period)] = [{"period": period}]
                if period == "202604":
                    current = staging_root / f"{period}_demo_{digest}"; current.mkdir()
            args = SimpleNamespace(
                backend_url="http://mock", staging_root=str(staging_root), keep_staging=False,
                run_summary_csv=str(root / "summary.csv"), max_folders=0, max_per_folder=0,
            )
            status = {"current_relative_dir": str(current)}
            active_summary = {"staging_dir": str(current), "period": "202604"}
            later_summary = {"staging_dir": str(staging_root / "later"), "period": "202603"}
            with patch.object(mod, "json_request", return_value=status), \
                 patch.object(mod, "attach_existing_group", return_value=active_summary) as attach, \
                 patch.object(mod, "restore_backend_work_dir") as restore, \
                 patch.object(mod, "run_group", return_value=later_summary) as run, \
                 patch.object(mod.shutil, "rmtree") as remove, \
                 patch.object(mod, "write_dict_csv"):
                summaries = mod.resume_existing_then_continue(args, groups, "stamp")
        self.assertEqual([row["period"] for row in summaries], ["202604", "202603"])
        self.assertEqual(next(iter(attach.call_args.args[2]))[2], "202604")
        self.assertEqual(run.call_args.args[3], "202603")
        restore.assert_called_once_with("http://mock", sources["202604"])
        remove.assert_called_once_with(current, ignore_errors=True)
        self.assertFalse(args.keep_staging)

    def test_attach_polls_and_never_starts_or_switches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "photos"; source.mkdir()
            image = source / "one.jpg"; image.write_bytes(b"x")
            output = root / "out"; audit = output / "_ocr_audit" / "202601_demo"; audit.mkdir(parents=True)
            (audit / "success_records.csv").write_text("file_name,view_type,model,price\none.jpg,單機,X,100\n", encoding="utf-8")
            staging_root = output / "_ocr_staging"
            digest = __import__("hashlib").sha1(str(source.resolve()).encode()).hexdigest()[:8]
            staging = staging_root / f"202601_demo_{digest}"
            staging.mkdir(parents=True)
            args = SimpleNamespace(
                staging_root=str(staging_root), backend_url="http://mock", timeout_minutes=1,
                poll_seconds=0, run_summary_csv=str(root / "summary.csv"), keep_staging=True,
                dry_run=True, output_dir=str(output), price_symbol="$", min_completion_ratio=0.98,
                min_quality_guard_records=20, max_single_missing_ratio=0.65,
            )
            rows = [{"source_path": str(image), "source_folder": str(source), "audit_folder": str(audit), "period": "202601", "file_name": "one.jpg"}]
            grouped = {(str(source), str(audit), "202601"): rows}
            status = {"is_running": True, "current_relative_dir": str(staging), "stats": {"processed": 0, "total": 1}}
            done = {"is_running": False, "current_relative_dir": str(staging), "stats": {"processed": 1, "total": 1}}
            records = [{"file_name": "one.jpg", "view_type": "單機", "model": "X", "price": "100"}]
            with patch.object(mod, "json_request", side_effect=[status, records]) as request, patch.object(mod, "wait_for_folder_done", return_value=done), patch.object(mod, "rebuild_outputs", return_value={"records": 1}), patch.object(mod, "write_dict_csv") as write:
                result = mod.attach_existing_group(args, rows, grouped)
            self.assertEqual(result["processed"], 1)
            calls = [call.args[1] for call in request.call_args_list]
            self.assertEqual(calls, ["/api/status", "/api/success_records"])
            write.assert_called()

    def test_attach_rejects_multiple_groups_before_api(self):
        args = SimpleNamespace(backend_url="http://mock", staging_root="C:/out/_ocr_staging")
        groups = {("a", "b", "202601"): [], ("c", "d", "202601"): []}
        with patch.object(mod, "json_request") as request:
            with self.assertRaises(RuntimeError):
                mod.attach_existing_group(args, [], groups)
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()

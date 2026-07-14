import tempfile
import unittest
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rerun_staged_candidates as mod


class AttachExistingTests(unittest.TestCase):
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

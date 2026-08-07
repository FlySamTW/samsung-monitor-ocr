from pathlib import Path
import csv
import importlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch


class RecursiveCompletionContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = (Path(__file__).resolve().parent / "recursive_ocr_flat_export.py").read_text(encoding="utf-8")

    def test_folder_error_blocked_missing_or_changed_inventory_returns_nonzero(self):
        tail = self.runner[self.runner.index('incomplete_folders: list[dict[str, str]]'):]
        self.assertIn('reason = "missing_summary"', tail)
        self.assertIn('reason = "source_inventory_changed"', tail)
        self.assertIn('{"copied", "skipped_existing", "checkpoint_predecessor"}', tail)
        self.assertIn('not in', tail)
        self.assertIn('"error_count": len(incomplete_folders)', tail)
        self.assertLess(tail.index("return 2"), tail.index("return 0"))

    def test_historical_folders_require_shared_content_bound_receipt(self):
        self.assertNotIn("--ignore-current-year-review-gate", self.runner)
        self.assertIn("--historical-continuation-receipt", self.runner)
        self.assertIn("validate_receipt(", self.runner)
        self.assertIn('paused_reason"] = "historical_continuation_gate"', self.runner)
        self.assertIn("if frozen_inventory:", self.runner)
        self.assertIn("verify_inventory_folder", self.runner)
        self.assertIn("verify_full_inventory", self.runner)

    def test_bound_inventory_restart_loads_snapshot_without_full_rescan(self):
        self.assertIn('core_receipt.get("source_inventory_summary_sha256")', self.runner)
        self.assertIn("load_snapshot(", self.runner)
        fast = self.runner.index('core_receipt.get("source_inventory_summary_sha256")')
        slow = self.runner.index("ensure_frozen_snapshot(", fast)
        self.assertLess(self.runner.index("load_snapshot(", fast), slow)
        self.assertIn("verify_inventory_folder", self.runner[slow:])
        self.assertIn("verify_full_inventory", self.runner[slow:])

    def test_historical_receipt_skips_current_year_instead_of_reprocessing_it(self):
        tools_dir = Path(__file__).resolve().parent
        sys.path.insert(0, str(tools_dir))
        try:
            runner = importlib.import_module("recursive_ocr_flat_export")
        finally:
            sys.path.pop(0)
        self.assertTrue(
            runner.skip_period_already_authorized_by_current_year_completion(
                True, "202606"
            )
        )
        self.assertFalse(
            runner.skip_period_already_authorized_by_current_year_completion(
                True, "202512"
            )
        )
        self.assertFalse(
            runner.skip_period_already_authorized_by_current_year_completion(
                False, "202606"
            )
        )
        self.assertIn('"status": "skipped_existing"', self.runner)
        self.assertIn('"start_response": "sealed_current_year_terminal_authority"', self.runner)
        skip = self.runner.index("skip_period_already_authorized_by_current_year_completion(")
        start = self.runner.index("summary = process_folder(", skip)
        self.assertLess(self.runner.index('"status": "skipped_existing"', skip), start)

    def test_resume_requires_exact_success_copy_and_source_counts(self):
        tools_dir = Path(__file__).resolve().parent
        sys.path.insert(0, str(tools_dir))
        try:
            runner = importlib.import_module("recursive_ocr_flat_export")
        finally:
            sys.path.pop(0)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "result.jpg"
            target.write_bytes(b"image")
            original = root / "original.jpg"
            original.write_bytes(b"image")
            copied = root / "copied.csv"
            with copied.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["original_path", "target_path"])
                writer.writeheader()
                writer.writerow({"original_path": str(original), "target_path": str(target)})
            summary = root / "summary.csv"
            fields = ["folder", "status", "image_count", "success_records", "copied_count", "missing_result", "missing_source", "conflict", "failed", "copy_error", "copied_path"]
            base = {
                "folder": str(root / "source"), "status": "copied", "image_count": 1,
                "success_records": 0, "copied_count": 1, "missing_result": 0,
                "missing_source": 0, "conflict": 0, "failed": 0, "copy_error": "",
                "copied_path": str(copied),
            }
            with summary.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow(base)
            self.assertEqual(runner.build_resume_index(summary), {})
            base["success_records"] = 1
            with summary.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow(base)
            self.assertIn(base["folder"], runner.build_resume_index(summary))

    def test_start_folder_attaches_only_to_exact_active_folder(self):
        tools_dir = Path(__file__).resolve().parent
        sys.path.insert(0, str(tools_dir))
        try:
            runner = importlib.import_module("recursive_ocr_flat_export")
        finally:
            sys.path.pop(0)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp).resolve()
            args = SimpleNamespace(backend_url="http://mock", restart=False)
            with patch.object(
                runner,
                "json_request",
                return_value={"is_running": True, "image_dir": str(folder)},
            ) as request:
                result = runner.start_folder_batch(args, folder)
            self.assertEqual(result["status"], "attached")
            request.assert_called_once_with("http://mock", "/api/status", timeout=30)

    def test_start_folder_refuses_different_active_folder(self):
        tools_dir = Path(__file__).resolve().parent
        sys.path.insert(0, str(tools_dir))
        try:
            runner = importlib.import_module("recursive_ocr_flat_export")
        finally:
            sys.path.pop(0)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            folder = root / "expected"
            folder.mkdir()
            other = root / "other"
            other.mkdir()
            args = SimpleNamespace(backend_url="http://mock", restart=False)
            with patch.object(
                runner,
                "json_request",
                return_value={"is_running": True, "image_dir": str(other)},
            ):
                with self.assertRaisesRegex(RuntimeError, "different folder"):
                    runner.start_folder_batch(args, folder)

    def test_active_checkpoint_repairs_and_skips_preceding_attach_errors(self):
        self.assertIn('active_status = json_request(args.backend_url, "/api/status"', self.runner)
        self.assertIn('repaired_status = "checkpoint_predecessor"', self.runner)
        self.assertIn('"start_response": "active_checkpoint_proves_prior_traversal"', self.runner)
        self.assertIn('state["active_checkpoint_floor"]', self.runner)
        floor = self.runner.index("active_floor_index = 0")
        traversal = self.runner.index("while True:", floor)
        self.assertIn("backend_configured = True", self.runner[floor:traversal])
        self.assertLess(self.runner.index("write_merged_summaries()", floor), traversal)

    def test_checkpoint_predecessor_is_a_durable_resume_status(self):
        self.assertIn(
            '{"copied", "skipped_existing", "checkpoint_predecessor"}',
            self.runner,
        )
        self.assertIn('if status == "checkpoint_predecessor":', self.runner)

    def test_one_folder_error_cannot_overwrite_every_later_summary(self):
        self.assertIn('state["paused_reason"] = "folder_processing_error"', self.runner)
        self.assertIn("if folder_failed:", self.runner)


if __name__ == "__main__":
    unittest.main()

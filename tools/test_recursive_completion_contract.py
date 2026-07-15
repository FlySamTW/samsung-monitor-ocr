from pathlib import Path
import csv
import importlib
import sys
import tempfile
import unittest


class RecursiveCompletionContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = (Path(__file__).resolve().parent / "recursive_ocr_flat_export.py").read_text(encoding="utf-8")

    def test_folder_error_blocked_missing_or_changed_inventory_returns_nonzero(self):
        tail = self.runner[self.runner.index('incomplete_folders: list[dict[str, str]]'):]
        self.assertIn('reason = "missing_summary"', tail)
        self.assertIn('reason = "source_inventory_changed"', tail)
        self.assertIn('{"copied", "skipped_existing"}', tail)
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


if __name__ == "__main__":
    unittest.main()

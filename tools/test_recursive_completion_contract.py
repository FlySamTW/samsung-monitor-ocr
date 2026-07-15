from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()

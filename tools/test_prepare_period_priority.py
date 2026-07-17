from pathlib import Path
import csv
import json
import tempfile
import unittest

from tools.prepare_period_priority import prepare


class PreparePeriodPriorityTests(unittest.TestCase):
    def test_dry_run_is_read_only_and_execute_writes_bound_staging(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_root = root / "source"
            source = source_root / "商化照片-202606"
            output = root / "output"
            source.mkdir(parents=True)
            for index, payload in enumerate((b"a", b"bb", b"ccc"), start=1):
                (source / f"M-test-{index}.jpg").write_bytes(payload)

            dry = prepare(source_root, source, output, "202606", execute=False, stamp="dry")
            self.assertEqual(dry["image_count"], 3)
            self.assertFalse(output.exists())

            result = prepare(source_root, source, output, "202606", execute=True, stamp="run")
            self.assertTrue(result["complete"])
            staging = Path(result["staging_dir"])
            self.assertEqual(len(list(staging.glob("*.jpg"))), 3)
            self.assertFalse((staging / ".period_priority_incomplete").exists())

            source_map = json.loads((staging / ".ocr_source_map.json").read_text(encoding="utf-8"))
            self.assertEqual(set(source_map["items"]), {f"M-test-{index}.jpg" for index in range(1, 4)})
            for item in source_map["items"].values():
                self.assertEqual(item["period"], "202606")
                self.assertEqual(item["audit_folder"], result["audit_folder"])
                self.assertEqual(len(item["source_item_id"]), 64)

            with Path(result["candidate_csv"]).open("r", encoding="utf-8-sig", newline="") as handle:
                candidates = list(csv.DictReader(handle))
            self.assertEqual(len(candidates), 3)
            self.assertTrue(all(row["reason"] == "new_period_priority" for row in candidates))

            with Path(result["discovery_csv"]).open("r", encoding="utf-8-sig", newline="") as handle:
                discovery = list(csv.DictReader(handle))
            self.assertEqual(len(discovery), 1)
            self.assertEqual(discovery[0]["period"], "202606")
            self.assertEqual(discovery[0]["image_count"], "3")

    def test_refuses_period_mismatch_and_outside_source_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_root = root / "source"
            mismatch = source_root / "商化照片-202605"
            outside = root / "outside-202606"
            mismatch.mkdir(parents=True)
            outside.mkdir()
            (mismatch / "a.jpg").write_bytes(b"a")
            (outside / "a.jpg").write_bytes(b"a")
            with self.assertRaises(ValueError):
                prepare(source_root, mismatch, root / "output", "202606", execute=False)
            with self.assertRaises(ValueError):
                prepare(source_root, outside, root / "output", "202606", execute=False)


if __name__ == "__main__":
    unittest.main()

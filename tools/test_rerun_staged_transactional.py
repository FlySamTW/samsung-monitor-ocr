import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

import rerun_staged_candidates as staged


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = list(rows[0])
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


class TransactionalRebuildTests(unittest.TestCase):
    def test_only_verified_candidate_replaces_previous_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            output = root / "output"
            audit = root / "audit"
            source.mkdir()
            output.mkdir()
            audit.mkdir()

            candidate = source / "M-city-store-1.jpg"
            untouched = source / "M-city-store-2.jpg"
            Image.new("RGB", (40, 30), "red").save(candidate)
            Image.new("RGB", (40, 30), "blue").save(untouched)

            old_candidate = output / "old-candidate.jpg"
            old_untouched = output / "old-untouched.jpg"
            old_candidate.write_bytes(candidate.read_bytes())
            old_untouched.write_bytes(untouched.read_bytes())
            untouched_before = old_untouched.read_bytes()
            write_rows(
                audit / "copied.csv",
                [
                    {
                        "status": "copied",
                        "reason": "",
                        "period": "202601",
                        "original_name": candidate.name,
                        "target_name": old_candidate.name,
                        "category": "單機",
                        "model": "OLD",
                        "price": "＄1000",
                        "original_path": str(candidate),
                        "target_path": str(old_candidate),
                    },
                    {
                        "status": "copied",
                        "reason": "",
                        "period": "202601",
                        "original_name": untouched.name,
                        "target_name": old_untouched.name,
                        "category": "單機",
                        "model": "KEEP",
                        "price": "＄2000",
                        "original_path": str(untouched),
                        "target_path": str(old_untouched),
                    },
                ],
            )
            records = [
                {"file_name": candidate.name, "view_type": "單機", "model": "NEW", "price": "3000"},
                {"file_name": untouched.name, "view_type": "單機", "model": "KEEP", "price": "2000"},
            ]
            args = SimpleNamespace(
                output_dir=str(output),
                dry_run=False,
                price_symbol="＄",
            )

            result = staged.rebuild_outputs(
                args,
                source,
                audit,
                "202601",
                records,
                {candidate.name},
            )

            copied = staged.read_dict_csv(audit / "copied.csv")
            candidate_row = next(row for row in copied if row["original_name"] == candidate.name)
            new_candidate = Path(candidate_row["target_path"])
            self.assertEqual(result["copied"], 1)
            self.assertTrue(new_candidate.is_file())
            self.assertNotEqual(new_candidate, old_candidate)
            self.assertFalse(old_candidate.exists())
            with Image.open(new_candidate) as image:
                image.verify()
            self.assertEqual(old_untouched.read_bytes(), untouched_before)
            self.assertEqual(
                next(row for row in copied if row["original_name"] == untouched.name)["target_path"],
                str(old_untouched),
            )
            self.assertFalse(list(output.glob(".*.staged-rerun-*")))


if __name__ == "__main__":
    unittest.main()

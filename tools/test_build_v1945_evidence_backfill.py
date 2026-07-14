import csv
import json
import tempfile
import unittest
from pathlib import Path

from tools.build_v1945_evidence_backfill import run, stable_source_id


class EvidenceBackfillBuilderTests(unittest.TestCase):
    def make_audit(self, root: Path, sources: list[Path]) -> Path:
        audit = root / "_ocr_audit"
        folder = audit / "0001_202605_fixture"
        folder.mkdir(parents=True)
        with (folder / "copied.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=("period", "original_name", "original_path", "target_name"))
            writer.writeheader()
            for source in sources:
                writer.writerow({
                    "period": "202605", "original_name": source.name,
                    "original_path": str(source), "target_name": f"M-202605-{source.name}",
                })
        return audit

    def test_verified_source_is_skipped_and_csv_is_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second = root / "one.jpg", root / "two.jpg"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            audit = self.make_audit(root, [first, second])
            trace = audit / "v1945_evidence_trace.jsonl"
            trace.write_text(json.dumps({
                "trace_version": "v19.45",
                "source_item_id": stable_source_id(first),
                "guard_decision": {"verified": True},
            }) + "\n", encoding="utf-8")
            output = audit / "backfill.csv"
            summary = run(audit, "2026", output, execute=True)
            with output.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertTrue(summary["executed"])
            self.assertEqual(summary["already_verified_year_sources"], 1)
            self.assertEqual([row["file_name"] for row in rows], ["two.jpg"])
            self.assertEqual(rows[0]["reason"], "v1945_evidence_backfill")

    def test_missing_source_fails_closed_without_replacing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing.jpg"
            audit = self.make_audit(root, [missing])
            output = audit / "backfill.csv"
            output.write_text("sentinel", encoding="utf-8")
            summary = run(audit, "2026", output, execute=True)
            self.assertFalse(summary["executed"])
            self.assertEqual(summary["missing_sources"], 1)
            self.assertEqual(output.read_text(encoding="utf-8"), "sentinel")

    def test_other_year_is_not_emitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "one.jpg"
            source.write_bytes(b"one")
            audit = self.make_audit(root, [source])
            output = audit / "backfill.csv"
            summary = run(audit, "2025", output, execute=True)
            self.assertEqual(summary["candidate_rows"], 0)
            with output.open("r", encoding="utf-8-sig", newline="") as handle:
                self.assertEqual(list(csv.DictReader(handle)), [])


if __name__ == "__main__":
    unittest.main()

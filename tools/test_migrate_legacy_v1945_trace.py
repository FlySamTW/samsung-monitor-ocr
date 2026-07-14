from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from tools.migrate_legacy_v1945_trace import migrate_trace, stable_source_id


class LegacyTraceMigrationTests(unittest.TestCase):
    def _candidate_csv(self, root: Path, source: Path, period: str = "202604") -> Path:
        path = root / "candidates.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["period", "file_name", "source_path", "audit_folder"])
            writer.writeheader()
            writer.writerow({
                "period": period,
                "file_name": source.name,
                "source_path": str(source),
                "audit_folder": str(root / "_ocr_audit" / "period"),
            })
        return path

    def test_migration_enriches_identity_deduplicates_and_writes_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / "商化照片-202604" / "M-test-1.jpg"
            original.parent.mkdir()
            original.write_bytes(b"photo")
            candidate = self._candidate_csv(root, original)
            legacy_path = root / "_ocr_staging" / "202604_group" / original.name
            trace = {
                "trace_id": "trace-1",
                "source_path": str(legacy_path),
                "file_name": original.name,
                "attempt": 1,
                "guard_decision": {"verified": True},
            }
            source = root / "v1945_evidence_trace.jsonl"
            source.write_text(
                json.dumps(trace, ensure_ascii=False) + "\n" + json.dumps(trace, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            destination = root / "_ocr_audit" / "v1945_evidence_trace.jsonl"

            summary = migrate_trace(source, destination, [candidate], execute=True)
            self.assertTrue(summary["executed"])
            self.assertEqual(summary["resolved_new_rows"], 1)
            self.assertEqual(summary["duplicate_rows"], 1)
            row = json.loads(destination.read_text(encoding="utf-8").strip())
            self.assertEqual(row["source_item_id"], stable_source_id(str(original)))
            self.assertEqual(row["source_path"], str(original.resolve()))
            self.assertEqual(row["staging_source_path"], str(legacy_path))
            self.assertEqual(row["period"], "202604")
            self.assertTrue(row["legacy_trace_migrated"])

    def test_unresolved_row_is_fail_closed_and_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "v1945_evidence_trace.jsonl"
            source.write_text(json.dumps({"trace_id": "x", "file_name": "unknown.jpg"}) + "\n", encoding="utf-8")
            candidate = root / "empty.csv"
            candidate.write_text("period,file_name,source_path,audit_folder\n", encoding="utf-8")
            destination = root / "_ocr_audit" / "v1945_evidence_trace.jsonl"

            summary = migrate_trace(source, destination, [candidate], execute=True)
            self.assertFalse(summary["executed"])
            self.assertEqual(summary["unresolved_rows"], 1)
            self.assertFalse(destination.exists())

    def test_existing_destination_is_preserved_and_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / "商化照片-202604" / "M-test-2.jpg"
            original.parent.mkdir()
            original.write_bytes(b"photo")
            candidate = self._candidate_csv(root, original)
            source = root / "legacy.jsonl"
            source.write_text(json.dumps({
                "trace_id": "new",
                "source_path": str(root / "_ocr_staging" / "202604_group" / original.name),
                "file_name": original.name,
            }) + "\n", encoding="utf-8")
            destination = root / "_ocr_audit" / "v1945_evidence_trace.jsonl"
            destination.parent.mkdir()
            destination.write_text(json.dumps({"trace_id": "existing", "source_item_id": "a" * 64}) + "\n", encoding="utf-8")

            summary = migrate_trace(source, destination, [candidate], execute=True)
            rows = [json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(summary["destination_rows_after"], 2)
            self.assertEqual({row["trace_id"] for row in rows}, {"existing", "new"})


if __name__ == "__main__":
    unittest.main(verbosity=2)

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from tools.build_upload_gate_proof import build_proof, file_sha256


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = list(rows[0]) if rows else ["source_path", "file_name", "status", "reasons"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


class UploadGateBatchBindingTests(unittest.TestCase):
    def build_fixture(self, root: Path) -> tuple[Path, list[dict[str, str]]]:
        output = root / "output"
        audit = output / "_ocr_audit"
        manifest = output / "_drive_upload"
        audit.mkdir(parents=True)
        manifest.mkdir(parents=True)

        candidate = audit / "candidate.csv"
        candidate_summary = audit / "candidate.csv.summary.json"
        result = audit / "result.csv"
        run_summary = audit / "run.csv"
        for path in (candidate, candidate_summary, result, run_summary):
            path.write_text("authority\n", encoding="utf-8")

        risk_csv = audit / "distant_followme_risk_2026_latest.csv"
        risk_csv.write_text("file_name,reason\n", encoding="utf-8")
        audit_input = "a" * 64
        finalization = {
            "audit_complete": True,
            "complete": True,
            "audit_input_sha256": audit_input,
            "backfill_run_id": "run-1",
            "candidate_rows": 1,
            "candidate_csv": str(candidate.resolve()),
            "candidate_summary_json": str(candidate_summary.resolve()),
            "result_csv": str(result.resolve()),
            "run_summary_csv": str(run_summary.resolve()),
        }
        risk_json = audit / "distant_followme_risk_2026_latest.json"
        risk_json.write_text(
            json.dumps(
                {
                    "audit_complete": True,
                    "audit_input_sha256": audit_input,
                    "risk_output_sha256": file_sha256(risk_csv),
                    "backfill_run_id": "run-1",
                    "finalization_proof": finalization,
                }
            ),
            encoding="utf-8",
        )

        pending_rows = [
            {
                "source_path": "source/one.jpg", "file_name": "one.jpg", "year": "2026",
                "period": "202601", "drive_folder": "2026", "size_bytes": "5",
                "content_sha256": "1" * 64,
                "status": "ready", "reasons": "",
            },
            {
                "source_path": "source/two.jpg", "file_name": "two.jpg", "year": "2026",
                "period": "202601", "drive_folder": "2026", "size_bytes": "5",
                "content_sha256": "2" * 64,
                "status": "ready", "reasons": "",
            },
        ]
        pending = manifest / "drive_upload_ready_pending.csv"
        next_batch = manifest / "drive_upload_next_batch.csv"
        write_csv(pending, pending_rows)
        write_csv(next_batch, pending_rows[:1])
        summary = {
            "current_year_risk_audit_fresh": True,
            "current_year_upload_gate_open": True,
            "current_audit_input_sha256": audit_input,
            "ready_pending": 2,
            "next_batch": 1,
            "next_batch_sha256": file_sha256(next_batch),
            "upload_scope_years": ["2026"],
        }
        (manifest / "drive_upload_summary.json").write_text(json.dumps(summary), encoding="utf-8")
        return output, pending_rows

    def test_limited_batch_is_bound_as_pending_prefix(self):
        with tempfile.TemporaryDirectory() as temp:
            output, _ = self.build_fixture(Path(temp))
            proof, errors = build_proof(output, 2026)
            self.assertEqual(errors, [])
            self.assertIsNotNone(proof)
            self.assertEqual(proof["pending_count"], 2)
            self.assertEqual(proof["next_batch_count"], 1)

    def test_non_prefix_batch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            output, pending_rows = self.build_fixture(Path(temp))
            manifest = output / "_drive_upload"
            next_batch = manifest / "drive_upload_next_batch.csv"
            write_csv(next_batch, pending_rows[1:])
            summary_path = manifest / "drive_upload_summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["next_batch_sha256"] = file_sha256(next_batch)
            summary_path.write_text(json.dumps(summary), encoding="utf-8")
            proof, errors = build_proof(output, 2026)
            self.assertIsNone(proof)
            self.assertIn("next_batch_not_pending_prefix", errors)

    def test_same_identity_with_changed_year_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            output, pending_rows = self.build_fixture(Path(temp))
            manifest = output / "_drive_upload"
            next_batch = manifest / "drive_upload_next_batch.csv"
            changed = dict(pending_rows[0])
            changed["year"] = "2025"
            changed["drive_folder"] = "2025"
            write_csv(next_batch, [changed])
            summary_path = manifest / "drive_upload_summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["next_batch_sha256"] = file_sha256(next_batch)
            summary_path.write_text(json.dumps(summary), encoding="utf-8")
            proof, errors = build_proof(output, 2026)
            self.assertIsNone(proof)
            self.assertIn("next_batch_not_pending_prefix", errors)

    def test_pending_rows_require_a_nonempty_next_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            output, _ = self.build_fixture(Path(temp))
            manifest = output / "_drive_upload"
            next_batch = manifest / "drive_upload_next_batch.csv"
            write_csv(next_batch, [])
            summary_path = manifest / "drive_upload_summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["next_batch"] = 0
            summary["next_batch_sha256"] = file_sha256(next_batch)
            summary_path.write_text(json.dumps(summary), encoding="utf-8")
            proof, errors = build_proof(output, 2026)
            self.assertIsNone(proof)
            self.assertIn("next_batch_empty_with_pending", errors)


if __name__ == "__main__":
    unittest.main()

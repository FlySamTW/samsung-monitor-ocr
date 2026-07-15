import csv
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.historical_continuation_gate import (
    EVIDENCE_GUARD_REVISION,
    MARKER_NAME,
    PROOF_NAME,
    RECEIPT_NAME,
    REVIEW_NAME,
    bind_source_inventory,
    create_or_migrate_request,
    validate_receipt,
    write_receipt,
)
from tools.source_inventory_snapshot import ensure_frozen_snapshot


class HistoricalContinuationGateTests(unittest.TestCase):
    def build_fixture(self, root: Path):
        source = root / "source"
        output = root / "output"
        audit = output / "_ocr_audit"
        drive = output / "_drive_upload"
        source.mkdir()
        audit.mkdir(parents=True)
        drive.mkdir(parents=True)
        requested_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        create_or_migrate_request(
            source,
            output,
            current_year=2026,
            requested_at=requested_at,
        )
        proof = {
            "schema": "samsung-ocr-upload-gate-proof/v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "gate_open": True,
            "pending_count": 0,
            "upload_scope_years": [2026],
            "audit_input_sha256": "a" * 64,
            "manifest_summary_sha256": "b" * 64,
            "pending_sha256": "c" * 64,
            "backfill_run_id": "rev14-run",
        }
        (drive / PROOF_NAME).write_text(json.dumps(proof), encoding="utf-8")
        marker = {
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "upload_gate_schema": proof["schema"],
            "pending_count": 0,
            **{name: proof[name] for name in (
                "audit_input_sha256",
                "manifest_summary_sha256",
                "pending_sha256",
                "backfill_run_id",
            )},
        }
        (audit / MARKER_NAME).write_text(json.dumps(marker), encoding="utf-8")
        with (drive / REVIEW_NAME).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["year", "reasons"])
            writer.writeheader()
        return source, output, audit, drive

    @staticmethod
    def idle_status(_url: str):
        return {"running": False, "worker_alive": False, "processed": 10, "total": 10}

    def test_valid_authorities_write_and_revalidate_content_bound_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output, audit, _drive = self.build_fixture(Path(tmp))
            result = write_receipt(
                source,
                output,
                current_year=2026,
                backend_url="http://127.0.0.1:5000",
                status_reader=self.idle_status,
            )
            self.assertTrue(result["valid"])
            receipt, errors = validate_receipt(
                audit / RECEIPT_NAME,
                source,
                output,
                current_year=2026,
                backend_url="http://127.0.0.1:5000",
            )
            self.assertIsNotNone(receipt)
            self.assertEqual(errors, [])
            self.assertEqual(receipt["evidence_guard_revision"], EVIDENCE_GUARD_REVISION)

    def test_historical_receipt_requires_exact_frozen_inventory_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output, audit, _drive = self.build_fixture(Path(tmp))
            (source / "2025").mkdir()
            (source / "2025" / "a.jpg").write_bytes(b"photo")
            result = write_receipt(
                source, output, current_year=2026,
                backend_url="http://127.0.0.1:5000", status_reader=self.idle_status,
            )
            self.assertTrue(result["valid"])
            receipt, errors = validate_receipt(
                audit / RECEIPT_NAME, source, output, current_year=2026,
                backend_url="http://127.0.0.1:5000", require_source_inventory=True,
            )
            self.assertIsNone(receipt)
            self.assertIn("source_inventory_binding_missing", errors)
            ensure_frozen_snapshot(audit, source)
            bound = bind_source_inventory(
                audit / RECEIPT_NAME, source, output, current_year=2026,
                backend_url="http://127.0.0.1:5000",
            )
            self.assertTrue(bound["valid"])
            (audit / "source_inventory_v1.csv").write_text("tampered", encoding="utf-8")
            receipt, errors = validate_receipt(
                audit / RECEIPT_NAME, source, output, current_year=2026,
                backend_url="http://127.0.0.1:5000", require_source_inventory=True,
            )
            self.assertIsNone(receipt)
            self.assertTrue(any("source_inventory_csv_sha256" in item for item in errors))

    def test_current_year_review_row_blocks_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output, _audit, drive = self.build_fixture(Path(tmp))
            with (drive / REVIEW_NAME).open("a", encoding="utf-8-sig", newline="") as handle:
                csv.writer(handle).writerow(["2026", "no_price"])
            result = write_receipt(
                source,
                output,
                current_year=2026,
                backend_url="http://127.0.0.1:5000",
                status_reader=self.idle_status,
            )
            self.assertFalse(result["valid"])
            self.assertTrue(any("current_year_review_required_nonzero" in item for item in result["errors"]))

    def test_runtime_interlocks_and_busy_backend_block_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output, audit, _drive = self.build_fixture(Path(tmp))
            (audit / "model_benchmark.lock").write_text("locked", encoding="utf-8")
            locked = write_receipt(
                source,
                output,
                current_year=2026,
                backend_url="http://127.0.0.1:5000",
                status_reader=self.idle_status,
            )
            self.assertFalse(locked["valid"])
            self.assertTrue(any("active_interlock" in item for item in locked["errors"]))
            (audit / "model_benchmark.lock").unlink()
            busy = write_receipt(
                source,
                output,
                current_year=2026,
                backend_url="http://127.0.0.1:5000",
                status_reader=lambda _url: {"running": True, "worker_alive": True, "processed": 1, "total": 2},
            )
            self.assertFalse(busy["valid"])
            self.assertIn("backend_not_idle", busy["errors"])

    def test_stale_shared_proof_cannot_create_a_new_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output, _audit, drive = self.build_fixture(Path(tmp))
            proof_path = drive / PROOF_NAME
            proof = json.loads(proof_path.read_text(encoding="utf-8"))
            proof["generated_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            proof_path.write_text(json.dumps(proof), encoding="utf-8")
            result = write_receipt(
                source, output, current_year=2026,
                backend_url="http://127.0.0.1:5000", status_reader=self.idle_status,
            )
            self.assertFalse(result["valid"])
            self.assertTrue(any("upload_gate_proof_stale" in item for item in result["errors"]))

    def test_authority_tamper_invalidates_existing_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output, audit, drive = self.build_fixture(Path(tmp))
            result = write_receipt(
                source,
                output,
                current_year=2026,
                backend_url="http://127.0.0.1:5000",
                status_reader=self.idle_status,
            )
            self.assertTrue(result["valid"])
            proof_path = drive / PROOF_NAME
            proof = json.loads(proof_path.read_text(encoding="utf-8"))
            proof["backfill_run_id"] = "tampered"
            proof_path.write_text(json.dumps(proof), encoding="utf-8")
            receipt, errors = validate_receipt(
                audit / RECEIPT_NAME,
                source,
                output,
                current_year=2026,
                backend_url="http://127.0.0.1:5000",
            )
            self.assertIsNone(receipt)
            self.assertTrue(any("current_year_marker_backfill_run_id_mismatch" in item for item in errors))

    def test_legacy_request_and_noncanonical_receipt_path_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output, audit, _drive = self.build_fixture(Path(tmp))
            request_path = audit / "full_project_continuation_requested.json"
            request_path.write_text(json.dumps({"requested_at": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
            rejected = write_receipt(
                source,
                output,
                current_year=2026,
                backend_url="http://127.0.0.1:5000",
                status_reader=self.idle_status,
            )
            self.assertFalse(rejected["valid"])
            self.assertIn("request_schema_invalid", rejected["errors"])
            other = output / "receipt.json"
            other.write_text("{}", encoding="utf-8")
            receipt, errors = validate_receipt(
                other,
                source,
                output,
                current_year=2026,
                backend_url="http://127.0.0.1:5000",
            )
            self.assertIsNone(receipt)
            self.assertIn("receipt_path_not_canonical", errors)


if __name__ == "__main__":
    unittest.main()

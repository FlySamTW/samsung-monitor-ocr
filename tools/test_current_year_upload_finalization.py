from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skills.audit_fields import EVIDENCE_GUARD_REVISION
from tools import build_upload_gate_proof as upload_gate_module
from tools.audit_distant_followme_risk import (
    file_sha256,
    finalization_input_sha256,
    validate_finalization_proof,
)
from tools.build_upload_gate_proof import run as build_upload_gate_proof
from tools.prepare_drive_upload_manifest import current_year_risk_audit_is_fresh, main as prepare_manifest_main


def write_csv(path: Path, rows: list[dict[str, object]], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


class CurrentYearUploadFinalizationTests(unittest.TestCase):
    def build_fixture(self, root: Path, *, candidate_count: int = 1) -> dict[str, Path]:
        output = root / "output"
        audit = output / "_ocr_audit" / "0001_202601_sample"
        source_dir = root / "source" / "202601"
        source = source_dir / "one.jpg"
        target = output / "M-202601-sample-one.jpg"
        source_dir.mkdir(parents=True)
        audit.mkdir(parents=True)
        output.mkdir(exist_ok=True)
        source.write_bytes(b"source-image")
        target.write_bytes(b"target-image")

        copied = {
            "period": "202601",
            "original_name": source.name,
            "target_name": target.name,
            "original_path": str(source.resolve()),
            "target_path": str(target.resolve()),
        }
        write_csv(audit / "copied.csv", [copied], list(copied))
        success_row = {
            "file_name": source.name,
            "period": "202601",
            "auto_verified": "true",
            "auto_review_required": "false",
            "evidence_contract_version": "v19.45",
            "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
            "evidence_contract_valid": "true",
            "view_type": "單機",
            "model": "S24F332EAC",
            "price": "2390",
            "thinking": "唯一主角與價牌相符",
            "run_id": "test",
        }
        write_csv(audit / "success_records.csv", [success_row], list(success_row))
        write_csv(audit / "rename_plan.csv", [{"original_name": source.name}], ["original_name"])
        (audit / "v1945_evidence_trace.jsonl").write_text(json.dumps({
            "trace_version": "v19.45",
            "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
            "file_name": source.name,
            "period": "202601",
            "source_path": str(source.resolve()),
            "guard_decision": {"verified": True},
        }) + "\n", encoding="utf-8")

        audit_root = output / "_ocr_audit"
        candidate = audit_root / "v1945_evidence_backfill_2026.csv"
        result = audit_root / "v1945_evidence_backfill_2026_results.csv"
        summary = audit_root / "v1945_evidence_backfill_2026_run_summary.csv"
        candidate_rows = [{
            "source_path": str(source.resolve()),
            "file_name": source.name,
            "period": "202601",
            "audit_folder": str(audit.resolve()),
            "reason": "v1945_evidence_backfill",
            "source_item_id": "",
        }] if candidate_count else []
        write_csv(
            candidate,
            candidate_rows,
            ["source_path", "file_name", "period", "audit_folder", "reason", "source_item_id"],
        )
        candidate.with_suffix(candidate.suffix + ".summary.json").write_text(
            json.dumps({
                "executed": True,
                "output": str(candidate.resolve()),
                "candidate_rows": candidate_count,
                "unique_year_sources": 1,
                "already_verified_year_sources": 1 - candidate_count,
                "missing_sources": 0,
                "conflicting_sources": 0,
                "invalid_rows": 0,
            }),
            encoding="utf-8",
        )
        if candidate_count:
            result_row = {
                "source_path": str(source.resolve()),
                "file_name": source.name,
                "period": "202601",
                "audit_folder": str(audit.resolve()),
                "source_folder": str(source_dir.resolve()),
            }
            write_csv(result, [result_row], list(result_row))
            run_row = {
                "folder": str(source_dir.resolve()),
                "period": "202601",
                "audit_folder": str(audit.resolve()),
                "queued": 1,
                "staged": 1,
                "processed": 1,
                "aborted": 0,
                "failed_replacements": 0,
            }
            write_csv(summary, [run_row], list(run_row))
        return {"output": output, "audit": audit, "candidate": candidate, "result": result, "summary": summary}

    @staticmethod
    def completed_backfill() -> tuple[list[dict[str, str]], dict[str, int]]:
        return [], {
            "unique_year_sources": 1,
            "already_verified_year_sources": 1,
            "missing_sources": 0,
            "conflicting_sources": 0,
            "invalid_rows": 0,
        }

    def test_complete_candidate_run_produces_content_bound_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = self.build_fixture(Path(tmp))
            with patch("tools.audit_distant_followme_risk.build_candidates", return_value=self.completed_backfill()):
                proof = validate_finalization_proof(item["output"], 2026, item["candidate"], item["result"], item["summary"])
            self.assertTrue(proof["complete"])
            self.assertEqual(proof["expected_candidate_count"], 1)
            self.assertEqual(proof["scanned_result_count"], 1)
            self.assertEqual(proof["missing_or_invalid"], [])

    def test_zero_candidate_is_complete_only_when_all_year_sources_are_already_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = self.build_fixture(Path(tmp), candidate_count=0)
            with patch("tools.audit_distant_followme_risk.build_candidates", return_value=self.completed_backfill()):
                proof = validate_finalization_proof(item["output"], 2026, item["candidate"], item["result"], item["summary"])
            self.assertTrue(proof["complete"])
            self.assertEqual(proof["candidate_rows"], 0)
            self.assertEqual(proof["expected_candidate_count"], 1)
            self.assertEqual(proof["scanned_result_count"], 1)

    def test_missing_selected_result_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = self.build_fixture(Path(tmp))
            write_csv(item["result"], [], ["source_path", "file_name", "period", "audit_folder", "source_folder"])
            with patch("tools.audit_distant_followme_risk.build_candidates", return_value=self.completed_backfill()):
                proof = validate_finalization_proof(item["output"], 2026, item["candidate"], item["result"], item["summary"])
            self.assertFalse(proof["complete"])
            self.assertFalse(proof["result_source_set_matches"])

    def test_canonical_tamper_changes_fingerprint_and_stales_risk_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = self.build_fixture(Path(tmp))
            with patch("tools.audit_distant_followme_risk.build_candidates", return_value=self.completed_backfill()):
                proof = validate_finalization_proof(item["output"], 2026, item["candidate"], item["result"], item["summary"])
            risk_csv = item["output"] / "_ocr_audit" / "distant_followme_risk_2026_latest.csv"
            risk_json = item["output"] / "_ocr_audit" / "distant_followme_risk_2026_latest.json"
            write_csv(risk_csv, [], ["file_name", "reason"])
            risk_json.write_text(json.dumps({
                "audit_complete": True,
                "audit_input_sha256": proof["audit_input_sha256"],
                "risk_output_sha256": file_sha256(risk_csv),
                "finalization_proof": proof,
            }), encoding="utf-8")
            self.assertTrue(current_year_risk_audit_is_fresh(item["output"], 2026))
            before = finalization_input_sha256(item["output"], 2026, item["candidate"], item["result"], item["summary"])
            with (item["audit"] / "success_records.csv").open("a", encoding="utf-8") as handle:
                handle.write("tampered\n")
            after = finalization_input_sha256(item["output"], 2026, item["candidate"], item["result"], item["summary"])
            self.assertNotEqual(before, after)
            self.assertFalse(current_year_risk_audit_is_fresh(item["output"], 2026))

    def test_manifest_emits_gate_proof_and_exact_next_batch_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = self.build_fixture(root)
            with patch("tools.audit_distant_followme_risk.build_candidates", return_value=self.completed_backfill()):
                proof = validate_finalization_proof(item["output"], 2026, item["candidate"], item["result"], item["summary"])
            risk_csv = item["output"] / "_ocr_audit" / "distant_followme_risk_2026_latest.csv"
            risk_json = item["output"] / "_ocr_audit" / "distant_followme_risk_2026_latest.json"
            write_csv(risk_csv, [], ["file_name", "reason"])
            risk_json.write_text(json.dumps({
                "audit_complete": True,
                "audit_input_sha256": proof["audit_input_sha256"],
                "risk_output_sha256": file_sha256(risk_csv),
                "backfill_run_id": proof["backfill_run_id"],
                "finalization_proof": proof,
            }), encoding="utf-8")
            manifest = root / "manifest"
            argv = [
                "prepare_drive_upload_manifest.py",
                "--output-dir", str(item["output"]),
                "--manifest-dir", str(manifest),
                "--no-stage",
            ]
            with patch.object(sys, "argv", argv):
                self.assertEqual(prepare_manifest_main(), 0)
            summary = json.loads((manifest / "drive_upload_summary.json").read_text(encoding="utf-8"))
            batch = manifest / "drive_upload_next_batch.csv"
            self.assertTrue(summary["current_year_risk_audit_fresh"])
            self.assertTrue(summary["current_year_upload_gate_open"])
            self.assertEqual(summary["current_year_upload_gate_fail_reasons"], [])
            self.assertEqual(summary["current_year_finalization_proof"]["expected_candidate_count"], 1)
            self.assertEqual(summary["next_batch_sha256"], file_sha256(batch))

    def test_shared_upload_gate_proof_is_atomic_and_tamper_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = self.build_fixture(root)
            with patch("tools.audit_distant_followme_risk.build_candidates", return_value=self.completed_backfill()):
                proof = validate_finalization_proof(item["output"], 2026, item["candidate"], item["result"], item["summary"])
            risk_csv = item["output"] / "_ocr_audit" / "distant_followme_risk_2026_latest.csv"
            risk_json = item["output"] / "_ocr_audit" / "distant_followme_risk_2026_latest.json"
            write_csv(risk_csv, [], ["file_name", "reason"])
            risk_json.write_text(json.dumps({
                "audit_complete": True,
                "audit_input_sha256": proof["audit_input_sha256"],
                "risk_output_sha256": file_sha256(risk_csv),
                "backfill_run_id": proof["backfill_run_id"],
                "finalization_proof": proof,
            }), encoding="utf-8")
            argv = [
                "prepare_drive_upload_manifest.py",
                "--output-dir", str(item["output"]),
                "--no-stage",
            ]
            with patch.object(sys, "argv", argv):
                self.assertEqual(prepare_manifest_main(), 0)
            built = build_upload_gate_proof(item["output"], 2026, execute=True)
            proof_path = item["output"] / "_drive_upload" / "upload_gate_proof.json"
            self.assertTrue(built["valid"])
            self.assertTrue(proof_path.is_file())
            pending = item["output"] / "_drive_upload" / "drive_upload_ready_pending.csv"
            with pending.open("a", encoding="utf-8") as handle:
                handle.write("tampered\n")
            rejected = build_upload_gate_proof(item["output"], 2026, execute=True)
            self.assertFalse(rejected["valid"])
            self.assertFalse(proof_path.exists())

    def test_shared_upload_gate_proof_refuses_model_benchmark_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "output"
            audit = output / "_ocr_audit"
            audit.mkdir(parents=True)
            (audit / "model_benchmark.lock").write_text("locked", encoding="utf-8")
            proof_path = output / "_drive_upload" / "upload_gate_proof.json"
            proof_path.parent.mkdir(parents=True)
            proof_path.write_text("{}", encoding="utf-8")
            rejected = build_upload_gate_proof(output, 2026, execute=True)
            self.assertFalse(rejected["valid"])
            self.assertTrue(any("model_benchmark_lock_active" in error for error in rejected["errors"]))
            self.assertFalse(proof_path.exists())

    def test_shared_proof_rechecks_interlocks_immediately_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "output"
            proof_path = output / "_drive_upload" / "upload_gate_proof.json"
            proof_path.parent.mkdir(parents=True)
            proof_path.write_text("stale", encoding="utf-8")
            with (
                patch.object(upload_gate_module, "build_proof", return_value=({"gate_open": True}, [])),
                patch.object(
                    upload_gate_module,
                    "active_interlock_errors",
                    return_value=["model_benchmark_lock_active:racing-lock"],
                ),
            ):
                rejected = upload_gate_module.run(output, 2026, execute=True)
            self.assertFalse(rejected["valid"])
            self.assertFalse(proof_path.exists())
            self.assertIn("model_benchmark_lock_active:racing-lock", rejected["errors"])

    def test_shared_proof_removes_itself_when_any_authority_changes_during_readback(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "output"
            proof_path = output / "_drive_upload" / "upload_gate_proof.json"
            first = {"schema": upload_gate_module.SCHEMA, "gate_open": True, "pending_count": 1}
            changed = {"schema": upload_gate_module.SCHEMA, "gate_open": True, "pending_count": 2}
            with (
                patch.object(upload_gate_module, "build_proof", side_effect=[(first, []), (changed, [])]),
                patch.object(upload_gate_module, "active_interlock_errors", return_value=[]),
            ):
                rejected = upload_gate_module.run(output, 2026, execute=True)
            self.assertFalse(rejected["valid"])
            self.assertFalse(proof_path.exists())
            self.assertIn("proof_authority_snapshot_changed_during_build", rejected["errors"])


if __name__ == "__main__":
    unittest.main()

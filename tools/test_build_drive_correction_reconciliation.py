import csv
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tools import build_drive_correction_reconciliation as builder
from tools.build_drive_correction_reconciliation import build_rows, write_atomic_jsonl


class BuildDriveCorrectionLedgerTests(unittest.TestCase):
    def test_exact_content_maps_old_name_to_current_source_and_ready_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); output = root / "out"; output.mkdir()
            original = root / "original.jpg"; original.write_bytes(b"source")
            old = output / "M-202605-City-A-Store-遠景-1.jpg"; old.write_bytes(b"same")
            current = output / "M-202605-City-A-Store-單機-X-1.jpg"; current.write_bytes(b"same")
            digest = __import__("hashlib").sha256(b"same").hexdigest()
            rows, summary = build_rows(
                output_dir=output, year="2026",
                stale_rows=[{"year":"2026","file_name":old.name,"source_path":str(old),"remote_path":f"2026/{old.name}"}],
                uploaded_rows=[{"year":"2026","file_name":old.name,"drive_file_id":"old-id"}],
                manifest_rows=[{"year":"2026","file_name":current.name,"status":"ready","reasons":""}],
                current_outputs=[{"period":"202605","original_path":str(original),"target_path":str(current),"target_name":current.name,"content_sha256":digest,"sequence":"1","prefix":"m-202605-city-a-store"}],
            )
            self.assertEqual(summary["mapping_errors"], 0)
            self.assertEqual(rows[0]["status"], "new_ready")
            self.assertEqual(rows[0]["source_identity"], __import__("hashlib").sha256(str(original.resolve()).casefold().encode()).hexdigest())
            self.assertEqual(rows[0]["old_drive_file_id"], "old-id")
            self.assertEqual(rows[0]["replacement_mode"], "replace_name")

    def test_missing_old_local_uses_strict_prefix_sequence_and_preserves_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); output = root / "out"; output.mkdir()
            original = root / "original.jpg"; original.write_bytes(b"source")
            old_name = "M-202605-City-A-Store-遠景-2.jpg"
            current = output / "M-202605-City-A-Store-單機-X-2_2.jpg"; current.write_bytes(b"new")
            rows, summary = build_rows(
                output_dir=output, year="2026",
                stale_rows=[{"year":"2026","file_name":old_name,"source_path":str(output/old_name)}],
                uploaded_rows=[],
                manifest_rows=[{"year":"2026","file_name":current.name,"status":"review","reasons":"evidence_missing"}],
                current_outputs=[{"period":"202605","original_path":str(original),"target_path":str(current),"target_name":current.name,"content_sha256":"h","sequence":"2","prefix":"m-202605-city-a-store"}],
            )
            self.assertEqual(summary["mapping_errors"], 0)
            self.assertEqual(summary["missing_old_local"], 1)
            self.assertEqual(rows[0]["status"], "detected")
            self.assertEqual(rows[0]["gate_evidence"], "evidence_missing")

    def test_ambiguous_mapping_is_fail_closed_and_atomic_writer_keeps_unicode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); output = root / "out"; output.mkdir()
            old = output / "M-202605-台中市-區-店-遠景-3.jpg"; old.write_bytes(b"same")
            digest = __import__("hashlib").sha256(b"same").hexdigest()
            candidates = []
            for index in (1, 2):
                original = root / f"original-{index}.jpg"; original.write_bytes(b"source")
                current = output / f"M-202605-台中市-區-店-單機-X-3_{index}.jpg"; current.write_bytes(b"same")
                candidates.append({"period":"202605","original_path":str(original),"target_path":str(current),"target_name":current.name,"content_sha256":digest,"sequence":"3","prefix":"m-202605-台中市-區-店"})
            rows, summary = build_rows(
                output_dir=output, year="2026",
                stale_rows=[{"year":"2026","file_name":old.name,"source_path":str(old)}],
                uploaded_rows=[], manifest_rows=[], current_outputs=candidates,
            )
            self.assertEqual(summary["mapping_errors"], 1)
            self.assertEqual(rows[0]["mapping_error"], "current_source_mapping_ambiguous")
            ledger = root / "ledger.jsonl"; write_atomic_jsonl(ledger, [{"name":"台中市"}])
            self.assertIn("台中市", ledger.read_text(encoding="utf-8"))


class BuildDriveCorrectionRunGateTests(unittest.TestCase):
    @staticmethod
    def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def fixture(self, root: Path, *, manifest_status="ready", reasons="", old_id="old-id"):
        output = root / "out"
        upload = output / "_drive_upload"
        audit = output / "_ocr_audit" / "period"
        output.mkdir()
        original = root / "original.jpg"; original.write_bytes(b"source")
        old = output / "M-202605-City-A-Store-old-1.jpg"; old.write_bytes(b"same")
        current = output / "M-202605-City-A-Store-new-1.jpg"; current.write_bytes(b"same")
        self.write_csv(
            upload / "drive_upload_stale_uploaded_review_required.csv",
            [{"year":"2026","period":"202605","file_name":old.name,"source_path":str(old),"remote_path":f"2026/{old.name}"}],
            ["year","period","file_name","source_path","remote_path"],
        )
        uploaded_rows = ([{"year":"2026","file_name":old.name,"drive_file_id":old_id}] if old_id else [])
        self.write_csv(upload / "drive_upload_uploaded.csv", uploaded_rows, ["year","file_name","drive_file_id"])
        self.write_csv(
            upload / "drive_upload_all.csv",
            [{"year":"2026","file_name":current.name,"status":manifest_status,"reasons":reasons}],
            ["year","file_name","status","reasons"],
        )
        self.write_csv(
            audit / "copied.csv",
            [{"period":"202605","original_path":str(original),"target_path":str(current),"target_name":current.name}],
            ["period","original_path","target_path","target_name"],
        )
        return output

    def test_gate_blocked_ledger_is_written_for_discovery_but_not_declared_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = self.fixture(root, manifest_status="review", reasons="evidence_missing")
            ledger = root / "ledger.jsonl"
            summary = builder.run(output, "2026", ledger, execute=True)
            self.assertTrue(summary["ledger_integrity_ok"])
            self.assertTrue(summary["all_rows_accounted"])
            self.assertFalse(summary["all_replacements_gate_ready"])
            self.assertFalse(summary["safe_to_upload_new"])
            self.assertFalse(summary["safe_to_replace"])
            self.assertTrue(summary["ledger_written"])
            self.assertTrue(ledger.is_file())

    def test_upload_and_replace_safety_are_separate_until_old_ids_are_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = self.fixture(root, old_id="")
            ledger = root / "ledger.jsonl"
            summary = builder.run(output, "2026", ledger, execute=True)
            self.assertTrue(summary["ledger_integrity_ok"])
            self.assertTrue(summary["all_rows_accounted"])
            self.assertTrue(summary["all_replacements_gate_ready"])
            self.assertTrue(summary["safe_to_upload_new"])
            self.assertFalse(summary["all_old_drive_ids_resolved"])
            self.assertFalse(summary["safe_to_replace"])
            self.assertTrue(ledger.is_file())

    def test_multiple_distinct_stale_names_for_one_source_are_reconcilable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = self.fixture(root)
            upload = output / "_drive_upload"
            with (upload / "drive_upload_stale_uploaded_review_required.csv").open(encoding="utf-8-sig", newline="") as handle:
                stale = list(csv.DictReader(handle))
            duplicate_old = output / "M-202605-City-A-Store-old-copy-2.jpg"; duplicate_old.write_bytes(b"same")
            stale.append({"year":"2026","period":"202605","file_name":duplicate_old.name,"source_path":str(duplicate_old),"remote_path":f"2026/{duplicate_old.name}"})
            self.write_csv(upload / "drive_upload_stale_uploaded_review_required.csv", stale, ["year","period","file_name","source_path","remote_path"])
            uploaded = [
                {"year":"2026","file_name":stale[0]["file_name"],"drive_file_id":"old-1"},
                {"year":"2026","file_name":duplicate_old.name,"drive_file_id":"old-2"},
            ]
            self.write_csv(upload / "drive_upload_uploaded.csv", uploaded, ["year","file_name","drive_file_id"])
            ledger = root / "ledger.jsonl"
            summary = builder.run(output, "2026", ledger, execute=True)
            self.assertEqual(summary["duplicate_identities"], 0)
            self.assertEqual(summary["multi_stale_source_identities"], 1)
            self.assertTrue(summary["ledger_integrity_ok"])
            self.assertTrue(summary["safe_to_upload_new"])
            self.assertTrue(summary["safe_to_replace"])
            self.assertTrue(ledger.exists())

    def test_same_old_remote_path_twice_is_an_identity_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = self.fixture(root)
            upload = output / "_drive_upload"
            with (upload / "drive_upload_stale_uploaded_review_required.csv").open(encoding="utf-8-sig", newline="") as handle:
                stale = list(csv.DictReader(handle))
            stale.append(dict(stale[0]))
            self.write_csv(
                upload / "drive_upload_stale_uploaded_review_required.csv",
                stale,
                ["year","period","file_name","source_path","remote_path"],
            )
            ledger = root / "ledger.jsonl"
            summary = builder.run(output, "2026", ledger, execute=True)
            self.assertEqual(summary["duplicate_identities"], 1)
            self.assertEqual(summary["conflicting_source_identities"], 1)
            self.assertFalse(summary["ledger_integrity_ok"])
            self.assertFalse(summary["safe_to_replace"])
            self.assertFalse(ledger.exists())

    def test_stale_ledger_count_mismatch_is_never_safe_to_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = self.fixture(root)
            ledger = root / "ledger.jsonl"
            incomplete_summary = {
                "year":"2026", "stale_rows":1, "ledger_rows":0,
                "new_ready":0, "gate_blocked":0, "mapping_errors":0,
                "missing_old_local":0, "unique_old_drive_id":0,
                "old_drive_id_discovery_required":0, "replace_name":0,
                "verify_unchanged":0, "error_samples":[],
            }
            with patch.object(builder, "build_rows", return_value=([], incomplete_summary)):
                summary = builder.run(output, "2026", ledger, execute=False)
            self.assertFalse(summary["all_rows_accounted"])
            self.assertFalse(summary["safe_to_upload_new"])
            self.assertFalse(summary["safe_to_replace"])


if __name__ == "__main__":
    unittest.main()

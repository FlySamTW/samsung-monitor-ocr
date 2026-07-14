import tempfile
import unittest
from pathlib import Path

from build_drive_correction_reconciliation import build_rows, write_atomic_jsonl


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


if __name__ == "__main__":
    unittest.main()

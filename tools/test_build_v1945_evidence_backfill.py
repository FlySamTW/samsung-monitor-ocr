import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from skills.audit_fields import EVIDENCE_GUARD_REVISION
from tools.build_v1945_evidence_backfill import (
    BACKFILL_COMPATIBLE_GUARD_REVISIONS,
    build_candidates,
    load_bound_visual_authorities,
    run,
    stable_source_id,
)


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
                "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
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

    def test_explicit_64_and_current_compatible_trace_is_not_reprocessed(self):
        self.assertIn(EVIDENCE_GUARD_REVISION, BACKFILL_COMPATIBLE_GUARD_REVISIONS)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "compatible.jpg"
            source.write_bytes(b"compatible")
            audit = self.make_audit(root, [source])
            (audit / "v1945_evidence_trace.jsonl").write_text(
                json.dumps(
                    {
                        "trace_version": "v19.45",
                        "evidence_guard_revision": "20260721.64",
                        "source_item_id": stable_source_id(source),
                        "guard_decision": {"verified": True},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            rows, summary = build_candidates(audit, "2026", {})
            self.assertEqual(rows, [])
            self.assertEqual(summary["already_verified_year_sources"], 1)

    def test_revision_before_64_is_still_reprocessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "old-contract.jpg"
            source.write_bytes(b"old-contract")
            audit = self.make_audit(root, [source])
            (audit / "v1945_evidence_trace.jsonl").write_text(
                json.dumps(
                    {
                        "trace_version": "v19.45",
                        "evidence_guard_revision": "20260721.63",
                        "source_item_id": stable_source_id(source),
                        "guard_decision": {"verified": True},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            rows, summary = build_candidates(audit, "2026", {})
            self.assertEqual([row["file_name"] for row in rows], [source.name])
            self.assertEqual(summary["already_verified_year_sources"], 0)

    def test_revision_70_safe_rule_remains_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "safe-70.jpg"
            source.write_bytes(b"safe-70")
            audit = self.make_audit(root, [source])
            (audit / "v1945_evidence_trace.jsonl").write_text(
                json.dumps({
                    "trace_version": "v19.45",
                    "evidence_guard_revision": "20260721.70",
                    "source_item_id": stable_source_id(source),
                    "guard_decision": {"verified": True},
                    "parsed_output": {"adjudication_rule": "two_pass_single_consensus"},
                }) + "\n",
                encoding="utf-8",
            )
            rows, summary = build_candidates(audit, "2026", {})
            self.assertEqual(rows, [])
            self.assertEqual(summary["already_verified_year_sources"], 1)

    def test_revision_70_offending_geometry_rule_is_reprocessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "unsafe-70.jpg"
            source.write_bytes(b"unsafe-70")
            audit = self.make_audit(root, [source])
            (audit / "v1945_evidence_trace.jsonl").write_text(
                json.dumps({
                    "trace_version": "v19.45",
                    "evidence_guard_revision": "20260721.70",
                    "source_item_id": stable_source_id(source),
                    "guard_decision": {"verified": True},
                    "parsed_output": {
                        "adjudication_rule": "two_wide_geometry_votes_veto_single_identity_outlier"
                    },
                }) + "\n",
                encoding="utf-8",
            )
            rows, summary = build_candidates(audit, "2026", {})
            self.assertEqual([row["file_name"] for row in rows], [source.name])
            self.assertEqual(summary["already_verified_year_sources"], 0)

    def test_revision_71_verified_field_erasure_is_reprocessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "unsafe-71.jpg"
            source.write_bytes(b"unsafe-71")
            audit = self.make_audit(root, [source])
            (audit / "v1945_evidence_trace.jsonl").write_text(
                json.dumps({
                    "trace_version": "v19.45",
                    "evidence_guard_revision": "20260721.71",
                    "source_item_id": stable_source_id(source),
                    "guard_decision": {"verified": True},
                    "raw_objects": [json.dumps({"model": "S27DG502EC", "price": "8990"})],
                    "parsed_output": {
                        "three_pass_adjudicated": True,
                        "model": "S27DG502EC",
                        "price": None,
                        "adjudication_pass_summaries": [
                            {"model": "S27DG502EC", "price": "8990", "label_ownership": "matched"},
                            {"model": "S27DG502EC", "price": "8990", "label_ownership": "matched"},
                        ],
                    },
                }) + "\n",
                encoding="utf-8",
            )
            rows, summary = build_candidates(audit, "2026", {})
            self.assertEqual([row["file_name"] for row in rows], [source.name])
            self.assertEqual(summary["already_verified_year_sources"], 0)

    def test_human_audited_source_is_excluded_only_when_pixels_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "known-distant.jpg"
            source.write_bytes(b"known-distant-pixels")
            audit = self.make_audit(root, [source])
            source_id = stable_source_id(source)
            authority = {
                source_id: {
                    "source_file_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "view_type": "遠景",
                }
            }
            rows, summary = build_candidates(audit, "2026", authority)
            self.assertEqual(rows, [])
            self.assertEqual(summary["human_audited_year_sources"], 1)
            self.assertEqual(summary["conflicting_sources"], 0)

            rows, summary = build_candidates(
                audit,
                "2026",
                {source_id: {"source_file_sha256": "0" * 64, "view_type": "遠景"}},
            )
            self.assertEqual(rows, [])
            self.assertEqual(summary["human_audited_year_sources"], 0)
            self.assertEqual(summary["conflicting_sources"], 1)

    def test_bound_visual_authority_manifest_is_loaded_only_with_exact_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "reviewed.jpg"
            source.write_bytes(b"reviewed-pixels")
            audit = self.make_audit(root, [source])
            source_id = stable_source_id(source)
            manifest_dir = audit / "visual_authorities"
            manifest_dir.mkdir()
            manifest = {
                "schema": "samsung-ocr-bound-visual-authorities/v1",
                "entry_count": 1,
                "entries": [
                    {
                        "source_item_id": source_id,
                        "original_source_path": str(source),
                        "source_file_sha256": hashlib.sha256(
                            source.read_bytes()
                        ).hexdigest(),
                        "input_image_sha256": "f" * 64,
                        "view_type": "遠景",
                        "model": None,
                        "price": None,
                        "authority": "human_audited_pixel_authority",
                    }
                ],
            }
            path = manifest_dir / "reviewed.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            authorities = load_bound_visual_authorities(audit)
            self.assertIn(source_id, authorities)
            rows, summary = build_candidates(audit, "2026")
            self.assertEqual(rows, [])
            self.assertEqual(summary["human_audited_year_sources"], 1)

            manifest["entries"][0]["source_file_sha256"] = "0" * 64
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_bound_visual_authorities(audit)

    def test_old_v1945_verified_trace_without_guard_revision_is_reprocessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "old-rule.jpg"
            source.write_bytes(b"old")
            audit = self.make_audit(root, [source])
            (audit / "v1945_evidence_trace.jsonl").write_text(json.dumps({
                "trace_version": "v19.45",
                "source_item_id": stable_source_id(source),
                "guard_decision": {"verified": True},
            }) + "\n", encoding="utf-8")
            output = audit / "backfill.csv"
            summary = run(audit, "2026", output, execute=True)
            with output.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(summary["already_verified_year_sources"], 0)
            self.assertEqual([row["file_name"] for row in rows], [source.name])

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

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
    load_current_upload_queue_source_ids,
    load_current_upload_receipt_source_ids,
    load_verified_source_ids,
    run,
    stable_source_id,
    verified_row_conflicts_with_known_authority,
)


class EvidenceBackfillBuilderTests(unittest.TestCase):
    def test_superseded_234_verified_row_is_not_accepted_as_current(self):
        source_id = "e6b85baa98ec3589edbf79f6388239f92e4fa5e3b8b91c26b482f81a21c73ce9"
        image_hash = "f8a38f32e21f0e01c3047e64f70c4b37008d382beacd3e1ecf188a0415423e8d"
        stale = {
            "source_item_id": source_id,
            "parsed_output": {
                "input_image_sha256": image_hash,
                "view_type": "單機",
                "complete_screen_count": 1,
                "model": None,
                "price": None,
            },
        }
        corrected = {
            "source_item_id": source_id,
            "parsed_output": {
                "input_image_sha256": image_hash,
                "view_type": "遠景",
                "complete_screen_count": 0,
                "model": None,
                "price": None,
            },
        }
        self.assertTrue(verified_row_conflicts_with_known_authority(stale))
        self.assertFalse(verified_row_conflicts_with_known_authority(corrected))

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

    def test_current_drive_receipt_is_terminal_and_changed_source_is_reprocessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "uploaded.jpg"
            source.write_bytes(b"original")
            audit = self.make_audit(root, [source])
            source_id = stable_source_id(source)
            receipts = root / "_drive_upload_stream" / "receipts"
            receipts.mkdir(parents=True)
            receipt = {
                "schema": "samsung-ocr-stream-receipt-v1",
                "source_item_id": source_id,
                "original_source_path": str(source.resolve()),
                "source_sha256": hashlib.sha256(b"original").hexdigest(),
                "period": "202605",
                "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
                "confirmed_at": "2999-01-01T00:00:00",
            }
            (receipts / f"{source_id}.json").write_text(
                json.dumps(receipt), encoding="utf-8"
            )

            verified, invalid = load_current_upload_receipt_source_ids(audit)
            self.assertEqual(verified, {source_id})
            self.assertEqual(invalid, [])
            rows, summary = build_candidates(audit, "2026", {})
            self.assertEqual(rows, [])
            self.assertEqual(summary["current_upload_receipt_source_ids"], 1)

            receipt["confirmed_at"] = "2000-01-01T00:00:00"
            source.write_bytes(b"changed")
            (receipts / f"{source_id}.json").write_text(
                json.dumps(receipt), encoding="utf-8"
            )
            verified, invalid = load_current_upload_receipt_source_ids(audit)
            self.assertEqual(verified, set())
            self.assertEqual(len(invalid), 1)
            rows, summary = build_candidates(audit, "2026", {})
            self.assertEqual([row["file_name"] for row in rows], [source.name])

    def test_old_drive_receipt_revision_does_not_hide_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "old-revision.jpg"
            source.write_bytes(b"old")
            audit = self.make_audit(root, [source])
            source_id = stable_source_id(source)
            receipts = root / "_drive_upload_stream" / "receipts"
            receipts.mkdir(parents=True)
            (receipts / f"{source_id}.json").write_text(
                json.dumps(
                    {
                        "schema": "samsung-ocr-stream-receipt-v1",
                        "source_item_id": source_id,
                        "original_source_path": str(source.resolve()),
                        "source_sha256": hashlib.sha256(b"old").hexdigest(),
                        "period": "202605",
                        "evidence_guard_revision": "20260731.89",
                        "confirmed_at": "2999-01-01T00:00:00",
                    }
                ),
                encoding="utf-8",
            )
            rows, summary = build_candidates(audit, "2026", {})
            self.assertEqual([row["file_name"] for row in rows], [source.name])
            self.assertEqual(summary["current_upload_receipt_source_ids"], 0)

    def test_current_durable_upload_queue_is_terminal_until_upload_finishes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "queued.jpg"
            source.write_bytes(b"queued-source")
            audit = self.make_audit(root, [source])
            source_id = stable_source_id(source)
            pending = root / "_drive_upload_stream" / "pending"
            pending.mkdir(parents=True)
            job = {
                "schema": "samsung-ocr-stream-upload-v1",
                "source_item_id": source_id,
                "original_source_path": str(source.resolve()),
                "source_sha256": hashlib.sha256(b"queued-source").hexdigest(),
                "input_image_sha256": hashlib.sha256(b"normalized-image").hexdigest(),
                "period": "202605",
                "target_name": "M-202605-queued-遠景.jpg",
                "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
                "final_result": {
                    "view_type": "遠景",
                    "category": "遠景",
                    "model": None,
                    "price": None,
                    "complete_screen_count": 3,
                    "unique_main": False,
                    "label_ownership": "not_visible",
                    "followme_physical_evidence": [],
                    "three_pass_adjudicated": True,
                },
            }
            job_path = pending / f"{source_id}.json"
            job_path.write_text(json.dumps(job), encoding="utf-8")

            queued, invalid = load_current_upload_queue_source_ids(audit)
            self.assertEqual(queued, {source_id})
            self.assertEqual(invalid, [])
            rows, summary = build_candidates(audit, "2026", {})
            self.assertEqual(rows, [])
            self.assertEqual(summary["current_upload_queue_source_ids"], 1)

            job["source_sha256"] = "0" * 64
            job_path.write_text(json.dumps(job), encoding="utf-8")
            queued, invalid = load_current_upload_queue_source_ids(audit)
            self.assertEqual(queued, set())
            self.assertEqual(len(invalid), 1)
            rows, summary = build_candidates(audit, "2026", {})
            self.assertEqual([row["file_name"] for row in rows], [source.name])

    def test_current_and_scoped_safe_86_guard_revisions_are_compatible(self):
        self.assertEqual(
            BACKFILL_COMPATIBLE_GUARD_REVISIONS,
            frozenset(
                {
                    EVIDENCE_GUARD_REVISION,
                    "20260730.86",
                    "20260731.87",
                    "20260731.88",
                    "20260731.89",
                }
            ),
        )
        self.assertEqual(EVIDENCE_GUARD_REVISION, "20260807.96")
        self.assertNotIn("20260726.82", BACKFILL_COMPATIBLE_GUARD_REVISIONS)
        self.assertNotIn("20260726.81", BACKFILL_COMPATIBLE_GUARD_REVISIONS)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "compatible.jpg"
            source.write_bytes(b"compatible")
            audit = self.make_audit(root, [source])
            (audit / "v1945_evidence_trace.jsonl").write_text(
                json.dumps(
                    {
                        "trace_version": "v19.45",
                        "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
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

    def test_revision_86_generic_m7_followme_early_exit_is_reprocessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "unsafe-m7.jpg"
            source.write_bytes(b"unsafe-m7")
            audit = self.make_audit(root, [source])
            parsed = {
                "view_type": "單機",
                "model": "FollowMe 型號未細分",
                "price": "9990",
                "ordered_followme_early_exit": True,
                "followme_family_confirmed": True,
                "thinking": "同一台 Samsung Smart Monitor M7 商品卡，白色支架與圓形底座。",
                "complete_screen_count": 2,
                "unique_main": True,
                "label_ownership": "matched",
                "followme_physical_evidence": [
                    {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
                    {"cue": "round_base", "same_subject": True, "strength": "strong"},
                    {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
                ],
            }
            (audit / "v1945_evidence_trace.jsonl").write_text(
                json.dumps(
                    {
                        "trace_version": "v19.45",
                        "evidence_guard_revision": "20260730.86",
                        "source_item_id": stable_source_id(source),
                        "parsed_output": parsed,
                        "guard_decision": {"verified": True},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            rows, summary = build_candidates(audit, "2026", {})
            self.assertEqual([row["file_name"] for row in rows], [source.name])
            self.assertEqual(summary["already_verified_year_sources"], 0)

    def test_revision_81_is_reprocessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "unsafe-81.jpg"
            source.write_bytes(b"unsafe-81")
            audit = self.make_audit(root, [source])
            (audit / "v1945_evidence_trace.jsonl").write_text(
                json.dumps(
                    {
                        "trace_version": "v19.45",
                        "evidence_guard_revision": "20260726.81",
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
    def test_revision_82_is_reprocessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "unsafe-82.jpg"
            source.write_bytes(b"unsafe-82")
            audit = self.make_audit(root, [source])
            (audit / "v1945_evidence_trace.jsonl").write_text(
                json.dumps(
                    {
                        "trace_version": "v19.45",
                        "evidence_guard_revision": "20260726.82",
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

    def test_revision_70_safe_rule_is_reprocessed_after_rev82_content_change(self):
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
                }) + "\n",
                encoding="utf-8",
            )
            rows, summary = build_candidates(audit, "2026", {})
            self.assertEqual([row["file_name"] for row in rows], [source.name])
            self.assertEqual(summary["already_verified_year_sources"], 0)
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

    def test_compatible_verified_cross_field_contradiction_is_reprocessed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "stale-quality.jpg"
            source.write_bytes(b"stale-quality")
            audit = root / "audit"
            audit.mkdir()
            trace = audit / "v1945_evidence_trace.jsonl"
            trace.write_text(
                json.dumps(
                    {
                        "trace_version": "v19.45",
                        "evidence_guard_revision": "20260723.75",
                        "source_item_id": stable_source_id(source),
                        "guard_decision": {"verified": True},
                        "parsed_output": {
                            "view_type": "單機",
                            "model": "S32DG702EC",
                            "price": "14900",
                            "quality_issue": "不合格-沒有價格牌",
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertNotIn(stable_source_id(source), load_verified_source_ids(audit))

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
            self.assertEqual(summary["terminal_authorized_year_sources"], 1)
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

import json
import tempfile
import unittest
from pathlib import Path

from skills.audit_fields import EVIDENCE_GUARD_REVISION
from tools.clear_lifetime_cap_fuse_after_recovery import clear_after_recovery


class LifetimeCapFuseClearanceTests(unittest.TestCase):
    def test_exact_recovered_photo_clears_without_authorizing_call_four(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit = root / "_ocr_audit"
            audit.mkdir()
            source_id = "a" * 64
            name = "photo.jpg"
            result = root / "result.json"
            result.write_text(
                json.dumps(
                    [
                        {
                            "data": {
                                "image": f"/data/upload/1/{name}",
                                "ocr_meta": {
                                    "auto_verified": True,
                                    "auto_review_required": False,
                                    "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
                                    "ocr_attempt": 3,
                                    "three_pass_adjudicated": True,
                                    "adjudication_rule": (
                                        "three_bound_cross_run_raw_structured_single_consensus"
                                    ),
                                },
                            }
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (audit / "runtime_health_fuse.json").write_text(
                json.dumps(
                    {
                        "active": True,
                        "source_file": name,
                        "reasons": ["lifetime_model_call_ledger_blocked"],
                    }
                ),
                encoding="utf-8",
            )
            pending = root / "_drive_upload_stream" / "pending" / f"{source_id}.json"
            pending.parent.mkdir(parents=True)
            pending.write_text("{}", encoding="utf-8")
            recovery = audit / "consumed_cap_missing_result_recovery" / "one.json"
            recovery.parent.mkdir()
            recovery.write_text(
                json.dumps(
                    {
                        "status": "recovered",
                        "file_name": name,
                        "source_item_id": source_id,
                        "fourth_call_made": False,
                        "adjudication_rule": (
                            "three_bound_cross_run_raw_structured_single_consensus"
                        ),
                        "result_file": str(result),
                        "queued_job": str(pending),
                    }
                ),
                encoding="utf-8",
            )
            dry = clear_after_recovery(
                audit_dir=audit,
                result_file=result,
                file_name=name,
                source_item_id=source_id,
                apply=False,
            )
            self.assertEqual(dry["status"], "would_clear")
            self.assertFalse(dry["fourth_call_authorized"])
            applied = clear_after_recovery(
                audit_dir=audit,
                result_file=result,
                file_name=name,
                source_item_id=source_id,
                apply=True,
            )
            self.assertEqual(applied["status"], "cleared")
            self.assertFalse((audit / "runtime_health_fuse.json").exists())
            self.assertTrue(Path(applied["clearance_receipt"]).is_file())
            self.assertTrue(Path(applied["fuse_history"]).is_file())

    def test_unrelated_fuse_is_never_cleared(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp) / "_ocr_audit"
            audit.mkdir()
            (audit / "runtime_health_fuse.json").write_text(
                json.dumps(
                    {
                        "active": True,
                        "source_file": "other.jpg",
                        "reasons": ["request_id_mismatch"],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "exact recovered"):
                clear_after_recovery(
                    audit_dir=audit,
                    result_file=Path(tmp) / "missing.json",
                    file_name="photo.jpg",
                    source_item_id="a" * 64,
                    apply=True,
                )


if __name__ == "__main__":
    unittest.main()

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from skills.model_call_ledger import LEDGER_SCHEMA, build_source_image_binding
from tools.migrate_lifetime_cap_fuse_to_deferred import (
    FUSE_REASON,
    FUSE_SCHEMA,
    QUEUE_SCHEMA,
    _prepared_input_sha256,
    migrate_lifetime_cap_fuse,
)


class LifetimeCapFuseMigrationTests(unittest.TestCase):
    def _fixture(
        self,
        root: Path,
        *,
        consumed_calls: int = 3,
        verified: bool = False,
        reason: str = FUSE_REASON,
    ) -> tuple[Path, Path, Path, Path, str, str]:
        audit = root / "audit"
        staging = root / "staging"
        original_dir = root / "original"
        for directory in (audit, staging, original_dir):
            directory.mkdir()

        name = "photo-671.jpg"
        staged = staging / name
        original = original_dir / name
        Image.new("RGB", (80, 60), (30, 90, 140)).save(staged, quality=95)
        original.write_bytes(staged.read_bytes())
        source_id = hashlib.sha256(b"stable-source-671").hexdigest()
        (staging / ".ocr_source_map.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "items": {
                        name: {
                            "source_item_id": source_id,
                            "original_source_path": str(original),
                            "period": "202601",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        input_hash = _prepared_input_sha256(staged)
        binding = build_source_image_binding(
            source_item_id=source_id,
            original_source_path=original,
            input_image_sha256=input_hash,
        )
        ledger = (
            audit
            / "model_call_lifetime_ledger_v1"
            / source_id[:2]
            / f"{source_id}.json"
        )
        ledger.parent.mkdir(parents=True)
        ledger.write_text(
            json.dumps(
                {
                    "schema": LEDGER_SCHEMA,
                    **binding.as_dict(),
                    "max_calls": 3,
                    "reserved_calls": consumed_calls,
                    "reservations": [],
                }
            ),
            encoding="utf-8",
        )
        (audit / "runtime_health_fuse.json").write_text(
            json.dumps(
                {
                    "schema": FUSE_SCHEMA,
                    "active": True,
                    "reasons": [reason],
                    "source_file": name,
                    "run_id": "run-671",
                    "attempt": 1,
                    "tripped_at": "2026-07-28T17:45:44+08:00",
                }
            ),
            encoding="utf-8",
        )
        result = staging / "batch-OCR成功.json"
        rows = []
        if verified:
            rows.append(
                {
                    "data": {
                        "image": f"/data/upload/1/{name}",
                        "ocr_meta": {
                            "auto_verified": True,
                            "auto_review_required": False,
                        },
                    }
                }
            )
        result.write_text(json.dumps(rows), encoding="utf-8")
        return audit, staging, ledger, result, name, source_id

    def test_dry_run_writes_nothing_and_apply_only_migrates_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audit, staging, ledger, result, name, source_id = self._fixture(root)
            ledger_before = ledger.read_bytes()
            result_before = result.read_bytes()

            dry = migrate_lifetime_cap_fuse(
                audit_dir=audit,
                staging_dir=staging,
                apply=False,
            )
            self.assertEqual(dry["status"], "would_defer")
            self.assertFalse(dry["fourth_call_authorized"])
            self.assertTrue((audit / "runtime_health_fuse.json").is_file())
            self.assertFalse(
                (staging / ".ocr_capped_adjudication_queue.json").exists()
            )
            self.assertEqual(ledger.read_bytes(), ledger_before)
            self.assertEqual(result.read_bytes(), result_before)

            applied = migrate_lifetime_cap_fuse(
                audit_dir=audit,
                staging_dir=staging,
                apply=True,
            )
            self.assertEqual(applied["status"], "deferred")
            self.assertFalse((audit / "runtime_health_fuse.json").exists())
            queue = json.loads(
                (staging / ".ocr_capped_adjudication_queue.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(queue["schema"], QUEUE_SCHEMA)
            self.assertEqual(len(queue["items"]), 1)
            item = queue["items"][0]
            self.assertEqual(item["file_name"], name)
            self.assertEqual(item["source_item_id"], source_id)
            self.assertEqual(item["consumed_calls"], 3)
            self.assertEqual(item["state"], "awaiting_zero_model_adjudication")
            self.assertFalse(item["verified"])
            self.assertFalse(item["uploaded"])
            self.assertTrue(Path(applied["clearance_receipt"]).is_file())
            self.assertTrue(Path(applied["fuse_history"]).is_file())
            self.assertEqual(ledger.read_bytes(), ledger_before)
            self.assertEqual(result.read_bytes(), result_before)
            self.assertFalse((root / "_drive_upload_stream").exists())

    def test_wrong_fuse_reason_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            audit, staging, *_ = self._fixture(
                Path(temp), reason="request_id_mismatch"
            )
            with self.assertRaisesRegex(RuntimeError, "exhausted photo-local"):
                migrate_lifetime_cap_fuse(
                    audit_dir=audit,
                    staging_dir=staging,
                    apply=True,
                )

    def test_exhausted_structured_narration_failure_is_deferred_without_call_four(self):
        with tempfile.TemporaryDirectory() as temp:
            audit, staging, *_ = self._fixture(
                Path(temp), reason="structured_narration_invalid"
            )
            report = migrate_lifetime_cap_fuse(
                audit_dir=audit,
                staging_dir=staging,
                apply=True,
            )
            self.assertEqual(report["status"], "deferred")
            self.assertFalse(report["fourth_call_authorized"])
            queue = json.loads(
                (staging / ".ocr_capped_adjudication_queue.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(queue["items"][0]["error"], "structured_narration_invalid")

    def test_exhausted_model_conflict_is_deferred_without_call_four(self):
        with tempfile.TemporaryDirectory() as temp:
            audit_dir, staging_dir, *_ = self._fixture(
                Path(temp), reason="structured_authority_material_conflict:model"
            )
            report = migrate_lifetime_cap_fuse(
                audit_dir=audit_dir,
                staging_dir=staging_dir,
                apply=True,
            )
            self.assertEqual(report["status"], "deferred")
            self.assertFalse(report["fourth_call_authorized"])
            queue = json.loads(
                (staging_dir / ".ocr_capped_adjudication_queue.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                queue["items"][0]["error"],
                "structured_authority_material_conflict:model",
            )

    def test_unconsumed_ledger_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            audit, staging, *_ = self._fixture(Path(temp), consumed_calls=2)
            with self.assertRaisesRegex(RuntimeError, "not consumed three"):
                migrate_lifetime_cap_fuse(
                    audit_dir=audit,
                    staging_dir=staging,
                    apply=True,
                )

    def test_verified_result_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            audit, staging, *_ = self._fixture(Path(temp), verified=True)
            with self.assertRaisesRegex(RuntimeError, "already has a verified"):
                migrate_lifetime_cap_fuse(
                    audit_dir=audit,
                    staging_dir=staging,
                    apply=True,
                )

    def test_input_hash_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            audit, staging, ledger, *_ = self._fixture(Path(temp))
            payload = json.loads(ledger.read_text(encoding="utf-8"))
            payload["input_image_sha256"] = "f" * 64
            ledger.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "input_image_sha256"):
                migrate_lifetime_cap_fuse(
                    audit_dir=audit,
                    staging_dir=staging,
                    apply=True,
                )

    def test_existing_queue_binding_collision_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            audit, staging, *_ = self._fixture(Path(temp))
            (staging / ".ocr_capped_adjudication_queue.json").write_text(
                json.dumps(
                    {
                        "schema": QUEUE_SCHEMA,
                        "image_dir": str(staging.resolve()),
                        "items": [
                            {
                                "file_name": "photo-671.jpg",
                                "source_item_id": "b" * 64,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "binding collision"):
                migrate_lifetime_cap_fuse(
                    audit_dir=audit,
                    staging_dir=staging,
                    apply=True,
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)

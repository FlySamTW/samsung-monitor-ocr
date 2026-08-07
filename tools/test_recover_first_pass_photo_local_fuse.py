from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.recover_first_pass_photo_local_fuse import RECOVERY_RULE, recover


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class RecoverFirstPassPhotoLocalFuseTests(unittest.TestCase):
    """Exercise the narrow model-only recovery without a live image processor."""

    file_name = "photo.jpg"
    source_id = "a" * 64
    raw_model = "S27CG552EC"

    def _fixture(self, root: Path, *, reasons: list[str] | None = None):
        staging = root / "staging"
        audit = root / "audit"
        staging.mkdir()
        audit.mkdir()
        staged = staging / self.file_name
        staged.write_bytes(b"original staged photo")
        input_hash = hashlib.sha256(staged.read_bytes()).hexdigest()
        source = root / "original.jpg"
        source.write_bytes(b"source provenance")
        _write(
            staging / ".ocr_source_map.json",
            {
                "items": {
                    self.file_name: {
                        "source_item_id": self.source_id,
                        "original_source_path": str(source),
                        "period": "202607",
                    }
                }
            },
        )
        _write(
            staging / ".ocr_retry_queue.json",
            {
                "image_dir": str(staging),
                "retry_queue": ["later.jpg", self.file_name],
                "auto_attempts": {self.file_name: 1},
                "auto_result_history": {},
            },
        )
        snapshot = {
            "model": self.raw_model,
            "price": "4990",
            "view_type": "單機",
            "complete_screen_count": 1,
            "unique_main": True,
            "label_ownership": "matched",
            "structured_authority_blocked_fields": ["model"],
            "request_binding_enforced": True,
            "request_id_verified": True,
            "independent_pass": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
            "input_image_sha256": input_hash,
            "source_item_id": self.source_id,
            "raw_model_output": json.dumps(
                {
                    "request_id": "b" * 32,
                    "data": {
                        "model": self.raw_model,
                        "price": "4990",
                        "view_type": "單機",
                    },
                }
            ),
        }
        fuse = audit / "runtime_health_fuse.json"
        _write(
            fuse,
            {
                "schema": "samsung-ocr-runtime-health-fuse/v1",
                "active": True,
                "reasons": reasons
                or ["structured_authority_material_conflict:model"],
                "source_file": self.file_name,
                "attempt": 1,
                "run_id": "formal-run",
                "record_snapshot": snapshot,
            },
        )
        return staging, fuse, staged

    @staticmethod
    def _prepared_hash(path: Path, attempt: int = 1) -> str:
        del attempt
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _recover(self, *, staging: Path, fuse: Path, apply: bool) -> dict:
        # The helper's image preparation is integration-tested elsewhere.  This
        # unit test binds its exact staged bytes while isolating recovery state.
        with (
            patch(
                "tools.recover_first_pass_photo_local_fuse._prepared_input_sha256",
                side_effect=self._prepared_hash,
            ),
            patch(
                "tools.recover_first_pass_photo_local_fuse.validate_evidence_contract",
                return_value=(True, [], {"fixture": "normalized"}),
            ),
        ):
            return recover(staging_dir=staging, fuse_file=fuse, apply=apply)

    def test_exact_model_only_conflict_supports_dry_run_then_apply(self):
        with tempfile.TemporaryDirectory() as temp:
            staging, fuse, _staged = self._fixture(Path(temp))

            dry_run = self._recover(staging=staging, fuse=fuse, apply=False)
            self.assertEqual(dry_run["status"], "would_recover")
            self.assertEqual(dry_run["recovery"], RECOVERY_RULE)
            self.assertEqual(dry_run["suppressed_raw_model"], self.raw_model)
            self.assertTrue(fuse.exists())
            state = json.loads(
                (staging / ".ocr_retry_queue.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["auto_result_history"], {})

            applied = self._recover(staging=staging, fuse=fuse, apply=True)
            state = json.loads(
                (staging / ".ocr_retry_queue.json").read_text(encoding="utf-8")
            )
            history = state["auto_result_history"][self.file_name]
            self.assertEqual(applied["status"], "recovered")
            self.assertEqual(state["retry_queue"], [self.file_name, "later.jpg"])
            self.assertEqual(len(history), 1)
            self.assertIsNone(history[0]["model"])
            self.assertEqual(history[0]["raw_structured_model"], self.raw_model)
            self.assertIn(
                "model:structured_authority_material_conflict",
                history[0]["field_suppression_reasons"],
            )
            self.assertFalse(fuse.exists())
            self.assertTrue(Path(applied["receipt"]).is_file())
            self.assertTrue(Path(applied["archived_fuse"]).is_file())

    def test_changed_staged_bytes_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            staging, fuse, staged = self._fixture(Path(temp))
            staged.write_bytes(b"changed staged photo")
            with self.assertRaisesRegex(RuntimeError, "bytes do not match"):
                self._recover(staging=staging, fuse=fuse, apply=False)
            self.assertTrue(fuse.exists())

    def test_extra_price_conflict_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            staging, fuse, _staged = self._fixture(
                Path(temp),
                reasons=[
                    "structured_authority_material_conflict:model",
                    "structured_authority_material_conflict:price",
                ],
            )
            with self.assertRaisesRegex(RuntimeError, "not a currently containable"):
                self._recover(staging=staging, fuse=fuse, apply=False)
            self.assertTrue(fuse.exists())


if __name__ == "__main__":
    unittest.main()

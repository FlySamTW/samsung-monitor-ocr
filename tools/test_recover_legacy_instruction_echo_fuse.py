from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import recover_legacy_instruction_echo_fuse as recovery


class LegacyInstructionEchoRecoveryTests(unittest.TestCase):
    def _fixture(self, root: Path, *, attempt: int = 2) -> tuple[Path, Path, Path, str]:
        staging = root / "staging"
        audit = root / "audit"
        source_dir = root / "source"
        staging.mkdir()
        audit.mkdir()
        source_dir.mkdir()

        file_name = "M-test-echo.jpg"
        source_item_id = "a" * 64
        image_hash = "b" * 64
        request_id = "c" * 32
        original = source_dir / file_name
        staged = staging / file_name
        original.write_bytes(b"source")
        staged.write_bytes(b"source")

        earlier = {
            "request_id_verified": True,
            "request_binding_enforced": True,
            "independent_pass": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
            "source_item_id": source_item_id,
            "input_image_sha256": image_hash,
        }
        retry = {
            "image_dir": str(staging),
            "auto_attempts": {file_name: attempt},
            "auto_result_history": {
                file_name: [earlier] if attempt == 2 else [],
            },
            "retry_queue": ["other.jpg", file_name],
            "priority_queue": [],
        }
        retry_file = staging / ".ocr_retry_queue.json"
        retry_file.write_text(json.dumps(retry), encoding="utf-8")
        (staging / ".ocr_source_map.json").write_text(
            json.dumps(
                {
                    "items": {
                        file_name: {
                            "source_item_id": source_item_id,
                            "original_source_path": str(original),
                            "period": "202602",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        echo = (
            "我看到遠景。完整台數只能看第一張全尺寸照片並只計一次；"
            "自然敘述不得抄寫這些規則。"
        )
        raw = {
            "request_id": request_id,
            "narration": echo,
            "view_type": "遠景",
            "model": None,
            "price": None,
        }
        fuse_file = audit / "runtime_health_fuse.json"
        fuse_file.write_text(
            json.dumps(
                {
                    "schema": recovery.RUNTIME_HEALTH_FUSE_SCHEMA,
                    "active": True,
                    "reasons": [recovery.FUSE_REASON],
                    "source_file": file_name,
                    "attempt": attempt,
                    "run_id": "20260724_170000_123456",
                    "record_snapshot": {
                        "request_id_verified": True,
                        "request_binding_enforced": True,
                        "independent_pass": True,
                        "prior_answer_exposed": False,
                        "prompt_contamination": False,
                        "source_item_id": source_item_id,
                        "input_image_sha256": image_hash,
                        "narration": echo,
                        "raw_model_output": json.dumps(raw, ensure_ascii=False),
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return staging, fuse_file, retry_file, file_name

    def test_apply_preserves_attempt_requeues_first_and_archives_before_clear(self):
        with tempfile.TemporaryDirectory() as temp:
            staging, fuse_file, retry_file, file_name = self._fixture(
                Path(temp), attempt=2
            )

            report = recovery.recover(
                staging_dir=staging,
                fuse_file=fuse_file,
                apply=True,
            )

            durable = json.loads(retry_file.read_text(encoding="utf-8"))
            self.assertEqual(durable["auto_attempts"][file_name], 2)
            self.assertEqual(len(durable["auto_result_history"][file_name]), 1)
            self.assertEqual(durable["retry_queue"], [file_name, "other.jpg"])
            self.assertFalse(fuse_file.exists())
            self.assertTrue(Path(report["receipt"]).is_file())
            self.assertTrue(Path(report["archived_fuse"]).is_file())
            self.assertEqual(report["consumed_attempt_before"], 2)
            self.assertEqual(report["consumed_attempt_after"], 2)
            self.assertTrue(report["discarded_output"])
            self.assertFalse(report["model_called"])
            self.assertFalse(report["result_written"])
            self.assertFalse(report["upload_enqueued"])

    def test_dry_run_does_not_mutate_or_clear_attempt_one(self):
        with tempfile.TemporaryDirectory() as temp:
            staging, fuse_file, retry_file, file_name = self._fixture(
                Path(temp), attempt=1
            )
            before_retry = retry_file.read_bytes()
            before_fuse = fuse_file.read_bytes()

            report = recovery.recover(
                staging_dir=staging,
                fuse_file=fuse_file,
                apply=False,
            )

            self.assertEqual(report["status"], "would_recover")
            self.assertEqual(report["consumed_attempt_after"], 1)
            self.assertEqual(retry_file.read_bytes(), before_retry)
            self.assertEqual(fuse_file.read_bytes(), before_fuse)
            self.assertFalse(
                (fuse_file.parent / "runtime_health_fuse_clearance").exists()
            )

    def test_archive_failure_keeps_active_fuse_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            staging, fuse_file, _retry_file, _file_name = self._fixture(
                Path(temp), attempt=1
            )
            real_atomic = recovery._atomic_json

            def fail_archive(path: Path, value: dict) -> None:
                if path.parent.name == "runtime_health_fuse_history":
                    raise OSError("simulated archive failure")
                real_atomic(path, value)

            with patch.object(recovery, "_atomic_json", side_effect=fail_archive):
                with self.assertRaisesRegex(OSError, "simulated archive failure"):
                    recovery.recover(
                        staging_dir=staging,
                        fuse_file=fuse_file,
                        apply=True,
                    )
            self.assertTrue(fuse_file.is_file())
            self.assertTrue(json.loads(fuse_file.read_text(encoding="utf-8"))["active"])

    def test_rejects_narration_without_actual_echo(self):
        with tempfile.TemporaryDirectory() as temp:
            staging, fuse_file, _retry_file, _file_name = self._fixture(
                Path(temp), attempt=1
            )
            fuse = json.loads(fuse_file.read_text(encoding="utf-8"))
            raw = json.loads(fuse["record_snapshot"]["raw_model_output"])
            raw["narration"] = "我看到六台完整螢幕，因此判定為遠景。"
            fuse["record_snapshot"]["raw_model_output"] = json.dumps(
                raw, ensure_ascii=False
            )
            fuse_file.write_text(json.dumps(fuse, ensure_ascii=False), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "actual instruction echo"):
                recovery.recover(
                    staging_dir=staging,
                    fuse_file=fuse_file,
                    apply=False,
                )


if __name__ == "__main__":
    unittest.main()

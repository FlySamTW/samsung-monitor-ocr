from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from tools.recover_contained_request_binding_fuse import recover


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class ContainedRequestBindingRecoveryTests(unittest.TestCase):
    def _fixture(self, root: Path, *, reason: str = "request_binding_unverified"):
        staging = root / "staging"
        staging.mkdir()
        file_name = "photo.jpg"
        staged_bytes = b"staged"
        (staging / file_name).write_bytes(staged_bytes)
        source = root / "source.jpg"
        source.write_bytes(b"source")
        source_id = "a" * 64
        _write(
            staging / ".ocr_source_map.json",
            {
                "items": {
                    file_name: {
                        "source_item_id": source_id,
                        "original_source_path": str(source),
                    }
                }
            },
        )
        _write(
            staging / ".ocr_retry_queue.json",
            {
                "image_dir": str(staging),
                "retry_queue": [],
                "priority_queue": [],
                "auto_attempts": {file_name: 1},
                "auto_result_history": {},
                "runtime_health_incident_sources": {
                    reason: [file_name],
                },
            },
        )
        fuse = root / "audit" / "runtime_health_fuse.json"
        _write(
            fuse,
            {
                "active": True,
                "reasons": [reason],
                "source_file": file_name,
                "attempt": 1,
                "record_snapshot": {
                    "prior_answer_exposed": False,
                    "prompt_contamination": False,
                    "request_binding_expected": "a" * 32,
                    "request_binding_actual": "b" * 32,
                    "input_image_sha256": hashlib.sha256(staged_bytes).hexdigest(),
                },
            },
        )
        return staging, fuse, file_name

    def test_preserves_consumed_call_and_requeues_same_photo(self):
        with tempfile.TemporaryDirectory() as temp:
            staging, fuse, file_name = self._fixture(Path(temp))
            report = recover(staging_dir=staging, fuse_file=fuse, apply=True)
            state = json.loads(
                (staging / ".ocr_retry_queue.json").read_text(encoding="utf-8")
            )
            self.assertEqual(report["consumed_calls"], 1)
            self.assertEqual(report["remaining_calls"], 2)
            self.assertFalse(report["fourth_call_allowed"])
            self.assertEqual(state["auto_attempts"][file_name], 1)
            self.assertEqual(state["retry_queue"], [file_name])
            self.assertFalse(fuse.exists())
            self.assertTrue(Path(report["receipt"]).is_file())
            self.assertTrue(Path(report["fuse_history"]).is_file())

    def test_legacy_sparse_explicit_mismatch_is_requeued_but_never_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            staging, fuse, file_name = self._fixture(
                Path(temp), reason="request_id_mismatch"
            )
            report = recover(staging_dir=staging, fuse_file=fuse, apply=True)
            state = json.loads(
                (staging / ".ocr_retry_queue.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["auto_attempts"][file_name], 1)
            self.assertEqual(state["auto_result_history"], {})
            self.assertEqual(state["retry_queue"], [file_name])
            self.assertEqual(
                report["discarded_request_binding_fault"], ["request_id_mismatch"]
            )
            self.assertFalse(fuse.exists())

    def test_three_source_explicit_mismatch_remains_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            staging, fuse, file_name = self._fixture(
                Path(temp), reason="request_id_mismatch"
            )
            state_path = staging / ".ocr_retry_queue.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["runtime_health_incident_sources"]["request_id_mismatch"] = [
                file_name,
                "second.jpg",
                "third.jpg",
            ]
            _write(state_path, state)
            with self.assertRaisesRegex(RuntimeError, "not proven sparse"):
                recover(staging_dir=staging, fuse_file=fuse, apply=True)
            self.assertTrue(fuse.exists())


if __name__ == "__main__":
    unittest.main()

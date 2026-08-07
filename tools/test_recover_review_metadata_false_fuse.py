import json
import tempfile
import unittest
from pathlib import Path

from tools.recover_review_metadata_false_fuse import RECOVERY_RULE, recover


class RecoverReviewMetadataFalseFuseTests(unittest.TestCase):
    def _call(self, attempt: int) -> dict:
        return {
            "file_name": "sample-639.jpg",
            "source_item_id": "a" * 64,
            "run_id": "formal-run",
            "period": "202606",
            "ocr_attempt": attempt,
            "input_image_sha256": "b" * 64,
            "request_id_verified": True,
            "independent_pass": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
            "runtime_health": {"healthy": True, "reasons": []},
            "view_type": "單機",
            "model": None,
            "price": "4990",
        }

    def test_exact_pre_inference_false_fuse_rolls_back_only_attempt_counter(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            staging = root / "staging"
            audit = root / "audit"
            staging.mkdir()
            audit.mkdir()
            retry = {
                "auto_attempts": {"sample-639.jpg": 3},
                "auto_result_history": {
                    "sample-639.jpg": [self._call(1), self._call(2)]
                },
            }
            (staging / ".ocr_retry_queue.json").write_text(
                json.dumps(retry), encoding="utf-8"
            )
            trace = audit / "trace.jsonl"
            trace.write_text(
                "\n".join(
                    json.dumps({
                        "file_name": "sample-639.jpg",
                        "run_id": "formal-run",
                        "parsed_output": self._call(attempt),
                    })
                    for attempt in (1, 2)
                )
                + "\n",
                encoding="utf-8",
            )
            fuse = audit / "runtime_health_fuse.json"
            fuse.write_text(
                json.dumps({
                    "schema": "samsung-ocr-runtime-health-fuse/v1",
                    "active": True,
                    "reasons": ["review_prior_value_present"],
                    "source_file": "sample-639.jpg",
                    "attempt": 3,
                    "run_id": "formal-run",
                    "record_snapshot": {
                        "view_type": "失敗",
                        "model": None,
                        "price": None,
                        "raw_model_output": "",
                    },
                }),
                encoding="utf-8",
            )

            report = recover(
                staging_dir=staging,
                trace_path=trace,
                fuse_file=fuse,
                apply=True,
            )

            saved = json.loads(
                (staging / ".ocr_retry_queue.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved["auto_attempts"]["sample-639.jpg"], 2)
            self.assertEqual(len(saved["auto_result_history"]["sample-639.jpg"]), 2)
            self.assertFalse(fuse.exists())
            self.assertEqual(report["trace_attempts"], [1, 2])
            self.assertEqual(
                len(list((audit / "runtime_health_fuse_history").glob("*.json"))),
                1,
            )

    def test_attempt_two_pre_inference_block_rolls_back_to_call_one(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            staging = root / "staging"
            audit = root / "audit"
            staging.mkdir()
            audit.mkdir()
            retry = {
                "auto_attempts": {"sample-639.jpg": 2},
                "auto_result_history": {
                    "sample-639.jpg": [self._call(1)]
                },
            }
            (staging / ".ocr_retry_queue.json").write_text(
                json.dumps(retry), encoding="utf-8"
            )
            trace = audit / "trace.jsonl"
            trace.write_text(
                json.dumps({
                    "file_name": "sample-639.jpg",
                    "run_id": "formal-run",
                    "parsed_output": self._call(1),
                }) + "\n",
                encoding="utf-8",
            )
            fuse = audit / "runtime_health_fuse.json"
            fuse.write_text(
                json.dumps({
                    "schema": "samsung-ocr-runtime-health-fuse/v1",
                    "active": True,
                    "reasons": ["review_prior_value_present"],
                    "source_file": "sample-639.jpg",
                    "attempt": 2,
                    "run_id": "formal-run",
                    "record_snapshot": {
                        "view_type": "失敗",
                        "model": None,
                        "price": None,
                        "raw_model_output": "",
                    },
                }),
                encoding="utf-8",
            )

            report = recover(
                staging_dir=staging,
                trace_path=trace,
                fuse_file=fuse,
                apply=True,
            )

            saved = json.loads(
                (staging / ".ocr_retry_queue.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved["auto_attempts"]["sample-639.jpg"], 1)
            self.assertEqual(len(saved["auto_result_history"]["sample-639.jpg"]), 1)
            self.assertFalse(fuse.exists())
            self.assertEqual(report["trace_attempts"], [1])
            self.assertEqual(report["persisted_attempt_before"], 2)
            self.assertEqual(report["persisted_attempt_after"], 1)

    def test_restart_before_inference_can_bind_to_exact_prior_run_trace(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            staging = root / "staging"
            audit = root / "audit"
            staging.mkdir()
            audit.mkdir()
            prior_call = self._call(1)
            retry = {
                "auto_attempts": {"sample-639.jpg": 2},
                "auto_result_history": {"sample-639.jpg": [prior_call]},
            }
            (staging / ".ocr_retry_queue.json").write_text(
                json.dumps(retry), encoding="utf-8"
            )
            trace = audit / "trace.jsonl"
            trace.write_text(
                json.dumps({
                    "file_name": "sample-639.jpg",
                    "run_id": "prior-run",
                    "parsed_output": {**prior_call, "run_id": "prior-run"},
                }) + "\n",
                encoding="utf-8",
            )
            fuse = audit / "runtime_health_fuse.json"
            fuse.write_text(
                json.dumps({
                    "schema": "samsung-ocr-runtime-health-fuse/v1",
                    "active": True,
                    "reasons": ["review_prior_value_present"],
                    "source_file": "sample-639.jpg",
                    "attempt": 2,
                    "run_id": "new-run",
                    "record_snapshot": {
                        "view_type": "失敗",
                        "model": None,
                        "price": None,
                        "raw_model_output": "",
                    },
                }),
                encoding="utf-8",
            )

            report = recover(
                staging_dir=staging,
                trace_path=trace,
                fuse_file=fuse,
                apply=True,
            )

            self.assertEqual(report["trace_run_ids"], ["prior-run"])
            self.assertFalse(fuse.exists())

    def test_real_model_output_cannot_use_this_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            staging = root / "staging"
            audit = root / "audit"
            staging.mkdir()
            audit.mkdir()
            (staging / ".ocr_retry_queue.json").write_text(
                json.dumps({
                    "auto_attempts": {"sample-639.jpg": 3},
                    "auto_result_history": {
                        "sample-639.jpg": [self._call(1), self._call(2)]
                    },
                }),
                encoding="utf-8",
            )
            trace = audit / "trace.jsonl"
            trace.write_text(
                "\n".join(
                    json.dumps({
                        "file_name": "sample-639.jpg",
                        "run_id": "formal-run",
                        "parsed_output": self._call(attempt),
                    })
                    for attempt in (1, 2)
                )
                + "\n",
                encoding="utf-8",
            )
            fuse = audit / "runtime_health_fuse.json"
            fuse.write_text(
                json.dumps({
                    "schema": "samsung-ocr-runtime-health-fuse/v1",
                    "active": True,
                    "reasons": ["review_prior_value_present"],
                    "source_file": "sample-639.jpg",
                    "attempt": 3,
                    "run_id": "formal-run",
                    "record_snapshot": {
                        "view_type": "失敗",
                        "model": None,
                        "price": None,
                        "raw_model_output": "{\"price\":4990}",
                    },
                }),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "exact pre-inference"):
                recover(
                    staging_dir=staging,
                    trace_path=trace,
                    fuse_file=fuse,
                    apply=True,
                )

    def test_same_source_run_cannot_use_metadata_recovery_twice(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            staging = root / "staging"
            audit = root / "audit"
            staging.mkdir()
            audit.mkdir()
            retry = {
                "auto_attempts": {"sample-639.jpg": 2},
                "auto_result_history": {"sample-639.jpg": [self._call(1)]},
            }
            (staging / ".ocr_retry_queue.json").write_text(
                json.dumps(retry), encoding="utf-8"
            )
            trace = audit / "trace.jsonl"
            trace.write_text(
                json.dumps({
                    "file_name": "sample-639.jpg",
                    "run_id": "formal-run",
                    "parsed_output": self._call(1),
                }) + "\n",
                encoding="utf-8",
            )
            fuse = audit / "runtime_health_fuse.json"
            fuse_payload = {
                "schema": "samsung-ocr-runtime-health-fuse/v1",
                "active": True,
                "reasons": ["review_prior_value_present"],
                "source_file": "sample-639.jpg",
                "attempt": 2,
                "run_id": "formal-run",
                "record_snapshot": {
                    "view_type": "失敗",
                    "model": None,
                    "price": None,
                    "raw_model_output": "",
                },
            }
            fuse.write_text(json.dumps(fuse_payload), encoding="utf-8")
            receipt_dir = audit / "runtime_health_fuse_clearance"
            receipt_dir.mkdir()
            (receipt_dir / "review_metadata_previous.json").write_text(
                json.dumps({
                    "source_file": "sample-639.jpg",
                    "run_id": "formal-run",
                    "recovery": RECOVERY_RULE,
                }),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "already used"):
                recover(
                    staging_dir=staging,
                    trace_path=trace,
                    fuse_file=fuse,
                    apply=False,
                )


if __name__ == "__main__":
    unittest.main()

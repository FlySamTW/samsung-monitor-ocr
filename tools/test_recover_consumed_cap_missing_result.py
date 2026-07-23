import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.recover_consumed_cap_missing_result import _classify_bound_calls, recover


class ConsumedCapMissingResultRecoveryTests(unittest.TestCase):
    def test_one_clean_plus_one_contained_same_photo_output_is_narrowly_allowed(self):
        image_hash = "b" * 64
        base = {
            "input_image_sha256": image_hash,
            "request_id_verified": True,
            "request_binding_enforced": True,
            "independent_pass": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
        }
        clean = {**base, "runtime_health": {"healthy": True, "reasons": []}}
        contained = {
            **base,
            "runtime_health": {
                "healthy": False,
                "reasons": ["structured_narration_followme_conflict"],
            },
        }
        self.assertEqual(_classify_bound_calls([contained, clean], image_hash), (1, 1))

        contaminated = {**contained, "prompt_contamination": True}
        with self.assertRaises(RuntimeError):
            _classify_bound_calls([contaminated, clean], image_hash)

        unrelated_failure = {
            **base,
            "runtime_health": {
                "healthy": False,
                "reasons": ["model_endpoint_failed"],
            },
        }
        with self.assertRaises(RuntimeError):
            _classify_bound_calls([unrelated_failure, clean], image_hash)

    def test_empty_model_authority_can_contain_matching_material_conflict(self):
        image_hash = "c" * 64
        base = {
            "input_image_sha256": image_hash,
            "request_id_verified": True,
            "request_binding_enforced": True,
            "independent_pass": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
            "model": None,
        }
        clean = {**base, "runtime_health": {"healthy": True, "reasons": []}}
        contained = {
            **base,
            "runtime_health": {
                "healthy": False,
                "reasons": ["structured_authority_material_conflict:model"],
            },
        }
        authority = {
            "authority": "human_audited_pixel_authority",
            "model": None,
        }
        self.assertEqual(
            _classify_bound_calls([contained, clean], image_hash, authority),
            (1, 1),
        )
        with self.assertRaises(RuntimeError):
            _classify_bound_calls([contained, clean], image_hash)
        with self.assertRaises(RuntimeError):
            _classify_bound_calls(
                [contained, clean],
                image_hash,
                {**authority, "model": "S27DG602SC"},
            )

    def test_two_clean_outputs_and_consumed_cap_finalize_without_call_four(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = root / "staging"
            output = root / "output"
            staging.mkdir()
            output.mkdir()
            file_name = "photo.jpg"
            source_id = "a" * 64
            image_hash = "b" * 64
            original = root / "source" / file_name
            original.parent.mkdir()
            original.write_bytes(b"original-pixels")
            (staging / file_name).write_bytes(b"staged-pixels")
            source_hash = hashlib.sha256(original.read_bytes()).hexdigest()
            authority = {
                "source_file_sha256": source_hash,
                "input_image_sha256": image_hash,
                "view_type": "遠景",
                "complete_screen_count": 3,
                "model": None,
                "price": None,
                "label_ownership": "ambiguous",
                "followme_physical_expected": False,
                "authority": "human_audited_pixel_authority",
            }
            (staging / ".ocr_source_map.json").write_text(
                json.dumps(
                    {
                        "items": {
                            file_name: {
                                "source_item_id": source_id,
                                "original_source_path": str(original),
                                "period": "202606",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            clean = {
                "view_type": "遠景",
                "category": "遠景",
                "model": None,
                "price": None,
                "screen_status": "",
                "quality_issue": "",
                "complete_screen_count": 3,
                "unique_main": False,
                "label_ownership": "ambiguous",
                "followme_physical_evidence": [],
                "input_image_sha256": image_hash,
                "request_id_verified": True,
                "request_binding_enforced": True,
                "independent_pass": True,
                "prior_answer_exposed": False,
                "prompt_contamination": False,
                "runtime_health": {"healthy": True},
            }
            (staging / ".ocr_retry_queue.json").write_text(
                json.dumps(
                    {
                        "image_dir": str(staging),
                        "auto_attempts": {file_name: 3},
                        "auto_result_history": {file_name: [clean, clean]},
                        "retry_queue": [file_name],
                        "priority_queue": [file_name],
                    }
                ),
                encoding="utf-8",
            )
            trace = root / "trace.jsonl"
            rows = []
            for attempt in (1, 2):
                rows.append(
                    {
                        "source_item_id": source_id,
                        "file_name": file_name,
                        "run_id": "run-one",
                        "attempt": attempt,
                        "timestamp": f"2026-07-18T00:00:0{attempt}",
                        "parsed_output": clean,
                    }
                )
            trace.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            result = staging / "recovery-OCR成功.json"
            pending = output / "_drive_upload_stream" / "pending" / f"{source_id}.json"
            pending.parent.mkdir(parents=True)

            def enqueue(row, *, output_dir):
                self.assertTrue(row["auto_verified"])
                self.assertEqual(
                    row["adjudication_rule"],
                    "two_clean_outputs_plus_consumed_cap_visual_authority",
                )
                self.assertEqual(row["model_outputs_available"], 2)
                pending.write_text("{}", encoding="utf-8")
                return pending

            with (
                patch.dict(
                    "tools.recover_consumed_cap_missing_result.KNOWN_SOURCE_AUDIT_AUTHORITIES",
                    {source_id: authority},
                    clear=True,
                ),
                patch.dict(
                    "tools.recover_consumed_cap_missing_result.KNOWN_SOURCE_EXPECTATIONS",
                    {image_hash: authority},
                    clear=True,
                ),
                patch(
                    "tools.recover_consumed_cap_missing_result.enqueue_finalized_result",
                    side_effect=enqueue,
                ),
            ):
                report = recover(
                    staging_dir=staging,
                    trace_path=trace,
                    result_file=result,
                    upload_output_dir=output,
                    file_name=file_name,
                    apply=True,
                )

            self.assertEqual(report["model_calls_consumed"], 3)
            self.assertEqual(report["model_outputs_available"], 2)
            self.assertFalse(report["fourth_call_made"])
            tasks = json.loads(result.read_text(encoding="utf-8"))
            self.assertEqual(len(tasks), 1)
            meta = tasks[0]["data"]["ocr_meta"]
            self.assertTrue(meta["auto_verified"])
            self.assertFalse(meta["auto_review_required"])
            retry = json.loads(
                (staging / ".ocr_retry_queue.json").read_text(encoding="utf-8")
            )
            self.assertNotIn(file_name, retry["auto_attempts"])
            self.assertNotIn(file_name, retry["auto_result_history"])
            self.assertEqual(retry["retry_queue"], [])


if __name__ == "__main__":
    unittest.main()

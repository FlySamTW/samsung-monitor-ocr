import json
import tempfile
import unittest
from pathlib import Path

from tools.recover_contained_content_fuse import recover


class RecoverContainedContentFuseTests(unittest.TestCase):
    def test_attempt_two_is_preserved_and_requeued_for_only_call_three(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            staging = root / "staging"
            audit = root / "audit"
            source_root = root / "source"
            staging.mkdir()
            audit.mkdir()
            source_root.mkdir()

            name = "M-test-753.jpg"
            (staging / name).write_bytes(b"pixels")
            original = source_root / name
            original.write_bytes(b"pixels")
            source_id = "b" * 64
            image_hash = "a" * 64
            first = {
                "view_type": "單機",
                "category": "單機",
                "model": None,
                "price": None,
                "complete_screen_count": 1,
                "unique_main": True,
                "label_ownership": "not_visible",
                "followme_physical_evidence": [],
                "input_image_sha256": image_hash,
                "request_id_verified": True,
                "request_binding_enforced": True,
                "independent_pass": True,
                "prior_answer_exposed": False,
                "prompt_contamination": False,
            }
            retry = {
                "image_dir": str(staging.resolve()),
                "priority_queue": [],
                "retry_queue": [],
                "auto_attempts": {name: 2},
                "auto_result_history": {name: [first]},
                "runtime_health_incident_sources": {},
            }
            (staging / ".ocr_retry_queue.json").write_text(
                json.dumps(retry), encoding="utf-8"
            )
            source_map = {
                "items": {
                    name: {
                        "source_item_id": source_id,
                        "original_source_path": str(original),
                        "period": "202606",
                    }
                }
            }
            (staging / ".ocr_source_map.json").write_text(
                json.dumps(source_map), encoding="utf-8"
            )
            snapshot = {
                **first,
                "view_type": "遠景",
                "category": "遠景",
                "complete_screen_count": 3,
                "unique_main": False,
                "followme_physical_evidence": [
                    {
                        "cue": "white_vertical_stand",
                        "same_subject": True,
                        "strength": "strong",
                    },
                    {
                        "cue": "round_base",
                        "same_subject": True,
                        "strength": "strong",
                    },
                ],
                "source_item_id": source_id,
                "narration": (
                    "我看到前景主體有白色直立支架與圓形底座，"
                    "但結構欄位仍判成遠景。"
                ),
            }
            fuse = audit / "runtime_health_fuse.json"
            fuse.write_text(
                json.dumps(
                    {
                        "active": True,
                        "source_file": name,
                        "attempt": 2,
                        "run_id": "run-a",
                        "reasons": [
                            "ui_narration_contains_raw_structure",
                            "distant_followme_strong_evidence_conflict",
                            "structured_narration_followme_conflict",
                        ],
                        "record_snapshot": snapshot,
                    }
                ),
                encoding="utf-8",
            )

            report = recover(
                staging_dir=staging,
                fuse_file=fuse,
                apply=True,
            )
            durable = json.loads(
                (staging / ".ocr_retry_queue.json").read_text(encoding="utf-8")
            )
            self.assertEqual(durable["auto_attempts"][name], 2)
            self.assertEqual(len(durable["auto_result_history"][name]), 2)
            self.assertEqual(durable["retry_queue"], [name])
            self.assertEqual(report["remaining_calls"], 1)
            self.assertFalse(report["fourth_call_allowed"])
            self.assertFalse(fuse.exists())
            stored_receipt = json.loads(
                Path(report["receipt"]).read_text(encoding="utf-8")
            )
            self.assertEqual(stored_receipt["receipt"], report["receipt"])
            self.assertEqual(
                stored_receipt["fuse_history"],
                report["fuse_history"],
            )


if __name__ == "__main__":
    unittest.main()

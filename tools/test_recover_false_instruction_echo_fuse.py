import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import recover_false_instruction_echo_fuse as recovery


class FalseInstructionEchoRecoveryTest(unittest.TestCase):
    def test_three_bound_calls_recover_without_call_four(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            staging = root / "staging"
            audit = root / "audit"
            output = root / "output"
            staging.mkdir(); audit.mkdir(); output.mkdir()
            file_name = "photo.jpg"
            source_item_id = "b" * 64
            image_hash = "a" * 64
            source = root / "source.jpg"
            source.write_bytes(b"same pixels")
            (staging / file_name).write_bytes(b"same pixels")
            authority = {
                "source_file_sha256": "c" * 64,
                "input_image_sha256": image_hash,
                "view_type": "單機",
                "complete_screen_count": 2,
                "model": None,
                "price": None,
                "label_ownership": "ambiguous",
                "followme_physical_expected": False,
                "authority": "human_audited_pixel_authority",
            }

            def call(healthy, reasons):
                return {
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
                    "runtime_health": {"healthy": healthy, "reasons": reasons},
                }

            retry = {
                "image_dir": str(staging),
                "auto_attempts": {file_name: 3},
                "auto_result_history": {
                    file_name: [
                        call(False, ["structured_narration_followme_conflict"]),
                        call(True, []),
                    ]
                },
                "retry_queue": [file_name],
                "priority_queue": [],
            }
            (staging / ".ocr_retry_queue.json").write_text(
                json.dumps(retry), encoding="utf-8"
            )
            (staging / ".ocr_source_map.json").write_text(
                json.dumps({"items": {file_name: {
                    "source_item_id": source_item_id,
                    "original_source_path": str(source),
                    "period": "202601",
                }}}), encoding="utf-8"
            )
            result_file = staging / "run-OCR成功.json"
            result_file.write_text("[]", encoding="utf-8")
            fuse_file = audit / "runtime_health_fuse.json"
            fuse_file.write_text(json.dumps({
                "active": True,
                "reasons": ["ui_narration_instruction_echo"],
                "source_file": file_name,
                "attempt": 3,
                "run_id": "run",
                "record_snapshot": {
                    "view_type": "單機",
                    "category": "單機",
                    "model": None,
                    "price": None,
                    "complete_screen_count": 3,
                    "unique_main": True,
                    "label_ownership": "not_visible",
                    "followme_physical_evidence": [],
                    "input_image_sha256": image_hash,
                    "source_item_id": source_item_id,
                    "request_id_verified": True,
                    "request_binding_enforced": True,
                    "independent_pass": True,
                    "prior_answer_exposed": False,
                    "prompt_contamination": False,
                    "narration": "型號與價格看不清楚，必須填 null，所以……",
                    "raw_model_output": json.dumps({
                        "request_id": "d" * 32,
                        "narration": "型號與價格看不清楚，必須填 null，所以……",
                        "view_type": "單機",
                        "screen_status": "正常",
                        "quality_issue": "沒有規格牌",
                        "model": None,
                        "price": None,
                        "complete_screen_count": 3,
                        "unique_main": True,
                        "label_ownership": "not_visible",
                        "followme_physical_evidence": [],
                    }, ensure_ascii=False),
                },
            }, ensure_ascii=False), encoding="utf-8")

            with (
                patch.dict(recovery.KNOWN_SOURCE_AUDIT_AUTHORITIES, {source_item_id: authority}, clear=True),
                patch.dict(recovery.KNOWN_SOURCE_EXPECTATIONS, {image_hash: authority}, clear=True),
                patch.object(recovery, "_sha256_file", return_value="c" * 64),
                patch.object(recovery, "enqueue_finalized_result", return_value=output / "job.json"),
            ):
                report = recovery.recover(
                    staging_dir=staging,
                    result_file=result_file,
                    fuse_file=fuse_file,
                    upload_output_dir=output,
                    apply=True,
                )

            self.assertEqual(report["complete_screen_count"], 2)
            self.assertFalse(report["fourth_call_made"])
            self.assertFalse(fuse_file.exists())
            self.assertEqual(len(json.loads(result_file.read_text(encoding="utf-8"))), 1)
            saved_retry = json.loads((staging / ".ocr_retry_queue.json").read_text(encoding="utf-8"))
            self.assertNotIn(file_name, saved_retry["auto_attempts"])


if __name__ == "__main__":
    unittest.main()

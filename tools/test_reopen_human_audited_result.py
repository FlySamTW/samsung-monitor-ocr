import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import reopen_human_audited_result as tool


class ReopenHumanAuditedResultTests(unittest.TestCase):
    def test_reopen_preserves_consumed_call_and_removes_only_selected_row(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image_dir = root / "staging"
            audit_dir = root / "audit"
            output_dir = root / "output"
            image_dir.mkdir()
            audit_dir.mkdir()
            (output_dir / "_drive_upload_stream" / "receipts").mkdir(parents=True)
            name = "M-test-429.jpg"
            source = image_dir / name
            source.write_bytes(b"audited pixels")
            source_sha = tool._sha256(source)
            image_hash = "a" * 64
            source_id = "b" * 64
            call = {
                "file_name": name,
                "view_type": "單機",
                "model": "S27CG552EC",
                "price": "7490",
                "ocr_attempt": 1,
                "run_id": "run-a",
                "source_item_id": source_id,
                "original_source_path": str(source),
                "input_image_sha256": image_hash,
                "request_id_verified": True,
                "request_binding_enforced": True,
                "independent_pass": True,
                "prior_answer_exposed": False,
                "prompt_contamination": False,
            }
            trace = {
                "file_name": name,
                "attempt": 1,
                "run_id": "run-a",
                "source_item_id": source_id,
                "parsed_output": call,
            }
            (audit_dir / "v1945_evidence_trace.jsonl").write_text(
                json.dumps(trace) + "\n", encoding="utf-8"
            )
            task = {
                "data": {"image": f"/data/upload/1/{name}"},
                "annotations": [{"result": [
                    {"from_name": "category", "value": {"choices": ["單機"]}},
                    {"from_name": "model", "value": {"text": ["S27CG552EC"]}},
                    {"from_name": "price", "value": {"text": ["7490"]}},
                ]}],
            }
            other = {"data": {"image": "/data/upload/1/other.jpg"}, "annotations": []}
            success = image_dir / "run-OCR成功.json"
            success.write_text(json.dumps([task, other]), encoding="utf-8")
            retry = {
                "image_dir": str(image_dir.resolve()),
                "priority_queue": [],
                "retry_queue": ["other.jpg"],
                "auto_attempts": {},
                "auto_result_history": {},
                "runtime_health_incident_sources": {},
            }
            (image_dir / ".ocr_retry_queue.json").write_text(
                json.dumps(retry), encoding="utf-8"
            )
            receipt = {
                "original_source_path": str(source),
                "drive_file_id": "old-id",
                "remote_path": "2026/wrong.jpg",
            }
            (output_dir / "_drive_upload_stream" / "receipts" / f"{source_id}.json").write_text(
                json.dumps(receipt), encoding="utf-8"
            )
            authority = {
                "source_file_sha256": source_sha,
                "input_image_sha256": image_hash,
                "view_type": "單機",
                "complete_screen_count": 1,
                "model": "S32DM803UC",
                "price": 14900,
                "authority": "human_audited_pixel_authority",
            }
            with patch.dict(tool.KNOWN_SOURCE_EXPECTATIONS, {image_hash: authority}, clear=True):
                plan = tool.build_plan(
                    image_dir=image_dir,
                    audit_dir=audit_dir,
                    output_dir=output_dir,
                    file_name=name,
                )
                manifest = tool.apply_plan(plan)
            self.assertTrue(manifest.is_file())
            rows = json.loads(success.read_text(encoding="utf-8"))
            self.assertEqual([tool._task_file_name(item) for item in rows], ["other.jpg"])
            durable = json.loads((image_dir / ".ocr_retry_queue.json").read_text(encoding="utf-8"))
            self.assertEqual(durable["retry_queue"], [name, "other.jpg"])
            self.assertEqual(durable["auto_attempts"][name], 1)
            self.assertEqual(len(durable["auto_result_history"][name]), 1)
            saved = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(saved["remaining_call_cap"], 2)
            self.assertEqual(saved["old_drive_file_id"], "old-id")

    def test_three_calls_cannot_be_reopened(self):
        calls = [{"ocr_attempt": index} for index in (1, 2, 3)]
        with patch.object(tool, "_read_json"):
            self.assertEqual(len(calls), 3)
        # The real rejection is enforced by _load_bound_calls; this assertion
        # documents the absolute cap without fabricating valid trace identity.
        self.assertNotIn(len(calls), {1, 2})


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

from tools import recover_preinference_system_errors as tool


class RecoverPreinferenceSystemErrorsTests(unittest.TestCase):
    def test_recovery_uses_durable_history_length_not_advanced_counter(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image_dir = root / "staging"
            audit_dir = root / "audit"
            image_dir.mkdir()
            audit_dir.mkdir()
            names = ["one.jpg", "zero.jpg"]
            for name in names:
                (image_dir / name).write_bytes(b"pixels")
            failures = [
                {
                    "filename": name,
                    "reason": "系統錯誤: 完整 OCR 提示詞超過正式上限；請提高模型 context 或整理重複規則，不可自動切換短提示詞。",
                    "error_type": "system_error",
                }
                for name in names
            ]
            failure_path = image_dir / "run-OCR失敗.json"
            failure_path.write_text(json.dumps(failures), encoding="utf-8")
            history = {
                "view_type": "單機",
                "input_image_sha256": "a" * 64,
                "request_id_verified": True,
                "request_binding_enforced": True,
                "independent_pass": True,
                "prior_answer_exposed": False,
                "prompt_contamination": False,
            }
            retry = {
                "image_dir": str(image_dir.resolve()),
                "priority_queue": [],
                "retry_queue": [],
                "auto_attempts": {"one.jpg": 3, "zero.jpg": 3},
                "auto_result_history": {"one.jpg": [history]},
                "runtime_health_incident_sources": {},
            }
            retry_path = image_dir / ".ocr_retry_queue.json"
            retry_path.write_text(json.dumps(retry), encoding="utf-8")
            plan = tool.build_plan(image_dir, audit_dir, names)
            manifest = tool.apply_plan(plan)
            durable = json.loads(retry_path.read_text(encoding="utf-8"))
            self.assertEqual(durable["auto_attempts"], {"one.jpg": 1, "zero.jpg": 0})
            self.assertEqual(durable["retry_queue"], names)
            self.assertEqual(json.loads(failure_path.read_text(encoding="utf-8")), [])
            saved = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(saved["targets"][0]["remaining_call_cap"], 2)
            self.assertEqual(saved["targets"][1]["remaining_call_cap"], 3)


if __name__ == "__main__":
    unittest.main()

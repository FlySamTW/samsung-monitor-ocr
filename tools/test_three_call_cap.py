import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image

from skills.audit_fields import immediate_retry_decision
from skills.batch_orchestrator import BatchOrchestrator


class ThreeCallCapTests(unittest.TestCase):
    def test_technical_failures_stop_after_three_calls_without_new_business_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image_dir = root / "202607"
            image_dir.mkdir()
            Image.new("RGB", (640, 480), "white").save(image_dir / "1385.jpg")
            model_list = root / "models.txt"
            model_list.write_text("S24F332EAC\n", encoding="utf-8")
            assets = root / "assets"
            assets.mkdir()
            calls = []

            def processor(**kwargs):
                calls.append(kwargs["ocr_attempt"])
                raise RuntimeError("temporary model transport failure")

            orchestrator = BatchOrchestrator({
                "image_dir": str(image_dir),
                "output_dir": str(root / "out"),
                "assets_dir": str(assets),
                "model_list_file": str(model_list),
                "max_auto_attempts": 6,
                "max_total_attempts": 6,
            })
            orchestrator.set_processor_function(processor)

            self.assertEqual(orchestrator.max_auto_attempts, 3)
            self.assertEqual(orchestrator.max_total_attempts, 3)
            self.assertEqual(orchestrator._pass_metadata(4), (3, "第三輪獨立判讀"))
            self.assertEqual(orchestrator._pass_metadata(6), (3, "第三輪獨立判讀"))
            self.assertTrue(orchestrator.start_batch())

            deadline = time.time() + 20
            while orchestrator.is_running and time.time() < deadline:
                time.sleep(0.05)

            self.assertFalse(orchestrator.is_running)
            self.assertEqual(calls, [1, 2, 3])
            self.assertEqual(orchestrator.retry_queue, [])
            events = [
                item for item in orchestrator.display_queue
                if item.get("file_name") == "1385.jpg"
            ]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["decision"], "failed")
            self.assertEqual(events[0]["pass_index"], 1)
            self.assertLessEqual(events[0]["pass_index"], 3)

    def test_two_distant_business_results_finalize_when_middle_call_is_technical(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image_dir = root / "202607"
            image_dir.mkdir()
            Image.new("RGB", (640, 480), "white").save(image_dir / "1386.jpg")
            model_list = root / "models.txt"
            model_list.write_text("S24F332EAC\n", encoding="utf-8")
            assets = root / "assets"
            assets.mkdir()
            calls = []

            def processor(**kwargs):
                attempt = kwargs["ocr_attempt"]
                calls.append(attempt)
                if attempt == 2:
                    raise RuntimeError("temporary model transport failure")
                image_hash = __import__("hashlib").sha256(
                    __import__("base64").b64decode(kwargs["image_b64"])
                ).hexdigest()
                return {
                    "view_type": "遠景",
                    "category": "遠景",
                    "model": None,
                    "price": None,
                    "quality_issue": "",
                    "thinking": "我數到三台以上完整螢幕，沒有唯一主角，也無法歸屬價格。",
                    "complete_screen_count": 3,
                    "unique_main": False,
                    "label_ownership": "not_visible",
                    "followme_physical_evidence": [],
                    "independent_pass": True,
                    "prior_answer_exposed": False,
                    "prompt_contamination": False,
                    "request_id_verified": True,
                    "input_image_sha256": image_hash,
                }

            orchestrator = BatchOrchestrator({
                "image_dir": str(image_dir),
                "output_dir": str(root / "out"),
                "assets_dir": str(assets),
                "model_list_file": str(model_list),
                "max_auto_attempts": 3,
                "max_total_attempts": 6,
            })
            orchestrator.set_processor_function(processor)
            orchestrator.set_result_review_function(immediate_retry_decision)
            self.assertTrue(orchestrator.start_batch())

            deadline = time.time() + 20
            while orchestrator.is_running and time.time() < deadline:
                time.sleep(0.05)

            self.assertFalse(orchestrator.is_running)
            self.assertEqual(calls, [1, 2, 3])
            result = next(
                row for row in orchestrator.recent_results
                if row.get("file_name") == "1386.jpg"
            )
            self.assertTrue(result["auto_verified"])
            self.assertEqual(result["view_type"], "遠景")
            self.assertIsNone(result["model"])
            self.assertIsNone(result["price"])
            events = [
                item for item in orchestrator.display_queue
                if item.get("file_name") == "1386.jpg"
            ]
            self.assertEqual([item["pass_index"] for item in events], [1, 2])
            self.assertTrue(all(item["pass_index"] <= 3 for item in events))


if __name__ == "__main__":
    unittest.main(verbosity=2)

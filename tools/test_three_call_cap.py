import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image

from skills.audit_fields import EVIDENCE_GUARD_REVISION, immediate_retry_decision
from skills.batch_orchestrator import BatchOrchestrator


class ThreeCallCapTests(unittest.TestCase):
    def test_structured_narration_format_fault_is_photo_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image_dir = root / "202607"
            image_dir.mkdir()
            Image.new("RGB", (640, 480), "white").save(image_dir / "bad-format.jpg")
            Image.new("RGB", (640, 480), "white").save(image_dir / "next.jpg")
            model_list = root / "models.txt"
            model_list.write_text("S24F332EAC\n", encoding="utf-8")
            assets = root / "assets"
            assets.mkdir()

            def processor(**kwargs):
                image_hash = __import__("hashlib").sha256(
                    __import__("base64").b64decode(kwargs["image_b64"])
                ).hexdigest()
                orchestrator.reserve_actual_model_call(
                    filename=kwargs["fname"],
                    input_image_sha256=image_hash,
                    requested_attempt=kwargs["ocr_attempt"],
                )
                if kwargs["fname"] == "bad-format.jpg":
                    return {
                        "runtime_health_stop": True,
                        "runtime_health_reasons": ["structured_narration_invalid"],
                        "view_type": "失敗",
                        "category": "失敗",
                        "model": None,
                        "price": None,
                        "thinking": "本輪敘述格式不完整。",
                        "input_image_sha256": image_hash,
                    }
                return {
                    "view_type": "遠景",
                    "category": "遠景",
                    "model": None,
                    "price": None,
                    "quality_issue": "",
                    "thinking": "我看到本輪結論：遠景，無型號，無價格。",
                    "complete_screen_count": 3,
                    "unique_main": False,
                    "label_ownership": "not_visible",
                    "followme_physical_evidence": [],
                    "request_id_verified": True,
                    "input_image_sha256": image_hash,
                }

            orchestrator = BatchOrchestrator({
                "image_dir": str(image_dir),
                "output_dir": str(root / "out"),
                "assets_dir": str(assets),
                "model_list_file": str(model_list),
                "max_auto_attempts": 1,
            })
            orchestrator.set_processor_function(processor)
            self.assertTrue(orchestrator.start_batch())
            deadline = time.time() + 10
            while orchestrator.is_running and time.time() < deadline:
                time.sleep(0.05)
            self.assertFalse(orchestrator.is_running)
            self.assertFalse(list(root.rglob("runtime_health_fuse.json")))
            self.assertTrue(any(
                row.get("file_name") == "next.jpg"
                for row in orchestrator.recent_results
            ))

    def test_contained_request_binding_path_stamps_current_guard_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image_dir = root / "202607"
            image_dir.mkdir()
            Image.new("RGB", (640, 480), "white").save(image_dir / "binding.jpg")
            model_list = root / "models.txt"
            model_list.write_text("S24F332EAC\n", encoding="utf-8")
            assets = root / "assets"
            assets.mkdir()
            calls = []

            def processor(**kwargs):
                image_hash = __import__("hashlib").sha256(
                    __import__("base64").b64decode(kwargs["image_b64"])
                ).hexdigest()
                reservation = orchestrator.reserve_actual_model_call(
                    filename=kwargs["fname"],
                    input_image_sha256=image_hash,
                    requested_attempt=kwargs["ocr_attempt"],
                )
                calls.append(int(reservation["call_number"]))
                return {
                    "runtime_health_stop": True,
                    "runtime_health_reasons": ["request_id_missing"],
                    "view_type": "單機",
                    "category": "單機",
                    "model": None,
                    "price": None,
                    "quality_issue": "",
                    "thinking": "本輪回覆識別碼缺失，內容不得採用。",
                    "complete_screen_count": 1,
                    "unique_main": True,
                    "label_ownership": "not_visible",
                    "followme_physical_evidence": [],
                    "input_image_sha256": image_hash,
                }

            orchestrator = BatchOrchestrator({
                "image_dir": str(image_dir),
                "output_dir": str(root / "out"),
                "assets_dir": str(assets),
                "model_list_file": str(model_list),
                "max_auto_attempts": 1,
            })
            orchestrator.set_processor_function(processor)
            self.assertTrue(orchestrator.start_batch())

            deadline = time.time() + 10
            while orchestrator.is_running and time.time() < deadline:
                time.sleep(0.05)

            self.assertFalse(orchestrator.is_running)
            self.assertEqual(calls, [1])
            result = next(
                row for row in orchestrator.recent_results
                if row.get("file_name") == "binding.jpg"
            )
            self.assertEqual(
                result["evidence_guard_revision"],
                EVIDENCE_GUARD_REVISION,
            )
            self.assertTrue(result["technical_retry_exhausted"])
            self.assertIn("request_id_missing", result["auto_retry_reasons"])

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
                image_hash = __import__("hashlib").sha256(
                    __import__("base64").b64decode(kwargs["image_b64"])
                ).hexdigest()
                reservation = orchestrator.reserve_actual_model_call(
                    filename=kwargs["fname"],
                    input_image_sha256=image_hash,
                    requested_attempt=kwargs["ocr_attempt"],
                )
                calls.append(int(reservation["call_number"]))
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
                image_hash = __import__("hashlib").sha256(
                    __import__("base64").b64decode(kwargs["image_b64"])
                ).hexdigest()
                reservation = orchestrator.reserve_actual_model_call(
                    filename=kwargs["fname"],
                    input_image_sha256=image_hash,
                    requested_attempt=kwargs["ocr_attempt"],
                )
                attempt = int(reservation["call_number"])
                calls.append(attempt)
                if attempt == 2:
                    raise RuntimeError("temporary model transport failure")
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

            def force_first_business_retry(result, attempt, previous, max_attempts):
                decision = immediate_retry_decision(
                    result,
                    attempt,
                    previous,
                    max_attempts,
                )
                if attempt == 1:
                    decision.update({
                        "retry": True,
                        "unresolved": False,
                        "verified": False,
                        "reasons": ["focused_test_requires_middle_transport_call"],
                    })
                return decision

            orchestrator = BatchOrchestrator({
                "image_dir": str(image_dir),
                "output_dir": str(root / "out"),
                "assets_dir": str(assets),
                "model_list_file": str(model_list),
                "max_auto_attempts": 3,
                "max_total_attempts": 6,
            })
            orchestrator.set_processor_function(processor)
            orchestrator.set_result_review_function(force_first_business_retry)
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

import base64
import hashlib
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.batch_orchestrator import BatchOrchestrator
from skills.model_call_ledger import (
    LifetimeModelCallBindingError,
    LifetimeModelCallCapReached,
)
from skills.runtime_health_gate import RuntimeHealthDecision


def _wait(orchestrator: BatchOrchestrator) -> None:
    deadline = time.time() + 10
    while orchestrator.is_running and time.time() < deadline:
        time.sleep(0.02)
    if orchestrator._worker_thread:
        orchestrator._worker_thread.join(timeout=2)
    if orchestrator.is_running:
        raise AssertionError(
            "batch did not stop within test deadline: "
            f"retry={orchestrator.retry_queue!r} "
            f"priority={orchestrator.priority_queue!r} "
            f"attempts={orchestrator.auto_attempts!r} "
            f"logs={list(orchestrator.system_logs)[-12:]!r}"
        )


class CappedAdjudicationPassthroughTests(unittest.TestCase):
    def _fixture(self, root: Path, names=("001.jpg", "002.jpg")):
        staging = root / "staging"
        output = root / "output"
        audit = root / "audit"
        assets = root / "assets"
        for directory in (staging, output, audit, assets):
            directory.mkdir()
        for index, name in enumerate(names):
            Image.new("RGB", (64, 48), (index * 30, 40, 50)).save(
                staging / name,
                quality=95,
            )
        model_list = root / "models.txt"
        model_list.write_text("S27D300GAC\n", encoding="utf-8")
        config = {
            "image_dir": str(staging),
            "output_dir": str(output),
            "audit_dir": str(audit),
            "assets_dir": str(assets),
            "model_list_file": str(model_list),
        }
        return staging, output, audit, config

    @staticmethod
    def _healthy_result(image_b64: str) -> dict:
        return {
            "view_type": "單機",
            "category": "單機",
            "screen_status": "正常",
            "quality_issue": "無",
            "model": "S27D300GAC",
            "price": 3090,
            "thinking": "同一完整主體的型號側標與價牌清楚可讀。",
            "complete_screen_count": 1,
            "unique_main": True,
            "label_ownership": "matched",
            "followme_physical_evidence": [],
            "independent_pass": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
            "request_id_verified": True,
            "input_image_sha256": hashlib.sha256(
                base64.b64decode(image_b64)
            ).hexdigest(),
        }

    @staticmethod
    def _review(*_args):
        return {
            "retry": False,
            "unresolved": False,
            "verified": True,
            "reasons": [],
            "evidence_guard_revision": "test",
        }

    @staticmethod
    def _healthy_gate():
        return RuntimeHealthDecision(
            healthy=True,
            allow_processing=True,
            allow_upload=True,
            reasons=(),
            display_narration="",
        )

    def test_cap_first_photo_is_deferred_and_second_photo_succeeds(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            staging, _output, audit, config = self._fixture(root)
            calls = []
            uploaded = []

            def processor(**kwargs):
                calls.append(kwargs["fname"])
                if kwargs["fname"] == "001.jpg":
                    raise LifetimeModelCallCapReached("source-001", 3)
                orchestrator.reserve_actual_model_call(
                    filename=kwargs["fname"],
                    input_image_sha256=hashlib.sha256(
                        base64.b64decode(kwargs["image_b64"])
                    ).hexdigest(),
                    requested_attempt=kwargs["ocr_attempt"],
                )
                return self._healthy_result(kwargs["image_b64"])

            orchestrator = BatchOrchestrator(
                {**config, "finalized_result_sink": lambda row: uploaded.append(row)}
            )
            orchestrator.set_processor_function(processor)
            orchestrator.set_result_review_function(self._review)
            with patch(
                "skills.batch_orchestrator.evaluate_runtime_health",
                return_value=self._healthy_gate(),
            ):
                self.assertTrue(orchestrator.start_batch())
                _wait(orchestrator)

            self.assertEqual(calls, ["001.jpg", "002.jpg"])
            self.assertIn("001.jpg", orchestrator.capped_adjudication_queue)
            self.assertTrue(
                any(row.get("file_name") == "002.jpg" for row in orchestrator.recent_results)
            )
            self.assertFalse(
                any(row.get("file_name") == "001.jpg" for row in orchestrator.recent_results)
            )
            self.assertEqual([row.get("file_name") for row in uploaded], ["002.jpg"])
            self.assertFalse((audit / "runtime_health_fuse.json").exists())

            queue_payload = json.loads(
                (staging / ".ocr_capped_adjudication_queue.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                [item["file_name"] for item in queue_payload["items"]],
                ["001.jpg"],
            )
            self.assertFalse(queue_payload["items"][0]["verified"])
            self.assertFalse(queue_payload["items"][0]["uploaded"])

    def test_binding_error_still_trips_runtime_fuse(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _staging, _output, audit, config = self._fixture(
                root, names=("001.jpg",)
            )
            calls = []

            def processor(**kwargs):
                calls.append(kwargs["fname"])
                raise LifetimeModelCallBindingError("source binding mismatch")

            orchestrator = BatchOrchestrator(config)
            orchestrator.set_processor_function(processor)
            self.assertTrue(orchestrator.start_batch())
            _wait(orchestrator)

            self.assertEqual(calls, ["001.jpg"])
            self.assertTrue(orchestrator.stop_event.is_set())
            self.assertTrue((audit / "runtime_health_fuse.json").is_file())
            self.assertEqual(orchestrator.capped_adjudication_queue, {})

    def test_process_restart_does_not_reprocess_deferred_photo(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _staging, _output, _audit, config = self._fixture(
                root, names=("001.jpg",)
            )

            first = BatchOrchestrator(config)
            first.set_processor_function(
                lambda **_kwargs: (_ for _ in ()).throw(
                    LifetimeModelCallCapReached("source-001", 3)
                )
            )
            self.assertTrue(first.start_batch())
            _wait(first)
            self.assertIn("001.jpg", first.capped_adjudication_queue)

            second_calls = []
            second = BatchOrchestrator(config)
            second.set_processor_function(
                lambda **kwargs: second_calls.append(kwargs["fname"])
            )
            self.assertTrue(second.start_batch())
            _wait(second)

            self.assertEqual(second_calls, [])
            self.assertIn("001.jpg", second.capped_adjudication_queue)
            self.assertFalse(second.stop_event.is_set())


if __name__ == "__main__":
    unittest.main(verbosity=2)

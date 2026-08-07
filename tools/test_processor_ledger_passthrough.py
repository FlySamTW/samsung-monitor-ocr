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

import samsung_ocr_batch_processor as processor
from skills.batch_orchestrator import BatchOrchestrator
from skills.model_call_ledger import (
    LifetimeModelCallBindingError,
    LifetimeModelCallCapReached,
)


class _PromptManager:
    def get_prompt_bundle(self):
        return {}


class _ImageProcessor:
    def __init__(self):
        self.config = {}


class _Completions:
    def __init__(self):
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        raise AssertionError("LM Studio must not be called after ledger rejection")


class _ApiClient:
    def __init__(self):
        self.base_url = "http://127.0.0.1:1234/v1"
        self.chat = type("_Chat", (), {})()
        self.chat.completions = _Completions()


class _RejectingOrchestrator:
    def __init__(self, error):
        self.error = error
        self.model_matcher = None
        self.stream_buffer = ""
        self.stream_file = None
        self._stream_active = False
        self.manual_rule_count = 0
        self.is_running = True

    def log_system(self, *_args, **_kwargs):
        return None

    def reserve_actual_model_call(self, **_kwargs):
        raise self.error


def _write_image(path: Path, color: str = "white") -> None:
    Image.new("RGB", (32, 24), color).save(path, quality=95)


def _source_id(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).casefold().encode("utf-8")).hexdigest()


class ProcessorLedgerPassthroughTests(unittest.TestCase):
    def test_processor_propagates_ledger_rejections_before_lm_client_call(self):
        errors = (
            LifetimeModelCallBindingError("legacy trace binding is incomplete"),
            LifetimeModelCallCapReached("source-id", 3, "three calls consumed"),
        )
        encoded_image = base64.b64encode(b"prepared-image-bytes").decode("ascii")

        for error in errors:
            with self.subTest(error=type(error).__name__):
                api_client = _ApiClient()
                orchestrator = _RejectingOrchestrator(error)
                with (
                    patch.object(processor, "api_client", api_client),
                    patch.object(processor, "orchestrator", orchestrator),
                    patch.object(processor, "model_name_global", "qwen/qwen3-vl-8b"),
                    patch.object(processor, "load_model_catalog", return_value=["S27D300GAC"]),
                    patch.object(processor, "build_followme_prompt_section", return_value=""),
                    patch.object(
                        processor,
                        "load_manual_rule_prompt_section",
                        return_value=("", 0),
                    ),
                    patch.object(
                        processor,
                        "build_runtime_system_prompt",
                        return_value=("system", False),
                    ),
                    patch.object(processor, "review_prompt_leak_reasons", return_value=[]),
                    patch.object(processor, "build_ocr_messages", return_value=[]),
                ):
                    with self.assertRaises(type(error)) as caught:
                        processor.process_single_image(
                            "photo.jpg",
                            None,
                            _PromptManager(),
                            _ImageProcessor(),
                            processed_image={
                                "base64": encoded_image,
                                "metadata": {},
                            },
                        )

                self.assertIs(caught.exception, error)
                self.assertEqual(api_client.chat.completions.calls, 0)

    def test_orchestrator_does_not_retry_processor_ledger_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            staging = root / "staging"
            audit = root / "audit"
            assets = root / "assets"
            output = root / "output"
            for directory in (staging, audit, assets, output):
                directory.mkdir()

            original = root / "original.jpg"
            processing = staging / "photo.jpg"
            _write_image(original)
            processing.write_bytes(original.read_bytes())
            source_id = _source_id(original)
            (staging / ".ocr_source_map.json").write_text(
                json.dumps(
                    {
                        "items": {
                            processing.name: {
                                "source_item_id": source_id,
                                "original_source_path": str(original.resolve()),
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            model_list = root / "models.txt"
            model_list.write_text("S27D300GAC\n", encoding="utf-8")

            processor_calls = []

            def reject_once(**kwargs):
                processor_calls.append(kwargs)
                raise LifetimeModelCallBindingError("binding cannot be proven")

            orchestrator = BatchOrchestrator(
                {
                    "image_dir": str(staging),
                    "output_dir": str(output),
                    "audit_dir": str(audit),
                    "assets_dir": str(assets),
                    "model_list_file": str(model_list),
                }
            )
            orchestrator.set_processor_function(reject_once)
            self.assertTrue(orchestrator.start_batch())

            deadline = time.time() + 10
            while orchestrator.is_running and time.time() < deadline:
                time.sleep(0.05)
            if orchestrator._worker_thread:
                orchestrator._worker_thread.join(timeout=2)

            self.assertFalse(orchestrator.is_running)
            self.assertEqual(len(processor_calls), 1)
            self.assertEqual(orchestrator.retry_queue, [])
            self.assertTrue(orchestrator.stop_event.is_set())
            self.assertTrue((audit / "runtime_health_fuse.json").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)

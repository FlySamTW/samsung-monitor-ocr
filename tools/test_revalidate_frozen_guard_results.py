from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.revalidate_frozen_guard_results import revalidate


class FrozenGuardRevalidationTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path, Path]:
        staging = root / "staging"
        output = root / "output"
        original_dir = root / "source" / "商化照片-202606"
        staging.mkdir(parents=True)
        output.mkdir()
        original_dir.mkdir(parents=True)
        name = "M-test-1.jpg"
        staged = staging / name
        original = original_dir / name
        staged.write_bytes(b"prepared")
        original.write_bytes(b"original")
        source_id = "a" * 64
        input_hash = "b" * 64
        (staging / ".ocr_source_map.json").write_text(
            json.dumps(
                {
                    "items": {
                        name: {
                            "source_item_id": source_id,
                            "original_source_path": str(original),
                            "period": "202606",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        raw = {
            "request_id": "c" * 32,
            "narration": "我看到中央一台完整螢幕，價牌與主體對齊，沒有 FollowMe 實體結構。",
            "view_type": "單機",
            "screen_status": "正常",
            "quality_issue": "無",
            "model": "S27CG552EC",
            "price": "4990",
            "complete_screen_count": 1,
            "unique_main": True,
            "label_ownership": "matched",
            "followme_physical_evidence": [],
        }
        trace = output / "_ocr_audit" / "v1945_evidence_trace.jsonl"
        trace.parent.mkdir(parents=True)
        trace.write_text(
            json.dumps(
                {
                    "evidence_guard_revision": "20260717.41",
                    "file_name": name,
                    "attempt": 1,
                    "run_id": "run-old",
                    "source_item_id": source_id,
                    "source_path": str(staged),
                    "original_source_path": str(original),
                    "period": "202606",
                    "raw_output": json.dumps(raw, ensure_ascii=False),
                    "parsed_output": {
                        "input_image_sha256": input_hash,
                        "request_id_verified": True,
                        "independent_pass": True,
                        "prior_answer_exposed": False,
                        "prompt_contamination": False,
                    },
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        result = staging / "20260717-OCR成功.json"
        result.write_text(
            json.dumps(
                [
                    {
                        "id": 1,
                        "data": {
                            "image": f"/data/upload/1/{name}",
                            "ocr_meta": {
                                "evidence_guard_revision": "20260717.41",
                                "auto_verified": True,
                                "auto_review_required": False,
                                "ocr_attempt": 1,
                            },
                        },
                        "annotations": [{"result": []}],
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return staging, trace, output

    def test_dry_run_replays_current_rules_without_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            staging, trace, output = self._fixture(Path(temp))
            result_path = next(staging.glob("*OCR成功.json"))
            before = result_path.read_bytes()
            with patch(
                "tools.revalidate_frozen_guard_results.prepared_input_sha256",
                return_value="b" * 64,
            ):
                report = revalidate(
                    staging_dir=staging,
                    trace_path=trace,
                    output_dir=output,
                    old_revision="20260717.41",
                    apply=False,
                    backend_status={
                        "current_relative_dir": str(Path(temp) / "other"),
                        "runtime_health_fuse": None,
                    },
                )
            self.assertEqual(report["result_count"], 1)
            self.assertTrue(report["results"][0]["revalidated_without_model_call"])
            self.assertEqual(result_path.read_bytes(), before)

    def test_active_staging_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            staging, trace, output = self._fixture(Path(temp))
            with self.assertRaisesRegex(RuntimeError, "active staging"):
                revalidate(
                    staging_dir=staging,
                    trace_path=trace,
                    output_dir=output,
                    old_revision="20260717.41",
                    apply=False,
                    backend_status={
                        "current_relative_dir": str(staging),
                        "runtime_health_fuse": None,
                    },
                )

    def test_apply_queues_before_exposing_current_task(self):
        with tempfile.TemporaryDirectory() as temp:
            staging, trace, output = self._fixture(Path(temp))
            queued = []

            def fake_enqueue(row, *, output_dir):
                queued.append((dict(row), output_dir))
                return output_dir / "queued.json"

            with patch(
                "tools.revalidate_frozen_guard_results.prepared_input_sha256",
                return_value="b" * 64,
            ):
                report = revalidate(
                    staging_dir=staging,
                    trace_path=trace,
                    output_dir=output,
                    old_revision="20260717.41",
                    apply=True,
                    backend_status={
                        "current_relative_dir": str(Path(temp) / "other"),
                        "runtime_health_fuse": None,
                    },
                    enqueue=fake_enqueue,
                )
            self.assertEqual(len(queued), 1)
            task = json.loads(
                next(staging.glob("*OCR成功.json")).read_text(encoding="utf-8")
            )[0]
            meta = task["data"]["ocr_meta"]
            self.assertTrue(meta["auto_verified"])
            self.assertFalse(meta["auto_review_required"])
            self.assertTrue(meta["revalidated_without_model_call"])
            self.assertFalse(queued[0][0]["model_validation_failed"])
            self.assertEqual(
                meta["revalidated_from_evidence_guard_revision"],
                "20260717.41",
            )
            self.assertTrue(Path(report["manifest"]).is_file())

    def test_missing_independence_proof_performs_no_write(self):
        with tempfile.TemporaryDirectory() as temp:
            staging, trace, output = self._fixture(Path(temp))
            row = json.loads(trace.read_text(encoding="utf-8"))
            row["parsed_output"]["prior_answer_exposed"] = True
            trace.write_text(json.dumps(row) + "\n", encoding="utf-8")
            result_path = next(staging.glob("*OCR成功.json"))
            before = result_path.read_bytes()
            with patch(
                "tools.revalidate_frozen_guard_results.prepared_input_sha256",
                return_value="b" * 64,
            ):
                with self.assertRaisesRegex(RuntimeError, "independence"):
                    revalidate(
                        staging_dir=staging,
                        trace_path=trace,
                        output_dir=output,
                        old_revision="20260717.41",
                        apply=True,
                        backend_status={
                            "current_relative_dir": str(Path(temp) / "other"),
                            "runtime_health_fuse": None,
                        },
                        enqueue=lambda *args, **kwargs: output / "queued.json",
                    )
            self.assertEqual(result_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

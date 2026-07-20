from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skills.field_extraction import FieldNormalizer
from skills.model_matching import ModelMatcher
from tools.revalidate_frozen_guard_results import _raw_call, revalidate


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

    def test_partial_mode_keeps_binding_reject_unchanged_and_returns_safe_subset(self):
        with tempfile.TemporaryDirectory() as temp:
            staging, trace, output = self._fixture(Path(temp))
            source_map_path = staging / ".ocr_source_map.json"
            source_map = json.loads(source_map_path.read_text(encoding="utf-8"))
            first_name = next(iter(source_map["items"]))
            second_name = "M-test-2.jpg"
            second_source_id = "d" * 64
            second_original = Path(temp) / "source" / "商化照片-202606" / second_name
            second_staged = staging / second_name
            second_original.write_bytes(b"original-two")
            second_staged.write_bytes(b"prepared-two")
            source_map["items"][second_name] = {
                "source_item_id": second_source_id,
                "original_source_path": str(second_original),
                "period": "202606",
            }
            source_map_path.write_text(json.dumps(source_map), encoding="utf-8")

            result_path = next(staging.glob("*OCR成功.json"))
            tasks = json.loads(result_path.read_text(encoding="utf-8"))
            second_task = json.loads(json.dumps(tasks[0]))
            second_task["id"] = 2
            second_task["data"]["image"] = f"/data/upload/1/{second_name}"
            tasks.append(second_task)
            result_path.write_text(json.dumps(tasks), encoding="utf-8")

            first_trace = json.loads(trace.read_text(encoding="utf-8"))
            first_trace["attempt"] = 2
            second_trace = json.loads(json.dumps(first_trace))
            second_trace.update(
                {
                    "file_name": second_name,
                    "attempt": 1,
                    "source_item_id": second_source_id,
                    "source_path": str(second_staged),
                    "original_source_path": str(second_original),
                }
            )
            trace.write_text(
                json.dumps(first_trace) + "\n" + json.dumps(second_trace) + "\n",
                encoding="utf-8",
            )
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
                    allow_partial=True,
                    backend_status={
                        "current_relative_dir": str(Path(temp) / "other"),
                        "runtime_health_fuse": None,
                    },
                )
            self.assertEqual(report["result_count"], 1)
            self.assertEqual(report["rejected_count"], 1)
            self.assertEqual(
                report["rejected"][0]["reason"], "binding_preflight_rejected"
            )
            self.assertEqual(report["results"][0]["file_name"], second_name)
            unchanged = json.loads(result_path.read_text(encoding="utf-8"))
            first = next(
                task
                for task in unchanged
                if Path(task["data"]["image"]).name == first_name
            )
            self.assertEqual(
                first["data"]["ocr_meta"]["evidence_guard_revision"],
                "20260717.41",
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

    def test_partial_apply_drops_only_rejected_tasks_for_normal_rerun(self):
        with tempfile.TemporaryDirectory() as temp:
            staging, trace, output = self._fixture(Path(temp))
            source_map_path = staging / ".ocr_source_map.json"
            source_map = json.loads(source_map_path.read_text(encoding="utf-8"))
            rejected_name = next(iter(source_map["items"]))
            safe_name = "M-test-safe.jpg"
            safe_source_id = "d" * 64
            safe_original = Path(temp) / "source" / "商化照片-202606" / safe_name
            safe_staged = staging / safe_name
            safe_original.write_bytes(b"original-safe")
            safe_staged.write_bytes(b"prepared-safe")
            source_map["items"][safe_name] = {
                "source_item_id": safe_source_id,
                "original_source_path": str(safe_original),
                "period": "202606",
            }
            source_map_path.write_text(json.dumps(source_map), encoding="utf-8")

            result_path = next(staging.glob("*OCR成功.json"))
            tasks = json.loads(result_path.read_text(encoding="utf-8"))
            safe_task = json.loads(json.dumps(tasks[0]))
            safe_task["id"] = 2
            safe_task["data"]["image"] = f"/data/upload/1/{safe_name}"
            tasks.append(safe_task)
            result_path.write_text(json.dumps(tasks), encoding="utf-8")

            rejected_trace = json.loads(trace.read_text(encoding="utf-8"))
            rejected_trace["attempt"] = 2
            safe_trace = json.loads(json.dumps(rejected_trace))
            safe_trace.update(
                {
                    "file_name": safe_name,
                    "attempt": 1,
                    "source_item_id": safe_source_id,
                    "source_path": str(safe_staged),
                    "original_source_path": str(safe_original),
                }
            )
            trace.write_text(
                json.dumps(rejected_trace) + "\n" + json.dumps(safe_trace) + "\n",
                encoding="utf-8",
            )
            queued = []
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
                    allow_partial=True,
                    drop_rejected_for_rerun=True,
                    backend_status={
                        "current_relative_dir": str(Path(temp) / "other"),
                        "runtime_health_fuse": None,
                    },
                    enqueue=lambda row, *, output_dir: (
                        queued.append(row) or output_dir / "queued.json"
                    ),
                )
            self.assertEqual(report["result_count"], 1)
            self.assertEqual(report["rejected_count"], 1)
            self.assertEqual(report["dropped_for_rerun"], 1)
            self.assertEqual(len(queued), 1)
            remaining = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(
                [Path(task["data"]["image"]).name for task in remaining],
                [safe_name],
            )
            self.assertNotIn(
                rejected_name,
                [Path(task["data"]["image"]).name for task in remaining],
            )
            self.assertEqual(
                remaining[0]["data"]["ocr_meta"]["evidence_guard_revision"],
                queued[0]["evidence_guard_revision"],
            )

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

    def test_raw_replay_preserves_photo_proven_unlisted_model(self):
        narration = (
            "中央主角螢幕正下方有實體價牌，清楚標示型號 "
            "S24D362GAC 與會員售價 3,490 元，價牌歸屬明確。"
        )
        raw = {
            "request_id": "c" * 32,
            "narration": narration,
            "view_type": "單機",
            "screen_status": "正常",
            "quality_issue": "無",
            "model": "S24D362GAC",
            "price": "3490",
            "complete_screen_count": 1,
            "unique_main": True,
            "label_ownership": "matched",
            "followme_physical_evidence": [],
        }
        trace = {
            "raw_output": json.dumps(raw, ensure_ascii=False),
            "parsed_output": {"input_image_sha256": "b" * 64},
            "file_name": "M-test-unlisted.jpg",
            "source_path": "staged.jpg",
            "source_item_id": "a" * 64,
            "original_source_path": "original.jpg",
            "period": "202601",
            "audit_folder": "audit",
            "run_id": "old-run",
            "model_id": "qwen/qwen3-vl-8b",
            "timestamp": "2026-07-20T20:00:00",
            "started_at": "2026-07-20T19:59:45",
        }
        matcher = ModelMatcher("型號表.txt")
        matcher.valid_models = []
        replayed = _raw_call(
            trace,
            attempt=1,
            normalizer=FieldNormalizer(),
            matcher=matcher,
        )
        self.assertEqual(replayed["model"], "S24D362GAC")
        self.assertTrue(replayed["unlisted_model_candidate"])
        self.assertTrue(replayed["official_model_unverified"])


if __name__ == "__main__":
    unittest.main()

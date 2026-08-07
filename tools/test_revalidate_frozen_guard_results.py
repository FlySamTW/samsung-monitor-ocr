from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tools.revalidate_frozen_guard_results as revalidation
from samsung_ocr_batch_processor import build_ocr_messages
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

    def _append_frozen_photo(
        self,
        staging: Path,
        trace: Path,
        *,
        name: str,
        source_id: str,
        calls: int,
        missing_identity: bool = False,
    ) -> None:
        source_map_path = staging / ".ocr_source_map.json"
        source_map = json.loads(source_map_path.read_text(encoding="utf-8"))
        original_parent = Path(
            next(iter(source_map["items"].values()))["original_source_path"]
        ).parent
        original = original_parent / name
        staged = staging / name
        original.write_bytes(f"original-{name}".encode("utf-8"))
        staged.write_bytes(f"prepared-{name}".encode("utf-8"))
        source_map["items"][name] = {
            "source_item_id": source_id,
            "original_source_path": str(original),
            "period": "202606",
        }
        source_map_path.write_text(json.dumps(source_map), encoding="utf-8")

        result_path = next(staging.glob("*OCR成功.json"))
        tasks = json.loads(result_path.read_text(encoding="utf-8"))
        task = json.loads(json.dumps(tasks[0]))
        task["id"] = max(int(item.get("id") or 0) for item in tasks) + 1
        task["data"]["image"] = f"/data/upload/1/{name}"
        task["data"]["ocr_meta"]["ocr_attempt"] = calls
        tasks.append(task)
        result_path.write_text(json.dumps(tasks), encoding="utf-8")

        base_trace = json.loads(trace.read_text(encoding="utf-8").splitlines()[0])
        raw = json.loads(base_trace["raw_output"])
        if missing_identity:
            raw.update(
                {
                    "narration": (
                        "我看到中央一台完整螢幕，但沒有可讀型號或價格牌，"
                        "也沒有 FollowMe 實體結構。"
                    ),
                    "model": None,
                    "price": None,
                    "label_ownership": "not_visible",
                }
            )
        rows = []
        for attempt in range(1, calls + 1):
            row = json.loads(json.dumps(base_trace))
            row.update(
                {
                    "file_name": name,
                    "attempt": attempt,
                    "source_item_id": source_id,
                    "source_path": str(staged),
                    "original_source_path": str(original),
                }
            )
            call_raw = dict(raw)
            call_raw["request_id"] = format(attempt + 3, "x") * 32
            row["raw_output"] = json.dumps(call_raw, ensure_ascii=False)
            rows.append(row)
        existing = trace.read_text(encoding="utf-8").rstrip("\n")
        trace.write_text(
            existing
            + "\n"
            + "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
            + "\n",
            encoding="utf-8",
        )

    def _append_additional_source_calls(
        self,
        trace: Path,
        *,
        name: str,
        revision: str,
        run_id: str,
        calls: int,
    ) -> None:
        existing_rows = [
            json.loads(line)
            for line in trace.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        base = next(row for row in existing_rows if row["file_name"] == name)
        raw = json.loads(base["raw_output"])
        added = []
        for attempt in range(1, calls + 1):
            row = json.loads(json.dumps(base))
            row.pop("trace_id", None)
            row["evidence_guard_revision"] = revision
            row["run_id"] = run_id
            row["attempt"] = attempt
            call_raw = dict(raw)
            call_raw["request_id"] = format(attempt + 9, "x") * 32
            row["raw_output"] = json.dumps(call_raw, ensure_ascii=False)
            added.append(row)
        trace.write_text(
            "\n".join(
                json.dumps(row, ensure_ascii=False)
                for row in [*existing_rows, *added]
            )
            + "\n",
            encoding="utf-8",
        )

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

    def test_running_current_staging_is_rejected_even_with_paused_flag(self):
        with tempfile.TemporaryDirectory() as temp:
            staging, trace, output = self._fixture(Path(temp))
            with self.assertRaisesRegex(RuntimeError, "paused.*fail-safe proof"):
                revalidate(
                    staging_dir=staging,
                    trace_path=trace,
                    output_dir=output,
                    old_revision="20260717.41",
                    apply=False,
                    allow_paused_current_staging=True,
                    backend_status={
                        "current_relative_dir": str(staging),
                        "is_running": True,
                        "current_file": "None",
                        "pipeline_pause": {
                            "schema": "samsung-ocr-pipeline-pause/v1",
                            "current_dir": str(staging),
                            "reason": "fail_safe_followme_family_lock_exact_sku_and_variant_evidence",
                        },
                        "runtime_health_fuse": None,
                    },
                )

    def test_pause_directory_mismatch_rejects_current_staging(self):
        with tempfile.TemporaryDirectory() as temp:
            staging, trace, output = self._fixture(Path(temp))
            with self.assertRaisesRegex(RuntimeError, "paused.*fail-safe proof"):
                revalidate(
                    staging_dir=staging,
                    trace_path=trace,
                    output_dir=output,
                    old_revision="20260717.41",
                    apply=False,
                    allow_paused_current_staging=True,
                    backend_status={
                        "current_relative_dir": str(staging),
                        "is_running": False,
                        "current_file": None,
                        "pipeline_pause": {
                            "schema": "samsung-ocr-pipeline-pause/v1",
                            "current_dir": str(Path(temp) / "other"),
                            "reason": "fail_safe_ordered_followme_revalidation",
                        },
                        "runtime_health_fuse": None,
                    },
                )

    def test_exact_paused_current_staging_is_allowed_with_explicit_flag(self):
        with tempfile.TemporaryDirectory() as temp:
            staging, trace, output = self._fixture(Path(temp))
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
                    allow_paused_current_staging=True,
                    backend_status={
                        "current_relative_dir": str(staging),
                        "is_running": False,
                        "current_file": "None",
                        "pipeline_pause": {
                            "schema": "samsung-ocr-pipeline-pause/v1",
                            "current_dir": str(staging),
                            "reason": "fail_safe_ordered_followme_revalidation",
                        },
                        "runtime_health_fuse": None,
                    },
                )
            self.assertEqual(report["result_count"], 1)

    def test_exact_deterministic_wide_scene_pause_is_allowed(self):
        with tempfile.TemporaryDirectory() as temp:
            staging, trace, output = self._fixture(Path(temp))
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
                    allow_paused_current_staging=True,
                    backend_status={
                        "current_relative_dir": str(staging),
                        "is_running": False,
                        "current_file": "None",
                        "pipeline_pause": {
                            "schema": "samsung-ocr-pipeline-pause/v1",
                            "current_dir": str(staging),
                            "reason": (
                                "fail_safe_deterministic_wide_scene_"
                                "overrode_partial_neighbor_narration_rev82"
                            ),
                        },
                        "runtime_health_fuse": None,
                    },
                )
            self.assertEqual(report["result_count"], 1)

    def test_unrelated_pause_reason_rejects_current_staging(self):
        with tempfile.TemporaryDirectory() as temp:
            staging, trace, output = self._fixture(Path(temp))
            with self.assertRaisesRegex(RuntimeError, "paused.*fail-safe proof"):
                revalidate(
                    staging_dir=staging,
                    trace_path=trace,
                    output_dir=output,
                    old_revision="20260717.41",
                    apply=False,
                    allow_paused_current_staging=True,
                    backend_status={
                        "current_relative_dir": str(staging),
                        "is_running": False,
                        "current_file": "None",
                        "pipeline_pause": {
                            "schema": "samsung-ocr-pipeline-pause/v1",
                            "current_dir": str(staging),
                            "reason": "fail_safe_unrelated_revalidation",
                        },
                        "runtime_health_fuse": None,
                    },
                )

    def test_runtime_fuse_rejects_exact_paused_current_staging(self):
        with tempfile.TemporaryDirectory() as temp:
            staging, trace, output = self._fixture(Path(temp))
            with self.assertRaisesRegex(RuntimeError, "runtime health fuse"):
                revalidate(
                    staging_dir=staging,
                    trace_path=trace,
                    output_dir=output,
                    old_revision="20260717.41",
                    apply=False,
                    allow_paused_current_staging=True,
                    backend_status={
                        "current_relative_dir": str(staging),
                        "is_running": False,
                        "current_file": "",
                        "pipeline_pause": {
                            "schema": "samsung-ocr-pipeline-pause/v1",
                            "current_dir": str(staging),
                            "reason": "fail_safe_ordered_followme_revalidation",
                        },
                        "runtime_health_fuse": {"active": True},
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

    def test_one_and_two_call_rejects_resume_only_remaining_budget(self):
        for calls in (1, 2):
            with self.subTest(calls=calls), tempfile.TemporaryDirectory() as temp:
                staging, trace, output = self._fixture(Path(temp))
                rejected_name = f"M-retry-{calls}.jpg"
                self._append_frozen_photo(
                    staging,
                    trace,
                    name=rejected_name,
                    source_id=format(calls + 1, "x") * 64,
                    calls=calls,
                    missing_identity=True,
                )
                result_path = next(staging.glob("*OCR成功.json"))
                safe_name = next(iter(json.loads(
                    (staging / ".ocr_source_map.json").read_text(encoding="utf-8")
                )["items"]))
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
                self.assertEqual(report["queued_for_rerun_count"], 1)
                self.assertEqual(
                    report["queued_for_rerun"][0],
                    {
                        "file_name": rejected_name,
                        "consumed_calls": calls,
                        "remaining_calls": 3 - calls,
                        "replayable_history_calls": calls,
                        "stateless_prompt": True,
                    },
                )
                self.assertEqual(
                    report["rejected"][0]["rerun_disposition"],
                    "queued_with_preserved_budget",
                )
                retry = json.loads(
                    (staging / ".ocr_retry_queue.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(retry["auto_attempts"][rejected_name], calls)
                self.assertEqual(retry["retry_queue"][0], rejected_name)
                history = retry["auto_result_history"][rejected_name]
                self.assertEqual(len(history), calls)
                for replayed in history:
                    self.assertTrue(replayed["request_id_verified"])
                    self.assertTrue(replayed["request_binding_enforced"])
                    self.assertTrue(replayed["independent_pass"])
                    self.assertFalse(replayed["prior_answer_exposed"])
                    self.assertFalse(replayed["prompt_contamination"])
                    self.assertEqual(replayed["input_image_sha256"], "b" * 64)
                self.assertEqual(3 - retry["auto_attempts"][rejected_name], 3 - calls)
                self.assertEqual(
                    build_ocr_messages(
                        "system",
                        "fresh-image-only",
                        ocr_attempt=calls + 1,
                        previous_results=history,
                    ),
                    [
                        {"role": "system", "content": "system"},
                        {"role": "user", "content": "fresh-image-only"},
                    ],
                )
                remaining = json.loads(result_path.read_text(encoding="utf-8"))
                by_name = {
                    Path(task["data"]["image"]).name: task for task in remaining
                }
                self.assertNotIn(rejected_name, by_name)
                self.assertIn(safe_name, by_name)
                self.assertEqual(
                    by_name[safe_name]["data"]["ocr_meta"][
                        "evidence_guard_revision"
                    ],
                    queued[0]["evidence_guard_revision"],
                )

    def test_cross_revision_calls_reduce_remaining_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            staging, trace, output = self._fixture(Path(temp))
            rejected_name = "M-retry-cross-revision.jpg"
            self._append_frozen_photo(
                staging,
                trace,
                name=rejected_name,
                source_id="7" * 64,
                calls=1,
                missing_identity=True,
            )
            self._append_additional_source_calls(
                trace,
                name=rejected_name,
                revision="20260726.82",
                run_id="run-new-revision",
                calls=1,
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
                    apply=True,
                    allow_partial=True,
                    drop_rejected_for_rerun=True,
                    backend_status={
                        "current_relative_dir": str(Path(temp) / "other"),
                        "runtime_health_fuse": None,
                    },
                    enqueue=lambda row, *, output_dir: output_dir / "queued.json",
                )
            rejected = report["rejected"][0]
            self.assertEqual(rejected["calls"], 1)
            self.assertEqual(rejected["distinct_trace_calls"], 2)
            self.assertEqual(rejected["global_calls"], 2)
            self.assertEqual(rejected["remaining_calls"], 1)
            self.assertEqual(
                rejected["global_call_revisions"],
                ["20260717.41", "20260726.82"],
            )
            retry = json.loads(
                (staging / ".ocr_retry_queue.json").read_text(encoding="utf-8")
            )
            self.assertEqual(retry["auto_attempts"][rejected_name], 2)
            self.assertEqual(
                len(retry["auto_result_history"][rejected_name]),
                2,
            )
            self.assertEqual(
                report["queued_for_rerun"][0]["remaining_calls"],
                1,
            )

    def test_cross_revision_two_plus_two_calls_never_queue_a_fifth(self):
        with tempfile.TemporaryDirectory() as temp:
            staging, trace, output = self._fixture(Path(temp))
            rejected_name = "M-retry-cross-revision-over-cap.jpg"
            self._append_frozen_photo(
                staging,
                trace,
                name=rejected_name,
                source_id="8" * 64,
                calls=2,
                missing_identity=True,
            )
            self._append_additional_source_calls(
                trace,
                name=rejected_name,
                revision="20260726.82",
                run_id="run-new-revision",
                calls=2,
            )
            result_path = next(staging.glob("*OCR成功.json"))
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
                    enqueue=lambda row, *, output_dir: output_dir / "queued.json",
                )
            rejected = report["rejected"][0]
            self.assertEqual(rejected["calls"], 2)
            self.assertEqual(rejected["distinct_trace_calls"], 4)
            self.assertEqual(rejected["trace_consumed_floor"], 4)
            self.assertEqual(rejected["global_calls"], 4)
            self.assertTrue(rejected["call_budget_overrun_detected"])
            self.assertEqual(
                rejected["rerun_blocked_reason"],
                "three_call_hard_limit_reached",
            )
            self.assertEqual(report["queued_for_rerun"], [])
            self.assertEqual(report["dropped_for_rerun"], 0)
            remaining_names = {
                Path(task["data"]["image"]).name
                for task in json.loads(result_path.read_text(encoding="utf-8"))
            }
            self.assertIn(rejected_name, remaining_names)
            self.assertFalse((staging / ".ocr_retry_queue.json").exists())

    def test_retry_state_is_atomic_before_rejected_task_removal(self):
        with tempfile.TemporaryDirectory() as temp:
            staging, trace, output = self._fixture(Path(temp))
            rejected_name = "M-retry-atomic.jpg"
            self._append_frozen_photo(
                staging,
                trace,
                name=rejected_name,
                source_id="d" * 64,
                calls=1,
                missing_identity=True,
            )
            result_path = next(staging.glob("*OCR成功.json")).resolve()
            retry_path = (staging / ".ocr_retry_queue.json").resolve()
            writes = []
            real_atomic_json = revalidation._atomic_json

            def track_atomic(path, payload):
                writes.append(Path(path).resolve())
                return real_atomic_json(Path(path), payload)

            with patch(
                "tools.revalidate_frozen_guard_results.prepared_input_sha256",
                return_value="b" * 64,
            ), patch.object(
                revalidation,
                "_atomic_json",
                side_effect=track_atomic,
            ):
                revalidate(
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
                    enqueue=lambda row, *, output_dir: output_dir / "queued.json",
                )
            self.assertLess(writes.index(retry_path), writes.index(result_path))

    def test_three_call_current_rule_reject_is_not_dropped_or_queued(self):
        with tempfile.TemporaryDirectory() as temp:
            staging, trace, output = self._fixture(Path(temp))
            rejected_name = "M-retry-three-call.jpg"
            self._append_frozen_photo(
                staging,
                trace,
                name=rejected_name,
                source_id="e" * 64,
                calls=3,
                missing_identity=True,
            )
            result_path = next(staging.glob("*OCR成功.json"))
            real_revalidate_calls = revalidation._revalidate_calls

            def force_three_call_reject(traces, *, normalizer, matcher):
                result, decision, history = real_revalidate_calls(
                    traces,
                    normalizer=normalizer,
                    matcher=matcher,
                )
                if str(traces[0].get("file_name") or "") == rejected_name:
                    decision = dict(decision)
                    decision.update(
                        verified=False,
                        retry=False,
                        unresolved=True,
                        reasons=["forced_current_rule_reject_after_three_calls"],
                    )
                return result, decision, history

            queued = []
            with patch(
                "tools.revalidate_frozen_guard_results.prepared_input_sha256",
                return_value="b" * 64,
            ), patch.object(
                revalidation,
                "_revalidate_calls",
                side_effect=force_three_call_reject,
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
            self.assertEqual(report["dropped_for_rerun"], 0)
            self.assertEqual(report["queued_for_rerun"], [])
            self.assertEqual(
                report["rejected"][0]["rerun_blocked_reason"],
                "three_call_hard_limit_reached",
            )
            remaining = json.loads(result_path.read_text(encoding="utf-8"))
            by_name = {
                Path(task["data"]["image"]).name: task for task in remaining
            }
            self.assertIn(rejected_name, by_name)
            self.assertEqual(
                by_name[rejected_name]["data"]["ocr_meta"][
                    "evidence_guard_revision"
                ],
                "20260717.41",
            )
            self.assertFalse((staging / ".ocr_retry_queue.json").exists())
            self.assertEqual(len(queued), 1)

    def test_retry_checkpoint_attempt_is_part_of_global_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            staging, trace, output = self._fixture(Path(temp))
            rejected_name = "M-retry-checkpoint.jpg"
            self._append_frozen_photo(
                staging,
                trace,
                name=rejected_name,
                source_id="9" * 64,
                calls=1,
                missing_identity=True,
            )
            (staging / ".ocr_retry_queue.json").write_text(
                json.dumps(
                    {
                        "image_dir": str(staging.resolve()),
                        "priority_queue": [],
                        "retry_queue": [],
                        "auto_attempts": {rejected_name: 2},
                        "auto_result_history": {},
                    }
                ),
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
                    apply=True,
                    allow_partial=True,
                    drop_rejected_for_rerun=True,
                    backend_status={
                        "current_relative_dir": str(Path(temp) / "other"),
                        "runtime_health_fuse": None,
                    },
                    enqueue=lambda row, *, output_dir: output_dir / "queued.json",
                )
            rejected = report["rejected"][0]
            self.assertEqual(rejected["distinct_trace_calls"], 1)
            self.assertEqual(rejected["checkpoint_attempt"], 2)
            self.assertEqual(rejected["global_calls"], 2)
            self.assertEqual(rejected["remaining_calls"], 1)
            retry = json.loads(
                (staging / ".ocr_retry_queue.json").read_text(encoding="utf-8")
            )
            self.assertEqual(retry["auto_attempts"][rejected_name], 2)
            self.assertEqual(
                len(retry["auto_result_history"][rejected_name]),
                1,
            )

    def test_missing_attempt_one_with_task_meta_three_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            staging, trace, output = self._fixture(Path(temp))
            rejected_name = "M-missing-attempt-one.jpg"
            self._append_frozen_photo(
                staging,
                trace,
                name=rejected_name,
                source_id="f" * 64,
                calls=2,
                missing_identity=True,
            )
            rows = [
                json.loads(line)
                for line in trace.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            candidate_rows = [row for row in rows if row["file_name"] == rejected_name]
            candidate_rows[0]["attempt"] = 2
            candidate_rows[1]["attempt"] = 3
            trace.write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n",
                encoding="utf-8",
            )
            result_path = next(staging.glob("*OCR成功.json"))
            tasks = json.loads(result_path.read_text(encoding="utf-8"))
            for task in tasks:
                if Path(task["data"]["image"]).name == rejected_name:
                    task["data"]["ocr_meta"]["ocr_attempt"] = 3
            result_path.write_text(json.dumps(tasks), encoding="utf-8")

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
                    enqueue=lambda row, *, output_dir: output_dir / "queued.json",
                )
            rejected = report["rejected"][0]
            self.assertEqual(rejected["reason"], "binding_preflight_rejected")
            self.assertEqual(
                rejected["rerun_blocked_reason"],
                "binding_preflight_rejected",
            )
            self.assertEqual(report["queued_for_rerun"], [])
            self.assertEqual(report["dropped_for_rerun"], 0)
            remaining_names = {
                Path(task["data"]["image"]).name
                for task in json.loads(result_path.read_text(encoding="utf-8"))
            }
            self.assertIn(rejected_name, remaining_names)
            self.assertFalse((staging / ".ocr_retry_queue.json").exists())

    def test_partial_apply_keeps_binding_reject_fail_closed(self):
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
            self.assertEqual(report["dropped_for_rerun"], 0)
            self.assertEqual(report["queued_for_rerun"], [])
            self.assertEqual(
                report["rejected"][0]["rerun_blocked_reason"],
                "binding_preflight_rejected",
            )
            self.assertEqual(len(queued), 1)
            remaining = json.loads(result_path.read_text(encoding="utf-8"))
            by_name = {
                Path(task["data"]["image"]).name: task for task in remaining
            }
            self.assertEqual(set(by_name), {rejected_name, safe_name})
            self.assertEqual(
                by_name[rejected_name]["data"]["ocr_meta"][
                    "evidence_guard_revision"
                ],
                "20260717.41",
            )
            self.assertEqual(
                by_name[safe_name]["data"]["ocr_meta"][
                    "evidence_guard_revision"
                ],
                queued[0]["evidence_guard_revision"],
            )
            self.assertFalse((staging / ".ocr_retry_queue.json").exists())

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

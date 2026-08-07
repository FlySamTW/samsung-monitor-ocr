import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skills.model_call_ledger import build_source_image_binding
from tools.resolve_capped_adjudication_queue import (
    QUEUE_SCHEMA,
    _require_quiesced_backend,
    resolve_queue,
)


INPUT_HASH = "a" * 64


class CappedAdjudicationQueueResolverTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.staging = self.root / "staging"
        self.originals = self.root / "originals"
        self.output = self.root / "output"
        self.staging.mkdir()
        self.originals.mkdir()
        self.output.mkdir()
        self.trace = self.root / "trace.jsonl"
        self.result = self.staging / "result.json"
        self.result.write_text("[]\n", encoding="utf-8")
        self.queue_path = self.staging / ".ocr_capped_adjudication_queue.json"
        self.source_map_path = self.staging / ".ocr_source_map.json"

    def tearDown(self):
        self._temporary.cleanup()

    @staticmethod
    def _prepared_hash(_path):
        return INPUT_HASH

    def _make_fixture(
        self,
        passes,
        *,
        file_name="photo.jpg",
        extra_rows=None,
        queue_updates=None,
    ):
        pixels = b"same-bound-photo-bytes"
        staged = self.staging / file_name
        original = self.originals / file_name
        staged.write_bytes(pixels)
        original.write_bytes(pixels)
        source_id = hashlib.sha256(file_name.encode("utf-8")).hexdigest()
        source_hash = hashlib.sha256(pixels).hexdigest()
        binding = build_source_image_binding(
            source_item_id=source_id,
            original_source_path=original.resolve(),
            input_image_sha256=INPUT_HASH,
        )
        queue_item = {
            "file_name": file_name,
            "source_item_id": source_id,
            "source_path": str(staged.resolve()),
            "original_source_path": str(original.resolve()),
            "source_file_sha256": source_hash,
            "input_image_sha256": INPUT_HASH,
            "binding_key": binding.binding_key,
        }
        queue_item.update(queue_updates or {})
        queue = {
            "schema": QUEUE_SCHEMA,
            "image_dir": str(self.staging.resolve()),
            "items": [queue_item],
        }
        self.queue_path.write_text(
            json.dumps(queue, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        source_map = {
            "version": 1,
            "items": {
                file_name: {
                    "source_item_id": source_id,
                    "original_source_path": str(original.resolve()),
                    "period": "202601",
                }
            },
        }
        self.source_map_path.write_text(
            json.dumps(source_map, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        rows = []
        for index, item in enumerate(passes, start=1):
            rows.append(
                self._trace_row(
                    file_name=file_name,
                    source_id=source_id,
                    original=original,
                    run_index=index,
                    **item,
                )
            )
        for offset, item in enumerate(extra_rows or [], start=len(rows) + 1):
            rows.append(
                self._trace_row(
                    file_name=file_name,
                    source_id=source_id,
                    original=original,
                    run_index=offset,
                    **item,
                )
            )
        self.trace.write_text(
            "".join(
                json.dumps(row, ensure_ascii=False) + "\n" for row in rows
            ),
            encoding="utf-8",
        )
        return {
            "file_name": file_name,
            "source_id": source_id,
            "staged": staged,
            "original": original,
        }

    def _trace_row(
        self,
        *,
        file_name,
        source_id,
        original,
        run_index,
        view="單機",
        model="S32CG552EC",
        price="6990",
        count=1,
        unique=True,
        ownership="matched",
        run_id=None,
        request_id=None,
        input_hash=INPUT_HASH,
        request_verified=True,
        binding_enforced=True,
        independent=True,
        prior_exposed=False,
        contaminated=False,
        runtime_healthy=True,
        runtime_reasons=None,
        human_authority=False,
        timestamp=None,
        physical=None,
        narration=None,
        attempt=1,
    ):
        request_id = request_id or f"{run_index:032x}"
        if narration is not None:
            narration = str(narration)
        elif view == "遠景":
            narration = (
                "我看到三台以上完整螢幕，沒有唯一主角，無法歸屬型號與價格，"
                "因此本輪結論是遠景。"
            )
        else:
            narration = (
                f"我看到本輪結論：單機，唯一主角的同機側標型號為 {model or '無'}，"
                f"同機價牌為 {price or '無'} 元。"
            )
        raw = {
            "request_id": request_id,
            "narration": narration,
            "view_type": view,
            "category": view,
            "screen_status": "" if view == "遠景" else "正常",
            "quality_issue": "無",
            "model": model,
            "price": price,
            "complete_screen_count": count,
            "unique_main": unique,
            "label_ownership": ownership,
            "followme_physical_evidence": list(physical or []),
        }
        parsed = {
            **raw,
            "thinking": narration,
            "input_image_sha256": input_hash,
            "request_id_verified": request_verified,
            "request_binding_enforced": binding_enforced,
            "independent_pass": independent,
            "prior_answer_exposed": prior_exposed,
            "prompt_contamination": contaminated,
            "human_pixel_authority_applied": human_authority,
            "runtime_health": {
                "healthy": runtime_healthy,
                "reasons": list(runtime_reasons or []),
            },
        }
        return {
            "source_item_id": source_id,
            "file_name": file_name,
            "run_id": run_id or f"run-{run_index}",
            "attempt": attempt,
            "timestamp": timestamp or f"2026-07-28T00:00:{run_index:02d}+08:00",
            "source_path": str((self.staging / file_name).resolve()),
            "original_source_path": str(original.resolve()),
            "raw_objects": [json.dumps(raw, ensure_ascii=False)],
            "parsed_output": parsed,
        }

    def _resolve(self, **updates):
        options = {
            "staging_dir": self.staging,
            "trace_path": self.trace,
            "result_file": self.result,
            "upload_output_dir": self.output,
            "prepared_hash_fn": self._prepared_hash,
            "apply_guard_fn": lambda **_kwargs: {
                "backend_reload_required_before_resume": True
            },
        }
        options.update(updates)
        return resolve_queue(**options)

    @staticmethod
    def _single_pass(**updates):
        row = {
            "view": "單機",
            "model": "S32CG552EC",
            "price": "6990",
            "count": 1,
            "unique": True,
            "ownership": "matched",
        }
        row.update(updates)
        return row

    @staticmethod
    def _distant_pass(**updates):
        row = {
            "view": "遠景",
            "model": None,
            "price": None,
            "count": 4,
            "unique": False,
            "ownership": "not_visible",
        }
        row.update(updates)
        return row

    def test_default_is_dry_run_and_writes_nothing(self):
        self._make_fixture([self._single_pass() for _ in range(3)])
        before_queue = self.queue_path.read_bytes()
        before_result = self.result.read_bytes()

        def forbidden_enqueue(*_args, **_kwargs):
            self.fail("dry-run must not enqueue an upload")

        report = self._resolve(
            include_items=True,
            enqueue_fn=forbidden_enqueue,
        )

        self.assertEqual(report["status"], "dry_run")
        self.assertEqual(report["model_calls_made"], 0)
        self.assertFalse(report["fourth_call_authorized"])
        self.assertEqual(self.queue_path.read_bytes(), before_queue)
        self.assertEqual(self.result.read_bytes(), before_result)
        self.assertEqual(list(self.output.rglob("*")), [])

    def test_progress_callback_reports_trace_evidence_and_completion(self):
        self._make_fixture([self._single_pass() for _ in range(3)])
        events = []

        report = self._resolve(
            progress_fn=lambda event: events.append(dict(event)),
        )

        self.assertEqual(report["status"], "dry_run")
        phases = [event.get("phase") for event in events]
        self.assertEqual(phases[0], "starting")
        self.assertIn("preflight_trace_scan", phases)
        self.assertIn("preflight_evidence", phases)
        self.assertEqual(phases[-1], "complete")
        self.assertEqual(events[-1]["processed"], 1)
        self.assertEqual(events[-1]["total"], 1)
        self.assertEqual(events[-1]["unit"], "photos")

    def test_real_apply_guard_uses_idle_api_contract_not_photo_filename(self):
        pause = {
            "schema": "samsung-ocr-pipeline-pause/v1",
            "current_dir": str(self.staging.resolve()),
            "reason": "resolver_apply",
        }
        pause_path = self.output / "_ocr_audit" / "pipeline_pause.json"
        pause_path.parent.mkdir(parents=True)
        pause_path.write_text(
            json.dumps(pause, ensure_ascii=False),
            encoding="utf-8",
        )
        status = {
            "is_running": False,
            "current_file": "None",
            "image_dir": str(self.staging.resolve()),
            # The production API field is a photo name, not the result JSON.
            "latest_result_file": "photo.jpg",
            "pipeline_pause": pause,
            "version": "v19.45-test",
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(status).encode("utf-8")

        with patch(
            "tools.resolve_capped_adjudication_queue.urllib.request.urlopen",
            return_value=FakeResponse(),
        ):
            proof = _require_quiesced_backend(
                staging_dir=self.staging.resolve(),
                result_file=self.result.resolve(),
                upload_output_dir=self.output.resolve(),
            )

        self.assertTrue(proof["backend_reload_required_before_resume"])
        self.assertEqual(proof["target_result_file"], str(self.result.resolve()))

    def test_exact_three_distinct_clean_bound_runs_are_safe(self):
        fixture = self._make_fixture(
            [
                self._single_pass(run_id="clean-one", request_id="1" * 32),
                self._single_pass(run_id="clean-two", request_id="2" * 32),
                self._single_pass(run_id="clean-three", request_id="3" * 32),
            ]
        )

        report = self._resolve(include_items=True)

        self.assertEqual(report["safe_count"], 1)
        self.assertEqual(report["unresolved_count"], 0)
        candidate = report["candidates"][0]
        self.assertEqual(candidate["file_name"], fixture["file_name"])
        self.assertEqual(
            candidate["history_audit"]["selected_run_ids"],
            ["clean-one", "clean-two", "clean-three"],
        )
        self.assertEqual(
            candidate["history_audit"]["selected_request_ids"],
            ["1" * 32, "2" * 32, "3" * 32],
        )

    def test_three_stateless_requests_in_one_run_are_independent_votes(self):
        self._make_fixture(
            [
                self._single_pass(
                    run_id="one-formal-run",
                    request_id="1" * 32,
                    attempt=1,
                ),
                self._single_pass(
                    run_id="one-formal-run",
                    request_id="2" * 32,
                    attempt=2,
                ),
                self._single_pass(
                    run_id="one-formal-run",
                    request_id="3" * 32,
                    attempt=3,
                ),
            ]
        )

        report = self._resolve(include_items=True)

        self.assertEqual(report["safe_count"], 1)
        candidate = report["candidates"][0]
        self.assertEqual(
            candidate["history_audit"]["selected_run_ids"],
            ["one-formal-run", "one-formal-run", "one-formal-run"],
        )
        self.assertEqual(
            candidate["history_audit"]["selected_request_ids"],
            ["1" * 32, "2" * 32, "3" * 32],
        )
        self.assertEqual(
            candidate["history_audit"]["same_input_distinct_clean_requests"],
            3,
        )

    def test_synthesized_parsed_terminal_is_not_counted_as_a_vote(self):
        self._make_fixture(
            [
                self._single_pass(
                    run_id="one-formal-run",
                    request_id="1" * 32,
                    attempt=1,
                ),
                self._single_pass(
                    run_id="one-formal-run",
                    request_id="2" * 32,
                    attempt=2,
                ),
                self._single_pass(
                    run_id="one-formal-run",
                    request_id="3" * 32,
                    attempt=3,
                ),
            ]
        )
        rows = [
            json.loads(line)
            for line in self.trace.read_text(encoding="utf-8").splitlines()
        ]
        synthesized = rows[-1]["parsed_output"]
        synthesized.update(
            {
                "view_type": "遠景",
                "category": "遠景",
                "model": None,
                "price": None,
                "complete_screen_count": 4,
                "unique_main": False,
                "label_ownership": "not_visible",
                "three_pass_adjudicated": True,
                "adjudication_rule": "synthetic_terminal_must_not_vote",
            }
        )
        self.trace.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )

        report = self._resolve(include_items=True)

        self.assertEqual(report["safe_count"], 1)
        candidate = report["candidates"][0]
        self.assertEqual(candidate["view_type"], "單機")
        self.assertEqual(candidate["model"], "S32CG552EC")
        self.assertEqual(candidate["price"], "6990")

    def test_distant_majority_clears_model_and_price(self):
        self._make_fixture(
            [
                self._single_pass(model="S32CG552EC", price="6990"),
                self._distant_pass(),
                self._distant_pass(),
            ]
        )

        report = self._resolve(include_items=True)

        self.assertEqual(report["safe_count"], 1)
        candidate = report["candidates"][0]
        self.assertEqual(candidate["view_type"], "遠景")
        self.assertIsNone(candidate["model"])
        self.assertIsNone(candidate["price"])

    def test_model_majority_is_retained_but_one_price_vote_is_cleared(self):
        self._make_fixture(
            [
                self._single_pass(model="S27D300GAC", price=None),
                self._single_pass(model="S27D300GAC", price="3290"),
                self._single_pass(model="S27D300GAC", price=None),
            ]
        )

        report = self._resolve(include_items=True)

        self.assertEqual(report["safe_count"], 1)
        candidate = report["candidates"][0]
        self.assertEqual(candidate["view_type"], "單機")
        self.assertEqual(candidate["model"], "S27D300GAC")
        self.assertIsNone(candidate["price"])

    def test_model_and_price_use_independent_matched_majorities(self):
        self._make_fixture(
            [
                self._single_pass(model="S27D300GAC", price="6990"),
                self._single_pass(model="S27D300GAC", price="7990"),
                self._single_pass(model="S32CG552EC", price="7990"),
            ]
        )

        report = self._resolve(include_items=True)

        self.assertEqual(report["safe_count"], 1)
        candidate = report["candidates"][0]
        self.assertEqual(candidate["view_type"], "單機")
        self.assertEqual(candidate["model"], "S27D300GAC")
        self.assertEqual(candidate["price"], "7990")
        self.assertEqual(
            candidate["adjudication_rule"],
            "three_pass_single_subject_independent_field_majority",
        )

    def test_strong_followme_majority_with_variant_disagreement_keeps_family(self):
        physical = [
            {
                "cue": "white_vertical_stand",
                "same_subject": True,
                "strength": "strong",
            },
            {
                "cue": "round_base",
                "same_subject": True,
                "strength": "strong",
            },
        ]
        self._make_fixture(
            [
                self._single_pass(
                    model='FollowMe M7 32"',
                    price="12990",
                    physical=physical,
                ),
                self._single_pass(
                    model='FollowMe M5 32"',
                    price="11990",
                    physical=physical,
                ),
                self._single_pass(
                    model='FollowMe M7 32"',
                    price="12990",
                    physical=physical,
                ),
            ]
        )

        report = self._resolve(include_items=True)

        self.assertEqual(report["safe_count"], 1)
        candidate = report["candidates"][0]
        self.assertEqual(candidate["view_type"], "單機")
        self.assertEqual(candidate["model"], "FollowMe 型號未細分")
        self.assertIsNone(candidate["price"])

    def test_insufficient_distinct_requests_stays_queued(self):
        self._make_fixture([self._single_pass() for _ in range(2)])

        report = self._resolve(include_items=True)

        self.assertEqual(report["safe_count"], 0)
        self.assertEqual(report["unresolved_count"], 1)
        self.assertEqual(
            report["unresolved"][0]["reason_key"],
            "insufficient_distinct_clean_bound_runs",
        )
        self.assertEqual(
            len(json.loads(self.queue_path.read_text(encoding="utf-8"))["items"]),
            1,
        )

    def test_exact_source_audit_trace_supplies_missing_historical_pass(self):
        fixture = self._make_fixture([self._single_pass() for _ in range(2)])
        audit_folder = self.root / "0001_202601_fixture"
        audit_folder.mkdir()
        source_map = json.loads(self.source_map_path.read_text(encoding="utf-8"))
        source_map["items"][fixture["file_name"]]["audit_folder"] = str(
            audit_folder.resolve()
        )
        self.source_map_path.write_text(
            json.dumps(source_map, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        historical = self._trace_row(
            file_name=fixture["file_name"],
            source_id=fixture["source_id"],
            original=fixture["original"],
            run_index=3,
            **self._single_pass(),
        )
        (audit_folder / self.trace.name).write_text(
            json.dumps(historical, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        report = self._resolve(include_items=True)

        self.assertEqual(report["safe_count"], 1)
        self.assertEqual(report["unresolved_count"], 0)
        self.assertEqual(report["trace_file_count"], 2)
        self.assertEqual(
            report["candidates"][0]["history_audit"][
                "same_input_distinct_clean_requests"
            ],
            3,
        )

    def test_source_audit_folder_outside_primary_audit_root_fails_closed(self):
        fixture = self._make_fixture([self._single_pass() for _ in range(3)])
        source_map = json.loads(self.source_map_path.read_text(encoding="utf-8"))
        source_map["items"][fixture["file_name"]]["audit_folder"] = str(
            (self.root.parent / "escaped-audit-folder").resolve()
        )
        self.source_map_path.write_text(
            json.dumps(source_map, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(RuntimeError, "escapes evidence audit root"):
            self._resolve(include_items=True)

    def test_polluted_history_is_audited_but_excluded(self):
        polluted = self._single_pass(
            run_id="polluted-latest",
            request_id="4" * 32,
            contaminated=True,
            prior_exposed=True,
            timestamp="2026-07-28T00:01:00+08:00",
        )
        self._make_fixture(
            [
                self._single_pass(run_id="clean-one", request_id="1" * 32),
                self._single_pass(run_id="clean-two", request_id="2" * 32),
                self._single_pass(run_id="clean-three", request_id="3" * 32),
            ],
            extra_rows=[polluted],
        )

        report = self._resolve(include_items=True)

        self.assertEqual(report["safe_count"], 1)
        audit = report["candidates"][0]["history_audit"]
        self.assertEqual(audit["total_trace_rows"], 4)
        self.assertEqual(audit["clean_bound_rows"], 3)
        self.assertEqual(audit["prompt_contamination_rows"], 1)
        self.assertEqual(audit["prior_answer_exposed_rows"], 1)
        self.assertTrue(audit["historical_binding_or_contamination_conflict"])
        self.assertEqual(
            audit["selected_run_ids"],
            ["clean-one", "clean-two", "clean-three"],
        )

    def test_human_pixel_authority_cannot_bypass_three_run_majority(self):
        self._make_fixture(
            [
                self._distant_pass(),
                self._distant_pass(),
                self._single_pass(human_authority=True),
            ]
        )

        report = self._resolve(include_items=True)

        self.assertEqual(report["safe_count"], 0)
        self.assertEqual(report["unresolved_count"], 1)
        audit = report["unresolved"][0]["history_audit"]
        self.assertEqual(audit["human_pixel_authority_rows"], 1)
        self.assertTrue(audit["historical_binding_or_contamination_conflict"])

    def test_binding_mismatch_is_unsafe(self):
        self._make_fixture(
            [self._single_pass() for _ in range(3)],
            queue_updates={"binding_key": "f" * 64},
        )

        report = self._resolve(include_items=True)

        self.assertEqual(report["safe_count"], 0)
        self.assertEqual(report["unresolved_count"], 1)
        self.assertIn(
            "binding_key does not match",
            report["unresolved"][0]["reason"],
        )

    def test_apply_enqueues_before_terminal_then_removes_queue(self):
        fixture = self._make_fixture([self._single_pass() for _ in range(3)])
        events = []

        def enqueue(record, *, output_dir):
            events.append("enqueue")
            self.assertEqual(json.loads(self.result.read_text(encoding="utf-8")), [])
            queue = json.loads(self.queue_path.read_text(encoding="utf-8"))
            self.assertEqual(len(queue["items"]), 1)
            job = output_dir / "pending" / f"{record['source_item_id']}.json"
            job.parent.mkdir(parents=True, exist_ok=True)
            job.write_text("{}", encoding="utf-8")
            return job

        report = self._resolve(apply=True, enqueue_fn=enqueue)
        events.append("returned")

        self.assertEqual(events, ["enqueue", "returned"])
        self.assertEqual(report["status"], "resolved")
        self.assertEqual(report["enqueued_count"], 1)
        self.assertEqual(report["terminal_appended_count"], 1)
        self.assertEqual(report["queue_removed_count"], 1)
        tasks = json.loads(self.result.read_text(encoding="utf-8"))
        self.assertEqual(len(tasks), 1)
        self.assertEqual(
            Path(tasks[0]["data"]["image"].replace("\\", "/")).name,
            fixture["file_name"],
        )
        self.assertTrue(tasks[0]["data"]["ocr_meta"]["auto_verified"])
        queue = json.loads(self.queue_path.read_text(encoding="utf-8"))
        self.assertEqual(queue["items"], [])

    def test_apply_guard_failure_prevents_every_write(self):
        self._make_fixture([self._single_pass() for _ in range(3)])
        before_result = self.result.read_bytes()
        before_queue = self.queue_path.read_bytes()

        def blocked(**_kwargs):
            raise RuntimeError("backend is not quiesced")

        def forbidden_enqueue(*_args, **_kwargs):
            self.fail("failed quiescence proof must precede enqueue")

        with self.assertRaisesRegex(RuntimeError, "not quiesced"):
            self._resolve(
                apply=True,
                apply_guard_fn=blocked,
                enqueue_fn=forbidden_enqueue,
            )

        self.assertEqual(self.result.read_bytes(), before_result)
        self.assertEqual(self.queue_path.read_bytes(), before_queue)

    def test_apply_aborts_when_pause_changes_before_terminal_write(self):
        self._make_fixture([self._single_pass() for _ in range(3)])
        before_result = self.result.read_bytes()
        before_queue = self.queue_path.read_bytes()
        calls = 0

        def changing_guard(**_kwargs):
            nonlocal calls
            calls += 1
            return {
                "pipeline_pause": {
                    "schema": "samsung-ocr-pipeline-pause/v1",
                    "current_dir": str(self.staging.resolve()),
                    "paused_at": f"call-{calls}",
                },
                "backend_reload_required_before_resume": True,
            }

        def forbidden_enqueue(*_args, **_kwargs):
            self.fail("changed pause must be detected before enqueue")

        with self.assertRaisesRegex(RuntimeError, "pause changed"):
            self._resolve(
                apply=True,
                apply_guard_fn=changing_guard,
                enqueue_fn=forbidden_enqueue,
            )

        self.assertEqual(self.result.read_bytes(), before_result)
        self.assertEqual(self.queue_path.read_bytes(), before_queue)

    def test_enqueue_failure_leaves_result_and_queue_membership_unchanged(self):
        fixture = self._make_fixture([self._single_pass() for _ in range(3)])
        before_result = self.result.read_bytes()

        def fail_enqueue(*_args, **_kwargs):
            raise RuntimeError("simulated enqueue failure")

        report = self._resolve(apply=True, enqueue_fn=fail_enqueue)

        self.assertEqual(report["status"], "partial_failure")
        self.assertEqual(report["terminal_appended_count"], 0)
        self.assertEqual(report["queue_removed_count"], 0)
        self.assertEqual(self.result.read_bytes(), before_result)
        queue = json.loads(self.queue_path.read_text(encoding="utf-8"))
        self.assertEqual(len(queue["items"]), 1)
        item = queue["items"][0]
        self.assertEqual(item["file_name"], fixture["file_name"])
        self.assertEqual(item["source_item_id"], fixture["source_id"])
        self.assertEqual(
            item["deferred_resolution_reason_key"],
            "upload_enqueue_failed",
        )
        self.assertIn(
            "simulated enqueue failure",
            item["deferred_resolution_reason"],
        )

    def test_apply_revalidates_source_bytes_after_guard(self):
        fixture = self._make_fixture([self._single_pass() for _ in range(3)])

        def mutate_after_initial_scan(**_kwargs):
            fixture["staged"].write_bytes(b"changed-after-initial-scan")
            return {"backend_reload_required_before_resume": True}

        def forbidden_enqueue(*_args, **_kwargs):
            self.fail("changed bytes must never be enqueued")

        report = self._resolve(
            apply=True,
            apply_guard_fn=mutate_after_initial_scan,
            enqueue_fn=forbidden_enqueue,
        )

        self.assertEqual(report["status"], "partial_failure")
        self.assertEqual(report["enqueued_count"], 0)
        self.assertEqual(len(report["apply_revalidation_failures"]), 1)
        queue = json.loads(self.queue_path.read_text(encoding="utf-8"))
        self.assertEqual(len(queue["items"]), 1)
        self.assertEqual(
            queue["items"][0]["deferred_resolution_reason_key"],
            "apply_revalidation_failed",
        )

    def test_existing_terminal_requires_exact_source_and_pixel_binding(self):
        self._make_fixture([self._single_pass() for _ in range(3)])
        queue_before = self.queue_path.read_bytes()

        def enqueue(record, *, output_dir):
            job = output_dir / "pending" / f"{record['source_item_id']}.json"
            job.parent.mkdir(parents=True, exist_ok=True)
            job.write_text("{}", encoding="utf-8")
            return job

        first = self._resolve(apply=True, enqueue_fn=enqueue)
        self.assertEqual(first["queue_removed_count"], 1)
        self.queue_path.write_bytes(queue_before)
        tasks = json.loads(self.result.read_text(encoding="utf-8"))
        tasks[0]["data"]["ocr_meta"]["source_item_id"] = "f" * 64
        self.result.write_text(
            json.dumps(tasks, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        def forbidden_enqueue(*_args, **_kwargs):
            self.fail("an unbound existing terminal must be isolated before enqueue")

        second = self._resolve(apply=True, enqueue_fn=forbidden_enqueue)

        self.assertEqual(second["status"], "partial_failure")
        self.assertEqual(second["enqueued_count"], 0)
        self.assertEqual(
            second["apply_revalidation_failures"][0]["reason_key"],
            "existing_terminal_binding_conflict",
        )
        queue = json.loads(self.queue_path.read_text(encoding="utf-8"))
        self.assertEqual(len(queue["items"]), 1)
        self.assertEqual(
            queue["items"][0]["deferred_resolution_reason_key"],
            "existing_terminal_binding_conflict",
        )

    def test_apply_persists_insufficient_run_reason_without_upload(self):
        fixture = self._make_fixture([self._single_pass() for _ in range(2)])
        before_result = self.result.read_bytes()

        def forbidden_enqueue(*_args, **_kwargs):
            self.fail("an unsafe item must not be enqueued")

        report = self._resolve(apply=True, enqueue_fn=forbidden_enqueue)

        self.assertEqual(report["status"], "resolved")
        self.assertEqual(report["safe_count"], 0)
        self.assertEqual(report["unresolved_count"], 1)
        self.assertEqual(report["enqueued_count"], 0)
        self.assertEqual(report["terminal_appended_count"], 0)
        self.assertEqual(report["queue_removed_count"], 0)
        self.assertEqual(self.result.read_bytes(), before_result)
        queue = json.loads(self.queue_path.read_text(encoding="utf-8"))
        self.assertEqual(len(queue["items"]), 1)
        item = queue["items"][0]
        self.assertEqual(item["file_name"], fixture["file_name"])
        self.assertEqual(item["source_item_id"], fixture["source_id"])
        self.assertEqual(
            item["deferred_resolution_reason_key"],
            "insufficient_distinct_clean_bound_runs",
        )

    def test_post_response_narration_guard_rebuilds_bound_raw_votes(self):
        guarded = self._single_pass(
            runtime_healthy=False,
            runtime_reasons=["structured_narration_followme_conflict"],
        )
        self._make_fixture([dict(guarded) for _ in range(3)])

        report = self._resolve(include_items=True)

        self.assertEqual(report["safe_count"], 1)
        self.assertEqual(report["unresolved_count"], 0)
        self.assertEqual(
            report["candidates"][0]["history_audit"]["recovery_mode"],
            "request_bound_raw_post_response_guard",
        )
        self.assertEqual(report["model_calls_made"], 0)

    def test_two_current_bound_owned_identity_tail_closes_without_fourth_call(self):
        self._make_fixture(
            [
                self._single_pass(attempt=2),
                self._single_pass(attempt=3),
            ]
        )

        report = self._resolve(include_items=True)

        self.assertEqual(report["safe_count"], 1)
        self.assertEqual(report["unresolved_count"], 0)
        self.assertEqual(
            report["candidates"][0]["history_audit"]["recovery_mode"],
            "two_current_bound_owned_identity_tail",
        )
        self.assertEqual(report["model_calls_made"], 0)

    def test_two_current_bound_followme_tail_closes_with_direct_physical_evidence(self):
        physical = [
            {
                "cue": "white_vertical_stand",
                "same_subject": True,
                "strength": "strong",
            },
            {
                "cue": "round_base",
                "same_subject": True,
                "strength": "strong",
            },
            {
                "cue": "attached_followme_product_card",
                "same_subject": True,
                "strength": "strong",
            },
        ]
        self._make_fixture(
            [
                self._single_pass(
                    attempt=2,
                    model='FollowMe Pro M7 43"',
                    price="17990",
                    physical=physical,
                ),
                self._single_pass(
                    attempt=3,
                    model='FollowMe Pro M7 43"',
                    price="17990",
                    physical=physical,
                ),
            ]
        )

        report = self._resolve(include_items=True)

        self.assertEqual(report["safe_count"], 1)
        self.assertEqual(report["unresolved_count"], 0)
        candidate = report["candidates"][0]
        self.assertEqual(candidate["model"], 'FollowMe Pro M7 43"')
        self.assertEqual(candidate["price"], "17990")
        self.assertEqual(
            candidate["history_audit"]["recovery_mode"],
            "two_current_bound_followme_identity_tail",
        )
        self.assertEqual(report["model_calls_made"], 0)

    def test_exhausted_strong_first_distant_ignores_polluted_later_reply(self):
        self._make_fixture(
            [
                self._distant_pass(timestamp="2099-01-01T00:00:01+08:00"),
                self._single_pass(
                    timestamp="2099-01-01T00:00:02+08:00",
                    narration="感謝提醒，上一輪答案需要更正為單機。",
                ),
            ],
            queue_updates={"consumed_calls": 3},
        )

        report = self._resolve(include_items=True)

        self.assertEqual(report["safe_count"], 1)
        candidate = report["candidates"][0]
        self.assertEqual(candidate["view_type"], "遠景")
        self.assertIsNone(candidate["model"])
        self.assertIsNone(candidate["price"])
        self.assertEqual(
            candidate["adjudication_rule"],
            "three_call_exhausted_conservative_terminal",
        )
        self.assertEqual(candidate["model_outputs_available"], 1)
        self.assertEqual(
            candidate["history_audit"]["raw_slots_polluted"], 1
        )
        self.assertEqual(report["model_calls_made"], 0)

    def test_exhausted_strong_first_owned_single_keeps_exact_pair(self):
        self._make_fixture(
            [
                self._single_pass(
                    model="S27D300GAC",
                    price="3090",
                    timestamp="2099-01-01T00:00:01+08:00",
                ),
                self._distant_pass(
                    timestamp="2099-01-01T00:00:02+08:00",
                    narration="您指正得對，先前答案應改成遠景。",
                ),
            ],
            queue_updates={"consumed_calls": 3},
        )

        report = self._resolve(include_items=True)

        candidate = report["candidates"][0]
        self.assertEqual(candidate["view_type"], "單機")
        self.assertEqual(candidate["model"], "S27D300GAC")
        self.assertEqual(candidate["price"], "3090")
        self.assertEqual(candidate["hard_cap_consumed_attempts"], 3)
        self.assertFalse(report["fourth_call_authorized"])

    def test_exhausted_unpolluted_majority_closes_view_and_clears_fields(self):
        rows = [
            self._single_pass(
                model="S27D300GAC",
                price="3090",
                count=3,
                unique=True,
                request_verified=False,
                timestamp="2099-01-01T00:00:01+08:00",
            ),
            self._distant_pass(
                request_verified=False,
                timestamp="2099-01-01T00:00:02+08:00",
            ),
            self._distant_pass(
                request_verified=False,
                timestamp="2099-01-01T00:00:03+08:00",
            ),
        ]
        self._make_fixture(rows, queue_updates={"consumed_calls": 3})

        report = self._resolve(include_items=True)

        candidate = report["candidates"][0]
        self.assertEqual(candidate["view_type"], "遠景")
        self.assertIsNone(candidate["model"])
        self.assertIsNone(candidate["price"])
        self.assertEqual(
            candidate["adjudication_decision_source"],
            "unpolluted_raw_view_majority",
        )

    def test_exhausted_repeated_followme_text_without_physical_clears_only_model(self):
        self._make_fixture(
            [
                self._single_pass(
                    model='FollowMe M7 32"',
                    price="12990",
                    timestamp="2099-01-01T00:00:01+08:00",
                ),
                self._single_pass(
                    model='FollowMe M7 32"',
                    price="12990",
                    timestamp="2099-01-01T00:00:02+08:00",
                ),
            ],
            queue_updates={"consumed_calls": 3},
        )

        report = self._resolve(include_items=True)

        self.assertEqual(report["safe_count"], 1)
        candidate = report["candidates"][0]
        self.assertEqual(candidate["view_type"], "單機")
        self.assertIsNone(candidate["model"])
        self.assertEqual(candidate["price"], "12990")
        self.assertEqual(report["model_calls_made"], 0)

    def test_exhausted_without_coherent_geometry_stays_unresolved(self):
        self._make_fixture(
            [
                self._single_pass(
                    count=3,
                    unique=True,
                    request_verified=False,
                    timestamp="2099-01-01T00:00:01+08:00",
                )
            ],
            queue_updates={"consumed_calls": 3},
        )

        report = self._resolve(include_items=True)

        self.assertEqual(report["safe_count"], 0)
        self.assertEqual(report["unresolved_count"], 1)
        self.assertIn("coherent view geometry", report["unresolved"][0]["reason"])
        self.assertEqual(report["model_calls_made"], 0)

    def test_legacy_complete_three_attempt_run_uses_raw_not_old_terminal(self):
        fixture = self._make_fixture(
            [
                self._distant_pass(
                    run_id="legacy-run",
                    attempt=attempt,
                    timestamp=f"2099-01-01T00:00:0{attempt}+08:00",
                )
                for attempt in (1, 2, 3)
            ]
        )
        rows = [json.loads(line) for line in self.trace.read_text(encoding="utf-8").splitlines()]
        for row in rows:
            raw = json.loads(row["raw_objects"][0])
            raw.pop("request_id", None)
            row["raw_objects"] = [json.dumps(raw, ensure_ascii=False)]
            parsed = row["parsed_output"]
            for field in (
                "input_image_sha256",
                "request_id_verified",
                "request_binding_enforced",
                "independent_pass",
                "prior_answer_exposed",
                "prompt_contamination",
                "runtime_health",
            ):
                parsed.pop(field, None)
            # Simulate the historical synthesis bug; the resolver must ignore
            # these parsed fields and retain the unanimous raw distant votes.
            parsed.update({"view_type": "單機", "model": "FollowMe M7", "price": "17990"})
        self.trace.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )

        report = self._resolve(include_items=True)

        self.assertEqual(report["safe_count"], 1)
        self.assertEqual(report["unresolved_count"], 0)
        candidate = report["candidates"][0]
        self.assertEqual(candidate["file_name"], fixture["file_name"])
        self.assertEqual(candidate["view_type"], "遠景")
        self.assertIsNone(candidate["model"])
        self.assertIsNone(candidate["price"])
        self.assertEqual(
            candidate["history_audit"]["recovery_mode"],
            "legacy_exact_path_unchanged_raw_three_attempts",
        )

    def test_legacy_rebind_rejects_source_modified_after_trace(self):
        self._make_fixture(
            [
                self._distant_pass(
                    run_id="legacy-run",
                    attempt=attempt,
                    timestamp=f"2020-01-01T00:00:0{attempt}+08:00",
                )
                for attempt in (1, 2, 3)
            ]
        )
        rows = [json.loads(line) for line in self.trace.read_text(encoding="utf-8").splitlines()]
        for row in rows:
            raw = json.loads(row["raw_objects"][0])
            raw.pop("request_id", None)
            row["raw_objects"] = [json.dumps(raw, ensure_ascii=False)]
            for field in (
                "input_image_sha256",
                "request_id_verified",
                "request_binding_enforced",
                "independent_pass",
                "prior_answer_exposed",
                "prompt_contamination",
                "runtime_health",
            ):
                row["parsed_output"].pop(field, None)
        self.trace.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )

        report = self._resolve(include_items=True)

        self.assertEqual(report["safe_count"], 0)
        self.assertEqual(report["unresolved_count"], 1)
        self.assertIn("changed after", report["unresolved"][0]["reason"])


if __name__ == "__main__":
    unittest.main()

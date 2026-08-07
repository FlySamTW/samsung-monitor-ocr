import hashlib
import json
import tempfile
import unittest
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rerun_staged_candidates as mod
import rerun_questionable_records as questionable


class AttachExistingTests(unittest.TestCase):
    @staticmethod
    def write_success_tasks(staging: Path, records: list[dict[str, object]]) -> Path:
        tasks = []
        for index, record in enumerate(records, start=1):
            name = str(record["file_name"])
            view = str(record.get("view_type") or "單機")
            tasks.append(
                {
                    "id": index,
                    "data": {
                        "image": f"/data/upload/1/{name}",
                        "ocr_meta": {
                            "view_type": view,
                            "auto_verified": True,
                            "evidence_contract_valid": True,
                        },
                    },
                    "annotations": [
                        {
                            "id": index,
                            "created_at": f"2026-07-29T00:00:{index:02d}",
                            "result": [
                                {
                                    "from_name": "category",
                                    "value": {"choices": [view]},
                                },
                                {
                                    "from_name": "model",
                                    "value": {"text": [str(record.get("model") or "null")]},
                                },
                                {
                                    "from_name": "price",
                                    "value": {"text": [str(record.get("price") or "null")]},
                                },
                            ],
                        }
                    ],
                }
            )
        path = staging / "session-OCR成功.json"
        path.write_text(
            json.dumps(tasks, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def write_source_map(staging: Path, names: list[str]) -> Path:
        path = staging / ".ocr_source_map.json"
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "items": {
                        name: {
                            "source_item_id": hashlib.sha256(
                                name.encode("utf-8")
                            ).hexdigest(),
                            "original_source_path": str((staging / name).resolve()),
                        }
                        for name in names
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def write_capped_queue(staging: Path, names: list[str]) -> Path:
        items = []
        for index, name in enumerate(names, start=1):
            staged = staging / name
            if not staged.exists():
                staged.write_bytes(f"staged-{name}".encode("utf-8"))
            items.append(
                {
                    "file_name": name,
                    "source_item_id": hashlib.sha256(name.encode("utf-8")).hexdigest(),
                    "source_path": str(staged.resolve()),
                    "input_image_sha256": f"{index:064x}",
                }
            )
        path = staging / ".ocr_capped_adjudication_queue.json"
        path.write_text(
            json.dumps(
                {
                    "schema": "samsung-ocr-capped-adjudication-queue/v1",
                    "image_dir": str(staging.resolve()),
                    "items": items,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return path

    def test_quality_guard_rejects_structured_single_when_own_narration_concludes_distant(self):
        records = [{
            "file_name": "wall.jpg",
            "view_type": "單機",
            "category": "單機",
            "model": None,
            "price": None,
            "thinking": "可見多台完整螢幕，無法鎖定唯一主角，整體符合「遠景」條件。",
            "raw_model_output": '{"view_type":"遠景","model":null,"price":null}',
        }]
        args = SimpleNamespace(
            min_completion_ratio=0.98,
            min_quality_guard_records=20,
            max_single_missing_ratio=0.65,
        )
        reason, details = mod.abort_reason_for_rerun(args, records, {"wall.jpg"}, [])
        self.assertEqual(reason, "structured_narration_conflict")
        self.assertEqual(details["conflicting_records"], 1)

    def test_quality_guard_keeps_review_contained_conflict_without_stopping_batch(self):
        records = [{
            "file_name": "wall.jpg",
            "view_type": "單機",
            "category": "單機",
            "model": None,
            "price": None,
            "thinking": "可見多台完整螢幕，無法鎖定唯一主角，整體符合「遠景」條件。",
            "raw_model_output": '{"view_type":"遠景","model":null,"price":null}',
            "auto_review_required": True,
            "review_status": "需慢模型或人工校正",
        }]
        args = SimpleNamespace(
            min_completion_ratio=0.98,
            min_quality_guard_records=20,
            max_single_missing_ratio=0.65,
        )
        reason, details = mod.abort_reason_for_rerun(args, records, {"wall.jpg"}, [])
        self.assertEqual(reason, "")
        self.assertEqual(details, {"matched_records": 1})
        self.assertEqual(
            mod.contained_structured_narration_conflicts(records, {"wall.jpg"}),
            ["wall.jpg"],
        )

    def test_quality_guard_understands_serialized_review_flag(self):
        record = {
            "file_name": "wall.jpg",
            "view_type": "單機",
            "thinking": "整體符合遠景條件。",
            "auto_review_required": "true",
        }
        self.assertTrue(mod.is_explicitly_contained_for_review(record))

    def test_wait_tolerates_transient_status_failures(self):
        done = {"is_running": False, "stats": {"processed": 1, "total": 1, "success": 1, "failed": 0}}
        with patch.object(questionable, "json_request", side_effect=[OSError("temporary"), done]) as request:
            result = questionable.wait_for_folder_done(
                "http://mock", Path("group"), 1, 0, max_consecutive_status_errors=2, retry_sleep_seconds=0
            )
        self.assertEqual(result, done)
        self.assertEqual(request.call_count, 2)

    def test_wait_refuses_idle_incomplete_folder_instead_of_advancing(self):
        incomplete = {
            "is_running": False,
            "stats": {"processed": 1, "total": 3, "success": 1, "failed": 0},
        }
        with patch.object(questionable, "json_request", return_value=incomplete):
            with self.assertRaisesRegex(
                RuntimeError,
                "preserve staging and do not advance",
            ):
                questionable.wait_for_folder_done(
                    "http://mock",
                    Path("group"),
                    1,
                    0,
                    max_consecutive_status_errors=1,
                    retry_sleep_seconds=0,
                )

    def test_wait_accepts_exact_terminal_plus_capped_partition(self):
        settled = {
            "is_running": False,
            "stats": {"processed": 1, "total": 3, "success": 1, "failed": 0},
            "capped_adjudication": {"count": 2},
        }
        with patch.object(questionable, "json_request", return_value=settled):
            result = questionable.wait_for_folder_done(
                "http://mock",
                Path("group"),
                1,
                0,
                max_consecutive_status_errors=1,
                retry_sleep_seconds=0,
            )
        self.assertEqual(result, settled)

    def test_group_disk_guard_refuses_before_copying(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "one.jpg"
            source.write_bytes(b"photo")
            staging = root / "staging"
            staging.mkdir()
            args = SimpleNamespace(staging_root=str(staging), keep_staging=False)
            disk = SimpleNamespace(
                free=mod.STAGING_FREE_RESERVE_BYTES + source.stat().st_size - 1,
            )
            with patch.object(mod.shutil, "disk_usage", return_value=disk), patch.object(
                mod, "stage_images"
            ) as stage:
                summary = mod.run_group(
                    args,
                    str(root),
                    str(root / "audit"),
                    "202605",
                    [{"source_path": str(source)}],
                    1,
                    1,
                    "stamp",
                )
            stage.assert_not_called()
        self.assertEqual(summary["aborted"], 1)
        self.assertIn("staging_disk_guard", summary["abort_reason"])

    def test_resume_selects_active_group_and_only_later_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging_root = root / "staging"; staging_root.mkdir()
            groups = {}
            for period in ("202605", "202604", "202603"):
                source = root / period; source.mkdir()
                digest = __import__("hashlib").sha1(str(source.resolve()).encode()).hexdigest()[:8]
                groups[(str(source), str(root / f"audit-{period}"), period)] = [{"period": period}]
                if period == "202604":
                    current = staging_root / f"{period}_demo_{digest}"; current.mkdir()
            active, remaining = mod.split_groups_at_current_staging(
                {"current_relative_dir": str(current)}, groups, staging_root
            )
        self.assertEqual([key[2] for key in active], ["202604"])
        self.assertEqual([key[2] for key, _rows in remaining], ["202603"])

    def test_period_priority_stage_matches_durable_audit_folder_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "商化照片-202606"
            source.mkdir()
            audit = root / "202606_8ae67c526e285b524d08822d0767b17e_商化照片-202606"
            audit.mkdir()
            stage = root / "202606_商化照片-202606_8ae67c52"
            stage.mkdir()
            self.assertTrue(
                mod._staging_dir_matches_group(stage, "202606", source, audit)
            )
            self.assertFalse(
                mod._staging_dir_matches_group(stage, "202605", source, audit)
            )

    def test_resume_restores_dashboard_before_cleaning_active_staging_and_skips_prior_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging_root = root / "staging"; staging_root.mkdir()
            groups = {}
            sources = {}
            current = None
            for period in ("202605", "202604", "202603"):
                source = root / period; source.mkdir(); sources[period] = source
                digest = __import__("hashlib").sha1(str(source.resolve()).encode()).hexdigest()[:8]
                groups[(str(source), str(root / f"audit-{period}"), period)] = [{"period": period}]
                if period == "202604":
                    current = staging_root / f"{period}_demo_{digest}"; current.mkdir()
            args = SimpleNamespace(
                backend_url="http://mock", staging_root=str(staging_root), keep_staging=False,
                run_summary_csv=str(root / "summary.csv"), max_folders=0, max_per_folder=0,
            )
            status = {"current_relative_dir": str(current)}
            active_summary = {"staging_dir": str(current), "period": "202604"}
            later_summary = {"staging_dir": str(staging_root / "later"), "period": "202603"}
            with patch.object(mod, "json_request", return_value=status), \
                 patch.object(mod, "attach_existing_group", return_value=active_summary) as attach, \
                 patch.object(mod, "restore_backend_work_dir") as restore, \
                 patch.object(mod, "run_group", return_value=later_summary) as run, \
                 patch.object(mod.shutil, "rmtree") as remove, \
                 patch.object(mod, "write_dict_csv"):
                summaries = mod.resume_existing_then_continue(args, groups, "stamp")
        self.assertEqual([row["period"] for row in summaries], ["202604", "202603"])
        self.assertEqual(next(iter(attach.call_args.args[2]))[2], "202604")
        self.assertEqual(run.call_args.args[3], "202603")
        restore.assert_called_once_with("http://mock", sources["202604"])
        remove.assert_called_once_with(current, ignore_errors=True)
        self.assertFalse(args.keep_staging)

    def test_resume_starts_uniquely_matched_incomplete_active_group_before_attach(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging_root = root / "staging"
            staging_root.mkdir()
            source = root / "202601"
            source.mkdir()
            digest = __import__("hashlib").sha1(str(source.resolve()).encode()).hexdigest()[:8]
            current = staging_root / f"202601_demo_{digest}"
            current.mkdir()
            groups = {(str(source), str(root / "audit"), "202601"): [{"period": "202601"}]}
            args = SimpleNamespace(
                backend_url="http://mock", staging_root=str(staging_root), keep_staging=True,
                run_summary_csv=str(root / "summary.csv"), max_folders=0, max_per_folder=0,
            )
            status = {
                "is_running": False,
                "current_relative_dir": str(current),
                "stats": {"processed": 829, "total": 1504},
            }
            started = {"status": "started"}
            running = {
                "is_running": True,
                "current_relative_dir": str(current),
                "stats": {"processed": 829, "total": 1504},
            }
            active_summary = {"staging_dir": str(current), "period": "202601"}
            with patch.object(mod, "json_request", side_effect=[status, started, running]) as request, \
                 patch.object(mod, "attach_existing_group", return_value=active_summary) as attach, \
                 patch.object(mod, "write_dict_csv"):
                summaries = mod.resume_existing_then_continue(args, groups, "stamp")
            self.assertEqual(summaries, [active_summary])
            self.assertEqual(request.call_args_list[1].args[1], "/api/start_batch")
            self.assertEqual(request.call_args_list[1].args[2]["dir"], str(current.resolve()))
            self.assertTrue(request.call_args_list[1].args[2]["confirmed"])
            self.assertEqual(request.call_args_list[2].args[1], "/api/status")
            attach.assert_called_once()

    def test_resume_start_wait_tolerates_async_idle_gap(self):
        idle = {"is_running": False, "stats": {"processed": 2, "total": 3}}
        running = {"is_running": True, "stats": {"processed": 2, "total": 3}}
        with patch.object(mod, "json_request", side_effect=[idle, running]) as request, \
             patch.object(mod.time, "sleep"):
            result = mod.wait_for_resume_start("http://mock", timeout_seconds=2)
        self.assertIs(result, running)
        self.assertEqual(request.call_count, 2)

    def test_resume_start_treats_processed_plus_capped_as_finished(self):
        settled = {
            "is_running": False,
            "stats": {"processed": 2, "total": 3, "success": 2, "failed": 0},
            "capped_adjudication": {"count": 1},
        }
        with patch.object(mod, "json_request", return_value=settled) as request, \
             patch.object(mod.time, "sleep") as sleep:
            result = mod.wait_for_resume_start(
                "http://mock",
                timeout_seconds=1,
                poll_seconds=0,
            )
        self.assertIs(result, settled)
        request.assert_called_once_with("http://mock", "/api/status", timeout=30)
        sleep.assert_not_called()

    def test_group_finalizer_stops_resolves_and_reattaches_from_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            staging = output / "_ocr_staging" / "202602_demo"
            staging.mkdir(parents=True)
            first = staging / "one.jpg"
            second = staging / "two.jpg"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            self.write_source_map(staging, [first.name, second.name])
            self.write_capped_queue(staging, [second.name])
            success = self.write_success_tasks(
                staging,
                [
                    {
                        "file_name": first.name,
                        "view_type": "單機",
                        "model": "S27D300GAC",
                        "price": "3090",
                    }
                ],
            )
            trace = output / "_ocr_audit" / "v1945_evidence_trace.jsonl"
            trace.parent.mkdir(parents=True)
            trace.write_text("", encoding="utf-8")
            status = {
                "is_running": False,
                "current_file": "None",
                "current_relative_dir": str(staging),
                "stats": {"processed": 1, "total": 2},
                "capped_adjudication": {"count": 1},
            }
            pause = {
                "schema": "samsung-ocr-pipeline-pause/v1",
                "current_dir": str(staging),
                "reason": "capped_zero_model_adjudication_apply",
            }
            idle = {**status, "pipeline_pause": pause}
            args = SimpleNamespace(
                backend_url="http://mock",
                output_dir=str(output),
            )

            resolve_calls = []

            def resolve(**kwargs):
                resolve_calls.append(bool(kwargs["apply"]))
                self.assertEqual(kwargs["result_file"], success)
                if not kwargs["apply"]:
                    return {
                        "status": "dry_run",
                        "safe_count": 1,
                        "model_calls_made": 0,
                        "fourth_call_authorized": False,
                    }
                tasks = json.loads(success.read_text(encoding="utf-8"))
                added_path = self.write_success_tasks(
                    staging,
                    [
                        {
                            "file_name": second.name,
                            "view_type": "遠景",
                            "model": None,
                            "price": None,
                        }
                    ],
                )
                added = json.loads(added_path.read_text(encoding="utf-8"))
                success.write_text(
                    json.dumps(tasks + added, ensure_ascii=False),
                    encoding="utf-8",
                )
                queue = json.loads(
                    (staging / ".ocr_capped_adjudication_queue.json").read_text(
                        encoding="utf-8"
                    )
                )
                queue["items"] = []
                (staging / ".ocr_capped_adjudication_queue.json").write_text(
                    json.dumps(queue, ensure_ascii=False),
                    encoding="utf-8",
                )
                return {
                    "status": "resolved",
                    "safe_count": 1,
                    "model_calls_made": 0,
                    "fourth_call_authorized": False,
                    "apply_revalidation_failure_count": 0,
                    "enqueue_failures": [],
                }

            resumed = {
                **status,
                "pipeline_pause": None,
                "stats": {"processed": 2, "total": 2},
                "capped_adjudication": {"count": 0},
            }
            with patch.object(
                mod,
                "json_request",
                side_effect=[
                    {"status": "stopped", "pipeline_pause": pause},
                    idle,
                    {"status": "started"},
                    resumed,
                ],
            ) as request, patch.object(
                mod,
                "resolve_capped_queue",
                side_effect=resolve,
            ) as resolver:
                finalized = mod.finalize_capped_group(args, staging, status)

            self.assertEqual(finalized["source_names"], {"one.jpg", "two.jpg"})
            self.assertEqual(finalized["record_names"], {"one.jpg", "two.jpg"})
            self.assertEqual(finalized["capped_names"], set())
            self.assertEqual(
                [call.args[1] for call in request.call_args_list],
                ["/api/stop", "/api/status", "/api/start_batch", "/api/status"],
            )
            self.assertEqual(resolve_calls, [False, True])
            self.assertEqual(resolver.call_count, 2)

    def test_group_finalizer_resumes_when_one_upload_intent_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            staging = output / "_ocr_staging" / "202601_partial_enqueue"
            staging.mkdir(parents=True)
            first = staging / "one.jpg"
            second = staging / "two.jpg"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            self.write_source_map(staging, [first.name, second.name])
            self.write_capped_queue(staging, [first.name, second.name])
            trace = output / "_ocr_audit" / "v1945_evidence_trace.jsonl"
            trace.parent.mkdir(parents=True)
            trace.write_text("", encoding="utf-8")
            status = {
                "is_running": False,
                "current_file": "None",
                "current_relative_dir": str(staging),
                "stats": {"processed": 0, "total": 2},
                "capped_adjudication": {"count": 2},
            }
            pause = {
                "schema": "samsung-ocr-pipeline-pause/v1",
                "current_dir": str(staging),
                "reason": "capped_zero_model_adjudication_apply",
            }
            idle = {**status, "pipeline_pause": pause}
            args = SimpleNamespace(
                backend_url="http://mock",
                output_dir=str(output),
            )

            def resolve(**kwargs):
                if not kwargs["apply"]:
                    return {
                        "status": "dry_run",
                        "safe_count": 2,
                        "model_calls_made": 0,
                        "fourth_call_authorized": False,
                    }
                result_file = kwargs["result_file"]
                result_file.write_text(
                    self.write_success_tasks(
                        staging,
                        [
                            {
                                "file_name": first.name,
                                "view_type": "遠景",
                                "model": None,
                                "price": None,
                            }
                        ],
                    ).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
                queue = json.loads(
                    (staging / ".ocr_capped_adjudication_queue.json").read_text(
                        encoding="utf-8"
                    )
                )
                queue["items"] = [
                    item
                    for item in queue["items"]
                    if item["file_name"] == second.name
                ]
                (staging / ".ocr_capped_adjudication_queue.json").write_text(
                    json.dumps(queue, ensure_ascii=False),
                    encoding="utf-8",
                )
                return {
                    "status": "partial_failure",
                    "safe_count": 2,
                    "queue_remaining_count": 1,
                    "enqueued_count": 1,
                    "model_calls_made": 0,
                    "fourth_call_authorized": False,
                    "enqueue_failures": [
                        {
                            "file_name": second.name,
                            "reason": "different upload job",
                        }
                    ],
                }

            resumed = {
                **status,
                "pipeline_pause": None,
                "stats": {"processed": 1, "total": 2},
                "capped_adjudication": {"count": 1},
            }
            with patch.object(
                mod,
                "json_request",
                side_effect=[
                    {"status": "stopped", "pipeline_pause": pause},
                    idle,
                    {"status": "started"},
                    resumed,
                ],
            ), patch.object(
                mod,
                "resolve_capped_queue",
                side_effect=resolve,
            ):
                finalized = mod.finalize_capped_group(args, staging, status)

            self.assertEqual(finalized["record_names"], {first.name})
            self.assertEqual(finalized["capped_names"], {second.name})
            self.assertTrue(
                finalized["resolver_report"]["checkpoint_auto_resumed"]
            )

    def test_group_finalizer_creates_empty_terminal_container_when_all_are_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            staging = output / "_ocr_staging" / "202601_all_capped"
            staging.mkdir(parents=True)
            image = staging / "one.jpg"
            image.write_bytes(b"one")
            self.write_source_map(staging, [image.name])
            self.write_capped_queue(staging, [image.name])
            trace = output / "_ocr_audit" / "v1945_evidence_trace.jsonl"
            trace.parent.mkdir(parents=True)
            trace.write_text("", encoding="utf-8")
            status = {
                "is_running": False,
                "current_file": "None",
                "current_relative_dir": str(staging),
                "stats": {"processed": 0, "total": 1},
                "capped_adjudication": {"count": 1},
            }
            pause = {
                "schema": "samsung-ocr-pipeline-pause/v1",
                "current_dir": str(staging),
                "reason": "capped_zero_model_adjudication_apply",
            }
            idle = {**status, "pipeline_pause": pause}
            args = SimpleNamespace(
                backend_url="http://mock",
                output_dir=str(output),
            )

            resolve_calls = []

            def resolve(**kwargs):
                resolve_calls.append(bool(kwargs["apply"]))
                result_file = kwargs["result_file"]
                self.assertEqual(result_file.parent, staging)
                self.assertTrue(result_file.name.endswith("OCR成功.json"))
                if not kwargs["apply"]:
                    self.assertFalse(result_file.exists())
                    return {
                        "status": "dry_run",
                        "safe_count": 1,
                        "model_calls_made": 0,
                        "fourth_call_authorized": False,
                    }
                self.assertEqual(
                    json.loads(result_file.read_text(encoding="utf-8")),
                    [],
                )
                replacement = self.write_success_tasks(
                    staging,
                    [
                        {
                            "file_name": image.name,
                            "view_type": "遠景",
                            "model": None,
                            "price": None,
                        }
                    ],
                )
                replacement.replace(result_file)
                queue = json.loads(
                    (staging / ".ocr_capped_adjudication_queue.json").read_text(
                        encoding="utf-8"
                    )
                )
                queue["items"] = []
                (staging / ".ocr_capped_adjudication_queue.json").write_text(
                    json.dumps(queue, ensure_ascii=False),
                    encoding="utf-8",
                )
                return {
                    "status": "resolved",
                    "safe_count": 1,
                    "model_calls_made": 0,
                    "fourth_call_authorized": False,
                    "apply_revalidation_failure_count": 0,
                    "enqueue_failures": [],
                }

            resumed = {
                **status,
                "pipeline_pause": None,
                "stats": {"processed": 1, "total": 1},
                "capped_adjudication": {"count": 0},
            }
            with patch.object(
                mod,
                "json_request",
                side_effect=[
                    {"status": "stopped", "pipeline_pause": pause},
                    idle,
                    {"status": "started"},
                    resumed,
                ],
            ), patch.object(
                mod,
                "resolve_capped_queue",
                side_effect=resolve,
            ) as resolver:
                finalized = mod.finalize_capped_group(args, staging, status)

            self.assertEqual(finalized["record_names"], {image.name})
            self.assertEqual(finalized["capped_names"], set())
            self.assertEqual(resolve_calls, [False, True])
            self.assertEqual(resolver.call_count, 2)

    def test_group_finalizer_keeps_unresolved_capped_rows_without_global_pause(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            staging = output / "_ocr_staging" / "202605_unresolved"
            staging.mkdir(parents=True)
            image = staging / "one.jpg"
            image.write_bytes(b"one")
            self.write_source_map(staging, [image.name])
            self.write_capped_queue(staging, [image.name])
            self.write_success_tasks(staging, [])
            trace = output / "_ocr_audit" / "v1945_evidence_trace.jsonl"
            trace.parent.mkdir(parents=True)
            trace.write_text("", encoding="utf-8")
            status = {
                "is_running": False,
                "current_file": "None",
                "current_relative_dir": str(staging),
                "pipeline_pause": None,
                "stats": {"processed": 0, "total": 1},
                "capped_adjudication": {"count": 1},
            }
            args = SimpleNamespace(
                backend_url="http://mock",
                output_dir=str(output),
            )
            preflight = {
                "status": "dry_run",
                "safe_count": 0,
                "unresolved_count": 1,
                "model_calls_made": 0,
                "fourth_call_authorized": False,
            }
            with patch.object(
                mod,
                "resolve_capped_queue",
                return_value=preflight,
            ) as resolver, patch.object(
                mod,
                "json_request",
            ) as request:
                finalized = mod.finalize_capped_group(args, staging, status)

            resolver.assert_called_once()
            request.assert_not_called()
            self.assertEqual(finalized["record_names"], set())
            self.assertEqual(finalized["capped_names"], {image.name})
            self.assertEqual(
                finalized["resolver_report"]["status"],
                "deferred_unresolved",
            )
            self.assertFalse(
                finalized["resolver_report"]["checkpoint_auto_resumed"]
            )

    def test_group_finalizer_clears_legacy_pause_when_nothing_is_safe_to_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            staging = output / "_ocr_staging" / "202605_legacy_pause"
            staging.mkdir(parents=True)
            image = staging / "one.jpg"
            image.write_bytes(b"one")
            self.write_source_map(staging, [image.name])
            self.write_capped_queue(staging, [image.name])
            self.write_success_tasks(staging, [])
            trace = output / "_ocr_audit" / "v1945_evidence_trace.jsonl"
            trace.parent.mkdir(parents=True)
            trace.write_text("", encoding="utf-8")
            pause = {
                "schema": "samsung-ocr-pipeline-pause/v1",
                "current_dir": str(staging),
                "reason": "capped_zero_model_adjudication_apply",
            }
            status = {
                "is_running": False,
                "current_file": "None",
                "current_relative_dir": str(staging),
                "pipeline_pause": pause,
                "stats": {"processed": 0, "total": 1},
                "capped_adjudication": {"count": 1},
            }
            resumed = {**status, "pipeline_pause": None}
            args = SimpleNamespace(
                backend_url="http://mock",
                output_dir=str(output),
            )
            preflight = {
                "status": "dry_run",
                "safe_count": 0,
                "unresolved_count": 1,
                "model_calls_made": 0,
                "fourth_call_authorized": False,
            }
            with patch.object(
                mod,
                "resolve_capped_queue",
                return_value=preflight,
            ), patch.object(
                mod,
                "json_request",
                side_effect=[{"status": "started"}, resumed],
            ) as request:
                finalized = mod.finalize_capped_group(args, staging, status)

            self.assertEqual(
                [call.args[1] for call in request.call_args_list],
                ["/api/start_batch", "/api/status"],
            )
            self.assertTrue(
                finalized["resolver_report"]["checkpoint_auto_resumed"]
            )

    def test_group_finalizer_failure_keeps_pause_checkpoint_and_never_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            staging = output / "_ocr_staging" / "202602_demo"
            staging.mkdir(parents=True)
            image = staging / "one.jpg"
            image.write_bytes(b"one")
            self.write_source_map(staging, [image.name])
            self.write_capped_queue(staging, [image.name])
            self.write_success_tasks(staging, [])
            trace = output / "_ocr_audit" / "v1945_evidence_trace.jsonl"
            trace.parent.mkdir(parents=True)
            trace.write_text("", encoding="utf-8")
            status = {
                "is_running": False,
                "current_file": "None",
                "current_relative_dir": str(staging),
                "stats": {"processed": 0, "total": 1},
                "capped_adjudication": {"count": 1},
            }
            pause = {
                "schema": "samsung-ocr-pipeline-pause/v1",
                "current_dir": str(staging),
            }
            idle = {**status, "pipeline_pause": pause}
            args = SimpleNamespace(
                backend_url="http://mock",
                output_dir=str(output),
            )
            with patch.object(
                mod,
                "json_request",
                side_effect=[
                    {"status": "stopped", "pipeline_pause": pause},
                    idle,
                ],
            ) as request, patch.object(
                mod,
                "resolve_capped_queue",
                side_effect=RuntimeError("resolver failed closed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "failed closed"):
                    mod.finalize_capped_group(args, staging, status)
            self.assertTrue(staging.is_dir())
            self.assertTrue(
                (staging / ".ocr_capped_adjudication_queue.json").is_file()
            )
            self.assertNotIn(
                "/api/start_batch",
                [call.args[1] for call in request.call_args_list],
            )

    def test_attach_accepts_exact_success_and_capped_filename_union(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "photos"
            source.mkdir()
            first = source / "one.jpg"
            second = source / "two.jpg"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            output = root / "out"
            audit = output / "_ocr_audit" / "202601_demo"
            audit.mkdir(parents=True)
            (audit / "success_records.csv").write_text(
                "file_name,view_type,model,price\n",
                encoding="utf-8",
            )
            staging_root = output / "_ocr_staging"
            digest = hashlib.sha1(str(source.resolve()).encode()).hexdigest()[:8]
            staging = staging_root / f"202601_demo_{digest}"
            staging.mkdir(parents=True)
            self.write_capped_queue(staging, [second.name])
            self.write_source_map(staging, [first.name, second.name])
            args = SimpleNamespace(
                staging_root=str(staging_root),
                backend_url="http://mock",
                timeout_minutes=1,
                poll_seconds=0,
                run_summary_csv=str(root / "summary.csv"),
                keep_staging=False,
                dry_run=True,
                output_dir=str(output),
                price_symbol="$",
                min_completion_ratio=0.98,
                min_quality_guard_records=20,
                max_single_missing_ratio=0.65,
            )
            rows = [
                {
                    "source_path": str(image),
                    "source_folder": str(source),
                    "audit_folder": str(audit),
                    "period": "202601",
                    "file_name": image.name,
                }
                for image in (first, second)
            ]
            grouped = {(str(source), str(audit), "202601"): rows}
            status = {
                "is_running": False,
                "current_relative_dir": str(staging),
                "stats": {"processed": 1, "total": 2, "success": 1, "failed": 0},
                # The compact API intentionally exposes at most the latest 20
                # items.  Exact partitioning must use the durable queue file.
                "capped_adjudication": {"count": 1},
            }
            records = [
                {
                    "file_name": first.name,
                    "view_type": "單機",
                    "model": "X",
                    "price": "100",
                }
            ]
            self.assertEqual(
                mod._durable_capped_adjudication_names(staging, status),
                {second.name},
            )
            finalized = {
                "records": records,
                "record_names": {first.name},
                "source_names": {first.name, second.name},
                "capped_names": {second.name},
                "resolver_report": {"safe_count": 0},
            }
            with patch.object(
                mod, "json_request", return_value=status
            ) as request, patch.object(
                mod, "finalize_capped_group", return_value=finalized
            ), patch.object(
                mod, "abort_reason_for_rerun", return_value=("", {"matched_records": 1})
            ), patch.object(
                mod, "rebuild_outputs", return_value={"records": 1}
            ), patch.object(
                mod.shutil, "rmtree"
            ) as remove, patch.object(
                mod, "write_dict_csv"
            ):
                result = mod.attach_existing_group(args, rows, grouped)

            self.assertEqual(result["processed"], 1)
            self.assertEqual(
                [call.args[1] for call in request.call_args_list],
                ["/api/status"],
            )
            remove.assert_not_called()
            self.assertTrue(staging.is_dir())

    def test_attach_rejects_when_success_and_capped_union_is_not_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "photos"
            source.mkdir()
            first = source / "one.jpg"
            second = source / "two.jpg"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            staging_root = root / "staging"
            digest = hashlib.sha1(str(source.resolve()).encode()).hexdigest()[:8]
            staging = staging_root / f"202601_demo_{digest}"
            staging.mkdir(parents=True)
            self.write_capped_queue(staging, ["unexpected.jpg"])
            self.write_source_map(staging, [first.name, second.name])
            audit = root / "audit"
            audit.mkdir()
            args = SimpleNamespace(
                staging_root=str(staging_root),
                backend_url="http://mock",
                timeout_minutes=1,
                poll_seconds=0,
                run_summary_csv=str(root / "summary.csv"),
                keep_staging=True,
            )
            rows = [
                {
                    "source_path": str(image),
                    "source_folder": str(source),
                    "audit_folder": str(audit),
                    "period": "202601",
                    "file_name": image.name,
                }
                for image in (first, second)
            ]
            grouped = {(str(source), str(audit), "202601"): rows}
            status = {
                "is_running": False,
                "current_relative_dir": str(staging),
                "stats": {"processed": 1, "total": 2},
                "capped_adjudication": {"count": 1},
            }
            records = [{"file_name": first.name, "view_type": "單機"}]
            inconsistent = {
                "records": records,
                "record_names": {first.name},
                "source_names": {first.name},
                "capped_names": {"unexpected.jpg"},
                "resolver_report": {"safe_count": 0},
            }
            with patch.object(mod, "json_request", return_value=status), patch.object(
                mod, "finalize_capped_group", return_value=inconsistent
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "filenames do not exactly",
                ):
                    mod.attach_existing_group(args, rows, grouped)

    def test_resume_preserves_capped_staging_and_continues_later_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging_root = root / "staging"
            staging_root.mkdir()
            groups = {}
            sources = {}
            current = None
            for period in ("202602", "202601"):
                source = root / period
                source.mkdir()
                sources[period] = source
                digest = hashlib.sha1(str(source.resolve()).encode()).hexdigest()[:8]
                groups[(str(source), str(root / f"audit-{period}"), period)] = [
                    {
                        "period": period,
                        "source_path": str(source / f"{period}.jpg"),
                    }
                ]
                if period == "202602":
                    current = staging_root / f"{period}_demo_{digest}"
                    current.mkdir()
            self.write_capped_queue(current, ["202602.jpg"])
            args = SimpleNamespace(
                backend_url="http://mock",
                staging_root=str(staging_root),
                keep_staging=False,
                run_summary_csv=str(root / "summary.csv"),
                max_folders=0,
                max_per_folder=0,
            )
            status = {
                "is_running": False,
                "current_relative_dir": str(current),
                "stats": {"processed": 0, "total": 1},
                "capped_adjudication": {"count": 1},
            }
            active_summary = {
                "staging_dir": str(current),
                "period": "202602",
                "deferred_capped": 1,
            }
            later_summary = {
                "staging_dir": str(staging_root / "later"),
                "period": "202601",
            }
            with patch.object(mod, "json_request", return_value=status) as request, \
                 patch.object(
                     mod, "attach_existing_group", return_value=active_summary
                 ) as attach, patch.object(
                     mod, "restore_backend_work_dir"
                 ) as restore, patch.object(
                     mod, "run_group", return_value=later_summary
                 ) as run, patch.object(
                     mod.shutil, "rmtree"
                 ) as remove, patch.object(
                     mod, "write_dict_csv"
                 ):
                summaries = mod.resume_existing_then_continue(args, groups, "stamp")

            self.assertEqual(
                [row["period"] for row in summaries],
                ["202602", "202601"],
            )
            request.assert_called_once_with("http://mock", "/api/status", timeout=30)
            attach.assert_called_once()
            restore.assert_called_once_with("http://mock", sources["202602"])
            remove.assert_not_called()
            self.assertTrue(current.is_dir())
            self.assertEqual(run.call_args.args[3], "202601")
            self.assertFalse(args.keep_staging)

    def test_attach_polls_and_never_starts_or_switches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "photos"; source.mkdir()
            image = source / "one.jpg"; image.write_bytes(b"x")
            output = root / "out"; audit = output / "_ocr_audit" / "202601_demo"; audit.mkdir(parents=True)
            (audit / "success_records.csv").write_text("file_name,view_type,model,price\none.jpg,單機,X,100\n", encoding="utf-8")
            staging_root = output / "_ocr_staging"
            digest = __import__("hashlib").sha1(str(source.resolve()).encode()).hexdigest()[:8]
            staging = staging_root / f"202601_demo_{digest}"
            staging.mkdir(parents=True)
            self.write_source_map(staging, [image.name])
            args = SimpleNamespace(
                staging_root=str(staging_root), backend_url="http://mock", timeout_minutes=1,
                poll_seconds=0, run_summary_csv=str(root / "summary.csv"), keep_staging=True,
                dry_run=True, output_dir=str(output), price_symbol="$", min_completion_ratio=0.98,
                min_quality_guard_records=20, max_single_missing_ratio=0.65,
            )
            rows = [{"source_path": str(image), "source_folder": str(source), "audit_folder": str(audit), "period": "202601", "file_name": "one.jpg"}]
            grouped = {(str(source), str(audit), "202601"): rows}
            status = {"is_running": True, "current_relative_dir": str(staging), "stats": {"processed": 0, "total": 1}}
            done = {"is_running": False, "current_relative_dir": str(staging), "stats": {"processed": 1, "total": 1}}
            records = [{"file_name": "one.jpg", "view_type": "單機", "model": "X", "price": "100"}]
            finalized = {
                "records": records,
                "record_names": {"one.jpg"},
                "source_names": {"one.jpg"},
                "capped_names": set(),
                "resolver_report": {"safe_count": 0},
            }
            with patch.object(mod, "json_request", return_value=status) as request, patch.object(mod, "wait_for_folder_done", return_value=done), patch.object(mod, "finalize_capped_group", return_value=finalized), patch.object(mod, "rebuild_outputs", return_value={"records": 1}), patch.object(mod, "write_dict_csv") as write:
                result = mod.attach_existing_group(args, rows, grouped)
            self.assertEqual(result["processed"], 1)
            calls = [call.args[1] for call in request.call_args_list]
            self.assertEqual(calls, ["/api/status"])
            write.assert_called()

    def test_attach_uses_staged_source_map_when_refreshed_csv_drops_terminal_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "photos"
            source.mkdir()
            first = source / "one.jpg"
            second = source / "two.jpg"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            output = root / "out"
            audit = output / "_ocr_audit" / "202601_demo"
            audit.mkdir(parents=True)
            (audit / "success_records.csv").write_text(
                "file_name,view_type,model,price\n",
                encoding="utf-8",
            )
            staging_root = output / "_ocr_staging"
            digest = hashlib.sha1(str(source.resolve()).encode()).hexdigest()[:8]
            staging = staging_root / f"202601_demo_{digest}"
            staging.mkdir(parents=True)
            self.write_source_map(staging, [first.name, second.name])
            args = SimpleNamespace(
                staging_root=str(staging_root),
                backend_url="http://mock",
                timeout_minutes=1,
                poll_seconds=0,
                run_summary_csv=str(root / "summary.csv"),
                keep_staging=True,
                dry_run=True,
                output_dir=str(output),
                price_symbol="$",
                min_completion_ratio=0.98,
                min_quality_guard_records=20,
                max_single_missing_ratio=0.65,
            )
            # The refreshed input no longer includes ``one.jpg`` because it
            # already became terminal, but the original staged inventory does.
            rows = [{
                "source_path": str(second),
                "source_folder": str(source),
                "audit_folder": str(audit),
                "period": "202601",
                "file_name": second.name,
            }]
            grouped = {(str(source), str(audit), "202601"): rows}
            status = {
                "is_running": False,
                "current_relative_dir": str(staging),
                "stats": {"processed": 2, "total": 2, "success": 2, "failed": 0},
                "capped_adjudication": {"count": 0},
            }
            records = [
                {"file_name": first.name, "view_type": "單機", "model": "A", "price": "100"},
                {"file_name": second.name, "view_type": "單機", "model": "B", "price": "200"},
            ]
            finalized = {
                "records": records,
                "record_names": {first.name, second.name},
                "source_names": {first.name, second.name},
                "capped_names": set(),
                "resolver_report": {"safe_count": 0},
            }
            with patch.object(
                mod, "json_request", return_value=status
            ), patch.object(
                mod, "finalize_capped_group", return_value=finalized
            ), patch.object(
                mod, "abort_reason_for_rerun", return_value=("", {"matched_records": 2})
            ), patch.object(
                mod, "rebuild_outputs", return_value={"records": 2}
            ), patch.object(
                mod, "write_dict_csv"
            ):
                result = mod.attach_existing_group(args, rows, grouped)
            self.assertEqual(result["queued"], 1)
            self.assertEqual(result["staged"], 2)

    def test_attach_rejects_multiple_groups_before_api(self):
        args = SimpleNamespace(backend_url="http://mock", staging_root="C:/out/_ocr_staging")
        groups = {("a", "b", "202601"): [], ("c", "d", "202601"): []}
        with patch.object(mod, "json_request") as request:
            with self.assertRaises(RuntimeError):
                mod.attach_existing_group(args, [], groups)
            request.assert_not_called()

    def test_settled_eligible_system_error_recovers_once_in_same_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = root / "staging"
            staging.mkdir()
            first = staging / "one.jpg"
            failed = staging / "two.jpg"
            first.write_bytes(b"one")
            failed.write_bytes(b"two")
            self.write_source_map(staging, [first.name, failed.name])
            args = SimpleNamespace(
                backend_url="http://mock",
                output_dir=str(root / "output"),
                timeout_minutes=1,
                poll_seconds=0,
            )
            initial = {
                "is_running": False,
                "current_relative_dir": str(staging),
                "stats": {"processed": 2, "total": 2, "failed": 1},
                "capped_adjudication": {"count": 0},
            }
            idle = {
                **initial,
                "current_file": None,
                "pipeline_pause": {"current_dir": str(staging)},
            }
            after_retry = {
                **initial,
                "stats": {"processed": 2, "total": 2, "failed": 0},
            }
            first_record = {"file_name": first.name}
            recovered_record = {"file_name": failed.name}
            terminal = {
                "records": [first_record, recovered_record],
                "record_names": {first.name, failed.name},
                "source_names": {first.name, failed.name},
                "capped_names": set(),
                "resolver_report": {"safe_count": 0},
            }

            def request(_url, endpoint, payload=None, timeout=30):
                if endpoint == "/api/stop":
                    return {"status": "stopped"}
                if endpoint == "/api/status":
                    return idle
                if endpoint == "/api/start_batch":
                    self.assertEqual(payload["dir"], str(staging.resolve()))
                    self.assertFalse(payload["restart"])
                    return {"status": "started"}
                raise AssertionError(endpoint)

            with patch.object(
                mod,
                "load_group_records_from_disk",
                side_effect=[[first_record], [first_record, recovered_record]],
            ), patch.object(
                mod,
                "build_preinference_system_error_recovery",
                return_value={"targets": [failed.name]},
            ) as build, patch.object(
                mod,
                "apply_preinference_system_error_recovery",
                return_value=root / "manifest.json",
            ) as apply, patch.object(
                mod,
                "json_request",
                side_effect=request,
            ) as api, patch.object(
                mod,
                "wait_for_resume_start",
            ) as wait_start, patch.object(
                mod,
                "wait_for_folder_done",
                return_value=after_retry,
            ) as wait_done, patch.object(
                mod,
                "_finalize_capped_group_after_technical_recovery",
                return_value=terminal,
            ) as finalize:
                result = mod.finalize_capped_group(args, staging, initial)

            build.assert_called_once_with(
                staging.resolve(),
                (Path(args.output_dir).resolve() / "_ocr_audit"),
                [failed.name],
            )
            apply.assert_called_once()
            wait_start.assert_called_once_with(args.backend_url)
            wait_done.assert_called_once_with(
                args.backend_url,
                staging.resolve(),
                args.timeout_minutes,
                args.poll_seconds,
            )
            finalize.assert_called_once_with(args, staging.resolve(), after_retry)
            self.assertEqual(result["preinference_recovered_names"], [failed.name])
            self.assertEqual(
                [call.args[1] for call in api.call_args_list],
                ["/api/stop", "/api/status", "/api/start_batch"],
            )
            self.assertNotIn("/api/rerun", [call.args[1] for call in api.call_args_list])

    def test_same_system_error_after_bounded_retry_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = root / "staging"
            staging.mkdir()
            good = staging / "one.jpg"
            failed = staging / "two.jpg"
            good.write_bytes(b"one")
            failed.write_bytes(b"two")
            self.write_source_map(staging, [good.name, failed.name])
            args = SimpleNamespace(
                backend_url="http://mock",
                output_dir=str(root / "output"),
                timeout_minutes=1,
                poll_seconds=0,
            )
            status = {
                "is_running": False,
                "current_relative_dir": str(staging),
                "stats": {"processed": 2, "total": 2, "failed": 1},
                "capped_adjudication": {"count": 0},
            }
            idle = {
                **status,
                "current_file": None,
                "pipeline_pause": {"current_dir": str(staging)},
            }
            record = {"file_name": good.name}

            def request(_url, endpoint, payload=None, timeout=30):
                if endpoint == "/api/stop":
                    return {"status": "stopped"}
                if endpoint == "/api/status":
                    return idle
                if endpoint == "/api/start_batch":
                    return {"status": "started"}
                raise AssertionError(endpoint)

            with patch.object(
                mod,
                "load_group_records_from_disk",
                side_effect=[[record], [record]],
            ), patch.object(
                mod,
                "build_preinference_system_error_recovery",
                return_value={"targets": [failed.name]},
            ) as build, patch.object(
                mod,
                "apply_preinference_system_error_recovery",
                return_value=root / "manifest.json",
            ) as apply, patch.object(
                mod,
                "json_request",
                side_effect=request,
            ) as api, patch.object(
                mod,
                "wait_for_resume_start",
            ), patch.object(
                mod,
                "wait_for_folder_done",
                return_value=status,
            ), patch.object(
                mod,
                "_finalize_capped_group_after_technical_recovery",
            ) as finalize:
                with self.assertRaisesRegex(RuntimeError, "failed again"):
                    mod.finalize_capped_group(args, staging, status)

            build.assert_called_once()
            apply.assert_called_once()
            finalize.assert_not_called()
            self.assertEqual(
                [call.args[1] for call in api.call_args_list].count("/api/start_batch"),
                1,
            )
            self.assertTrue(staging.is_dir())

    def test_unknown_missing_failure_keeps_exact_pause_and_does_not_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = root / "staging"
            staging.mkdir()
            failed = staging / "unknown.jpg"
            failed.write_bytes(b"unknown")
            self.write_source_map(staging, [failed.name])
            args = SimpleNamespace(
                backend_url="http://mock",
                output_dir=str(root / "output"),
                timeout_minutes=1,
                poll_seconds=0,
            )
            status = {
                "is_running": False,
                "current_relative_dir": str(staging),
                "stats": {"processed": 1, "total": 1, "failed": 1},
                "capped_adjudication": {"count": 0},
            }
            idle = {
                **status,
                "current_file": None,
                "pipeline_pause": {"current_dir": str(staging)},
            }

            def request(_url, endpoint, payload=None, timeout=30):
                if endpoint == "/api/stop":
                    return {"status": "stopped"}
                if endpoint == "/api/status":
                    return idle
                raise AssertionError(f"unexpected restart: {endpoint}")

            with patch.object(
                mod,
                "load_group_records_from_disk",
                return_value=[],
            ), patch.object(
                mod,
                "build_preinference_system_error_recovery",
                side_effect=RuntimeError(
                    "expected one exact eligible pre-inference failure"
                ),
            ) as build, patch.object(
                mod,
                "apply_preinference_system_error_recovery",
            ) as apply, patch.object(
                mod,
                "json_request",
                side_effect=request,
            ) as api, patch.object(
                mod,
                "_finalize_capped_group_after_technical_recovery",
            ) as finalize:
                with self.assertRaisesRegex(RuntimeError, "exact eligible"):
                    mod.finalize_capped_group(args, staging, status)

            build.assert_called_once()
            apply.assert_not_called()
            finalize.assert_not_called()
            self.assertEqual(
                [call.args[1] for call in api.call_args_list],
                ["/api/stop", "/api/status"],
            )
            self.assertTrue(staging.is_dir())

    def test_group_candidates_uses_valid_bound_source_path_without_tree_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "商化照片-202601"
            source.mkdir()
            image = source / "one.jpg"
            image.write_bytes(b"photo")
            rows = [{
                "source_path": str(image),
                "file_name": image.name,
                "period": "202601",
                "audit_folder": str(root / "audit"),
            }]
            with patch.object(mod, "resolve_source_path", side_effect=AssertionError("tree scan must not run")):
                grouped, skipped = mod.group_candidates(rows, root, [])
            self.assertEqual(skipped, 0)
            self.assertEqual(len(grouped), 1)
            key = next(iter(grouped))
            self.assertEqual(Path(key[0]), source.resolve())
            self.assertEqual(grouped[key][0]["source_path"], str(image.resolve()))


if __name__ == "__main__":
    unittest.main()

"""Regression tests for durable per-photo presentation history."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from threading import RLock
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import samsung_ocr_batch_processor as backend
from skills.batch_orchestrator import BatchOrchestrator
from tools.rerun_staged_candidates import stage_images


class PresentationHistoryTests(unittest.TestCase):
    def test_queue_event_prefers_same_result_detailed_narration(self):
        orchestrator = BatchOrchestrator.__new__(BatchOrchestrator)
        orchestrator.config = {"model_id": "local-model", "accuracy_profile": "strict"}
        orchestrator.last_model_name = "local-model"
        orchestrator.presentation_sequence = 0
        orchestrator.display_queue = []
        orchestrator._state_lock = RLock()
        result = {
            "file_name": "wall.jpg",
            "source_path": "D:/photos/wall.jpg",
            "run_id": "run-1",
            "view_type": "遠景",
            "category": "遠景",
            "model": None,
            "price": None,
            "thinking": "可見三台以上完整螢幕，且無法鎖定唯一主角，因此符合遠景條件。",
        }
        with patch.object(orchestrator, "_append_presentation_audit"):
            event = orchestrator.queue_presentation_event(
                result=result,
                attempt=1,
                started_at="2026-07-14T10:00:00",
                completed_at="2026-07-14T10:00:10",
                previous_results=[],
                retry_reasons=[],
                decision="accepted",
                narration="這張已完成辨識：遠景，無型號，無價格。",
            )
        self.assertEqual(event["narration"], result["thinking"])
        self.assertEqual(event["stream_buffer"], result["thinking"])
        self.assertEqual(event["full_ai_narration"], result["thinking"])
        self.assertEqual(event["run_id"], "run-1")

    def test_live_status_presentation_window_is_small_and_has_no_inline_images(self):
        class FakeOrchestrator:
            is_running = True
            recent_results = []
            display_queue = [
                {
                    "presentation_id": f"p-{index:09d}",
                    "presentation_sequence": index,
                    "source_item_id": f"{index:064x}",
                    "file_name": f"photo-{index}.jpg",
                    "source_path": f"D:/photos/photo-{index}.jpg",
                    "run_id": "run-live",
                    "evidence_guard_revision": "20260715.11",
                    "narration": "判讀摘要" * 40,
                    "thumb_b64": "MUST_NOT_LEAK" * 10000,
                    "raw_model_output": "MUST_NOT_LEAK",
                    "result": {
                        "view_type": "單機",
                        "model": f"M{index}",
                        "auto_verified": True,
                        "auto_review_required": False,
                        "review_status": "待審核",
                        "thumb_b64": "MUST_NOT_LEAK",
                        "raw_objects": ["MUST_NOT_LEAK"],
                    },
                }
                for index in range(40)
            ]

        previous_events = backend._presentation_events
        previous_next_id = backend._presentation_next_id
        previous_by_source = backend._presentation_by_source
        backend._presentation_events = []
        backend._presentation_next_id = 0
        backend._presentation_by_source = {}
        try:
            items = backend._presentation_payload(FakeOrchestrator())
        finally:
            backend._presentation_events = previous_events
            backend._presentation_next_id = previous_next_id
            backend._presentation_by_source = previous_by_source

        encoded = json.dumps(items, ensure_ascii=False)
        self.assertEqual(len(items), backend.STATUS_PRESENTATION_WINDOW)
        self.assertLess(len(encoded.encode("utf-8")), 100_000)
        self.assertNotIn("MUST_NOT_LEAK", encoded)
        self.assertEqual(items[-1]["presentation_id"], "p-000000039")
        self.assertEqual(items[-1]["run_id"], "run-live")
        self.assertEqual(items[-1]["evidence_guard_revision"], "20260715.11")
        self.assertTrue(items[-1]["result"]["auto_verified"])
        self.assertFalse(items[-1]["result"]["auto_review_required"])

    def test_idle_or_new_batch_never_replays_prior_presentation_events(self):
        class IdleOrchestrator:
            is_running = False
            display_queue = []
            recent_results = [{"presentation_id": "p-stale", "file_name": "deleted-staging.jpg"}]

        class NewBatchOrchestrator:
            is_running = True
            recent_results = []
            display_queue = [{
                "presentation_id": "p-new",
                "presentation_sequence": 900,
                "file_name": "current-batch.jpg",
                "source_path": "D:/current/current-batch.jpg",
                "result": {"view_type": "單機", "model": "M7"},
            }]

        previous_events = backend._presentation_events
        try:
            backend._presentation_events = [{
                "presentation_id": "p-stale",
                "presentation_sequence": 899,
                "file_name": "deleted-staging.jpg",
            }]
            self.assertEqual(backend._presentation_payload(IdleOrchestrator()), [])
            self.assertEqual(
                [item["presentation_id"] for item in backend._presentation_payload(NewBatchOrchestrator())],
                ["p-new"],
            )
        finally:
            backend._presentation_events = previous_events

    def test_idle_status_keeps_durable_presentation_sequence(self):
        class IdleOrchestrator:
            is_running = False
            presentation_sequence = 13321
            display_queue = []
            recent_results = []
            stats = {"total": 0}
            stream_buffer = ""
            current_file = None
            stream_file = None
            image_dir = None
            system_logs = []

            def get_performance_metrics(self):
                return {}

            def get_all_records(self):
                return []

            def get_all_failed_records(self):
                return []

        previous = backend.orchestrator
        backend.orchestrator = IdleOrchestrator()
        try:
            response = backend.flask_app.test_client().get("/api/status")
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload["presentation_sequence"], 13321)
            self.assertTrue(payload["presentation_sequence_durable"])
            self.assertEqual(payload["presentation_queue"], [])
        finally:
            backend.orchestrator = previous

    def test_staging_source_map_preserves_original_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "商化照片-202604" / "M-test-1.jpg"
            source.parent.mkdir()
            source.write_bytes(b"image-placeholder")
            staging = root / "staging"
            audit_folder = root / "_ocr_audit" / "202604_sample"
            rows = [{
                "source_path": str(source),
                "period": "202604",
                "audit_folder": str(audit_folder),
            }]
            self.assertEqual(stage_images(rows, staging), 1)
            payload = json.loads((staging / ".ocr_source_map.json").read_text(encoding="utf-8"))
            metadata = payload["items"][source.name]
            self.assertEqual(metadata["original_source_path"], str(source.resolve()))
            self.assertEqual(metadata["period"], "202604")
            self.assertEqual(len(metadata["source_item_id"]), 64)

    def test_disk_and_live_history_merge_without_image_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_root = Path(tmp)
            history_dir = audit_root / "presentation_history"
            history_dir.mkdir(parents=True)
            source_id = "a" * 64
            disk_event = {
                "presentation_id": "p-disk",
                "presentation_sequence": 1,
                "source_item_id": source_id,
                "pass_index": 1,
                "narration": "第一輪",
                "thumb_b64": "MUST_NOT_LEAK",
                "result": {"view_type": "單機"},
            }
            (history_dir / "presentation_20260714.jsonl").write_text(
                json.dumps(disk_event, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            orchestrator = BatchOrchestrator.__new__(BatchOrchestrator)
            orchestrator.config = {"audit_dir": str(audit_root)}
            orchestrator.output_dir = str(audit_root)
            orchestrator._state_lock = RLock()
            orchestrator.display_queue = [{
                "presentation_id": "p-live",
                "presentation_sequence": 2,
                "source_item_id": source_id,
                "pass_index": 2,
                "narration": "第二輪",
                "thumb_b64": "MUST_NOT_LEAK",
                "result": {"view_type": "單機"},
            }]

            items = orchestrator.get_presentation_history(source_id, limit=12)
            self.assertEqual([item["presentation_id"] for item in items], ["p-disk", "p-live"])
            self.assertTrue(all("thumb_b64" not in item for item in items))
            self.assertNotIn("MUST_NOT_LEAK", json.dumps(items, ensure_ascii=False))

    def test_presentation_sequence_recovers_highest_durable_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_root = Path(tmp)
            history_dir = audit_root / "presentation_history"
            history_dir.mkdir(parents=True)
            (history_dir / "presentation_20260715.jsonl").write_text(
                "\n".join([
                    json.dumps({"presentation_id": "p-1", "presentation_sequence": 41}),
                    "not-json",
                    json.dumps({"presentation_id": "p-2", "presentation_sequence": 1031}),
                ]) + "\n",
                encoding="utf-8",
            )
            orchestrator = BatchOrchestrator.__new__(BatchOrchestrator)
            orchestrator.config = {"audit_dir": str(audit_root)}
            orchestrator.output_dir = str(audit_root)
            self.assertEqual(orchestrator._load_presentation_sequence(), 1031)

    def test_presentation_sequence_counts_passes_after_process_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_root = Path(tmp)
            history_dir = audit_root / "presentation_history"
            history_dir.mkdir(parents=True)
            (history_dir / "presentation_20260715.jsonl").write_text(
                "\n".join([
                    json.dumps({"presentation_id": "p-old-1", "presentation_sequence": 1029}),
                    json.dumps({"presentation_id": "p-old-2", "presentation_sequence": 1031}),
                    json.dumps({"presentation_id": "p-new-1", "presentation_sequence": 1}),
                    "not-json",
                    json.dumps({"presentation_id": "p-new-2", "presentation_sequence": 2}),
                ]) + "\n",
                encoding="utf-8",
            )
            orchestrator = BatchOrchestrator.__new__(BatchOrchestrator)
            orchestrator.config = {"audit_dir": str(audit_root)}
            orchestrator.output_dir = str(audit_root)
            self.assertEqual(orchestrator._load_presentation_sequence(), 1033)

    def test_presentation_sequence_does_not_readd_legacy_segment_after_second_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_root = Path(tmp)
            history_dir = audit_root / "presentation_history"
            history_dir.mkdir(parents=True)
            (history_dir / "presentation_20260715.jsonl").write_text(
                "\n".join([
                    json.dumps({"presentation_id": "p-old-1", "presentation_sequence": 1029}),
                    json.dumps({"presentation_id": "p-old-2", "presentation_sequence": 1031}),
                    json.dumps({"presentation_id": "p-reset-1", "presentation_sequence": 1}),
                    json.dumps({"presentation_id": "p-reset-2", "presentation_sequence": 2}),
                    # A fixed service loaded 1033 and then persisted absolute
                    # cumulative values.  These must replace, not re-add, the
                    # legacy 1031-event segment on the next restart.
                    json.dumps({"presentation_id": "p-durable-1", "presentation_sequence": 1034}),
                    json.dumps({"presentation_id": "p-durable-latest", "presentation_sequence": 14401}),
                ]) + "\n",
                encoding="utf-8",
            )
            orchestrator = BatchOrchestrator.__new__(BatchOrchestrator)
            orchestrator.config = {"audit_dir": str(audit_root)}
            orchestrator.output_dir = str(audit_root)
            self.assertEqual(orchestrator._load_presentation_sequence(), 14401)

    def test_recent_history_restores_disk_and_live_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_root = Path(tmp)
            history_dir = audit_root / "presentation_history"
            history_dir.mkdir(parents=True)
            disk_items = [
                {
                    "presentation_id": "p-old",
                    "presentation_sequence": 500,
                    "source_item_id": "a" * 64,
                    "run_id": "run-old",
                    "file_name": "old.jpg",
                    "source_path": "D:/photos/old.jpg",
                    "completed_at": "2026-07-14T23:59:00",
                    "thumb_b64": "MUST_NOT_LEAK",
                    "result": {"view_type": "遠景"},
                },
                {
                    "presentation_id": "p-new-process",
                    "presentation_sequence": 1,
                    "source_item_id": "b" * 64,
                    "run_id": "run-new",
                    "file_name": "new.jpg",
                    "source_path": "D:/photos/new.jpg",
                    "completed_at": "2026-07-15T00:01:00",
                    "result": {"view_type": "單機", "model": "S27D300GAC"},
                },
            ]
            (history_dir / "presentation_20260715.jsonl").write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in disk_items) + "\n{broken",
                encoding="utf-8",
            )
            orchestrator = BatchOrchestrator.__new__(BatchOrchestrator)
            orchestrator.config = {"audit_dir": str(audit_root)}
            orchestrator.output_dir = str(audit_root)
            orchestrator._state_lock = RLock()
            orchestrator.display_queue = [dict(disk_items[1])]

            items = orchestrator.get_recent_presentation_history(limit=10)
            self.assertEqual([item["presentation_id"] for item in items], ["p-new-process", "p-old"])
            self.assertNotIn("MUST_NOT_LEAK", json.dumps(items, ensure_ascii=False))
            self.assertEqual(items[0]["source_path"], "D:/photos/new.jpg")

            scoped = orchestrator.get_recent_presentation_history(limit=10, source_item_ids={"b" * 64})
            self.assertEqual([item["presentation_id"] for item in scoped], ["p-new-process"])
            run_scoped = orchestrator.get_recent_presentation_history(
                limit=10,
                source_item_ids={"a" * 64, "b" * 64},
                run_id="run-new",
                latest_run_only=True,
            )
            self.assertEqual([item["presentation_id"] for item in run_scoped], ["p-new-process"])
            orchestrator.display_queue.append({
                "presentation_id": "p-legacy",
                "presentation_sequence": 0,
                "source_item_id": "a" * 64,
                "file_name": "legacy.jpg",
                "source_path": "D:/photos/legacy.jpg",
                "completed_at": "2026-07-13T00:00:00",
                "result": {"view_type": "遠景"},
            })
            legacy_scoped = orchestrator.get_recent_presentation_history(
                limit=10,
                source_item_ids={"a" * 64, "b" * 64},
                legacy_run_only=True,
            )
            self.assertEqual([item["presentation_id"] for item in legacy_scoped], ["p-legacy"])

    def test_work_dir_switch_clears_cross_folder_state_and_recovers_run_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_dir = root / "old"
            new_dir = root / "new"
            old_dir.mkdir()
            new_dir.mkdir()
            orchestrator = BatchOrchestrator.__new__(BatchOrchestrator)
            orchestrator.image_dir = str(old_dir)
            orchestrator.config = {"image_dir": str(old_dir)}
            orchestrator._state_lock = RLock()
            orchestrator._worker_thread = None
            orchestrator.is_running = False
            orchestrator.priority_queue = ["old.jpg"]
            orchestrator.retry_queue = ["old.jpg"]
            orchestrator.session_processed = {"old.jpg"}
            orchestrator.current_file = "old.jpg"
            orchestrator.stream_file = "old.jpg"
            orchestrator.latest_result_file = "old.jpg"
            orchestrator.stream_buffer = "old narration"
            orchestrator.display_queue = [{"file_name": "old.jpg"}]
            orchestrator.recent_results = [{"file_name": "old.jpg"}]
            orchestrator.session_results = [{"file_name": "old.jpg"}]
            orchestrator.auto_attempts = {"old.jpg": 3}
            orchestrator.auto_result_history = {"old.jpg": [{}]}

            orchestrator._persist_presentation_run_id(new_dir, "run-new-folder")
            changed, error = orchestrator.set_work_dir(str(new_dir))

            self.assertTrue(changed, error)
            self.assertEqual(orchestrator.current_run_id, "run-new-folder")
            self.assertIsNone(orchestrator.current_file)
            self.assertIsNone(orchestrator.stream_file)
            self.assertIsNone(orchestrator.latest_result_file)
            self.assertEqual(orchestrator.stream_buffer, "")
            self.assertEqual(orchestrator.display_queue, [])
            self.assertEqual(orchestrator.recent_results, [])
            self.assertEqual(orchestrator.priority_queue, [])

    def test_current_batch_without_marker_requests_legacy_history_only(self):
        class FakeOrchestrator:
            current_run_id = ""

            def get_current_source_item_ids(self):
                return {"c" * 64}

            def get_recent_presentation_history(self, **kwargs):
                self.kwargs = kwargs
                return []

        previous = backend.orchestrator
        fake = FakeOrchestrator()
        backend.orchestrator = fake
        try:
            response = backend.flask_app.test_client().get(
                "/api/presentation_history?limit=200&scope=current_batch"
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["run_id"], "")
            self.assertFalse(fake.kwargs["latest_run_only"])
            self.assertTrue(fake.kwargs["legacy_run_only"])
        finally:
            backend.orchestrator = previous

    def test_history_api_validates_id_and_limit(self):
        class FakeOrchestrator:
            def get_presentation_history(self, source_item_id, limit=12):
                return [{
                    "presentation_id": "p-1",
                    "presentation_sequence": 1,
                    "source_item_id": source_item_id,
                }][:limit]

        previous = backend.orchestrator
        backend.orchestrator = FakeOrchestrator()
        try:
            client = backend.flask_app.test_client()
            bad = client.get("/api/presentation_history/not-a-valid-id")
            self.assertEqual(bad.status_code, 400)
            bad_limit = client.get(f"/api/presentation_history/{'b' * 64}?limit=nope")
            self.assertEqual(bad_limit.status_code, 400)
            response = client.get(f"/api/presentation_history/{'b' * 64}?limit=999")
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload["source_item_id"], "b" * 64)
            self.assertEqual(payload["count"], 1)
        finally:
            backend.orchestrator = previous

    def test_recent_history_api_validates_limit_and_returns_collection(self):
        class FakeOrchestrator:
            def get_recent_presentation_history(self, limit=200):
                return [{"presentation_id": "p-1", "file_name": "one.jpg"}][:limit]

        previous = backend.orchestrator
        backend.orchestrator = FakeOrchestrator()
        try:
            client = backend.flask_app.test_client()
            bad = client.get("/api/presentation_history?limit=nope")
            self.assertEqual(bad.status_code, 400)
            response = client.get("/api/presentation_history?limit=999")
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["items"][0]["presentation_id"], "p-1")
        finally:
            backend.orchestrator = previous

    def test_recent_history_api_scopes_result_rail_to_current_batch(self):
        class FakeOrchestrator:
            current_run_id = "run-current"

            def get_current_source_item_ids(self):
                return {"b" * 64}

            def get_recent_presentation_history(self, limit=200, source_item_ids=None, run_id="", latest_run_only=False, legacy_run_only=False):
                self.received_scope = source_item_ids
                self.received_run_id = run_id
                self.received_latest_run_only = latest_run_only
                self.received_legacy_run_only = legacy_run_only
                return [{"presentation_id": "p-current", "source_item_id": "b" * 64}]

        previous = backend.orchestrator
        fake = FakeOrchestrator()
        backend.orchestrator = fake
        try:
            client = backend.flask_app.test_client()
            invalid = client.get("/api/presentation_history?scope=wrong")
            self.assertEqual(invalid.status_code, 400)
            response = client.get("/api/presentation_history?limit=200&scope=current_batch")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["scope"], "current_batch")
            self.assertEqual(response.get_json()["source_item_ids"], ["b" * 64])
            self.assertEqual(response.get_json()["run_id"], "run-current")
            self.assertEqual(fake.received_scope, {"b" * 64})
            self.assertEqual(fake.received_run_id, "run-current")
            self.assertTrue(fake.received_latest_run_only)
            self.assertFalse(fake.received_legacy_run_only)
        finally:
            backend.orchestrator = previous


if __name__ == "__main__":
    unittest.main(verbosity=2)

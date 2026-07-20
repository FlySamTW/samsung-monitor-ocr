from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from skills.audit_fields import EVIDENCE_GUARD_REVISION
from tools.continue_after_period_priority import (
    prepared_input_sha256,
    receipt_revision_is_proven,
)
from tools.recover_failed_fuse_uploads import (
    _canonical_sha256,
    apply_recovery,
    build_recovery_plan,
)
from tools.rclone_drive_upload import sha256_file
from tools.stream_drive_upload import (
    FUSE_UPLOAD_RECOVERY_SCHEMA,
    validate_fuse_failed_upload_recovery,
)


SOURCE_ID = "a" * 64
OLD_REVISION = "20260718.48"
RUN_ID = "run-bound-1"
FILE_NAME = "one.jpg"


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class FailedFuseUploadRecoveryTests(unittest.TestCase):
    def fixture(self, root: Path) -> dict[str, object]:
        priority = root / "priority"
        output = root / "output"
        source_root = root / "source"
        priority.mkdir()
        source_root.mkdir()
        source = source_root / FILE_NAME
        staging = priority / FILE_NAME
        Image.new("RGB", (8, 8), (10, 20, 30)).save(source, "JPEG")
        staging.write_bytes(source.read_bytes())
        source_sha = sha256_file(source)
        input_sha = prepared_input_sha256(staging)
        source_map = {
            "items": {
                FILE_NAME: {
                    "source_item_id": SOURCE_ID,
                    "original_source_path": str(source.resolve()),
                    "period": "202606",
                }
            }
        }
        write_json(priority / ".ocr_source_map.json", source_map)
        task = {
            "id": 1,
            "data": {
                "image": f"/data/upload/1/{FILE_NAME}",
                "ocr_meta": {
                    "view_type": "單機",
                    "screen_status": "",
                    "quality_issue": "",
                    "price_status": "match",
                    "price_symbol": "✓",
                    "official_price": 4990,
                    "price_diff_percent": 0.0,
                    "auto_verified": True,
                    "auto_review_required": False,
                    "review_status": "已完成",
                    "evidence_contract_version": "v19.45",
                    "evidence_guard_revision": OLD_REVISION,
                    "evidence_contract_valid": True,
                },
            },
            "annotations": [
                {
                    "created_at": "2026-07-18T00:00:00",
                    "result": [
                        {
                            "from_name": "category",
                            "value": {"choices": ["單機"]},
                        },
                        {
                            "from_name": "model",
                            "value": {"text": ["S27F612EAC"]},
                        },
                        {
                            "from_name": "price",
                            "value": {"text": ["4990"]},
                        },
                    ],
                }
            ],
        }
        write_json(priority / "run-OCR成功.json", [task])
        parsed = {
            "request_id_verified": True,
            "request_binding_enforced": True,
            "independent_pass": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
            "input_image_sha256": input_sha,
            "runtime_health": {"healthy": True},
        }
        trace = {
            "source_item_id": SOURCE_ID,
            "run_id": RUN_ID,
            "parsed_output": parsed,
        }
        trace_path = output / "_ocr_audit" / "v1945_evidence_trace.jsonl"
        trace_path.parent.mkdir(parents=True)
        trace_path.write_text(
            json.dumps(trace, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        final_result = {
            "view_type": "單機",
            "category": "單機",
            "model": "S27F612EAC",
            "price": "4990",
            "price_symbol": "✓",
            "price_status": "match",
            "official_price": 4990,
            "price_diff_percent": 0.0,
            "screen_status": "",
            "quality_issue": "",
            "complete_screen_count": 1,
            "unique_main": True,
            "label_ownership": "matched",
            "followme_physical_evidence": [],
            "followme_family_confirmed": False,
            "three_pass_adjudicated": False,
            "adjudication_rule": "",
        }
        target_name = "M-202606-one-單機-S27F612EAC-✓＄4990.jpg"
        job = {
            "schema": "samsung-ocr-stream-upload-v1",
            "source_item_id": SOURCE_ID,
            "original_source_path": str(source.resolve()),
            "source_sha256": source_sha,
            "input_image_sha256": input_sha,
            "period": "202606",
            "year": "2026",
            "target_name": target_name,
            "plan": {
                "status": "ready",
                "target_name": target_name,
                "original_path": str(source.resolve()),
            },
            "final_result": final_result,
            "run_id": RUN_ID,
            "ocr_attempt": 1,
            "evidence_guard_revision": OLD_REVISION,
            "failed_at": "2026-07-18T00:01:00",
            "error": f"runtime health fuse is active: {output / '_ocr_audit' / 'runtime_health_fuse.json'}",
        }
        failed = output / "_drive_upload_stream" / "failed" / f"{SOURCE_ID}.json"
        write_json(failed, job)
        presentation = {
            "file_name": FILE_NAME,
            "view_type": "單機",
            "model": "S27F612EAC",
            "price": "4990",
            "evidence_guard_revision": OLD_REVISION,
        }
        status = {
            "current_relative_dir": str(priority.resolve()),
            "is_running": False,
            "runtime_health_fuse": None,
            "stats": {
                "total": 1,
                "success": 1,
                "verified": 1,
                "failed": 0,
                "review_required": 0,
                "verification_unknown": 0,
            },
            "stream_upload": {
                "pending": 0,
                "working": 0,
                "worker_pid": 999,
            },
        }
        return {
            "priority": priority,
            "output": output,
            "job": job,
            "failed": failed,
            "presentation": presentation,
            "status": status,
            "source": source,
            "input_sha": input_sha,
        }

    def test_exact_fuse_failure_is_archived_and_requeued_without_restamp(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.fixture(Path(temp))

            def requester(_url: str, endpoint: str):
                if endpoint == "/api/status":
                    return fixture["status"]
                if endpoint == "/api/success_records":
                    return [fixture["presentation"]]
                raise AssertionError(endpoint)

            with (
                patch(
                    "tools.recover_failed_fuse_uploads._request_json",
                    side_effect=requester,
                ),
                patch(
                    "tools.recover_failed_fuse_uploads.psutil.pid_exists",
                    return_value=True,
                ),
            ):
                recoverable, stale = build_recovery_plan(
                    priority_dir=fixture["priority"],
                    output_dir=fixture["output"],
                    backend_url="http://unit.test",
                )
            self.assertEqual(len(recoverable), 1)
            self.assertEqual(stale, [])
            manifest = apply_recovery(
                recoverable=recoverable,
                stale_markers=stale,
                output_dir=fixture["output"],
            )
            pending = (
                fixture["output"]
                / "_drive_upload_stream"
                / "pending"
                / f"{SOURCE_ID}.json"
            )
            recovered = json.loads(pending.read_text(encoding="utf-8"))
            self.assertFalse(fixture["failed"].exists())
            self.assertTrue(manifest.is_file())
            self.assertEqual(recovered["evidence_guard_revision"], OLD_REVISION)
            self.assertNotEqual(
                recovered["evidence_guard_revision"],
                EVIDENCE_GUARD_REVISION,
            )
            valid, errors = validate_fuse_failed_upload_recovery(
                recovered,
                output_dir=fixture["output"],
            )
            self.assertTrue(valid, errors)
            archive = Path(
                recovered["fuse_failed_upload_recovery"]["archived_failed_job"]
            )
            archived = json.loads(archive.read_text(encoding="utf-8"))
            self.assertEqual(
                _canonical_sha256(archived),
                recovered["fuse_failed_upload_recovery"]["failed_job_sha256"],
            )

    def test_below_minimum_or_non_fuse_job_is_never_requeued(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.fixture(Path(temp))
            job = dict(fixture["job"])
            job["evidence_guard_revision"] = "20260717.41"
            job["error"] = "network failed"
            write_json(fixture["failed"], job)

            def requester(_url: str, endpoint: str):
                return (
                    fixture["status"]
                    if endpoint == "/api/status"
                    else [fixture["presentation"]]
                )

            with (
                patch(
                    "tools.recover_failed_fuse_uploads._request_json",
                    side_effect=requester,
                ),
                patch(
                    "tools.recover_failed_fuse_uploads.psutil.pid_exists",
                    return_value=True,
                ),
            ):
                recoverable, stale = build_recovery_plan(
                    priority_dir=fixture["priority"],
                    output_dir=fixture["output"],
                    backend_url="http://unit.test",
                )
            self.assertEqual(recoverable, [])
            self.assertEqual(stale, [])
            self.assertTrue(fixture["failed"].is_file())

    def test_exact_stopped_worker_boundary_is_allowed_without_live_race(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.fixture(Path(temp))

            def requester(_url: str, endpoint: str):
                if endpoint == "/api/status":
                    return fixture["status"]
                if endpoint == "/api/success_records":
                    return [fixture["presentation"]]
                raise AssertionError(endpoint)

            with (
                patch(
                    "tools.recover_failed_fuse_uploads._request_json",
                    side_effect=requester,
                ),
                patch(
                    "tools.recover_failed_fuse_uploads.psutil.pid_exists",
                    return_value=False,
                ),
            ):
                recoverable, stale = build_recovery_plan(
                    priority_dir=fixture["priority"],
                    output_dir=fixture["output"],
                    backend_url="http://unit.test",
                    expected_stopped_worker_pid=999,
                )
            self.assertEqual(len(recoverable), 1)
            self.assertEqual(stale, [])

    def test_stopped_worker_boundary_rejects_a_different_pid(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.fixture(Path(temp))

            def requester(_url: str, endpoint: str):
                return (
                    fixture["status"]
                    if endpoint == "/api/status"
                    else [fixture["presentation"]]
                )

            with (
                patch(
                    "tools.recover_failed_fuse_uploads._request_json",
                    side_effect=requester,
                ),
                patch(
                    "tools.recover_failed_fuse_uploads.psutil.pid_exists",
                    return_value=False,
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "exact stopped idle boundary",
                ):
                    build_recovery_plan(
                        priority_dir=fixture["priority"],
                        output_dir=fixture["output"],
                        backend_url="http://unit.test",
                        expected_stopped_worker_pid=1000,
                    )

    def test_inactive_complete_priority_can_recover_while_another_batch_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.fixture(Path(temp))
            status = dict(fixture["status"])
            status["current_relative_dir"] = str(
                (Path(temp) / "different-active-batch").resolve()
            )
            status["is_running"] = True
            status["stats"] = {
                "total": 10,
                "success": 2,
                "verified": 2,
                "failed": 0,
                "review_required": 0,
                "verification_unknown": 0,
            }

            def requester(_url: str, endpoint: str):
                if endpoint == "/api/status":
                    return status
                raise AssertionError(
                    "inactive frozen recovery must not read another batch presentation"
                )

            with (
                patch(
                    "tools.recover_failed_fuse_uploads._request_json",
                    side_effect=requester,
                ),
                patch(
                    "tools.recover_failed_fuse_uploads.psutil.pid_exists",
                    return_value=True,
                ),
            ):
                recoverable, stale = build_recovery_plan(
                    priority_dir=fixture["priority"],
                    output_dir=fixture["output"],
                    backend_url="http://unit.test",
                    inactive_priority=True,
                )
            self.assertEqual(len(recoverable), 1)
            self.assertEqual(stale, [])

    def test_inactive_priority_rejects_incomplete_local_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.fixture(Path(temp))
            source_map_path = fixture["priority"] / ".ocr_source_map.json"
            source_map = json.loads(source_map_path.read_text(encoding="utf-8"))
            source_map["items"]["missing.jpg"] = {
                "source_item_id": "c" * 64,
                "original_source_path": str((Path(temp) / "missing.jpg").resolve()),
                "period": "202606",
            }
            write_json(source_map_path, source_map)
            status = dict(fixture["status"])
            status["current_relative_dir"] = str(
                (Path(temp) / "different-active-batch").resolve()
            )
            status["is_running"] = True

            def requester(_url: str, endpoint: str):
                if endpoint == "/api/status":
                    return status
                raise AssertionError(endpoint)

            with (
                patch(
                    "tools.recover_failed_fuse_uploads._request_json",
                    side_effect=requester,
                ),
                patch(
                    "tools.recover_failed_fuse_uploads.psutil.pid_exists",
                    return_value=True,
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "complete verified frozen batch",
                ):
                    build_recovery_plan(
                        priority_dir=fixture["priority"],
                        output_dir=fixture["output"],
                        backend_url="http://unit.test",
                        inactive_priority=True,
                    )

    def test_active_current_revision_rejection_can_recover_without_stopping_ocr(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.fixture(Path(temp))
            current_job = dict(fixture["job"])
            current_job["evidence_guard_revision"] = EVIDENCE_GUARD_REVISION
            current_job["error"] = (
                "stale or invalid stream upload job: missing_recovery_envelope"
            )
            write_json(fixture["failed"], current_job)

            task_path = fixture["priority"] / "run-OCR成功.json"
            tasks = json.loads(task_path.read_text(encoding="utf-8"))
            tasks[0]["data"]["ocr_meta"]["evidence_guard_revision"] = (
                EVIDENCE_GUARD_REVISION
            )
            write_json(task_path, tasks)

            presentation = dict(fixture["presentation"])
            presentation["evidence_guard_revision"] = EVIDENCE_GUARD_REVISION
            status = dict(fixture["status"])
            status["is_running"] = True
            status["stats"] = {
                "total": 20,
                "success": 1,
                "verified": 1,
                "failed": 0,
                "review_required": 0,
                "verification_unknown": 0,
            }

            def requester(_url: str, endpoint: str):
                if endpoint == "/api/status":
                    return status
                raise AssertionError(
                    "active recovery must use the finalized local result snapshot"
                )

            with (
                patch(
                    "tools.recover_failed_fuse_uploads._request_json",
                    side_effect=requester,
                ),
                patch(
                    "tools.recover_failed_fuse_uploads.psutil.pid_exists",
                    return_value=True,
                ),
            ):
                recoverable, stale = build_recovery_plan(
                    priority_dir=fixture["priority"],
                    output_dir=fixture["output"],
                    backend_url="http://unit.test",
                    active_priority=True,
                )
            self.assertEqual(len(recoverable), 1)
            self.assertEqual(stale, [])
            self.assertEqual(
                recoverable[0]["recovery_reason"],
                "current_revision_rejected_by_older_uploader",
            )
            apply_recovery(
                recoverable=recoverable,
                stale_markers=stale,
                output_dir=fixture["output"],
            )
            pending = (
                fixture["output"]
                / "_drive_upload_stream"
                / "pending"
                / f"{SOURCE_ID}.json"
            )
            recovered = json.loads(pending.read_text(encoding="utf-8"))
            valid, errors = validate_fuse_failed_upload_recovery(
                recovered,
                output_dir=fixture["output"],
            )
            self.assertTrue(valid, errors)
            self.assertTrue(status["is_running"])

    def test_receipt_revision_delta_requires_one_exact_migration_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.jpg"
            source.write_bytes(b"source")
            migration_dir = root / "migrations"
            receipt = {
                "source_item_id": SOURCE_ID,
                "source_sha256": hashlib.sha256(b"source").hexdigest(),
                "run_id": RUN_ID,
                "period": "202606",
                "file_name": "published.jpg",
                "evidence_guard_revision": "20260718.48",
            }
            archived = {
                "schema": "samsung-ocr-stream-upload-v1",
                "source_item_id": SOURCE_ID,
                "evidence_guard_revision": "20260718.47",
                "original_source_path": str(source.resolve()),
                "source_sha256": receipt["source_sha256"],
                "run_id": RUN_ID,
                "period": "202606",
                "target_name": "published.jpg",
            }
            path = (
                migration_dir
                / f"{SOURCE_ID}.20260718.47.20260718_000000_000000.json"
            )
            write_json(path, archived)
            self.assertTrue(
                receipt_revision_is_proven(
                    source_item_id=SOURCE_ID,
                    record_revision="20260718.47",
                    receipt=receipt,
                    migration_dir=migration_dir,
                    original_source_path=source.resolve(),
                )
            )
            write_json(
                migration_dir
                / f"{SOURCE_ID}.20260718.47.20260718_000001_000000.json",
                archived,
            )
            self.assertFalse(
                receipt_revision_is_proven(
                    source_item_id=SOURCE_ID,
                    record_revision="20260718.47",
                    receipt=receipt,
                    migration_dir=migration_dir,
                    original_source_path=source.resolve(),
                )
            )


if __name__ == "__main__":
    unittest.main()

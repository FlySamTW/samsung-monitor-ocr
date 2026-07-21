import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.audit_fields import EVIDENCE_GUARD_REVISION
from tools.photo_rename_planner import copy_planned_image_idempotent, plan_single_image
from tools.rclone_drive_upload import md5_file, read_csv
from tools.stream_drive_upload import (
    _equivalent_upload_job,
    COMPATIBLE_PENDING_REVISION_MIGRATIONS,
    FUSE_UPLOAD_RECOVERY_SCHEMA,
    _YEAR_FOLDER_ID_CACHE,
    _append_uploaded_atomic,
    _atomic_json,
    _canonical_sha256,
    enqueue_finalized_result,
    is_transient_upload_failure,
    migrate_compatible_pending_jobs,
    process_one_job,
    read_stream_status,
    requeue_transient_job,
    remote_stat_exact,
)


def make_image(path: Path, color=(20, 40, 60)) -> None:
    Image.new("RGB", (40, 30), color).save(path, format="JPEG", quality=92)


def verified_result(source: Path, **overrides):
    row = {
        "source_item_id": "a" * 64,
        "original_source_path": str(source),
        "source_path": str(source),
        "file_name": source.name,
        "period": "202601",
        "view_type": "單機",
        "category": "單機",
        "model": "S27CG552EC",
        "price": "4990",
        "price_status": "match",
        "price_symbol": "✓",
        "screen_status": "正常",
        "quality_issue": "無",
        "complete_screen_count": 1,
        "unique_main": True,
        "label_ownership": "matched",
        "followme_physical_evidence": [],
        "auto_verified": True,
        "auto_review_required": False,
        "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
        "independent_pass": True,
        "request_binding_enforced": True,
        "request_id_verified": True,
        "prior_answer_exposed": False,
        "prompt_contamination": False,
        "runtime_health": {"healthy": True},
        "input_image_sha256": "b" * 64,
        "run_id": "run-test",
        "ocr_attempt": 3,
    }
    row.update(overrides)
    return row


class FakeRclone:
    def __init__(self):
        self.remote = None
        self.copy_calls = 0
        self.copy_command = None

    def __call__(self, command, **_kwargs):
        action = command[1]
        if action == "copyto":
            local = Path(command[2])
            self.copy_calls += 1
            self.copy_command = list(command)
            self.remote = {
                "Name": Path(command[3]).name,
                "Size": local.stat().st_size,
                "Hashes": {"MD5": md5_file(local)},
                "ID": "drive-test-id",
            }
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if action == "backend":
            query = command[4]
            if "mimeType = 'application/vnd.google-apps.folder'" in query:
                payload = [{
                    "id": "drive-year-2026",
                    "name": "2026",
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": ["16X5qALC3zRYc7PpnexXLYprorBzBtT_f"],
                }]
            elif self.remote is None:
                payload = []
            else:
                payload = [{
                    "id": self.remote["ID"],
                    "name": self.remote["Name"],
                    "mimeType": "image/jpeg",
                    "parents": ["drive-year-2026"],
                    "size": str(self.remote["Size"]),
                    "md5Checksum": self.remote["Hashes"]["MD5"],
                }]
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")
        raise AssertionError(command)


class StreamDriveUploadTests(unittest.TestCase):
    def test_network_readback_failure_is_retryable_but_gate_failure_is_not(self):
        self.assertTrue(
            is_transient_upload_failure(
                RuntimeError("exact remote readback failed: rc=1")
            )
        )
        self.assertFalse(
            is_transient_upload_failure(
                RuntimeError("source bytes changed after OCR finalization")
            )
        )

    def test_transient_failure_returns_job_to_delayed_pending_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            dirs = {
                name: output / "_drive_upload_stream" / name
                for name in ("pending", "working")
            }
            for path in dirs.values():
                path.mkdir(parents=True, exist_ok=True)
            job_path = dirs["working"] / "a.json"
            job_path.write_text(
                json.dumps({"schema": "test"}, ensure_ascii=False),
                encoding="utf-8",
            )
            pending = requeue_transient_job(
                job_path,
                RuntimeError("exact remote readback failed: rc=1"),
                output_dir=output,
                now_epoch=1000.0,
            )
            payload = json.loads(pending.read_text(encoding="utf-8"))
            self.assertFalse(job_path.exists())
            self.assertEqual(payload["transport_retry_count"], 1)
            self.assertEqual(payload["retry_not_before_epoch"], 1015.0)

    def test_upload_job_equivalence_ignores_only_enqueue_audit_metadata(self):
        base = {
            "schema": "samsung-ocr-stream-upload-v1",
            "source_item_id": "a" * 64,
            "target_name": "same.jpg",
            "queued_at": "first",
            "superseded_receipt": {"archived_path": "old.json"},
        }
        retried = dict(base)
        retried["queued_at"] = "second"
        retried.pop("superseded_receipt")
        self.assertTrue(_equivalent_upload_job(base, retried))
        retried["target_name"] = "different.jpg"
        self.assertFalse(_equivalent_upload_job(base, retried))

    def setUp(self):
        _YEAR_FOLDER_ID_CACHE.clear()

    def test_status_write_retries_transient_windows_replace_denial(self):
        with tempfile.TemporaryDirectory() as root:
            status_path = Path(root) / "status.json"
            with (
                patch(
                    "tools.stream_drive_upload.os.replace",
                    side_effect=[PermissionError(5, "busy"), PermissionError(5, "busy"), None],
                ) as replace_mock,
                patch("tools.stream_drive_upload.time.sleep") as sleep_mock,
            ):
                _atomic_json(status_path, {"worker_state": "running"})

            self.assertEqual(replace_mock.call_count, 3)
            self.assertEqual(sleep_mock.call_count, 2)

    def test_explicitly_approved_pending_revision_migrates_with_durable_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "M-新北市-中和區-TK3C-中和-1333.jpg"
            make_image(source)
            output = root / "output"
            job = enqueue_finalized_result(verified_result(source), output_dir=output)
            original = json.loads(job.read_text(encoding="utf-8"))
            original["evidence_guard_revision"] = "20260718.49"
            job.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")

            with patch.dict(
                COMPATIBLE_PENDING_REVISION_MIGRATIONS,
                {"20260718.49": EVIDENCE_GUARD_REVISION},
                clear=True,
            ):
                migrated = migrate_compatible_pending_jobs(output)
            upgraded = json.loads(job.read_text(encoding="utf-8"))

            self.assertEqual(migrated, 1)
            self.assertEqual(upgraded["evidence_guard_revision"], EVIDENCE_GUARD_REVISION)
            self.assertEqual(upgraded["revision_migration"]["from"], "20260718.49")
            self.assertEqual(upgraded["revision_migration"]["target_name"], original["target_name"])
            archives = list(
                (output / "_drive_upload_stream" / "revision_migrations").glob("*.json")
            )
            self.assertEqual(len(archives), 1)
            archived = json.loads(archives[0].read_text(encoding="utf-8"))
            self.assertEqual(archived, original)

    def test_rev68_pending_jobs_are_explicitly_compatible_with_rev69(self):
        self.assertEqual(
            COMPATIBLE_PENDING_REVISION_MIGRATIONS.get("20260721.68"),
            "20260721.69",
        )

    def test_rev69_pending_jobs_are_explicitly_compatible_with_rev70(self):
        self.assertEqual(
            COMPATIBLE_PENDING_REVISION_MIGRATIONS.get("20260721.69"),
            "20260721.70",
        )

    def test_rev70_pending_jobs_do_not_migrate_across_geometry_fix(self):
        self.assertNotIn("20260721.70", COMPATIBLE_PENDING_REVISION_MIGRATIONS)

    def test_historical_rev68_migration_does_not_chain_into_current_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "M-test-1368.jpg"
            make_image(source)
            output = root / "output"
            job_path = enqueue_finalized_result(
                verified_result(source), output_dir=output
            )
            job = json.loads(job_path.read_text(encoding="utf-8"))
            job["evidence_guard_revision"] = "20260721.68"
            job["final_result"]["adjudication_rule"] = (
                "distant_structural_veto_over_wide_geometry_single_votes"
            )
            job_path.write_text(
                json.dumps(job, ensure_ascii=False), encoding="utf-8"
            )
            with self.assertRaisesRegex(RuntimeError, "unapproved pending upload revision"):
                migrate_compatible_pending_jobs(output)

    def test_worker_migrates_jobs_that_arrive_after_startup(self):
        loop = Path(__file__).with_name("stream_drive_upload.py").read_text(
            encoding="utf-8"
        )
        while_index = loop.index("while True:", loop.index("def run_worker"))
        migrate_index = loop.index(
            "newly_migrated = migrate_compatible_pending_jobs(output_dir)",
            while_index,
        )
        claim_index = loop.index("job_path = claim_next_job(output_dir)", while_index)
        self.assertLess(migrate_index, claim_index)

    def test_frozen_fuse_recovery_upload_preserves_original_ocr_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "M-新北市-中和區-TK3C-中和-1335.jpg"
            make_image(source)
            output = root / "output"
            job_path = enqueue_finalized_result(
                verified_result(source),
                output_dir=output,
            )
            frozen = json.loads(job_path.read_text(encoding="utf-8"))
            frozen["evidence_guard_revision"] = "20260718.48"
            frozen["failed_at"] = "2026-07-18T00:01:00"
            frozen["error"] = (
                "runtime health fuse is active: "
                + str(output / "_ocr_audit" / "runtime_health_fuse.json")
            )
            archive = (
                output
                / "_ocr_audit"
                / "fuse_failed_upload_recovery"
                / "unit"
                / "failed_jobs"
                / job_path.name
            )
            _atomic_json(archive, frozen)
            frozen["fuse_failed_upload_recovery"] = {
                "schema": FUSE_UPLOAD_RECOVERY_SCHEMA,
                "reason": "runtime_health_fuse_cleared_after_ocr_finalization",
                "approved_uploader_revision": EVIDENCE_GUARD_REVISION,
                "source_item_id": frozen["source_item_id"],
                "source_revision": "20260718.48",
                "source_sha256": frozen["source_sha256"],
                "input_image_sha256": frozen["input_image_sha256"],
                "run_id": frozen["run_id"],
                "target_name": frozen["target_name"],
                "failed_job_sha256": _canonical_sha256(
                    {
                        key: value
                        for key, value in frozen.items()
                        if key != "fuse_failed_upload_recovery"
                    }
                ),
                "archived_failed_job": str(archive.resolve()),
                "prepared_at": "2026-07-18T00:02:00",
            }
            _atomic_json(job_path, frozen)

            self.assertEqual(migrate_compatible_pending_jobs(output), 0)
            fake = FakeRclone()
            receipt = process_one_job(
                job_path,
                output_dir=output,
                rclone=Path("rclone.exe"),
                runner=fake,
            )

            self.assertEqual(receipt["evidence_guard_revision"], "20260718.48")
            self.assertNotEqual(
                receipt["evidence_guard_revision"],
                EVIDENCE_GUARD_REVISION,
            )
            self.assertEqual(
                receipt["upload_recovery"]["failed_job_sha256"],
                frozen["fuse_failed_upload_recovery"]["failed_job_sha256"],
            )

    def test_previous_model_rule_revision_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "M-新北市-中和區-TK3C-中和-1334.jpg"
            make_image(source)
            output = root / "output"
            job = enqueue_finalized_result(verified_result(source), output_dir=output)
            payload = json.loads(job.read_text(encoding="utf-8"))
            payload["evidence_guard_revision"] = "20260718.51"
            job.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "unapproved pending upload revision"):
                migrate_compatible_pending_jobs(output)

    def test_unapproved_pending_revision_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "M-新北市-中和區-TK3C-中和-1332.jpg"
            make_image(source)
            output = root / "output"
            job = enqueue_finalized_result(verified_result(source), output_dir=output)
            payload = json.loads(job.read_text(encoding="utf-8"))
            payload["evidence_guard_revision"] = "20260718.46"
            job.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "unapproved pending upload revision"):
                migrate_compatible_pending_jobs(output)

    def test_pending_revision_migration_rejects_filename_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "M-新北市-中和區-TK3C-中和-1331.jpg"
            make_image(source)
            output = root / "output"
            job = enqueue_finalized_result(verified_result(source), output_dir=output)
            payload = json.loads(job.read_text(encoding="utf-8"))
            payload["evidence_guard_revision"] = "20260718.49"
            payload["target_name"] = "tampered.jpg"
            job.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            with patch.dict(
                COMPATIBLE_PENDING_REVISION_MIGRATIONS,
                {"20260718.49": EVIDENCE_GUARD_REVISION},
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "filename changed"):
                    migrate_compatible_pending_jobs(output)

    def test_exact_drive_query_returns_every_duplicate_name_with_hash_and_id(self):
        calls = []

        def runner(command, **_kwargs):
            calls.append(command)
            query = command[4]
            if "mimeType = 'application/vnd.google-apps.folder'" in query:
                payload = [{
                    "id": "year-id",
                    "name": "2026",
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": ["16X5qALC3zRYc7PpnexXLYprorBzBtT_f"],
                }]
            else:
                payload = [
                    {
                        "id": "duplicate-a",
                        "name": "same.jpg",
                        "mimeType": "image/jpeg",
                        "parents": ["year-id"],
                        "size": "123",
                        "md5Checksum": "a" * 32,
                    },
                    {
                        "id": "duplicate-b",
                        "name": "same.jpg",
                        "mimeType": "image/jpeg",
                        "parents": ["year-id"],
                        "size": "456",
                        "md5Checksum": "b" * 32,
                    },
                ]
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

        entries = remote_stat_exact(
            Path("rclone.exe"),
            "samsung_ocr_drive",
            "2026",
            "same.jpg",
            runner=runner,
        )

        self.assertEqual([item["ID"] for item in entries], ["duplicate-a", "duplicate-b"])
        self.assertEqual(entries[0]["Size"], 123)
        self.assertEqual(entries[1]["Hashes"]["MD5"], "b" * 32)
        self.assertTrue(all(command[1:3] == ["backend", "query"] for command in calls))

    def test_exact_drive_query_escapes_quote_in_filename(self):
        queries = []

        def runner(command, **_kwargs):
            query = command[4]
            queries.append(query)
            payload = [{
                "id": "year-id",
                "name": "2026",
                "mimeType": "application/vnd.google-apps.folder",
                "parents": ["16X5qALC3zRYc7PpnexXLYprorBzBtT_f"],
            }] if "mimeType = 'application/vnd.google-apps.folder'" in query else []
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

        remote_stat_exact(
            Path("rclone.exe"),
            "samsung_ocr_drive",
            "2026",
            "店'名照片.jpg",
            runner=runner,
        )

        self.assertIn("name = '店\\'名照片.jpg'", queries[-1])

    def test_duplicate_year_folder_fails_closed(self):
        def runner(command, **_kwargs):
            payload = [
                {
                    "id": suffix,
                    "name": "2026",
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": ["16X5qALC3zRYc7PpnexXLYprorBzBtT_f"],
                }
                for suffix in ("year-a", "year-b")
            ]
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

        with self.assertRaisesRegex(RuntimeError, "missing or duplicated"):
            remote_stat_exact(
                Path("rclone.exe"),
                "samsung_ocr_drive",
                "2026",
                "same.jpg",
                runner=runner,
            )

    def test_fresh_receipt_refreshes_stale_matching_legacy_ledger_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "drive_upload_uploaded.csv"
            stale = {
                "index": "7",
                "source_path": "same-source.jpg",
                "file_name": "same-target.jpg",
                "drive_file_id": "",
                "uploaded_at": "2026-07-04T15:46:47",
            }
            fresh = {
                **stale,
                "drive_file_id": "fresh-drive-id",
                "uploaded_at": "2026-07-16T18:00:00",
            }

            _append_uploaded_atomic(ledger, stale)
            count = _append_uploaded_atomic(ledger, fresh)

            self.assertEqual(count, 1)
            rows = read_csv(ledger)
            self.assertEqual(rows[0]["index"], "1")
            self.assertEqual(rows[0]["drive_file_id"], "fresh-drive-id")
            self.assertEqual(rows[0]["uploaded_at"], "2026-07-16T18:00:00")

    def test_only_verified_bound_result_enters_outbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "M-台中市-南區-TK3C-台中旗艦-943.jpg"
            make_image(source)
            output = root / "output"

            job = enqueue_finalized_result(verified_result(source), output_dir=output)
            rejected = enqueue_finalized_result(
                verified_result(source, source_item_id="c" * 64, auto_review_required=True),
                output_dir=output,
            )

            self.assertIsNotNone(job)
            self.assertTrue(job.is_file())
            self.assertIsNone(rejected)
            self.assertEqual(read_stream_status(output)["pending"], 1)

    def test_old_revision_receipt_never_suppresses_corrected_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "M-台中市-南區-TK3C-台中旗艦-940.jpg"
            make_image(source)
            output = root / "output"
            receipt_dir = output / "_drive_upload_stream" / "receipts"
            receipt_dir.mkdir(parents=True)
            key = "a" * 64
            stale = {
                "schema": "samsung-ocr-stream-receipt-v1",
                "source_item_id": key,
                "source_sha256": "old-bytes",
                "file_name": "old-wrong-name.jpg",
                "evidence_guard_revision": "20260716.21",
                "remote_path": "samsung_ocr_drive:2026/old-wrong-name.jpg",
                "drive_file_id": "old-drive-id",
            }
            (receipt_dir / f"{key}.json").write_text(
                json.dumps(stale, ensure_ascii=False), encoding="utf-8"
            )

            job = enqueue_finalized_result(verified_result(source), output_dir=output)
            payload = json.loads(job.read_text(encoding="utf-8"))

            self.assertEqual(job.parent.name, "pending")
            self.assertEqual(payload["evidence_guard_revision"], EVIDENCE_GUARD_REVISION)
            self.assertEqual(payload["superseded_receipt"]["drive_file_id"], "old-drive-id")
            self.assertFalse((receipt_dir / f"{key}.json").exists())
            archived = list((output / "_drive_upload_stream" / "superseded_receipts").glob("*.json"))
            self.assertEqual(len(archived), 1)

    def test_current_exact_receipt_remains_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "M-台中市-南區-TK3C-台中旗艦-940.jpg"
            make_image(source)
            output = root / "output"
            first_job = enqueue_finalized_result(verified_result(source), output_dir=output)
            payload = json.loads(first_job.read_text(encoding="utf-8"))
            first_job.unlink()
            receipt_dir = output / "_drive_upload_stream" / "receipts"
            receipt_dir.mkdir(parents=True, exist_ok=True)
            receipt = {
                "schema": "samsung-ocr-stream-receipt-v1",
                "source_item_id": "a" * 64,
                "source_sha256": payload["source_sha256"],
                "file_name": payload["target_name"],
                "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
            }
            receipt_path = receipt_dir / f"{'a' * 64}.json"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

            existing = enqueue_finalized_result(verified_result(source), output_dir=output)

            self.assertEqual(existing, receipt_path)
            self.assertFalse(any((output / "_drive_upload_stream" / "pending").glob("*.json")))

    def test_distant_is_a_valid_final_upload_job_without_model_or_price(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "M-台中市-南區-TK3C-台中旗艦-942.jpg"
            make_image(source)
            output = root / "output"
            row = verified_result(
                source,
                view_type="遠景",
                category="遠景",
                model=None,
                price=None,
                price_status="not_compared",
                price_symbol="",
                complete_screen_count=3,
                unique_main=False,
                label_ownership="ambiguous",
            )

            job = enqueue_finalized_result(row, output_dir=output)
            payload = json.loads(job.read_text(encoding="utf-8"))

            self.assertIn("-遠景-", payload["target_name"])
            self.assertNotIn("型號未辨識", payload["target_name"])
            self.assertNotIn("無價格", payload["target_name"])

    def test_confirmed_followme_family_never_falls_back_to_distant_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "M-台中市-大里區-SF-大里-632.jpg"
            make_image(source)
            output = root / "output"
            physical = [
                {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
                {"cue": "round_base", "same_subject": True, "strength": "strong"},
                {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
            ]
            row = verified_result(
                source,
                model=None,
                price=None,
                price_status="not_compared",
                price_symbol="",
                followme_family_confirmed=True,
                followme_physical_evidence=physical,
            )

            job = enqueue_finalized_result(row, output_dir=output)
            payload = json.loads(job.read_text(encoding="utf-8"))

            self.assertIn("-單機-FollowMe_型號未細分-", payload["target_name"])
            self.assertNotIn("-遠景-", payload["target_name"])

    def test_price_comparison_symbol_is_preserved_in_target_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "M-台中市-南區-TK3C-台中旗艦-941.jpg"
            make_image(source)
            for index, symbol in enumerate(("↑", "↓", "✓")):
                row = verified_result(source, source_item_id=f"{index + 1:064x}", price_symbol=symbol)
                job = enqueue_finalized_result(row, output_dir=root / "output")
                self.assertIn(f"-{symbol}＄4990-", json.loads(job.read_text(encoding="utf-8"))["target_name"])

    def test_publish_is_idempotent_and_never_creates_suffix_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "M-台中市-南區-TK3C-台中旗艦-940.jpg"
            make_image(source)
            output = root / "output"
            plan = plan_single_image(source, verified_result(source), "202601", "＄", current_year=2026)

            first = copy_planned_image_idempotent(plan, output)
            second = copy_planned_image_idempotent(plan, output)

            self.assertEqual(first["target_path"], second["target_path"])
            self.assertEqual(second["status"], "existing_same_bytes")
            self.assertEqual(len(list(output.glob("*.jpg"))), 1)
            self.assertFalse(any("_2" in path.stem for path in output.glob("*.jpg")))

    def test_upload_requires_exact_readback_before_receipt_and_legacy_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "M-台中市-南區-TK3C-台中旗艦-939.jpg"
            make_image(source)
            output = root / "output"
            job = enqueue_finalized_result(verified_result(source), output_dir=output)
            fake = FakeRclone()

            receipt = process_one_job(
                job,
                output_dir=output,
                rclone=Path("rclone.exe"),
                runner=fake,
            )

            self.assertEqual(fake.copy_calls, 1)
            self.assertIn("--local-encoding", fake.copy_command)
            self.assertEqual(fake.copy_command[fake.copy_command.index("--local-encoding") + 1], "None")
            self.assertEqual(receipt["drive_file_id"], "drive-test-id")
            self.assertTrue((output / "_drive_upload_stream" / "receipts" / f"{'a' * 64}.json").is_file())
            ledger = (output / "_drive_upload" / "drive_upload_uploaded.csv").read_text(encoding="utf-8-sig")
            self.assertIn(receipt["file_name"], ledger)

    def test_existing_confirmed_remote_is_idempotent_without_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "M-台中市-南區-TK3C-台中旗艦-938.jpg"
            make_image(source)
            output = root / "output"
            job = enqueue_finalized_result(verified_result(source), output_dir=output)
            payload = json.loads(job.read_text(encoding="utf-8"))
            published = copy_planned_image_idempotent(payload["plan"], output)
            local = Path(published["target_path"])
            fake = FakeRclone()
            fake.remote = {
                "Name": local.name,
                "Size": local.stat().st_size,
                "Hashes": {"MD5": md5_file(local)},
                "ID": "already-there",
            }

            receipt = process_one_job(
                job,
                output_dir=output,
                rclone=Path("rclone.exe"),
                runner=fake,
            )

            self.assertEqual(fake.copy_calls, 0)
            self.assertEqual(receipt["drive_file_id"], "already-there")

    def test_wrong_remote_hash_is_replaced_then_receipted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "M-台中市-南區-TK3C-台中旗艦-937.jpg"
            make_image(source)
            output = root / "output"
            job = enqueue_finalized_result(verified_result(source), output_dir=output)
            fake = FakeRclone()
            fake.remote = {
                "Name": "wrong.jpg",
                "Size": 1,
                "Hashes": {"MD5": "0" * 32},
                "ID": "wrong-object",
            }

            receipt = process_one_job(
                job,
                output_dir=output,
                rclone=Path("rclone.exe"),
                runner=fake,
            )

            self.assertEqual(fake.copy_calls, 1)
            self.assertEqual(receipt["drive_file_id"], "drive-test-id")
            self.assertTrue((output / "_drive_upload_stream" / "receipts" / f"{'a' * 64}.json").exists())

    def test_failed_post_replace_readback_never_writes_any_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "M-bad-readback-936.jpg"
            make_image(source)
            output = root / "output"
            job = enqueue_finalized_result(verified_result(source), output_dir=output)

            class NonUpdatingRclone(FakeRclone):
                def __call__(self, command, **kwargs):
                    if command[1] == "copyto":
                        self.copy_calls += 1
                        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
                    return super().__call__(command, **kwargs)

            fake = NonUpdatingRclone()
            fake.remote = {
                "Name": "wrong.jpg",
                "Size": 1,
                "Hashes": {"MD5": "0" * 32},
                "ID": "wrong-object",
            }

            with self.assertRaisesRegex(RuntimeError, "size and MD5"):
                process_one_job(
                    job,
                    output_dir=output,
                    rclone=Path("rclone.exe"),
                    readback_attempts=1,
                    runner=fake,
                )

            self.assertFalse((output / "_drive_upload_stream" / "receipts" / f"{'a' * 64}.json").exists())
            self.assertFalse((output / "_drive_upload" / "drive_upload_uploaded.csv").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)

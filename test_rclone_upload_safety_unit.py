import csv
import hashlib
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


MODULE_PATH = Path(__file__).parent / "tools" / "rclone_drive_upload.py"
spec = importlib.util.spec_from_file_location("rclone_drive_upload", MODULE_PATH)
uploader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uploader)
ORIGINAL_ENSURE_NO_ACTIVE_OCR_RUNNER = uploader.ensure_no_active_ocr_runner
ORIGINAL_ENSURE_BACKEND_IDLE = uploader.ensure_backend_idle


def write_csv(path, rows, headers):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def write_valid_summary(manifest, batch, *, gate_open=True, fresh=True, proof_overrides=None):
    audit_hash = hashlib.sha256(b"audit-input").hexdigest()
    proof = {
        "complete": True,
        "expected_candidate_count": 2,
        "scanned_result_count": 2,
        "missing_or_invalid": [],
        "duplicate_source_identity": 0,
        "audit_input_sha256": audit_hash,
    }
    proof.update(proof_overrides or {})
    summary = {
        "next_batch_sha256": hashlib.sha256(batch.read_bytes()).hexdigest(),
        "upload_scope_years": [],
        "current_year_upload_gate_open": gate_open,
        "current_year_risk_audit_fresh": fresh,
        "current_year_finalization_proof": proof,
        "current_audit_input_sha256": audit_hash,
    }
    (manifest / "drive_upload_summary.json").write_text(json.dumps(summary), encoding="utf-8")


def write_valid_shared_gate(manifest, batch, rows, *, next_batch_sha256=None):
    pending = manifest / "drive_upload_ready_pending.csv"
    write_csv(pending, rows, rows[0].keys() if rows else ["source_path", "file_name", "year"])
    summary = manifest / "drive_upload_summary.json"
    proof = {
        "schema": uploader.upload_gate_authority.SCHEMA,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "gate_open": True,
        "manifest_summary_path": str(summary.resolve()),
        "manifest_summary_sha256": uploader.sha256_file(summary),
        "pending_csv_path": str(pending.resolve()),
        "pending_sha256": uploader.sha256_file(pending),
        "pending_count": len(rows),
        "next_batch_csv_path": str(batch.resolve()),
        "next_batch_sha256": next_batch_sha256 or uploader.sha256_file(batch),
        "next_batch_count": len(rows),
        "audit_input_sha256": hashlib.sha256(b"audit-input").hexdigest(),
        "backfill_run_id": "run-1",
    }
    (manifest / "upload_gate_proof.json").write_text(json.dumps(proof), encoding="utf-8")
    return proof


def write_historical_authorization(root, shared_proof):
    audit = root / "_ocr_audit"
    audit.mkdir(exist_ok=True)
    marker = audit / "current_year_rerun_cycle_complete.json"
    marker.write_text(
        json.dumps(
            {
                "upload_gate_schema": shared_proof["schema"],
                "audit_input_sha256": shared_proof["audit_input_sha256"],
                "backfill_run_id": shared_proof["backfill_run_id"],
                "pending_count": 0,
            }
        ),
        encoding="utf-8",
    )
    discovery = audit / "folder_discovery.csv"
    summary = audit / "folder_summary.csv"
    inventory_csv = audit / "source_inventory_v1.csv"
    inventory_summary = audit / "source_inventory_v1.json"
    inventory_rows = [{
        "folder_id": "f" * 64, "folder": "source", "period": "2025",
        "relative_path": "source/a.jpg", "size_bytes": "1", "mtime_ns": "100",
        "content_sha256": "a" * 64,
    }]
    write_csv(inventory_csv, inventory_rows, inventory_rows[0].keys())
    inventory_payload = {
        "schema": "samsung-ocr-source-inventory/v1",
        "inventory_csv_sha256": uploader.sha256_file(inventory_csv),
        "row_count": 1,
        "folder_count": 1,
    }
    inventory_summary.write_text(json.dumps(inventory_payload), encoding="utf-8")
    inventory_hash = uploader.sha256_file(inventory_csv)
    discovery_rows = [{"folder_id": "f" * 64, "folder": "source", "image_count": "1", "latest_mtime": "100", "source_inventory_sha256": inventory_hash}]
    summary_rows = [
        {
            "folder": "source",
            "image_count": "1",
            "source_latest_mtime": "100",
            "status": "copied",
            "folder_id": "f" * 64,
            "source_inventory_sha256": inventory_hash,
            "success_records": "1",
            "copied_count": "1",
            "missing_result": "0",
            "missing_source": "0",
            "conflict": "0",
            "failed": "0",
            "copy_error": "",
        }
    ]
    write_csv(discovery, discovery_rows, discovery_rows[0].keys())
    write_csv(summary, summary_rows, summary_rows[0].keys())
    authorization = {
        "schema": uploader.HISTORICAL_AUTH_SCHEMA,
        "all_year_questionable_review": True,
        "current_year_marker_path": str(marker.resolve()),
        "current_year_marker_sha256": uploader.sha256_file(marker),
        "folder_discovery_path": str(discovery.resolve()),
        "folder_discovery_sha256": uploader.sha256_file(discovery),
        "folder_summary_path": str(summary.resolve()),
        "folder_summary_sha256": uploader.sha256_file(summary),
        "source_inventory_csv_path": str(inventory_csv.resolve()),
        "source_inventory_csv_sha256": uploader.sha256_file(inventory_csv),
        "source_inventory_summary_path": str(inventory_summary.resolve()),
        "source_inventory_summary_sha256": uploader.sha256_file(inventory_summary),
        "source_inventory_row_count": 1,
        "source_inventory_folder_count": 1,
        "discovered_folder_count": 1,
        "completed_folder_count": 1,
        "error_count": 0,
    }
    (audit / "historical_upload_authorization.json").write_text(
        json.dumps(authorization), encoding="utf-8"
    )
    return {
        "authorization": audit / "historical_upload_authorization.json",
        "marker": marker,
        "discovery": discovery,
        "summary": summary,
    }


class UploadSafetyTests(unittest.TestCase):
    def setUp(self):
        self.backend_idle = patch.object(uploader, "ensure_backend_idle")
        self.no_runner = patch.object(uploader, "ensure_no_active_ocr_runner")
        self.proof_snapshot = patch.object(
            uploader.upload_gate_authority, "validate_proof_snapshot", return_value=[]
        )
        self.backend_idle.start()
        self.no_runner.start()
        self.proof_snapshot.start()

    def tearDown(self):
        self.proof_snapshot.stop()
        self.no_runner.stop()
        self.backend_idle.stop()

    def test_staged_paths_reject_files_outside_staging(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "_drive_upload"
            (manifest / "staging").mkdir(parents=True)
            outside = root / "output.jpg"
            outside.write_bytes(b"x")
            write_csv(
                manifest / "staging_map.csv",
                [{"stage_file": str(outside), "source_path": "src", "file_name": "photo.jpg"}],
                ["stage_file", "source_path", "file_name"],
            )
            with self.assertRaisesRegex(SystemExit, "staged upload files missing"):
                uploader.load_staged_paths(
                    manifest, [{"source_path": "src", "file_name": "photo.jpg"}]
                )

    def test_staged_bytes_must_match_the_content_hash_bound_by_the_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "_drive_upload"
            staged = manifest / "staging" / "2026" / "photo.jpg"
            staged.parent.mkdir(parents=True)
            staged.write_bytes(b"other")
            expected = hashlib.sha256(b"image").hexdigest()
            row = {
                "source_path": "src/photo.jpg",
                "file_name": "photo.jpg",
                "year": "2026",
                "content_sha256": expected,
            }
            write_csv(
                manifest / "staging_map.csv",
                [{"stage_file": str(staged), **row}],
                ["stage_file", "source_path", "file_name", "year", "content_sha256"],
            )
            with self.assertRaisesRegex(SystemExit, "bytes do not match"):
                uploader.load_staged_paths(manifest, [row])

    def test_upload_logs_only_remote_files_that_are_confirmed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "_drive_upload"
            staging = manifest / "staging" / "2026"
            staging.mkdir(parents=True)
            rows = [
                {"source_path": "source/one.jpg", "file_name": "one.jpg", "year": "2026", "content_sha256": hashlib.sha256(b"image").hexdigest()},
                {"source_path": "source/two.jpg", "file_name": "two.jpg", "year": "2026", "content_sha256": hashlib.sha256(b"image").hexdigest()},
            ]
            for row in rows:
                (staging / row["file_name"]).write_bytes(b"image")
            batch = manifest / "drive_upload_next_batch.csv"
            write_csv(batch, rows, rows[0].keys())
            write_valid_summary(manifest, batch)
            write_csv(
                manifest / "staging_map.csv",
                [
                    {
                        "stage_file": str(staging / row["file_name"]),
                        "source_path": row["source_path"],
                        "file_name": row["file_name"],
                        "content_sha256": row["content_sha256"],
                    }
                    for row in rows
                ],
                ["stage_file", "source_path", "file_name", "content_sha256"],
            )
            args = SimpleNamespace(
                limit=2,
                manifest_dir=manifest,
                output_dir=root,
                rclone=Path("rclone"),
                remote=uploader.DEFAULT_REMOTE,
                transfers=1,
                checkers=1,
                dry_run=False,
                execute=True,
                rclone_timeout_seconds=10,
                continue_on_timeout=True,
                log_path=manifest / "upload.log",
                uploaded_log=manifest / "drive_upload_uploaded.csv",
                backend_url="http://127.0.0.1:5000",
                years="",
            )
            with (
                patch.object(uploader, "prepare_manifest", return_value=manifest / "drive_upload_next_batch.csv"),
                patch.object(
                    uploader.upload_gate_authority,
                    "run",
                    return_value={"valid": True, "executed": True, "errors": []},
                ),
                patch.object(uploader, "run_command", return_value=0),
                patch.object(
                    uploader,
                    "remote_file_map",
                    return_value={
                        "one.jpg": [
                            {
                                "ID": "one-id",
                                "Size": 5,
                                "Hashes": {"MD5": hashlib.md5(b"image").hexdigest()},
                            }
                        ],
                        "two.jpg": [
                            {"ID": "two-id", "Size": 5, "Hashes": {"MD5": "0" * 32}}
                        ],
                    },
                ),
            ):
                write_valid_shared_gate(manifest, batch, rows)
                with self.assertRaisesRegex(SystemExit, "remote content confirmation failed"):
                    uploader.upload_once(args, "batch", 1)
            logged = uploader.read_csv(args.uploaded_log)
            self.assertEqual([row["file_name"] for row in logged], ["one.jpg"])
            self.assertEqual(logged[0]["drive_file_id"], "one-id")

    def test_remote_confirmation_requires_unique_size_and_md5_match(self):
        with tempfile.TemporaryDirectory() as temp:
            staged = Path(temp) / "photo.jpg"
            staged.write_bytes(b"image")
            exact = {
                "ID": "id-1",
                "Size": 5,
                "Hashes": {"MD5": hashlib.md5(b"image").hexdigest()},
            }
            self.assertEqual(uploader.confirmed_remote_entry(staged, [exact]), exact)
            bad_cases = [
                [],
                [exact, dict(exact)],
                [{**exact, "Size": 6}],
                [{**exact, "Hashes": {"MD5": "0" * 32}}],
                [{**exact, "Hashes": {}}],
            ]
            for entries in bad_cases:
                with self.subTest(entries=entries):
                    self.assertIsNone(uploader.confirmed_remote_entry(staged, entries))

    def test_remote_readback_timeout_fails_closed(self):
        args = SimpleNamespace(
            rclone=Path("rclone"),
            remote=uploader.DEFAULT_REMOTE,
            rclone_timeout_seconds=1,
        )
        with patch.object(
            uploader.subprocess,
            "run",
            side_effect=uploader.subprocess.TimeoutExpired(["rclone"], 1),
        ):
            with self.assertRaisesRegex(SystemExit, "readback timed out"):
                uploader.remote_file_map(args, "2026")

    def test_backend_idle_check_fails_closed_when_status_unreachable(self):
        with patch.object(uploader, "urlopen", side_effect=uploader.URLError("offline")):
            with self.assertRaisesRegex(SystemExit, "cannot be proven"):
                ORIGINAL_ENSURE_BACKEND_IDLE("http://127.0.0.1:5000")

    def test_backend_idle_check_rejects_actual_running_status_payload(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"is_running": true}'

        with patch.object(uploader, "urlopen", return_value=Response()):
            with self.assertRaisesRegex(SystemExit, "backend reports OCR running"):
                ORIGINAL_ENSURE_BACKEND_IDLE("http://127.0.0.1:5000")

    def test_owned_active_runner_blocks_even_when_api_could_be_idle(self):
        process = SimpleNamespace(
            info={
                "pid": 123,
                "cmdline": [
                    "python",
                    str(uploader.REPO_ROOT / "tools" / "rerun_staged_candidates.py"),
                ],
            }
        )
        with patch.object(uploader.psutil, "process_iter", return_value=[process]):
            with self.assertRaisesRegex(SystemExit, "active OCR runner"):
                ORIGINAL_ENSURE_NO_ACTIVE_OCR_RUNNER()

    def test_unapproved_remote_is_rejected_before_manifest_build(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "_drive_upload"
            manifest.mkdir()
            args = self._args(root, manifest)
            args.remote = "other_remote"
            with patch.object(uploader, "prepare_manifest") as prepare_manifest:
                with self.assertRaisesRegex(SystemExit, "approved rclone remote"):
                    uploader.upload_once(args, "batch", 1)
                prepare_manifest.assert_not_called()

    def test_noncanonical_backend_url_cannot_bypass_the_live_dashboard_backend(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "_drive_upload"
            manifest.mkdir()
            args = self._args(root, manifest)
            args.backend_url = "http://127.0.0.1:5001"
            with patch.object(uploader, "prepare_manifest") as prepare_manifest:
                with self.assertRaisesRegex(SystemExit, "canonical backend health check"):
                    uploader.upload_once(args, "batch", 1)
                prepare_manifest.assert_not_called()

    def test_requested_current_year_scope_rejects_historical_rows(self):
        args = SimpleNamespace(limit=100, years="2026")
        with self.assertRaisesRegex(SystemExit, "outside the requested scope"):
            uploader.validate_requested_scope(
                args, [{"year": "2025", "drive_folder": "2025", "file_name": "old.jpg"}]
            )

    def test_stop_on_timeout_exits_nonzero_after_content_readback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "_drive_upload"
            staging = manifest / "staging" / "2026"
            staging.mkdir(parents=True)
            row = {"source_path": "source/one.jpg", "file_name": "one.jpg", "year": "2026", "content_sha256": hashlib.sha256(b"image").hexdigest()}
            staged = staging / "one.jpg"
            staged.write_bytes(b"image")
            batch = manifest / "drive_upload_next_batch.csv"
            write_csv(batch, [row], row.keys())
            write_valid_summary(manifest, batch)
            write_valid_shared_gate(manifest, batch, [row])
            write_csv(
                manifest / "staging_map.csv",
                [{"stage_file": str(staged), **row}],
                ["stage_file", "source_path", "file_name", "year", "content_sha256"],
            )
            args = self._args(root, manifest)
            args.continue_on_timeout = False
            with (
                patch.object(uploader, "prepare_manifest", return_value=batch),
                patch.object(
                    uploader.upload_gate_authority,
                    "run",
                    return_value={"valid": True, "executed": True, "errors": []},
                ),
                patch.object(uploader, "run_command", return_value=124),
                patch.object(
                    uploader,
                    "remote_file_map",
                    return_value={
                        "one.jpg": [
                            {
                                "ID": "one-id",
                                "Size": 5,
                                "Hashes": {"MD5": hashlib.md5(b"image").hexdigest()},
                            }
                        ]
                    },
                ),
            ):
                with self.assertRaises(SystemExit) as raised:
                    uploader.upload_once(args, "batch", 1)
            self.assertEqual(raised.exception.code, 124)
            self.assertEqual(uploader.read_csv(args.uploaded_log)[0]["drive_file_id"], "one-id")

    def test_retryable_timeout_with_zero_confirmed_keeps_repeat_alive_without_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "_drive_upload"
            staging = manifest / "staging" / "2026"
            staging.mkdir(parents=True)
            row = {"source_path": "source/one.jpg", "file_name": "one.jpg", "year": "2026", "content_sha256": hashlib.sha256(b"image").hexdigest()}
            staged = staging / "one.jpg"
            staged.write_bytes(b"image")
            batch = manifest / "drive_upload_next_batch.csv"
            write_csv(batch, [row], row.keys())
            write_valid_summary(manifest, batch)
            write_valid_shared_gate(manifest, batch, [row])
            write_csv(
                manifest / "staging_map.csv",
                [{"stage_file": str(staged), **row}],
                ["stage_file", "source_path", "file_name", "year", "content_sha256"],
            )
            args = self._args(root, manifest)
            with (
                patch.object(uploader, "prepare_manifest", return_value=batch),
                patch.object(
                    uploader.upload_gate_authority,
                    "run",
                    return_value={"valid": True, "executed": True, "errors": []},
                ),
                patch.object(uploader, "run_command", return_value=124),
                patch.object(uploader, "remote_file_map", return_value={}),
            ):
                self.assertEqual(uploader.upload_once(args, "batch", 1), uploader.RETRYABLE_TIMEOUT)
            self.assertEqual(uploader.read_csv(args.uploaded_log), [])

    def test_retryable_timeout_is_nonzero_when_no_next_cycle_is_guaranteed(self):
        for extra_args in ([], ["--repeat", "--max-cycles", "1"]):
            with self.subTest(extra_args=extra_args), tempfile.TemporaryDirectory() as temp:
                argv = [
                    "rclone_drive_upload.py",
                    "--output-dir",
                    temp,
                    "--execute",
                    *extra_args,
                ]
                with (
                    patch.object(uploader.sys, "argv", argv),
                    patch.object(uploader, "resolve_rclone", return_value=Path("rclone")),
                    patch.object(uploader, "upload_once", return_value=uploader.RETRYABLE_TIMEOUT),
                ):
                    self.assertEqual(uploader.main(), 124)

    def test_batch_hash_mismatch_stops_before_rclone(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "_drive_upload"
            manifest.mkdir()
            batch = manifest / "drive_upload_next_batch.csv"
            rows = [{"source_path": "source/one.jpg", "file_name": "one.jpg", "year": "2025", "content_sha256": hashlib.sha256(b"image").hexdigest()}]
            write_csv(batch, rows, rows[0].keys())
            write_valid_summary(manifest, batch)
            batch.write_text(batch.read_text(encoding="utf-8") + "tampered", encoding="utf-8")
            args = self._args(root, manifest)
            with (
                patch.object(uploader, "prepare_manifest", return_value=batch),
                patch.object(uploader, "run_command") as run_command,
            ):
                with self.assertRaisesRegex(SystemExit, "SHA-256"):
                    uploader.upload_once(args, "batch", 1)
                run_command.assert_not_called()

    def test_2026_incomplete_or_mismatched_proof_stops_before_rclone(self):
        cases = [
            ({"gate_open": False}, "gate is not open"),
            ({"fresh": False}, "risk audit is not fresh"),
            ({"proof_overrides": {"complete": False}}, "missing or incomplete"),
            ({"proof_overrides": {"scanned_result_count": 1}}, "count mismatch"),
            ({"proof_overrides": {"missing_or_invalid": ["202602"]}}, "missing or invalid"),
            ({"proof_overrides": {"duplicate_source_identity": 1}}, "duplicate source"),
            ({"proof_overrides": {"audit_input_sha256": "0" * 64}}, "does not match"),
        ]
        for summary_args, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                manifest = root / "_drive_upload"
                manifest.mkdir()
                batch = manifest / "drive_upload_next_batch.csv"
                rows = [{"source_path": "source/one.jpg", "file_name": "one.jpg", "year": "2026"}]
                write_csv(batch, rows, rows[0].keys())
                write_valid_summary(manifest, batch, **summary_args)
                args = self._args(root, manifest)
                with (
                    patch.object(uploader, "prepare_manifest", return_value=batch),
                    patch.object(uploader, "run_command") as run_command,
                ):
                    with self.assertRaisesRegex(SystemExit, message):
                        uploader.upload_once(args, "batch", 1)
                    run_command.assert_not_called()

    def test_2026_missing_proof_field_stops_before_rclone(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "_drive_upload"
            manifest.mkdir()
            batch = manifest / "drive_upload_next_batch.csv"
            rows = [{"source_path": "source/one.jpg", "file_name": "one.jpg", "year": "2026"}]
            write_csv(batch, rows, rows[0].keys())
            write_valid_summary(manifest, batch)
            summary_path = manifest / "drive_upload_summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            del summary["current_year_finalization_proof"]["missing_or_invalid"]
            summary_path.write_text(json.dumps(summary), encoding="utf-8")
            args = self._args(root, manifest)
            with (
                patch.object(uploader, "prepare_manifest", return_value=batch),
                patch.object(uploader, "run_command") as run_command,
            ):
                with self.assertRaisesRegex(SystemExit, "missing required fields"):
                    uploader.upload_once(args, "batch", 1)
                run_command.assert_not_called()

    def test_non_2026_batch_hash_alone_cannot_authorize_direct_upload(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "_drive_upload"
            manifest.mkdir()
            batch = manifest / "drive_upload_next_batch.csv"
            rows = [{"source_path": "source/one.jpg", "file_name": "one.jpg", "year": "2025"}]
            write_csv(batch, rows, rows[0].keys())
            summary = {"next_batch_sha256": hashlib.sha256(batch.read_bytes()).hexdigest()}
            (manifest / "drive_upload_summary.json").write_text(json.dumps(summary), encoding="utf-8")
            uploader.validate_prepared_manifest(manifest, batch, rows)
            with patch.object(
                uploader.upload_gate_authority,
                "run",
                return_value={"valid": False, "executed": False, "errors": ["finalization_incomplete"]},
            ):
                with self.assertRaisesRegex(SystemExit, "shared upload gate proof is not valid"):
                    uploader.refresh_and_validate_shared_upload_gate(root, manifest, batch, rows)

    def test_historical_direct_upload_accepts_only_fresh_shared_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "_drive_upload"
            staging = manifest / "staging" / "2025"
            staging.mkdir(parents=True)
            rows = [{"source_path": "source/one.jpg", "file_name": "one.jpg", "year": "2025", "content_sha256": hashlib.sha256(b"image").hexdigest()}]
            (staging / "one.jpg").write_bytes(b"image")
            batch = manifest / "drive_upload_next_batch.csv"
            write_csv(batch, rows, rows[0].keys())
            write_valid_summary(manifest, batch)
            shared_proof = write_valid_shared_gate(manifest, batch, rows)
            write_csv(
                manifest / "staging_map.csv",
                [{"stage_file": str(staging / "one.jpg"), **rows[0]}],
                ["stage_file", "source_path", "file_name", "year", "content_sha256"],
            )
            args = self._args(root, manifest)
            with (
                patch.object(uploader, "prepare_manifest", return_value=batch),
                patch.object(
                    uploader.upload_gate_authority,
                    "run",
                    return_value={"valid": True, "executed": True, "errors": []},
                ),
                patch.object(uploader, "run_command", return_value=0),
                patch.object(
                    uploader,
                    "remote_file_map",
                    return_value={
                        "one.jpg": [
                            {
                                "ID": "one-id",
                                "Size": 5,
                                "Hashes": {"MD5": hashlib.md5(b"image").hexdigest()},
                            }
                        ]
                    },
                ),
            ):
                with self.assertRaisesRegex(SystemExit, "historical upload authorization"):
                    uploader.upload_once(args, "batch", 1)
                write_historical_authorization(root, shared_proof)
                self.assertEqual(uploader.upload_once(args, "batch", 1), 1)

    def test_historical_authorization_rejects_marker_and_inventory_tamper(self):
        rows = [{"source_path": "source/one.jpg", "file_name": "one.jpg", "year": "2025"}]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "_drive_upload"
            manifest.mkdir()
            batch = manifest / "drive_upload_next_batch.csv"
            write_csv(batch, rows, rows[0].keys())
            write_valid_summary(manifest, batch)
            proof = write_valid_shared_gate(manifest, batch, rows)
            paths = write_historical_authorization(root, proof)
            uploader.validate_historical_upload_authorization(root, rows, proof)

            marker_payload = json.loads(paths["marker"].read_text(encoding="utf-8"))
            marker_payload["tampered"] = True
            paths["marker"].write_text(json.dumps(marker_payload), encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "hash mismatch"):
                uploader.validate_historical_upload_authorization(root, rows, proof)

            paths = write_historical_authorization(root, proof)
            summary_rows = [
                {
                    "folder": "source",
                    "image_count": "1",
                    "source_latest_mtime": "100",
                    "status": "planned",
                }
            ]
            write_csv(paths["summary"], summary_rows, summary_rows[0].keys())
            authorization = json.loads(paths["authorization"].read_text(encoding="utf-8"))
            authorization["folder_summary_sha256"] = uploader.sha256_file(paths["summary"])
            paths["authorization"].write_text(json.dumps(authorization), encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "incomplete or blocked"):
                uploader.validate_historical_upload_authorization(root, rows, proof)

    def test_tampered_shared_gate_stops_before_rclone(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "_drive_upload"
            manifest.mkdir()
            rows = [{"source_path": "source/one.jpg", "file_name": "one.jpg", "year": "2025"}]
            batch = manifest / "drive_upload_next_batch.csv"
            write_csv(batch, rows, rows[0].keys())
            write_valid_summary(manifest, batch)
            write_valid_shared_gate(manifest, batch, rows, next_batch_sha256="0" * 64)
            args = self._args(root, manifest)
            with (
                patch.object(uploader, "prepare_manifest", return_value=batch),
                patch.object(
                    uploader.upload_gate_authority,
                    "run",
                    return_value={"valid": True, "executed": True, "errors": []},
                ),
                patch.object(uploader, "run_command") as run_command,
            ):
                with self.assertRaisesRegex(SystemExit, "proof hash mismatch"):
                    uploader.upload_once(args, "batch", 1)
                run_command.assert_not_called()

    def test_running_backend_blocks_after_gate_and_before_rclone(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "_drive_upload"
            manifest.mkdir()
            rows = [{"source_path": "source/one.jpg", "file_name": "one.jpg", "year": "2025"}]
            batch = manifest / "drive_upload_next_batch.csv"
            write_csv(batch, rows, rows[0].keys())
            write_valid_summary(manifest, batch)
            write_valid_shared_gate(manifest, batch, rows)
            args = self._args(root, manifest)
            with (
                patch.object(uploader, "prepare_manifest", return_value=batch),
                patch.object(
                    uploader.upload_gate_authority,
                    "run",
                    return_value={"valid": True, "executed": True, "errors": []},
                ),
                patch.object(
                    uploader,
                    "ensure_backend_idle",
                    side_effect=SystemExit("backend reports OCR running or an unknown state; upload blocked"),
                ),
                patch.object(uploader, "run_command") as run_command,
            ):
                with self.assertRaisesRegex(SystemExit, "backend reports OCR running"):
                    uploader.upload_once(args, "batch", 1)
                run_command.assert_not_called()

    def test_benchmark_lock_blocks_before_manifest_or_rclone(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "_drive_upload"
            manifest.mkdir()
            audit = root / "_ocr_audit"
            audit.mkdir()
            (audit / "model_benchmark.lock").write_text("locked", encoding="utf-8")
            args = self._args(root, manifest)
            with (
                patch.object(uploader, "prepare_manifest") as prepare_manifest,
                patch.object(uploader, "run_command") as run_command,
            ):
                with self.assertRaisesRegex(SystemExit, "benchmark/backfill lock"):
                    uploader.upload_once(args, "batch", 1)
                prepare_manifest.assert_not_called()
                run_command.assert_not_called()

    @staticmethod
    def _args(root, manifest):
        return SimpleNamespace(
            limit=1,
            manifest_dir=manifest,
            output_dir=root,
            rclone=Path("rclone"),
                remote=uploader.DEFAULT_REMOTE,
            transfers=1,
            checkers=1,
            dry_run=False,
            execute=True,
            rclone_timeout_seconds=10,
            continue_on_timeout=True,
            log_path=manifest / "upload.log",
            uploaded_log=manifest / "drive_upload_uploaded.csv",
            backend_url="http://127.0.0.1:5000",
            years="",
        )


if __name__ == "__main__":
    unittest.main()

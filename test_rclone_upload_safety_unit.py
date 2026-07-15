import csv
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


MODULE_PATH = Path(__file__).parent / "tools" / "rclone_drive_upload.py"
spec = importlib.util.spec_from_file_location("rclone_drive_upload", MODULE_PATH)
uploader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uploader)


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
        "current_year_upload_gate_open": gate_open,
        "current_year_risk_audit_fresh": fresh,
        "current_year_finalization_proof": proof,
        "current_audit_input_sha256": audit_hash,
    }
    (manifest / "drive_upload_summary.json").write_text(json.dumps(summary), encoding="utf-8")


class UploadSafetyTests(unittest.TestCase):
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

    def test_upload_logs_only_remote_files_that_are_confirmed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "_drive_upload"
            staging = manifest / "staging" / "2026"
            staging.mkdir(parents=True)
            rows = [
                {"source_path": "source/one.jpg", "file_name": "one.jpg", "year": "2026"},
                {"source_path": "source/two.jpg", "file_name": "two.jpg", "year": "2026"},
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
                    }
                    for row in rows
                ],
                ["stage_file", "source_path", "file_name"],
            )
            args = SimpleNamespace(
                limit=2,
                manifest_dir=manifest,
                output_dir=root / "output",
                rclone=Path("rclone"),
                remote="drive",
                transfers=1,
                checkers=1,
                dry_run=False,
                execute=True,
                rclone_timeout_seconds=10,
                continue_on_timeout=True,
                log_path=manifest / "upload.log",
                uploaded_log=manifest / "drive_upload_uploaded.csv",
            )
            with (
                patch.object(uploader, "prepare_manifest", return_value=manifest / "drive_upload_next_batch.csv"),
                patch.object(uploader, "run_command", return_value=0),
                patch.object(uploader, "remote_file_map", return_value={"one.jpg": {"ID": "one-id"}}),
            ):
                self.assertEqual(uploader.upload_once(args, "batch", 1), 1)
            logged = uploader.read_csv(args.uploaded_log)
            self.assertEqual([row["file_name"] for row in logged], ["one.jpg"])
            self.assertEqual(logged[0]["drive_file_id"], "one-id")

    def test_batch_hash_mismatch_stops_before_rclone(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "_drive_upload"
            manifest.mkdir()
            batch = manifest / "drive_upload_next_batch.csv"
            rows = [{"source_path": "source/one.jpg", "file_name": "one.jpg", "year": "2025"}]
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

    def test_non_2026_batch_only_requires_matching_batch_hash(self):
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

    @staticmethod
    def _args(root, manifest):
        return SimpleNamespace(
            limit=1,
            manifest_dir=manifest,
            output_dir=root / "output",
            rclone=Path("rclone"),
            remote="drive",
            transfers=1,
            checkers=1,
            dry_run=False,
            execute=True,
            rclone_timeout_seconds=10,
            continue_on_timeout=True,
            log_path=manifest / "upload.log",
            uploaded_log=manifest / "drive_upload_uploaded.csv",
        )


if __name__ == "__main__":
    unittest.main()

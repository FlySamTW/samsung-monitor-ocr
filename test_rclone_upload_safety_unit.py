import csv
import importlib.util
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
            write_csv(manifest / "drive_upload_next_batch.csv", rows, rows[0].keys())
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


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

from skills.audit_fields import EVIDENCE_GUARD_REVISION
from tools.build_v1945_evidence_backfill import stable_source_id
from tools.clear_redundant_terminal_fuse import recover
from tools.rclone_drive_upload import sha256_file


class RedundantTerminalFuseTests(unittest.TestCase):
    def test_confirmed_terminal_receipt_clears_only_the_redundant_fuse(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            output = root / "output"
            staging = output / "_ocr_staging" / "run" / "period"
            audit = output / "_ocr_audit"
            source = root / "source" / "photo.jpg"
            published = output / "published.jpg"
            for directory in (repo, staging, audit, source.parent):
                directory.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"source-pixels")
            published.write_bytes(b"published-pixels")
            source_id = stable_source_id(source)
            file_name = source.name
            (staging / ".ocr_source_map.json").write_text(
                json.dumps({"items": {file_name: {
                    "source_item_id": source_id,
                    "original_source_path": str(source),
                    "period": "202605",
                }}}),
                encoding="utf-8",
            )
            fuse = {
                "schema": "samsung-ocr-runtime-health-fuse/v1",
                "active": True,
                "source_file": file_name,
                "run_id": "redundant-run",
                "reasons": ["review_prior_fields_present"],
            }
            (audit / "runtime_health_fuse.json").write_text(
                json.dumps(fuse), encoding="utf-8"
            )
            receipt_dir = output / "_drive_upload_stream" / "receipts"
            receipt_dir.mkdir(parents=True)
            (receipt_dir / f"{source_id}.json").write_text(
                json.dumps({
                    "schema": "samsung-ocr-stream-receipt-v1",
                    "source_item_id": source_id,
                    "original_source_path": str(source),
                    "published_path": str(published),
                    "source_sha256": sha256_file(source),
                    "published_sha256": sha256_file(published),
                    "run_id": "terminal-run",
                    "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
                    "drive_file_id": "drive-id",
                    "remote_path": "remote:2026/published.jpg",
                    "confirmed_at": "2026-08-01T00:00:00",
                }),
                encoding="utf-8",
            )
            ledger = audit / "model_call_lifetime_ledger_v1" / source_id[:2]
            ledger.mkdir(parents=True)
            (ledger / f"{source_id}.json").write_text(
                json.dumps({"reservations": [{"run_id": "redundant-run"}]}),
                encoding="utf-8",
            )
            status = {"is_running": False, "image_dir": str(staging)}
            dry = recover(
                repo_root=repo,
                output_dir=output,
                staging_dir=staging,
                backend_url="unused",
                apply=False,
                status=status,
            )
            self.assertEqual(dry["status"], "would_clear")
            self.assertTrue((audit / "runtime_health_fuse.json").exists())
            applied = recover(
                repo_root=repo,
                output_dir=output,
                staging_dir=staging,
                backend_url="unused",
                apply=True,
                status=status,
            )
            self.assertEqual(applied["status"], "cleared")
            self.assertFalse((audit / "runtime_health_fuse.json").exists())
            self.assertTrue(Path(applied["clearance_receipt"]).is_file())
            self.assertTrue(Path(applied["archived_fuse"]).is_file())


if __name__ == "__main__":
    unittest.main()

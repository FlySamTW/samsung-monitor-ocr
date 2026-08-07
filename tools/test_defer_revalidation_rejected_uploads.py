import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from tools.defer_revalidation_rejected_uploads import (
    HOLD_SCHEMA,
    defer_rejected_uploads,
)


class RevalidationUploadHoldTests(unittest.TestCase):
    def _fixture(self, root: Path):
        output = root / "output"
        pending = output / "_drive_upload_stream" / "pending"
        pending.mkdir(parents=True)
        source = root / "M-test-960.jpg"
        Image.new("RGB", (8, 8), "black").save(source)
        import hashlib

        source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        source_id = "a" * 64
        job = {
            "schema": "samsung-ocr-stream-upload-v1",
            "source_item_id": source_id,
            "original_source_path": str(source),
            "source_sha256": source_sha,
            "evidence_guard_revision": "old",
            "target_name": "old.jpg",
        }
        job_path = pending / f"{source_id}.json"
        job_path.write_text(json.dumps(job), encoding="utf-8")
        manifest = root / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema": "samsung-ocr-frozen-guard-revalidation/v1",
                    "mode": "apply",
                    "old_revision": "old",
                    "current_revision": "current",
                    "rejected": [
                        {
                            "file_name": source.name,
                            "rerun_disposition": "queued_with_preserved_budget",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return output, job_path, manifest

    def test_apply_holds_only_manifest_bound_old_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            output, job_path, manifest = self._fixture(Path(tmp))
            with patch(
                "tools.defer_revalidation_rejected_uploads.EVIDENCE_GUARD_REVISION",
                "current",
            ):
                report = defer_rejected_uploads(
                    manifest_path=manifest,
                    output_dir=output,
                    old_revision="old",
                    apply=True,
                )
            self.assertEqual(report["schema"], HOLD_SCHEMA)
            self.assertEqual(report["moved_count"], 1)
            self.assertFalse(job_path.exists())
            self.assertTrue(Path(report["moved"][0]["hold_path"]).is_file())
            self.assertTrue(Path(report["receipt"]).is_file())

    def test_unlisted_old_job_fails_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            output, job_path, manifest = self._fixture(Path(tmp))
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["rejected"] = []
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with patch(
                "tools.defer_revalidation_rejected_uploads.EVIDENCE_GUARD_REVISION",
                "current",
            ):
                with self.assertRaisesRegex(RuntimeError, "lack an approved"):
                    defer_rejected_uploads(
                        manifest_path=manifest,
                        output_dir=output,
                        old_revision="old",
                        apply=True,
                    )
            self.assertTrue(job_path.is_file())


if __name__ == "__main__":
    unittest.main()

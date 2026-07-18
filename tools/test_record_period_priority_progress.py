import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.record_period_priority_progress import (
    SUMMARY_FIELDS,
    record_progress,
)


class PeriodPriorityProgressTests(unittest.TestCase):
    def _fixture(self, root: Path):
        output = root / "output"
        audit = output / "_ocr_audit"
        audit.mkdir(parents=True)
        source = root / "source" / "period"
        source.mkdir(parents=True)
        staging = root / "staging"
        staging.mkdir()
        names = ["one.jpg", "two.jpg"]
        items = {}
        tasks = []
        for index, name in enumerate(names):
            original = source / name
            original.write_bytes(f"source-{index}".encode())
            (staging / name).write_bytes(f"staged-{index}".encode())
            source_id = hashlib.sha256(f"id-{index}".encode()).hexdigest()
            items[name] = {
                "source_item_id": source_id,
                "original_source_path": str(original),
                "period": "202606",
            }
            tasks.append(
                {
                    "data": {
                        "image": f"/data/upload/1/{name}",
                        "ocr_meta": {
                            "auto_verified": True,
                            "auto_review_required": False,
                            "evidence_contract_valid": True,
                            "evidence_guard_revision": "20260718.52",
                        },
                    }
                }
            )
        source_map = staging / ".ocr_source_map.json"
        source_map.write_text(
            json.dumps({"version": 1, "items": items}),
            encoding="utf-8",
        )
        (staging / ".period_priority_manifest.json").write_text(
            json.dumps(
                {
                    "schema": "samsung-ocr-period-priority/v1",
                    "complete": True,
                    "period": "202606",
                    "source_folder": str(source),
                    "staging_dir": str(staging),
                    "image_count": 2,
                    "source_map": str(source_map),
                    "source_map_sha256": hashlib.sha256(
                        source_map.read_bytes()
                    ).hexdigest(),
                }
            ),
            encoding="utf-8",
        )
        (staging / "run-OCR成功.json").write_text(
            json.dumps(tasks),
            encoding="utf-8",
        )
        with (audit / "folder_discovery.csv").open(
            "w", encoding="utf-8-sig", newline=""
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "order",
                    "folder_id",
                    "folder",
                    "period",
                    "image_count",
                    "latest_mtime",
                    "source_inventory_sha256",
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "order": "1",
                    "folder_id": "f" * 64,
                    "folder": str(source),
                    "period": "202606",
                    "image_count": "2",
                    "latest_mtime": "2026-07-01T00:00:00",
                }
            )
        with (audit / "folder_summary.csv").open(
            "w", encoding="utf-8-sig", newline=""
        ) as handle:
            csv.DictWriter(handle, fieldnames=SUMMARY_FIELDS).writeheader()
        return output, source, staging

    def test_exact_verified_set_updates_only_ocr_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            output, source, staging = self._fixture(Path(tmp))
            dry = record_progress(
                output_dir=output,
                staging_dir=staging,
                source_folder=source,
                period="202606",
                apply=False,
            )
            self.assertEqual(dry["status"], "would_write")
            self.assertFalse(dry["drive_upload_complete"])
            self.assertEqual(
                (output / "_ocr_audit" / "folder_summary.csv")
                .read_text(encoding="utf-8-sig")
                .count("\n"),
                1,
            )

            report = record_progress(
                output_dir=output,
                staging_dir=staging,
                source_folder=source,
                period="202606",
                apply=True,
            )
            self.assertEqual(report["processed_tasks"], 2)
            self.assertEqual(report["current_guard_final_tasks"], 2)
            self.assertEqual(report["nonfinal_tasks"], 0)
            with (output / "_ocr_audit" / "folder_summary.csv").open(
                "r", encoding="utf-8-sig", newline=""
            ) as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["processed"], "2")
            self.assertEqual(rows[0]["ready"], "0")
            self.assertEqual(rows[0]["success_records"], "0")
            self.assertEqual(rows[0]["copied_count"], "0")
            self.assertEqual(
                rows[0]["status"], "period_priority_processed_unexported"
            )
            manifest = json.loads(
                Path(report["manifest_path"]).read_text(encoding="utf-8")
            )
            self.assertFalse(manifest["drive_upload_complete"])
            self.assertEqual(manifest["claim"], "ocr_progress_only")

    def test_missing_processed_task_set_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            output, source, staging = self._fixture(Path(tmp))
            payload = json.loads(
                (staging / "run-OCR成功.json").read_text(encoding="utf-8")
            )
            payload = payload[:1]
            (staging / "run-OCR成功.json").write_text(
                json.dumps(payload),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "task set"):
                record_progress(
                    output_dir=output,
                    staging_dir=staging,
                    source_folder=source,
                    period="202606",
                    apply=True,
                )
            with (output / "_ocr_audit" / "folder_summary.csv").open(
                "r", encoding="utf-8-sig", newline=""
            ) as handle:
                self.assertEqual(list(csv.DictReader(handle)), [])

    def test_stale_guard_counts_as_processed_but_not_current_guard_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            output, source, staging = self._fixture(Path(tmp))
            result_path = staging / "run-OCR成功.json"
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            payload[0]["data"]["ocr_meta"]["evidence_guard_revision"] = "20260717.41"
            result_path.write_text(json.dumps(payload), encoding="utf-8")
            report = record_progress(
                output_dir=output,
                staging_dir=staging,
                source_folder=source,
                period="202606",
                apply=True,
            )
            self.assertEqual(report["processed_tasks"], 2)
            self.assertEqual(report["current_guard_final_tasks"], 1)
            self.assertEqual(report["nonfinal_tasks"], 1)
            self.assertEqual(report["stale_guard_tasks"], 1)
            with (output / "_ocr_audit" / "folder_summary.csv").open(
                "r", encoding="utf-8-sig", newline=""
            ) as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["processed"], "2")
            self.assertEqual(row["ready"], "0")


if __name__ == "__main__":
    unittest.main()

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.finalize_safe_consumed_cap_batch import finalize_batch


class SafeConsumedCapBatchTests(unittest.TestCase):
    def _fixture(self, root: Path, *, conflicting: bool = False):
        staging = root / "staging"
        output = root / "output"
        source = root / "source"
        staging.mkdir()
        output.mkdir()
        source.mkdir()
        result_file = staging / "result.json"
        result_file.write_text("[]", encoding="utf-8")
        trace = root / "trace.jsonl"
        items = {}
        rows = []
        names = ["safe.jpg", "conflict.jpg"] if conflicting else ["safe.jpg"]
        for photo_index, name in enumerate(names, start=1):
            source_id = hashlib.sha256(name.encode()).hexdigest()
            original = source / name
            pixels = f"pixels-{name}".encode()
            original.write_bytes(pixels)
            (staging / name).write_bytes(pixels)
            items[name] = {
                "source_item_id": source_id,
                "original_source_path": str(original),
                "period": "202601",
            }
            for run_index in range(3):
                historical = root / f"run-{run_index}" / name
                historical.parent.mkdir(exist_ok=True)
                historical.write_bytes(pixels)
                price = (
                    "7990"
                    if name == "conflict.jpg" and run_index == 2
                    else "6990"
                )
                raw = {
                    "request_id": f"{photo_index * 100 + run_index:032x}",
                    "narration": "同機側標與同機價牌清楚可讀。",
                    "view_type": "單機",
                    "screen_status": "正常",
                    "quality_issue": "無",
                    "model": "S32CG552EC",
                    "price": price,
                    "complete_screen_count": 1,
                    "unique_main": True,
                    "label_ownership": "matched",
                    "followme_physical_evidence": [],
                }
                parsed = {
                    **raw,
                    "input_image_sha256": hashlib.sha256(
                        b"prepared-" + pixels
                    ).hexdigest(),
                    "request_id_verified": True,
                    "request_binding_enforced": True,
                    "independent_pass": True,
                    "prior_answer_exposed": False,
                    "prompt_contamination": False,
                    "runtime_health": {"healthy": True, "reasons": []},
                }
                rows.append(
                    {
                        "source_item_id": source_id,
                        "file_name": name,
                        "run_id": f"run-{run_index}",
                        "attempt": 1,
                        "timestamp": f"2026-07-2{run_index}T00:00:00",
                        "source_path": str(historical),
                        "original_source_path": str(original),
                        "raw_objects": [json.dumps(raw, ensure_ascii=False)],
                        "parsed_output": parsed,
                    }
                )
        (staging / ".ocr_source_map.json").write_text(
            json.dumps({"version": 1, "items": items}, ensure_ascii=False),
            encoding="utf-8",
        )
        (staging / ".ocr_retry_queue.json").write_text(
            json.dumps(
                {
                    "image_dir": str(staging),
                    "auto_attempts": {},
                    "auto_result_history": {},
                    "retry_queue": [],
                    "priority_queue": [],
                }
            ),
            encoding="utf-8",
        )
        trace.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        return staging, trace, result_file, output

    def test_dry_run_single_scan_finds_safe_and_rejects_incompatible(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging, trace, result_file, output = self._fixture(
                Path(tmp), conflicting=True
            )
            report = finalize_batch(
                staging_dir=staging,
                trace_path=trace,
                result_file=result_file,
                upload_output_dir=output,
                include_incompatible=True,
            )
            self.assertEqual(report["status"], "dry_run")
            self.assertEqual(report["trace_scan_passes"], 1)
            self.assertEqual(report["safe_count"], 1)
            self.assertEqual(report["incompatible_count"], 1)
            self.assertEqual(report["candidates"][0]["file_name"], "safe.jpg")
            self.assertEqual(
                report["incompatible"][0]["file_name"], "conflict.jpg"
            )
            self.assertEqual(report["model_calls_made"], 0)
            self.assertEqual(json.loads(result_file.read_text()), [])

    def test_apply_is_idempotent_and_enqueues_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging, trace, result_file, output = self._fixture(Path(tmp))
            enqueue_calls = []

            def enqueue(row, *, output_dir):
                enqueue_calls.append(row["source_item_id"])
                path = (
                    output_dir
                    / "_drive_upload_stream"
                    / "pending"
                    / f"{row['source_item_id']}.json"
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}", encoding="utf-8")
                return path

            with patch(
                "tools.recover_consumed_cap_missing_result.enqueue_finalized_result",
                side_effect=enqueue,
            ):
                first = finalize_batch(
                    staging_dir=staging,
                    trace_path=trace,
                    result_file=result_file,
                    upload_output_dir=output,
                    apply=True,
                )
                second = finalize_batch(
                    staging_dir=staging,
                    trace_path=trace,
                    result_file=result_file,
                    upload_output_dir=output,
                    apply=True,
                )

            self.assertEqual(first["safe_count"], 1)
            self.assertEqual(first["applied_count"], 1)
            self.assertEqual(second["safe_count"], 0)
            self.assertEqual(second["applied_count"], 0)
            self.assertEqual(second["already_finalized_count"], 1)
            self.assertEqual(len(enqueue_calls), 1)
            self.assertEqual(
                len(json.loads(result_file.read_text(encoding="utf-8"))), 1
            )

    def test_existing_terminal_is_a_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging, trace, result_file, output = self._fixture(Path(tmp))
            result_file.write_text(
                json.dumps(
                    [
                        {
                            "id": 1,
                            "data": {"image": "/data/upload/1/safe.jpg"},
                            "annotations": [],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            report = finalize_batch(
                staging_dir=staging,
                trace_path=trace,
                result_file=result_file,
                upload_output_dir=output,
                apply=True,
            )
            self.assertEqual(report["already_finalized_count"], 1)
            self.assertEqual(report["pending_count"], 0)
            self.assertEqual(report["safe_count"], 0)
            self.assertEqual(report["applied_count"], 0)


if __name__ == "__main__":
    unittest.main()

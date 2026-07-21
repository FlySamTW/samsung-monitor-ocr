import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.build_visual_authority_manifest import _select_clean_capped_run, build_manifest
from tools.finalize_existing_three_pass_reviews import load_authority_manifest


class VisualAuthorityManifestTests(unittest.TestCase):
    def test_clean_capped_tail_can_cross_a_process_run_boundary(self):
        image_hash = "b" * 64
        base = {
            "input_image_sha256": image_hash,
            "request_id_verified": True,
            "request_binding_enforced": True,
            "independent_pass": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
            "runtime_health": {"healthy": True, "reasons": []},
        }
        groups = [
            [{**base, "ocr_attempt": 1, "timestamp": "2026-07-21T01:00:00"}],
            [{**base, "ocr_attempt": 3, "timestamp": "2026-07-21T01:01:00"}],
        ]
        selected = _select_clean_capped_run(groups)
        self.assertEqual([row["ocr_attempt"] for row in selected], [1, 3])

    def test_manifest_binds_decision_to_source_and_clean_capped_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = root / "staging"
            staging.mkdir()
            file_name = "one.jpg"
            source_id = "a" * 64
            image_hash = "b" * 64
            original = root / "source" / file_name
            original.parent.mkdir()
            original.write_bytes(b"exact-source-pixels")
            (staging / file_name).write_bytes(b"staged")
            (staging / ".ocr_source_map.json").write_text(
                json.dumps(
                    {
                        "items": {
                            file_name: {
                                "source_item_id": source_id,
                                "original_source_path": str(original),
                                "period": "202606",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            clean = {
                "input_image_sha256": image_hash,
                "request_id_verified": True,
                "request_binding_enforced": True,
                "independent_pass": True,
                "prior_answer_exposed": False,
                "prompt_contamination": False,
                "runtime_health": {"healthy": True, "reasons": []},
            }
            trace = root / "trace.jsonl"
            trace.write_text(
                "".join(
                    json.dumps(
                        {
                            "source_item_id": source_id,
                            "file_name": file_name,
                            "run_id": "run-one",
                            "attempt": attempt,
                            "timestamp": f"2026-07-18T00:00:0{attempt}",
                            "parsed_output": clean,
                        }
                    )
                    + "\n"
                    for attempt in (1, 2, 3)
                ),
                encoding="utf-8",
            )
            decisions = root / "decisions.json"
            decisions.write_text(
                json.dumps(
                    {
                        "schema": "samsung-ocr-visual-decisions/v1",
                        "period": "202606",
                        "decisions": [
                            {
                                "file_name": file_name,
                                "view_type": "遠景",
                                "complete_screen_count": 3,
                                "model": None,
                                "price": None,
                                "label_ownership": "ambiguous",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            manifest = root / "manifest.json"
            report = build_manifest(
                staging_dir=staging,
                trace_path=trace,
                decisions_path=decisions,
                output_path=manifest,
                apply=True,
            )
            self.assertEqual(report["entry_count"], 1)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            entry = payload["entries"][0]
            self.assertEqual(entry["source_item_id"], source_id)
            self.assertEqual(entry["input_image_sha256"], image_hash)
            self.assertEqual(
                entry["source_file_sha256"],
                hashlib.sha256(original.read_bytes()).hexdigest(),
            )
            with patch.dict(
                "tools.finalize_existing_three_pass_reviews.KNOWN_SOURCE_EXPECTATIONS",
                {},
                clear=True,
            ) as expectations:
                self.assertEqual(load_authority_manifest(manifest), 1)
                self.assertEqual(expectations[image_hash], entry)

    def test_manifest_rejects_technical_runtime_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = root / "staging"
            staging.mkdir()
            file_name = "one.jpg"
            source_id = "a" * 64
            original = root / file_name
            original.write_bytes(b"pixels")
            (staging / file_name).write_bytes(b"staged")
            (staging / ".ocr_source_map.json").write_text(
                json.dumps(
                    {
                        "items": {
                            file_name: {
                                "source_item_id": source_id,
                                "original_source_path": str(original),
                                "period": "202606",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            row = {
                "input_image_sha256": "b" * 64,
                "request_id_verified": True,
                "request_binding_enforced": True,
                "independent_pass": True,
                "prior_answer_exposed": False,
                "prompt_contamination": False,
                "runtime_health": {
                    "healthy": False,
                    "reasons": ["request_binding_unverified"],
                },
            }
            trace = root / "trace.jsonl"
            trace.write_text(
                "".join(
                    json.dumps(
                        {
                            "source_item_id": source_id,
                            "file_name": file_name,
                            "run_id": "run-one",
                            "attempt": attempt,
                            "parsed_output": row,
                        }
                    )
                    + "\n"
                    for attempt in (1, 2, 3)
                ),
                encoding="utf-8",
            )
            decisions = root / "decisions.json"
            decisions.write_text(
                json.dumps(
                    {
                        "schema": "samsung-ocr-visual-decisions/v1",
                        "period": "202606",
                        "decisions": [
                            {
                                "file_name": file_name,
                                "view_type": "遠景",
                                "complete_screen_count": 3,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "no clean capped run"):
                build_manifest(
                    staging_dir=staging,
                    trace_path=trace,
                    decisions_path=decisions,
                    output_path=root / "manifest.json",
                    apply=False,
                )


if __name__ == "__main__":
    unittest.main()

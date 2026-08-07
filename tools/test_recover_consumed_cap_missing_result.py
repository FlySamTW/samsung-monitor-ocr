import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from tools.recover_consumed_cap_missing_result import (
    _classify_bound_calls,
    _load_cross_run_trace_calls,
    _raw_structured_distant_consensus,
    _raw_structured_single_consensus,
    _validate_three_trace_bindings,
    recover,
)


class ConsumedCapMissingResultRecoveryTests(unittest.TestCase):
    def test_cross_run_clean_same_input_consensus_is_not_cherry_picked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / "source" / "photo.jpg"
            staged = root / "current" / "photo.jpg"
            original.parent.mkdir()
            staged.parent.mkdir()
            pixels = b"stable-source-pixels"
            original.write_bytes(pixels)
            staged.write_bytes(pixels)
            source_item_id = "a" * 64
            input_hash = "b" * 64
            trace = root / "trace.jsonl"
            payloads = []
            for index in range(4):
                historical = root / f"run-{index}" / "photo.jpg"
                historical.parent.mkdir()
                historical.write_bytes(pixels)
                request_id = f"{index + 1:032x}"
                raw = {
                    "request_id": request_id,
                    "narration": "同機側標 S32CG552EC，同機價牌 6,990。",
                    "view_type": "單機",
                    "screen_status": "正常",
                    "quality_issue": "無",
                    "model": "S32CG552EC",
                    "price": "6990",
                    "complete_screen_count": 1,
                    "unique_main": True,
                    "label_ownership": "matched",
                    "followme_physical_evidence": [],
                }
                payloads.append(
                    {
                        "source_item_id": source_item_id,
                        "file_name": "photo.jpg",
                        "run_id": f"run-{index}",
                        "attempt": 1,
                        "timestamp": f"2026-07-2{index}T00:00:00",
                        "source_path": str(historical),
                        "original_source_path": str(original),
                        "raw_objects": [json.dumps(raw, ensure_ascii=False)],
                        "parsed_output": {
                            **raw,
                            "input_image_sha256": input_hash,
                            "request_id_verified": True,
                            "request_binding_enforced": True,
                            "independent_pass": True,
                            "prior_answer_exposed": False,
                            "prompt_contamination": False,
                            "runtime_health": {
                                "healthy": True,
                                "reasons": [],
                            },
                        },
                    }
                )
            trace.write_text(
                "".join(
                    json.dumps(item, ensure_ascii=False) + "\n"
                    for item in payloads
                ),
                encoding="utf-8",
            )
            calls = _load_cross_run_trace_calls(
                trace,
                source_item_id=source_item_id,
                file_name="photo.jpg",
            )
            self.assertEqual(len(calls), 3)
            self.assertEqual(len({item["run_id"] for item in calls}), 3)
            self.assertEqual(
                _validate_three_trace_bindings(
                    calls,
                    staged_path=staged,
                    original_source=original,
                    allow_cross_run=True,
                ),
                input_hash,
            )
            self.assertEqual(
                _raw_structured_single_consensus(calls)["model"],
                "S32CG552EC",
            )

            conflicting = json.loads(json.dumps(payloads))
            conflict_raw = json.loads(conflicting[-1]["raw_objects"][0])
            conflict_raw["price"] = "7990"
            conflicting[-1]["raw_objects"] = [
                json.dumps(conflict_raw, ensure_ascii=False)
            ]
            conflicting[-1]["parsed_output"]["price"] = "7990"
            trace.write_text(
                "".join(
                    json.dumps(item, ensure_ascii=False) + "\n"
                    for item in conflicting
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "three clean same-input"):
                _load_cross_run_trace_calls(
                    trace,
                    source_item_id=source_item_id,
                    file_name="photo.jpg",
                )

    def test_three_bound_raw_single_outputs_recover_exact_model_price_consensus(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staged = root / "current-staging" / "photo.jpg"
            original = root / "source" / "photo.jpg"
            staged.parent.mkdir()
            original.parent.mkdir()
            staged.write_bytes(b"same-source-pixels")
            original.write_bytes(b"same-source-pixels")
            image_hash = "d" * 64
            model_labels = (
                "Samsung Odyssey OLED G8",
                "Samsung Odyssey OLED G8 S32DG802SC",
                "Samsung Odyssey OLED G8 S32DG802SC",
            )
            calls = []
            for attempt, model_label in enumerate(model_labels, start=1):
                raw = {
                    "request_id": f"{attempt:032x}",
                    "narration": (
                        "主角自己的實體標籤可讀 S32DG802SC，售價 36,900。"
                    ),
                    "view_type": "單機",
                    "screen_status": "正常",
                    "quality_issue": "無",
                    "model": model_label,
                    "price": "36900",
                    "complete_screen_count": 1,
                    "unique_main": True,
                    "label_ownership": "matched",
                    "followme_physical_evidence": [],
                }
                calls.append(
                    {
                        "ocr_attempt": attempt,
                        "input_image_sha256": image_hash,
                        "request_id_verified": True,
                        "request_binding_enforced": True,
                        "independent_pass": True,
                        "prior_answer_exposed": False,
                        "prompt_contamination": False,
                        "runtime_health": {"healthy": True, "reasons": []},
                        "_trace_source_path": str(staged),
                        "_trace_original_source_path": str(original),
                        "_trace_raw_objects": [json.dumps(raw, ensure_ascii=False)],
                    }
                )

            self.assertEqual(
                _validate_three_trace_bindings(
                    calls,
                    staged_path=staged,
                    original_source=original,
                ),
                image_hash,
            )
            consensus = _raw_structured_single_consensus(calls)
            self.assertEqual(consensus["model"], "S32DG802SC")
            self.assertEqual(consensus["price"], "36900")
            self.assertEqual(consensus["label_ownership"], "matched")

            exposed = deepcopy(calls)
            exposed[1]["prior_answer_exposed"] = True
            with self.assertRaises(RuntimeError):
                _validate_three_trace_bindings(
                    exposed,
                    staged_path=staged,
                    original_source=original,
                )

            contaminated = deepcopy(calls)
            contaminated[1]["prompt_contamination"] = True
            with self.assertRaises(RuntimeError):
                _validate_three_trace_bindings(
                    contaminated,
                    staged_path=staged,
                    original_source=original,
                )

            wrong_source = deepcopy(calls)
            wrong_source[2]["_trace_source_path"] = str(root / "other.jpg")
            with self.assertRaises(RuntimeError):
                _validate_three_trace_bindings(
                    wrong_source,
                    staged_path=staged,
                    original_source=original,
                )

            disagreement = deepcopy(calls)
            raw = json.loads(disagreement[2]["_trace_raw_objects"][0])
            raw["price"] = "36990"
            disagreement[2]["_trace_raw_objects"] = [
                json.dumps(raw, ensure_ascii=False)
            ]
            with self.assertRaises(RuntimeError):
                _raw_structured_single_consensus(disagreement)

    def _make_bound_distant_calls(self, root: Path):
        file_name = "photo.jpg"
        staged = root / "current-staging" / file_name
        original = root / "source" / file_name
        staged.parent.mkdir()
        original.parent.mkdir()
        pixels = b"immutable-distant-scene-pixels"
        staged.write_bytes(pixels)
        original.write_bytes(pixels)
        image_hash = "e" * 64
        calls = []
        for attempt, count in enumerate((7, 8, 7), start=1):
            historical = root / f"historical-staging-{attempt}" / file_name
            historical.parent.mkdir()
            historical.write_bytes(pixels)
            raw = {
                "request_id": f"{attempt + 100:032x}",
                "narration": (
                    f"賣場中至少有 {count} 台完整一般桌上螢幕；沒有唯一主體，"
                    "沒有同主體 FollowMe 強實體證據。"
                ),
                "view_type": "遠景",
                "screen_status": "",
                "quality_issue": "無",
                "model": None,
                "price": None,
                "complete_screen_count": count,
                "unique_main": False,
                "label_ownership": "not_visible",
                "followme_physical_evidence": [],
                "wide_scene_followme_present": False,
            }
            calls.append(
                {
                    "ocr_attempt": attempt,
                    "input_image_sha256": image_hash,
                    "request_id_verified": True,
                    "request_binding_enforced": True,
                    "independent_pass": True,
                    "prior_answer_exposed": False,
                    "prompt_contamination": False,
                    "runtime_health": {"healthy": True, "reasons": []},
                    "_trace_source_path": str(historical),
                    "_trace_original_source_path": str(original),
                    "_trace_raw_objects": [json.dumps(raw, ensure_ascii=False)],
                }
            )
        return staged, original, image_hash, calls

    def test_three_bound_raw_distant_outputs_recover_with_conservative_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged, original, image_hash, calls = self._make_bound_distant_calls(
                Path(tmp)
            )
            self.assertEqual(
                _validate_three_trace_bindings(
                    calls,
                    staged_path=staged,
                    original_source=original,
                ),
                image_hash,
            )
            consensus = _raw_structured_distant_consensus(calls)
            self.assertEqual(consensus["view_type"], "遠景")
            self.assertEqual(consensus["complete_screen_count"], 7)
            self.assertIsNone(consensus["model"])
            self.assertIsNone(consensus["price"])

    def test_distant_recovery_rejects_missing_historical_source_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged, original, _, calls = self._make_bound_distant_calls(Path(tmp))
            Path(calls[2]["_trace_source_path"]).unlink()
            with self.assertRaisesRegex(RuntimeError, "historical trace source file"):
                _validate_three_trace_bindings(
                    calls,
                    staged_path=staged,
                    original_source=original,
                )

    def test_distant_recovery_rejects_historical_source_byte_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged, original, _, calls = self._make_bound_distant_calls(Path(tmp))
            Path(calls[1]["_trace_source_path"]).write_bytes(b"different-photo")
            with self.assertRaisesRegex(RuntimeError, "historical trace source bytes"):
                _validate_three_trace_bindings(
                    calls,
                    staged_path=staged,
                    original_source=original,
                )

    def test_distant_recovery_rejects_same_subject_strong_followme_cue(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, _, calls = self._make_bound_distant_calls(Path(tmp))
            raw = json.loads(calls[1]["_trace_raw_objects"][0])
            raw["followme_physical_evidence"] = [
                {
                    "cue": "direct_followme_branding_on_unit",
                    "same_subject": True,
                    "strength": "direct",
                }
            ]
            calls[1]["_trace_raw_objects"] = [
                json.dumps(raw, ensure_ascii=False)
            ]
            with self.assertRaisesRegex(RuntimeError, "strong FollowMe evidence"):
                _raw_structured_distant_consensus(calls)

    def test_distant_recovery_rejects_material_content_disagreement(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, _, calls = self._make_bound_distant_calls(Path(tmp))
            raw = json.loads(calls[2]["_trace_raw_objects"][0])
            raw["view_type"] = "單機"
            raw["complete_screen_count"] = 1
            raw["unique_main"] = True
            raw["label_ownership"] = "matched"
            raw["model"] = "S27CG552EC"
            raw["price"] = "4990"
            calls[2]["_trace_raw_objects"] = [
                json.dumps(raw, ensure_ascii=False)
            ]
            with self.assertRaisesRegex(RuntimeError, "do not agree"):
                _raw_structured_distant_consensus(calls)

    def test_one_clean_plus_one_contained_same_photo_output_is_narrowly_allowed(self):
        image_hash = "b" * 64
        base = {
            "input_image_sha256": image_hash,
            "request_id_verified": True,
            "request_binding_enforced": True,
            "independent_pass": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
        }
        clean = {**base, "runtime_health": {"healthy": True, "reasons": []}}
        contained = {
            **base,
            "runtime_health": {
                "healthy": False,
                "reasons": ["structured_narration_followme_conflict"],
            },
        }
        self.assertEqual(_classify_bound_calls([contained, clean], image_hash), (1, 1))

        contaminated = {**contained, "prompt_contamination": True}
        with self.assertRaises(RuntimeError):
            _classify_bound_calls([contaminated, clean], image_hash)

        unrelated_failure = {
            **base,
            "runtime_health": {
                "healthy": False,
                "reasons": ["model_endpoint_failed"],
            },
        }
        with self.assertRaises(RuntimeError):
            _classify_bound_calls([unrelated_failure, clean], image_hash)

    def test_empty_model_authority_can_contain_matching_material_conflict(self):
        image_hash = "c" * 64
        base = {
            "input_image_sha256": image_hash,
            "request_id_verified": True,
            "request_binding_enforced": True,
            "independent_pass": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
            "model": None,
        }
        clean = {**base, "runtime_health": {"healthy": True, "reasons": []}}
        contained = {
            **base,
            "runtime_health": {
                "healthy": False,
                "reasons": ["structured_authority_material_conflict:model"],
            },
        }
        authority = {
            "authority": "human_audited_pixel_authority",
            "model": None,
        }
        self.assertEqual(
            _classify_bound_calls([contained, clean], image_hash, authority),
            (1, 1),
        )
        with self.assertRaises(RuntimeError):
            _classify_bound_calls([contained, clean], image_hash)
        with self.assertRaises(RuntimeError):
            _classify_bound_calls(
                [contained, clean],
                image_hash,
                {**authority, "model": "S27DG602SC"},
            )

    def test_two_clean_outputs_and_consumed_cap_finalize_without_call_four(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = root / "staging"
            output = root / "output"
            staging.mkdir()
            output.mkdir()
            file_name = "photo.jpg"
            source_id = "a" * 64
            image_hash = "b" * 64
            original = root / "source" / file_name
            original.parent.mkdir()
            original.write_bytes(b"original-pixels")
            (staging / file_name).write_bytes(b"staged-pixels")
            source_hash = hashlib.sha256(original.read_bytes()).hexdigest()
            authority = {
                "source_file_sha256": source_hash,
                "input_image_sha256": image_hash,
                "view_type": "遠景",
                "complete_screen_count": 3,
                "model": None,
                "price": None,
                "label_ownership": "ambiguous",
                "followme_physical_expected": False,
                "authority": "human_audited_pixel_authority",
            }
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
                "view_type": "遠景",
                "category": "遠景",
                "model": None,
                "price": None,
                "screen_status": "",
                "quality_issue": "",
                "complete_screen_count": 3,
                "unique_main": False,
                "label_ownership": "ambiguous",
                "followme_physical_evidence": [],
                "input_image_sha256": image_hash,
                "request_id_verified": True,
                "request_binding_enforced": True,
                "independent_pass": True,
                "prior_answer_exposed": False,
                "prompt_contamination": False,
                "runtime_health": {"healthy": True},
            }
            (staging / ".ocr_retry_queue.json").write_text(
                json.dumps(
                    {
                        "image_dir": str(staging),
                        "auto_attempts": {file_name: 3},
                        "auto_result_history": {file_name: [clean, clean]},
                        "retry_queue": [file_name],
                        "priority_queue": [file_name],
                    }
                ),
                encoding="utf-8",
            )
            trace = root / "trace.jsonl"
            rows = []
            for attempt in (1, 2):
                rows.append(
                    {
                        "source_item_id": source_id,
                        "file_name": file_name,
                        "run_id": "run-one",
                        "attempt": attempt,
                        "timestamp": f"2026-07-18T00:00:0{attempt}",
                        "parsed_output": clean,
                    }
                )
            trace.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            result = staging / "recovery-OCR成功.json"
            pending = output / "_drive_upload_stream" / "pending" / f"{source_id}.json"
            pending.parent.mkdir(parents=True)

            def enqueue(row, *, output_dir):
                self.assertTrue(row["auto_verified"])
                self.assertEqual(
                    row["adjudication_rule"],
                    "two_clean_outputs_plus_consumed_cap_visual_authority",
                )
                self.assertEqual(row["model_outputs_available"], 2)
                pending.write_text("{}", encoding="utf-8")
                return pending

            with (
                patch.dict(
                    "tools.recover_consumed_cap_missing_result.KNOWN_SOURCE_AUDIT_AUTHORITIES",
                    {source_id: authority},
                    clear=True,
                ),
                patch.dict(
                    "tools.recover_consumed_cap_missing_result.KNOWN_SOURCE_EXPECTATIONS",
                    {image_hash: authority},
                    clear=True,
                ),
                patch(
                    "tools.recover_consumed_cap_missing_result.enqueue_finalized_result",
                    side_effect=enqueue,
                ),
            ):
                report = recover(
                    staging_dir=staging,
                    trace_path=trace,
                    result_file=result,
                    upload_output_dir=output,
                    file_name=file_name,
                    apply=True,
                )

            self.assertEqual(report["model_calls_consumed"], 3)
            self.assertEqual(report["model_outputs_available"], 2)
            self.assertFalse(report["fourth_call_made"])
            tasks = json.loads(result.read_text(encoding="utf-8"))
            self.assertEqual(len(tasks), 1)
            meta = tasks[0]["data"]["ocr_meta"]
            self.assertTrue(meta["auto_verified"])
            self.assertFalse(meta["auto_review_required"])
            retry = json.loads(
                (staging / ".ocr_retry_queue.json").read_text(encoding="utf-8")
            )
            self.assertNotIn(file_name, retry["auto_attempts"])
            self.assertNotIn(file_name, retry["auto_result_history"])
            self.assertEqual(retry["retry_queue"], [])


if __name__ == "__main__":
    unittest.main()

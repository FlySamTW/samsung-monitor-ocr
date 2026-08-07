from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from skills.audit_fields import EVIDENCE_GUARD_REVISION
from tools import revalidate_completed_source_results as target


OLD_REVISION = "20260803.92"
SOURCE_ID = "a" * 64


def _task(name: str) -> dict:
    return {
        "id": 1,
        "data": {
            "image": f"/data/upload/1/{name}",
            "ocr_meta": {
                "evidence_guard_revision": OLD_REVISION,
                "auto_verified": True,
                "auto_review_required": False,
                "ocr_attempt": 3,
            },
        },
        "annotations": [
            {
                "result": [
                    {
                        "from_name": "category",
                        "value": {"choices": ["單機"]},
                    },
                    {"from_name": "model", "value": {"text": ["null"]}},
                    {"from_name": "price", "value": {"text": ["null"]}},
                ]
            }
        ],
    }


def _technical_task(name: str) -> dict:
    task = _task(name)
    meta = task["data"]["ocr_meta"]
    meta.update(
        auto_verified=False,
        auto_review_required=True,
        review_status="技術錯誤／已停止該張上傳",
        auto_retry_reasons=(
            "three_pass_current_integrity_invalid；"
            "structured_narration_followme_conflict；"
            "three_call_hard_limit_reached"
        ),
    )
    for row in task["annotations"][0]["result"]:
        if row.get("from_name") == "model":
            row["value"] = {"text": ["C24F390FHE"]}
        elif row.get("from_name") == "price":
            row["value"] = {"text": ["4290"]}
    return task


def _row(source: Path, attempt: int) -> dict:
    return {
        "attempt": attempt,
        "run_id": "run-1",
        "source_item_id": SOURCE_ID,
        "source_path": str(source),
        "original_source_path": str(source),
        "period": "202201",
        "parsed_output": {
            "request_id_verified": True,
            "independent_pass": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
        },
    }


def _semantic() -> dict:
    return {
        "final": {
            "view_type": "單機",
            "unique_main": True,
            "complete_screen_count": 1,
            "label_ownership": "matched",
            "model": "F24T350FHC",
            "price": "3990",
        },
        "recovery": {
            "model": "F24T350FHC",
            "price": "3990",
            "mode": "three_pass_first_letter_tail_with_one_validated_model",
        },
    }


def _corrected(source: Path) -> dict:
    return {
        "view_type": "單機",
        "category": "單機",
        "model": "F24T350FHC",
        "price": "3990",
        "complete_screen_count": 1,
        "unique_main": True,
        "label_ownership": "matched",
        "file_name": source.name,
        "source_path": str(source),
        "original_source_path": str(source),
        "source_item_id": SOURCE_ID,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "input_image_sha256": "b" * 64,
        "period": "202201",
        "run_id": "run-1",
        "ocr_attempt": 3,
        "auto_verified": True,
        "auto_review_required": False,
        "review_status": "已完成",
        "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
        "evidence_contract_valid": True,
        "three_pass_adjudicated": True,
        "adjudication_rule": "three_pass_first_letter_tail_with_one_validated_model",
        "revalidated_from_evidence_guard_revision": OLD_REVISION,
        "revalidated_without_model_call": True,
    }


class CompletedResultRevalidationTests(unittest.TestCase):
    def _fixture(self, root: Path, period: str = "202201"):
        folder = root / f"commercial-{period}"
        folder.mkdir(parents=True)
        source = folder / "photo.jpg"
        source.write_bytes(b"test-image")
        result = folder / "run-OCR成功.json"
        result.write_text(
            json.dumps([_task(source.name)], ensure_ascii=False),
            encoding="utf-8",
        )
        trace = root / "trace.jsonl"
        trace.write_text("{}\n", encoding="utf-8")
        output = root / "output"
        output.mkdir()
        return result, trace, output, source

    def _patch_safe(self, source: Path):
        rows = [_row(source, attempt) for attempt in (1, 2, 3)]
        return (
            patch.object(target, "_load_trace_groups", return_value={source.name: rows}),
            patch.object(target, "_semantic_candidate", return_value=(_semantic(), "")),
            patch.object(
                target,
                "_source_hash_reason",
                return_value=("", hashlib.sha256(source.read_bytes()).hexdigest(), "b" * 64),
            ),
            patch.object(target, "_final_result", return_value=_corrected(source)),
        )

    def test_dry_run_and_apply_update_annotation_after_enqueue(self):
        with tempfile.TemporaryDirectory() as temp:
            result, trace, output, source = self._fixture(Path(temp))
            patches = self._patch_safe(source)
            with patches[0], patches[1], patches[2], patches[3]:
                dry = target.revalidate_completed_result(
                    result_file=result,
                    trace_path=trace,
                    output_dir=output,
                )
            self.assertEqual(dry["correction_count"], 1)
            self.assertEqual(target._annotation_value(_read(result)[0], "model"), "")

            calls: list[str] = []

            def enqueue(row, **_kwargs):
                calls.append(row["file_name"])
                return output / "pending.json"

            presentation_rows: list[dict] = []
            patches = self._patch_safe(source)
            with patches[0], patches[1], patches[2], patches[3]:
                applied = target.revalidate_completed_result(
                    result_file=result,
                    trace_path=trace,
                    output_dir=output,
                    apply=True,
                    backend_status={
                        "is_running": True,
                        "runtime_health_fuse": None,
                        "review_progress": {"period": "202112"},
                    },
                    enqueue=enqueue,
                    append_presentations=lambda _output, rows: presentation_rows.extend(rows),
                )
            self.assertEqual(calls, [source.name])
            self.assertEqual(len(presentation_rows), 1)
            updated = _read(result)[0]
            self.assertEqual(target._annotation_value(updated, "model"), "F24T350FHC")
            self.assertEqual(target._annotation_value(updated, "price"), "3990")
            self.assertEqual(applied["apply_phase"], "complete")
            self.assertTrue(Path(applied["backup_path"]).is_file())
            self.assertTrue(Path(applied["manifest_path"]).is_file())

    def test_hash_mismatch_is_rejected_without_write(self):
        with tempfile.TemporaryDirectory() as temp:
            result, trace, output, source = self._fixture(Path(temp))
            before = result.read_bytes()
            rows = [_row(source, attempt) for attempt in (1, 2, 3)]
            with (
                patch.object(target, "_load_trace_groups", return_value={source.name: rows}),
                patch.object(target, "_semantic_candidate", return_value=(_semantic(), "")),
                patch.object(
                    target,
                    "_source_hash_reason",
                    return_value=("prepared_input_hash_mismatch", "", ""),
                ),
            ):
                report = target.revalidate_completed_result(
                    result_file=result,
                    trace_path=trace,
                    output_dir=output,
                )
            self.assertEqual(report["correction_count"], 0)
            self.assertEqual(report["rejected"]["prepared_input_hash_mismatch"], 1)
            self.assertEqual(result.read_bytes(), before)

    def test_same_card_ambiguity_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            result, trace, output, source = self._fixture(Path(temp))
            rows = [_row(source, attempt) for attempt in (1, 2, 3)]
            with (
                patch.object(target, "_load_trace_groups", return_value={source.name: rows}),
                patch.object(
                    target,
                    "_semantic_candidate",
                    return_value=(None, "same_card_recovery_not_proven"),
                ),
            ):
                report = target.revalidate_completed_result(
                    result_file=result,
                    trace_path=trace,
                    output_dir=output,
                )
            self.assertEqual(report["correction_count"], 0)
            self.assertEqual(report["rejected"]["same_card_recovery_not_proven"], 1)

    def test_exhausted_same_card_technical_error_is_a_revalidation_candidate(self):
        task = _technical_task("photo.jpg")
        candidates = target._candidate_tasks([task], old_revision=OLD_REVISION)
        self.assertEqual(set(candidates), {"photo.jpg"})

    def test_same_card_adjudication_allows_visible_neighbor_count(self):
        final = {
            "view_type": "單機",
            "unique_main": True,
            "complete_screen_count": 3,
            "label_ownership": "matched",
            "model": "C24F390FHE",
            "price": "4290",
            "three_pass_adjudicated": True,
            "adjudication_rule": "three_pass_same_card_raw_field_consensus",
        }
        with (
            patch.object(target, "_raw_call", side_effect=[{}, {}, {}]),
            patch.object(
                target,
                "_revalidate_calls",
                return_value=(final, {"verified": True}, []),
            ),
            patch.object(
                target,
                "_historical_same_card_raw_recovery",
                return_value={
                    "model": "C24F390FHE",
                    "price": "4290",
                    "mode": "two_pass_exact_same_card_pair",
                },
            ),
            patch.object(target, "evaluate_runtime_health") as health,
        ):
            health.return_value.to_dict.return_value = {
                "healthy": True,
                "allow_processing": True,
                "allow_upload": False,
                "reasons": [],
            }
            semantic, reason = target._semantic_candidate(
                [{}, {}, {}],
                normalizer=object(),
                matcher=object(),
            )
        self.assertEqual(reason, "")
        self.assertEqual(semantic["final"]["model"], "C24F390FHE")

    def test_current_year_is_never_rewritten(self):
        with tempfile.TemporaryDirectory() as temp:
            period = f"{datetime.now().year}01"
            result, trace, output, _source = self._fixture(Path(temp), period=period)
            with self.assertRaisesRegex(RuntimeError, "current-year"):
                target.revalidate_completed_result(
                    result_file=result,
                    trace_path=trace,
                    output_dir=output,
                )

    def test_apply_rejects_internal_output_subdirectory(self):
        with tempfile.TemporaryDirectory() as temp:
            result, trace, output, _source = self._fixture(Path(temp))
            internal = output / "_ocr_audit" / "completed_result_revalidation"
            internal.mkdir(parents=True)
            with self.assertRaisesRegex(RuntimeError, "OCR output root"):
                target.revalidate_completed_result(
                    result_file=result,
                    trace_path=trace,
                    output_dir=internal,
                    apply=True,
                    backend_status={
                        "is_running": True,
                        "runtime_health_fuse": None,
                        "review_progress": {"period": "202112"},
                    },
                )

    def test_enqueue_failure_keeps_result_file_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            result, trace, output, source = self._fixture(Path(temp))
            before = result.read_bytes()
            patches = self._patch_safe(source)

            def fail_enqueue(_row, **_kwargs):
                raise RuntimeError("queue unavailable")

            with patches[0], patches[1], patches[2], patches[3]:
                with self.assertRaisesRegex(RuntimeError, "queue unavailable"):
                    target.revalidate_completed_result(
                        result_file=result,
                        trace_path=trace,
                        output_dir=output,
                        apply=True,
                        backend_status={
                            "is_running": True,
                            "runtime_health_fuse": None,
                            "review_progress": {"period": "202112"},
                        },
                        enqueue=fail_enqueue,
                        append_presentations=lambda _output, _rows: None,
                    )
            self.assertEqual(result.read_bytes(), before)
            manifests = list(
                (output / "_ocr_audit" / "completed_result_revalidation").rglob(
                    "manifest.json"
                )
            )
            self.assertEqual(len(manifests), 1)
            self.assertEqual(_read(manifests[0])["apply_phase"], "enqueue_failed")

    def test_basic_trace_rejects_prior_answer_exposure(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "photo.jpg"
            source.write_bytes(b"image")
            rows = [_row(source, attempt) for attempt in (1, 2, 3)]
            rows[1]["parsed_output"]["prior_answer_exposed"] = True
            reason = target._basic_trace_reason(
                rows,
                name=source.name,
                source=source.resolve(),
                period="202201",
            )
            self.assertEqual(reason, "binding_or_independence")

    def test_exact_fused_current_period_photo_boundary_apply_is_allowed(self):
        with tempfile.TemporaryDirectory() as temp:
            result, trace, output, source = self._fixture(Path(temp))
            patches = self._patch_safe(source)
            status = {
                "is_running": False,
                "current_file": "None",
                "image_dir": str(result.parent),
                "runtime_health_fuse": {
                    "active": True,
                    "reasons": [
                        "historical_same_card_narration_snapshot_loss",
                        "verified_null_fields_despite_repeated_physical_card_evidence",
                    ],
                },
                "review_progress": {"period": "202201", "current_file": None},
                "pipeline_pause": {
                    "schema": "samsung-ocr-pipeline-pause/v1",
                    "reason": "systemic_same_card_model_price_recovery_repair",
                    "current_dir": str(result.parent),
                },
            }
            calls: list[str] = []
            with patches[0], patches[1], patches[2], patches[3]:
                applied = target.revalidate_completed_result(
                    result_file=result,
                    trace_path=trace,
                    output_dir=output,
                    apply=True,
                    backend_status=status,
                    enqueue=lambda row, **_kwargs: (
                        calls.append(row["file_name"]) or output / "pending.json"
                    ),
                    append_presentations=lambda _output, _rows: None,
                )
            self.assertEqual(applied["apply_phase"], "complete")
            self.assertEqual(calls, [source.name])

            wrong = dict(status)
            wrong["runtime_health_fuse"] = {
                "active": True,
                "reasons": ["unrelated_failure"],
            }
            with self.assertRaisesRegex(RuntimeError, "exact fused"):
                target._assert_live_other_period(
                    wrong,
                    "202201",
                    result_dir=result.parent,
                )

            current_order_repair = dict(status)
            current_order_repair["runtime_health_fuse"] = {
                "active": True,
                "reasons": [
                    "live_same_card_field_recovery_preempted_by_current_integrity_guard",
                    "verified_physical_card_fields_blocked_as_technical_error",
                ],
            }
            current_order_repair["pipeline_pause"] = {
                "schema": "samsung-ocr-pipeline-pause/v1",
                "reason": "systemic_same_card_field_recovery_order_repair",
                "current_dir": str(result.parent),
            }
            target._assert_live_other_period(
                current_order_repair,
                "202201",
                result_dir=result.parent,
            )


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


if __name__ == "__main__":
    unittest.main()

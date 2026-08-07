"""Repair completed historical null/null rows from bound three-pass evidence.

The normal mode works on an inactive completed period while live OCR keeps
running.  The only current-period exception is an exact, fused photo-boundary
repair for this same-card snapshot-loss incident: OCR is stopped, the current
file is empty, the Dashboard remains online, and upload is blocked by the
matching runtime fuse.  No model call is made.  A row is eligible only when all
three stored calls bind the same source bytes, are stateless, and the current
adjudicator plus the historical same-card helper agree on one single monitor,
model, and price.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import samsung_ocr_batch_processor as backend
from skills.audit_fields import (
    EVIDENCE_GUARD_REVISION,
    _historical_same_card_raw_recovery,
    refresh_authoritative_price_comparison,
    validate_evidence_contract,
)
from skills.field_extraction import FieldNormalizer
from skills.model_matching import ModelMatcher
from skills.runtime_health_gate import evaluate_runtime_health
from tools.continue_after_period_priority import prepared_input_sha256
from tools.finalize_existing_three_pass_reviews import (
    _append_final_presentation_events,
)
from tools.photo_rename_planner import READY_STATUS, plan_single_image
from tools.revalidate_frozen_guard_results import (
    _raw_call,
    _revalidate_calls,
    _update_task,
)
from tools.stream_drive_upload import enqueue_finalized_result


SCHEMA = "samsung-ocr-completed-result-revalidation/v1"
DEFAULT_BACKEND_URL = "http://127.0.0.1:5002"
OLD_REVISION_DEFAULT = "20260803.92"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
INTERNAL_OUTPUT_DIR_NAMES = {"_ocr_audit", "_drive_upload_stream"}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temp, path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_ocr_output_root(output_dir: Path) -> None:
    """Reject internal subdirectories where the live uploader never looks."""

    internal_parts = {
        part.casefold() for part in output_dir.parts
    } & INTERNAL_OUTPUT_DIR_NAMES
    if internal_parts:
        names = ", ".join(sorted(internal_parts))
        raise RuntimeError(
            "output_dir must be the OCR output root, not an internal "
            f"subdirectory ({names})"
        )


def _status(backend_url: str) -> dict[str, Any]:
    with urllib.request.urlopen(
        backend_url.rstrip("/") + "/api/status",
        timeout=15,
    ) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("backend status is not an object")
    return value


def _period_from_result_file(result_file: Path) -> str:
    matches = re.findall(r"20\d{4}", result_file.parent.name)
    if len(matches) != 1:
        raise RuntimeError("result parent does not identify exactly one YYYYMM period")
    return matches[0]


def _task_name(task: dict[str, Any]) -> str:
    return Path(str((task.get("data") or {}).get("image") or "")).name


def _annotation_value(task: dict[str, Any], field: str) -> str:
    for annotation in task.get("annotations") or []:
        if not isinstance(annotation, dict):
            continue
        for row in annotation.get("result") or []:
            if not isinstance(row, dict) or row.get("from_name") != field:
                continue
            value = row.get("value") or {}
            values = value.get("text") or value.get("choices") or []
            if not values:
                return ""
            text = str(values[0] or "").strip()
            return "" if text.lower() in {"null", "none"} else text
    return ""


def _candidate_tasks(
    tasks: list[dict[str, Any]],
    *,
    old_revision: str,
) -> dict[str, dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            raise RuntimeError("result file contains a non-object task")
        name = _task_name(task)
        if not name or name in seen:
            raise RuntimeError(f"result task filename is missing or duplicated: {name}")
        seen.add(name)
        meta = (task.get("data") or {}).get("ocr_meta") or {}
        retry_reasons = str(meta.get("auto_retry_reasons") or "")
        verified_null_fields = bool(
            meta.get("auto_verified") is True
            and meta.get("auto_review_required") is not True
            and not _annotation_value(task, "model")
            and not _annotation_value(task, "price")
        )
        exhausted_same_card_technical_error = bool(
            meta.get("auto_verified") is not True
            and meta.get("auto_review_required") is True
            and str(meta.get("review_status") or "")
            == "技術錯誤／已停止該張上傳"
            and "three_pass_current_integrity_invalid" in retry_reasons
            and "three_call_hard_limit_reached" in retry_reasons
        )
        if (
            meta.get("evidence_guard_revision") == old_revision
            and int(meta.get("ocr_attempt") or 0) == 3
            and (verified_null_fields or exhausted_same_card_technical_error)
        ):
            candidates[name] = task
    return candidates


def _load_trace_groups(
    trace_path: Path,
    *,
    names: set[str],
    period: str,
    old_revision: str,
) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with trace_path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(row, dict):
                continue
            name = str(row.get("file_name") or "")
            if (
                name in names
                and str(row.get("period") or "") == period
                and str(row.get("evidence_guard_revision") or "") == old_revision
            ):
                groups[name].append(row)
    for rows in groups.values():
        rows.sort(key=lambda item: int(item.get("attempt") or 0))
    return groups


def _basic_trace_reason(
    rows: list[dict[str, Any]],
    *,
    name: str,
    source: Path,
    period: str,
) -> str:
    if len(rows) != 3 or [int(row.get("attempt") or 0) for row in rows] != [1, 2, 3]:
        return "trace_count_or_attempts"
    runs = {str(row.get("run_id") or "") for row in rows}
    source_ids = {str(row.get("source_item_id") or "").lower() for row in rows}
    source_paths = {Path(str(row.get("source_path") or "")).resolve() for row in rows}
    originals = {
        Path(str(row.get("original_source_path") or "")).resolve()
        for row in rows
    }
    if len(runs) != 1 or "" in runs:
        return "run_mismatch"
    if (
        len(source_ids) != 1
        or not re.fullmatch(r"[0-9a-f]{64}", next(iter(source_ids), ""))
    ):
        return "source_id_mismatch"
    if source_paths != {source} or originals != {source}:
        return "source_path_mismatch"
    if any(str(row.get("period") or "") != period for row in rows):
        return "period_mismatch"
    for row in rows:
        parsed = row.get("parsed_output") or {}
        if (
            parsed.get("request_id_verified") is not True
            or parsed.get("independent_pass") is not True
            or parsed.get("prior_answer_exposed") is True
            or parsed.get("prompt_contamination") is True
        ):
            return "binding_or_independence"
    return ""


def _source_hash_reason(source: Path, rows: list[dict[str, Any]]) -> tuple[str, str, str]:
    source_sha256 = _sha256_file(source)
    input_sha256 = prepared_input_sha256(source)
    input_hashes = {
        str((row.get("parsed_output") or {}).get("input_image_sha256") or "").lower()
        for row in rows
    }
    source_hashes = {
        str((row.get("parsed_output") or {}).get("source_file_sha256") or "").lower()
        for row in rows
        if str((row.get("parsed_output") or {}).get("source_file_sha256") or "")
    }
    if input_hashes != {input_sha256}:
        return "prepared_input_hash_mismatch", source_sha256, input_sha256
    if source_hashes and source_hashes != {source_sha256}:
        return "source_file_hash_mismatch", source_sha256, input_sha256
    return "", source_sha256, input_sha256


def _semantic_candidate(
    rows: list[dict[str, Any]],
    *,
    normalizer: FieldNormalizer,
    matcher: ModelMatcher,
) -> tuple[dict[str, Any] | None, str]:
    raw_passes: list[dict[str, Any]] = []
    try:
        for attempt, row in enumerate(rows, start=1):
            record = _raw_call(
                row,
                attempt=attempt,
                normalizer=normalizer,
                matcher=matcher,
            )
            narration = str(record.get("thinking") or record.get("narration") or "")
            record["runtime_health"] = evaluate_runtime_health(
                record,
                narration,
                attempt=1,
                upstream_upload_authorized=False,
            ).to_dict()
            raw_passes.append(record)
        final, decision, _history = _revalidate_calls(
            rows,
            normalizer=normalizer,
            matcher=matcher,
        )
    except RuntimeError:
        return None, "raw_call_replay_failed"
    recovery = _historical_same_card_raw_recovery(raw_passes)
    if decision.get("verified") is not True:
        return None, "current_rules_not_verified"
    same_card_adjudicated = bool(
        final.get("three_pass_adjudicated") is True
        and str(final.get("adjudication_rule") or "")
        == "three_pass_same_card_raw_field_consensus"
    )
    complete_count = int(final.get("complete_screen_count") or 0)
    if (
        str(final.get("view_type") or "") != "單機"
        or final.get("unique_main") is not True
        or complete_count < 1
        or (complete_count != 1 and not same_card_adjudicated)
        or str(final.get("label_ownership") or "") != "matched"
    ):
        return None, "not_one_bound_single_monitor"
    if not recovery:
        return None, "same_card_recovery_not_proven"
    if (
        str(final.get("model") or "").upper()
        != str(recovery.get("model") or "").upper()
        or re.sub(r"[^0-9]", "", str(final.get("price") or ""))
        != re.sub(r"[^0-9]", "", str(recovery.get("price") or ""))
    ):
        return None, "current_result_and_same_card_recovery_disagree"
    return {"final": final, "recovery": recovery}, ""


def _final_result(
    semantic: dict[str, Any],
    *,
    rows: list[dict[str, Any]],
    source: Path,
    source_sha256: str,
    input_sha256: str,
    period: str,
    old_revision: str,
) -> dict[str, Any]:
    result = dict(semantic["final"])
    recovery = dict(semantic["recovery"])
    result.update(
        {
            "view_type": "單機",
            "category": "單機",
            "model": str(recovery["model"]),
            "price": str(recovery["price"]),
            "file_name": source.name,
            "source_path": str(source),
            "original_source_path": str(source),
            "source_item_id": str(rows[0].get("source_item_id") or "").lower(),
            "source_sha256": source_sha256,
            "input_image_sha256": input_sha256,
            "period": period,
            "run_id": str(rows[0].get("run_id") or ""),
            "ocr_attempt": 3,
            "lifetime_model_call_count": 3,
            "independent_pass": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
            "request_binding_enforced": True,
            "request_id_verified": True,
            "runtime_health": {
                "healthy": True,
                "allow_processing": True,
                "allow_upload": True,
                "reasons": [],
                "resolved_by_completed_result_revalidation": True,
            },
            "auto_verified": True,
            "auto_review_required": False,
            "review_status": "已完成",
            "technical_retry_required": False,
            "technical_retry_exhausted": False,
            "three_pass_adjudicated": True,
            "adjudication_rule": str(recovery["mode"]),
            "adjudication_summary": (
                f"既有三輪無記憶證據已交叉核對："
                f"單機／{recovery['model']}／{recovery['price']}"
            ),
            "model_validation_failed": False,
            "rejected_model": "",
            "price_conflict_detected": False,
            "official_model_unverified": False,
            "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
            "evidence_contract_valid": True,
            "revalidated_from_evidence_guard_revision": old_revision,
            "revalidated_without_model_call": True,
        }
    )
    refresh_authoritative_price_comparison(result, result["model"], result["price"])
    valid, errors, normalized = validate_evidence_contract(result)
    if not valid:
        raise RuntimeError(
            f"corrected result contract failed for {source.name}: " + ";".join(errors)
        )
    result["normalized_evidence"] = normalized
    plan = plan_single_image(
        source,
        result,
        period,
        "＄",
        current_year=datetime.now().year,
    )
    if plan.get("status") != READY_STATUS:
        raise RuntimeError(
            f"corrected upload plan is not ready for {source.name}: "
            + str(plan.get("reason") or "unknown")
        )
    return result


def _active_period(status: dict[str, Any]) -> str:
    progress = status.get("review_progress") or {}
    return str(progress.get("period") or status.get("period") or "")


def _assert_live_other_period(
    status: dict[str, Any], period: str, *, result_dir: Path | None = None
) -> None:
    fuse = status.get("runtime_health_fuse")
    fuse_active = bool(
        fuse.get("active") is True if isinstance(fuse, dict) else fuse
    )
    active_period = _active_period(status)
    if (
        status.get("is_running") is True
        and not fuse_active
        and active_period != period
    ):
        return

    current_file = status.get("current_file")
    progress = status.get("review_progress") or {}
    progress_file = progress.get("current_file") if isinstance(progress, dict) else None
    current_file_empty = all(
        value is None or str(value).strip().lower() in {"", "none"}
        for value in (current_file, progress_file)
    )
    pause = status.get("pipeline_pause")
    fuse_reasons = {
        str(reason)
        for reason in ((fuse or {}).get("reasons") or [])
        if str(reason)
    } if isinstance(fuse, dict) else set()
    exact_repair_profiles = {
        (
            frozenset(
                {
                    "historical_same_card_narration_snapshot_loss",
                    "verified_null_fields_despite_repeated_physical_card_evidence",
                }
            ),
            "systemic_same_card_model_price_recovery_repair",
        ),
        (
            frozenset(
                {
                    "live_same_card_field_recovery_preempted_by_current_integrity_guard",
                    "verified_physical_card_fields_blocked_as_technical_error",
                }
            ),
            "systemic_same_card_field_recovery_order_repair",
        ),
    }
    status_dir = Path(
        str(status.get("image_dir") or status.get("current_dir") or "")
    ).resolve()
    exact_paused_current_repair = bool(
        result_dir is not None
        and status.get("is_running") is False
        and current_file_empty
        and active_period == period
        and status_dir == result_dir.resolve()
        and fuse_active
        and isinstance(pause, dict)
        and pause.get("schema") == "samsung-ocr-pipeline-pause/v1"
        and (frozenset(fuse_reasons), str(pause.get("reason") or ""))
        in exact_repair_profiles
        and Path(str(pause.get("current_dir") or "")).resolve()
        == result_dir.resolve()
    )
    if exact_paused_current_repair:
        return
    raise RuntimeError(
        "completed-result apply requires live OCR on another period or the "
        "exact fused same-card photo-boundary repair"
    )


def revalidate_completed_result(
    *,
    result_file: Path,
    trace_path: Path,
    output_dir: Path,
    old_revision: str = OLD_REVISION_DEFAULT,
    apply: bool = False,
    backend_status: dict[str, Any] | None = None,
    enqueue: Callable[..., Path | None] = enqueue_finalized_result,
    append_presentations: Callable[[Path, list[dict[str, Any]]], None]
    | None = None,
) -> dict[str, Any]:
    result_file = result_file.resolve()
    trace_path = trace_path.resolve()
    output_dir = output_dir.resolve()
    if not result_file.is_file() or not trace_path.is_file():
        raise FileNotFoundError(result_file if not result_file.is_file() else trace_path)
    if apply:
        _assert_ocr_output_root(output_dir)
    period = _period_from_result_file(result_file)
    if int(period[:4]) >= datetime.now().year:
        raise RuntimeError("current-year completed results require the live price gate")
    tasks = _read_json(result_file)
    if not isinstance(tasks, list):
        raise RuntimeError("completed result is not a task list")
    candidates = _candidate_tasks(tasks, old_revision=old_revision)
    groups = _load_trace_groups(
        trace_path,
        names=set(candidates),
        period=period,
        old_revision=old_revision,
    )
    normalizer = FieldNormalizer()
    matcher = ModelMatcher(str(backend.MODEL_LIST_PATH))
    corrections: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()

    for name in sorted(candidates):
        source = (result_file.parent / name).resolve()
        if source.suffix.lower() not in IMAGE_EXTENSIONS or not source.is_file():
            rejected["source_missing"] += 1
            continue
        rows = groups.get(name) or []
        reason = _basic_trace_reason(
            rows,
            name=name,
            source=source,
            period=period,
        )
        if reason:
            rejected[reason] += 1
            continue
        semantic, reason = _semantic_candidate(
            rows,
            normalizer=normalizer,
            matcher=matcher,
        )
        if reason or semantic is None:
            rejected[reason or "semantic_recovery_failed"] += 1
            continue
        reason, source_sha256, input_sha256 = _source_hash_reason(source, rows)
        if reason:
            rejected[reason] += 1
            continue
        corrections.append(
            _final_result(
                semantic,
                rows=rows,
                source=source,
                source_sha256=source_sha256,
                input_sha256=input_sha256,
                period=period,
                old_revision=old_revision,
            )
        )

    before_sha256 = _sha256_file(result_file)
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": datetime.now().astimezone().isoformat(),
        "mode": "apply" if apply else "dry_run",
        "result_file": str(result_file),
        "trace_path": str(trace_path),
        "period": period,
        "old_revision": old_revision,
        "current_revision": EVIDENCE_GUARD_REVISION,
        "result_sha256_before": before_sha256,
        "candidate_count": len(candidates),
        "correction_count": len(corrections),
        "rejected": dict(sorted(rejected.items())),
        "corrections": [
            {
                "file_name": row["file_name"],
                "source_item_id": row["source_item_id"],
                "model": row["model"],
                "price": row["price"],
                "adjudication_rule": row["adjudication_rule"],
            }
            for row in corrections
        ],
    }
    if not apply:
        return report
    if backend_status is None:
        raise RuntimeError("apply requires current backend status")
    _assert_live_other_period(
        backend_status,
        period,
        result_dir=result_file.parent,
    )
    if not corrections:
        report["result_sha256_after"] = before_sha256
        return report

    audit_dir = (
        output_dir
        / "_ocr_audit"
        / "completed_result_revalidation"
        / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    )
    audit_dir.mkdir(parents=True, exist_ok=False)
    backup_path = audit_dir / result_file.name
    shutil.copy2(result_file, backup_path)
    report["backup_path"] = str(backup_path)
    manifest_path = audit_dir / "manifest.json"
    _atomic_json(manifest_path, {**report, "apply_phase": "backup_complete"})

    queued: list[str] = []
    try:
        for row in corrections:
            queued_path = enqueue(row, output_dir=output_dir, price_symbol="＄")
            if queued_path is None:
                raise RuntimeError(f"enqueue rejected corrected row: {row['file_name']}")
            queued.append(str(row["source_item_id"]))
    except Exception as exc:
        _atomic_json(
            manifest_path,
            {
                **report,
                "apply_phase": "enqueue_failed",
                "queued_source_item_ids": queued,
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        raise

    by_name = {_task_name(task): task for task in tasks}
    for row in corrections:
        task = by_name[row["file_name"]]
        _update_task(task, row)
        meta = task.setdefault("data", {}).setdefault("ocr_meta", {})
        for field in (
            "screen_status",
            "quality_issue",
            "source_item_id",
            "source_sha256",
            "input_image_sha256",
            "period",
            "run_id",
            "revalidated_from_evidence_guard_revision",
            "revalidated_without_model_call",
        ):
            meta[field] = row.get(field)
    _atomic_json(result_file, tasks)
    after_sha256 = _sha256_file(result_file)
    if after_sha256 == before_sha256:
        raise RuntimeError("completed result file did not change")
    presentation_writer = append_presentations or _append_final_presentation_events
    presentation_writer(output_dir, corrections)
    report.update(
        {
            "apply_phase": "complete",
            "queued_source_item_ids": queued,
            "result_sha256_after": after_sha256,
            "manifest_path": str(manifest_path),
        }
    )
    _atomic_json(manifest_path, report)
    return report


def _summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        key: report.get(key)
        for key in (
            "schema",
            "mode",
            "period",
            "old_revision",
            "current_revision",
            "candidate_count",
            "correction_count",
            "rejected",
            "result_sha256_before",
            "result_sha256_after",
            "backup_path",
            "manifest_path",
            "apply_phase",
        )
        if key in report
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-file", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        metavar="OCR_OUTPUT_ROOT",
        help=(
            "正式 OCR 輸出根目錄，例如 D:\\00_商化\\00_已OCR照片；"
            "不得傳入 _ocr_audit 或 _drive_upload_stream 子目錄"
        ),
    )
    parser.add_argument("--old-revision", default=OLD_REVISION_DEFAULT)
    parser.add_argument("--backend-url", default=DEFAULT_BACKEND_URL)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--full-report", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    status = _status(args.backend_url) if args.apply else None
    report = revalidate_completed_result(
        result_file=args.result_file,
        trace_path=args.trace,
        output_dir=args.output_dir,
        old_revision=args.old_revision,
        apply=args.apply,
        backend_status=status,
    )
    print(
        json.dumps(
            report if args.full_report else _summary(report),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

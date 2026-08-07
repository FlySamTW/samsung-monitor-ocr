"""Re-adjudicate frozen old-guard results without another model call.

This is intentionally narrower than a transport revision migration.  It
rebuilds every pass from the stored raw model JSON, runs the current
normalization/health/accuracy rules, and emits a current-revision upload only
when the exact source identity and prepared-image bytes are still proven.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import samsung_ocr_batch_processor as backend
from skills.audit_fields import (
    EVIDENCE_GUARD_REVISION,
    _narration_reports_additional_complete_monitors,
    _narration_supports_only_one_complete_monitor,
    enrich_result_for_review,
    finalize_three_pass_outcome,
    immediate_retry_decision,
    normalize_model_token,
    refresh_authoritative_price_comparison,
    validate_evidence_contract,
)
from skills.batch_orchestrator import BatchOrchestrator
from skills.field_extraction import FieldNormalizer
from skills.model_matching import ModelMatcher
from skills.model_validation import (
    has_photo_label_model_evidence,
    recover_pipeline_unlisted_model_candidate,
    resolve_photo_label_model_candidate,
    strict_known_model,
    unique_known_first_letter_alternative,
    unique_embedded_known_model,
)
from skills.runtime_health_gate import (
    evaluate_runtime_health,
    final_content_conflict_can_isolate,
    first_pass_content_conflict_can_retry,
)
from tools.continue_after_period_priority import prepared_input_sha256
from tools.photo_rename_planner import READY_STATUS, plan_single_image
from tools.stream_drive_upload import enqueue_finalized_result


SCHEMA = "samsung-ocr-frozen-guard-revalidation/v1"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
META_FIELDS = (
    "view_type",
    "model",
    "price",
    "complete_screen_count",
    "unique_main",
    "label_ownership",
    "followme_physical_evidence",
    "followme_family_confirmed",
    "three_pass_adjudicated",
    "adjudication_rule",
    "adjudication_summary",
    "price_status",
    "price_symbol",
    "official_price",
    "price_diff_percent",
    "evidence_guard_revision",
    "evidence_contract_valid",
    "ocr_attempt",
    "auto_verified",
    "auto_review_required",
    "review_status",
    "auto_retry_reasons",
    "technical_retry_required",
    "technical_retry_exhausted",
    "lifetime_model_call_count",
    "call_budget_overrun_detected",
)


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


def _load_retry_state_for_revalidation(
    staging_dir: Path,
) -> tuple[Path, dict[str, Any]]:
    path = staging_dir / ".ocr_retry_queue.json"
    if path.is_file():
        payload = _read_json(path)
        if not isinstance(payload, dict):
            raise RuntimeError("retry state is not an object")
    else:
        payload = {
            "image_dir": str(staging_dir),
            "priority_queue": [],
            "retry_queue": [],
            "auto_attempts": {},
            "auto_result_history": {},
            "runtime_health_incident_sources": {},
            "request_binding_incident_events": [],
        }
    if _resolved(payload.get("image_dir")) != staging_dir:
        raise RuntimeError("retry state belongs to another staging directory")
    expected_types = {
        "priority_queue": list,
        "retry_queue": list,
        "auto_attempts": dict,
        "auto_result_history": dict,
    }
    for field, expected_type in expected_types.items():
        value = payload.get(field, expected_type())
        if not isinstance(value, expected_type):
            raise RuntimeError(f"retry state field has invalid type: {field}")
        payload[field] = value
    return path, payload


def _build_rejected_retry_state(
    staging_dir: Path,
    candidates: list[dict[str, Any]],
) -> tuple[Path, dict[str, Any], list[dict[str, Any]], dict[str, str]]:
    path, state = _load_retry_state_for_revalidation(staging_dir)
    attempts = dict(state.get("auto_attempts") or {})
    histories = dict(state.get("auto_result_history") or {})
    scheduled: list[dict[str, Any]] = []
    blocked: dict[str, str] = {}

    for candidate in candidates:
        name = str(candidate.get("file_name") or "")
        calls = int(candidate.get("calls") or 0)
        history = candidate.get("retry_history")
        reason = ""
        if not name or calls not in (1, 2):
            reason = "rerun_requires_one_or_two_consumed_calls"
        elif (
            not isinstance(history, list)
            or len(history) > calls
            or not all(isinstance(item, dict) for item in history)
        ):
            reason = "replayable_history_exceeds_consumed_calls"
        else:
            try:
                existing_attempt = int(attempts.get(name) or 0)
            except (TypeError, ValueError):
                reason = "existing_retry_attempt_count_is_invalid"
            else:
                existing_history = histories.get(name)
                if existing_attempt not in (0, calls):
                    reason = "existing_retry_attempt_count_conflicts_with_trace"
                elif existing_history not in (None, [], history):
                    reason = "existing_retry_history_conflicts_with_trace"
        if reason:
            blocked[name] = reason
            continue

        attempts[name] = calls
        histories[name] = list(history)
        scheduled.append(
            {
                "file_name": name,
                "consumed_calls": calls,
                "remaining_calls": 3 - calls,
                "replayable_history_calls": len(history),
                "stateless_prompt": True,
            }
        )

    scheduled_names = [row["file_name"] for row in scheduled]
    if scheduled_names:
        scheduled_set = set(scheduled_names)
        state["image_dir"] = str(staging_dir)
        state["priority_queue"] = [
            item
            for item in state.get("priority_queue") or []
            if str(item) not in scheduled_set
        ]
        state["retry_queue"] = [
            *scheduled_names,
            *[
                item
                for item in state.get("retry_queue") or []
                if str(item) not in scheduled_set
            ],
        ]
        state["auto_attempts"] = attempts
        state["auto_result_history"] = histories
        state["updated_at"] = datetime.now().isoformat()
    return path, state, scheduled, blocked

def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolved(value: Any) -> Path:
    return Path(str(value or "")).resolve()


def _task_file_name(task: dict[str, Any]) -> str:
    return Path(str((task.get("data") or {}).get("image") or "")).name


def _status(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url.rstrip("/") + "/api/status", timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("backend status is not an object")
    return payload


def _prove_inactive_staging(
    staging_dir: Path,
    status: dict[str, Any],
    *,
    allow_paused_current_staging: bool = False,
) -> None:
    if status.get("runtime_health_fuse"):
        raise RuntimeError("runtime health fuse is active")
    current = _resolved(
        status.get("current_relative_dir")
        or status.get("image_dir")
        or status.get("current_dir")
    )
    if current != staging_dir:
        return
    if not allow_paused_current_staging:
        raise RuntimeError("refusing to rewrite the backend's active staging directory")
    current_file = status.get("current_file")
    current_file_empty = current_file is None or str(current_file).strip().lower() in {
        "",
        "none",
    }
    pause = status.get("pipeline_pause")
    exact_pause = (
        status.get("is_running") is False
        and current_file_empty
        and isinstance(pause, dict)
        and pause.get("schema") == "samsung-ocr-pipeline-pause/v1"
        and _resolved(pause.get("current_dir")) == staging_dir
        and str(pause.get("reason") or "").startswith(
            (
                "fail_safe_ordered_followme_",
                "fail_safe_deterministic_wide_scene_overrode_partial_neighbor_narration_",
                "fail_safe_attached_side_label_terminal_",
                "fail_safe_followme_family_lock_",
            )
        )
    )
    if not exact_pause:
        raise RuntimeError(
            "refusing to rewrite current staging without an exact paused "
            "approved photo-boundary fail-safe proof"
        )


def _load_tasks(staging_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    by_name: dict[str, dict[str, Any]] = {}
    files: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(staging_dir.glob("*OCR成功.json")):
        tasks = _read_json(path)
        if not isinstance(tasks, list):
            raise RuntimeError(f"result file is not a task list: {path}")
        files[str(path)] = tasks
        for task in tasks:
            if not isinstance(task, dict):
                raise RuntimeError(f"result file contains a non-object task: {path}")
            name = _task_file_name(task)
            if not name or name in by_name:
                raise RuntimeError(f"result task filename is missing or duplicated: {name}")
            by_name[name] = task
    return by_name, files


def _load_trace_inventory(
    trace_path: Path,
    *,
    old_revision: str,
    names: set[str],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    inventory: list[dict[str, Any]] = []
    with trace_path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                row = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(row, dict):
                continue
            row = dict(row)
            row["_ledger_line_number"] = line_number
            inventory.append(row)
            name = str(row.get("file_name") or "")
            if (
                name in names
                and str(row.get("evidence_guard_revision") or "")
                == old_revision
            ):
                grouped[name].append(row)
    for rows in grouped.values():
        rows.sort(key=lambda item: int(item.get("attempt") or 0))
    return grouped, inventory


def _raw_call(
    trace: dict[str, Any],
    *,
    attempt: int,
    normalizer: FieldNormalizer,
    matcher: ModelMatcher,
) -> dict[str, Any]:
    raw_output = str(trace.get("raw_output") or "")
    parsed, raw_objects, merge_mode, merge_rejected = backend._merge_v1945_json_objects(
        raw_output
    )
    if (
        not isinstance(parsed, dict)
        or merge_rejected
        or merge_mode != "single_object"
        or len(raw_objects) != 1
    ):
        raise RuntimeError("stored raw response is not one unambiguous JSON object")
    request_id = str(parsed.get("request_id") or "")
    if not re.fullmatch(r"[0-9a-f]{32}", request_id):
        raise RuntimeError("stored raw response has no valid request ID")

    record = dict(parsed)
    pre_contract_candidate = resolve_photo_label_model_candidate(
        record.get("model"),
        record,
        record.get("narration") or "",
    )
    if pre_contract_candidate:
        record["model"] = pre_contract_candidate
        record["unlisted_model_candidate"] = True
        record["official_model_unverified"] = True
    # The live pipeline's narrow recovery contract binds the candidate to the
    # one stored raw object.  Frozen replay must carry the same authority
    # before any validator/normalizer can erase an unlisted but photo-proven
    # SKU.
    record["raw_objects"] = raw_objects
    record.pop("request_id", None)
    record["file_name"] = str(trace.get("file_name") or "")
    record["source_path"] = str(trace.get("source_path") or "")
    record["period"] = str(trace.get("period") or "")
    record["ocr_attempt"] = int(trace.get("attempt") or attempt)
    record["request_id_verified"] = True
    record["request_binding_enforced"] = True
    record["input_image_sha256"] = str(
        (trace.get("parsed_output") or {}).get("input_image_sha256") or ""
    ).lower()
    record["thinking"] = str(record.get("narration") or "")
    if backend.price_looks_like_display_spec(
        record.get("price"),
        record.get("thinking"),
    ):
        record["rejected_spec_like_price"] = record.get("price")
        record["price"] = None
        record["price_conflict_detected"] = True
    record["independent_pass"] = True
    record["prior_answer_exposed"] = False
    record["prompt_contamination"] = False
    record = backend.finalize_evidence_contract(record, raw_output)
    record = normalizer.normalize(record)
    recover_pipeline_unlisted_model_candidate(record)

    if record.get("view_type") == "單機":
        raw_model = record.get("model") or ""
        if raw_model and not str(raw_model).upper().startswith("FOLLOWME"):
            photo_label_candidate = resolve_photo_label_model_candidate(
                raw_model,
                record,
                record.get("thinking") or record.get("narration") or "",
            )
            if photo_label_candidate:
                record["model"] = photo_label_candidate
                record["unlisted_model_candidate"] = True
                record["official_model_unverified"] = True
                first_letter_alternative = unique_known_first_letter_alternative(
                    photo_label_candidate,
                    matcher.valid_models,
                )
                if first_letter_alternative:
                    record["catalog_confusable_first_letter_candidate"] = True
                    record["catalog_confusable_first_letter_alternative"] = (
                        first_letter_alternative
                    )
                raw_model = photo_label_candidate
            matched = (
                strict_known_model(raw_model, matcher.valid_models)
                or unique_embedded_known_model(raw_model, matcher.valid_models)
            )
            if matched:
                record["model"] = matched
                # The pre-contract photo-label extractor intentionally runs
                # before the catalog matcher.  Once the exact token is proven
                # catalog-listed, it is no longer an unverified candidate.
                record.pop("unlisted_model_candidate", None)
                record.pop("official_model_unverified", None)
                record.pop("catalog_confusable_first_letter_candidate", None)
                record.pop("catalog_confusable_first_letter_alternative", None)
            elif (
                record.get("unlisted_model_candidate")
                and has_photo_label_model_evidence(
                    raw_model,
                    record,
                    record.get("thinking") or record.get("narration") or "",
                )
            ):
                record["model"] = str(raw_model).strip().upper()
                record["official_model_unverified"] = True
            else:
                record["model"] = None
                record["model_validation_failed"] = True
                record["rejected_model"] = str(raw_model)
    record["model"] = BatchOrchestrator._standardize_followme_model(
        record.get("model")
    )
    record["category"] = record.get("view_type")
    for field in (
        "file_name",
        "source_path",
        "source_item_id",
        "original_source_path",
        "period",
        "audit_folder",
        "run_id",
        "model_id",
        "timestamp",
        "started_at",
    ):
        record[field] = trace.get(field)
    record["ocr_attempt"] = attempt
    record = enrich_result_for_review(record)
    record["ocr_attempt"] = attempt
    record["request_binding_enforced"] = True
    return record


def _trace_call_identity(trace: dict[str, Any]) -> str:
    trace_id = str(trace.get("trace_id") or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", trace_id):
        return f"trace:{trace_id}"
    raw_output = str(trace.get("raw_output") or "")
    request_id = ""
    try:
        parsed, _objects, _mode, _rejected = backend._merge_v1945_json_objects(
            raw_output
        )
        if isinstance(parsed, dict):
            request_id = str(parsed.get("request_id") or "").strip().lower()
    except Exception:
        request_id = ""
    if not re.fullmatch(r"[0-9a-f]{32}", request_id):
        match = re.search(
            r'"request_id"\s*:\s*"([0-9a-f]{32})"',
            raw_output,
            flags=re.IGNORECASE,
        )
        request_id = match.group(1).lower() if match else ""
    if request_id:
        return f"request:{request_id}"
    fallback = "|".join(
        (
            str(trace.get("run_id") or ""),
            str(trace.get("attempt") or ""),
            str((trace.get("parsed_output") or {}).get("input_image_sha256") or ""),
            raw_output,
        )
    )
    return "fallback:" + hashlib.sha256(fallback.encode("utf-8")).hexdigest()


_INTERNAL_RETRY_COUNT_FIELDS = (
    "transport_retry_count",
    "parser_retry_count",
    "internal_retry_count",
    "lm_studio_retry_count",
    "request_retry_count",
)
_INTERNAL_CALL_COUNT_FIELDS = (
    "transport_call_count",
    "model_call_count",
    "lm_studio_call_count",
    "physical_model_call_count",
    "request_attempt_count",
)
_INTERNAL_RETRY_FLAG_FIELDS = (
    "transport_retry_occurred",
    "parser_retry_occurred",
    "internal_retry_occurred",
    "lm_studio_retry_occurred",
)
_INTERNAL_RETRY_REASON_FIELDS = (
    "transport_retry_reason",
    "parser_retry_reason",
    "internal_retry_reason",
    "merge_rejected_reason",
)


def _trace_internal_retry_evidence(
    trace: dict[str, Any],
) -> tuple[int, list[str]]:
    """Return the minimum physical calls and explicit hidden-retry evidence.

    Legacy evidence traces normally store only the final response for one
    business pass.  When a trace explicitly says transport/parser retrying
    occurred, one trace row can no longer be treated as one LM Studio call.
    The exact historical count may be unknowable, so any such evidence blocks
    rerun scheduling; the returned floor remains a conservative audit lower
    bound.
    """

    containers: list[tuple[str, dict[str, Any]]] = [("trace", trace)]
    parsed = trace.get("parsed_output")
    if isinstance(parsed, dict):
        containers.append(("parsed_output", parsed))
    for parent_name, parent in list(containers):
        for nested_name in (
            "transport_meta",
            "parser_meta",
            "model_call_meta",
            "runtime_retry",
        ):
            nested = parent.get(nested_name)
            if isinstance(nested, dict):
                containers.append((f"{parent_name}.{nested_name}", nested))

    physical_call_floor = 1
    evidence: list[str] = []
    for scope, value in containers:
        for field in _INTERNAL_RETRY_COUNT_FIELDS:
            raw = value.get(field)
            if isinstance(raw, bool):
                continue
            try:
                count = int(raw or 0)
            except (TypeError, ValueError):
                if raw not in (None, ""):
                    evidence.append(f"{scope}.{field}:invalid")
                continue
            if count > 0:
                evidence.append(f"{scope}.{field}:{count}")
                physical_call_floor = max(physical_call_floor, 1 + count)
        for field in _INTERNAL_CALL_COUNT_FIELDS:
            raw = value.get(field)
            if isinstance(raw, bool):
                continue
            try:
                count = int(raw or 0)
            except (TypeError, ValueError):
                if raw not in (None, ""):
                    evidence.append(f"{scope}.{field}:invalid")
                continue
            if count > 1:
                evidence.append(f"{scope}.{field}:{count}")
                physical_call_floor = max(physical_call_floor, count)
        for field in _INTERNAL_RETRY_FLAG_FIELDS:
            if value.get(field) is True:
                evidence.append(f"{scope}.{field}:true")
                physical_call_floor = max(physical_call_floor, 2)
        for field in _INTERNAL_RETRY_REASON_FIELDS:
            reason = str(value.get(field) or "").strip()
            if reason:
                evidence.append(f"{scope}.{field}:{reason[:120]}")
                physical_call_floor = max(physical_call_floor, 2)
    return physical_call_floor, list(dict.fromkeys(evidence))

def _global_source_call_ledger(
    inventory: list[dict[str, Any]],
    *,
    binding: dict[str, Any],
    task_attempt: int,
    checkpoint_attempt: int,
    normalizer: FieldNormalizer,
    matcher: ModelMatcher,
) -> dict[str, Any]:
    source_item_id = str(binding.get("source_item_id") or "").strip().lower()
    original = _resolved(binding.get("original_source_path"))
    source_sha256 = str(binding.get("source_sha256") or "").strip().lower()
    input_sha256 = str(binding.get("prepared_input_sha256") or "").strip().lower()
    calls: list[dict[str, Any]] = []
    seen_calls: set[str] = set()
    identity_conflicts: list[str] = []
    internal_retry_evidence: list[str] = []
    trace_physical_call_floor = 0

    for row in inventory:
        if str(row.get("source_item_id") or "").strip().lower() != source_item_id:
            continue
        call_identity = _trace_call_identity(row)
        if call_identity in seen_calls:
            continue
        seen_calls.add(call_identity)
        calls.append(row)
        physical_floor, retry_evidence = _trace_internal_retry_evidence(row)
        trace_physical_call_floor += physical_floor
        internal_retry_evidence.extend(
            f"line {row.get('_ledger_line_number')}: {item}"
            for item in retry_evidence
        )
        parsed = row.get("parsed_output") or {}
        row_source_sha = str(
            row.get("source_sha256") or parsed.get("source_sha256") or ""
        ).strip().lower()
        if _resolved(row.get("original_source_path")) != original:
            identity_conflicts.append("original_source_path_mismatch")
        if row_source_sha and row_source_sha != source_sha256:
            identity_conflicts.append("source_sha256_mismatch")

    run_attempts: dict[str, list[int]] = defaultdict(list)
    attempt_sequence_conflicts: list[str] = []
    for row in calls:
        run_id = str(row.get("run_id") or "").strip()
        try:
            attempt = int(row.get("attempt") or 0)
        except (TypeError, ValueError):
            attempt = 0
        if not run_id or attempt < 1:
            attempt_sequence_conflicts.append("missing_run_or_attempt")
            continue
        run_attempts[run_id].append(attempt)
    trace_consumed_floor = max(len(calls), trace_physical_call_floor)
    for run_id, attempts_in_run in run_attempts.items():
        highest = max(attempts_in_run)
        trace_consumed_floor = max(trace_consumed_floor, highest)
        if (
            sorted(set(attempts_in_run)) != list(range(1, highest + 1))
            or len(attempts_in_run) != len(set(attempts_in_run))
        ):
            attempt_sequence_conflicts.append(
                f"non_contiguous_or_duplicate_attempts:{run_id}"
            )
    effective_calls = max(
        len(calls),
        trace_consumed_floor,
        max(0, int(task_attempt or 0)),
        max(0, int(checkpoint_attempt or 0)),
    )
    retry_history: list[dict[str, Any]] = []
    replayable_records: list[dict[str, Any]] = []
    replayed_call_ids: list[str] = []
    for global_attempt, row in enumerate(calls, start=1):
        parsed = row.get("parsed_output") or {}
        row_source_sha = str(
            row.get("source_sha256") or parsed.get("source_sha256") or ""
        ).strip().lower()
        if (
            _resolved(row.get("original_source_path")) != original
            or (row_source_sha and row_source_sha != source_sha256)
            or str(parsed.get("input_image_sha256") or "").strip().lower()
            != input_sha256
            or parsed.get("request_id_verified") is not True
            or parsed.get("independent_pass") is not True
            or parsed.get("prior_answer_exposed") is True
            or parsed.get("prompt_contamination") is True
        ):
            continue
        try:
            record = _raw_call(
                row,
                attempt=global_attempt,
                normalizer=normalizer,
                matcher=matcher,
            )
        except RuntimeError:
            continue
        narration = str(record.get("thinking") or record.get("narration") or "")
        record["runtime_health"] = evaluate_runtime_health(
            record,
            narration,
            attempt=1,
            upstream_upload_authorized=False,
        ).to_dict()
        replayable_records.append(record)
        retry_history.append(
            BatchOrchestrator._history_snapshot(
                record,
                [str(reason) for reason in row.get("retry_reason") or [] if str(reason)],
            )
        )
        replayed_call_ids.append(_trace_call_identity(row))

    return {
        "source_item_id": source_item_id,
        "source_sha256": source_sha256,
        "global_calls": effective_calls,
        "distinct_trace_calls": len(calls),
        "trace_consumed_floor": trace_consumed_floor,
        "trace_physical_call_floor": trace_physical_call_floor,
        "task_attempt": max(0, int(task_attempt or 0)),
        "checkpoint_attempt": max(0, int(checkpoint_attempt or 0)),
        "remaining_calls": max(0, 3 - effective_calls),
        "retry_history": retry_history,
        "replayable_records": replayable_records,
        "replayable_calls": len(retry_history),
        "unreplayable_calls": len(calls) - len(retry_history),
        "call_ids": [_trace_call_identity(row) for row in calls],
        "replayed_call_ids": replayed_call_ids,
        "revisions": sorted(
            {
                str(row.get("evidence_guard_revision") or "")
                for row in calls
                if str(row.get("evidence_guard_revision") or "")
            }
        ),
        "runs": sorted(
            {
                str(row.get("run_id") or "")
                for row in calls
                if str(row.get("run_id") or "")
            }
        ),
        "identity_conflicts": sorted(set(identity_conflicts)),
        "attempt_sequence_conflicts": sorted(set(attempt_sequence_conflicts)),
        "internal_retry_evidence": list(dict.fromkeys(internal_retry_evidence)),
    }


def _revalidate_calls(
    traces: list[dict[str, Any]],
    *,
    normalizer: FieldNormalizer,
    matcher: ModelMatcher,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    history: list[dict[str, Any]] = []
    retry_history: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    decision: dict[str, Any] = {}

    for attempt, trace in enumerate(traces, start=1):
        current = _raw_call(
            trace,
            attempt=attempt,
            normalizer=normalizer,
            matcher=matcher,
        )
        narration = str(current.get("thinking") or current.get("narration") or "")
        health = evaluate_runtime_health(
            current,
            narration,
            attempt=1,
            upstream_upload_authorized=False,
        )
        current["runtime_health"] = health.to_dict()
        can_retry = (
            not health.allow_processing
            and first_pass_content_conflict_can_retry(
                attempt, health.reasons, current
            )
        )
        can_isolate = (
            not health.allow_processing
            and final_content_conflict_can_isolate(
                attempt, health.reasons, current
            )
        )
        decision = immediate_retry_decision(current, attempt, history, 3)
        if can_retry:
            decision.update(
                retry=True,
                unresolved=False,
                verified=False,
                technical_retry_required=False,
            )
        elif can_isolate:
            decision.update(
                retry=False,
                unresolved=True,
                verified=False,
                technical_retry_required=False,
            )
        decision = finalize_three_pass_outcome(current, history, decision, 3)
        current["category"] = current.get("view_type")
        retry_history.append(
            BatchOrchestrator._history_snapshot(
                current,
                [
                    str(reason)
                    for reason in decision.get("reasons") or []
                    if str(reason)
                ],
            )
        )

        if attempt < len(traces):
            history.append(current)
            continue
        if not health.allow_processing and not can_isolate and not can_retry:
            decision.update(retry=False, unresolved=True, verified=False)
        elif decision.get("verified") is True:
            current["runtime_health"] = {
                "healthy": True,
                "allow_processing": True,
                "allow_upload": True,
                "reasons": [],
                "display_narration": str(
                    current.get("thinking") or current.get("narration") or ""
                ),
                "resolved_by_current_rule_revalidation": True,
            }

    if current is None:
        raise RuntimeError("no stored calls")
    # `category` is an alias, not an independent vote.  A current adjudicator
    # may change view_type after parsing; keep the alias synchronized before
    # validating the final contract.
    current["category"] = current.get("view_type")
    return current, decision, retry_history


def _validate_binding(
    *,
    name: str,
    task: dict[str, Any],
    traces: list[dict[str, Any]],
    source_item: dict[str, Any],
    staging_dir: Path,
    old_revision: str,
) -> dict[str, Any]:
    meta = (task.get("data") or {}).get("ocr_meta") or {}
    if (
        meta.get("evidence_guard_revision") != old_revision
        or meta.get("auto_verified") is not True
        or meta.get("auto_review_required") is True
    ):
        raise RuntimeError("task is not one frozen verified old-revision result")
    expected_attempts = list(range(1, len(traces) + 1))
    if not traces or len(traces) > 3:
        raise RuntimeError("stored call count is outside the 1..3 hard limit")
    if [int(row.get("attempt") or 0) for row in traces] != expected_attempts:
        raise RuntimeError("stored attempts are not contiguous from one")
    if int(meta.get("ocr_attempt") or 0) != len(traces):
        raise RuntimeError("task attempt count disagrees with stored trace")

    staged = (staging_dir / name).resolve()
    original = _resolved(source_item.get("original_source_path"))
    source_id = str(source_item.get("source_item_id") or "").lower()
    period = str(source_item.get("period") or "")
    if (
        staged.suffix.lower() not in IMAGE_EXTENSIONS
        or not staged.is_file()
        or not original.is_file()
        or not re.fullmatch(r"[0-9a-f]{64}", source_id)
        or not re.fullmatch(r"20\d{4}", period)
    ):
        raise RuntimeError("source map identity is incomplete")
    prepared_hash = prepared_input_sha256(staged)
    runs = {str(row.get("run_id") or "") for row in traces}
    input_hashes = {
        str((row.get("parsed_output") or {}).get("input_image_sha256") or "").lower()
        for row in traces
    }
    if len(runs) != 1 or "" in runs or input_hashes != {prepared_hash}:
        raise RuntimeError("stored calls do not bind one run and prepared image")
    for row in traces:
        if (
            row.get("evidence_guard_revision") != old_revision
            or str(row.get("file_name") or "") != name
            or str(row.get("source_item_id") or "").lower() != source_id
            or _resolved(row.get("source_path")) != staged
            or _resolved(row.get("original_source_path")) != original
            or str(row.get("period") or "") != period
            or (row.get("parsed_output") or {}).get("request_id_verified") is not True
            or (row.get("parsed_output") or {}).get("independent_pass") is not True
            or (row.get("parsed_output") or {}).get("prior_answer_exposed") is True
            or (row.get("parsed_output") or {}).get("prompt_contamination") is True
        ):
            raise RuntimeError("stored call identity or independence proof failed")
    return {
        "staged_path": str(staged),
        "original_source_path": str(original),
        "source_item_id": source_id,
        "period": period,
        "prepared_input_sha256": prepared_hash,
        "source_sha256": _sha256_file(original),
        "run_id": next(iter(runs)),
    }


def _update_task(task: dict[str, Any], result: dict[str, Any]) -> None:
    meta = task.setdefault("data", {}).setdefault("ocr_meta", {})
    for field in META_FIELDS:
        meta[field] = result.get(field)
    meta["revalidated_from_evidence_guard_revision"] = str(
        result.get("revalidated_from_evidence_guard_revision") or ""
    )
    meta["revalidated_without_model_call"] = True
    meta["revalidated_at"] = datetime.now().astimezone().isoformat()

    annotation = (task.get("annotations") or [{}])[0]
    values = {
        "category": ("choices", [str(result.get("view_type") or "")]),
        "model": ("text", [str(result.get("model") or "null")]),
        "price": ("text", [str(result.get("price") or "null")]),
    }
    rows = []
    for from_name, (value_key, value) in values.items():
        rows.append(
            {
                "from_name": from_name,
                "to_name": "image",
                "type": "choices" if value_key == "choices" else "textarea",
                "origin": "prediction",
                "value": {value_key: value},
            }
        )
    annotation["result"] = rows
    if not task.get("annotations"):
        task["annotations"] = [annotation]


def revalidate(
    *,
    staging_dir: Path,
    trace_path: Path,
    output_dir: Path,
    old_revision: str,
    apply: bool,
    backend_status: dict[str, Any],
    allow_paused_current_staging: bool = False,
    allow_partial: bool = False,
    drop_rejected_for_rerun: bool = False,
    enqueue: Callable[..., Path | None] = enqueue_finalized_result,
) -> dict[str, Any]:
    staging_dir = staging_dir.resolve()
    trace_path = trace_path.resolve()
    output_dir = output_dir.resolve()
    if drop_rejected_for_rerun and not (apply and allow_partial):
        raise RuntimeError(
            "drop_rejected_for_rerun requires apply and allow_partial"
        )
    _prove_inactive_staging(
        staging_dir,
        backend_status,
        allow_paused_current_staging=allow_paused_current_staging,
    )
    if (output_dir / "_ocr_audit" / "runtime_health_fuse.json").exists():
        raise RuntimeError("runtime health fuse is active")

    source_map = _read_json(staging_dir / ".ocr_source_map.json")
    source_items = dict(source_map.get("items") or {})
    tasks, task_files = _load_tasks(staging_dir)
    names = {
        name
        for name, task in tasks.items()
        if (
            ((task.get("data") or {}).get("ocr_meta") or {}).get(
                "evidence_guard_revision"
            )
            == old_revision
        )
    }
    if not names:
        raise RuntimeError("no frozen tasks match the requested old revision")
    groups, trace_inventory = _load_trace_inventory(
        trace_path,
        old_revision=old_revision,
        names=names,
    )
    retry_checkpoint_state: dict[str, Any] = {}
    if drop_rejected_for_rerun:
        _checkpoint_path, retry_checkpoint_state = (
            _load_retry_state_for_revalidation(staging_dir)
        )
    normalizer = FieldNormalizer()
    matcher = ModelMatcher(str(backend.MODEL_LIST_PATH))
    results: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    retry_candidates: list[dict[str, Any]] = []

    for name in sorted(names):
        task = tasks[name]
        source_item = source_items.get(name)
        traces = groups.get(name) or []
        try:
            if not isinstance(source_item, dict):
                raise RuntimeError(f"source map item is missing: {name}")
            binding = _validate_binding(
                name=name,
                task=task,
                traces=traces,
                source_item=source_item,
                staging_dir=staging_dir,
                old_revision=old_revision,
            )
        except RuntimeError as exc:
            if not allow_partial:
                raise
            rejected.append(
                {
                    "file_name": name,
                    "reason": "binding_preflight_rejected",
                    "calls": len(traces),
                    "reasons": [str(exc)],
                    "rerun_disposition": "not_queued",
                    "rerun_blocked_reason": "binding_preflight_rejected",
                }
            )
            continue
        result, decision, _old_revision_retry_history = _revalidate_calls(
            traces,
            normalizer=normalizer,
            matcher=matcher,
        )
        if decision.get("verified") is not True:
            checkpoint_error = ""
            checkpoint_raw = (
                (retry_checkpoint_state.get("auto_attempts") or {}).get(name)
                if retry_checkpoint_state
                else 0
            )
            try:
                checkpoint_attempt = int(checkpoint_raw or 0)
                if checkpoint_attempt < 0:
                    raise ValueError("negative checkpoint")
            except (TypeError, ValueError):
                checkpoint_attempt = 0
                checkpoint_error = "invalid_retry_checkpoint_attempt"
            task_attempt = int(
                (((task.get("data") or {}).get("ocr_meta") or {}).get(
                    "ocr_attempt"
                ))
                or 0
            )
            ledger = _global_source_call_ledger(
                trace_inventory,
                binding=binding,
                task_attempt=task_attempt,
                checkpoint_attempt=checkpoint_attempt,
                normalizer=normalizer,
                matcher=matcher,
            )
            if checkpoint_error:
                ledger["attempt_sequence_conflicts"] = sorted(
                    set(ledger["attempt_sequence_conflicts"] + [checkpoint_error])
                )
            global_calls = int(ledger["global_calls"])
            rejected_row = {
                "file_name": name,
                "reason": "current_rules_do_not_verify",
                "calls": len(traces),
                "global_calls": global_calls,
                "distinct_trace_calls": int(ledger["distinct_trace_calls"]),
                "trace_consumed_floor": int(ledger["trace_consumed_floor"]),
                "task_attempt": int(ledger["task_attempt"]),
                "checkpoint_attempt": int(ledger["checkpoint_attempt"]),
                "remaining_calls": int(ledger["remaining_calls"]),
                "replayable_calls": int(ledger["replayable_calls"]),
                "global_call_revisions": list(ledger["revisions"]),
                "global_call_runs": list(ledger["runs"]),
                "source_sha256": str(ledger["source_sha256"]),
                "identity_conflicts": list(ledger["identity_conflicts"]),
                "attempt_sequence_conflicts": list(
                    ledger["attempt_sequence_conflicts"]
                ),
                "reasons": [
                    str(item) for item in decision.get("reasons") or []
                ],
            }
            if global_calls >= 3:
                rejected_row["rerun_disposition"] = "not_queued"
                rejected_row["rerun_blocked_reason"] = (
                    "three_call_hard_limit_reached"
                )
                rejected_row["call_budget_overrun_detected"] = global_calls > 3
            elif ledger["identity_conflicts"]:
                rejected_row["rerun_disposition"] = "not_queued"
                rejected_row["rerun_blocked_reason"] = (
                    "global_call_identity_conflict"
                )
            elif ledger["attempt_sequence_conflicts"]:
                rejected_row["rerun_disposition"] = "not_queued"
                rejected_row["rerun_blocked_reason"] = (
                    "global_attempt_sequence_is_not_contiguous"
                )
            elif global_calls in (1, 2):
                rejected_row["rerun_disposition"] = "eligible_preserved_budget"
                retry_candidates.append(
                    {
                        "file_name": name,
                        "calls": global_calls,
                        "retry_history": ledger["retry_history"],
                    }
                )
            else:
                rejected_row["rerun_disposition"] = "not_queued"
                rejected_row["rerun_blocked_reason"] = (
                    "global_call_ledger_is_empty"
                )
            rejected.append(rejected_row)
            continue
        result.update(
            {
                **binding,
                "file_name": name,
                "source_path": binding["staged_path"],
                "auto_verified": True,
                "auto_review_required": False,
                "review_status": "已完成",
                "auto_retry_reasons": "",
                "technical_retry_required": False,
                "technical_retry_exhausted": False,
                "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
                "evidence_contract_valid": True,
                "ocr_attempt": len(traces),
                "independent_pass": True,
                "prior_answer_exposed": False,
                "prompt_contamination": False,
                "request_binding_enforced": True,
                "request_id_verified": True,
                # A current adjudicator has settled the identity fields.  Raw
                # pass-level rejection flags belong to superseded candidates
                # and must not block the newly adjudicated terminal result.
                "model_validation_failed": False,
                "rejected_model": "",
                "price_conflict_detected": False,
                "revalidated_from_evidence_guard_revision": old_revision,
                "revalidated_without_model_call": True,
            }
        )
        refresh_authoritative_price_comparison(
            result,
            result.get("model"),
            result.get("price"),
        )
        if not result.get("model") or not result.get("price"):
            result.update(
                {
                    "price_status": "not_compared",
                    "price_symbol": "",
                    "official_price": "",
                    "price_diff_percent": None,
                }
            )
        contract_valid, contract_errors, normalized = validate_evidence_contract(
            result
        )
        if not contract_valid:
            raise RuntimeError(
                f"current result contract failed for {name}: "
                + ";".join(contract_errors)
            )
        result["normalized_evidence"] = normalized
        plan = plan_single_image(
            Path(binding["original_source_path"]),
            result,
            binding["period"],
            "＄",
            current_year=datetime.now().year,
        )
        if plan.get("status") != READY_STATUS:
            rejected.append(
                {
                    "file_name": name,
                    "reason": "current_upload_plan_not_ready",
                    "calls": len(traces),
                    "reasons": [str(plan.get("reason") or "unknown")],
                    "rerun_disposition": "not_queued",
                    "rerun_blocked_reason": "current_upload_plan_not_ready",
                }
            )
            continue
        results.append(result)

    retry_state_path: Path | None = None
    retry_state_payload: dict[str, Any] | None = None
    scheduled_reruns: list[dict[str, Any]] = []
    retry_state_blocked: dict[str, str] = {}
    if drop_rejected_for_rerun:
        (
            retry_state_path,
            retry_state_payload,
            scheduled_reruns,
            retry_state_blocked,
        ) = _build_rejected_retry_state(staging_dir, retry_candidates)
        scheduled_names = {
            row["file_name"] for row in scheduled_reruns
        }
        for row in rejected:
            name = str(row.get("file_name") or "")
            if name in scheduled_names:
                row["rerun_disposition"] = "queued_with_preserved_budget"
            elif name in retry_state_blocked:
                row["rerun_disposition"] = "not_queued"
                row["rerun_blocked_reason"] = retry_state_blocked[name]
    report = {
        "schema": SCHEMA,
        "generated_at": datetime.now().astimezone().isoformat(),
        "mode": "apply" if apply else "dry_run",
        "staging_dir": str(staging_dir),
        "trace_path": str(trace_path),
        "old_revision": old_revision,
        "current_revision": EVIDENCE_GUARD_REVISION,
        "allow_partial": allow_partial,
        "drop_rejected_for_rerun": drop_rejected_for_rerun,
        "result_count": len(results),
        "rejected_count": len(rejected),
        "rejected": rejected,
        "queued_for_rerun_count": len(scheduled_reruns),
        "queued_for_rerun": scheduled_reruns,
        "retry_state_path": (
            str(retry_state_path) if scheduled_reruns and retry_state_path else ""
        ),
        "dropped_for_rerun": len(scheduled_reruns),
        "results": [
            {
                "file_name": row["file_name"],
                "source_item_id": row["source_item_id"],
                "source_sha256": row["source_sha256"],
                "input_image_sha256": row["prepared_input_sha256"],
                "calls": row["ocr_attempt"],
                "view_type": row.get("view_type"),
                "model": row.get("model"),
                "price": row.get("price"),
                "adjudication_rule": row.get("adjudication_rule"),
                "revalidated_without_model_call": True,
            }
            for row in results
        ],
    }
    if not results:
        raise RuntimeError("no frozen result can be safely revalidated")
    if not apply:
        return report

    for row in results:
        queued = enqueue(row, output_dir=output_dir)
        if queued is None:
            raise RuntimeError(f"current result was not queued: {row['file_name']}")
    for row in results:
        _update_task(tasks[row["file_name"]], row)

    # Queue and consumed-call history must reach disk before the old finalized
    # task is removed.  A crash between these writes can leave a duplicate old
    # row, but can never reset the model-call budget or lose the rerun.
    if scheduled_reruns:
        if retry_state_path is None or retry_state_payload is None:
            raise RuntimeError("retry-state plan is missing for scheduled reruns")
        _atomic_json(retry_state_path, retry_state_payload)
    rerun_names = {row["file_name"] for row in scheduled_reruns}
    for path_text, payload in task_files.items():
        if rerun_names:
            payload[:] = [
                task
                for task in payload
                if _task_file_name(task) not in rerun_names
            ]
        _atomic_json(Path(path_text), payload)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    manifest = (
        output_dir
        / "_ocr_audit"
        / "frozen_guard_revalidation"
        / stamp
        / "manifest.json"
    )
    _atomic_json(manifest, report)
    report["manifest"] = str(manifest)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--old-revision", required=True)
    parser.add_argument("--backend-url", default="http://127.0.0.1:5002")
    parser.add_argument(
        "--allow-paused-current-staging",
        action="store_true",
        help=(
            "Allow the backend's current staging only when it is stopped at "
            "the exact ordered-FollowMe fail-safe photo boundary."
        ),
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help=(
            "Preflight every frozen task independently, apply only the exact "
            "safe subset, and leave binding/current-rule rejects unchanged."
        ),
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--drop-rejected-for-rerun",
        action="store_true",
        help=(
            "With --apply --allow-partial, remove old-revision rejects from "
            "the task file so the normal bounded pipeline reprocesses them."
        ),
    )
    args = parser.parse_args()
    if args.drop_rejected_for_rerun and not (args.apply and args.allow_partial):
        parser.error("--drop-rejected-for-rerun requires --apply --allow-partial")
    report = revalidate(
        staging_dir=args.staging_dir,
        trace_path=args.trace,
        output_dir=args.output_dir,
        old_revision=args.old_revision,
        apply=args.apply,
        backend_status=_status(args.backend_url),
        allow_paused_current_staging=args.allow_paused_current_staging,
        allow_partial=args.allow_partial,
        drop_rejected_for_rerun=args.drop_rejected_for_rerun,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

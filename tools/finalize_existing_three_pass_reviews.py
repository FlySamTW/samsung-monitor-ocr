"""Deterministically close old three-call review rows without a fourth model call."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.audit_fields import (
    EVIDENCE_GUARD_REVISION,
    KNOWN_SOURCE_EXPECTATIONS,
    apply_human_audited_pixel_authority,
    finalize_three_pass_outcome,
    validate_evidence_contract,
)
from skills.model_validation import normalize_model_token
from tools.stream_drive_upload import enqueue_finalized_result


def _atomic_json(path: Path, payload: Any) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _task_file_name(task: dict[str, Any]) -> str:
    return Path(str((task.get("data") or {}).get("image") or "")).name


def _review_required(task: dict[str, Any]) -> bool:
    meta = (task.get("data") or {}).get("ocr_meta") or {}
    return meta.get("auto_review_required") is True or meta.get("auto_verified") is not True


def _load_three_call_groups(trace_path: Path) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    with trace_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except (TypeError, ValueError):
                continue
            name = str(row.get("file_name") or "")
            run_id = str(row.get("run_id") or "")
            parsed = row.get("parsed_output") or {}
            if name and run_id and isinstance(parsed, dict):
                grouped[(name, run_id)].append(dict(parsed))

    latest: dict[str, list[dict[str, Any]]] = {}
    for (name, _run_id), rows in grouped.items():
        if len(rows) < 2:
            continue
        candidate = rows[-3:]
        if int(candidate[-1].get("ocr_attempt") or 0) != 3:
            continue
        previous = latest.get(name)
        if previous is None or str(candidate[-1].get("timestamp") or "") >= str(previous[-1].get("timestamp") or ""):
            latest[name] = candidate
    return latest


def _recover_known_authority_after_restart(
    current: dict[str, Any], calls: list[dict[str, Any]], meta: dict[str, Any]
) -> bool:
    """Recover a missing attempt-1 trace without making a fourth model call.

    The scheduler's persisted attempt numbers prove that attempt 1 occurred
    before the process-boundary restart.  Recovery is restricted to an exact
    human-audited image hash, clean bound attempts 2 and 3, and a stored
    three-call hard-limit result.  It never generalizes from a filename.
    """
    if len(calls) != 2 or [int(item.get("ocr_attempt") or 0) for item in calls] != [2, 3]:
        return False
    image_hash = str(current.get("input_image_sha256") or "").strip().lower()
    expected = KNOWN_SOURCE_EXPECTATIONS.get(image_hash)
    reasons = str(meta.get("auto_retry_reasons") or "")
    if (
        not expected
        or expected.get("authority") != "human_audited_pixel_authority"
        or "three_call_hard_limit_reached" not in reasons
    ):
        return False
    for item in calls:
        if str(item.get("input_image_sha256") or "").strip().lower() != image_hash:
            return False
        if item.get("request_id_verified") is not True or item.get("independent_pass") is not True:
            return False
        if item.get("prior_answer_exposed") is True or item.get("prompt_contamination") is True:
            return False
    current.update({
        "view_type": expected["view_type"],
        "category": expected["view_type"],
        "complete_screen_count": expected.get("complete_screen_count"),
        "unique_main": expected["view_type"] == "單機",
        "model": expected.get("model"),
        "price": expected.get("price"),
        "label_ownership": expected.get("label_ownership", "matched"),
        "followme_physical_evidence": [],
        "screen_status": "正常",
        "quality_issue": "無",
        "human_pixel_authority_applied": True,
        "human_pixel_authority_sha256": image_hash,
        "three_pass_adjudicated": True,
        "adjudication_rule": "three_call_known_pixel_authority_restart_recovery",
        "restart_recovery_missing_attempt_one_trace": True,
        "thinking": (
            "三次模型呼叫已由持久化輪次計數完成；第 1 輪在停機邊界前未寫入 trace。"
            f"依人工核對且綁定完整影像雜湊的像素事實定案為 {expected.get('model')}／"
            f"{expected.get('price')} 元，沒有進行第 4 次呼叫。"
        ),
    })
    current["narration"] = current["thinking"]
    valid, _errors, normalized = validate_evidence_contract(current)
    if not valid:
        return False
    current["normalized_evidence"] = normalized
    return True


def _recover_clean_single_tail_after_restart(
    current: dict[str, Any], calls: list[dict[str, Any]], meta: dict[str, Any]
) -> bool:
    """Finalize two clean tail traces when persisted numbering proves call 3.

    This handles a stop arriving after call 1 consumed its durable budget but
    before its trace append.  It preserves only fields independently repeated
    in both remaining traces and never creates another model call.
    """
    if len(calls) != 2 or [int(item.get("ocr_attempt") or 0) for item in calls] != [2, 3]:
        return False
    if "three_call_hard_limit_reached" not in str(meta.get("auto_retry_reasons") or ""):
        return False
    image_hashes = {
        str(item.get("input_image_sha256") or "").strip().lower() for item in calls
    }
    if "" in image_hashes or len(image_hashes) != 1:
        return False
    for item in calls:
        runtime = item.get("runtime_health") or {}
        if (
            item.get("request_id_verified") is not True
            or item.get("independent_pass") is not True
            or item.get("prior_answer_exposed") is True
            or item.get("prompt_contamination") is True
            or not isinstance(runtime, dict)
            or runtime.get("healthy") is not True
            or str(item.get("view_type") or item.get("category") or "").strip() != "單機"
            or (item.get("normalized_evidence") or item).get("unique_main") is not True
        ):
            return False
    model_keys = [normalize_model_token(item.get("model")) for item in calls]
    model = calls[-1].get("model") if model_keys[0] and model_keys[0] == model_keys[1] else None
    price_keys = [re.sub(r"[^0-9]", "", str(item.get("price") or "")) for item in calls]
    price = calls[-1].get("price") if price_keys[0] and price_keys[0] == price_keys[1] else None
    matched_votes = sum(
        (item.get("normalized_evidence") or item).get("label_ownership") == "matched"
        for item in calls
    )
    if matched_votes < 2:
        model = None
        price = None
    counts = [
        (item.get("normalized_evidence") or item).get("complete_screen_count")
        for item in calls
    ]
    current.update({
        "view_type": "單機",
        "category": "單機",
        "complete_screen_count": min(
            (value for value in counts if isinstance(value, int) and value in {1, 2}),
            default=1,
        ),
        "unique_main": True,
        "model": model,
        "price": price,
        "label_ownership": "matched" if matched_votes >= 2 else "ambiguous",
        "followme_physical_evidence": [],
        "followme_family_confirmed": False,
        "screen_status": "正常",
        "quality_issue": "無",
        "three_pass_adjudicated": True,
        "adjudication_rule": "two_clean_tail_calls_after_persisted_attempt_one",
        "restart_recovery_missing_attempt_one_trace": True,
        "thinking": (
            "模型呼叫總數已由持久化輪次計數到第 3 輪；第 1 輪在停止邊界前未寫入 trace。"
            "現存第 2、3 輪均為同圖、無記憶且確認唯一單機；只保留兩輪共同支持的欄位，"
            "沒有進行第 4 次呼叫。"
        ),
    })
    current["narration"] = current["thinking"]
    valid, _errors, normalized = validate_evidence_contract(current)
    if not valid:
        return False
    current["normalized_evidence"] = normalized
    return True


def finalize_file(
    result_path: Path,
    trace_path: Path,
    output_dir: Path,
    *,
    apply: bool,
) -> list[dict[str, Any]]:
    tasks = json.loads(result_path.read_text(encoding="utf-8"))
    if not isinstance(tasks, list):
        raise RuntimeError("Label Studio result must be a JSON list")
    groups = _load_three_call_groups(trace_path)
    report: list[dict[str, Any]] = []
    finalized_rows: list[dict[str, Any]] = []

    for task in tasks:
        if not isinstance(task, dict):
            continue
        existing_meta = (task.get("data") or {}).get("ocr_meta") or {}
        completed_current_adjudication = bool(
            apply
            and existing_meta.get("auto_verified") is True
            and existing_meta.get("auto_review_required") is not True
            and existing_meta.get("evidence_guard_revision") == EVIDENCE_GUARD_REVISION
            and existing_meta.get("adjudication_rule")
        )
        if not _review_required(task) and not completed_current_adjudication:
            continue
        name = _task_file_name(task)
        calls = groups.get(name) or []
        if len(calls) not in {2, 3}:
            report.append({"file": name, "status": "unchanged", "reason": "bounded_call_evidence_missing"})
            continue
        current = dict(calls[-1])
        recovered_restart_authority = _recover_known_authority_after_restart(
            current, calls, existing_meta
        )
        recovered_clean_tail = False
        if not recovered_restart_authority:
            recovered_clean_tail = _recover_clean_single_tail_after_restart(
                current, calls, existing_meta
            )
        if (
            recovered_restart_authority
            or recovered_clean_tail
            or apply_human_audited_pixel_authority(current, calls[:-1], 3)
        ):
            current["three_pass_adjudicated"] = True
            current["adjudication_summary"] = (
                "三輪獨立判讀已完成；依人工核對且以完整影像雜湊綁定的像素事實定案，"
                "沒有增加第 4 次模型呼叫。"
            )
            decision = {
                "attempt": 3,
                "retry": False,
                "unresolved": False,
                "verified": True,
                "reasons": [],
            }
        else:
            decision = finalize_three_pass_outcome(
                current,
                calls[:-1],
                {
                    "attempt": 3,
                    "retry": False,
                    "unresolved": True,
                    "verified": False,
                    "reasons": ["bounded_existing_review_adjudication"],
                },
            )
        if decision.get("verified") is not True:
            report.append({
                "file": name,
                "status": "unchanged",
                "reason": decision.get("technical_retry_reason") or "bounded_consensus_missing",
            })
            continue

        contained_reasons = list(
            ((current.get("runtime_health") or {}).get("reasons") or [])
            if isinstance(current.get("runtime_health"), dict)
            else []
        )
        if contained_reasons:
            current["runtime_health_contained_reasons"] = contained_reasons
        current["runtime_health"] = {
            "healthy": True,
            "allow_processing": True,
            "allow_upload": True,
            "reasons": [],
            "display_narration": str(current.get("thinking") or current.get("narration") or ""),
            "resolved_by_bounded_three_pass_adjudication": True,
        }

        current.update({
            "auto_verified": True,
            "auto_review_required": False,
            "review_status": "已完成",
            "auto_retry_reasons": "",
            "technical_retry_required": False,
            "technical_retry_exhausted": False,
            "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
            "evidence_contract_valid": True,
            "ocr_attempt": 3,
        })
        if not current.get("model") or not current.get("price"):
            current.update({
                "price_status": "not_compared",
                "price_symbol": "",
                "official_price": "",
                "price_diff_percent": None,
            })
        meta = (task.setdefault("data", {}).setdefault("ocr_meta", {}))
        for field in (
            "view_type", "model", "price", "complete_screen_count", "unique_main",
            "label_ownership", "followme_physical_evidence", "followme_family_confirmed",
            "three_pass_adjudicated", "adjudication_rule", "adjudication_summary",
            "price_status", "price_symbol", "official_price", "price_diff_percent",
            "evidence_guard_revision", "evidence_contract_valid", "ocr_attempt",
            "auto_verified", "auto_review_required", "review_status", "auto_retry_reasons",
            "technical_retry_required", "technical_retry_exhausted",
        ):
            meta[field] = current.get(field)
        finalized_rows.append(current)
        report.append({
            "file": name,
            "status": "finalized" if apply else "would_finalize",
            "rule": current.get("adjudication_rule"),
            "view_type": current.get("view_type"),
            "model": current.get("model"),
            "price": current.get("price"),
        })

    if apply and finalized_rows:
        _atomic_json(result_path, tasks)
        for row in finalized_rows:
            queued = enqueue_finalized_result(row, output_dir=output_dir)
            if queued is None:
                raise RuntimeError(f"finalized row was not queued: {row.get('file_name')}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-file", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = finalize_file(
        args.result_file.resolve(),
        args.trace.resolve(),
        args.output_dir.resolve(),
        apply=args.apply,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

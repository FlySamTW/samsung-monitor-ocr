#!/usr/bin/env python3
"""Rerun a candidate subset in a staging folder, then merge results back.

The normal rerun API can queue priority files, but the backend still works at
folder scope. This helper copies only candidate images into a temporary staging
folder, lets the existing backend process that small folder, then merges the new
records back into the original audit folder and rebuilds flat output filenames.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from skills.model_catalog_rules import (  # noqa: E402
    FOLLOWME_UNRESOLVED,
    normalize_followme_family,
    resolve_followme_model,
)

from photo_rename_planner import (  # noqa: E402
    CONFLICT_STATUS,
    MISSING_RESULT_STATUS,
    MISSING_SOURCE_STATUS,
    NO_CHANGE_STATUS,
    READY_STATUS,
    copy_image_for_flat_output,
    make_plan,
    summarize,
    unique_target_path,
    write_csv,
)
from rerun_questionable_records import (  # noqa: E402
    FULLWIDTH_DOLLAR,
    DEFAULT_BACKEND_URL,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_SOURCE_ROOT,
    SUCCESS_HEADERS,
    infer_period_from_text,
    json_request,
    read_dict_csv,
    records_to_map,
    resolve_source_path,
    wait_for_folder_done,
    write_dict_csv,
)
from recover_preinference_system_errors import (  # noqa: E402
    apply_plan as apply_preinference_system_error_recovery,
    build_plan as build_preinference_system_error_recovery,
)
from resolve_capped_adjudication_queue import (  # noqa: E402
    resolve_queue as resolve_capped_queue,
)

SINGLE_VIEW_TEXT = "\u55ae\u6a5f"
NO_MODEL_TEXT = "\u7121\u578b\u865f"
UNKNOWN_MODEL_TEXT = "\u578b\u865f\u672a\u8fa8\u8b58"
NO_PRICE_TEXT = "\u7121\u50f9\u683c"
CONTEXT_ERROR_TEXTS = (
    "number of tokens to keep",
    "context length",
    "n_keep",
    "n_ctx",
    "larger context length",
)
FOLLOWME_RISK_TEXTS = (
    "followme",
    "follow me",
    "white vertical stand",
    "vertical stand",
    "\u767d\u8272\u5782\u76f4\u652f\u67b6",
    "\u5782\u76f4\u652f\u67b6",
    "\u767d\u8272\u76f4\u7acb\u652f\u67b6",
    "\u76f4\u7acb\u652f\u67b6",
    "\u5713\u5f62\u5e95\u5ea7",
    "\u767d\u8272\u5713\u5f62\u5e95\u5ea7",
    "\u767d\u8272\u5e95\u5ea7",
    "\u79fb\u52d5\u5f0f\u667a\u6167",
    "\u79fb\u52d5\u5f0f",
    "\u6258\u76e4",
)
FOLLOWME_DISPLAY_FIXTURE_TERMS = (
    "\u7acb\u5f0f\u87a2\u5e55",
    "\u5c55\u793a\u87a2\u5e55",
    "\u986f\u793a\u87a2\u5e55",
    "\u76f4\u7acb\u87a2\u5e55",
    "\u7368\u7acb\u87a2\u5e55",
    "\u5c55\u793a\u7528",
    "\u7acb\u5f0f\u5c55\u793a",
    "\u76f4\u7acb\u5c55\u793a",
    "\u79fb\u52d5\u5f0f",
    "\u652f\u67b6",
    "\u5e95\u5ea7",
    "\u6258\u76e4",
)
FOLLOWME_DISPLAY_LABEL_TERMS = (
    "\u6a19\u7c64",
    "\u6a19\u724c",
    "\u724c\u9762",
    "\u7522\u54c1\u6a19\u793a",
    "\u4e0a\u65b9",
    "\u5074\u6a19",
    "\u65c1\u908a",
    "\u5beb\u8457",
    "\u986f\u793a",
)
SINGLE_UNIT_RISK_TEXTS = (
    "\u5224\u65b7\u662f\u55ae\u6a5f",
    "\u9019\u5f35\u5df2\u5b8c\u6210\u8fa8\u8b58\uff1a\u55ae\u6a5f",
    "\u55ae\u4e00\u4e3b\u89d2",
    "\u4e3b\u89d2\u5546\u54c1",
    "\u4e3b\u89d2\u81ea\u5df1\u7684",
    "\u4e3b\u89d2\u87a2\u5e55",
    "\u4e3b\u9ad4\u662f",
    "\u4e3b\u89d2\u662f",
    "\u524d\u666f",
    "\u4e2d\u592e\u4e00\u53f0",
    "\u4e2d\u9593\u4e00\u53f0",
    "\u4e2d\u9593\u7684\u87a2\u5e55",
    "\u4e3b\u8981\u87a2\u5e55",
    "\u5074\u6a19",
    "\u5be6\u9ad4\u6a19\u7c64",
    "\u5be6\u9ad4\u50f9\u724c",
    "\u578b\u865f\u6a19\u7c64",
    "\u4e0d\u662f\u9060\u666f",
    "\u4e0d\u5c6c\u65bc\u9060\u666f",
    "\u4e0d\u7b26\u5408\u9060\u666f",
    "\u4e00\u822c\u55ae\u6a5f",
    "\u55ae\u6a5f\u689d\u4ef6",
)
DISTANT_VIEW_TEXT = "\u9060\u666f"
NEGATION_TEXTS = (
    "\u6c92\u6709",
    "\u6c92\u770b\u5230",
    "\u672a\u770b\u5230",
    "\u770b\u4e0d\u5230",
    "\u4e0d\u662f",
    "\u4e26\u975e",
    "\u7121",
    "no ",
    "not ",
    "without",
)


def get_log_total(base_url: str) -> int:
    try:
        data = json_request(base_url, "/api/logs?last=0&lines=1", timeout=10)
        return int(data.get("total") or 0)
    except Exception:
        return 0


def get_logs_since(base_url: str, start: int, max_lines: int = 800) -> list[str]:
    logs: list[str] = []
    cursor = max(0, start)
    while len(logs) < max_lines:
        remaining = max_lines - len(logs)
        try:
            data = json_request(base_url, f"/api/logs?last={cursor}&lines={min(200, remaining)}", timeout=20)
        except Exception:
            break
        batch = data.get("logs") or []
        logs.extend(str(item) for item in batch)
        next_id = data.get("next_id")
        if next_id is None:
            break
        try:
            next_cursor = int(next_id)
        except (TypeError, ValueError):
            break
        if next_cursor <= cursor:
            break
        cursor = next_cursor
    return logs


def has_context_error(logs: list[str]) -> bool:
    text = "\n".join(logs).lower()
    return any(token in text for token in CONTEXT_ERROR_TEXTS)


def text_value(value: object) -> str:
    return str(value or "").strip()


def missing_model(value: object) -> bool:
    text = text_value(value)
    lower = text.lower()
    return lower in {"", "null", "none", "nan", "unknown", "?", "\uff1f"} or NO_MODEL_TEXT in text or UNKNOWN_MODEL_TEXT in text


def missing_price(value: object) -> bool:
    text = text_value(value)
    return not any(ch.isdigit() for ch in text) or NO_PRICE_TEXT in text


def single_missing_ratio(records: list[dict[str, object]], candidate_names: set[str]) -> tuple[float, int, int]:
    matched = [
        record
        for record in records
        if str(record.get("file_name") or record.get("filename") or "") in candidate_names
    ]
    if not matched:
        return 1.0, 0, 0
    bad = 0
    for record in matched:
        view_text = " ".join(
            text_value(record.get(key))
            for key in ("view_type", "category", "human_category")
        )
        model = record.get("human_model") or record.get("model")
        price = record.get("human_price") or record.get("price")
        if SINGLE_VIEW_TEXT in view_text and missing_model(model) and missing_price(price):
            bad += 1
    return bad / len(matched), bad, len(matched)


def has_explicit_distant_conclusion(value: object) -> bool:
    text = text_value(value)
    if not text:
        return False
    if re.search(r'["\']view_type["\']\s*:\s*["\']遠景["\']', text, flags=re.IGNORECASE):
        return True
    conclusions = ("整體符合「遠景」條件", "符合「遠景」條件", "符合遠景條件")
    for conclusion in conclusions:
        start = 0
        while True:
            index = text.find(conclusion, start)
            if index < 0:
                break
            before = text[max(0, index - 12):index]
            if not any(token in before for token in ("不", "並非", "不是", "未", "無法判定")):
                return True
            start = index + len(conclusion)
    return False


def is_explicitly_contained_for_review(record: dict[str, object]) -> bool:
    """Return whether a contradictory result is already isolated from auto acceptance."""
    truthy = {"1", "true", "yes", "y"}
    if str(record.get("auto_review_required") or "").strip().lower() in truthy:
        return True
    if str(record.get("evidence_unresolved") or "").strip().lower() in truthy:
        return True
    review_status = text_value(record.get("review_status")).strip().lower()
    return review_status in {
        "review_required",
        "unresolved",
        "需慢模型或人工校正",
        "需人工校正",
        "待人工校正",
    }


def structured_narration_conflicts(
    records: list[dict[str, object]], candidate_names: set[str]
) -> list[str]:
    """Return uncontained single-unit rows whose own evidence concludes distant view."""
    conflicts: list[str] = []
    for record in records:
        name = str(record.get("file_name") or record.get("filename") or "")
        if name not in candidate_names:
            continue
        # A contradiction that the evidence gate already marked for review is a
        # contained outcome, not permission to stop all later candidates.  It
        # must remain excluded from automatic acceptance/export.
        if is_explicitly_contained_for_review(record):
            continue
        view_text = " ".join(text_value(record.get(key)) for key in ("view_type", "category", "human_category"))
        if SINGLE_VIEW_TEXT not in view_text or DISTANT_VIEW_TEXT in view_text:
            continue
        evidence = " ".join(
            text_value(record.get(key))
            for key in ("thinking", "stream_buffer", "raw", "raw_model_output", "notes")
        )
        if has_explicit_distant_conclusion(evidence):
            conflicts.append(name)
    return conflicts


def contained_structured_narration_conflicts(
    records: list[dict[str, object]], candidate_names: set[str]
) -> list[str]:
    """Return contradictory rows that were safely isolated for review."""
    contained: list[str] = []
    for record in records:
        name = str(record.get("file_name") or record.get("filename") or "")
        if name not in candidate_names or not is_explicitly_contained_for_review(record):
            continue
        view_text = " ".join(text_value(record.get(key)) for key in ("view_type", "category", "human_category"))
        if SINGLE_VIEW_TEXT not in view_text or DISTANT_VIEW_TEXT in view_text:
            continue
        evidence = " ".join(
            text_value(record.get(key))
            for key in ("thinking", "stream_buffer", "raw", "raw_model_output", "notes")
        )
        if has_explicit_distant_conclusion(evidence):
            contained.append(name)
    return contained


def has_positive_followme_indicator(text: str) -> bool:
    if has_followme_display_fixture_indicator(text):
        return True
    lower = text.lower()
    for token in FOLLOWME_RISK_TEXTS:
        start = 0
        token_lower = token.lower()
        while True:
            index = lower.find(token_lower, start)
            if index < 0:
                break
            before = lower[max(0, index - 28) : index]
            if not any(negation in before for negation in NEGATION_TEXTS):
                return True
            start = index + len(token_lower)
    return False


def has_single_unit_indicator(text: str) -> bool:
    raw = str(text or "")
    if has_positive_followme_indicator(raw):
        return True
    lower = raw.lower()
    for token in SINGLE_UNIT_RISK_TEXTS:
        token_lower = token.lower()
        start = 0
        while True:
            index = lower.find(token_lower, start)
            if index < 0:
                break
            before = lower[max(0, index - 28) : index]
            if not any(negation in before for negation in NEGATION_TEXTS):
                return True
            start = index + len(token_lower)
    return False


def has_followme_display_fixture_indicator(text: str) -> bool:
    raw = str(text or "")
    upper = raw.upper().replace(" ", "")
    has_followme = "FOLLOWME" in upper or "FOLLOWME" in upper.replace("FOLLOW ME", "FOLLOWME")
    if not has_followme:
        return False
    has_samsung = "SAMSUNG" in upper or "\u4e09\u661f" in raw
    has_fixture = any(term in raw for term in FOLLOWME_DISPLAY_FIXTURE_TERMS)
    has_label_context = any(term in raw for term in FOLLOWME_DISPLAY_LABEL_TERMS)
    has_negative_product_context = any(
        term in raw
        for term in (
            "\u53ea\u662f\u6d77\u5831",
            "\u55ae\u7d14\u6d77\u5831",
            "\u5ee3\u544a\u6d77\u5831",
            "\u4e0d\u662f\u5546\u54c1",
            "\u4e0d\u662f\u4e3b\u89d2",
            "\u65c1\u908a\u5ee3\u544a",
        )
    )
    return has_samsung and (has_fixture or has_label_context) and not has_negative_product_context


def followme_distant_risk(records: list[dict[str, object]], candidate_names: set[str]) -> list[str]:
    risky: list[str] = []
    for record in records:
        name = str(record.get("file_name") or record.get("filename") or "")
        if name not in candidate_names:
            continue
        view_text = " ".join(text_value(record.get(key)) for key in ("view_type", "category", "human_category"))
        model = record.get("human_model") or record.get("model")
        if DISTANT_VIEW_TEXT not in view_text or not missing_model(model):
            continue
        evidence = " ".join(
            text_value(record.get(key))
            for key in (
                "thinking",
                "stream_buffer",
                "raw",
                "notes",
                "model",
                "human_model",
            )
        ).lower()
        if has_positive_followme_indicator(evidence):
            risky.append(name)
    return risky


def infer_followme_model_from_evidence(evidence: str, price: object = None) -> str:
    """Resolve only same-photo identity evidence; price never selects a family."""
    del price
    return resolve_followme_model(evidence, unresolved=True) or FOLLOWME_UNRESOLVED


def build_rescued_followme_thinking(record: dict[str, object], evidence: str) -> str:
    existing_model = str(record.get("model") or "").strip()
    model = (
        normalize_followme_family(existing_model)
        or infer_followme_model_from_evidence(evidence)
    )
    price = str(record.get("price") or record.get("human_price") or "").strip() or NO_PRICE_TEXT
    return (
        f"最終校正：這張判定為{SINGLE_VIEW_TEXT}，型號 {model}，價格 {price}。"
        "畫面中有 Samsung FollowMe 立式展示/產品標示，且全張原圖完整入鏡螢幕最多兩台；"
        "若全圖實際有三台以上完整螢幕，必須改判遠景，不能讓 FollowMe 覆蓋全圖幾何。"
    )


def build_demoted_single_thinking(record: dict[str, object]) -> str:
    model = str(record.get("model") or record.get("human_model") or "").strip() or NO_MODEL_TEXT
    price = str(record.get("price") or record.get("human_price") or "").strip() or NO_PRICE_TEXT
    return (
        f"最終校正：這張含單一主角、側標、實體價牌或 FollowMe 線索，不能當遠景放行；"
        f"暫判為{SINGLE_VIEW_TEXT}，型號 {model}，價格 {price}。"
    )


def rescue_followme_distant_records(records: list[dict[str, object]], candidate_names: set[str]) -> list[str]:
    """Turn obvious foreground FollowMe false-distant outputs into single-unit candidates.

    The result is still subject to normal filename/upload guards. If the price is
    missing, current-year output remains review-required instead of being
    uploaded as complete.
    """
    rescued: list[str] = []
    for record in records:
        name = str(record.get("file_name") or record.get("filename") or "")
        if name not in candidate_names:
            continue
        view_text = " ".join(text_value(record.get(key)) for key in ("view_type", "category", "human_category"))
        model = record.get("human_model") or record.get("model")
        if DISTANT_VIEW_TEXT not in view_text or not missing_model(model):
            continue
        evidence = " ".join(
            text_value(record.get(key))
            for key in (
                "thinking",
                "stream_buffer",
                "raw",
                "notes",
                "model",
                "human_model",
            )
        )
        if not has_positive_followme_indicator(evidence):
            continue
        record["view_type"] = SINGLE_VIEW_TEXT
        record["category"] = SINGLE_VIEW_TEXT
        if missing_model(record.get("model")):
            record["model"] = infer_followme_model_from_evidence(evidence, record.get("price") or record.get("human_price"))
        record["thinking"] = build_rescued_followme_thinking(record, evidence)
        record["stream_buffer"] = record["thinking"]
        rescued.append(name)
    return rescued


def demote_single_clue_distant_records(records: list[dict[str, object]], candidate_names: set[str]) -> list[str]:
    """Turn unsafe distant outputs with single-unit clues into blocked single-unit rows."""
    demoted: list[str] = []
    for record in records:
        name = str(record.get("file_name") or record.get("filename") or "")
        if name not in candidate_names:
            continue
        view_text = " ".join(text_value(record.get(key)) for key in ("view_type", "category", "human_category"))
        if DISTANT_VIEW_TEXT not in view_text:
            continue
        evidence = " ".join(
            text_value(record.get(key))
            for key in (
                "thinking",
                "stream_buffer",
                "raw",
                "notes",
                "model",
                "human_model",
            )
        )
        if not has_single_unit_indicator(evidence):
            continue
        record["view_type"] = SINGLE_VIEW_TEXT
        record["category"] = SINGLE_VIEW_TEXT
        record["thinking"] = build_demoted_single_thinking(record)
        record["stream_buffer"] = record["thinking"]
        demoted.append(name)
    return demoted


def abort_reason_for_rerun(
    args,
    rerun_records: list[dict[str, object]],
    candidate_names: set[str],
    logs: list[str],
) -> tuple[str, dict[str, object]]:
    if has_context_error(logs):
        return "model_context_error", {}

    matched_names = {
        str(record.get("file_name") or record.get("filename") or "")
        for record in rerun_records
        if str(record.get("file_name") or record.get("filename") or "") in candidate_names
    }
    required = math.ceil(len(candidate_names) * args.min_completion_ratio)
    if len(matched_names) < required:
        return "incomplete_staged_rerun", {"matched_records": len(matched_names), "required_records": required}

    narration_conflicts = structured_narration_conflicts(rerun_records, candidate_names)
    if narration_conflicts:
        return (
            "structured_narration_conflict",
            {
                "matched_records": len(matched_names),
                "conflicting_records": len(narration_conflicts),
                "sample": ";".join(narration_conflicts[:5]),
            },
        )

    ratio, bad_count, matched_count = single_missing_ratio(rerun_records, candidate_names)
    if matched_count >= args.min_quality_guard_records and ratio >= args.max_single_missing_ratio:
        return (
            "suspicious_mass_single_missing",
            {
                "matched_records": matched_count,
                "single_missing_records": bad_count,
                "single_missing_ratio": f"{ratio:.3f}",
            },
        )
    risky_followme = followme_distant_risk(rerun_records, candidate_names)
    if risky_followme:
        return (
            "followme_distant_risk",
            {
                "matched_records": len(matched_names),
                "risky_followme_distant": len(risky_followme),
                "sample": ";".join(risky_followme[:5]),
            },
        )
    return "", {"matched_records": len(matched_names)}


def row_matches_reason(row: dict[str, object], reason_contains: list[str]) -> bool:
    if not reason_contains:
        return True
    reason = str(row.get("reason") or row.get("reasons") or "")
    return any(token in reason for token in reason_contains)


def group_candidates(
    rows: list[dict[str, object]],
    source_root: Path,
    reason_contains: list[str],
) -> tuple[dict[tuple[str, str, str], list[dict[str, object]]], int]:
    grouped: dict[tuple[str, str, str], list[dict[str, object]]] = {}
    skipped = 0
    resolved_source_root = source_root.resolve()
    for row in rows:
        if not row_matches_reason(row, reason_contains):
            continue
        folder = str(row.get("source_folder") or "")
        audit_folder = str(row.get("audit_folder") or "")
        period = str(row.get("period") or infer_period_from_text(folder))
        file_name = str(row.get("file_name") or "")
        source_path = None
        direct_text = str(row.get("source_path") or "").strip()
        if direct_text and file_name:
            direct = Path(direct_text)
            try:
                resolved_direct = direct.resolve()
                resolved_direct.relative_to(resolved_source_root)
                if (
                    resolved_direct.is_file()
                    and resolved_direct.name == file_name
                    and (not period or period in str(resolved_direct))
                ):
                    source_path = resolved_direct
            except (OSError, ValueError):
                source_path = None
        if source_path is None:
            source_path = resolve_source_path(
                file_name,
                Path(folder) if folder else None,
                resolved_source_root,
                period,
            )
        if not source_path or not audit_folder:
            skipped += 1
            continue
        row["source_path"] = str(source_path)
        row["source_folder"] = str(source_path.parent)
        grouped.setdefault((str(source_path.parent), audit_folder, period), []).append(row)
    return grouped, skipped


def staging_dir_for(root: Path, stamp: str, folder: Path, period: str) -> Path:
    digest = hashlib.sha1(str(folder).encode("utf-8")).hexdigest()[:8]
    safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in folder.name)
    return root / stamp / f"{period}_{safe_name}_{digest}"


def restore_backend_work_dir(base_url: str, source_folder: Path) -> None:
    """Move the dashboard/backend away from a temporary staging folder."""
    try:
        json_request(base_url, "/api/set_work_dir", {"dir": str(source_folder)}, timeout=30)
    except Exception as exc:
        print(f"[warn] restore work dir failed: {source_folder} error={exc}", flush=True)


def stage_images(rows: list[dict[str, object]], staging_dir: Path) -> int:
    staging_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    seen: set[str] = set()
    source_map: dict[str, dict[str, str]] = {}
    for row in rows:
        source = Path(str(row.get("source_path") or ""))
        if not source.exists() or not source.is_file():
            continue
        target = staging_dir / source.name
        if target.name in seen:
            continue
        seen.add(target.name)
        shutil.copy2(source, target)
        original = str(source.resolve())
        source_map[target.name] = {
            "source_item_id": hashlib.sha256(original.casefold().encode("utf-8")).hexdigest(),
            "original_source_path": original,
            "period": str(row.get("period") or infer_period_from_text(original)),
            "audit_folder": str(row.get("audit_folder") or ""),
            # Future capture systems may explicitly lock the photo as distant
            # or close-up.  Preserve only explicit metadata; never infer this
            # contract from a legacy filename.
            "source_view_hint": str(row.get("source_view_hint") or ""),
            "source_view_hint_locked": str(
                row.get("source_view_hint_locked") or ""
            ).strip().casefold() in {"1", "true", "yes"},
            "source_view_hint_source": str(
                row.get("source_view_hint_source") or ""
            ),
            "source_view_hint_version": str(
                row.get("source_view_hint_version") or ""
            ),
        }
        count += 1
    if source_map:
        map_path = staging_dir / ".ocr_source_map.json"
        temp_path = staging_dir / ".ocr_source_map.json.tmp"
        temp_path.write_text(
            json.dumps({"version": 1, "items": source_map}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp_path, map_path)
    return count


def read_success_rows(path: Path) -> list[dict[str, str]]:
    rows = read_dict_csv(path)
    for row in rows:
        for header in SUCCESS_HEADERS:
            row.setdefault(header, "")
    return rows


def merge_records(
    original_rows: list[dict[str, str]],
    rerun_records: list[dict[str, object]],
    candidate_names: set[str],
) -> tuple[list[dict[str, str]], int, int]:
    rerun_by_name = {
        str(record.get("file_name") or record.get("filename") or ""): record
        for record in rerun_records
        if str(record.get("file_name") or record.get("filename") or "") in candidate_names
    }
    merged: list[dict[str, str]] = []
    updated = 0
    seen: set[str] = set()

    for row in original_rows:
        name = row.get("file_name") or row.get("filename") or ""
        replacement = rerun_by_name.get(name)
        if replacement:
            new_row = dict(row)
            for header in SUCCESS_HEADERS:
                if header in replacement:
                    value = replacement.get(header)
                    new_row[header] = "" if value is None else str(value)
            merged.append(new_row)
            updated += 1
            seen.add(name)
        else:
            merged.append(row)
            if name:
                seen.add(name)

    appended = 0
    for name, record in rerun_by_name.items():
        if name in seen:
            continue
        merged.append({header: "" if record.get(header) is None else str(record.get(header, "")) for header in SUCCESS_HEADERS})
        appended += 1
    return merged, updated, appended


def clear_existing_outputs(audit_folder: Path, dry_run: bool, keep_backup: bool = False) -> tuple[Path, int, str]:
    copied_rows = read_dict_csv(audit_folder / "copied.csv")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = audit_folder / f"flat_output_backup_before_staged_rerun_{stamp}"
    count = 0
    for row in copied_rows:
        target_text = row.get("target_path") or ""
        if not target_text:
            continue
        target = Path(target_text)
        if not target.exists() or not target.is_file():
            continue
        count += 1
        if not dry_run:
            if keep_backup:
                backup_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target), str(backup_dir / target.name))
            else:
                target.unlink()
    action = "backed_up" if keep_backup else "removed"
    return backup_dir, count, action


def split_plan(plan: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    safe_statuses = {READY_STATUS, NO_CHANGE_STATUS}
    safe_rows = [row for row in plan if row.get("status") in safe_statuses]
    blocked_rows = [row for row in plan if row.get("status") not in safe_statuses]
    return safe_rows, blocked_rows


def rebuild_outputs(
    args,
    source_folder: Path,
    audit_folder: Path,
    period: str,
    merged_rows: list[dict[str, str]],
    candidate_names: set[str],
) -> dict[str, object]:
    success_path = audit_folder / "success_records.csv"
    if success_path.exists() and not args.dry_run:
        backup_success = audit_folder / f"success_records.csv.before_staged_rerun_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(success_path, backup_success)

    results_map = records_to_map(merged_rows)
    plan = make_plan(source_folder, results_map, period, args.price_symbol)
    counts = summarize(plan)
    safe_plan, blocked_plan = split_plan(plan)
    replacement_plan = [
        item for item in safe_plan if item.get("original_name", "") in candidate_names
    ]
    blocked_path = audit_folder / "blocked_after_staged_rerun.csv"

    copied = read_dict_csv(audit_folder / "copied.csv")
    replaced = 0
    failed_replacements: list[str] = []
    if not args.dry_run:
        # Publish each replacement independently.  The old flat file stays in
        # place until its new sibling has been written and verified.
        existing_by_name = {
            str(row.get("original_name") or ""): row
            for row in copied
            if row.get("original_name")
        }
        published: list[dict[str, str]] = []
        output_root = Path(args.output_dir).resolve()
        for item in replacement_plan:
            source = Path(item["original_path"])
            if not source.exists() or not source.is_file():
                failed_replacements.append(item.get("original_name", ""))
                continue
            old_row = existing_by_name.get(item.get("original_name", ""))
            old_target = (
                Path(old_row["target_path"]).resolve()
                if old_row and old_row.get("target_path")
                else None
            )
            desired_name = item["target_name"] or item["original_name"]
            if old_target and old_target.name == desired_name:
                target = old_target
            else:
                target = unique_target_path(output_root, desired_name)
            target.parent.mkdir(parents=True, exist_ok=True)
            staged = target.with_name(
                f".{target.stem}.staged-rerun-{os.getpid()}{target.suffix}"
            )
            try:
                staged.unlink(missing_ok=True)
                copy_image_for_flat_output(source, staged)
                if not staged.exists() or staged.stat().st_size == 0:
                    raise OSError("staged output is empty")
                # Re-open the generated file before it can replace a deliverable.
                from PIL import Image
                with Image.open(staged) as image:
                    image.verify()
                os.replace(staged, target)
                if old_target and old_target != target and old_target.is_file():
                    try:
                        old_target.relative_to(output_root)
                    except ValueError:
                        raise OSError(f"refusing to remove output outside root: {old_target}")
                    old_target.unlink()
            except Exception:
                staged.unlink(missing_ok=True)
                failed_replacements.append(item.get("original_name", ""))
                continue
            published.append({
                "status": "copied",
                "reason": "",
                "period": item["period"],
                "original_name": item["original_name"],
                "target_name": target.name,
                "category": item["category"],
                "model": item["model"],
                "price": item["price"],
                "original_path": item["original_path"],
                "target_path": str(target),
            })
            replaced += 1
        published_by_name = {row["original_name"]: row for row in published}
        copied = [published_by_name.get(row.get("original_name", ""), row) for row in copied]
        copied.extend(row for name, row in published_by_name.items() if name not in existing_by_name)
        write_dict_csv(success_path, merged_rows, SUCCESS_HEADERS)
        write_csv(audit_folder / "rename_plan.csv", plan)
        write_csv(audit_folder / "conflicts.csv", [item for item in plan if item["status"] == CONFLICT_STATUS])
        write_csv(blocked_path, blocked_plan)
        write_csv(audit_folder / "copied.csv", copied)

    return {
        "records": len(merged_rows),
        "ready": counts.get("ready", 0),
        "no_change": counts.get("no_change", 0),
        "blocked": len(blocked_plan),
        "missing_result": counts.get(MISSING_RESULT_STATUS, 0),
        "missing_source": counts.get(MISSING_SOURCE_STATUS, 0),
        "conflict": counts.get(CONFLICT_STATUS, 0),
        "blocked_path": str(blocked_path) if blocked_plan else "",
        "copied": replaced,
        "failed_replacements": len(failed_replacements),
        "backup_action": "transactional",
        "backup_dir": "",
    }


STAGING_FREE_RESERVE_BYTES = 16 * 1024 * 1024 * 1024


def run_group(args, source_folder_text: str, audit_folder_text: str, period: str, rows: list[dict[str, object]], index: int, total: int, stamp: str) -> dict[str, object]:
    source_folder = Path(source_folder_text)
    audit_folder = Path(audit_folder_text)
    staging_root = Path(args.staging_root)
    staging_dir = staging_dir_for(staging_root, stamp, source_folder, period)
    summary: dict[str, object] = {
        "folder": str(source_folder),
        "period": period,
        "audit_folder": str(audit_folder),
        "staging_dir": str(staging_dir),
        "queued": len(rows),
    }
    unique_sources: dict[str, Path] = {}
    for row in rows:
        source = Path(str(row.get("source_path") or ""))
        if source.is_file():
            unique_sources[str(source.resolve()).casefold()] = source
    source_bytes = sum(source.stat().st_size for source in unique_sources.values())
    disk_probe = staging_root if staging_root.exists() else staging_root.parent
    free_before = shutil.disk_usage(disk_probe).free
    summary["staging_source_bytes"] = source_bytes
    summary["staging_free_before"] = free_before
    if free_before < STAGING_FREE_RESERVE_BYTES + source_bytes:
        summary["staged"] = ""
        summary["aborted"] = 1
        summary["abort_reason"] = (
            "staging_disk_guard: "
            f"free={free_before} source_bytes={source_bytes} "
            f"reserve={STAGING_FREE_RESERVE_BYTES}"
        )
        print(
            f"[abort] {source_folder.name} reason={summary['abort_reason']}",
            flush=True,
        )
        return summary
    try:
        staged_count = stage_images(rows, staging_dir)
    except OSError as exc:
        if not args.keep_staging:
            shutil.rmtree(staging_dir, ignore_errors=True)
        summary["staged"] = ""
        summary["aborted"] = 1
        summary["abort_reason"] = f"staging_copy_failed: {exc}"
        print(f"[abort] {source_folder.name} reason=staging_copy_failed error={exc}", flush=True)
        return summary
    summary["staged"] = staged_count
    print(f"[folder] {index}/{total} {source_folder} candidates={len(rows)} staged={staged_count}", flush=True)
    if not staged_count:
        return summary

    if args.dry_run:
        return summary

    json_request(args.backend_url, "/api/set_work_dir", {"dir": str(staging_dir)}, timeout=30)
    log_start = get_log_total(args.backend_url)
    response = json_request(
        args.backend_url,
        "/api/start_batch",
        {"dir": str(staging_dir), "restart": False, "confirmed": True, "reprocess_last_n": 0},
        timeout=30,
    )
    print(f"[start] {staging_dir.name} response={response.get('status')}", flush=True)
    status = wait_for_folder_done(args.backend_url, staging_dir, args.timeout_minutes, args.poll_seconds)
    finalized = finalize_capped_group(args, staging_dir, status)
    rerun_records = list(finalized["records"])

    candidate_names = set(finalized["record_names"])
    capped_names = set(finalized["capped_names"])
    summary["deferred_capped"] = len(capped_names)
    resolver_report = dict(finalized["resolver_report"])
    summary["zero_model_resolved"] = int(resolver_report.get("safe_count") or 0)
    logs = get_logs_since(args.backend_url, log_start)
    rescued_followme = rescue_followme_distant_records(rerun_records, candidate_names)
    if rescued_followme:
        summary["rescued_followme_distant"] = len(rescued_followme)
        summary["rescued_followme_sample"] = ";".join(rescued_followme[:5])
    demoted_single = demote_single_clue_distant_records(rerun_records, candidate_names)
    if demoted_single:
        summary["demoted_distant_single_clue"] = len(demoted_single)
        summary["demoted_distant_single_sample"] = ";".join(demoted_single[:5])
    contained_conflicts = contained_structured_narration_conflicts(rerun_records, candidate_names)
    if contained_conflicts:
        summary["contained_review_conflicts"] = len(contained_conflicts)
        summary["contained_review_conflict_sample"] = ";".join(contained_conflicts[:5])
    abort_reason, guard_details = abort_reason_for_rerun(args, rerun_records, candidate_names, logs)
    if abort_reason:
        summary.update(guard_details)
        summary["processed"] = (status.get("stats") or {}).get("processed", "")
        summary["aborted"] = 1
        summary["abort_reason"] = abort_reason
        print(f"[abort] {source_folder.name} reason={abort_reason} details={guard_details}", flush=True)
        restore_backend_work_dir(args.backend_url, source_folder)
        if not args.keep_staging:
            shutil.rmtree(staging_dir, ignore_errors=True)
        return summary

    original_rows = read_success_rows(audit_folder / "success_records.csv")
    merged_rows, updated, appended = merge_records(original_rows, rerun_records, candidate_names)
    rebuild = rebuild_outputs(
        args,
        source_folder,
        audit_folder,
        period,
        merged_rows,
        candidate_names,
    )
    summary.update(rebuild)
    summary["updated"] = updated
    summary["appended"] = appended
    summary["processed"] = (status.get("stats") or {}).get("processed", "")
    summary["aborted"] = 0
    summary["abort_reason"] = ""
    print(
        f"[export] {source_folder.name} updated={updated} copied={summary.get('copied')} "
        f"old_outputs_{summary.get('backup_action')}={summary.get('backed_up')}",
        flush=True,
    )
    restore_backend_work_dir(args.backend_url, source_folder)
    if not args.keep_staging and not capped_names:
        shutil.rmtree(staging_dir, ignore_errors=True)
    return summary


def _status_work_dir(status: dict[str, object]) -> Path:
    value = status.get("current_relative_dir") or status.get("current_work_dir") or ""
    if not value:
        raise RuntimeError("attach refused: API did not report current work directory")
    return Path(str(value)).resolve()


def _capped_adjudication_names(status: dict[str, object]) -> set[str]:
    capped = status.get("capped_adjudication") or {}
    if not isinstance(capped, dict):
        return set()
    items = capped.get("items") or []
    if not isinstance(items, list):
        return set()
    return {
        Path(str(item.get("file_name") or "")).name
        for item in items
        if isinstance(item, dict) and str(item.get("file_name") or "").strip()
    }


def _durable_capped_adjudication_names(
    current_dir: Path,
    status: dict[str, object],
) -> set[str]:
    """Read the complete capped set from its source-bound durable queue."""

    _processed, expected_count, _total = _completed_or_deferred_count(status)
    if expected_count == 0:
        return set()
    queue_path = current_dir / ".ocr_capped_adjudication_queue.json"
    try:
        payload = json.loads(queue_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"attach refused: capped queue is unavailable: {queue_path}: {exc}"
        ) from exc
    if (
        not isinstance(payload, dict)
        or Path(str(payload.get("image_dir") or "")).resolve() != current_dir
        or not isinstance(payload.get("items"), list)
    ):
        raise RuntimeError("attach refused: capped queue binding is invalid")
    names = {
        Path(str(item.get("file_name") or "")).name
        for item in payload["items"]
        if isinstance(item, dict) and str(item.get("file_name") or "").strip()
    }
    if len(names) != expected_count:
        raise RuntimeError(
            "attach refused: capped queue count does not match backend status"
        )
    return names


def _durable_staged_source_names(
    current_dir: Path,
    status: dict[str, object],
) -> set[str]:
    """Read the immutable filename inventory written when the group was staged.

    The evidence candidate CSV is rebuilt while a long-running group is active.
    Rows that have already become terminal can therefore disappear from that
    CSV even though they remain part of the original staged group.  Finalizing
    against the refreshed CSV would reject a healthy exact partition forever.
    The source map is the content-bound inventory for this staging directory.
    """

    source_map_path = current_dir / ".ocr_source_map.json"
    try:
        payload = json.loads(source_map_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"attach refused: staged source map is unavailable: {source_map_path}: {exc}"
        ) from exc
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, dict) or not items:
        raise RuntimeError("attach refused: staged source map binding is invalid")
    names = {
        Path(str(name)).name
        for name, binding in items.items()
        if str(name).strip() and isinstance(binding, dict)
    }
    _processed, _deferred, expected_total = _completed_or_deferred_count(status)
    if not names or len(names) != len(items) or len(names) != expected_total:
        raise RuntimeError(
            "attach refused: staged source map count does not match backend status"
        )
    return names


def _completed_or_deferred_count(status: dict[str, object]) -> tuple[int, int, int]:
    stats = dict(status.get("stats") or {})
    processed = int(stats.get("processed") or 0)
    total = int(stats.get("total") or 0)
    capped = status.get("capped_adjudication") or {}
    deferred = int(capped.get("count") or 0) if isinstance(capped, dict) else 0
    return processed, deferred, total


def _task_record(task: dict[str, object]) -> dict[str, object] | None:
    """Convert one durable Label Studio task to the runner's flat record."""

    data = task.get("data") or {}
    if not isinstance(data, dict):
        return None
    file_name = Path(str(data.get("image") or "")).name
    if not file_name:
        return None
    meta = data.get("ocr_meta") or {}
    record: dict[str, object] = dict(meta) if isinstance(meta, dict) else {}
    record["file_name"] = file_name
    annotations = task.get("annotations") or []
    if isinstance(annotations, list) and annotations:
        annotation = annotations[0] if isinstance(annotations[0], dict) else {}
        record.setdefault("timestamp", annotation.get("created_at") or "")
        for field in annotation.get("result") or []:
            if not isinstance(field, dict):
                continue
            name = str(field.get("from_name") or "")
            value = field.get("value") or {}
            if not isinstance(value, dict):
                continue
            if name == "category":
                choices = value.get("choices") or []
                if isinstance(choices, list) and choices:
                    record["view_type"] = choices[0]
                    record["category"] = choices[0]
            elif name in {"model", "price"}:
                values = value.get("text") or []
                if isinstance(values, list) and values:
                    text = str(values[0] or "")
                    record[name] = None if text.lower() == "null" else text
    record.setdefault("category", record.get("view_type") or "")
    return record


def load_group_records_from_disk(staging_dir: Path) -> list[dict[str, object]]:
    """Reload the exact durable terminal set after an external resolver write."""

    records: dict[str, dict[str, object]] = {}
    result_files = sorted(
        staging_dir.glob("*OCR成功.json"),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
    )
    for path in result_files:
        try:
            tasks = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"disk attach refused: unreadable result file {path}: {exc}") from exc
        if not isinstance(tasks, list):
            raise RuntimeError(f"disk attach refused: result file is not a task list: {path}")
        for task in tasks:
            record = _task_record(task) if isinstance(task, dict) else None
            if record:
                records[str(record["file_name"])] = record
    return list(records.values())


def _source_names_from_disk(staging_dir: Path) -> set[str]:
    path = staging_dir / ".ocr_source_map.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"disk attach refused: unreadable source map: {exc}") from exc
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, dict) or not items:
        raise RuntimeError("disk attach refused: invalid source map")
    names = {
        Path(str(name)).name
        for name, binding in items.items()
        if str(name).strip() and isinstance(binding, dict)
    }
    if len(names) != len(items):
        raise RuntimeError("disk attach refused: duplicate/invalid source filenames")
    return names


def _capped_names_from_disk(staging_dir: Path) -> set[str]:
    path = staging_dir / ".ocr_capped_adjudication_queue.json"
    if not path.is_file():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"disk attach refused: unreadable capped queue: {exc}") from exc
    if (
        not isinstance(payload, dict)
        or Path(str(payload.get("image_dir") or "")).resolve() != staging_dir.resolve()
        or not isinstance(payload.get("items"), list)
    ):
        raise RuntimeError("disk attach refused: capped queue binding is invalid")
    return {
        Path(str(item.get("file_name") or "")).name
        for item in payload["items"]
        if isinstance(item, dict) and str(item.get("file_name") or "").strip()
    }


def _stop_at_exact_staging_boundary(
    args,
    staging_dir: Path,
    reason: str,
) -> dict[str, object]:
    """Establish and prove one source-bound photo-boundary pause."""

    stopped = json_request(
        args.backend_url,
        "/api/stop",
        {"reason": reason},
        timeout=30,
    )
    if str(stopped.get("status") or "") != "stopped":
        raise RuntimeError(f"group boundary stop was not accepted: {stopped}")
    idle = json_request(args.backend_url, "/api/status", timeout=30)
    idle_pause = idle.get("pipeline_pause") or {}
    if (
        idle.get("is_running") is not False
        or str(idle.get("current_file") or "").strip() not in {"", "None"}
        or _status_work_dir(idle) != staging_dir.resolve()
        or not isinstance(idle_pause, dict)
        or Path(str(idle_pause.get("current_dir") or "")).resolve()
        != staging_dir.resolve()
    ):
        raise RuntimeError("group finalization could not prove exact idle pipeline pause")
    return idle


def finalize_capped_group(
    args,
    staging_dir: Path,
    status: dict[str, object],
) -> dict[str, object]:
    """Recover exact pre-inference software faults once, then finalize.

    A settled staged group must be an exact partition of terminal records,
    durable capped rows, and (temporarily) exact eligible pre-inference
    failures.  Only the third set may be requeued, at most once per filename
    during this invocation.  Unknown/malformed failures fail closed while the
    exact staging pause remains in place.
    """

    staging_dir = staging_dir.resolve()
    recovered_names: set[str] = set()
    recovery_manifests: list[str] = []
    while True:
        processed, deferred, total = _completed_or_deferred_count(status)
        if total <= 0 or processed + deferred != total:
            raise RuntimeError(
                "group technical recovery refused before photo boundary "
                f"processed={processed} deferred={deferred} total={total}"
            )
        if _status_work_dir(status) != staging_dir:
            raise RuntimeError(
                "group technical recovery refused: backend staging binding changed"
            )

        source_names = _durable_staged_source_names(staging_dir, status)
        records = load_group_records_from_disk(staging_dir)
        record_names = {
            str(record.get("file_name") or record.get("filename") or "")
            for record in records
        }
        capped_names = _durable_capped_adjudication_names(staging_dir, status)
        if (
            not record_names.issubset(source_names)
            or not capped_names.issubset(source_names)
            or record_names & capped_names
        ):
            raise RuntimeError(
                "group technical recovery refused: terminal/capped binding is invalid"
            )
        missing_names = source_names - record_names - capped_names
        if not missing_names:
            finalized = _finalize_capped_group_after_technical_recovery(
                args,
                staging_dir,
                status,
            )
            finalized["preinference_recovered_names"] = sorted(recovered_names)
            finalized["preinference_recovery_manifests"] = recovery_manifests
            return finalized

        repeated = missing_names & recovered_names
        if repeated:
            raise RuntimeError(
                "group technical recovery refused: eligible filename failed again "
                f"after its one bounded recovery: {sorted(repeated)}"
            )

        _stop_at_exact_staging_boundary(
            args,
            staging_dir,
            "bounded_preinference_system_error_recovery",
        )
        # build_plan is the authoritative allow-list and durable call-history
        # validator.  Unknown errors, duplicate rows, contaminated evidence, or
        # an already exhausted three-call history all raise before any write.
        plan = build_preinference_system_error_recovery(
            staging_dir,
            Path(args.output_dir).resolve() / "_ocr_audit",
            sorted(missing_names),
        )
        manifest = apply_preinference_system_error_recovery(plan)
        recovered_names.update(missing_names)
        recovery_manifests.append(str(manifest))

        response = json_request(
            args.backend_url,
            "/api/start_batch",
            {
                "dir": str(staging_dir),
                "restart": False,
                "confirmed": True,
                "reprocess_last_n": 0,
            },
            timeout=30,
        )
        if str(response.get("status") or "") != "started":
            raise RuntimeError(
                "group technical recovery was applied but same staging did not start: "
                f"{response}"
            )
        wait_for_resume_start(args.backend_url)
        status = wait_for_folder_done(
            args.backend_url,
            staging_dir,
            args.timeout_minutes,
            args.poll_seconds,
        )


def _ensure_all_capped_result_file(
    staging_dir: Path,
    status: dict[str, object],
) -> Path:
    """Create the empty terminal container for a legacy all-capped run.

    A run normally creates ``*OCR成功.json`` after its first accepted result.
    When every source is already at the lifetime three-call cap, there is no
    first accepted result, but the deterministic zero-model resolver still
    needs a source-bound terminal container. Creating it is safe only after
    the backend is idle on the exact paused staging directory and the durable
    capped queue is the complete source partition.
    """

    staging_dir = staging_dir.resolve()
    success_files = sorted(
        staging_dir.glob("*OCR成功.json"),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
    )
    if success_files:
        return success_files[-1]

    processed, deferred, total = _completed_or_deferred_count(status)
    pause = status.get("pipeline_pause") or {}
    if (
        total <= 0
        or processed != 0
        or deferred != total
        or status.get("is_running") is not False
        or str(status.get("current_file") or "").strip() not in {"", "None"}
        or _status_work_dir(status) != staging_dir
        or not isinstance(pause, dict)
        or Path(str(pause.get("current_dir") or "")).resolve() != staging_dir
    ):
        raise RuntimeError(
            "group finalization has no durable success result file and "
            "the batch is not an exact idle all-capped partition"
        )

    source_names = _durable_staged_source_names(staging_dir, status)
    capped_names = _durable_capped_adjudication_names(staging_dir, status)
    if not source_names or source_names != capped_names:
        raise RuntimeError(
            "group finalization refused empty result creation: "
            "capped queue does not exactly equal the staged source set"
        )

    result_file = staging_dir / "capped-zero-model-OCR成功.json"
    temp_file = staging_dir / ".capped-zero-model-OCR成功.json.tmp"
    temp_file.write_text("[]\n", encoding="utf-8")
    os.replace(temp_file, result_file)
    return result_file


def _candidate_capped_result_file(staging_dir: Path) -> Path:
    """Return the existing terminal file or a deterministic future container."""

    success_files = sorted(
        staging_dir.glob("*OCR成功.json"),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
    )
    if success_files:
        return success_files[-1]
    return staging_dir / "capped-zero-model-OCR成功.json"


ZERO_MODEL_ACTIVITY_SCHEMA = "samsung-ocr-zero-model-activity/v1"


def _write_zero_model_activity(
    args,
    staging_dir: Path,
    *,
    active: bool,
    phase: str,
    progress: dict[str, object] | None = None,
    error: str = "",
) -> None:
    """Atomically expose deterministic adjudication without blocking it."""

    status_path = (
        Path(args.output_dir).resolve()
        / "_ocr_audit"
        / "zero_model_adjudication_status.json"
    )
    event = dict(progress or {})
    payload = {
        "schema": ZERO_MODEL_ACTIVITY_SCHEMA,
        "active": bool(active),
        "phase": str(phase or "unknown"),
        "updated_at": datetime.now().astimezone().isoformat(),
        "pid": os.getpid(),
        "staging_dir": str(staging_dir.resolve()),
        "period": (
            re.findall(r"20\d{4}", str(staging_dir))[-1]
            if re.findall(r"20\d{4}", str(staging_dir))
            else ""
        ),
        "processed": max(0, int(event.get("processed") or 0)),
        "total": max(0, int(event.get("total") or 0)),
        "safe": max(0, int(event.get("safe") or 0)),
        "unresolved": max(0, int(event.get("unresolved") or 0)),
        "enqueued": max(0, int(event.get("enqueued") or 0)),
        "matched": max(0, int(event.get("matched") or 0)),
        "unit": str(event.get("unit") or "photos"),
        "error": str(error or ""),
    }
    status_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = status_path.with_name(
        f".{status_path.name}.{os.getpid()}.tmp"
    )
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temp_path, status_path)


def _zero_model_progress_writer(args, staging_dir: Path, phase_prefix: str):
    def publish(event: dict[str, object]) -> None:
        phase = str(event.get("phase") or "working")
        _write_zero_model_activity(
            args,
            staging_dir,
            active=True,
            phase=f"{phase_prefix}_{phase}",
            progress=event,
        )

    return publish


def _resume_exact_staging_after_zero_model_finalization(
    args,
    staging_dir: Path,
) -> dict[str, object]:
    """Refresh backend disk state and clear the exact source-bound pause.

    ``/api/start_batch`` restores the durable terminal/capped partition from
    disk and atomically removes ``pipeline_pause.json`` only after the same
    staging directory has been accepted.  A group containing only completed
    and capped rows may settle immediately; that is a valid observable resume.
    """

    response = json_request(
        args.backend_url,
        "/api/start_batch",
        {
            "dir": str(staging_dir),
            "restart": False,
            "confirmed": True,
            "reprocess_last_n": 0,
        },
        timeout=30,
    )
    if str(response.get("status") or "") != "started":
        raise RuntimeError(
            "zero-model finalization completed but exact staging did not resume: "
            f"{response}"
        )
    resumed = wait_for_resume_start(args.backend_url)
    if resumed.get("is_running") is True:
        resumed = wait_for_folder_done(
            args.backend_url,
            staging_dir,
            float(getattr(args, "timeout_minutes", 5)),
            float(getattr(args, "poll_seconds", 0.25)),
        )
    processed, deferred, total = _completed_or_deferred_count(resumed)
    if (
        resumed.get("is_running") is not False
        or str(resumed.get("current_file") or "").strip() not in {"", "None"}
        or _status_work_dir(resumed) != staging_dir.resolve()
        or resumed.get("pipeline_pause")
        or total <= 0
        or processed + deferred != total
    ):
        raise RuntimeError(
            "zero-model finalization resume did not return to the exact settled "
            f"photo boundary processed={processed} deferred={deferred} total={total}"
        )
    return resumed


def _finalize_capped_group_after_technical_recovery(
    args,
    staging_dir: Path,
    status: dict[str, object],
) -> dict[str, object]:
    """Resolve a settled group's capped queue, then reattach from durable disk.

    Any failure is intentionally allowed to propagate after ``/api/stop`` so
    the source-bound pause and staging checkpoint remain intact.  No model call
    is made by this helper.
    """

    staging_dir = staging_dir.resolve()
    processed, deferred, total = _completed_or_deferred_count(status)
    if total <= 0 or processed + deferred != total:
        raise RuntimeError(
            "group finalization refused before photo boundary "
            f"processed={processed} deferred={deferred} total={total}"
        )
    if _status_work_dir(status) != staging_dir:
        raise RuntimeError("group finalization refused: backend staging binding changed")

    report: dict[str, object] = {
        "status": "not_needed",
        "model_calls_made": 0,
        "fourth_call_authorized": False,
    }
    if deferred:
        # First inspect the durable evidence without mutating files or creating
        # a global pause.  Legacy rows that lack three clean request-bound
        # traces are a valid durable review state; they must not freeze
        # unrelated photos or later folders.
        result_file = _candidate_capped_result_file(staging_dir)
        _write_zero_model_activity(
            args,
            staging_dir,
            active=True,
            phase="preflight_starting",
            progress={"processed": 0, "total": deferred, "unit": "photos"},
        )
        try:
            preflight = resolve_capped_queue(
                staging_dir=staging_dir,
                trace_path=Path(args.output_dir).resolve()
                / "_ocr_audit"
                / "v1945_evidence_trace.jsonl",
                result_file=result_file,
                upload_output_dir=Path(args.output_dir).resolve(),
                apply=False,
                progress_fn=_zero_model_progress_writer(
                    args,
                    staging_dir,
                    "preflight",
                ),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            _write_zero_model_activity(
                args,
                staging_dir,
                active=False,
                phase="preflight_failed",
                progress={"processed": 0, "total": deferred, "unit": "photos"},
                error=str(exc),
            )
            pause = status.get("pipeline_pause") or {}
            # Per-photo revalidation/enqueue failures remain in the exact
            # capped queue and are not exposed as completed results. The
            # successfully enqueued subset is already durable, so that
            # isolated remainder must not freeze this or later folders.
            if (
                not isinstance(pause, dict)
                or Path(str(pause.get("current_dir") or "")).resolve()
                != staging_dir
            ):
                _stop_at_exact_staging_boundary(
                    args,
                    staging_dir,
                    "capped_zero_model_preflight_failed",
                )
            raise
        if (
            preflight.get("status") != "dry_run"
            or int(preflight.get("model_calls_made") or 0) != 0
            or preflight.get("fourth_call_authorized") is not False
        ):
            raise RuntimeError(
                f"group zero-model preflight did not complete safely: {preflight}"
            )
        safe_count = int(preflight.get("safe_count") or 0)
        if safe_count:
            _write_zero_model_activity(
                args,
                staging_dir,
                active=True,
                phase="waiting_for_exact_photo_boundary",
                progress={
                    "processed": 0,
                    "total": safe_count,
                    "safe": safe_count,
                    "unresolved": int(preflight.get("unresolved_count") or 0),
                    "unit": "photos",
                },
            )
            idle_status = _stop_at_exact_staging_boundary(
                args,
                staging_dir,
                "capped_zero_model_adjudication_apply",
            )
            success_file = _ensure_all_capped_result_file(staging_dir, idle_status)
            try:
                report = resolve_capped_queue(
                    staging_dir=staging_dir,
                    trace_path=Path(args.output_dir).resolve()
                    / "_ocr_audit"
                    / "v1945_evidence_trace.jsonl",
                    result_file=success_file,
                    upload_output_dir=Path(args.output_dir).resolve(),
                    apply=True,
                    progress_fn=_zero_model_progress_writer(
                        args,
                        staging_dir,
                        "apply",
                    ),
                )
            except (OSError, RuntimeError, ValueError) as exc:
                _write_zero_model_activity(
                    args,
                    staging_dir,
                    active=False,
                    phase="apply_failed",
                    progress={
                        "processed": 0,
                        "total": safe_count,
                        "safe": safe_count,
                        "unit": "photos",
                    },
                    error=str(exc),
                )
                raise
            if (
                report.get("status") not in {"resolved", "partial_failure"}
                or int(report.get("model_calls_made") or 0) != 0
                or report.get("fourth_call_authorized") is not False
            ):
                _write_zero_model_activity(
                    args,
                    staging_dir,
                    active=False,
                    phase="apply_contract_failed",
                    progress={
                        "processed": int(report.get("enqueued_count") or 0),
                        "total": safe_count,
                        "safe": safe_count,
                        "unresolved": int(
                            report.get("queue_remaining_count") or 0
                        ),
                        "enqueued": int(report.get("enqueued_count") or 0),
                        "unit": "photos",
                    },
                    error=f"unsafe resolver report: {report.get('status')}",
                )
                raise RuntimeError(
                    f"group zero-model resolver did not complete safely: {report}"
                )
            try:
                _resume_exact_staging_after_zero_model_finalization(
                    args,
                    staging_dir,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                _write_zero_model_activity(
                    args,
                    staging_dir,
                    active=False,
                    phase="checkpoint_resume_failed",
                    progress={
                        "processed": int(report.get("enqueued_count") or 0),
                        "total": safe_count,
                        "safe": safe_count,
                        "unresolved": int(
                            report.get("queue_remaining_count") or 0
                        ),
                        "enqueued": int(report.get("enqueued_count") or 0),
                        "unit": "photos",
                    },
                    error=str(exc),
                )
                raise
            report["backend_reload_required_before_resume"] = False
            report["checkpoint_auto_resumed"] = True
            _write_zero_model_activity(
                args,
                staging_dir,
                active=False,
                phase="completed",
                progress={
                    "processed": safe_count,
                    "total": safe_count,
                    "safe": safe_count,
                    "unresolved": int(
                        report.get("queue_remaining_count") or 0
                    ),
                    "enqueued": int(report.get("enqueued_count") or 0),
                    "unit": "photos",
                },
            )
        else:
            report = {
                **preflight,
                "status": "deferred_unresolved",
                "backend_reload_required_before_resume": False,
                "checkpoint_auto_resumed": False,
            }
            pause = status.get("pipeline_pause") or {}
            if (
                isinstance(pause, dict)
                and Path(str(pause.get("current_dir") or "")).resolve()
                == staging_dir
            ):
                try:
                    _resume_exact_staging_after_zero_model_finalization(
                        args,
                        staging_dir,
                    )
                except (OSError, RuntimeError, ValueError) as exc:
                    _write_zero_model_activity(
                        args,
                        staging_dir,
                        active=False,
                        phase="checkpoint_resume_failed",
                        progress={
                            "processed": deferred,
                            "total": deferred,
                            "safe": 0,
                            "unresolved": int(
                                preflight.get("unresolved_count") or deferred
                            ),
                            "unit": "photos",
                        },
                        error=str(exc),
                    )
                    raise
                report["checkpoint_auto_resumed"] = True
            _write_zero_model_activity(
                args,
                staging_dir,
                active=False,
                phase="completed_no_safe_candidates",
                progress={
                    "processed": deferred,
                    "total": deferred,
                    "safe": 0,
                    "unresolved": int(
                        preflight.get("unresolved_count") or deferred
                    ),
                    "unit": "photos",
                },
            )

    records = load_group_records_from_disk(staging_dir)
    source_names = _source_names_from_disk(staging_dir)
    capped_names = _capped_names_from_disk(staging_dir)
    record_names = {
        str(record.get("file_name") or record.get("filename") or "")
        for record in records
    }
    if (
        not record_names.issubset(source_names)
        or not capped_names.issubset(source_names)
        or record_names & capped_names
        or record_names | capped_names != source_names
    ):
        raise RuntimeError(
            "disk attach refused: terminal and capped rows do not exactly partition source map"
        )
    return {
        "records": records,
        "record_names": record_names,
        "source_names": source_names,
        "capped_names": capped_names,
        "resolver_report": report,
    }


def wait_for_resume_start(
    backend_url: str,
    *,
    timeout_seconds: float = 30.0,
    poll_seconds: float = 0.25,
) -> dict[str, object]:
    """Wait until an accepted async start is observable or already complete.

    ``/api/start_batch`` returns before the worker thread necessarily publishes
    ``is_running=true``.  Treating that brief gap as an idle incomplete batch
    makes the continuity runner exit and retry forever.
    """
    deadline = time.monotonic() + max(1.0, timeout_seconds)
    last: dict[str, object] = {}
    while time.monotonic() < deadline:
        last = json_request(backend_url, "/api/status", timeout=30)
        stats = dict(last.get("stats") or {})
        processed, deferred, total = _completed_or_deferred_count(last)
        if bool(last.get("is_running") or stats.get("is_running")):
            return last
        if total > 0 and processed + deferred == total:
            return last
        time.sleep(max(0.01, poll_seconds))
    processed, deferred, total = _completed_or_deferred_count(last)
    raise RuntimeError(
        "resume refused: accepted start never became observable "
        f"processed={processed} deferred={deferred} total={total}"
    )


def attach_existing_group(args, rows: list[dict[str, object]], grouped: dict[tuple[str, str, str], list[dict[str, object]]]) -> dict[str, object]:
    """Finalize one already-running/already-finished staging group.

    This path deliberately has no staging, set_work_dir, or start_batch calls.
    """
    if len(grouped) != 1:
        raise RuntimeError(f"attach refused: input must resolve to exactly one group, got {len(grouped)}")
    (source_folder_text, audit_folder_text, period), group_rows = next(iter(grouped.items()))
    staging_root = Path(args.staging_root).resolve()
    status = json_request(args.backend_url, "/api/status", timeout=30)
    current_dir = _status_work_dir(status)
    try:
        current_dir.relative_to(staging_root)
    except ValueError as exc:
        raise RuntimeError(f"attach refused: current work directory is outside staging root: {current_dir}") from exc
    source_folder = Path(source_folder_text).resolve()
    audit_folder = Path(audit_folder_text).resolve()
    if not _staging_dir_matches_group(current_dir, period, source_folder, audit_folder):
        raise RuntimeError(f"attach refused: current staging group does not match input folder: {current_dir.name}")

    stats = status.get("stats") or {}
    if bool(status.get("is_running") or stats.get("is_running")):
        status = wait_for_folder_done(args.backend_url, current_dir, args.timeout_minutes, args.poll_seconds)
        stats = status.get("stats") or {}
    processed, deferred, total = _completed_or_deferred_count(status)
    if total <= 0 or processed + deferred != total:
        raise RuntimeError(
            "attach refused: incomplete staged work "
            f"processed={processed} deferred={deferred} total={total}"
        )

    finalized = finalize_capped_group(args, current_dir, status)
    rerun_records = list(finalized["records"])
    input_candidate_names = {
        Path(str(row.get("source_path") or "")).name for row in group_rows
    }
    candidate_names = set(finalized["source_names"])
    record_names = set(finalized["record_names"])
    capped_names = set(finalized["capped_names"])
    if (
        not input_candidate_names
        or not input_candidate_names.issubset(candidate_names)
        or not record_names.issubset(candidate_names)
        or not capped_names.issubset(candidate_names)
        or record_names & capped_names
        or record_names | capped_names != candidate_names
    ):
        raise RuntimeError(
            "attach refused: filenames do not exactly match staged group; "
            "terminal and capped rows must form one exact partition"
        )

    summary: dict[str, object] = {
        "folder": str(source_folder), "period": period, "audit_folder": audit_folder_text,
        "staging_dir": str(current_dir), "queued": len(group_rows), "staged": len(candidate_names),
        "processed": len(record_names), "deferred_capped": len(capped_names),
        "zero_model_resolved": int(
            dict(finalized["resolver_report"]).get("safe_count") or 0
        ),
        "aborted": 0, "abort_reason": "",
    }
    summary_path = Path(args.run_summary_csv)
    existing = read_dict_csv(summary_path)
    for row in existing:
        if row.get("staging_dir") == str(current_dir) and row.get("aborted") in {"0", "0.0", "False", "false"}:
            return dict(row)

    logs: list[str] = []
    rescued = rescue_followme_distant_records(rerun_records, record_names)
    demoted = demote_single_clue_distant_records(rerun_records, record_names)
    if rescued:
        summary["rescued_followme_distant"] = len(rescued)
    if demoted:
        summary["demoted_distant_single_clue"] = len(demoted)
    contained_conflicts = contained_structured_narration_conflicts(rerun_records, record_names)
    if contained_conflicts:
        summary["contained_review_conflicts"] = len(contained_conflicts)
        summary["contained_review_conflict_sample"] = ";".join(contained_conflicts[:5])
    abort_reason, details = abort_reason_for_rerun(args, rerun_records, record_names, logs)
    if abort_reason:
        raise RuntimeError(f"attach refused by quality guard: {abort_reason} {details}")
    original_rows = read_success_rows(Path(audit_folder_text) / "success_records.csv")
    merged_rows, updated, appended = merge_records(original_rows, rerun_records, record_names)
    summary.update(
        rebuild_outputs(
            args,
            source_folder,
            Path(audit_folder_text),
            period,
            merged_rows,
            record_names,
        )
    )
    summary.update({"updated": updated, "appended": appended})
    write_dict_csv(summary_path, existing + [summary], list(summary.keys()))
    if not args.keep_staging and not capped_names:
        shutil.rmtree(current_dir, ignore_errors=True)
    return summary


def split_groups_at_current_staging(
    status: dict[str, object],
    grouped: dict[tuple[str, str, str], list[dict[str, object]]],
    staging_root: str | Path,
) -> tuple[
    dict[tuple[str, str, str], list[dict[str, object]]],
    list[tuple[tuple[str, str, str], list[dict[str, object]]]],
]:
    """Select the active group and only the groups that follow it.

    Earlier groups are already finalized by the interrupted runner.  Matching
    uses the same period plus source-folder digest contract as staging creation,
    so a similarly named folder cannot make recovery skip unrelated work.
    """
    current_dir = _status_work_dir(status)
    staging_root_path = Path(staging_root).resolve()
    try:
        current_dir.relative_to(staging_root_path)
    except ValueError as exc:
        raise RuntimeError(f"resume refused: current work directory is outside staging root: {current_dir}") from exc

    items = list(grouped.items())
    matches: list[int] = []
    for index, ((source_folder_text, audit_folder_text, period), _rows) in enumerate(items):
        source_folder = Path(source_folder_text).resolve()
        audit_folder = Path(audit_folder_text).resolve()
        if _staging_dir_matches_group(current_dir, period, source_folder, audit_folder):
            matches.append(index)
    if len(matches) != 1:
        raise RuntimeError(
            f"resume refused: expected exactly one input group for current staging directory, got {len(matches)}"
        )
    active_index = matches[0]
    active_key, active_rows = items[active_index]
    return {active_key: active_rows}, items[active_index + 1 :]


def _staging_dir_matches_group(
    staging_dir: Path,
    period: str,
    source_folder: Path,
    audit_folder: Path,
) -> bool:
    """Match normal rerun stages and the older period-priority stage contract.

    Normal reruns end in the SHA-1 digest of the resolved source folder.  The
    202606 period-priority run predates that convention and ends in the first
    eight characters of the durable folder id embedded in its audit directory.
    Accept that legacy identity only when period and audit folder id both bind
    exactly; the later source-map partition check still protects every photo.
    """

    name = staging_dir.name
    if not name.startswith(f"{period}_"):
        return False
    source_digest = hashlib.sha1(str(source_folder.resolve()).encode("utf-8")).hexdigest()[:8]
    if name.endswith(f"_{source_digest}"):
        return True
    audit_name = audit_folder.name
    match = re.search(rf"(?:^|_){re.escape(period)}_([0-9a-fA-F]{{12,64}})(?:_|$)", audit_name)
    if not match:
        return False
    return name.endswith(f"_{match.group(1)[:8].lower()}")


def resume_existing_then_continue(
    args,
    grouped: dict[tuple[str, str, str], list[dict[str, object]]],
    stamp: str,
) -> list[dict[str, object]]:
    """Finalize the active group, skip prior groups, and run later groups."""
    status = json_request(args.backend_url, "/api/status", timeout=30)
    active_grouped, remaining_items = split_groups_at_current_staging(status, grouped, args.staging_root)
    active_rows = [row for group_rows in active_grouped.values() for row in group_rows]
    active_key = next(iter(active_grouped))
    stats = dict(status.get("stats") or {})
    processed, deferred, total = _completed_or_deferred_count(status)
    if (
        status.get("is_running") is False
        and total > 0
        and processed + deferred < total
    ):
        current_dir = _status_work_dir(status)
        response = json_request(
            args.backend_url,
            "/api/start_batch",
            {
                "dir": str(current_dir),
                "restart": False,
                "confirmed": True,
                "reprocess_last_n": 0,
            },
            timeout=30,
        )
        if str(response.get("status") or "") != "started":
            raise RuntimeError(f"resume refused: active incomplete staging did not start: {response}")
        wait_for_resume_start(args.backend_url)
        print(
            "[resume] continued incomplete active group "
            f"{current_dir} {processed}+{deferred}/{total}",
            flush=True,
        )
    original_keep_staging = bool(args.keep_staging)
    # Keep the active staging directory until the dashboard is moved back to
    # the original source folder.  Deleting it first creates a visible broken-
    # image window while the next group is being staged.
    args.keep_staging = True
    try:
        active_summary = attach_existing_group(args, active_rows, active_grouped)
    finally:
        args.keep_staging = original_keep_staging
    if not original_keep_staging:
        restore_backend_work_dir(args.backend_url, Path(active_key[0]))
        active_staging_text = str(active_summary.get("staging_dir") or "")
        if not active_staging_text:
            raise RuntimeError("resume refused cleanup: active summary has no staging directory")
        active_staging_dir = Path(active_staging_text).resolve()
        try:
            active_staging_dir.relative_to(Path(args.staging_root).resolve())
        except ValueError as exc:
            raise RuntimeError(
                f"resume refused cleanup: active staging directory is outside staging root: {active_staging_dir}"
            ) from exc
        if int(active_summary.get("deferred_capped") or 0) == 0:
            shutil.rmtree(active_staging_dir, ignore_errors=True)
    summaries = read_dict_csv(Path(args.run_summary_csv))
    active_dir = str(active_summary.get("staging_dir") or "")
    if not any(str(row.get("staging_dir") or "") == active_dir for row in summaries):
        summaries.append(active_summary)
        write_dict_csv(Path(args.run_summary_csv), summaries, list(active_summary.keys()))
    print(f"[resume] finalized active group {active_summary.get('staging_dir')}", flush=True)

    total_remaining = len(remaining_items)
    for index, ((source_folder, audit_folder, period), group_rows) in enumerate(remaining_items, start=1):
        if args.max_folders and index > args.max_folders:
            break
        if args.max_per_folder and len(group_rows) > args.max_per_folder:
            group_rows = group_rows[: args.max_per_folder]
        summary = run_group(args, source_folder, audit_folder, period, group_rows, index, total_remaining, stamp)
        summaries.append(summary)
        write_dict_csv(Path(args.run_summary_csv), summaries, list(summary.keys()))
    return summaries


def main() -> int:
    parser = argparse.ArgumentParser(description="Rerun only candidate images through staging folders and merge results.")
    parser.add_argument("--source-root", default=str(DEFAULT_SOURCE_ROOT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--backend-url", default=DEFAULT_BACKEND_URL)
    parser.add_argument("--input-csv", required=True)
    parser.add_argument("--output-csv", default="")
    parser.add_argument("--run-summary-csv", default="")
    parser.add_argument("--staging-root", default="")
    parser.add_argument("--reason-contains", action="append", default=[])
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--attach-existing", action="store_true", help="Finalize the one API-reported existing staging group without starting or switching work.")
    parser.add_argument(
        "--resume-existing-then-continue",
        action="store_true",
        help="Finalize the API-reported active group, skip prior groups, then execute only later input groups.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-staging", action="store_true")
    parser.add_argument(
        "--keep-flat-output-backup",
        action="store_true",
        help="保留上一輪 flat output 照片備份；預設直接刪除舊輸出以避免大量重跑塞滿硬碟。",
    )
    parser.add_argument("--max-folders", type=int, default=0)
    parser.add_argument("--max-per-folder", type=int, default=0)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--timeout-minutes", type=int, default=720)
    parser.add_argument("--min-completion-ratio", type=float, default=0.98)
    parser.add_argument("--min-quality-guard-records", type=int, default=20)
    parser.add_argument("--max-single-missing-ratio", type=float, default=0.65)
    parser.add_argument("--price-symbol", default=FULLWIDTH_DOLLAR, choices=[FULLWIDTH_DOLLAR, "$", ""])
    args = parser.parse_args()

    source_root = Path(args.source_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    audit_dir = output_dir / "_ocr_audit"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    args.output_dir = str(output_dir)
    if not args.staging_root:
        args.staging_root = str(output_dir / "_ocr_staging")
    if not args.output_csv:
        args.output_csv = str(audit_dir / f"staged_rerun_candidates_{stamp}.csv")
    if not args.run_summary_csv:
        args.run_summary_csv = str(audit_dir / f"staged_rerun_summary_{stamp}.csv")

    rows = read_dict_csv(Path(args.input_csv))
    grouped, skipped = group_candidates(rows, source_root, args.reason_contains)
    selected_rows = [row for group in grouped.values() for row in group]
    headers = [
        "period",
        "audit_folder",
        "source_folder",
        "source_path",
        "file_name",
        "reason",
        "view_type",
        "category",
        "model",
        "price",
        "price_status",
    ]
    write_dict_csv(Path(args.output_csv), selected_rows, headers)
    print(f"[scan] selected={len(selected_rows)} groups={len(grouped)} skipped={skipped} csv={args.output_csv}", flush=True)
    if args.attach_existing and args.resume_existing_then_continue:
        parser.error("--attach-existing and --resume-existing-then-continue are mutually exclusive")
    if args.resume_existing_then_continue:
        if not args.execute:
            parser.error("--resume-existing-then-continue requires --execute")
        try:
            resume_existing_then_continue(args, grouped, stamp)
            return 0
        except Exception as exc:
            print(f"[resume-refused] {exc}", flush=True)
            return 2
    if args.attach_existing:
        try:
            summary = attach_existing_group(args, selected_rows, grouped)
            write_dict_csv(Path(args.run_summary_csv), [summary], list(summary.keys())) if not Path(args.run_summary_csv).exists() else None
            print(f"[attach] finalized {summary.get('staging_dir')}", flush=True)
            return 0
        except Exception as exc:
            print(f"[attach-refused] {exc}", flush=True)
            return 2
    if not args.execute:
        print("[scan] scan only; add --execute to rerun.", flush=True)
        return 0

    summaries: list[dict[str, object]] = []
    items = list(grouped.items())
    for index, ((source_folder, audit_folder, period), group_rows) in enumerate(items, start=1):
        if args.max_folders and index > args.max_folders:
            break
        if args.max_per_folder and len(group_rows) > args.max_per_folder:
            group_rows = group_rows[: args.max_per_folder]
        summary = run_group(args, source_folder, audit_folder, period, group_rows, index, len(items), stamp)
        summaries.append(summary)
        write_dict_csv(Path(args.run_summary_csv), summaries, list(summary.keys()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

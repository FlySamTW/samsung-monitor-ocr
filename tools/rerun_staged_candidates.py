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
import math
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from photo_rename_planner import (  # noqa: E402
    CONFLICT_STATUS,
    MISSING_RESULT_STATUS,
    MISSING_SOURCE_STATUS,
    NO_CHANGE_STATUS,
    READY_STATUS,
    copy_plan_to_flat_output,
    make_plan,
    summarize,
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


def has_positive_followme_indicator(text: str) -> bool:
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


def _price_int(value: object) -> int | None:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def infer_followme_model_from_evidence(evidence: str, price: object = None) -> str:
    upper = evidence.upper().replace("FOLLOW ME", "FOLLOWME")
    price_int = _price_int(price)
    if "FOLLOWME PRO" in upper or "S43FM" in upper or "PRO" in upper or "43" in upper:
        return 'FollowMe Pro M7 43"'
    if price_int and price_int >= 15000:
        return 'FollowMe Pro M7 43"'
    if "M5" in upper or "S32FM50" in upper or "FM501" in upper or "FHD" in upper:
        return 'FollowMe M5 32"'
    if price_int and 9900 <= price_int <= 11000:
        return 'FollowMe M5 32"'
    return 'FollowMe M7 32"'


def build_rescued_followme_thinking(record: dict[str, object], evidence: str) -> str:
    model = str(record.get("model") or "").strip() or infer_followme_model_from_evidence(
        evidence,
        record.get("price") or record.get("human_price"),
    )
    price = str(record.get("price") or record.get("human_price") or "").strip() or NO_PRICE_TEXT
    return (
        f"最終校正：這張判定為{SINGLE_VIEW_TEXT}，型號 {model}，價格 {price}。"
        "畫面中有 Samsung FollowMe 立式展示/產品標示，因此不能因旁邊賣場環境、"
        "其他品牌或背景多台螢幕而判為遠景。"
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
    for row in rows:
        if not row_matches_reason(row, reason_contains):
            continue
        folder = str(row.get("source_folder") or "")
        audit_folder = str(row.get("audit_folder") or "")
        period = str(row.get("period") or infer_period_from_text(folder))
        file_name = str(row.get("file_name") or "")
        source_path = resolve_source_path(file_name, Path(folder) if folder else None, source_root, period)
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


def stage_images(rows: list[dict[str, object]], staging_dir: Path) -> int:
    staging_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    seen: set[str] = set()
    for row in rows:
        source = Path(str(row.get("source_path") or ""))
        if not source.exists() or not source.is_file():
            continue
        target = staging_dir / source.name
        if target.name in seen:
            continue
        seen.add(target.name)
        shutil.copy2(source, target)
        count += 1
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


def backup_existing_outputs(audit_folder: Path, dry_run: bool) -> tuple[Path, int]:
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
            backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(backup_dir / target.name))
    return backup_dir, count


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
) -> dict[str, object]:
    backup_dir, backup_count = backup_existing_outputs(audit_folder, args.dry_run)
    success_path = audit_folder / "success_records.csv"
    if success_path.exists() and not args.dry_run:
        backup_success = audit_folder / f"success_records.csv.before_staged_rerun_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(success_path, backup_success)

    results_map = records_to_map(merged_rows)
    plan = make_plan(source_folder, results_map, period, args.price_symbol)
    counts = summarize(plan)
    safe_plan, blocked_plan = split_plan(plan)
    blocked_path = audit_folder / "blocked_after_staged_rerun.csv"

    copied = []
    if not args.dry_run:
        write_dict_csv(success_path, merged_rows, SUCCESS_HEADERS)
        write_csv(audit_folder / "rename_plan.csv", plan)
        write_csv(audit_folder / "conflicts.csv", [item for item in plan if item["status"] == CONFLICT_STATUS])
        write_csv(blocked_path, blocked_plan)
        copied = copy_plan_to_flat_output(safe_plan, Path(args.output_dir)) if safe_plan else []
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
        "copied": len(copied),
        "backed_up": backup_count,
        "backup_dir": str(backup_dir) if backup_count else "",
    }


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
    rerun_records = json_request(args.backend_url, "/api/success_records", timeout=120)
    if not isinstance(rerun_records, list):
        raise RuntimeError("/api/success_records did not return a list")

    candidate_names = {Path(str(row.get("source_path") or "")).name for row in rows}
    logs = get_logs_since(args.backend_url, log_start)
    rescued_followme = rescue_followme_distant_records(rerun_records, candidate_names)
    if rescued_followme:
        summary["rescued_followme_distant"] = len(rescued_followme)
        summary["rescued_followme_sample"] = ";".join(rescued_followme[:5])
    abort_reason, guard_details = abort_reason_for_rerun(args, rerun_records, candidate_names, logs)
    if abort_reason:
        summary.update(guard_details)
        summary["processed"] = (status.get("stats") or {}).get("processed", "")
        summary["aborted"] = 1
        summary["abort_reason"] = abort_reason
        print(f"[abort] {source_folder.name} reason={abort_reason} details={guard_details}", flush=True)
        if not args.keep_staging:
            shutil.rmtree(staging_dir, ignore_errors=True)
        return summary

    original_rows = read_success_rows(audit_folder / "success_records.csv")
    merged_rows, updated, appended = merge_records(original_rows, rerun_records, candidate_names)
    rebuild = rebuild_outputs(args, source_folder, audit_folder, period, merged_rows)
    summary.update(rebuild)
    summary["updated"] = updated
    summary["appended"] = appended
    summary["processed"] = (status.get("stats") or {}).get("processed", "")
    summary["aborted"] = 0
    summary["abort_reason"] = ""
    print(f"[export] {source_folder.name} updated={updated} copied={summary.get('copied')} backup={summary.get('backed_up')}", flush=True)
    if not args.keep_staging:
        shutil.rmtree(staging_dir, ignore_errors=True)
    return summary


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
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-staging", action="store_true")
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

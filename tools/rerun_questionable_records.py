#!/usr/bin/env python3
"""Rerun risky OCR records and rebuild flat output files.

Targets records that are likely to produce incomplete or wrong filenames:
far/distant view, missing model, or missing price. The script groups candidates
by source folder, queues reruns through the dashboard backend, waits for each
folder to finish, snapshots success_records.csv, rebuilds rename_plan.csv, and
copies resized flat-output files to the configured output directory.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
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


FAR = "\u9060\u666f"
UNKNOWN_MODEL_TEXT = "\u578b\u865f\u672a\u8fa8\u8b58"
NO_MODEL_TEXT = "\u7121\u578b\u865f"
NO_PRICE_TEXT = "\u7121\u50f9\u683c"
FULLWIDTH_DOLLAR = "\uff04"
DEFAULT_SOURCE_ROOT = Path("D:/00_\u5546\u5316/00_\u672a\u6574\u7406\u5546\u5316\u7167\u7247")
DEFAULT_OUTPUT_DIR = Path("D:/00_\u5546\u5316/00_\u5df2OCR\u7167\u7247")
DEFAULT_BACKEND_URL = "http://127.0.0.1:5000"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
UNKNOWN_VALUES = {
    "",
    "null",
    "none",
    "nan",
    UNKNOWN_MODEL_TEXT,
    NO_MODEL_TEXT,
    "unknown",
    "(%s)" % NO_MODEL_TEXT,
    "?",
    "\uff1f",
}
TRUTHY = {"1", "true", "yes", "y"}
SUCCESS_HEADERS = [
    "timestamp",
    "started_at",
    "completed_at",
    "file_name",
    "source_path",
    "original_source_path",
    "source_item_id",
    "period",
    "category",
    "view_type",
    "screen_status",
    "quality_issue",
    "model",
    "price",
    "price_status",
    "price_symbol",
    "official_price",
    "price_diff_percent",
    "duration",
    "run_id",
    "model_id",
    "evidence_contract_version",
    "evidence_contract_valid",
    "evidence_contract_errors",
    "complete_screen_count",
    "unique_main",
    "label_ownership",
    "followme_physical_evidence",
    "normalized_evidence",
    "review_status",
    "human_category",
    "human_model",
    "human_price",
    "human_notes",
    "ocr_attempt",
    "auto_retry_reasons",
    "auto_verified",
    "auto_review_required",
    "model_validation_failed",
    "rejected_model",
    "price_conflict_detected",
    "thinking",
]


def read_dict_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_dict_csv(path: Path, rows: list[dict[str, object]], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            values = {}
            for header in headers:
                value = row.get(header, "")
                values[header] = json.dumps(value, ensure_ascii=False, separators=(",", ":")) if isinstance(value, (dict, list)) else value
            writer.writerow(values)


def json_request(base_url: str, path: str, payload: dict | None = None, timeout: int = 30):
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    data = None
    headers = {"Accept": "application/json"}
    method = "GET"
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
        method = "POST"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def norm(value: object) -> str:
    return str(value or "").strip()


def is_missing_model(value: object) -> bool:
    text = norm(value)
    lower = text.lower()
    return lower in UNKNOWN_VALUES or UNKNOWN_MODEL_TEXT in text or text in {"?", "\uff1f"}


def is_missing_price(value: object) -> bool:
    return not any(ch.isdigit() for ch in norm(value))


def is_distant(row: dict[str, str]) -> bool:
    text = " ".join(norm(row.get(key)) for key in ["view_type", "category", "human_category"])
    return FAR in text

def is_complete_auto_verified(row: dict[str, str]) -> bool:
    """Only v19.45 contract rows may be terminal for current-year reruns."""
    if norm(row.get("auto_review_required")).lower() in TRUTHY:
        return False
    if norm(row.get("auto_verified")).lower() not in TRUTHY:
        return False
    required_attempts = 3 if is_distant(row) else (2 if "FOLLOWME" in norm(row.get("model")).upper().replace(" ", "") else 1)
    try:
        if int(norm(row.get("ocr_attempt")) or "0") < required_attempts:
            return False
    except ValueError:
        return False
    period = norm(row.get("period") or row.get("file_name") or row.get("source_path"))
    if "2026" in period and norm(row.get("evidence_contract_version")) != "v19.45":
        return False
    if "2026" in period and norm(row.get("evidence_contract_valid")).lower() not in TRUTHY:
        return False
    evidence = " ".join(norm(row.get(key)) for key in ("thinking", "stream_buffer", "raw_response"))
    return bool(evidence) and bool(norm(row.get("run_id")) or norm(row.get("timestamp")))

def is_promo_followme_card(row: dict[str, str]) -> bool:
    text = " ".join(norm(row.get(key)) for key in ("thinking", "stream_buffer", "raw_response", "model"))
    upper = text.upper().replace(" ", "")
    promo = any(token in upper for token in ("PROMO", "PROMOTIONAL", "ADVERTISEMENT", "廣告", "促銷", "宣傳"))
    negative = any(token in upper for token in ("NOTFOLLOWME", "NOTAFOLLOWME", "不是FOLLOWME", "非FOLLOWME"))
    physical = "NOPHYSICAL" not in upper and any(token in upper for token in ("STAND", "BASE", "TRAY", "支架", "底座", "托盤", "託盤"))
    return "FOLLOWME" in upper and promo and negative and not physical


def reason_for(row: dict[str, str]) -> list[str]:
    reasons: list[str] = []
    if is_complete_auto_verified(row):
        return []
    model = row.get("human_model") or row.get("model")
    price = row.get("human_price") or row.get("price")
    distant = is_distant(row)
    if distant:
        reasons.append(FAR)
    if is_missing_model(model):
        reasons.append(NO_MODEL_TEXT)
    if not distant and is_missing_price(price):
        reasons.append(NO_PRICE_TEXT)
    if is_promo_followme_card(row):
        reasons.append("FollowMe promotional card without physical FollowMe evidence")
    return reasons


def infer_period_from_text(text: str) -> str:
    match = re.search(r"(20\d{4})", text)
    if match:
        return match.group(1)
    match = re.search(r"(20\d{2})", text)
    return match.group(1) if match else ""


def audit_folder_source_folder(audit_folder: Path, source_root: Path) -> Path | None:
    for row in read_dict_csv(audit_folder / "copied.csv"):
        source = row.get("original_path") or row.get("source_path") or ""
        if source:
            folder = Path(source).parent
            if folder.exists():
                return folder

    success_rows = read_dict_csv(audit_folder / "success_records.csv")
    names = {row.get("file_name") for row in success_rows if row.get("file_name")}
    if not names:
        return None

    best: tuple[int, Path] | None = None
    for folder in source_root.rglob("*"):
        if not folder.is_dir():
            continue
        try:
            folder_names = {
                path.name
                for path in folder.iterdir()
                if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
            }
        except OSError:
            continue
        score = len(names & folder_names)
        if score and (best is None or score > best[0]):
            best = (score, folder)
    return best[1] if best else None


def resolve_source_path(file_name: str, preferred_folder: Path | None, source_root: Path, period: str = "") -> Path | None:
    if not file_name:
        return None

    if preferred_folder:
        preferred_path = preferred_folder / file_name
        if preferred_path.exists():
            return preferred_path

    matches: list[Path] = []
    try:
        matches = [path for path in source_root.rglob(file_name) if path.is_file()]
    except OSError:
        matches = []
    if not matches:
        return None

    if period:
        period_matches = [path for path in matches if period in str(path)]
        if period_matches:
            return sorted(period_matches, key=lambda path: str(path), reverse=True)[0]
        return None

    return sorted(matches, key=lambda path: str(path), reverse=True)[0]


def collect_candidates(audit_dir: Path, source_root: Path, include_older: bool) -> list[dict[str, object]]:
    current_year = datetime.now().year
    rows: list[dict[str, object]] = []
    for success_csv in sorted(audit_dir.glob("*/success_records.csv")):
        audit_folder = success_csv.parent
        source_folder = audit_folder_source_folder(audit_folder, source_root)
        period = (
            infer_period_from_text(audit_folder.name)
            or infer_period_from_text(str(source_folder or ""))
            or infer_period_from_text(str(success_csv))
        )
        if not include_older:
            if not period:
                continue
            if int(period[:4]) < current_year:
                continue
        for record in read_dict_csv(success_csv):
            reasons = reason_for(record)
            file_name = record.get("file_name") or record.get("filename") or ""
            if not reasons or not file_name:
                continue
            source_path = resolve_source_path(file_name, source_folder, source_root, period)
            rows.append(
                {
                    "period": period,
                    "audit_folder": str(audit_folder),
                    "source_folder": str(source_path.parent if source_path else source_folder or ""),
                    "source_path": str(source_path or ""),
                    "file_name": file_name,
                    "reason": "+".join(reasons),
                    "view_type": record.get("view_type", ""),
                    "category": record.get("category", ""),
                    "model": record.get("model", ""),
                    "price": record.get("price", ""),
                    "price_status": record.get("price_status", ""),
                }
            )
    return rows


def records_to_map(records: list[dict[str, object]]) -> dict[str, dict[str, str]]:
    mapped: dict[str, dict[str, str]] = {}
    for record in records:
        file_name = norm(record.get("file_name") or record.get("filename"))
        if not file_name:
            continue
        mapped[file_name] = {key: "" if value is None else str(value) for key, value in record.items()}
        mapped[file_name]["file_name"] = file_name
    return mapped


def wait_for_folder_done(base_url: str, folder: Path, timeout_minutes: int, poll_seconds: int) -> dict:
    deadline = time.time() + timeout_minutes * 60
    last_line = 0.0
    stable_done_polls = 0
    last_done_tuple = None
    while time.time() < deadline:
        status = json_request(base_url, "/api/status", timeout=30)
        stats = status.get("stats") or {}
        running = bool(status.get("is_running") or stats.get("is_running"))
        processed = int(stats.get("processed") or 0)
        total = int(stats.get("total") or 0)
        success = int(stats.get("success") or 0)
        failed = int(stats.get("failed") or 0)
        now = time.time()
        if now - last_line >= poll_seconds:
            print(
                "[wait] {name} processed={processed}/{total} success={success} failed={failed} running={running}".format(
                    name=folder.name,
                    processed=processed,
                    total=total,
                    success=success,
                    failed=failed,
                    running=running,
                ),
                flush=True,
            )
            last_line = now
        if not running and total > 0:
            return status
        done_tuple = (processed, success, failed, total)
        if total > 0 and processed >= total:
            if done_tuple == last_done_tuple:
                stable_done_polls += 1
            else:
                stable_done_polls = 1
                last_done_tuple = done_tuple
            if stable_done_polls >= 3:
                print(f"[wait] {folder.name} appears complete but backend still running; stopping to export.", flush=True)
                try:
                    json_request(base_url, "/api/stop", payload={}, timeout=10)
                except Exception as exc:
                    print(f"[warn] stop after stable completion failed: {exc}", flush=True)
                time.sleep(2)
                return json_request(base_url, "/api/status", timeout=30)
        else:
            stable_done_polls = 0
            last_done_tuple = done_tuple
        time.sleep(max(1, poll_seconds))
    raise TimeoutError(f"folder timed out: {folder}")


def backup_previous_copied(audit_folder: Path, dry_run: bool) -> tuple[Path, int]:
    copied_rows = read_dict_csv(audit_folder / "copied.csv")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = audit_folder / f"flat_output_backup_before_questionable_rerun_{stamp}"
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


def split_plan_for_partial_copy(plan: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    safe_statuses = {READY_STATUS, NO_CHANGE_STATUS}
    safe_rows = [row for row in plan if row.get("status") in safe_statuses]
    blocked_rows = [row for row in plan if row.get("status") not in safe_statuses]
    return safe_rows, blocked_rows


def export_folder_outputs(args, folder: Path, audit_folder: Path, period: str) -> dict[str, object]:
    records = json_request(args.backend_url, "/api/success_records", timeout=120)
    if not isinstance(records, list):
        raise RuntimeError("/api/success_records did not return a list")

    backup_dir, backup_count = backup_previous_copied(audit_folder, args.dry_run)
    success_path = audit_folder / "success_records.csv"
    backup_success = audit_folder / f"success_records.csv.before_questionable_rerun_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    if success_path.exists() and not args.dry_run:
        shutil.copy2(success_path, backup_success)

    results_map = records_to_map(records)
    plan = make_plan(folder, results_map, period, args.price_symbol)
    counts = summarize(plan)
    safe_plan, blocked_plan = split_plan_for_partial_copy(plan)
    blocked_path = audit_folder / "blocked_after_rerun.csv"

    if not args.dry_run:
        write_dict_csv(success_path, records, SUCCESS_HEADERS)
        write_csv(audit_folder / "rename_plan.csv", plan)
        write_csv(audit_folder / "conflicts.csv", [item for item in plan if item["status"] == CONFLICT_STATUS])
        write_csv(blocked_path, blocked_plan)
        if blocked_plan:
            print(
                f"[warn] {folder.name} blocked_rows={len(blocked_plan)} written={blocked_path}; copying safe rows only.",
                flush=True,
            )
        copied = copy_plan_to_flat_output(safe_plan, Path(args.output_dir)) if safe_plan else []
        write_csv(audit_folder / "copied.csv", copied)
    else:
        copied = []

    return {
        "folder": str(folder),
        "period": period,
        "records": len(records),
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


def execute_candidates(args, candidates: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str], list[dict[str, object]]] = {}
    skipped_missing_source = 0
    for row in candidates:
        folder = str(row.get("source_folder") or "")
        audit_folder = str(row.get("audit_folder") or "")
        period = str(row.get("period") or infer_period_from_text(folder))
        file_name = str(row.get("file_name") or "")
        source_path = resolve_source_path(file_name, Path(folder) if folder else None, Path(args.source_root).resolve(), period)
        if not source_path:
            skipped_missing_source += 1
            continue
        row["source_path"] = str(source_path)
        folder = str(source_path.parent)
        if folder and audit_folder:
            grouped.setdefault((folder, audit_folder, period), []).append(row)

    if skipped_missing_source:
        print(f"[warn] skipped_missing_source={skipped_missing_source}", flush=True)

    if args.stop_current:
        try:
            json_request(args.backend_url, "/api/stop", payload={}, timeout=10)
            time.sleep(3)
        except Exception as exc:
            print(f"[warn] stop current failed/ignored: {exc}", flush=True)

    summaries: list[dict[str, object]] = []
    for index, ((folder_text, audit_folder_text, period), rows) in enumerate(grouped.items(), start=1):
        folder = Path(folder_text)
        audit_folder = Path(audit_folder_text)
        if args.max_folders and index > args.max_folders:
            break
        if args.max_per_folder and len(rows) > args.max_per_folder:
            rows = rows[: args.max_per_folder]
        if not folder.exists():
            print(f"[skip] missing source folder: {folder}", flush=True)
            continue

        print(f"[folder] {index}/{len(grouped)} {folder} candidates={len(rows)}", flush=True)
        json_request(args.backend_url, "/api/set_work_dir", {"dir": str(folder)}, timeout=30)
        queued = 0
        for row in rows:
            try:
                json_request(args.backend_url, "/api/rerun", {"filename": row["file_name"]}, timeout=30)
                queued += 1
                time.sleep(args.queue_delay_seconds)
            except Exception as exc:
                print(f"[warn] queue failed {row.get('file_name')}: {exc}", flush=True)
        if not queued:
            continue

        try:
            response = json_request(
                args.backend_url,
                "/api/start_batch",
                {"dir": str(folder), "restart": False, "confirmed": True, "reprocess_last_n": 0},
                timeout=30,
            )
            response_status = response.get("status")
        except urllib.error.HTTPError as exc:
            if exc.code != 400:
                raise
            response_status = "already_running"
        print(f"[start] {folder.name} queued={queued} response={response_status}", flush=True)
        status = wait_for_folder_done(args.backend_url, folder, args.timeout_minutes, args.poll_seconds)
        summary = export_folder_outputs(args, folder, audit_folder, period)
        summary["queued"] = queued
        summary["processed"] = (status.get("stats") or {}).get("processed", "")
        summaries.append(summary)
        write_dict_csv(Path(args.run_summary_csv), summaries, list(summary.keys()))
        print(f"[export] {folder.name} copied={summary['copied']} backup={summary['backed_up']}", flush=True)
    return summaries


def main() -> int:
    parser = argparse.ArgumentParser(description="Rerun risky OCR rows and rebuild flat output.")
    parser.add_argument("--source-root", default=str(DEFAULT_SOURCE_ROOT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--audit-dir", default="")
    parser.add_argument("--backend-url", default=DEFAULT_BACKEND_URL)
    parser.add_argument("--input-csv", default="", help="Use a prefiltered candidate CSV instead of scanning audit folders.")
    parser.add_argument("--output-csv", default="")
    parser.add_argument("--run-summary-csv", default="")
    parser.add_argument("--include-older", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--stop-current", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-folders", type=int, default=0)
    parser.add_argument("--max-per-folder", type=int, default=0)
    parser.add_argument("--queue-delay-seconds", type=float, default=0.03)
    parser.add_argument("--poll-seconds", type=int, default=20)
    parser.add_argument("--timeout-minutes", type=int, default=360)
    parser.add_argument("--price-symbol", default=FULLWIDTH_DOLLAR, choices=[FULLWIDTH_DOLLAR, "$", ""])
    args = parser.parse_args()

    source_root = Path(args.source_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    audit_dir = Path(args.audit_dir).resolve() if args.audit_dir else output_dir / "_ocr_audit"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if not args.output_csv:
        args.output_csv = str(audit_dir / f"questionable_rerun_candidates_{stamp}.csv")
    if not args.run_summary_csv:
        args.run_summary_csv = str(audit_dir / f"questionable_rerun_summary_{stamp}.csv")
    args.output_dir = str(output_dir)

    if args.input_csv:
        candidates = read_dict_csv(Path(args.input_csv))
    else:
        candidates = collect_candidates(audit_dir, source_root, include_older=args.include_older)
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
    write_dict_csv(Path(args.output_csv), candidates, headers)
    print(f"[scan] candidates={len(candidates)} csv={args.output_csv}", flush=True)

    by_reason: dict[str, int] = {}
    for row in candidates:
        by_reason[str(row["reason"])] = by_reason.get(str(row["reason"]), 0) + 1
    for reason, count in sorted(by_reason.items(), key=lambda item: (-item[1], item[0])):
        print(f"[scan] {reason}: {count}", flush=True)

    if args.execute:
        execute_candidates(args, candidates)
    else:
        print("[scan] scan only; add --execute to rerun.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

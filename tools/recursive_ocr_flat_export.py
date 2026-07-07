import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from photo_rename_planner import (
    CONFLICT_STATUS,
    IMAGE_EXTENSIONS,
    NO_CHANGE_STATUS,
    READY_STATUS,
    copy_plan_to_flat_output,
    make_plan,
    summarize,
    write_csv,
)


UNSUPPORTED_EXTENSIONS = {".heic", ".heif", ".webp"}
DEFAULT_BACKEND_URL = "http://127.0.0.1:5000"
DEFAULT_API_BASE = "http://127.0.0.1:1234/v1"
DEFAULT_MODEL = "qwen/qwen3-vl-8b"


def json_request(base_url: str, path: str, payload: Optional[dict] = None, timeout: int = 30):
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


def write_dict_csv(path: Path, rows: List[Dict[str, object]], headers: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})


def read_dict_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def int_value(value: object, default: int = 0) -> int:
    try:
        return int(str(value or "").strip())
    except ValueError:
        return default


def iso_from_mtime(mtime: float) -> str:
    return datetime.fromtimestamp(mtime).isoformat()


def path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_source_output_paths(source_root: Path, output_dir: Path) -> None:
    if output_dir == source_root:
        raise SystemExit(
            "輸出資料夾不可等於來源根資料夾；請指定新的單一輸出資料夾，例如：來源資料夾_OCR整理。"
        )
    if path_is_relative_to(output_dir, source_root):
        raise SystemExit(
            "輸出資料夾不可放在來源根資料夾底下，避免重跑時掃到自己輸出的改名照片；"
            "請改用來源資料夾旁邊的新資料夾，例如：來源資料夾_OCR整理。"
        )
    if path_is_relative_to(source_root, output_dir):
        raise SystemExit(
            "輸出資料夾不可是來源根資料夾的上層資料夾，避免把無關照片與審計檔算進輸出；"
            "請指定來源資料夾旁邊的新資料夾，例如：來源資料夾_OCR整理。"
        )


def copied_manifest_complete(copied_path: Path, expected_count: int) -> bool:
    rows = read_dict_csv(copied_path)
    if expected_count and len(rows) != expected_count:
        return False
    if not rows:
        return False
    for row in rows:
        target_path = row.get("target_path") or ""
        if not target_path or not Path(target_path).exists():
            return False
    return True


def build_resume_index(summary_path: Path) -> Dict[str, Dict[str, str]]:
    resume: Dict[str, Dict[str, str]] = {}
    for row in read_dict_csv(summary_path):
        folder = row.get("folder") or ""
        if not folder:
            continue
        status = row.get("status")
        if status in {"blocked", "skipped_blocked"}:
            resume[folder] = row
            continue
        if status not in {"copied", "skipped_existing"}:
            continue
        if any(int_value(row.get(key)) for key in ["missing_result", "missing_source", "conflict"]):
            continue
        copied_path_text = row.get("copied_path") or ""
        if not copied_path_text:
            continue
        copied_path = Path(copied_path_text)
        copied_count = int_value(row.get("copied_count"))
        if not copied_path or not copied_manifest_complete(copied_path, copied_count):
            continue
        resume[folder] = row
    return resume


def summary_from_resume(row: Dict[str, str], current: dict) -> Dict[str, object]:
    summary = dict(row)
    previous_status = row.get("status") or "skipped_existing"
    summary.update(
        {
            "folder": str(current["folder"]),
            "period": current["period"],
            "image_count": current["image_count"],
            "source_latest_mtime": iso_from_mtime(current["latest_mtime"]),
            "status": "skipped_blocked" if previous_status == "blocked" else "skipped_existing",
            "copy_error": row.get("copy_error", "") if previous_status == "blocked" else "",
            "start_response": "resume_skip_blocked" if previous_status == "blocked" else "resume_skip_existing",
        }
    )
    return summary


def resume_row_matches_current(row: Dict[str, str], current: dict) -> bool:
    if int_value(row.get("image_count"), default=-1) != int(current["image_count"]):
        return False
    previous_mtime = row.get("source_latest_mtime") or ""
    if previous_mtime and previous_mtime != iso_from_mtime(current["latest_mtime"]):
        return False
    return True


def find_period_in_text(text: str) -> str:
    month_matches = re.findall(r"20\d{4}", text)
    if month_matches:
        return month_matches[-1]
    year_matches = re.findall(r"20\d{2}", text)
    if year_matches:
        return year_matches[-1]
    return ""


def infer_period(folder: Path, images: List[Path]) -> str:
    for part in reversed(folder.parts):
        period = find_period_in_text(part)
        if period:
            return period
    if images:
        latest = max(path.stat().st_mtime for path in images)
        return datetime.fromtimestamp(latest).strftime("%Y%m")
    return datetime.now().strftime("%Y%m")


def folder_token(index: int, source_root: Path, folder: Path, period: str) -> str:
    try:
        rel = str(folder.relative_to(source_root))
    except ValueError:
        rel = folder.name
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", rel).strip("_") or folder.name
    digest = hashlib.sha1(str(folder).encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"{index:04d}_{period}_{text[:80]}_{digest}"


def discover_folders(source_root: Path):
    folders = []
    unsupported = []
    for current, _, filenames in os.walk(source_root):
        folder = Path(current)
        supported_images = []
        for name in filenames:
            path = folder / name
            suffix = path.suffix.lower()
            if suffix in IMAGE_EXTENSIONS:
                supported_images.append(path)
            elif suffix in UNSUPPORTED_EXTENSIONS:
                unsupported.append(
                    {
                        "folder": str(folder),
                        "path": str(path),
                        "extension": suffix,
                        "reason": "目前規格不處理 HEIC/WebP",
                    }
                )
        if supported_images:
            latest_mtime = max(path.stat().st_mtime for path in supported_images)
            period = infer_period(folder, supported_images)
            folders.append(
                {
                    "folder": folder,
                    "period": period,
                    "image_count": len(supported_images),
                    "latest_mtime": latest_mtime,
                }
            )

    def sort_key(row):
        period = row["period"]
        period_key = int(period) if re.fullmatch(r"20\d{4}", period) else 0
        return period_key, row["latest_mtime"], str(row["folder"])

    folders.sort(key=sort_key, reverse=True)
    return folders, unsupported


def records_to_map(records: Iterable[dict]) -> Dict[str, Dict[str, str]]:
    results: Dict[str, Dict[str, str]] = {}
    for record in records:
        file_name = (record.get("file_name") or record.get("filename") or "").strip()
        if not file_name:
            continue
        row = {key: "" if value is None else str(value) for key, value in record.items()}
        row["file_name"] = file_name
        results[file_name] = row
    return results


def split_plan_for_partial_copy(plan: List[Dict[str, str]]) -> tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    safe_statuses = {READY_STATUS, NO_CHANGE_STATUS}
    safe_rows = [row for row in plan if row.get("status") in safe_statuses]
    blocked_rows = [row for row in plan if row.get("status") not in safe_statuses]
    return safe_rows, blocked_rows


def price_digits(row: dict) -> str:
    value = row.get("human_price") or row.get("price") or ""
    return "".join(ch for ch in str(value) if ch.isdigit())


def is_current_or_future_period(period: str) -> bool:
    match = re.search(r"(20\d{2})", str(period or ""))
    if not match:
        return True
    return int(match.group(1)) >= datetime.now().year


def current_year_unknown_price_records(period: str, records: List[dict]) -> List[dict]:
    if not is_current_or_future_period(period):
        return []
    return [
        record
        for record in records
        if price_digits(record) and str(record.get("price_status") or "") == "unknown"
    ]


def write_results_snapshot(path: Path, records: List[dict]) -> None:
    headers = [
        "timestamp",
        "file_name",
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
        "review_status",
        "human_category",
        "human_model",
        "human_price",
    ]
    write_dict_csv(path, records, headers)


def ensure_local_llm(args) -> None:
    command = [
        sys.executable,
        str(Path(__file__).resolve().parent / "local_llm_manager.py"),
        "ensure",
        "--api-base",
        args.api_base,
        "--model",
        args.model,
    ]
    completed = subprocess.run(command, text=True, encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        raise RuntimeError("本機 LM Studio LLM 啟動失敗，請先確認 LM Studio CLI 與模型。")


def wait_for_backend(base_url: str, timeout_seconds: int) -> None:
    deadline = time.time() + timeout_seconds
    last_error = None
    while time.time() < deadline:
        try:
            json_request(base_url, "/api/status", timeout=5)
            return
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"OCR 後端未就緒：{last_error}")


def configure_llm(args) -> None:
    payload = {
        "api_base": args.api_base,
        "api_key": args.api_key,
        "model": args.model,
    }
    json_request(args.backend_url, "/api/set_llm_config", payload=payload, timeout=30)


def start_folder_batch(args, folder: Path) -> dict:
    json_request(args.backend_url, "/api/set_work_dir", payload={"dir": str(folder)}, timeout=30)
    payload = {
        "dir": str(folder),
        "restart": args.restart,
        "confirmed": True,
        "reprocess_last_n": 0,
    }
    response = json_request(args.backend_url, "/api/start_batch", payload=payload, timeout=30)
    if response.get("status") == "needs_confirmation":
        payload["confirmed"] = True
        response = json_request(args.backend_url, "/api/start_batch", payload=payload, timeout=30)
    if response.get("error"):
        raise RuntimeError(response["error"])
    return response


def wait_for_folder_done(args, folder: Path) -> dict:
    deadline = time.time() + (args.timeout_minutes * 60)
    last_print = 0
    while time.time() < deadline:
        status = json_request(args.backend_url, "/api/status", timeout=30)
        stats = status.get("stats") or {}
        running = bool(status.get("is_running") or stats.get("is_running"))
        now = time.time()
        if now - last_print >= max(1, args.poll_seconds):
            print(
                "[接力] {name} processed={processed}/{total} success={success} failed={failed} running={running}".format(
                    name=folder.name,
                    processed=stats.get("processed", 0),
                    total=stats.get("total", 0),
                    success=stats.get("success", 0),
                    failed=stats.get("failed", 0),
                    running=running,
                ),
                flush=True,
            )
            last_print = now
        if not running:
            return status
        time.sleep(max(1, args.poll_seconds))
    raise TimeoutError(f"{folder} 等待超過 {args.timeout_minutes} 分鐘")


def process_folder(args, source_root: Path, output_dir: Path, audit_dir: Path, row: dict, index: int) -> dict:
    folder = row["folder"]
    period = row["period"]
    token = folder_token(index, source_root, folder, period)
    folder_audit_dir = audit_dir / token
    folder_audit_dir.mkdir(parents=True, exist_ok=True)

    start_response = start_folder_batch(args, folder)
    status = wait_for_folder_done(args, folder)
    records = json_request(args.backend_url, "/api/success_records", timeout=60)
    if not isinstance(records, list):
        raise RuntimeError(f"/api/success_records 回傳格式不是清單：{records}")

    write_results_snapshot(folder_audit_dir / "success_records.csv", records)
    results_map = records_to_map(records)
    plan = make_plan(folder, results_map, period, args.price_symbol)

    plan_path = folder_audit_dir / "rename_plan.csv"
    conflict_path = folder_audit_dir / "conflicts.csv"
    copied_path = folder_audit_dir / "copied.csv"
    write_csv(plan_path, plan)
    write_csv(conflict_path, [item for item in plan if item["status"] == CONFLICT_STATUS])

    counts = summarize(plan)
    copied_count = 0
    copy_error = ""
    unknown_price_records = current_year_unknown_price_records(period, records)
    if unknown_price_records:
        review_path = folder_audit_dir / "price_review_required.csv"
        write_results_snapshot(review_path, unknown_price_records)
        copy_error = f"當年度價格查無 Samsung/PChome 參考價，需人工確認：{review_path}"
    if not args.no_copy:
        try:
            if copy_error:
                raise RuntimeError(copy_error)
            safe_plan, blocked_plan = split_plan_for_partial_copy(plan)
            if blocked_plan:
                blocked_path = folder_audit_dir / "blocked_after_recursive.csv"
                write_csv(blocked_path, blocked_plan)
                copy_error = f"{len(blocked_plan)} 筆需人工/補跑：{blocked_path}"
            copied = copy_plan_to_flat_output(safe_plan, output_dir) if safe_plan else []
            write_csv(copied_path, copied)
            copied_count = len(copied)
        except Exception as exc:
            copy_error = str(exc)

    stats = status.get("stats") or {}
    return {
        "folder": str(folder),
        "period": period,
        "image_count": row["image_count"],
        "source_latest_mtime": iso_from_mtime(row["latest_mtime"]),
        "success_records": len(records),
        "status": "copied" if copied_count else ("blocked" if copy_error else "planned"),
        "copied_count": copied_count,
        "missing_result": counts.get("missing_result", 0),
        "missing_source": counts.get("missing_source", 0),
        "conflict": counts.get("conflict", 0),
        "ready": counts.get("ready", 0),
        "no_change": counts.get("no_change", 0),
        "copy_error": copy_error,
        "processed": stats.get("processed", ""),
        "success": stats.get("success", ""),
        "failed": stats.get("failed", ""),
        "plan_path": str(plan_path),
        "copied_path": str(copied_path) if copied_count else "",
        "start_response": json.dumps(start_response, ensure_ascii=False),
    }


def write_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def child_args_without_watch(argv: List[str]) -> List[str]:
    child = []
    skip_next = False
    options_with_values = {"--watch-sleep-seconds", "--watch-cycles"}
    for item in argv:
        if skip_next:
            skip_next = False
            continue
        if item == "--watch":
            continue
        if item in options_with_values:
            skip_next = True
            continue
        if any(item.startswith(f"{option}=") for option in options_with_values):
            continue
        child.append(item)
    return child


def watch_loop(args) -> int:
    cycles = 0
    child_argv = child_args_without_watch(sys.argv[1:])
    while True:
        cycles += 1
        print(f"[接力] watch cycle={cycles} start {datetime.now().isoformat()}", flush=True)
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), *child_argv])
        if completed.returncode != 0:
            print(f"[接力] watch cycle={cycles} exit={completed.returncode}; sleep and retry", flush=True)
        if args.watch_cycles and cycles >= args.watch_cycles:
            return completed.returncode
        time.sleep(max(5, args.watch_sleep_seconds))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="遞迴接力跑 Samsung OCR，完成後把改名照片複製到同一層新資料夾。"
    )
    parser.add_argument("--source-root", required=True, help="要遞迴處理的照片根資料夾")
    parser.add_argument("--output-dir", required=True, help="改名後照片輸出的單一資料夾")
    parser.add_argument("--backend-url", default=DEFAULT_BACKEND_URL, help="OCR Dashboard 後端網址")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE, help="LM Studio API base")
    parser.add_argument("--api-key", default="lm-studio", help="LM Studio API key")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="LLM 模型名稱")
    parser.add_argument("--price-symbol", default="＄", choices=["＄", "$", ""], help="檔名價格符號")
    parser.add_argument("--poll-seconds", type=int, default=10, help="輪詢後端進度間隔秒數")
    parser.add_argument("--timeout-minutes", type=int, default=240, help="單一資料匣最長等待分鐘數")
    parser.add_argument("--limit-folders", type=int, default=0, help="只處理前 N 個資料匣，測試用")
    parser.add_argument("--restart", action="store_true", help="要求後端重跑資料匣既有結果")
    parser.add_argument("--dry-run", action="store_true", help="只列出資料匣順序與略過清單，不呼叫 OCR")
    parser.add_argument("--no-copy", action="store_true", help="只產生改名計畫，不複製照片")
    parser.add_argument("--no-resume", action="store_true", help="不使用既有 _ocr_audit 續跑狀態，重新處理所有資料匣")
    parser.add_argument("--ensure-llm", action="store_true", help="開始前先用 LM Studio CLI 確認本機模型已載入")
    parser.add_argument("--watch", action="store_true", help="keep traversing source-root; new or changed folders are picked up in later cycles")
    parser.add_argument("--watch-sleep-seconds", type=int, default=300, help="seconds to sleep between watch traversal cycles")
    parser.add_argument("--watch-cycles", type=int, default=0, help="maximum watch cycles; 0 means unlimited")
    args = parser.parse_args()

    source_root = Path(args.source_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    if not source_root.exists():
        raise SystemExit(f"來源資料夾不存在：{source_root}")
    validate_source_output_paths(source_root, output_dir)
    if args.watch:
        return watch_loop(args)

    output_dir.mkdir(parents=True, exist_ok=True)
    audit_dir = output_dir / "_ocr_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)

    folders, unsupported = discover_folders(source_root)
    if args.limit_folders and args.limit_folders > 0:
        folders = folders[: args.limit_folders]

    write_dict_csv(
        audit_dir / "skipped_unsupported.csv",
        unsupported,
        ["folder", "path", "extension", "reason"],
    )
    discovery_rows = [
        {
            "order": index,
            "folder": str(item["folder"]),
            "period": item["period"],
            "image_count": item["image_count"],
            "latest_mtime": iso_from_mtime(item["latest_mtime"]),
        }
        for index, item in enumerate(folders, start=1)
    ]
    write_dict_csv(
        audit_dir / "folder_discovery.csv",
        discovery_rows,
        ["order", "folder", "period", "image_count", "latest_mtime"],
    )

    state_path = audit_dir / "_recursive_ocr_state.json"
    summary_path = audit_dir / "folder_summary.csv"
    summary_headers = [
        "folder",
        "period",
        "image_count",
        "source_latest_mtime",
        "success_records",
        "status",
        "copied_count",
        "missing_result",
        "missing_source",
        "conflict",
        "ready",
        "no_change",
        "copy_error",
        "processed",
        "success",
        "failed",
        "plan_path",
        "copied_path",
        "start_response",
    ]

    state = {
        "source_root": str(source_root),
        "output_dir": str(output_dir),
        "backend_url": args.backend_url,
        "api_base": args.api_base,
        "model": args.model,
        "started_at": datetime.now().isoformat(),
        "dry_run": args.dry_run,
        "folders_total": len(folders),
        "unsupported_total": len(unsupported),
        "completed": [],
    }
    write_state(state_path, state)

    if args.dry_run:
        print(f"[接力] dry-run folders={len(folders)} unsupported={len(unsupported)}")
        print(f"[接力] discovery={audit_dir / 'folder_discovery.csv'}")
        print(f"[接力] skipped={audit_dir / 'skipped_unsupported.csv'}")
        return 0

    resume_enabled = not args.no_resume and not args.restart and not args.no_copy
    resume_index = build_resume_index(summary_path) if resume_enabled else {}
    pending_folders = [
        row for row in folders
        if not (
            str(row["folder"]) in resume_index
            and resume_row_matches_current(resume_index[str(row["folder"])], row)
        )
    ]

    if args.ensure_llm and pending_folders:
        ensure_local_llm(args)

    if pending_folders:
        wait_for_backend(args.backend_url, timeout_seconds=90)
        configure_llm(args)

    existing_summaries: List[Dict[str, object]] = []
    if resume_enabled and summary_path.exists():
        existing_summaries = [dict(row) for row in read_dict_csv(summary_path)]
    summary_by_folder: Dict[str, Dict[str, object]] = {
        str(row.get("folder") or ""): row
        for row in existing_summaries
        if row.get("folder")
    }
    discovered_folder_keys = [str(row["folder"]) for row in folders]

    def write_merged_summaries() -> None:
        ordered = [
            summary_by_folder[key]
            for key in discovered_folder_keys
            if key in summary_by_folder
        ]
        discovered = set(discovered_folder_keys)
        ordered.extend(
            row
            for key, row in summary_by_folder.items()
            if key and key not in discovered
        )
        write_dict_csv(summary_path, ordered, summary_headers)

    for index, folder_row in enumerate(folders, start=1):
        print(f"[接力] ({index}/{len(folders)}) 開始：{folder_row['folder']}", flush=True)
        resume_row = resume_index.get(str(folder_row["folder"]))
        if resume_row and not resume_row_matches_current(resume_row, folder_row):
            resume_row = None
        if resume_row:
            summary = summary_from_resume(resume_row, folder_row)
        else:
            try:
                summary = process_folder(args, source_root, output_dir, audit_dir, folder_row, index)
            except Exception as exc:
                summary = {
                    "folder": str(folder_row["folder"]),
                    "period": folder_row["period"],
                    "image_count": folder_row["image_count"],
                    "source_latest_mtime": iso_from_mtime(folder_row["latest_mtime"]),
                    "status": "error",
                    "copy_error": str(exc),
                }
        summary_by_folder[str(folder_row["folder"])] = summary
        write_merged_summaries()
        state["completed"].append(summary)
        state["updated_at"] = datetime.now().isoformat()
        write_state(state_path, state)
        print(f"[接力] 完成：{summary.get('status')} {folder_row['folder']}", flush=True)

    state["finished_at"] = datetime.now().isoformat()
    write_state(state_path, state)
    print(f"[接力] 全部結束 folders={len(folders)} unsupported={len(unsupported)}")
    print(f"[接力] summary={summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

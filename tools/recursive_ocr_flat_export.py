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
    copy_plan_to_flat_output,
    make_plan,
    summarize,
    write_csv,
)


UNSUPPORTED_EXTENSIONS = {".heic", ".heif", ".webp"}
DEFAULT_BACKEND_URL = "http://127.0.0.1:5000"
DEFAULT_API_BASE = "http://127.0.0.1:1234/v1"
DEFAULT_MODEL = "qwen3vl8b-ocr"


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
    if not args.no_copy:
        try:
            copied = copy_plan_to_flat_output(plan, output_dir)
            write_csv(copied_path, copied)
            copied_count = len(copied)
        except Exception as exc:
            copy_error = str(exc)

    stats = status.get("stats") or {}
    return {
        "folder": str(folder),
        "period": period,
        "image_count": row["image_count"],
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
    parser.add_argument("--ensure-llm", action="store_true", help="開始前先用 LM Studio CLI 確認本機模型已載入")
    args = parser.parse_args()

    source_root = Path(args.source_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    if not source_root.exists():
        raise SystemExit(f"來源資料夾不存在：{source_root}")

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
            "latest_mtime": datetime.fromtimestamp(item["latest_mtime"]).isoformat(),
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
        write_dict_csv(summary_path, [], summary_headers)
        print(f"[接力] dry-run folders={len(folders)} unsupported={len(unsupported)}")
        print(f"[接力] discovery={audit_dir / 'folder_discovery.csv'}")
        print(f"[接力] skipped={audit_dir / 'skipped_unsupported.csv'}")
        return 0

    if args.ensure_llm:
        ensure_local_llm(args)

    wait_for_backend(args.backend_url, timeout_seconds=90)
    configure_llm(args)

    summaries: List[Dict[str, object]] = []
    for index, folder_row in enumerate(folders, start=1):
        print(f"[接力] ({index}/{len(folders)}) 開始：{folder_row['folder']}", flush=True)
        try:
            summary = process_folder(args, source_root, output_dir, audit_dir, folder_row, index)
        except Exception as exc:
            summary = {
                "folder": str(folder_row["folder"]),
                "period": folder_row["period"],
                "image_count": folder_row["image_count"],
                "status": "error",
                "copy_error": str(exc),
            }
        summaries.append(summary)
        write_dict_csv(summary_path, summaries, summary_headers)
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

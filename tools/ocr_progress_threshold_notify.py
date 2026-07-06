#!/usr/bin/env python3
"""Track OCR progress notification thresholds.

The script does not send email itself. It reads the dashboard status API,
decides whether the completed-photo count crossed the next notification
threshold, and stores the last notified threshold in a local JSON state file.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_BACKEND_URL = "http://127.0.0.1:5000"

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_status(backend_url: str) -> dict[str, Any]:
    url = f"{backend_url.rstrip('/')}/api/status"
    with urllib.request.urlopen(url, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def build_report(status: dict[str, Any], state: dict[str, Any], step: int) -> dict[str, Any]:
    overall = status.get("overall_progress") or {}
    stats = status.get("stats") or {}
    processed = as_int(overall.get("processed_images") or stats.get("success") or stats.get("processed"))
    total = as_int(overall.get("total_images") or stats.get("total"))
    remaining = as_int(overall.get("remaining_images"))
    threshold = (processed // step) * step if step > 0 else processed
    last_threshold = as_int(state.get("last_notified_threshold"))
    should_notify = threshold > last_threshold and threshold > 0
    percent = round((processed / total) * 100, 1) if total else 0.0

    current_folder = status.get("current_relative_dir") or ""
    current_file = status.get("current_file") or ""
    running = bool(status.get("is_running"))

    subject = f"Samsung OCR progress: {threshold:,} photos completed"
    body = "\n".join(
        [
            "Samsung OCR progress notification",
            "",
            f"Completed threshold: {threshold:,}",
            f"Current processed: {processed:,} / {total:,} ({percent}%)",
            f"Remaining: {remaining:,}",
            f"Current folder: {current_folder}",
            f"Current file: {current_file}",
            f"Backend running: {running}",
            "",
            "Ready files continue uploading automatically. Questionable rows stay blocked until rerun or review.",
        ]
    )

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "processed": processed,
        "total": total,
        "remaining": remaining,
        "percent": percent,
        "threshold": threshold,
        "last_notified_threshold": last_threshold,
        "should_notify": should_notify,
        "subject": subject,
        "body": body,
        "current_folder": current_folder,
        "current_file": current_file,
        "running": running,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check OCR progress notification threshold.")
    parser.add_argument("--backend-url", default=DEFAULT_BACKEND_URL)
    parser.add_argument("--state-path", default="")
    parser.add_argument("--output-dir", default=r"D:\00_商化\00_已OCR照片")
    parser.add_argument("--step", type=int, default=10000)
    parser.add_argument("--mark-threshold", type=int, default=0)
    parser.add_argument("--initialize-to-current", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    state_path = Path(args.state_path).resolve() if args.state_path else output_dir / "_ocr_audit" / "progress_email_state.json"
    state = read_json(state_path)
    status = get_status(args.backend_url)
    report = build_report(status, state, args.step)

    if args.initialize_to_current:
        report["marked_threshold"] = report["threshold"]
        state.update(
            {
                "last_notified_threshold": report["threshold"],
                "last_marked_at": datetime.now().isoformat(timespec="seconds"),
                "last_processed": report["processed"],
            }
        )
        write_json(state_path, state)
    elif args.mark_threshold:
        report["marked_threshold"] = args.mark_threshold
        state.update(
            {
                "last_notified_threshold": args.mark_threshold,
                "last_marked_at": datetime.now().isoformat(timespec="seconds"),
                "last_processed": report["processed"],
            }
        )
        write_json(state_path, state)

    report["state_path"] = str(state_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build rerun candidates for folders blocked by missing OCR results.

The recursive exporter blocks a folder when rename_plan.csv contains
`missing_result` rows. Those rows mean the photo exists but the backend did not
produce an OCR record for it. This script collects only those missing rows into
a CSV that can be passed to tools/rerun_questionable_records.py.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path


DEFAULT_OUTPUT_DIR = Path(r"D:\00_商化\00_已OCR照片")
MISSING_RESULT_STATUS = "missing_result"
DEFAULT_STATUSES = {"blocked", "skipped_blocked"}


HEADERS = [
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
    "folder_status",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})


def parse_period_filter(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def collect(summary_path: Path, statuses: set[str], periods: set[str]) -> tuple[list[dict[str, str]], dict[str, object]]:
    rows: list[dict[str, str]] = []
    missing_source = 0
    source_missing_on_disk = 0
    folders_seen = 0
    folder_counter: Counter[str] = Counter()
    period_counter: Counter[str] = Counter()

    for summary in read_csv(summary_path):
        folder_status = summary.get("status", "")
        if statuses and folder_status not in statuses:
            continue
        period = summary.get("period", "")
        if periods and not any(period.startswith(token) for token in periods):
            continue
        plan_path = Path(summary.get("plan_path", ""))
        if not plan_path.exists():
            continue
        folders_seen += 1
        audit_folder = plan_path.parent
        for item in read_csv(plan_path):
            if item.get("status") != MISSING_RESULT_STATUS:
                continue
            source_path = Path(item.get("original_path") or "")
            if not str(source_path):
                missing_source += 1
                continue
            if not source_path.exists():
                source_missing_on_disk += 1
            source_folder = str(source_path.parent)
            file_name = item.get("original_name") or source_path.name
            rows.append(
                {
                    "period": period,
                    "audit_folder": str(audit_folder),
                    "source_folder": source_folder,
                    "source_path": str(source_path),
                    "file_name": file_name,
                    "reason": MISSING_RESULT_STATUS,
                    "view_type": "",
                    "category": "",
                    "model": "",
                    "price": "",
                    "price_status": "",
                    "folder_status": folder_status,
                }
            )
            folder_counter[source_folder] += 1
            period_counter[period] += 1

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary_path": str(summary_path),
        "folders_seen": folders_seen,
        "candidates": len(rows),
        "missing_source_path_value": missing_source,
        "source_missing_on_disk": source_missing_on_disk,
        "by_period": dict(sorted(period_counter.items(), reverse=True)),
        "by_folder": dict(sorted(folder_counter.items())),
    }
    return rows, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect missing_result rows for safe targeted rerun.")
    parser.add_argument(
        "--summary",
        default=str(DEFAULT_OUTPUT_DIR / "_ocr_audit" / "folder_summary.csv"),
        help="Recursive OCR folder_summary.csv.",
    )
    parser.add_argument("--output", default="", help="Candidate CSV output path.")
    parser.add_argument("--summary-json", default="", help="JSON summary output path.")
    parser.add_argument(
        "--statuses",
        default=",".join(sorted(DEFAULT_STATUSES)),
        help="Comma-separated folder_summary statuses to scan. Empty means all statuses.",
    )
    parser.add_argument(
        "--periods",
        default="",
        help="Comma-separated period prefixes to include, for example 202512,202502,202410.",
    )
    args = parser.parse_args()

    summary_path = Path(args.summary).resolve()
    if not summary_path.exists():
        raise SystemExit(f"summary not found: {summary_path}")

    audit_dir = summary_path.parent
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = Path(args.output).resolve() if args.output else audit_dir / f"missing_result_rerun_candidates_{stamp}.csv"
    summary_json = (
        Path(args.summary_json).resolve()
        if args.summary_json
        else output.with_suffix(".summary.json")
    )

    statuses = parse_period_filter(args.statuses)
    periods = parse_period_filter(args.periods)
    rows, summary = collect(summary_path, statuses, periods)

    write_csv(output, rows, HEADERS)
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**summary, "output": str(output), "summary_json": str(summary_json)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Split Drive review-required rows into actionable buckets."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path


BAD_PHOTO_TOKENS = [
    "照片不清楚",
    "照不清楚",
    "不合格",
    "黑屏",
]
MISSING_LABEL_TOKENS = [
    "無型號",
    "型號未辨識",
    "無價格",
    "沒有價格",
    "沒有規格",
    "沒有規格和價格牌",
]


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_rows(path: Path, rows: list[dict[str, str]], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def classify(row: dict[str, str]) -> str:
    file_name = row.get("file_name", "")
    reasons = row.get("reasons", "")
    text = f"{file_name};{reasons}"

    if any(token in text for token in BAD_PHOTO_TOKENS):
        return "bad_or_unclear_photo"
    if "current_year_distant_view_needs_rerun" in reasons:
        return "current_year_distant_view_needs_rerun"
    if "current_year_missing_compare_symbol" in reasons:
        return "needs_reference_price_compare"
    if any(token in text for token in MISSING_LABEL_TOKENS):
        return "missing_model_or_price_label"
    if "unknown_marker" in reasons:
        return "unresolved_ocr_marker"
    if "oversize" in reasons:
        return "oversize_file"
    return "other_review"


def main() -> int:
    parser = argparse.ArgumentParser(description="Split Drive review-required rows by action bucket.")
    parser.add_argument("--output-dir", default=r"D:\00_商化\00_已OCR照片")
    parser.add_argument("--manifest-dir", default="")
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    manifest_dir = Path(args.manifest_dir).resolve() if args.manifest_dir else output_dir / "_drive_upload"
    input_csv = manifest_dir / "drive_upload_review_required.csv"
    rows = read_rows(input_csv)
    if not rows:
        summary = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "input_csv": str(input_csv),
            "total": 0,
            "buckets": {},
        }
        (manifest_dir / "drive_upload_review_split_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    headers = list(rows[0].keys()) + ["review_bucket"]
    buckets: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        bucket = classify(row)
        item = dict(row)
        item["review_bucket"] = bucket
        buckets.setdefault(bucket, []).append(item)

    for bucket, bucket_rows in buckets.items():
        write_rows(manifest_dir / f"drive_upload_review_{bucket}.csv", bucket_rows, headers)

    counter = Counter({bucket: len(bucket_rows) for bucket, bucket_rows in buckets.items()})
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input_csv": str(input_csv),
        "total": len(rows),
        "buckets": dict(sorted(counter.items(), key=lambda item: (-item[1], item[0]))),
    }
    (manifest_dir / "drive_upload_review_split_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

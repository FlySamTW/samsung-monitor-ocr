#!/usr/bin/env python3
"""Prepare safe Google Drive upload manifests for flat OCR output photos.

This script separates deliverable files from records that should be rerun or
reviewed before going to Drive. It also prepares an ASCII-only staging folder
for connector-based uploads and can skip files already recorded as uploaded.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
PERIOD_RE = re.compile(r"^M-(\d{6})-")
PRICE_TOKEN_RE = re.compile(
    r"-(?P<symbol>[\u2191\u2193\u2713\u2714\?\uff1f]?)[\uff04$](?P<price>\d+)-"
)
DISTANT_VIEW_TOKEN = "\u9060\u666f"
OTHER_BRAND_TOKEN = "\u5b83\u724c("  # 它牌(
REVIEW_NAME_TOKENS = [
    "\u505c\u7522",  # discontinued
    "\u7121\u578b\u865f",  # no model
    "\u578b\u865f\u672a\u8fa8\u8b58",  # model not recognized
    "\u7121\u50f9\u683c",  # no price
    "\u4e0d\u5408\u683c",  # rejected
    "\u7167\u7247\u4e0d\u6e05\u695a",  # unclear photo
    "\u7167\u4e0d\u6e05\u695a",  # unclear photo
    "\u6c92\u6709\u898f\u683c",  # no spec label
    "\u6c92\u6709\u50f9\u683c",  # no price label
    "\u6c92\u6709\u898f\u683c\u548c\u50f9\u683c\u724c",  # no spec and price label
    "\u9ed1\u5c4f",  # black screen
]
COMPARE_SYMBOLS = {"\u2191", "\u2193", "\u2713", "\u2714"}
UNKNOWN_SYMBOLS = {"?", "\uff1f"}


@dataclass
class ManifestRow:
    source_path: str
    file_name: str
    year: str
    period: str
    drive_folder: str
    size_bytes: int
    status: str
    reasons: str


def infer_period(file_name: str) -> str:
    match = PERIOD_RE.match(file_name)
    return match.group(1) if match else ""


def load_uploaded(uploaded_log: Path | None) -> tuple[set[str], set[str]]:
    uploaded_names: set[str] = set()
    uploaded_paths: set[str] = set()
    if not uploaded_log or not uploaded_log.exists():
        return uploaded_names, uploaded_paths

    with uploaded_log.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            file_name = (row.get("file_name") or "").strip()
            source_path = (row.get("source_path") or "").strip()
            if file_name:
                uploaded_names.add(file_name)
            if source_path:
                uploaded_paths.add(str(Path(source_path).resolve()))
    return uploaded_names, uploaded_paths


def classify_file(path: Path, output_root: Path, max_bytes: int) -> ManifestRow:
    file_name = path.name
    period = infer_period(file_name)
    year = period[:4] if period else ""
    reasons: list[str] = []

    if not period:
        reasons.append("missing_period")

    if path.suffix.lower() not in IMAGE_EXTS:
        reasons.append("not_image")

    size_bytes = path.stat().st_size
    if size_bytes <= 0:
        reasons.append("empty_file")
    if size_bytes > max_bytes:
        reasons.append("oversize")

    is_distant_view = f"-{DISTANT_VIEW_TOKEN}-" in file_name
    is_other_brand = OTHER_BRAND_TOKEN in file_name
    for token in REVIEW_NAME_TOKENS:
        if token in file_name:
            reasons.append(f"name_contains_{token}")

    if "\uff1f" in file_name or "?" in file_name:
        reasons.append("unknown_marker")

    price_match = PRICE_TOKEN_RE.search(file_name)
    if period:
        period_year = int(year)
        symbol = price_match.group("symbol") if price_match else ""
        if period_year >= 2026:
            if is_distant_view:
                reasons.append("current_year_distant_view_needs_rerun")
            elif not price_match:
                reasons.append("current_year_missing_price")
            elif (not is_other_brand) and symbol not in COMPARE_SYMBOLS:
                reasons.append("current_year_missing_compare_symbol")
        if period_year < 2026 and price_match and symbol in (COMPARE_SYMBOLS | UNKNOWN_SYMBOLS):
            reasons.append("historical_has_compare_symbol")

    try:
        rel_parts = path.relative_to(output_root).parts
        if any(part.startswith("_") for part in rel_parts):
            reasons.append("internal_folder")
    except ValueError:
        pass

    status = "ready" if not reasons else "review"
    drive_folder = year if year else "_needs_review"
    return ManifestRow(
        source_path=str(path.resolve()),
        file_name=file_name,
        year=year,
        period=period,
        drive_folder=drive_folder,
        size_bytes=size_bytes,
        status=status,
        reasons=";".join(reasons),
    )


def write_csv(path: Path, rows: list[ManifestRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(ManifestRow.__dataclass_fields__.keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def newest_first_key(row: ManifestRow) -> tuple[int, str]:
    try:
        period_rank = int(row.period)
    except (TypeError, ValueError):
        period_rank = -1
    return (-period_rank, row.file_name.casefold())


def stage_upload_batch(rows: list[ManifestRow], stage_dir: Path) -> Path:
    stage_dir.mkdir(parents=True, exist_ok=True)
    for old_file in stage_dir.glob("upload_*"):
        if old_file.is_file():
            old_file.unlink()

    map_path = stage_dir.parent / "staging_map.csv"
    with map_path.open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = ["index", "stage_file", "source_path", "file_name", "year", "drive_folder"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for index, row in enumerate(rows, start=1):
            ext = Path(row.file_name).suffix.lower() or ".jpg"
            stage_name = f"upload_{index:04d}{ext}"
            stage_path = stage_dir / stage_name
            shutil.copy2(row.source_path, stage_path)
            writer.writerow(
                {
                    "index": index,
                    "stage_file": str(stage_path.resolve()),
                    "source_path": row.source_path,
                    "file_name": row.file_name,
                    "year": row.year,
                    "drive_folder": row.drive_folder,
                }
            )
    return map_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare Google Drive upload manifests for OCR output photos.")
    parser.add_argument("--output-dir", default=r"D:\00_商化\00_已OCR照片", help="Flat OCR output folder.")
    parser.add_argument("--manifest-dir", default=None, help="Where to write CSV/JSON manifests.")
    parser.add_argument("--uploaded-log", default=None, help="CSV of already uploaded files to skip.")
    parser.add_argument("--max-bytes", type=int, default=2_500_000, help="Mark larger files for review.")
    parser.add_argument("--limit-ready", type=int, default=0, help="Optional pending ready-row limit for upload.")
    parser.add_argument("--no-stage", action="store_true", help="Do not copy the next batch into staging.")
    args = parser.parse_args()

    output_root = Path(args.output_dir).resolve()
    if not output_root.exists():
        raise SystemExit(f"Output folder not found: {output_root}")

    manifest_dir = Path(args.manifest_dir).resolve() if args.manifest_dir else output_root / "_drive_upload"
    default_uploaded_log = manifest_dir / "drive_upload_uploaded.csv"
    uploaded_log = Path(args.uploaded_log).resolve() if args.uploaded_log else default_uploaded_log
    uploaded_names, uploaded_paths = load_uploaded(uploaded_log)

    all_rows: list[ManifestRow] = []
    for path in sorted(output_root.rglob("*")):
        if not path.is_file():
            continue
        try:
            rel_parts = path.relative_to(output_root).parts
        except ValueError:
            rel_parts = path.parts
        if any(part.startswith("_") for part in rel_parts):
            continue
        if path.suffix.lower() not in IMAGE_EXTS:
            continue
        all_rows.append(classify_file(path, output_root, args.max_bytes))

    all_rows.sort(key=newest_first_key)
    ready_rows = sorted((row for row in all_rows if row.status == "ready"), key=newest_first_key)
    review_rows = sorted((row for row in all_rows if row.status != "ready"), key=newest_first_key)
    pending_rows = [
        row
        for row in ready_rows
        if row.file_name not in uploaded_names and row.source_path not in uploaded_paths
    ]
    upload_rows = pending_rows[: args.limit_ready] if args.limit_ready else pending_rows

    write_csv(manifest_dir / "drive_upload_all.csv", all_rows)
    write_csv(manifest_dir / "drive_upload_ready.csv", ready_rows)
    write_csv(manifest_dir / "drive_upload_ready_pending.csv", pending_rows)
    write_csv(manifest_dir / "drive_upload_review_required.csv", review_rows)
    write_csv(manifest_dir / "drive_upload_next_batch.csv", upload_rows)

    staging_map = ""
    if upload_rows and not args.no_stage:
        staging_map = str(stage_upload_batch(upload_rows, manifest_dir / "staging").resolve())

    by_year: dict[str, int] = {}
    pending_by_year: dict[str, int] = {}
    for row in ready_rows:
        by_year[row.drive_folder] = by_year.get(row.drive_folder, 0) + 1
    for row in pending_rows:
        pending_by_year[row.drive_folder] = pending_by_year.get(row.drive_folder, 0) + 1

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "output_dir": str(output_root),
        "manifest_dir": str(manifest_dir),
        "uploaded_log": str(uploaded_log) if uploaded_log.exists() else "",
        "total_images": len(all_rows),
        "ready": len(ready_rows),
        "uploaded_skipped": len(ready_rows) - len(pending_rows),
        "ready_pending": len(pending_rows),
        "review_required": len(review_rows),
        "next_batch": len(upload_rows),
        "staging_map": staging_map,
        "max_bytes": args.max_bytes,
        "ready_by_year": dict(sorted(by_year.items(), reverse=True)),
        "pending_by_year": dict(sorted(pending_by_year.items(), reverse=True)),
    }
    (manifest_dir / "drive_upload_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Apply dashboard manual review corrections to flat OCR output filenames.

The dashboard review drawer is append-only. This script is the audited follow-up
step: read the latest human correction for each output photo, build the corrected
flat filename, and optionally rename the file.
"""

from __future__ import annotations

import argparse
import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable

from photo_rename_planner import sanitize_segment


DEFAULT_OUTPUT_DIR = Path(r"D:\00_商化\00_已OCR照片")
IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
VIEW_TYPES = {"單機", "遠景"}


def read_latest_corrections(path: Path) -> Dict[str, dict]:
    latest: Dict[str, dict] = {}
    if not path.exists():
        return latest
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            file_name = (row.get("file_name") or "").strip()
            source_path = (row.get("source_path") or "").strip()
            key = source_path or file_name
            if not key:
                continue
            latest[key] = row
    return latest


def clean_price_segment(price: str, price_symbol: str) -> str:
    raw = str(price or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return "無價格"
    symbol = str(price_symbol or "").strip()
    if symbol not in {"↑", "↓", "✓", "？"}:
        match = re.match(r"^([↑↓✓？?])", raw)
        symbol = match.group(1).replace("?", "？") if match else ""
    return f"{symbol}＄{digits}"


def build_corrected_name(current_name: str, correction: dict) -> str:
    path = Path(current_name)
    parts = path.stem.split("-")
    view_idx = next((idx for idx, part in enumerate(parts) if part in VIEW_TYPES), -1)
    if view_idx < 0:
        raise ValueError("filename does not contain 單機/遠景 segment")

    prefix = parts[:view_idx]
    serial = parts[-1] if parts else ""
    view_type = (correction.get("corrected_view_type") or parts[view_idx] or "單機").strip()
    if view_type not in VIEW_TYPES:
        view_type = "單機"

    if view_type == "遠景":
        segments = [*prefix, "遠景", serial]
    else:
        old_model = parts[view_idx + 1] if view_idx + 1 < len(parts) else ""
        old_price = parts[view_idx + 2] if view_idx + 2 < len(parts) else ""
        model = (correction.get("corrected_model") or old_model or "型號未辨識").strip()
        price = correction.get("corrected_price") or old_price
        price_segment = clean_price_segment(price, correction.get("corrected_price_symbol") or "")
        segments = [*prefix, "單機", model, price_segment, serial]

    return "-".join(sanitize_segment(segment) for segment in segments) + path.suffix.lower()


def unique_target(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    for idx in range(2, 10000):
        candidate = parent / f"{stem}_{idx}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"unable to find free target name for {path}")


def iter_plan(corrections: Dict[str, dict], output_dir: Path) -> Iterable[dict]:
    for key, row in corrections.items():
        action = (row.get("action") or "").strip()
        if action == "needs_rerun":
            yield {
                **row,
                "status": "skipped_needs_rerun",
                "current_path": row.get("source_path") or "",
                "target_path": "",
                "target_name": "",
                "reason": "marked for rerun, not manual rename",
            }
            continue

        current_path = Path(row.get("source_path") or "")
        if not current_path.exists():
            current_path = output_dir / (row.get("file_name") or "")
        if not current_path.exists() or current_path.suffix.lower() not in IMAGE_EXTS:
            yield {
                **row,
                "status": "missing_source",
                "current_path": str(current_path),
                "target_path": "",
                "target_name": "",
                "reason": "corrected output file not found",
            }
            continue

        try:
            target_name = build_corrected_name(current_path.name, row)
        except Exception as exc:
            yield {
                **row,
                "status": "invalid_filename",
                "current_path": str(current_path),
                "target_path": "",
                "target_name": "",
                "reason": str(exc),
            }
            continue

        target_path = current_path.with_name(target_name)
        status = "no_change" if target_path == current_path else "ready"
        if status == "ready" and target_path.exists():
            target_path = unique_target(target_path)
            target_name = target_path.name
            status = "ready_conflict_suffix"
        yield {
            **row,
            "status": status,
            "current_path": str(current_path),
            "target_path": str(target_path),
            "target_name": target_name,
            "reason": "",
        }


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply manual review corrections to flat OCR output names.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Flat OCR output folder.")
    parser.add_argument("--input", default=None, help="manual_corrections.csv path.")
    parser.add_argument("--apply", action="store_true", help="Actually rename files. Default is dry-run.")
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    input_path = Path(args.input).resolve() if args.input else output_dir / "_ocr_audit" / "manual_corrections.csv"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    plan_path = output_dir / "_ocr_audit" / f"manual_correction_rename_plan_{stamp}.csv"
    rollback_path = output_dir / "_ocr_audit" / f"manual_correction_rollback_{stamp}.csv"

    corrections = read_latest_corrections(input_path)
    plan_rows = list(iter_plan(corrections, output_dir))
    fieldnames = [
        "timestamp", "file_name", "source_path", "period", "year", "review_reasons",
        "corrected_view_type", "corrected_model", "corrected_price", "corrected_price_symbol",
        "note", "action", "learn_rule", "rule_hint", "status", "current_path", "target_path",
        "target_name", "reason",
    ]
    write_csv(plan_path, plan_rows, fieldnames)

    rollback_rows = []
    if args.apply:
        for row in plan_rows:
            if not str(row.get("status", "")).startswith("ready"):
                continue
            source = Path(row["current_path"])
            target = Path(row["target_path"])
            source.rename(target)
            rollback_rows.append({
                "renamed_at": datetime.now().isoformat(timespec="seconds"),
                "old_path": str(source),
                "new_path": str(target),
            })
        write_csv(rollback_path, rollback_rows, ["renamed_at", "old_path", "new_path"])

    ready = sum(1 for row in plan_rows if str(row.get("status", "")).startswith("ready"))
    print(f"input={input_path}")
    print(f"plan={plan_path}")
    print(f"rows={len(plan_rows)} ready={ready} applied={len(rollback_rows)}")
    if args.apply:
        print(f"rollback={rollback_path}")
    else:
        print("dry_run=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

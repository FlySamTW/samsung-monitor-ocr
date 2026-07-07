#!/usr/bin/env python3
"""Rebuild recursive OCR folder_summary.csv from per-folder audit artifacts."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


HEADERS = [
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


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in HEADERS})


def infer_folder(rows: list[dict[str, str]]) -> str:
    for row in rows:
        source = row.get("original_path") or row.get("source_path") or ""
        if source:
            return str(Path(source).parent)
    return ""


def count_status(rows: list[dict[str, str]], status: str) -> int:
    return sum(1 for row in rows if (row.get("status") or "").strip() == status)


def build_rows(audit_dir: Path) -> dict[str, dict[str, object]]:
    by_folder: dict[str, dict[str, object]] = {}
    for folder_dir in sorted(path for path in audit_dir.iterdir() if path.is_dir()):
        plan_path = folder_dir / "rename_plan.csv"
        copied_path = folder_dir / "copied.csv"
        success_path = folder_dir / "success_records.csv"
        plan_rows = read_csv(plan_path)
        copied_rows = read_csv(copied_path)
        success_rows = read_csv(success_path)
        if not plan_rows and not copied_rows and not success_rows:
            continue

        folder = infer_folder(copied_rows) or infer_folder(plan_rows)
        if not folder and success_rows:
            continue

        period = ""
        for source in (copied_rows, plan_rows):
            if source:
                period = source[0].get("period") or period
                if period:
                    break

        copied_count = len(copied_rows)
        ready = count_status(plan_rows, "ready")
        no_change = count_status(plan_rows, "no_change")
        conflict = count_status(plan_rows, "conflict")
        image_count = len(plan_rows) or len(success_rows) or copied_count
        missing_result = max(image_count - len(success_rows), 0) if success_rows else 0
        missing_source = 0
        for row in plan_rows:
            original = row.get("original_path") or ""
            if original and not Path(original).exists():
                missing_source += 1

        status = "copied" if copied_count and copied_count >= image_count and conflict == 0 and missing_source == 0 else "blocked"
        by_folder[folder] = {
            "folder": folder,
            "period": period,
            "image_count": image_count,
            "success_records": len(success_rows),
            "status": status,
            "copied_count": copied_count,
            "missing_result": missing_result,
            "missing_source": missing_source,
            "conflict": conflict,
            "ready": ready,
            "no_change": no_change,
            "copy_error": "",
            "processed": len(success_rows) or copied_count,
            "success": len(success_rows) or copied_count,
            "failed": 0,
            "plan_path": str(plan_path) if plan_path.exists() else "",
            "copied_path": str(copied_path) if copied_path.exists() else "",
            "start_response": "rebuilt_from_audit",
        }
    return by_folder


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=r"D:\00_商化\00_已OCR照片")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    audit_dir = output_dir / "_ocr_audit"
    discovery_path = audit_dir / "folder_discovery.csv"
    summary_path = audit_dir / "folder_summary.csv"
    if not discovery_path.exists():
        raise SystemExit(f"missing folder_discovery.csv: {discovery_path}")

    discovery_rows = read_csv(discovery_path)
    rebuilt = build_rows(audit_dir)
    current_rows = {row.get("folder", ""): row for row in read_csv(summary_path) if row.get("folder")}
    current_rows.update(rebuilt)

    ordered: list[dict[str, object]] = []
    for discovery in discovery_rows:
        folder = discovery.get("folder") or ""
        row = current_rows.get(folder)
        if not row:
            continue
        row = dict(row)
        row["period"] = row.get("period") or discovery.get("period", "")
        row["image_count"] = row.get("image_count") or discovery.get("image_count", "")
        row["source_latest_mtime"] = discovery.get("latest_mtime", "")
        ordered.append(row)

    discovered = {row.get("folder") for row in discovery_rows}
    for folder, row in current_rows.items():
        if folder and folder not in discovered:
            ordered.append(row)

    print(f"[rebuild] rows={len(ordered)} rebuilt={len(rebuilt)} summary={summary_path}")
    if not args.dry_run:
        write_csv(summary_path, ordered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

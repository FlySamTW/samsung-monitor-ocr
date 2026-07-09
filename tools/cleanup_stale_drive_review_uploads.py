#!/usr/bin/env python3
"""Remove current-year files that were uploaded before stricter review gates.

By default this is a dry run. With --execute it deletes the remote files listed
in _drive_upload/drive_upload_stale_uploaded_review_required.csv and removes
their rows from drive_upload_uploaded.csv so corrected/accepted outputs can be
uploaded again later.
"""
from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path


DEFAULT_OUTPUT_DIR = Path(r"D:\00_商化\00_已OCR照片")
DEFAULT_REMOTE = "samsung_ocr_drive"
DEFAULT_RCLONE_PATHS = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "rclone.exe",
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Microsoft"
    / "WinGet"
    / "Packages"
    / "Rclone.Rclone_Microsoft.Winget.Source_8wekyb3d8bbwe"
    / "rclone-v1.74.3-windows-amd64"
    / "rclone.exe",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def resolve_rclone(explicit: str) -> Path:
    if explicit:
        path = Path(explicit)
        if path.exists():
            return path
    found = shutil.which("rclone") or shutil.which("rclone.exe")
    if found:
        return Path(found)
    for path in DEFAULT_RCLONE_PATHS:
        if path.exists():
            return path
    raise SystemExit("rclone.exe not found")


def group_rows(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        year = (row.get("drive_folder") or row.get("year") or "").strip()
        name = (row.get("file_name") or "").strip()
        if not year or not name:
            continue
        try:
            if int(year) < 2026:
                continue
        except ValueError:
            continue
        grouped.setdefault(year, []).append(row)
    return grouped


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean stale current-year Drive uploads.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--remote", default=DEFAULT_REMOTE)
    parser.add_argument("--rclone", default="")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    manifest_dir = output_dir / "_drive_upload"
    stale_path = manifest_dir / "drive_upload_stale_uploaded_review_required.csv"
    uploaded_path = manifest_dir / "drive_upload_uploaded.csv"
    cleanup_log_path = manifest_dir / "drive_upload_stale_cleanup_log.csv"
    rclone = resolve_rclone(args.rclone)

    stale_rows = read_csv(stale_path)
    grouped = group_rows(stale_rows)
    selected_names: set[str] = set()
    cleanup_rows: list[dict[str, str]] = []
    stamp = datetime.now().isoformat(timespec="seconds")

    for year, rows in sorted(grouped.items(), reverse=True):
        if args.limit:
            remaining = max(0, args.limit - len(selected_names))
            if remaining <= 0:
                break
            rows = rows[:remaining]
        if not rows:
            continue
        files_from = manifest_dir / f"stale_review_delete_{year}.txt"
        with files_from.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                name = row["file_name"]
                handle.write(f"{name}\n")
                selected_names.add(name)
        command = [
            str(rclone),
            "delete",
            f"{args.remote}:{year}",
            "--files-from-raw",
            str(files_from),
        ]
        if not args.execute:
            command.append("--dry-run")
        completed = subprocess.run(command, text=True)
        for row in rows:
            cleanup_rows.append(
                {
                    "timestamp": stamp,
                    "year": year,
                    "file_name": row.get("file_name", ""),
                    "remote_path": f"{year}/{row.get('file_name', '')}",
                    "execute": "yes" if args.execute else "no",
                    "returncode": str(completed.returncode),
                }
            )
        if completed.returncode != 0:
            write_csv(
                cleanup_log_path,
                read_csv(cleanup_log_path) + cleanup_rows,
                ["timestamp", "year", "file_name", "remote_path", "execute", "returncode"],
            )
            return completed.returncode

    existing_log = read_csv(cleanup_log_path)
    write_csv(
        cleanup_log_path,
        existing_log + cleanup_rows,
        ["timestamp", "year", "file_name", "remote_path", "execute", "returncode"],
    )

    if args.execute and selected_names and uploaded_path.exists():
        uploaded_rows = read_csv(uploaded_path)
        headers = list(uploaded_rows[0].keys()) if uploaded_rows else []
        kept = [row for row in uploaded_rows if row.get("file_name", "") not in selected_names]
        backup = uploaded_path.with_name(f"drive_upload_uploaded.csv.before_stale_cleanup_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        shutil.copy2(uploaded_path, backup)
        write_csv(uploaded_path, kept, headers)
        print(f"[cleanup] removed_uploaded_log_rows={len(uploaded_rows) - len(kept)} backup={backup}", flush=True)

    print(
        f"[cleanup] selected={len(selected_names)} execute={args.execute} log={cleanup_log_path}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

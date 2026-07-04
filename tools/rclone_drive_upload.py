#!/usr/bin/env python3
"""Upload ready OCR photos to Google Drive with rclone.

This is the unattended uploader. It uses prepare_drive_upload_manifest.py to
select safe `ready` files, uploads them into year-only Drive folders, and then
records the uploaded rows so the next run resumes without duplicates.
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
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


UPLOAD_HEADERS = [
    "batch",
    "index",
    "year",
    "source_path",
    "file_name",
    "drive_folder_id",
    "drive_file_id",
    "url",
    "uploaded_at",
    "uploader",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def append_uploaded(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_csv(path)
    known = {(row.get("source_path", ""), row.get("file_name", "")) for row in existing}
    output_rows = list(existing)
    next_index = len(output_rows) + 1
    for row in rows:
        key = (row.get("source_path", ""), row.get("file_name", ""))
        if key in known:
            continue
        row = dict(row)
        row["index"] = str(next_index)
        output_rows.append(row)
        known.add(key)
        next_index += 1

    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=UPLOAD_HEADERS, extrasaction="ignore")
        writer.writeheader()
        for row in output_rows:
            writer.writerow({header: row.get(header, "") for header in UPLOAD_HEADERS})


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
    raise SystemExit("rclone.exe not found. Install with winget install --id Rclone.Rclone -e")


@contextmanager
def single_instance_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = None
    try:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        handle = os.open(str(lock_path), flags)
        os.write(handle, f"pid={os.getpid()}\nstarted={datetime.now().isoformat(timespec='seconds')}\n".encode("utf-8"))
        yield
    except FileExistsError:
        raise SystemExit(f"upload already appears to be running; lock exists: {lock_path}")
    finally:
        if handle is not None:
            os.close(handle)
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass


def run_command(command: list[str], log_path: Path, execute: bool, timeout_seconds: int) -> int:
    printable = " ".join(f'"{part}"' if " " in part else part for part in command)
    print(printable, flush=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] {printable}\n")
        if not execute:
            log.write("[dry-run]\n")
            return 0
        try:
            completed = subprocess.run(
                command,
                text=True,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=timeout_seconds if timeout_seconds > 0 else None,
            )
            return completed.returncode
        except subprocess.TimeoutExpired:
            log.write(f"[timeout] command exceeded {timeout_seconds} seconds\n")
            print(f"[upload] rclone timed out after {timeout_seconds} seconds", flush=True)
            return 124


def prepare_manifest(args, limit: int) -> Path:
    command = [
        sys.executable,
        str(REPO_ROOT / "tools" / "prepare_drive_upload_manifest.py"),
        "--output-dir",
        str(args.output_dir),
        "--limit-ready",
        str(limit),
        "--no-stage",
    ]
    completed = subprocess.run(command, text=True)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)
    return args.manifest_dir / "drive_upload_next_batch.csv"


def write_files_from(manifest_dir: Path, year: str, rows: list[dict[str, str]]) -> Path:
    path = manifest_dir / f"rclone_files_{year}.txt"
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(f"{row['file_name']}\n")
    return path


def upload_once(args, batch_id: str, cycle: int) -> int:
    next_batch = prepare_manifest(args, args.limit)
    rows = read_csv(next_batch)
    if not rows:
        print("[upload] no pending ready rows", flush=True)
        return 0

    by_year: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        year = row.get("year") or row.get("drive_folder") or "_needs_review"
        by_year.setdefault(year, []).append(row)

    uploaded_rows: list[dict[str, str]] = []
    for year, year_rows in sorted(by_year.items(), reverse=True):
        files_from = write_files_from(args.manifest_dir, year, year_rows)
        remote_path = f"{args.remote}:{year}"
        command = [
            str(args.rclone),
            "copy",
            str(args.output_dir),
            remote_path,
            "--files-from-raw",
            str(files_from),
            "--ignore-existing",
            "--transfers",
            str(args.transfers),
            "--checkers",
            str(args.checkers),
            "--drive-stop-on-upload-limit",
            "--stats",
            "30s",
        ]
        if args.dry_run:
            command.append("--dry-run")
        rc = run_command(command, args.log_path, execute=args.execute, timeout_seconds=args.rclone_timeout_seconds)
        if rc != 0:
            raise SystemExit(rc)

        uploaded_at = datetime.now().isoformat(timespec="seconds")
        for row in year_rows:
            uploaded_rows.append(
                {
                    "batch": f"{batch_id}_cycle{cycle:03d}",
                    "year": year,
                    "source_path": row.get("source_path", ""),
                    "file_name": row.get("file_name", ""),
                    "drive_folder_id": year,
                    "drive_file_id": "",
                    "url": f"rclone://{args.remote}/{year}/{row.get('file_name', '')}",
                    "uploaded_at": uploaded_at,
                    "uploader": "rclone",
                }
            )

    if args.execute and not args.dry_run:
        append_uploaded(args.uploaded_log, uploaded_rows)
        # Refresh manifest summary after logging successful uploads.
        prepare_manifest(args, args.limit)
    print(f"[upload] cycle={cycle} uploaded_or_existing={len(uploaded_rows)}", flush=True)
    return len(uploaded_rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload ready OCR photos to Google Drive year folders using rclone.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--manifest-dir", default="")
    parser.add_argument("--uploaded-log", default="")
    parser.add_argument("--rclone", default="")
    parser.add_argument("--remote", default=DEFAULT_REMOTE)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--repeat", action="store_true", help="Keep uploading batches until no ready rows remain.")
    parser.add_argument("--max-cycles", type=int, default=0, help="0 means no max when --repeat is used.")
    parser.add_argument("--sleep-seconds", type=int, default=10)
    parser.add_argument("--transfers", type=int, default=4)
    parser.add_argument("--checkers", type=int, default=8)
    parser.add_argument(
        "--rclone-timeout-seconds",
        type=int,
        default=1800,
        help="Abort one rclone batch if it runs longer than this. 0 disables the timeout.",
    )
    parser.add_argument("--execute", action="store_true", help="Actually upload. Without this, only print commands.")
    parser.add_argument("--dry-run", action="store_true", help="Pass --dry-run to rclone.")
    args = parser.parse_args()

    args.output_dir = Path(args.output_dir).resolve()
    args.manifest_dir = Path(args.manifest_dir).resolve() if args.manifest_dir else args.output_dir / "_drive_upload"
    args.uploaded_log = Path(args.uploaded_log).resolve() if args.uploaded_log else args.manifest_dir / "drive_upload_uploaded.csv"
    args.rclone = resolve_rclone(args.rclone)
    args.log_path = args.manifest_dir / "rclone_drive_upload.log"
    args.lock_path = args.manifest_dir / "rclone_drive_upload.lock"

    batch_id = datetime.now().strftime("rclone_%Y%m%d_%H%M%S")
    with single_instance_lock(args.lock_path):
        cycle = 1
        while True:
            uploaded = upload_once(args, batch_id, cycle)
            if not args.repeat or uploaded <= 0:
                break
            cycle += 1
            if args.max_cycles and cycle > args.max_cycles:
                break
            time.sleep(max(1, args.sleep_seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

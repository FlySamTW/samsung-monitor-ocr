#!/usr/bin/env python3
"""Upload ready OCR photos to Google Drive with rclone.

This is the unattended uploader. It uses prepare_drive_upload_manifest.py to
select safe `ready` files, uploads them into year-only Drive folders, and then
records the uploaded rows so the next run resumes without duplicates.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
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


def ensure_runtime_health_fuse_clear(output_dir: Path) -> None:
    fuse = output_dir / "_ocr_audit" / "runtime_health_fuse.json"
    if fuse.exists():
        raise SystemExit(f"runtime health fuse is active; upload blocked: {fuse}")


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
    ]
    completed = subprocess.run(command, text=True)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)
    return args.manifest_dir / "drive_upload_next_batch.csv"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: object) -> bool:
    text = str(value or "").strip().lower()
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)


def _is_zero_or_empty(value: object) -> bool:
    if value in (None, "", 0, "0"):
        return True
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def validate_prepared_manifest(manifest_dir: Path, next_batch: Path, rows: list[dict[str, str]]) -> None:
    """Reject a prepared batch unless its summary proves it is the exact safe batch."""
    summary_path = manifest_dir / "drive_upload_summary.json"
    if not next_batch.is_file():
        raise SystemExit(f"prepared upload batch missing: {next_batch}")
    if not summary_path.is_file():
        raise SystemExit(f"upload manifest summary missing: {summary_path}")
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
    except (OSError, TypeError, ValueError) as exc:
        raise SystemExit(f"upload manifest summary unreadable: {exc}") from exc
    if not isinstance(summary, dict):
        raise SystemExit("upload manifest summary must be a JSON object")

    expected_batch_hash = str(summary.get("next_batch_sha256") or "").strip().lower()
    actual_batch_hash = sha256_file(next_batch)
    if not _is_sha256(expected_batch_hash) or expected_batch_hash != actual_batch_hash:
        raise SystemExit("prepared upload batch SHA-256 does not match manifest summary")

    has_current_year = any(
        str(row.get("year") or row.get("drive_folder") or "").strip() == "2026"
        for row in rows
    )
    if not has_current_year:
        return

    if summary.get("current_year_upload_gate_open") is not True:
        raise SystemExit("2026 upload gate is not open")
    if summary.get("current_year_risk_audit_fresh") is not True:
        raise SystemExit("2026 risk audit is not fresh")

    proof = summary.get("current_year_finalization_proof")
    if not isinstance(proof, dict) or proof.get("complete") is not True:
        raise SystemExit("2026 finalization proof is missing or incomplete")
    required_proof_fields = {
        "expected_candidate_count",
        "scanned_result_count",
        "missing_or_invalid",
        "duplicate_source_identity",
        "audit_input_sha256",
    }
    if not required_proof_fields.issubset(proof):
        raise SystemExit("2026 finalization proof is missing required fields")
    try:
        expected_count = int(proof.get("expected_candidate_count"))
        scanned_count = int(proof.get("scanned_result_count"))
    except (TypeError, ValueError) as exc:
        raise SystemExit("2026 finalization proof counts are missing or invalid") from exc
    if expected_count <= 0 or scanned_count != expected_count:
        raise SystemExit("2026 finalization proof count mismatch")
    if not _is_zero_or_empty(proof.get("missing_or_invalid")):
        raise SystemExit("2026 finalization proof reports missing or invalid inputs")
    if not _is_zero_or_empty(proof.get("duplicate_source_identity")):
        raise SystemExit("2026 finalization proof reports duplicate source identities")

    proof_hash = str(proof.get("audit_input_sha256") or "").strip().lower()
    current_hash = str(summary.get("current_audit_input_sha256") or "").strip().lower()
    if not _is_sha256(proof_hash) or not _is_sha256(current_hash) or proof_hash != current_hash:
        raise SystemExit("2026 audit input SHA-256 proof does not match current inputs")


def load_staged_paths(manifest_dir: Path, rows: list[dict[str, str]]) -> dict[tuple[str, str], Path]:
    """Return only staged files belonging to this manifest batch."""
    staging_root = (manifest_dir / "staging").resolve()
    map_rows = read_csv(manifest_dir / "staging_map.csv")
    staged: dict[tuple[str, str], Path] = {}
    for item in map_rows:
        stage_path = Path(item.get("stage_file", "")).resolve()
        try:
            stage_path.relative_to(staging_root)
        except ValueError:
            continue
        if not stage_path.is_file():
            continue
        key = (item.get("source_path", ""), item.get("file_name", ""))
        staged[key] = stage_path

    missing = [row.get("file_name", "") for row in rows if
               (row.get("source_path", ""), row.get("file_name", "")) not in staged]
    if missing:
        raise SystemExit(f"staged upload files missing: {', '.join(missing[:5])}")
    return staged


def remote_file_map(args, year: str) -> dict[str, dict[str, object]]:
    command = [str(args.rclone), "lsjson", f"{args.remote}:{year}", "--files-only"]
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        return {}
    try:
        entries = json.loads(completed.stdout or "[]")
    except (TypeError, ValueError):
        return {}
    return {
        str(entry.get("Name", "")): entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("Name")
    }


def write_files_from(manifest_dir: Path, year: str, rows: list[dict[str, str]]) -> Path:
    path = manifest_dir / f"rclone_files_{year}.txt"
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(f"{row['file_name']}\n")
    return path


def upload_once(args, batch_id: str, cycle: int) -> int:
    ensure_runtime_health_fuse_clear(args.output_dir)
    next_batch = prepare_manifest(args, args.limit)
    rows = read_csv(next_batch)
    validate_prepared_manifest(args.manifest_dir, next_batch, rows)
    if not rows:
        print("[upload] no pending ready rows", flush=True)
        return 0

    by_year: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        year = row.get("year") or row.get("drive_folder") or "_needs_review"
        by_year.setdefault(year, []).append(row)

    uploaded_rows: list[dict[str, str]] = []
    staged_paths = load_staged_paths(args.manifest_dir, rows)
    staging_root = (args.manifest_dir / "staging").resolve()
    for year, year_rows in sorted(by_year.items(), reverse=True):
        ensure_runtime_health_fuse_clear(args.output_dir)
        year_stage = (staging_root / year).resolve()
        for row in year_rows:
            stage_path = staged_paths[(row.get("source_path", ""), row.get("file_name", ""))]
            if stage_path.parent != year_stage or stage_path.name != row.get("file_name", ""):
                raise SystemExit(f"invalid staged upload path: {stage_path}")
        files_from = write_files_from(args.manifest_dir, year, year_rows)
        command = [
            str(args.rclone), "copy", str(year_stage), f"{args.remote}:{year}",
            "--files-from-raw", str(files_from), "--ignore-existing",
            "--transfers", str(args.transfers), "--checkers", str(args.checkers),
            "--drive-stop-on-upload-limit", "--stats", "30s",
        ]
        if args.dry_run:
            command.append("--dry-run")
        rc = run_command(command, args.log_path, execute=args.execute, timeout_seconds=args.rclone_timeout_seconds)
        if rc not in {0, 124}:
            raise SystemExit(rc)

        remote_entries = remote_file_map(args, year) if args.execute and not args.dry_run else {}
        for row in year_rows:
            remote_entry = remote_entries.get(row.get("file_name", ""), {})
            if args.execute and not args.dry_run and not remote_entry:
                print(f"[upload] remote file not confirmed; leaving pending: {row.get('file_name', '')}", flush=True)
                continue
            uploaded_at = datetime.now().isoformat(timespec="seconds")
            uploaded_rows.append(
                {
                    "batch": f"{batch_id}_cycle{cycle:03d}",
                    "year": year,
                    "source_path": row.get("source_path", ""),
                    "file_name": row.get("file_name", ""),
                    "drive_folder_id": year,
                    "drive_file_id": str(remote_entry.get("ID", "")),
                    "url": f"rclone://{args.remote}/{year}/{row.get('file_name', '')}",
                    "uploaded_at": uploaded_at,
                    "uploader": "rclone",
                }
            )
        if rc == 124 and args.continue_on_timeout:
            if args.execute and not args.dry_run and uploaded_rows:
                append_uploaded(args.uploaded_log, uploaded_rows)
                prepare_manifest(args, args.limit)
            print("[upload] timeout treated as retryable; next cycle will resume with --ignore-existing", flush=True)
            return len(uploaded_rows)

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
    parser.add_argument("--limit", type=int, default=100)
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
    parser.add_argument(
        "--stop-on-timeout",
        dest="continue_on_timeout",
        action="store_false",
        help="Exit when one rclone batch times out. Default keeps retrying so unattended uploads do not stall.",
    )
    parser.set_defaults(continue_on_timeout=True)
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

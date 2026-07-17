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
import re
import shutil
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import psutil


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import build_upload_gate_proof as upload_gate_authority


DEFAULT_OUTPUT_DIR = Path(r"D:\00_商化\00_已OCR照片")
DEFAULT_BACKEND_URL = "http://127.0.0.1:5002"
DEFAULT_REMOTE = "samsung_ocr_drive"
CURRENT_YEAR = str(datetime.now().year)
HISTORICAL_AUTH_SCHEMA = "samsung-ocr-historical-upload-authorization/v1"
ACTIVE_OCR_RUNNERS = (
    "rerun_staged_candidates.py",
    "recursive_ocr_flat_export.py",
    "rerun_questionable_records.py",
)
RETRYABLE_TIMEOUT = -124
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


def ensure_upload_interlocks_clear(output_dir: Path) -> None:
    fuse = output_dir / "_ocr_audit" / "runtime_health_fuse.json"
    if fuse.exists():
        raise SystemExit(f"runtime health fuse is active; upload blocked: {fuse}")
    benchmark_lock = output_dir / "_ocr_audit" / "model_benchmark.lock"
    if benchmark_lock.exists():
        raise SystemExit(f"model benchmark/backfill lock is active; upload blocked: {benchmark_lock}")


def ensure_runtime_health_fuse_clear(output_dir: Path) -> None:
    """Backward-compatible alias for callers that used the old helper name."""
    ensure_upload_interlocks_clear(output_dir)


def ensure_backend_idle(backend_url: str) -> None:
    """A real upload requires a reachable backend that explicitly reports idle."""
    url = backend_url.rstrip("/") + "/api/status"
    try:
        request = Request(url, headers={"User-Agent": "samsung-ocr-safe-uploader/1"})
        with urlopen(request, timeout=10) as response:
            status = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError, URLError) as exc:
        raise SystemExit(f"backend idle state cannot be proven; upload blocked: {exc}") from exc
    if not isinstance(status, dict) or status.get("is_running") is not False:
        raise SystemExit("backend reports OCR running or an unknown state; upload blocked")


def ensure_no_active_ocr_runner(repo_root: Path = REPO_ROOT) -> None:
    """Fail closed when an owned OCR runner is alive, even between API jobs."""
    owned_root = str(repo_root.resolve()).lower()
    try:
        processes = psutil.process_iter(["pid", "cmdline"])
        for process in processes:
            try:
                command = " ".join(process.info.get("cmdline") or [])
            except psutil.NoSuchProcess:
                continue
            except (psutil.AccessDenied, psutil.ZombieProcess, OSError) as exc:
                raise SystemExit(f"OCR process inventory cannot be proven; upload blocked: {exc}") from exc
            lowered = command.lower()
            if owned_root in lowered and any(name in lowered for name in ACTIVE_OCR_RUNNERS):
                raise SystemExit(
                    f"active OCR runner detected; upload blocked: pid={process.info.get('pid')}"
                )
    except SystemExit:
        raise
    except (psutil.Error, OSError) as exc:
        raise SystemExit(f"OCR process inventory cannot be proven; upload blocked: {exc}") from exc


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
    stale_archive = None
    try:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            handle = os.open(str(lock_path), flags)
        except FileExistsError:
            try:
                text = lock_path.read_text(encoding="utf-8")
                match = re.search(r"(?m)^pid=(\d+)\s*$", text)
                owner_pid = int(match.group(1)) if match else 0
            except (OSError, UnicodeError, ValueError):
                owner_pid = 0
            if not owner_pid:
                raise SystemExit(f"upload lock has no verifiable owner; refusing recovery: {lock_path}")
            if psutil.pid_exists(owner_pid):
                raise SystemExit(f"upload already appears to be running; lock exists: {lock_path}")
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            stale_archive = lock_path.with_name(
                f"{lock_path.name}.stale.{owner_pid}.{stamp}"
            )
            try:
                os.replace(lock_path, stale_archive)
                handle = os.open(str(lock_path), flags)
            except FileExistsError:
                raise SystemExit(f"upload lock was reacquired during stale recovery: {lock_path}")
            except FileNotFoundError:
                raise SystemExit(f"upload lock changed during stale recovery: {lock_path}")
        os.write(handle, f"pid={os.getpid()}\nstarted={datetime.now().isoformat(timespec='seconds')}\n".encode("utf-8"))
        yield
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
        "--manifest-dir",
        str(args.manifest_dir),
        "--uploaded-log",
        str(args.uploaded_log),
    ]
    if args.years:
        command.extend(["--years", args.years])
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


def md5_file(path: Path) -> str:
    digest = hashlib.md5()
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


def validate_requested_scope(args, rows: list[dict[str, str]]) -> None:
    if args.limit > 0 and len(rows) > args.limit:
        raise SystemExit("prepared upload batch exceeds the requested batch limit")
    requested = {value.strip() for value in str(args.years or "").split(",") if value.strip()}
    if requested and any(
        str(row.get("year") or row.get("drive_folder") or "").strip() not in requested
        for row in rows
    ):
        raise SystemExit("prepared upload batch contains a year outside the requested scope")


def refresh_and_validate_shared_upload_gate(
    output_dir: Path,
    manifest_dir: Path,
    next_batch: Path,
    rows: list[dict[str, str]],
) -> dict:
    """Rebuild and verify the shared proof before any real upload.

    This deliberately applies to historical-only batches too.  A caller may
    not bypass current-year finalization merely by invoking this executable
    directly or by selecting a non-2026 batch.
    """
    canonical_manifest_dir = (output_dir / "_drive_upload").resolve()
    if manifest_dir.resolve() != canonical_manifest_dir:
        raise SystemExit("real uploads require the canonical output _drive_upload directory")

    ensure_upload_interlocks_clear(output_dir)
    result = upload_gate_authority.run(output_dir, 2026, execute=True)
    if result.get("valid") is not True or result.get("executed") is not True:
        errors = ",".join(str(item) for item in result.get("errors") or [])
        raise SystemExit(f"shared upload gate proof is not valid: {errors or 'unknown error'}")

    proof_path = manifest_dir / "upload_gate_proof.json"
    try:
        proof = json.loads(proof_path.read_text(encoding="utf-8-sig"))
    except (OSError, TypeError, ValueError) as exc:
        raise SystemExit(f"shared upload gate proof unreadable: {exc}") from exc
    if not isinstance(proof, dict):
        raise SystemExit("shared upload gate proof must be a JSON object")
    if proof.get("schema") != upload_gate_authority.SCHEMA or proof.get("gate_open") is not True:
        raise SystemExit("shared upload gate proof is closed or has the wrong schema")
    readback_errors = upload_gate_authority.validate_proof_snapshot(output_dir, 2026, proof)
    if readback_errors:
        proof_path.unlink(missing_ok=True)
        raise SystemExit(
            "shared upload gate proof authorities changed during validation: "
            + ",".join(readback_errors)
        )

    try:
        generated_at = datetime.fromisoformat(str(proof.get("generated_at") or ""))
        if generated_at.tzinfo is None:
            raise ValueError("timezone missing")
        age_seconds = (datetime.now(generated_at.tzinfo) - generated_at).total_seconds()
    except (TypeError, ValueError) as exc:
        raise SystemExit("shared upload gate proof timestamp is invalid") from exc
    if age_seconds < -60 or age_seconds > 15 * 60:
        raise SystemExit("shared upload gate proof is stale")

    expected_paths = {
        "manifest_summary_path": manifest_dir / "drive_upload_summary.json",
        "pending_csv_path": manifest_dir / "drive_upload_ready_pending.csv",
        "next_batch_csv_path": next_batch,
    }
    expected_hash_fields = {
        "manifest_summary_path": "manifest_summary_sha256",
        "pending_csv_path": "pending_sha256",
        "next_batch_csv_path": "next_batch_sha256",
    }
    for path_field, expected_path in expected_paths.items():
        try:
            proof_path_value = Path(str(proof.get(path_field) or "")).resolve()
        except (OSError, TypeError, ValueError) as exc:
            raise SystemExit(f"shared upload gate proof path is invalid: {path_field}") from exc
        expected_path = expected_path.resolve()
        if proof_path_value != expected_path or not expected_path.is_file():
            raise SystemExit(f"shared upload gate proof path mismatch: {path_field}")
        expected_hash = str(proof.get(expected_hash_fields[path_field]) or "").lower()
        if not _is_sha256(expected_hash) or expected_hash != sha256_file(expected_path):
            raise SystemExit(f"shared upload gate proof hash mismatch: {path_field}")

    try:
        pending_count = int(proof.get("pending_count"))
        next_batch_count = int(proof.get("next_batch_count"))
    except (TypeError, ValueError) as exc:
        raise SystemExit("shared upload gate proof counts are invalid") from exc
    pending_rows = read_csv(manifest_dir / "drive_upload_ready_pending.csv")
    if pending_count != len(pending_rows):
        raise SystemExit("shared upload gate proof pending count does not match the pending ledger")
    if next_batch_count != len(rows):
        raise SystemExit("shared upload gate proof next-batch count does not match this batch")
    return proof


def validate_historical_upload_authorization(
    output_dir: Path,
    rows: list[dict[str, str]],
    shared_proof: dict,
) -> None:
    """Require current-year completion and inventory-bound all-year approval."""
    if not any(str(row.get("year") or row.get("drive_folder") or "") != CURRENT_YEAR for row in rows):
        return

    audit_dir = output_dir / "_ocr_audit"
    authorization_path = audit_dir / "historical_upload_authorization.json"
    current_marker_path = audit_dir / "current_year_rerun_cycle_complete.json"
    discovery_path = audit_dir / "folder_discovery.csv"
    summary_path = audit_dir / "folder_summary.csv"
    inventory_csv_path = audit_dir / "source_inventory_v1.csv"
    inventory_summary_path = audit_dir / "source_inventory_v1.json"
    try:
        authorization = json.loads(authorization_path.read_text(encoding="utf-8-sig"))
        current_marker = json.loads(current_marker_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        raise SystemExit(f"historical upload authorization is missing or unreadable: {exc}") from exc
    if not isinstance(authorization, dict) or authorization.get("schema") != HISTORICAL_AUTH_SCHEMA:
        raise SystemExit("historical upload authorization has the wrong schema")
    if authorization.get("all_year_questionable_review") is not True:
        raise SystemExit("historical upload authorization lacks all-year questionable review")
    if not isinstance(current_marker, dict) or int(current_marker.get("pending_count", -1)) != 0:
        raise SystemExit("current-year completion marker is missing an exact zero-pending proof")

    canonical_paths = {
        "current_year_marker_path": current_marker_path,
        "folder_discovery_path": discovery_path,
        "folder_summary_path": summary_path,
        "source_inventory_csv_path": inventory_csv_path,
        "source_inventory_summary_path": inventory_summary_path,
    }
    hash_fields = {
        "current_year_marker_path": "current_year_marker_sha256",
        "folder_discovery_path": "folder_discovery_sha256",
        "folder_summary_path": "folder_summary_sha256",
        "source_inventory_csv_path": "source_inventory_csv_sha256",
        "source_inventory_summary_path": "source_inventory_summary_sha256",
    }
    for field, canonical in canonical_paths.items():
        if Path(str(authorization.get(field) or "")).resolve() != canonical.resolve():
            raise SystemExit(f"historical upload authorization path mismatch: {field}")
        if not canonical.is_file() or str(authorization.get(hash_fields[field]) or "").lower() != sha256_file(canonical):
            raise SystemExit(f"historical upload authorization hash mismatch: {field}")

    for field in ("upload_gate_schema", "audit_input_sha256", "backfill_run_id"):
        marker_value = str(current_marker.get(field) or "")
        proof_field = "schema" if field == "upload_gate_schema" else field
        if not marker_value or marker_value != str(shared_proof.get(proof_field) or ""):
            raise SystemExit(f"current-year completion marker no longer matches the shared proof: {field}")

    try:
        discovered = read_csv(discovery_path)
        summaries = read_csv(summary_path)
        inventory_rows = read_csv(inventory_csv_path)
        inventory_summary = json.loads(inventory_summary_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, TypeError, ValueError, csv.Error) as exc:
        raise SystemExit(f"historical inventory cannot be read: {exc}") from exc
    discovered_keys = [str(row.get("folder") or "") for row in discovered]
    summary_keys = [str(row.get("folder") or "") for row in summaries]
    summary_by_folder = {key: row for key, row in zip(summary_keys, summaries) if key}
    inventory_sha256 = sha256_file(inventory_csv_path)
    if (
        not isinstance(inventory_summary, dict)
        or inventory_summary.get("schema") != "samsung-ocr-source-inventory/v1"
        or str(inventory_summary.get("inventory_csv_sha256") or "") != inventory_sha256
        or int(inventory_summary.get("row_count", -1)) != len(inventory_rows)
        or int(authorization.get("source_inventory_row_count", -1)) != len(inventory_rows)
        or int(authorization.get("source_inventory_folder_count", -1)) != int(inventory_summary.get("folder_count", -1))
    ):
        raise SystemExit("historical per-photo inventory is incomplete or changed")
    if (
        not discovered
        or any(not key for key in discovered_keys + summary_keys)
        or len(set(discovered_keys)) != len(discovered_keys)
        or len(set(summary_keys)) != len(summary_keys)
        or set(discovered_keys) != set(summary_keys)
    ):
        raise SystemExit("historical inventory is incomplete or duplicated")
    try:
        discovered_image_count = sum(int(row.get("image_count") or 0) for row in discovered)
    except ValueError as exc:
        raise SystemExit("historical discovery image counts are invalid") from exc
    if int(inventory_summary.get("folder_count", -1)) != len(discovered) or len(inventory_rows) != discovered_image_count:
        raise SystemExit("historical discovery does not cover the exact per-photo inventory")
    for folder in discovered:
        key = str(folder.get("folder") or "")
        row = summary_by_folder.get(key)
        if not row or str(row.get("status") or "") not in {"copied", "skipped_existing"}:
            raise SystemExit(f"historical folder is incomplete or blocked: {key}")
        if not str(folder.get("folder_id") or "") or str(row.get("folder_id") or "") != str(folder.get("folder_id") or ""):
            raise SystemExit(f"historical folder identity changed: {key}")
        if str(folder.get("source_inventory_sha256") or "") != inventory_sha256 or str(row.get("source_inventory_sha256") or "") != inventory_sha256:
            raise SystemExit(f"historical folder inventory hash changed: {key}")
        if str(row.get("image_count") or "") != str(folder.get("image_count") or ""):
            raise SystemExit(f"historical folder image count changed: {key}")
        if str(row.get("source_latest_mtime") or "") != str(folder.get("latest_mtime") or ""):
            raise SystemExit(f"historical folder source identity changed: {key}")
        try:
            counts = [int(row.get(field) or 0) for field in ("image_count", "success_records", "copied_count")]
            errors = [int(row.get(field) or 0) for field in ("missing_result", "missing_source", "conflict", "failed")]
        except ValueError as exc:
            raise SystemExit(f"historical folder counts are invalid: {key}") from exc
        if counts[0] <= 0 or len(set(counts)) != 1 or any(errors) or str(row.get("copy_error") or "").strip():
            raise SystemExit(f"historical folder completion contract failed: {key}")
    if int(authorization.get("discovered_folder_count", -1)) != len(discovered):
        raise SystemExit("historical authorization discovered-folder count mismatch")
    if int(authorization.get("completed_folder_count", -1)) != len(discovered):
        raise SystemExit("historical authorization completed-folder count mismatch")
    if int(authorization.get("error_count", -1)) != 0:
        raise SystemExit("historical authorization reports folder errors")


def ensure_real_upload_configuration(args) -> None:
    if not (args.execute and not args.dry_run):
        return
    canonical_manifest = (args.output_dir / "_drive_upload").resolve()
    canonical_uploaded = canonical_manifest / "drive_upload_uploaded.csv"
    if args.manifest_dir.resolve() != canonical_manifest:
        raise SystemExit("real uploads require the canonical output _drive_upload directory")
    if args.uploaded_log.resolve() != canonical_uploaded.resolve():
        raise SystemExit("real uploads require the canonical uploaded receipt ledger")
    if args.remote != DEFAULT_REMOTE:
        raise SystemExit(f"real uploads require the approved rclone remote: {DEFAULT_REMOTE}")
    if args.backend_url.rstrip("/") != DEFAULT_BACKEND_URL:
        raise SystemExit(f"real uploads require the canonical backend health check: {DEFAULT_BACKEND_URL}")


def invalidate_shared_upload_gate(manifest_dir: Path) -> None:
    """Remove a proof after the uploaded ledger changes its bound manifest."""
    (manifest_dir / "upload_gate_proof.json").unlink(missing_ok=True)


def load_staged_paths(manifest_dir: Path, rows: list[dict[str, str]]) -> dict[tuple[str, str], Path]:
    """Return only staged files belonging to this manifest batch."""
    staging_root = (manifest_dir / "staging").resolve()
    map_rows = read_csv(manifest_dir / "staging_map.csv")
    staged: dict[tuple[str, str], Path] = {}
    expected_rows = {
        (row.get("source_path", ""), row.get("file_name", "")): row for row in rows
    }
    for item in map_rows:
        stage_path = Path(item.get("stage_file", "")).resolve()
        try:
            stage_path.relative_to(staging_root)
        except ValueError:
            continue
        if not stage_path.is_file():
            continue
        key = (item.get("source_path", ""), item.get("file_name", ""))
        expected_hash = str(expected_rows.get(key, {}).get("content_sha256") or "").lower()
        if not _is_sha256(expected_hash):
            raise SystemExit(f"staged upload content hash is missing: {stage_path.name}")
        if str(item.get("content_sha256") or "").lower() != expected_hash:
            raise SystemExit(f"staging map content hash mismatch: {stage_path.name}")
        if sha256_file(stage_path) != expected_hash:
            raise SystemExit(f"staged upload bytes do not match the authorized manifest: {stage_path.name}")
        staged[key] = stage_path

    missing = [row.get("file_name", "") for row in rows if
               (row.get("source_path", ""), row.get("file_name", "")) not in staged]
    if missing:
        raise SystemExit(f"staged upload files missing: {', '.join(missing[:5])}")
    return staged


def remote_file_map(args, year: str) -> dict[str, list[dict[str, object]]]:
    command = [str(args.rclone), "lsjson", f"{args.remote}:{year}", "--files-only", "--hash"]
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=args.rclone_timeout_seconds if args.rclone_timeout_seconds > 0 else None,
        )
    except subprocess.TimeoutExpired as exc:
        raise SystemExit("remote Drive readback timed out; upload remains unconfirmed") from exc
    if completed.returncode != 0:
        raise SystemExit(f"remote Drive readback failed with code {completed.returncode}")
    try:
        entries = json.loads(completed.stdout or "[]")
    except (TypeError, ValueError):
        raise SystemExit("remote Drive readback returned invalid JSON")
    if not isinstance(entries, list):
        raise SystemExit("remote Drive readback did not return a file list")
    mapped: dict[str, list[dict[str, object]]] = {}
    for entry in entries:
        if isinstance(entry, dict) and entry.get("Name"):
            mapped.setdefault(str(entry["Name"]), []).append(entry)
    return mapped


def confirmed_remote_entry(stage_path: Path, entries: list[dict[str, object]]) -> dict[str, object] | None:
    """Accept exactly one remote object with the staged file's size and MD5."""
    if len(entries) != 1:
        return None
    entry = entries[0]
    hashes = entry.get("Hashes")
    try:
        remote_size = int(entry.get("Size"))
    except (TypeError, ValueError):
        return None
    if not isinstance(hashes, dict):
        return None
    remote_md5 = str(hashes.get("MD5") or hashes.get("md5") or "").lower()
    if remote_size != stage_path.stat().st_size or remote_md5 != md5_file(stage_path):
        return None
    return entry


def write_files_from(manifest_dir: Path, year: str, rows: list[dict[str, str]]) -> Path:
    path = manifest_dir / f"rclone_files_{year}.txt"
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(f"{row['file_name']}\n")
    return path


def upload_once(args, batch_id: str, cycle: int) -> int:
    ensure_real_upload_configuration(args)
    ensure_upload_interlocks_clear(args.output_dir)
    if args.execute and not args.dry_run:
        ensure_backend_idle(args.backend_url)
        ensure_no_active_ocr_runner()
    next_batch = prepare_manifest(args, args.limit)
    rows = read_csv(next_batch)
    validate_prepared_manifest(args.manifest_dir, next_batch, rows)
    validate_requested_scope(args, rows)
    shared_proof: dict = {}
    if args.execute and not args.dry_run:
        shared_proof = refresh_and_validate_shared_upload_gate(
            args.output_dir, args.manifest_dir, next_batch, rows
        )
        validate_historical_upload_authorization(args.output_dir, rows, shared_proof)
        ensure_backend_idle(args.backend_url)
        ensure_no_active_ocr_runner()
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
        ensure_upload_interlocks_clear(args.output_dir)
        if args.execute and not args.dry_run:
            ensure_backend_idle(args.backend_url)
            ensure_no_active_ocr_runner()
            shared_proof = refresh_and_validate_shared_upload_gate(
                args.output_dir, args.manifest_dir, next_batch, rows
            )
            validate_historical_upload_authorization(args.output_dir, rows, shared_proof)
        year_stage = (staging_root / year).resolve()
        for row in year_rows:
            stage_path = staged_paths[(row.get("source_path", ""), row.get("file_name", ""))]
            if stage_path.parent != year_stage or stage_path.name != row.get("file_name", ""):
                raise SystemExit(f"invalid staged upload path: {stage_path}")
            expected_hash = str(row.get("content_sha256") or "").lower()
            if not _is_sha256(expected_hash) or sha256_file(stage_path) != expected_hash:
                raise SystemExit(f"staged upload content changed after proof validation: {stage_path}")
        files_from = write_files_from(args.manifest_dir, year, year_rows)
        command = [
            str(args.rclone), "copy", str(year_stage), f"{args.remote}:{year}",
            "--files-from-raw", str(files_from), "--ignore-existing",
            "--transfers", str(args.transfers), "--checkers", str(args.checkers),
            "--drive-stop-on-upload-limit", "--stats", "30s",
        ]
        if args.dry_run:
            command.append("--dry-run")
        if args.execute and not args.dry_run:
            ensure_upload_interlocks_clear(args.output_dir)
            ensure_backend_idle(args.backend_url)
            ensure_no_active_ocr_runner()
            for row in year_rows:
                stage_path = staged_paths[(row.get("source_path", ""), row.get("file_name", ""))]
                if sha256_file(stage_path) != str(row.get("content_sha256") or "").lower():
                    raise SystemExit(f"staged upload content changed immediately before copy: {stage_path}")
        rc = run_command(command, args.log_path, execute=args.execute, timeout_seconds=args.rclone_timeout_seconds)
        if rc not in {0, 124}:
            raise SystemExit(rc)

        remote_entries = remote_file_map(args, year) if args.execute and not args.dry_run else {}
        unconfirmed: list[str] = []
        for row in year_rows:
            stage_path = staged_paths[(row.get("source_path", ""), row.get("file_name", ""))]
            if args.execute and not args.dry_run:
                remote_entry = confirmed_remote_entry(
                    stage_path, remote_entries.get(row.get("file_name", ""), [])
                )
                if not remote_entry:
                    unconfirmed.append(row.get("file_name", ""))
                    print(
                        f"[upload] remote size/MD5 not confirmed; leaving pending: {row.get('file_name', '')}",
                        flush=True,
                    )
                    continue
            else:
                remote_entry = {}
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
        if rc == 124:
            if args.execute and not args.dry_run and uploaded_rows:
                append_uploaded(args.uploaded_log, uploaded_rows)
                prepare_manifest(args, args.limit)
                invalidate_shared_upload_gate(args.manifest_dir)
            if not args.continue_on_timeout:
                raise SystemExit(124)
            print("[upload] timeout treated as retryable; next cycle will resume with --ignore-existing", flush=True)
            return RETRYABLE_TIMEOUT
        if unconfirmed:
            if args.execute and not args.dry_run and uploaded_rows:
                append_uploaded(args.uploaded_log, uploaded_rows)
                prepare_manifest(args, args.limit)
                invalidate_shared_upload_gate(args.manifest_dir)
            raise SystemExit(
                f"remote content confirmation failed for {len(unconfirmed)} file(s); upload remains pending"
            )

    if args.execute and not args.dry_run:
        append_uploaded(args.uploaded_log, uploaded_rows)
        # Refresh manifest summary after logging successful uploads.
        prepare_manifest(args, args.limit)
        invalidate_shared_upload_gate(args.manifest_dir)
    print(f"[upload] cycle={cycle} uploaded_or_existing={len(uploaded_rows)}", flush=True)
    return len(uploaded_rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload ready OCR photos to Google Drive year folders using rclone.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--manifest-dir", default="")
    parser.add_argument("--uploaded-log", default="")
    parser.add_argument("--rclone", default="")
    parser.add_argument("--remote", default=DEFAULT_REMOTE)
    parser.add_argument("--backend-url", default=DEFAULT_BACKEND_URL)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--years", default="", help="Optional comma-separated upload years; current-year phase uses 2026.")
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
            if uploaded == RETRYABLE_TIMEOUT:
                if not args.repeat or (args.max_cycles and cycle >= args.max_cycles):
                    return 124
                cycle += 1
                time.sleep(max(1, args.sleep_seconds))
                continue
            if not args.repeat or uploaded <= 0:
                break
            cycle += 1
            if args.max_cycles and cycle > args.max_cycles:
                break
            time.sleep(max(1, args.sleep_seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

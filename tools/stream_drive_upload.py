"""Per-photo durable Drive uploader for finalized OCR results.

OCR only writes a small atomic outbox job.  A separate single worker publishes
and uploads one photo at a time, then requires an exact unique size+MD5 remote
readback before writing either receipt ledger.  The legacy bulk uploader and
its whole-batch gates remain unchanged.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.audit_fields import EVIDENCE_GUARD_REVISION, validate_evidence_contract
from tools.photo_rename_planner import (
    READY_STATUS,
    copy_planned_image_idempotent,
    plan_single_image,
)
from tools.rclone_drive_upload import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_REMOTE,
    UPLOAD_HEADERS,
    confirmed_remote_entry,
    md5_file,
    read_csv,
    resolve_rclone,
    sha256_file,
    single_instance_lock,
)


STREAM_SCHEMA = "samsung-ocr-stream-upload-v1"
RECEIPT_SCHEMA = "samsung-ocr-stream-receipt-v1"
APPROVED_DRIVE_ROOT_ID = "16X5qALC3zRYc7PpnexXLYprorBzBtT_f"
DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"
_YEAR_FOLDER_ID_CACHE: dict[tuple[str, str, str], str] = {}
COMPATIBLE_PENDING_REVISION_MIGRATIONS = {
    # Historical .49/.50 jobs were compatible with .51 because those revisions
    # changed transport containment only. .52 changes model identity and
    # FollowMe finalization, so no earlier queued result may migrate to .52.
    "20260718.49": "20260718.51",
    "20260718.50": "20260718.51",
}


def _truthy(value: object) -> bool:
    return value is True or str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    # On Windows a dashboard/status reader can briefly hold the destination
    # file without delete sharing.  Treat that as a transient presentation
    # collision, not as a reason to terminate the durable upload worker.
    for attempt in range(8):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            if attempt >= 7:
                raise
            time.sleep(0.05 * (attempt + 1))


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return payload


def _stream_dirs(output_dir: Path) -> dict[str, Path]:
    root = Path(output_dir).resolve() / "_drive_upload_stream"
    dirs = {
        "root": root,
        "pending": root / "pending",
        "working": root / "working",
        "failed": root / "failed",
        "receipts": root / "receipts",
        "superseded_receipts": root / "superseded_receipts",
        "revision_migrations": root / "revision_migrations",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def refresh_status(output_dir: Path, **updates: Any) -> dict[str, Any]:
    dirs = _stream_dirs(output_dir)
    status_path = dirs["root"] / "status.json"
    try:
        previous = _read_json(status_path)
    except (OSError, UnicodeError, ValueError, TypeError, RuntimeError):
        previous = {}
    if "canonical_uploaded" not in previous:
        previous["canonical_uploaded"] = len(read_csv(
            Path(output_dir).resolve() / "_drive_upload" / "drive_upload_uploaded.csv"
        ))
    status = {
        **previous,
        "schema": STREAM_SCHEMA,
        "pending": len(list(dirs["pending"].glob("*.json"))),
        "working": len(list(dirs["working"].glob("*.json"))),
        "failed": len(list(dirs["failed"].glob("*.json"))),
        "uploaded": len(list(dirs["receipts"].glob("*.json"))),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        **updates,
    }
    _atomic_json(status_path, status)
    return status


def read_stream_status(output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    dirs = _stream_dirs(output_dir)
    try:
        return _read_json(dirs["root"] / "status.json")
    except (OSError, UnicodeError, ValueError, TypeError, RuntimeError):
        return refresh_status(output_dir)


def _job_key(result: Mapping[str, Any]) -> str:
    source_item_id = str(result.get("source_item_id") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", source_item_id):
        raise RuntimeError("finalized result has no valid source_item_id")
    return source_item_id


def enqueue_finalized_result(
    result: Mapping[str, Any],
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    price_symbol: str = "＄",
) -> Path | None:
    """Atomically enqueue one verified result without doing any network I/O."""
    row = dict(result or {})
    if not _truthy(row.get("auto_verified")) or _truthy(row.get("auto_review_required")):
        return None
    if str(row.get("evidence_guard_revision") or "") != EVIDENCE_GUARD_REVISION:
        return None
    if row.get("independent_pass") is not True:
        return None
    if row.get("request_binding_enforced") is not True or row.get("request_id_verified") is not True:
        return None
    if row.get("prior_answer_exposed") is True or row.get("prompt_contamination") is True:
        return None
    runtime = row.get("runtime_health") or {}
    if not isinstance(runtime, dict) or runtime.get("healthy") is not True:
        return None
    contract_valid, contract_errors, normalized = validate_evidence_contract(row)
    if not contract_valid:
        raise RuntimeError("verified result failed evidence contract: " + ";".join(contract_errors))

    source = Path(str(row.get("original_source_path") or row.get("source_path") or "")).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    period = str(row.get("period") or "").strip()
    if not re.fullmatch(r"20\d{4}", period):
        raise RuntimeError("finalized result has no valid YYYYMM period")
    year = period[:4]
    key = _job_key(row)
    plan = plan_single_image(source, row, period, price_symbol, current_year=datetime.now().year)
    if plan.get("status") != READY_STATUS:
        raise RuntimeError(plan.get("reason") or "single-photo plan is not ready")

    dirs = _stream_dirs(output_dir)
    pending = dirs["pending"] / f"{key}.json"
    working = dirs["working"] / pending.name
    receipt = dirs["receipts"] / pending.name
    job = {
        "schema": STREAM_SCHEMA,
        "source_item_id": key,
        "original_source_path": str(source),
        "source_sha256": sha256_file(source),
        "input_image_sha256": str(row.get("input_image_sha256") or ""),
        "period": period,
        "year": year,
        "target_name": plan["target_name"],
        "plan": plan,
        "final_result": {
            field: row.get(field)
            for field in (
                "view_type", "category", "model", "price", "price_symbol", "price_status",
                "official_price", "price_diff_percent", "screen_status", "quality_issue",
                "complete_screen_count", "unique_main", "label_ownership",
                "followme_physical_evidence", "followme_family_confirmed",
                "three_pass_adjudicated", "adjudication_rule",
            )
        },
        "run_id": str(row.get("run_id") or ""),
        "ocr_attempt": int(row.get("ocr_attempt") or 1),
        "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
        "normalized_evidence": normalized,
        "queued_at": datetime.now().isoformat(timespec="seconds"),
    }
    canonical = json.dumps(job, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    for existing_path in (pending, working):
        if existing_path.exists():
            existing = _read_json(existing_path)
            existing_canonical = json.dumps(existing, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if existing_canonical == canonical:
                return existing_path
            raise RuntimeError(f"same source_item_id already has a different upload job: {key}")
    if receipt.exists():
        previous_receipt = _read_json(receipt)
        receipt_is_current = bool(
            previous_receipt.get("schema") == RECEIPT_SCHEMA
            and previous_receipt.get("evidence_guard_revision") == EVIDENCE_GUARD_REVISION
            and previous_receipt.get("source_sha256") == job["source_sha256"]
            and previous_receipt.get("file_name") == job["target_name"]
        )
        if receipt_is_current:
            return receipt
        # A receipt proves only the exact revision/name/bytes it recorded.  It
        # must never suppress a corrected result for the same source identity.
        # Preserve the old proof for audit, then enqueue the new revision.
        old_revision = re.sub(
            r"[^0-9A-Za-z._-]", "_",
            str(previous_receipt.get("evidence_guard_revision") or "legacy"),
        )
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        archived = dirs["superseded_receipts"] / f"{key}.{old_revision}.{stamp}.json"
        os.replace(receipt, archived)
        job["superseded_receipt"] = {
            "archived_path": str(archived),
            "evidence_guard_revision": previous_receipt.get("evidence_guard_revision"),
            "file_name": previous_receipt.get("file_name"),
            "remote_path": previous_receipt.get("remote_path"),
            "drive_file_id": previous_receipt.get("drive_file_id"),
        }
    _atomic_json(pending, job)
    refresh_status(output_dir, last_queued_file=plan["target_name"])
    return pending


def _runtime_fuse_clear(output_dir: Path) -> None:
    fuse = Path(output_dir).resolve() / "_ocr_audit" / "runtime_health_fuse.json"
    if fuse.exists():
        raise RuntimeError(f"runtime health fuse is active: {fuse}")


def remote_stat_exact(
    rclone: Path,
    remote: str,
    year: str,
    file_name: str,
    *,
    timeout_seconds: int = 180,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> list[dict[str, Any]]:
    if not re.fullmatch(r"20\d{2}", str(year)) or Path(file_name).name != file_name:
        raise RuntimeError("unsafe remote target")
    if any(char in file_name for char in ("\r", "\n", "\x00")):
        raise RuntimeError("unsafe remote filename")

    def query_literal(value: str) -> str:
        return value.replace("\\", "\\\\").replace("'", "\\'")

    def drive_query(query: str) -> list[dict[str, Any]]:
        command = [str(rclone), "backend", "query", f"{remote}:", query]
        try:
            completed = runner(
                command,
                text=True,
                encoding="utf-8",
                errors="strict",
                capture_output=True,
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("exact remote readback timed out") from exc
        if completed.returncode != 0:
            raise RuntimeError(f"exact remote readback failed: rc={completed.returncode}")
        try:
            payload = json.loads(completed.stdout or "null")
        except (TypeError, ValueError) as exc:
            raise RuntimeError("exact remote readback returned invalid JSON") from exc
        if payload in (None, []):
            return []
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise RuntimeError("exact remote readback returned invalid objects")
        return payload

    cache_key = (str(Path(rclone)), remote, str(year))
    year_folder_id = _YEAR_FOLDER_ID_CACHE.get(cache_key)
    if not year_folder_id:
        year_query = (
            f"'{APPROVED_DRIVE_ROOT_ID}' in parents "
            f"and name = '{query_literal(str(year))}' "
            f"and mimeType = '{DRIVE_FOLDER_MIME}' and trashed = false"
        )
        folders = drive_query(year_query)
        exact_folders = [
            item for item in folders
            if str(item.get("name") or "") == str(year)
            and str(item.get("mimeType") or "") == DRIVE_FOLDER_MIME
            and APPROVED_DRIVE_ROOT_ID in list(item.get("parents") or [])
            and str(item.get("id") or "")
        ]
        if len(exact_folders) != 1:
            raise RuntimeError("year folder is missing or duplicated under approved Drive root")
        year_folder_id = str(exact_folders[0]["id"])
        _YEAR_FOLDER_ID_CACHE[cache_key] = year_folder_id

    # Drive permits duplicate display names.  The backend query returns every
    # exact-name object under the immutable year-folder ID, unlike
    # `lsjson path --stat`, which may select one arbitrary duplicate.  This
    # avoids scanning the entire year folder for every photo while retaining
    # duplicate detection, size, MD5 and Drive ID.
    file_query = (
        f"'{query_literal(year_folder_id)}' in parents "
        f"and name = '{query_literal(file_name)}' and trashed = false"
    )
    payload = drive_query(file_query)
    entries: list[dict[str, Any]] = []
    for item in payload:
        if (
            str(item.get("name") or "") != file_name
            or year_folder_id not in list(item.get("parents") or [])
            or str(item.get("mimeType") or "") == DRIVE_FOLDER_MIME
        ):
            continue
        try:
            size = int(item.get("size"))
        except (TypeError, ValueError):
            raise RuntimeError("exact remote object has no valid size")
        entries.append({
            "Name": file_name,
            "Path": file_name,
            "Size": size,
            "Hashes": {"MD5": str(item.get("md5Checksum") or "")},
            "ID": str(item.get("id") or ""),
        })
    return entries


def _append_uploaded_atomic(path: Path, row: dict[str, str]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_csv(path)
    key = (row.get("source_path", ""), row.get("file_name", ""))
    matching_index = next(
        (
            index
            for index, item in enumerate(existing)
            if (item.get("source_path", ""), item.get("file_name", "")) == key
        ),
        None,
    )
    if matching_index is None:
        row = dict(row)
        row["index"] = str(len(existing) + 1)
        existing.append(row)
    else:
        # A fresh exact Drive readback supersedes stale legacy metadata for the
        # same source and deterministic filename.  Keep the stable row index,
        # but refresh hashes, receipt time and Drive ID atomically.
        replacement = dict(row)
        replacement["index"] = str(existing[matching_index].get("index") or matching_index + 1)
        existing[matching_index] = replacement
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=UPLOAD_HEADERS, extrasaction="ignore")
        writer.writeheader()
        for item in existing:
            writer.writerow({header: item.get(header, "") for header in UPLOAD_HEADERS})
    os.replace(temp, path)
    return len(existing)


def _copy_remote(
    rclone: Path,
    published: Path,
    remote: str,
    year: str,
    file_name: str,
    *,
    timeout_seconds: int,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> None:
    command = [
        str(rclone), "copyto", str(published), f"{remote}:{year}/{file_name}",
        # Windows permits the literal full-width question mark used for the
        # unknown-price badge. Rclone's default local encoder treats that
        # glyph as an escape for the forbidden ASCII '?', making a real file
        # appear missing. Preserve the exact local Unicode filename.
        "--local-encoding", "None",
        # The exact deterministic name may already contain a stale result from
        # an earlier OCR revision.  copyto must replace that object in place;
        # --ignore-existing would silently preserve the wrong bytes.
        "--drive-stop-on-upload-limit", "--transfers", "1", "--checkers", "1",
    ]
    try:
        completed = runner(
            command,
            text=True,
            encoding="utf-8",
            errors="strict",
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("single-photo upload timed out") from exc
    if completed.returncode != 0:
        raise RuntimeError(f"single-photo upload failed: rc={completed.returncode}")


def process_one_job(
    job_path: Path,
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    remote: str = DEFAULT_REMOTE,
    rclone: Path | None = None,
    timeout_seconds: int = 600,
    readback_attempts: int = 5,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict[str, Any]:
    output_dir = Path(output_dir).resolve()
    dirs = _stream_dirs(output_dir)
    job = _read_json(job_path)
    if job.get("schema") != STREAM_SCHEMA or job.get("evidence_guard_revision") != EVIDENCE_GUARD_REVISION:
        raise RuntimeError("stale or invalid stream upload job")
    source = Path(str(job.get("original_source_path") or "")).resolve()
    if not source.is_file() or sha256_file(source) != str(job.get("source_sha256") or ""):
        raise RuntimeError("source bytes changed after OCR finalization")
    if remote != DEFAULT_REMOTE:
        raise RuntimeError("unapproved Drive remote")
    published_row = copy_planned_image_idempotent(job["plan"], output_dir)
    published = Path(published_row["target_path"])
    year = str(job["year"])
    file_name = str(job["target_name"])
    rclone_path = Path(rclone) if rclone else resolve_rclone("")
    _runtime_fuse_clear(output_dir)

    lock_path = output_dir / "_drive_upload" / "rclone_drive_upload.lock"
    uploaded_log = output_dir / "_drive_upload" / "drive_upload_uploaded.csv"
    with single_instance_lock(lock_path):
        _runtime_fuse_clear(output_dir)
        entries = remote_stat_exact(
            rclone_path, remote, year, file_name,
            timeout_seconds=timeout_seconds, runner=runner,
        )
        if len(entries) > 1:
            raise RuntimeError("duplicate exact remote names require ID-scoped cleanup before upload")
        confirmed = confirmed_remote_entry(published, entries)
        if not confirmed:
            # A same-name object with different bytes is an obsolete OCR
            # result, not a reason to abandon this photo.  Replace the exact
            # deterministic path, then require a fresh size+MD5 readback.
            _copy_remote(
                rclone_path, published, remote, year, file_name,
                timeout_seconds=timeout_seconds, runner=runner,
            )
            # Drive metadata is eventually consistent after an in-place
            # update.  Never write a receipt from the copy return code alone;
            # retry fresh folder listings until exact unique size+MD5 appears.
            for attempt in range(max(1, int(readback_attempts))):
                entries = remote_stat_exact(
                    rclone_path, remote, year, file_name,
                    timeout_seconds=timeout_seconds, runner=runner,
                )
                confirmed = confirmed_remote_entry(published, entries)
                if confirmed:
                    break
                if attempt + 1 < max(1, int(readback_attempts)):
                    time.sleep(min(8.0, 1.5 * (attempt + 1)))
        if not confirmed:
            raise RuntimeError("remote upload was not uniquely confirmed by size and MD5")
        confirmed_at = datetime.now().isoformat(timespec="seconds")
        legacy_row = {
            "batch": f"stream_{datetime.now().strftime('%Y%m%d')}",
            "year": year,
            "source_path": str(published),
            "file_name": file_name,
            "drive_folder_id": year,
            "drive_file_id": str(confirmed.get("ID") or ""),
            "url": f"rclone://{remote}/{year}/{file_name}",
            "uploaded_at": confirmed_at,
            "uploader": "rclone-stream",
        }
        canonical_uploaded = _append_uploaded_atomic(uploaded_log, legacy_row)
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "source_item_id": job["source_item_id"],
            "original_source_path": str(source),
            "published_path": str(published),
            "file_name": file_name,
            "year": year,
            "period": job["period"],
            "source_sha256": job["source_sha256"],
            "published_sha256": sha256_file(published),
            "published_md5": md5_file(published),
            "size": published.stat().st_size,
            "run_id": job.get("run_id", ""),
            "ocr_attempt": job.get("ocr_attempt", 1),
            "evidence_guard_revision": job["evidence_guard_revision"],
            "drive_file_id": str(confirmed.get("ID") or ""),
            "remote_path": f"{remote}:{year}/{file_name}",
            "confirmed_at": confirmed_at,
        }
        receipt_path = dirs["receipts"] / f"{job['source_item_id']}.json"
        _atomic_json(receipt_path, receipt)
    job_path.unlink(missing_ok=True)
    refresh_status(
        output_dir,
        last_uploaded_file=file_name,
        last_uploaded_at=receipt["confirmed_at"],
        last_error="",
        worker_state="running",
        canonical_uploaded=canonical_uploaded,
    )
    return receipt


def recover_working_jobs(output_dir: Path) -> int:
    dirs = _stream_dirs(output_dir)
    count = 0
    for path in dirs["working"].glob("*.json"):
        target = dirs["pending"] / path.name
        if target.exists():
            raise RuntimeError(f"duplicate pending/working job: {path.name}")
        os.replace(path, target)
        count += 1
    return count


def migrate_compatible_pending_jobs(output_dir: Path) -> int:
    """Upgrade explicitly compatible queued jobs without weakening the gate.

    A synchronized backend/uploader deployment may happen while the durable
    outbox still contains jobs written by the immediately preceding evidence
    revision.  Only revisions listed above may migrate.  Every job is rebound
    to unchanged source bytes and a freshly recomputed deterministic filename;
    the exact original JSON is archived before the atomic replacement.
    """
    dirs = _stream_dirs(output_dir)
    migrated = 0
    for path in sorted(dirs["pending"].glob("*.json")):
        job = _read_json(path)
        old_revision = str(job.get("evidence_guard_revision") or "")
        if old_revision == EVIDENCE_GUARD_REVISION:
            continue
        if COMPATIBLE_PENDING_REVISION_MIGRATIONS.get(old_revision) != EVIDENCE_GUARD_REVISION:
            raise RuntimeError(f"unapproved pending upload revision: {old_revision or 'missing'}")
        if job.get("schema") != STREAM_SCHEMA:
            raise RuntimeError("stale or invalid stream upload job")
        source_item_id = str(job.get("source_item_id") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", source_item_id) or path.stem != source_item_id:
            raise RuntimeError("pending upload source identity mismatch")
        source = Path(str(job.get("original_source_path") or "")).resolve()
        if not source.is_file() or sha256_file(source) != str(job.get("source_sha256") or ""):
            raise RuntimeError("source bytes changed before upload revision migration")
        period = str(job.get("period") or "")
        if not re.fullmatch(r"20\d{4}", period) or str(job.get("year") or "") != period[:4]:
            raise RuntimeError("pending upload period mismatch")
        final_result = job.get("final_result")
        if not isinstance(final_result, dict):
            raise RuntimeError("pending upload has no final result")
        if final_result.get("adjudication_rule") == "distant_structural_veto_over_wide_geometry_single_votes":
            raise RuntimeError("new .48 adjudication cannot originate from a .47 upload job")
        recomputed = plan_single_image(
            source,
            final_result,
            period,
            "＄",
            current_year=datetime.now().year,
        )
        if (
            recomputed.get("status") != READY_STATUS
            or recomputed.get("target_name") != job.get("target_name")
            or (job.get("plan") or {}).get("target_name") != job.get("target_name")
        ):
            raise RuntimeError("pending upload filename changed during revision migration")

        canonical = json.dumps(job, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        original_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        archived = dirs["revision_migrations"] / f"{source_item_id}.{old_revision}.{stamp}.json"
        _atomic_json(archived, job)
        upgraded = dict(job)
        upgraded["evidence_guard_revision"] = EVIDENCE_GUARD_REVISION
        upgraded["revision_migration"] = {
            "from": old_revision,
            "to": EVIDENCE_GUARD_REVISION,
            "original_job_sha256": original_sha256,
            "archived_path": str(archived),
            "source_sha256": job["source_sha256"],
            "target_name": job["target_name"],
            "migrated_at": datetime.now().isoformat(timespec="seconds"),
        }
        _atomic_json(path, upgraded)
        migrated += 1
    return migrated


def claim_next_job(output_dir: Path) -> Path | None:
    dirs = _stream_dirs(output_dir)
    pending = sorted(dirs["pending"].glob("*.json"), key=lambda p: (p.stat().st_mtime_ns, p.name))
    if not pending:
        return None
    source = pending[0]
    target = dirs["working"] / source.name
    try:
        os.replace(source, target)
    except FileNotFoundError:
        return None
    return target


@contextmanager
def worker_lock(output_dir: Path):
    root = _stream_dirs(output_dir)["root"]
    path = root / "worker.lock"
    if path.exists():
        try:
            match = re.search(r"pid=(\d+)", path.read_text(encoding="utf-8"))
            pid = int(match.group(1)) if match else 0
            if pid and psutil.pid_exists(pid):
                process = psutil.Process(pid)
                command = " ".join(process.cmdline()).lower()
                if "stream_drive_upload.py" in command:
                    raise RuntimeError("stream upload worker is already running")
        except (OSError, ValueError, psutil.Error):
            pass
        path.unlink(missing_ok=True)
    handle = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(handle, f"pid={os.getpid()}\nstarted={datetime.now().isoformat()}\n".encode("utf-8"))
        yield
    finally:
        os.close(handle)
        path.unlink(missing_ok=True)


def run_worker(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    remote: str = DEFAULT_REMOTE,
    rclone: Path | None = None,
    poll_seconds: float = 5.0,
    once: bool = False,
    timeout_seconds: int = 600,
) -> int:
    output_dir = Path(output_dir).resolve()
    completed = 0
    with worker_lock(output_dir):
        recover_working_jobs(output_dir)
        migrated = migrate_compatible_pending_jobs(output_dir)
        refresh_status(
            output_dir,
            worker_state="running",
            worker_pid=os.getpid(),
            revision_migrated=migrated,
        )
        while True:
            job_path = claim_next_job(output_dir)
            if job_path is None:
                refresh_status(output_dir, worker_state="idle", worker_pid=os.getpid())
                if once:
                    break
                time.sleep(max(0.5, poll_seconds))
                continue
            try:
                process_one_job(
                    job_path,
                    output_dir=output_dir,
                    remote=remote,
                    rclone=rclone,
                    timeout_seconds=timeout_seconds,
                )
                completed += 1
            except SystemExit as exc:
                # Shared bulk lock contention is retryable and must not lose the job.
                target = _stream_dirs(output_dir)["pending"] / job_path.name
                os.replace(job_path, target)
                refresh_status(output_dir, worker_state="waiting", last_error=str(exc))
                if once:
                    break
                time.sleep(max(1.0, poll_seconds))
            except Exception as exc:
                job = _read_json(job_path)
                job["failed_at"] = datetime.now().isoformat(timespec="seconds")
                job["error"] = str(exc)
                failed = _stream_dirs(output_dir)["failed"] / job_path.name
                _atomic_json(failed, job)
                job_path.unlink(missing_ok=True)
                refresh_status(output_dir, worker_state="error", last_error=str(exc))
                if once:
                    break
        refresh_status(output_dir, worker_state="stopped", worker_pid=0)
    return completed


def main() -> int:
    parser = argparse.ArgumentParser(description="逐張上傳已完成自動定案的 OCR 照片")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--remote", default=DEFAULT_REMOTE)
    parser.add_argument("--rclone", default="")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.remote != DEFAULT_REMOTE:
        raise SystemExit(f"approved remote required: {DEFAULT_REMOTE}")
    rclone = resolve_rclone(args.rclone)
    return run_worker(
        output_dir=Path(args.output_dir),
        remote=args.remote,
        rclone=rclone,
        poll_seconds=args.poll_seconds,
        once=args.once,
        timeout_seconds=args.timeout_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())

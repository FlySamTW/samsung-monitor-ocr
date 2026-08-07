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

from skills.audit_fields import (
    EVIDENCE_GUARD_REVISION,
    adjudication_field_invariant_reasons,
    generic_smart_monitor_without_direct_followme_identity,
    normalize_terminal_quality_issue,
    validate_evidence_contract,
)
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
FUSE_UPLOAD_RECOVERY_SCHEMA = "samsung-ocr-fuse-failed-upload-recovery-v1"
APPROVED_DRIVE_ROOT_ID = "16X5qALC3zRYc7PpnexXLYprorBzBtT_f"
DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"
_YEAR_FOLDER_ID_CACHE: dict[tuple[str, str, str], str] = {}
MIN_FROZEN_RECOVERY_GUARD = (20260717, 42)
COMPATIBLE_PENDING_REVISION_MIGRATIONS = {
    # Historical .49/.50 jobs were compatible with .51 because those revisions
    # changed transport containment only. .52 changes model identity and
    # FollowMe finalization, so no earlier queued result may migrate to .52.
    "20260718.49": "20260718.51",
    "20260718.50": "20260718.51",
    # .69 adds one exact source-hash visual authority and safe reload/resume
    # interlocking.  It does not change the meaning or target name of any
    # already-finalized .68 job, so queued .68 jobs can migrate losslessly.
    "20260721.68": "20260721.69",
    # .70 adds three source-hash-bound pixel authorities for already-consumed
    # three-call recoveries; unrelated finalized .69 jobs are unchanged.
    "20260721.69": "20260721.70",
    # .76 only synchronizes terminal quality_issue with already-finalized
    # model/price fields. It does not change the deterministic target name.
    "20260723.75": "20260723.76",
    # .79/.80 repair only pre-inference leak detection and photo-local retry
    # containment. They do not change any already-finalized result, source
    # identity, deterministic target name, or upload contract.
    "20260724.78": "20260724.80",
    "20260724.79": "20260724.80",
    # .87/.88 change only a narrow FollowMe identity signature. .89 reconciles
    # a complete, same-pass ordinary SKU/price result when its prose explicitly
    # says one complete monitor but the JSON count/fixture fields contradict it.
    # .90 adds provider-side output shape enforcement and a narrower first-pass
    # unlisted-SKU fast path. .91 accepts a fully bound historical same-card
    # SKU/price on pass one and prioritizes physical-label clauses over an
    # opening narration typo; neither change invalidates prior safe records.
    # Safe older jobs can migrate after full byte/name revalidation below; the
    # affected Smart Monitor-only signature is explicitly rejected.
    "20260730.86": "20260807.96",
    "20260731.87": "20260807.96",
    "20260731.88": "20260807.96",
    "20260731.89": "20260807.96",
    "20260731.90": "20260807.96",
    "20260803.91": "20260807.96",
    "20260803.92": "20260807.96",
    "20260805.93": "20260807.96",
    "20260806.94": "20260807.96",
    "20260807.95": "20260807.96",
}
TRANSIENT_UPLOAD_ERROR_MARKERS = (
    "exact remote readback failed",
    "exact remote readback timed out",
    "exact remote readback returned invalid",
    "single-photo upload failed",
    "single-photo upload timed out",
    "remote upload was not uniquely confirmed",
    "runtime health fuse is active",
    "pipeline pause is active",
    "temporarily unavailable",
    "connection reset",
    "connection aborted",
    "connection refused",
    "network",
    "timeout",
    "timed out",
    "too many requests",
    "rate limit",
    "http 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
)


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


def _guard_key(value: object) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{8})\.(\d+)", str(value or "").strip())
    if not match:
        return (0, 0)
    return (int(match.group(1)), int(match.group(2)))


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_fuse_failed_upload_recovery(
    job: Mapping[str, Any],
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> tuple[bool, list[str]]:
    """Validate a frozen upload-only recovery without restamping OCR evidence.

    This exception is deliberately narrower than a revision migration.  It
    accepts only an immutable job whose sole recorded failure was an active
    runtime fuse after OCR had already finalized.  The original failed JSON
    must remain archived and byte-semantically identical to the recovered job
    before its recovery envelope was added.
    """
    row = dict(job or {})
    recovery = row.get("fuse_failed_upload_recovery")
    errors: list[str] = []
    if not isinstance(recovery, dict):
        return False, ["missing_recovery_envelope"]
    if recovery.get("schema") != FUSE_UPLOAD_RECOVERY_SCHEMA:
        errors.append("recovery_schema")
    recovery_reason = str(recovery.get("reason") or "")
    if recovery_reason not in {
        "runtime_health_fuse_cleared_after_ocr_finalization",
        "current_revision_rejected_by_older_uploader",
        "compatible_prior_revision_rejected_after_uploader_upgrade",
    }:
        errors.append("recovery_reason")
    if recovery.get("approved_uploader_revision") != EVIDENCE_GUARD_REVISION:
        errors.append("approved_uploader_revision")

    source_revision = str(row.get("evidence_guard_revision") or "")
    if _guard_key(source_revision) < MIN_FROZEN_RECOVERY_GUARD:
        errors.append("source_revision_too_old")
    if recovery.get("source_revision") != source_revision:
        errors.append("source_revision")
    if recovery.get("source_item_id") != row.get("source_item_id"):
        errors.append("source_item_id")
    if recovery.get("source_sha256") != row.get("source_sha256"):
        errors.append("source_sha256")
    if recovery.get("input_image_sha256") != row.get("input_image_sha256"):
        errors.append("input_image_sha256")
    if recovery.get("run_id") != row.get("run_id"):
        errors.append("run_id")
    if recovery.get("target_name") != row.get("target_name"):
        errors.append("target_name")
    failure = str(row.get("error") or "")
    if recovery_reason == "runtime_health_fuse_cleared_after_ocr_finalization":
        if not failure.startswith("runtime health fuse is active:"):
            errors.append("failure_was_not_runtime_fuse")
    elif recovery_reason == "current_revision_rejected_by_older_uploader":
        if not (
            source_revision == EVIDENCE_GUARD_REVISION
            and failure.startswith("stale or invalid stream upload job")
        ):
            errors.append("failure_was_not_older_uploader_revision")
    elif not (
        recovery_reason == "compatible_prior_revision_rejected_after_uploader_upgrade"
        and COMPATIBLE_PENDING_REVISION_MIGRATIONS.get(source_revision)
        == EVIDENCE_GUARD_REVISION
        and failure.startswith("stale or invalid stream upload job")
    ):
        errors.append("failure_was_not_compatible_prior_revision")

    frozen = dict(row)
    frozen.pop("fuse_failed_upload_recovery", None)
    frozen_sha256 = _canonical_sha256(frozen)
    if recovery.get("failed_job_sha256") != frozen_sha256:
        errors.append("failed_job_sha256")

    output_root = Path(output_dir).resolve()
    recovery_root = (
        output_root / "_ocr_audit" / "fuse_failed_upload_recovery"
    ).resolve()
    archived_text = str(recovery.get("archived_failed_job") or "")
    try:
        archived = Path(archived_text).resolve()
        archived.relative_to(recovery_root)
    except (OSError, ValueError):
        errors.append("archived_failed_job_scope")
        archived = None
    if archived is not None:
        try:
            archived_payload = _read_json(archived)
            if _canonical_sha256(archived_payload) != frozen_sha256:
                errors.append("archived_failed_job_hash")
        except (OSError, UnicodeError, ValueError, TypeError, RuntimeError):
            errors.append("archived_failed_job_unavailable")

    if not re.fullmatch(r"[0-9a-f]{64}", str(row.get("source_item_id") or "")):
        errors.append("invalid_source_item_id")
    if not re.fullmatch(r"[0-9a-f]{64}", str(row.get("source_sha256") or "")):
        errors.append("invalid_source_sha256")
    if not re.fullmatch(r"[0-9a-f]{64}", str(row.get("input_image_sha256") or "")):
        errors.append("invalid_input_image_sha256")
    if not str(row.get("run_id") or ""):
        errors.append("missing_run_id")
    if Path(str(row.get("target_name") or "")).name != str(row.get("target_name") or ""):
        errors.append("unsafe_target_name")
    plan = row.get("plan")
    if (
        not isinstance(plan, dict)
        or plan.get("status") != READY_STATUS
        or plan.get("target_name") != row.get("target_name")
    ):
        errors.append("plan_target_mismatch")
    return not errors, errors


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


def clear_stale_pause_status(output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    """Clear an obsolete repair badge after the pause file has disappeared."""
    current = read_stream_status(output_dir)
    if (
        current.get("mutation_blocked") is True
        or str(current.get("repair_reason") or "")
        or current.get("pipeline_pause") is not None
    ):
        return refresh_status(
            output_dir,
            mutation_blocked=False,
            repair_reason="",
            pipeline_pause=None,
        )
    return current


def read_pipeline_pause(output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any] | None:
    pause_path = Path(output_dir).resolve() / "_ocr_audit" / "pipeline_pause.json"
    if not pause_path.exists():
        return None
    try:
        payload = _read_json(pause_path)
    except (OSError, UnicodeError, ValueError, TypeError, RuntimeError):
        return {"schema": "invalid", "reason": "pipeline_pause_unreadable"}
    if payload.get("schema") != "samsung-ocr-pipeline-pause/v1":
        return {"schema": "invalid", "reason": "pipeline_pause_schema_invalid"}
    return payload


def _job_key(result: Mapping[str, Any]) -> str:
    source_item_id = str(result.get("source_item_id") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", source_item_id):
        raise RuntimeError("finalized result has no valid source_item_id")
    return source_item_id


def _equivalent_upload_job(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Compare durable upload intent while ignoring audit-only enqueue metadata."""
    volatile = {"queued_at", "superseded_receipt", "superseded_pending_job"}
    left_stable = {key: value for key, value in dict(left).items() if key not in volatile}
    right_stable = {key: value for key, value in dict(right).items() if key not in volatile}
    return left_stable == right_stable


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
    invariant_reasons = adjudication_field_invariant_reasons(row)
    if invariant_reasons:
        raise RuntimeError(
            "verified result failed adjudication field invariant: "
            + ";".join(invariant_reasons)
        )

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
                "adjudication_pass_summaries", "field_suppression_reasons",
                "raw_structured_model", "raw_structured_price",
            )
        },
        "run_id": str(row.get("run_id") or ""),
        "ocr_attempt": int(row.get("ocr_attempt") or 1),
        "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
        "normalized_evidence": normalized,
        "queued_at": datetime.now().isoformat(timespec="seconds"),
    }
    if pending.exists():
        existing = _read_json(pending)
        if _equivalent_upload_job(existing, job):
            return pending
        old_revision = str(existing.get("evidence_guard_revision") or "")
        same_bound_source = bool(
            existing.get("schema") == STREAM_SCHEMA
            and existing.get("source_item_id") == key
            and existing.get("source_sha256") == job["source_sha256"]
            and existing.get("input_image_sha256") == job["input_image_sha256"]
            and existing.get("period") == job["period"]
            and existing.get("year") == job["year"]
            and old_revision
            and old_revision != EVIDENCE_GUARD_REVISION
        )
        if not same_bound_source:
            raise RuntimeError(f"same source_item_id already has a different upload job: {key}")
        # The old job has not reached Drive, so a current-revision,
        # source-bound correction may replace it.  Preserve the exact old
        # intent first; the final atomic write below is the only mutation of
        # the durable pending slot.
        safe_revision = re.sub(r"[^0-9A-Za-z._-]", "_", old_revision)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        archived = (
            dirs["revision_migrations"]
            / f"{key}.{safe_revision}.{stamp}.superseded_pending.json"
        )
        _atomic_json(archived, existing)
        job["superseded_pending_job"] = {
            "archived_path": str(archived),
            "evidence_guard_revision": old_revision,
            "target_name": existing.get("target_name"),
            "source_sha256": existing.get("source_sha256"),
        }
    if working.exists():
        existing = _read_json(working)
        if _equivalent_upload_job(existing, job):
            return working
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


def _pipeline_pause_clear(output_dir: Path) -> None:
    pause = Path(output_dir).resolve() / "_ocr_audit" / "pipeline_pause.json"
    if pause.exists():
        raise RuntimeError(f"pipeline pause is active: {pause}")


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
    frozen_recovery_valid, frozen_recovery_errors = validate_fuse_failed_upload_recovery(
        job,
        output_dir=output_dir,
    )
    if (
        job.get("schema") != STREAM_SCHEMA
        or (
            job.get("evidence_guard_revision") != EVIDENCE_GUARD_REVISION
            and not frozen_recovery_valid
        )
    ):
        detail = ",".join(frozen_recovery_errors[:5])
        if detail:
            raise RuntimeError(f"stale or invalid stream upload job: {detail}")
        raise RuntimeError("stale or invalid stream upload job")
    source = Path(str(job.get("original_source_path") or "")).resolve()
    if not source.is_file() or sha256_file(source) != str(job.get("source_sha256") or ""):
        raise RuntimeError("source bytes changed after OCR finalization")
    if remote != DEFAULT_REMOTE:
        raise RuntimeError("unapproved Drive remote")
    # Both guards must run before publishing the renamed local copy as well as
    # before any remote Drive call.  During repair the worker remains alive for
    # Dashboard/status heartbeats, but performs no upload-side mutation.
    _runtime_fuse_clear(output_dir)
    _pipeline_pause_clear(output_dir)
    published_row = copy_planned_image_idempotent(job["plan"], output_dir)
    published = Path(published_row["target_path"])
    year = str(job["year"])
    file_name = str(job["target_name"])
    rclone_path = Path(rclone) if rclone else resolve_rclone("")

    lock_path = output_dir / "_drive_upload" / "rclone_drive_upload.lock"
    uploaded_log = output_dir / "_drive_upload" / "drive_upload_uploaded.csv"
    with single_instance_lock(lock_path):
        _runtime_fuse_clear(output_dir)
        _pipeline_pause_clear(output_dir)
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
        if frozen_recovery_valid:
            receipt["upload_recovery"] = dict(job["fuse_failed_upload_recovery"])
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


def is_transient_upload_failure(exc: BaseException) -> bool:
    """Return whether a failure may be retried without changing OCR evidence."""
    if isinstance(exc, (TimeoutError, ConnectionError, PermissionError)):
        return True
    text = str(exc or "").strip().lower()
    return any(marker in text for marker in TRANSIENT_UPLOAD_ERROR_MARKERS)


def requeue_transient_job(
    job_path: Path,
    exc: BaseException,
    *,
    output_dir: Path,
    now_epoch: float | None = None,
) -> Path:
    """Delay a network/fuse retry while allowing later photos to upload."""
    now = float(time.time() if now_epoch is None else now_epoch)
    job = _read_json(job_path)
    retry_count = max(0, int(job.get("transport_retry_count") or 0)) + 1
    delay_seconds = min(300.0, 15.0 * (2 ** min(retry_count - 1, 5)))
    job["transport_retry_count"] = retry_count
    job["last_transport_error"] = str(exc)
    job["last_transport_error_at"] = datetime.now().isoformat(timespec="seconds")
    job["retry_not_before_epoch"] = now + delay_seconds
    pending = _stream_dirs(output_dir)["pending"] / job_path.name
    _atomic_json(pending, job)
    job_path.unlink(missing_ok=True)
    return pending


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
        frozen_recovery_valid, _ = validate_fuse_failed_upload_recovery(
            job,
            output_dir=output_dir,
        )
        if frozen_recovery_valid:
            # This is an upload-only replay of a frozen, already-finalized
            # result. Preserve its original OCR revision in the receipt.
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
        final_result = dict(final_result)
        if (
            old_revision in {"20260730.86", "20260731.87"}
            and final_result.get("ordered_followme_early_exit") is True
            and generic_smart_monitor_without_direct_followme_identity(final_result)
        ):
            raise RuntimeError(
                "unsafe legacy generic Smart Monitor FollowMe result requires revalidation"
            )
        normalize_terminal_quality_issue(final_result)
        invariant_reasons = adjudication_field_invariant_reasons(final_result)
        if invariant_reasons:
            raise RuntimeError(
                "pending upload failed terminal field invariant: "
                + ";".join(invariant_reasons)
            )
        if (
            _guard_key(old_revision) < (20260718, 48)
            and final_result.get("adjudication_rule")
            == "distant_structural_veto_over_wide_geometry_single_votes"
        ):
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
        upgraded["final_result"] = final_result
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
    now = time.time()
    source = None
    for candidate in pending:
        try:
            retry_not_before = float(_read_json(candidate).get("retry_not_before_epoch") or 0)
        except (OSError, UnicodeError, ValueError, TypeError, RuntimeError):
            retry_not_before = 0
        if retry_not_before <= now:
            source = candidate
            break
    if source is None:
        return None
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
        migrated = 0
        transport_initialized = False
        refresh_status(
            output_dir,
            worker_state="running",
            worker_pid=os.getpid(),
            revision_migrated=migrated,
        )
        while True:
            pause = read_pipeline_pause(output_dir)
            if pause is not None:
                # A pause can arrive after transport initialization.  Force a
                # fresh recovery pass after it clears so the durable status
                # also leaves repair mode instead of continuing uploads while
                # still displaying a stale mutation_blocked=true state.
                transport_initialized = False
                refresh_status(
                    output_dir,
                    worker_state="repair",
                    worker_pid=os.getpid(),
                    mutation_blocked=True,
                    repair_reason="pipeline_pause",
                    pipeline_pause=pause,
                )
                if once:
                    break
                time.sleep(max(0.5, poll_seconds))
                continue
            # A pause can be created and removed while the worker is inside a
            # long Drive request.  In that race it never observes the file,
            # so clear any stale Dashboard repair badge on every normal loop.
            clear_stale_pause_status(output_dir)
            if not transport_initialized:
                recover_working_jobs(output_dir)
                migrated = migrate_compatible_pending_jobs(output_dir)
                transport_initialized = True
                refresh_status(
                    output_dir,
                    worker_state="running",
                    worker_pid=os.getpid(),
                    mutation_blocked=False,
                    repair_reason="",
                    pipeline_pause=None,
                    revision_migrated=migrated,
                )
            # The OCR backend can still be finishing the explicitly compatible
            # prior revision while this worker has already loaded the next
            # guard.  Migrate newly enqueued jobs before claiming each item,
            # not only once when the worker starts.
            newly_migrated = migrate_compatible_pending_jobs(output_dir)
            if newly_migrated:
                migrated += newly_migrated
                refresh_status(
                    output_dir,
                    worker_state="running",
                    worker_pid=os.getpid(),
                    revision_migrated=migrated,
                )
            # Close the small migration-to-claim window.  If a pause lands
            # after claim, process_one_job raises a transient hold and the job
            # is returned to pending without publishing or contacting Drive.
            if read_pipeline_pause(output_dir) is not None:
                continue
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
                if is_transient_upload_failure(exc):
                    requeue_transient_job(
                        job_path,
                        exc,
                        output_dir=output_dir,
                    )
                    refresh_status(
                        output_dir,
                        worker_state="waiting",
                        last_error=str(exc),
                    )
                else:
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

"""Content-bound authorization gate for starting historical OCR.

The gate is deliberately independent of the supervisor.  Both the supervisor
and the recursive runner must validate the same receipt, so a direct CLI call
cannot turn a supervisor-only boolean into historical authorization.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from skills.audit_fields import EVIDENCE_GUARD_REVISION
from tools.source_inventory_snapshot import CSV_NAME as INVENTORY_CSV_NAME
from tools.source_inventory_snapshot import SCHEMA as INVENTORY_SCHEMA
from tools.source_inventory_snapshot import SUMMARY_NAME as INVENTORY_SUMMARY_NAME


REQUEST_SCHEMA = "samsung-ocr-full-project-continuation-request/v1"
RECEIPT_SCHEMA = "samsung-ocr-historical-continuation-receipt/v1"
CANONICAL_BACKEND_URL = "http://127.0.0.1:5002"
REQUEST_NAME = "full_project_continuation_requested.json"
RECEIPT_NAME = "historical_continuation_receipt.json"
MARKER_NAME = "current_year_rerun_cycle_complete.json"
PROOF_NAME = "upload_gate_proof.json"
REVIEW_NAME = "drive_upload_review_required.csv"
TERMINAL_CANDIDATE_TEMPLATE = "v1945_evidence_backfill_{year}.csv"


def _csv_row_count(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return sum(1 for _row in csv.DictReader(handle))


def _sealed_terminal_authority(output_dir: Path, current_year: int) -> Tuple[Optional[dict], List[str]]:
    """Validate the immutable terminal inventory used after a year is sealed.

    The ordinary upload manifest/risk report remains intentionally mutable.  A
    later historical-run accident must not send a year that already received a
    completion marker back through OCR.  This authority is accepted only when
    the current evidence builder proves every source terminal and the candidate
    set is exactly empty; it never turns a partial/review batch into a handoff.
    """

    audit_dir = output_dir.resolve() / "_ocr_audit"
    candidate_path = audit_dir / TERMINAL_CANDIDATE_TEMPLATE.format(year=current_year)
    summary_path = candidate_path.with_suffix(candidate_path.suffix + ".summary.json")
    errors: List[str] = []
    if not candidate_path.is_file():
        errors.append(f"terminal_candidate_missing:{candidate_path}")
    if not summary_path.is_file():
        errors.append(f"terminal_summary_missing:{summary_path}")
    if errors:
        return None, errors
    try:
        summary = _read_json(summary_path)
        candidate_rows = _csv_row_count(candidate_path)
    except (OSError, UnicodeError, ValueError, TypeError, csv.Error, json.JSONDecodeError) as exc:
        return None, [f"terminal_authority_unreadable:{exc}"]

    unique_sources = int(summary.get("unique_year_sources") or 0)
    verified_sources = int(summary.get("already_verified_year_sources") or 0)
    human_audited_sources = int(summary.get("human_audited_year_sources") or 0)
    terminal_sources = int(summary.get("terminal_authorized_year_sources") or 0)
    expected_output = str(candidate_path.resolve()).casefold()
    supplied_output = str(Path(str(summary.get("output") or ".")).resolve()).casefold()
    checks = {
        "terminal_summary_not_executed": summary.get("executed") is True,
        "terminal_summary_year_mismatch": str(summary.get("year") or "") == str(current_year),
        "terminal_summary_output_mismatch": supplied_output == expected_output,
        "terminal_candidate_rows_nonzero": candidate_rows == 0 and int(summary.get("candidate_rows") or 0) == 0,
        "terminal_source_inventory_empty": unique_sources > 0,
        "terminal_authorized_count_mismatch": terminal_sources == unique_sources,
        "terminal_resolution_count_mismatch": verified_sources + human_audited_sources == unique_sources,
        "terminal_missing_sources_nonzero": int(summary.get("missing_sources") or 0) == 0,
        "terminal_conflicting_sources_nonzero": int(summary.get("conflicting_sources") or 0) == 0,
        "terminal_invalid_rows_nonzero": int(summary.get("invalid_rows") or 0) == 0,
        "terminal_invalid_upload_receipts_nonzero": int(summary.get("invalid_upload_receipts") or 0) == 0,
        "terminal_invalid_upload_jobs_nonzero": int(summary.get("invalid_upload_queue_jobs") or 0) == 0,
        "terminal_upload_queue_nonempty": int(summary.get("current_upload_queue_source_ids") or 0) == 0,
    }
    errors.extend(name for name, valid in checks.items() if not valid)
    if errors:
        return None, errors
    return {
        "candidate_path": str(candidate_path.resolve()),
        "candidate_sha256": file_sha256(candidate_path),
        "summary_path": str(summary_path.resolve()),
        "summary_sha256": file_sha256(summary_path),
        "year": current_year,
        "unique_year_sources": unique_sources,
        "verified_sources": verified_sources,
        "human_audited_sources": human_audited_sources,
        "terminal_authorized_sources": terminal_sources,
    }, []


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object")
    return value


def _parse_time(value: object) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("timestamp is empty")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(timezone.utc)


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _review_count(path: Path, current_year: int) -> int:
    count = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                year = int(str(row.get("year") or "0").strip())
            except ValueError:
                year = 0
            if year >= current_year and str(row.get("reasons") or "").strip():
                count += 1
    return count


def _default_status_reader(backend_url: str) -> dict:
    request = urllib.request.Request(
        f"{backend_url.rstrip('/')}/api/status",
        headers={"Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("backend status root must be an object")
    return value


def create_or_migrate_request(
    source_root: Path,
    output_dir: Path,
    *,
    current_year: int,
    preserve_existing: bool = False,
    requested_at: Optional[str] = None,
) -> dict:
    """Create the explicit, root-bound request; migration preserves user time."""
    source_root = source_root.resolve()
    output_dir = output_dir.resolve()
    path = output_dir / "_ocr_audit" / REQUEST_NAME
    existing: dict = {}
    if preserve_existing and path.is_file():
        existing = _read_json(path)
    timestamp = requested_at or existing.get("requested_at")
    if not timestamp:
        timestamp = datetime.now(timezone.utc).astimezone().isoformat()
    _parse_time(timestamp)
    payload = {
        "schema": REQUEST_SCHEMA,
        "requested_at": timestamp,
        "source_root": str(source_root),
        "output_dir": str(output_dir),
        "current_year": current_year,
        "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
        "objective": str(existing.get("objective") or "Continue all remaining source folders only after current-year correctness and upload finalization."),
        "accuracy_priority": True,
    }
    _write_atomic(path, payload)
    return {"valid": True, "request_path": str(path), "request_sha256": file_sha256(path), "request": payload}


def build_authority_snapshot(
    source_root: Path,
    output_dir: Path,
    *,
    current_year: int,
    backend_url: str,
    require_backend_idle: bool,
    status_reader: Callable[[str], dict] = _default_status_reader,
) -> Tuple[Optional[dict], List[str]]:
    """Validate every authority and return the exact snapshot for a receipt."""
    source_root = source_root.resolve()
    output_dir = output_dir.resolve()
    audit_dir = output_dir / "_ocr_audit"
    drive_dir = output_dir / "_drive_upload"
    paths = {
        "request": audit_dir / REQUEST_NAME,
        "current_year_marker": audit_dir / MARKER_NAME,
        "upload_gate_proof": drive_dir / PROOF_NAME,
        "review_required": drive_dir / REVIEW_NAME,
    }
    errors: List[str] = []
    for name, path in paths.items():
        if not path.is_file():
            errors.append(f"missing_{name}:{path}")
    for name in ("runtime_health_fuse.json", "model_benchmark.lock"):
        path = audit_dir / name
        if path.exists():
            errors.append(f"active_interlock:{path}")
    if backend_url.rstrip("/") != CANONICAL_BACKEND_URL:
        errors.append(f"noncanonical_backend:{backend_url}")
    if errors:
        return None, errors

    try:
        request = _read_json(paths["request"])
        marker = _read_json(paths["current_year_marker"])
        proof = _read_json(paths["upload_gate_proof"])
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return None, [f"authority_unreadable:{exc}"]

    if request.get("schema") != REQUEST_SCHEMA:
        errors.append("request_schema_invalid")
    if _canonical(Path(str(request.get("source_root") or "."))) != _canonical(source_root):
        errors.append("request_source_root_mismatch")
    if _canonical(Path(str(request.get("output_dir") or "."))) != _canonical(output_dir):
        errors.append("request_output_dir_mismatch")
    if int(request.get("current_year") or 0) != current_year:
        errors.append("request_current_year_mismatch")
    if request.get("evidence_guard_revision") != EVIDENCE_GUARD_REVISION:
        errors.append("request_evidence_guard_revision_mismatch")
    if request.get("accuracy_priority") is not True:
        errors.append("request_accuracy_priority_missing")

    terminal_authority, terminal_errors = _sealed_terminal_authority(output_dir, current_year)
    sealed_terminal = terminal_authority is not None

    if proof.get("gate_open") is not True or int(proof.get("pending_count", -1)) != 0:
        errors.append("upload_gate_not_finalized")
    try:
        upload_scope_years = [int(value) for value in list(proof.get("upload_scope_years") or [])]
    except (TypeError, ValueError):
        upload_scope_years = []
    if upload_scope_years != [current_year]:
        errors.append("upload_scope_not_current_year_only")
    if require_backend_idle:
        try:
            proof_age_minutes = (datetime.now(timezone.utc) - _parse_time(proof.get("generated_at"))).total_seconds() / 60
            if (proof_age_minutes < -5 or proof_age_minutes > 30) and not sealed_terminal:
                errors.append(f"upload_gate_proof_stale:{proof_age_minutes:.1f}")
        except ValueError as exc:
            errors.append(f"upload_gate_proof_timestamp_invalid:{exc}")
    bound_fields = ("audit_input_sha256", "manifest_summary_sha256", "pending_sha256", "backfill_run_id")
    if marker.get("upload_gate_schema") != proof.get("schema"):
        errors.append("current_year_marker_schema_mismatch")
    for field in bound_fields:
        if not str(marker.get(field) or "") or marker.get(field) != proof.get(field):
            errors.append(f"current_year_marker_{field}_mismatch")
    if int(marker.get("pending_count", -1)) != 0:
        errors.append("current_year_marker_pending_nonzero")
    try:
        if _parse_time(marker.get("completed_at")) < _parse_time(request.get("requested_at")):
            errors.append("current_year_marker_predates_request")
    except ValueError as exc:
        errors.append(f"authority_timestamp_invalid:{exc}")

    try:
        review_count = _review_count(paths["review_required"], current_year)
    except (OSError, UnicodeError, csv.Error) as exc:
        errors.append(f"review_required_unreadable:{exc}")
        review_count = -1
    if review_count != 0 and not sealed_terminal:
        errors.append(f"current_year_review_required_nonzero:{review_count}")
    if not sealed_terminal and terminal_errors:
        # Keep the normal fresh-proof path backward compatible.  Terminal
        # authority errors are diagnostic only unless the sealed fallback is
        # actually needed for stale proof/review state.
        terminal_errors = list(terminal_errors)

    backend_snapshot: Dict[str, object] = {}
    if require_backend_idle:
        try:
            status = status_reader(backend_url)
            running = bool(status.get("is_running", status.get("running")))
            backend_snapshot = {
                "running": running,
                "processed": status.get("processed"),
                "total": status.get("total"),
                "worker_alive": status.get("worker_alive"),
            }
            if running or status.get("worker_alive") is True:
                errors.append("backend_not_idle")
            processed = status.get("processed")
            total = status.get("total")
            if processed is not None and total is not None and int(processed) != int(total):
                errors.append("backend_batch_incomplete")
        except Exception as exc:  # Network/status uncertainty is fail-closed.
            errors.append(f"backend_status_unavailable:{exc}")

    if errors:
        return None, errors
    snapshot = {
        "source_root": str(source_root),
        "output_dir": str(output_dir),
        "current_year": current_year,
        "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
        "backend_url": backend_url.rstrip("/"),
        "backend_idle_snapshot": backend_snapshot,
        "request_path": str(paths["request"].resolve()),
        "request_sha256": file_sha256(paths["request"]),
        "current_year_marker_path": str(paths["current_year_marker"].resolve()),
        "current_year_marker_sha256": file_sha256(paths["current_year_marker"]),
        "upload_gate_proof_path": str(paths["upload_gate_proof"].resolve()),
        "upload_gate_proof_sha256": file_sha256(paths["upload_gate_proof"]),
        "review_required_path": str(paths["review_required"].resolve()),
        "review_required_sha256": file_sha256(paths["review_required"]),
        "current_year_review_required": 0,
        "sealed_terminal_completion": sealed_terminal,
        "legacy_current_year_review_rows_ignored": review_count if sealed_terminal else 0,
        "terminal_authority": terminal_authority or {},
        "audit_input_sha256": str(proof.get("audit_input_sha256") or ""),
        "backfill_run_id": str(proof.get("backfill_run_id") or ""),
    }
    return snapshot, []


def write_receipt(
    source_root: Path,
    output_dir: Path,
    *,
    current_year: int,
    backend_url: str,
    status_reader: Callable[[str], dict] = _default_status_reader,
) -> dict:
    receipt_path = output_dir.resolve() / "_ocr_audit" / RECEIPT_NAME
    snapshot, errors = build_authority_snapshot(
        source_root,
        output_dir,
        current_year=current_year,
        backend_url=backend_url,
        require_backend_idle=True,
        status_reader=status_reader,
    )
    if snapshot is None:
        receipt_path.unlink(missing_ok=True)
        return {"valid": False, "receipt_path": str(receipt_path), "errors": errors}
    payload = {
        "schema": RECEIPT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        **snapshot,
    }
    _write_atomic(receipt_path, payload)
    recheck, recheck_errors = validate_receipt(
        receipt_path,
        source_root,
        output_dir,
        current_year=current_year,
        backend_url=backend_url,
    )
    final_snapshot, final_errors = build_authority_snapshot(
        source_root,
        output_dir,
        current_year=current_year,
        backend_url=backend_url,
        require_backend_idle=True,
        status_reader=status_reader,
    )
    if final_snapshot is not None and snapshot != final_snapshot:
        final_errors.append("authority_snapshot_changed_during_receipt_write")
    if recheck_errors or final_errors:
        receipt_path.unlink(missing_ok=True)
        return {"valid": False, "receipt_path": str(receipt_path), "errors": [*recheck_errors, *final_errors]}
    return {"valid": True, "receipt_path": str(receipt_path), "receipt_sha256": file_sha256(receipt_path), "receipt": recheck, "errors": []}


def validate_receipt(
    receipt_path: Path,
    source_root: Path,
    output_dir: Path,
    *,
    current_year: int,
    backend_url: str,
    require_source_inventory: bool = False,
) -> Tuple[Optional[dict], List[str]]:
    """Revalidate a receipt during recursion without requiring backend idle."""
    source_root = source_root.resolve()
    output_dir = output_dir.resolve()
    canonical_receipt = output_dir / "_ocr_audit" / RECEIPT_NAME
    errors: List[str] = []
    if _canonical(receipt_path) != _canonical(canonical_receipt):
        errors.append("receipt_path_not_canonical")
    if not receipt_path.is_file():
        errors.append(f"receipt_missing:{receipt_path}")
        return None, errors
    try:
        receipt = _read_json(receipt_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return None, [f"receipt_unreadable:{exc}"]
    if receipt.get("schema") != RECEIPT_SCHEMA:
        errors.append("receipt_schema_invalid")
    snapshot, authority_errors = build_authority_snapshot(
        source_root,
        output_dir,
        current_year=current_year,
        backend_url=backend_url,
        require_backend_idle=False,
    )
    errors.extend(authority_errors)
    if snapshot is not None:
        for field, value in snapshot.items():
            if field == "backend_idle_snapshot":
                continue
            if receipt.get(field) != value:
                errors.append(f"receipt_authority_mismatch:{field}")
    inventory_summary_path = output_dir / "_ocr_audit" / INVENTORY_SUMMARY_NAME
    inventory_csv_path = output_dir / "_ocr_audit" / INVENTORY_CSV_NAME
    inventory_fields_present = bool(receipt.get("source_inventory_summary_sha256"))
    if require_source_inventory or inventory_fields_present:
        if not inventory_summary_path.is_file() or not inventory_csv_path.is_file():
            errors.append("source_inventory_binding_missing")
        else:
            try:
                inventory_summary = _read_json(inventory_summary_path)
                if inventory_summary.get("schema") != INVENTORY_SCHEMA:
                    errors.append("source_inventory_schema_mismatch")
                expected = {
                    "source_inventory_summary_path": str(inventory_summary_path.resolve()),
                    "source_inventory_summary_sha256": file_sha256(inventory_summary_path),
                    "source_inventory_csv_path": str(inventory_csv_path.resolve()),
                    "source_inventory_csv_sha256": file_sha256(inventory_csv_path),
                    "source_inventory_row_count": int(inventory_summary.get("row_count") or 0),
                    "source_inventory_folder_count": int(inventory_summary.get("folder_count") or 0),
                }
                for field, value in expected.items():
                    if receipt.get(field) != value:
                        errors.append(f"receipt_authority_mismatch:{field}")
            except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
                errors.append(f"source_inventory_binding_unreadable:{exc}")
    return (receipt if not errors else None), errors


def bind_source_inventory(
    receipt_path: Path,
    source_root: Path,
    output_dir: Path,
    *,
    current_year: int,
    backend_url: str,
) -> dict:
    """Atomically bind the just-created frozen inventory before historical OCR."""
    receipt, errors = validate_receipt(
        receipt_path,
        source_root,
        output_dir,
        current_year=current_year,
        backend_url=backend_url,
    )
    if receipt is None:
        return {"valid": False, "errors": errors}
    output_dir = output_dir.resolve()
    summary_path = output_dir / "_ocr_audit" / INVENTORY_SUMMARY_NAME
    csv_path = output_dir / "_ocr_audit" / INVENTORY_CSV_NAME
    try:
        summary = _read_json(summary_path)
        if summary.get("schema") != INVENTORY_SCHEMA:
            raise ValueError("source inventory schema mismatch")
        payload = dict(receipt)
        payload.update({
            "source_inventory_summary_path": str(summary_path.resolve()),
            "source_inventory_summary_sha256": file_sha256(summary_path),
            "source_inventory_csv_path": str(csv_path.resolve()),
            "source_inventory_csv_sha256": file_sha256(csv_path),
            "source_inventory_row_count": int(summary.get("row_count") or 0),
            "source_inventory_folder_count": int(summary.get("folder_count") or 0),
        })
        _write_atomic(receipt_path, payload)
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {"valid": False, "errors": [f"source_inventory_bind_failed:{exc}"]}
    rebound, rebound_errors = validate_receipt(
        receipt_path,
        source_root,
        output_dir,
        current_year=current_year,
        backend_url=backend_url,
        require_source_inventory=True,
    )
    return {"valid": rebound is not None, "errors": rebound_errors}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build or validate historical OCR continuation authority.")
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--current-year", type=int, default=datetime.now().year)
    parser.add_argument("--backend-url", default=CANONICAL_BACKEND_URL)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write-request", action="store_true")
    action.add_argument("--migrate-existing-request", action="store_true")
    action.add_argument("--write-receipt", action="store_true")
    action.add_argument("--validate-receipt", action="store_true")
    parser.add_argument("--requested-at")
    args = parser.parse_args()
    source_root = Path(args.source_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    if args.write_request or args.migrate_existing_request:
        result = create_or_migrate_request(
            source_root,
            output_dir,
            current_year=args.current_year,
            preserve_existing=args.migrate_existing_request,
            requested_at=args.requested_at,
        )
    elif args.write_receipt:
        result = write_receipt(
            source_root,
            output_dir,
            current_year=args.current_year,
            backend_url=args.backend_url,
        )
    else:
        receipt_path = output_dir / "_ocr_audit" / RECEIPT_NAME
        receipt, errors = validate_receipt(
            receipt_path,
            source_root,
            output_dir,
            current_year=args.current_year,
            backend_url=args.backend_url,
        )
        result = {"valid": receipt is not None, "receipt_path": str(receipt_path), "errors": errors}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("valid") else 2


if __name__ == "__main__":
    raise SystemExit(main())

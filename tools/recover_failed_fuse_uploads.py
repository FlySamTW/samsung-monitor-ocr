"""Recover upload jobs whose only failure was an already-cleared runtime fuse.

The tool performs no OCR and does not upgrade an old result to the current
evidence revision.  It binds each failed upload job to the current full result
on disk, source-map identity, immutable source bytes, and a clean request-bound
trace from the same run.  The original failed JSON is archived before a
recovery envelope is added and the job is atomically returned to the durable
pending queue.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.audit_fields import EVIDENCE_GUARD_REVISION
from tools.continue_after_period_priority import (
    normalized,
    prepared_input_sha256,
    receipt_revision_is_proven,
)
from tools.photo_rename_planner import READY_STATUS, plan_single_image
from tools.rclone_drive_upload import sha256_file
from tools.stream_drive_upload import (
    FUSE_UPLOAD_RECOVERY_SCHEMA,
    MIN_FROZEN_RECOVERY_GUARD,
    STREAM_SCHEMA,
    _atomic_json,
    _guard_key,
    validate_fuse_failed_upload_recovery,
)


PRESENTATION_RESULT_FIELDS = (
    "view_type",
    "model",
    "price",
    "price_symbol",
    "price_status",
    "official_price",
    "price_diff_percent",
    "screen_status",
    "quality_issue",
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _request_json(url: str, endpoint: str) -> Any:
    with urllib.request.urlopen(url.rstrip("/") + endpoint, timeout=60) as response:
        return json.load(response)


def _full_results(priority_dir: Path) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in sorted(priority_dir.glob("*-OCR成功.json")):
        payload = _read_json(path)
        if not isinstance(payload, list):
            raise RuntimeError(f"success result is not a list: {path}")
        for task in payload:
            if not isinstance(task, dict):
                continue
            data = dict(task.get("data") or {})
            file_name = Path(str(data.get("image") or "")).name
            if not file_name:
                continue
            meta = dict(data.get("ocr_meta") or task.get("ocr_meta") or {})
            row: dict[str, Any] = {
                "file_name": file_name,
                "view_type": meta.get("view_type") or "單機",
                "screen_status": meta.get("screen_status") or "",
                "quality_issue": meta.get("quality_issue") or "",
                "price_status": meta.get("price_status") or "",
                "price_symbol": meta.get("price_symbol") or "",
                "official_price": meta.get("official_price"),
                "price_diff_percent": meta.get("price_diff_percent"),
                "auto_verified": meta.get("auto_verified", False),
                "auto_review_required": meta.get("auto_review_required", False),
                "review_status": meta.get("review_status") or "",
                "evidence_contract_version": meta.get("evidence_contract_version") or "",
                "evidence_guard_revision": meta.get("evidence_guard_revision") or "",
                "evidence_contract_valid": meta.get("evidence_contract_valid", False),
            }
            annotations = list(task.get("annotations") or [])
            if annotations:
                row["timestamp"] = annotations[0].get("created_at") or ""
                for field in annotations[0].get("result") or []:
                    value = dict(field.get("value") or {})
                    if field.get("from_name") == "category":
                        choices = value.get("choices") or [""]
                        row["view_type"] = (
                            "遠景" if "遠景" in str(choices[0]) else "單機"
                        )
                    elif field.get("from_name") == "model":
                        row["model"] = (value.get("text") or [""])[0]
                    elif field.get("from_name") == "price":
                        row["price"] = (value.get("text") or [""])[0]
            grouped[file_name].append(row)
    duplicates = sorted(name for name, rows in grouped.items() if len(rows) != 1)
    if duplicates:
        raise RuntimeError(
            f"full result identity is not unique: {duplicates[:5]} (count={len(duplicates)})"
        )
    return {name: rows[0] for name, rows in grouped.items()}


def _trace_authorities(trace_path: Path) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    with trace_path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            row = json.loads(line)
            source_id = str(
                row.get("source_item_id") or row.get("source_identity") or ""
            )
            parsed = dict(row.get("parsed_output") or {})
            run_id = str(row.get("run_id") or parsed.get("run_id") or "")
            if source_id and run_id:
                grouped[(source_id, run_id)].append(row)
    return grouped


def _clean_trace_row(
    row: Mapping[str, Any],
    *,
    input_image_sha256: str,
) -> bool:
    parsed = dict(row.get("parsed_output") or {})
    runtime = parsed.get("runtime_health") or row.get("runtime_health") or {}
    traced_hash = str(
        parsed.get("input_image_sha256")
        or row.get("input_image_sha256")
        or ""
    ).lower()
    return bool(
        traced_hash == input_image_sha256
        and (parsed.get("request_id_verified") or row.get("request_id_verified"))
        is True
        and (
            parsed.get("request_binding_enforced")
            or row.get("request_binding_enforced")
        )
        is True
        and (parsed.get("independent_pass") or row.get("independent_pass"))
        is True
        and (
            parsed.get("prior_answer_exposed")
            if "prior_answer_exposed" in parsed
            else row.get("prior_answer_exposed")
        )
        is not True
        and (
            parsed.get("prompt_contamination")
            if "prompt_contamination" in parsed
            else row.get("prompt_contamination")
        )
        is not True
        and isinstance(runtime, dict)
        and runtime.get("healthy") is True
    )


def _presentation_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(row.get(field) if row.get(field) is not None else "")
        for field in (
            "file_name",
            "view_type",
            "model",
            "price",
            "evidence_guard_revision",
        )
    )


def _comparable(value: object) -> str:
    text = str(value if value is not None else "").strip()
    if text.lower() in {"", "null", "none"}:
        return ""
    return text


def build_recovery_plan(
    *,
    priority_dir: Path,
    output_dir: Path,
    backend_url: str,
    expected_stopped_worker_pid: int | None = None,
    inactive_priority: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    priority_dir = priority_dir.resolve()
    output_dir = output_dir.resolve()
    status = _request_json(backend_url, "/api/status")
    current_dir = Path(
        str(status.get("current_relative_dir") or status.get("image_dir") or "")
    ).resolve()
    stats = dict(status.get("stats") or {})
    upload = dict(status.get("stream_upload") or {})
    total = int(stats.get("total") or 0)
    if inactive_priority:
        if current_dir == priority_dir:
            raise RuntimeError("inactive priority recovery cannot target the active directory")
    elif current_dir != priority_dir or status.get("is_running"):
        raise RuntimeError("backend is not idle at the exact priority directory")
    if status.get("runtime_health_fuse"):
        raise RuntimeError("runtime health fuse is active")
    if not inactive_priority and (
        int(stats.get("success") or 0) != total
        or int(stats.get("verified") or 0) != total
        or int(stats.get("failed") or 0) != 0
        or int(stats.get("review_required") or 0) != 0
        or int(stats.get("verification_unknown") or 0) != 0
    ):
        raise RuntimeError("priority results are not fully verified")
    worker_pid = int(upload.get("worker_pid") or 0)
    if int(upload.get("pending") or 0) != 0 or int(upload.get("working") or 0) != 0:
        raise RuntimeError("stream uploader is not at an idle boundary")
    if expected_stopped_worker_pid is None:
        if worker_pid <= 0 or not psutil.pid_exists(worker_pid):
            raise RuntimeError("stream uploader is not at a live idle boundary")
    elif (
        expected_stopped_worker_pid <= 0
        or worker_pid != expected_stopped_worker_pid
        or psutil.pid_exists(expected_stopped_worker_pid)
    ):
        raise RuntimeError(
            "stream uploader does not match the exact stopped idle boundary"
        )

    source_map = _read_json(priority_dir / ".ocr_source_map.json")
    source_items = dict(source_map.get("items") or {})
    full_results = _full_results(priority_dir)
    if inactive_priority:
        nonempty_failures: list[str] = []
        for path in sorted(priority_dir.glob("*-OCR*.json")):
            if "失敗" not in path.name:
                continue
            payload = _read_json(path)
            if isinstance(payload, list) and payload:
                nonempty_failures.append(path.name)
        if (
            not source_items
            or len(full_results) != len(source_items)
            or nonempty_failures
            or any(
                row.get("auto_verified") is not True
                or row.get("auto_review_required") is True
                for row in full_results.values()
            )
        ):
            raise RuntimeError(
                "inactive priority directory is not a complete verified frozen batch"
            )
        presentation_keys = {
            _presentation_key(row) for row in full_results.values()
        }
    else:
        presentation = _request_json(backend_url, "/api/success_records")
        if not isinstance(presentation, list):
            raise RuntimeError("success record API did not return a list")
        presentation_keys = {_presentation_key(row) for row in presentation}
    traces = _trace_authorities(
        output_dir / "_ocr_audit" / "v1945_evidence_trace.jsonl"
    )

    root = output_dir / "_drive_upload_stream"
    failed_dir = root / "failed"
    receipt_dir = root / "receipts"
    recoverable: list[dict[str, Any]] = []
    stale_markers: list[dict[str, Any]] = []
    for failed_path in sorted(failed_dir.glob("*.json")):
        job = _read_json(failed_path)
        source_id = str(job.get("source_item_id") or "")
        matching_names = [
            name
            for name, info in source_items.items()
            if str((info or {}).get("source_item_id") or "") == source_id
        ]
        if len(matching_names) != 1:
            continue
        file_name = matching_names[0]
        row = full_results.get(file_name)
        if not isinstance(row, dict):
            continue
        info = dict(source_items[file_name] or {})
        receipt_path = receipt_dir / failed_path.name
        if receipt_path.exists():
            receipt = _read_json(receipt_path)
            original_source = normalized(
                str(info.get("original_source_path") or "")
            )
            published = normalized(str(receipt.get("published_path") or ""))
            current_plan = plan_single_image(
                original_source,
                row,
                str(info.get("period") or ""),
                "＄",
                current_year=datetime.now().year,
            )
            receipt_trace_rows = traces.get(
                (source_id, str(receipt.get("run_id") or "")),
                [],
            )
            staging_source = priority_dir / file_name
            prepared_hash = (
                prepared_input_sha256(staging_source)
                if staging_source.is_file()
                else ""
            )
            if (
                receipt.get("schema") == "samsung-ocr-stream-receipt-v1"
                and receipt.get("source_item_id") == source_id
                and receipt.get("drive_file_id")
                and receipt.get("remote_path")
                and normalized(str(receipt.get("original_source_path") or ""))
                == original_source
                and receipt.get("period") == info.get("period") == "202606"
                and receipt.get("source_sha256")
                == sha256_file(original_source)
                and published.is_file()
                and receipt.get("published_sha256") == sha256_file(published)
                and current_plan.get("status") == READY_STATUS
                and current_plan.get("target_name") == receipt.get("file_name")
                and receipt_revision_is_proven(
                    source_item_id=source_id,
                    record_revision=row.get("evidence_guard_revision"),
                    receipt=receipt,
                    migration_dir=root / "revision_migrations",
                    original_source_path=original_source,
                )
                and any(
                    _clean_trace_row(
                        trace_row,
                        input_image_sha256=prepared_hash,
                    )
                    for trace_row in receipt_trace_rows
                )
            ):
                stale_markers.append(
                    {
                        "source_item_id": source_id,
                        "file_name": file_name,
                        "failed_path": str(failed_path),
                        "receipt_path": str(receipt_path),
                    }
                )
            continue
        failure = str(job.get("error") or "")
        recovery_reason = ""
        if failure.startswith("runtime health fuse is active:"):
            recovery_reason = "runtime_health_fuse_cleared_after_ocr_finalization"
        elif (
            failure == "stale or invalid stream upload job"
            and job.get("evidence_guard_revision") == EVIDENCE_GUARD_REVISION
        ):
            recovery_reason = "current_revision_rejected_by_older_uploader"
        if (
            job.get("schema") != STREAM_SCHEMA
            or not recovery_reason
            or _guard_key(job.get("evidence_guard_revision"))
            < MIN_FROZEN_RECOVERY_GUARD
        ):
            continue
        source = Path(str(info.get("original_source_path") or "")).resolve()
        if (
            not source.is_file()
            or job.get("original_source_path") != str(source)
            or job.get("source_sha256") != sha256_file(source)
            or not (job.get("period") == info.get("period") == "202606")
            or job.get("source_item_id") != info.get("source_item_id")
            or job.get("evidence_guard_revision")
            != row.get("evidence_guard_revision")
            or _presentation_key(row) not in presentation_keys
            or row.get("auto_verified") is not True
            or row.get("auto_review_required") is True
        ):
            continue
        if any(
            _comparable(job.get("final_result", {}).get(field))
            != _comparable(row.get(field))
            for field in PRESENTATION_RESULT_FIELDS
        ):
            continue
        trace_rows = traces.get((source_id, str(job.get("run_id") or "")), [])
        if not any(
            _clean_trace_row(
                trace_row,
                input_image_sha256=str(job.get("input_image_sha256") or ""),
            )
            for trace_row in trace_rows
        ):
            continue
        recoverable.append(
            {
                "source_item_id": source_id,
                "file_name": file_name,
                "failed_path": str(failed_path),
                "failed_job_sha256": _canonical_sha256(job),
                "source_revision": job["evidence_guard_revision"],
                "source_sha256": job["source_sha256"],
                "input_image_sha256": job["input_image_sha256"],
                "run_id": job["run_id"],
                "target_name": job["target_name"],
                "recovery_reason": recovery_reason,
            }
        )
    return recoverable, stale_markers


def apply_recovery(
    *,
    recoverable: list[dict[str, Any]],
    stale_markers: list[dict[str, Any]],
    output_dir: Path,
) -> Path:
    output_dir = output_dir.resolve()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_root = (
        output_dir
        / "_ocr_audit"
        / "fuse_failed_upload_recovery"
        / stamp
    )
    archive_failed = archive_root / "failed_jobs"
    archive_stale = archive_root / "stale_failed_markers"
    pending_dir = output_dir / "_drive_upload_stream" / "pending"
    manifest_rows: list[dict[str, Any]] = []

    for item in recoverable:
        failed_path = Path(item["failed_path"]).resolve()
        expected_failed_root = (
            output_dir / "_drive_upload_stream" / "failed"
        ).resolve()
        failed_path.relative_to(expected_failed_root)
        pending_path = pending_dir / failed_path.name
        receipt_path = (
            output_dir / "_drive_upload_stream" / "receipts" / failed_path.name
        )
        if pending_path.exists() or receipt_path.exists():
            raise RuntimeError(f"recovery target already exists: {failed_path.name}")
        job = _read_json(failed_path)
        if _canonical_sha256(job) != item["failed_job_sha256"]:
            raise RuntimeError(f"failed job changed before recovery: {failed_path.name}")
        archived = archive_failed / failed_path.name
        _atomic_json(archived, job)
        recovery = {
            "schema": FUSE_UPLOAD_RECOVERY_SCHEMA,
            "reason": item["recovery_reason"],
            "approved_uploader_revision": EVIDENCE_GUARD_REVISION,
            "source_item_id": item["source_item_id"],
            "source_revision": item["source_revision"],
            "source_sha256": item["source_sha256"],
            "input_image_sha256": item["input_image_sha256"],
            "run_id": item["run_id"],
            "target_name": item["target_name"],
            "failed_job_sha256": item["failed_job_sha256"],
            "archived_failed_job": str(archived.resolve()),
            "prepared_at": datetime.now().isoformat(timespec="seconds"),
        }
        recovered_job = dict(job)
        recovered_job["fuse_failed_upload_recovery"] = recovery
        valid, errors = validate_fuse_failed_upload_recovery(
            recovered_job,
            output_dir=output_dir,
        )
        if not valid:
            raise RuntimeError(
                f"recovered job failed its own contract: {failed_path.name}: {errors}"
            )
        _atomic_json(pending_path, recovered_job)
        failed_path.unlink()
        manifest_rows.append(
            {
                **item,
                "action": "requeued",
                "pending_path": str(pending_path),
                "archived_failed_job": str(archived),
            }
        )

    for item in stale_markers:
        failed_path = Path(item["failed_path"]).resolve()
        archived = archive_stale / failed_path.name
        job = _read_json(failed_path)
        _atomic_json(archived, job)
        failed_path.unlink()
        manifest_rows.append(
            {
                **item,
                "action": "archived_stale_failed_marker",
                "archived_failed_job": str(archived),
            }
        )

    manifest = archive_root / "manifest.json"
    _atomic_json(
        manifest,
        {
            "schema": "samsung-ocr-fuse-failed-upload-recovery-manifest-v1",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "recoverable_count": len(recoverable),
            "stale_marker_count": len(stale_markers),
            "rows": manifest_rows,
        },
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recover immutable stream-upload jobs failed only by a cleared runtime fuse"
    )
    parser.add_argument("--priority-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--backend-url", default="http://127.0.0.1:5002")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--expected-stopped-worker-pid",
        type=int,
        help=(
            "Allow an apply only after this exact uploader PID was stopped at an "
            "already-proven idle boundary; the backend status must still name it"
        ),
    )
    parser.add_argument(
        "--inactive-priority",
        action="store_true",
        help=(
            "Recover a complete verified priority directory only while the backend "
            "is actively working in a different directory"
        ),
    )
    args = parser.parse_args()

    recoverable, stale_markers = build_recovery_plan(
        priority_dir=Path(args.priority_dir),
        output_dir=Path(args.output_dir),
        backend_url=args.backend_url,
        expected_stopped_worker_pid=args.expected_stopped_worker_pid,
        inactive_priority=args.inactive_priority,
    )
    summary = {
        "recoverable": len(recoverable),
        "stale_markers": len(stale_markers),
        "apply": bool(args.apply),
    }
    if args.apply:
        summary["manifest"] = str(
            apply_recovery(
                recoverable=recoverable,
                stale_markers=stale_markers,
                output_dir=Path(args.output_dir),
            )
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

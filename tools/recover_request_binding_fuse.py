"""Recover one final-call request-binding failure without a fourth model call.

This tool is intentionally narrow.  It accepts only an active
request_binding_unverified fuse on call three, requires exactly two earlier
healthy/request-bound calls with an identical non-FollowMe SKU/price result,
then discards the unbound response and persists the two-call consensus.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.audit_fields import (
    EVIDENCE_CONTRACT_VERSION,
    EVIDENCE_GUARD_REVISION,
    finalize_three_pass_outcome,
    validate_evidence_contract,
)
from tools.stream_drive_upload import enqueue_finalized_result


ALLOWED_FUSE_REASONS = {"request_binding_unverified"}
RECOVERY_RULE = "two_bound_pass_consensus_discarded_unbound_third"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _result_task(row: dict[str, Any], task_id: int) -> dict[str, Any]:
    result_items = [
        {
            "from_name": "category",
            "to_name": "image",
            "type": "choices",
            "origin": "prediction",
            "value": {"choices": [row["category"]]},
        },
        {
            "from_name": "model",
            "to_name": "image",
            "type": "textarea",
            "origin": "prediction",
            "value": {"text": [str(row.get("model") or "null")]},
        },
        {
            "from_name": "price",
            "to_name": "image",
            "type": "textarea",
            "origin": "prediction",
            "value": {"text": [str(row.get("price") or "null")]},
        },
    ]
    return {
        "id": task_id,
        "data": {
            "image": f"/data/upload/1/{row['file_name']}",
            "ocr_meta": {
                key: row.get(key)
                for key in (
                    "view_type",
                    "screen_status",
                    "quality_issue",
                    "price_status",
                    "price_symbol",
                    "official_price",
                    "price_diff_percent",
                    "ocr_attempt",
                    "auto_retry_reasons",
                    "auto_verified",
                    "auto_review_required",
                    "review_status",
                    "evidence_contract_version",
                    "evidence_guard_revision",
                    "evidence_contract_valid",
                    "model_validation_failed",
                    "rejected_model",
                    "price_conflict_detected",
                    "three_pass_adjudicated",
                    "adjudication_rule",
                    "adjudication_summary",
                )
            },
        },
        "annotations": [
            {
                "id": task_id,
                "created_at": row["timestamp"],
                "result": result_items,
                "was_cancelled": False,
                "ground_truth": False,
            }
        ],
    }


def recover(
    *,
    staging_dir: Path,
    result_file: Path,
    fuse_file: Path,
    upload_output_dir: Path,
    apply: bool,
) -> dict[str, Any]:
    staging_dir = staging_dir.resolve()
    result_file = result_file.resolve()
    fuse_file = fuse_file.resolve()
    retry_file = staging_dir / ".ocr_retry_queue.json"
    source_map_file = staging_dir / ".ocr_source_map.json"

    fuse = _read_json(fuse_file)
    reasons = {str(item) for item in fuse.get("reasons") or [] if str(item)}
    file_name = str(fuse.get("source_file") or "")
    if fuse.get("active") is not True:
        raise RuntimeError("runtime fuse is not active")
    if reasons != ALLOWED_FUSE_REASONS:
        raise RuntimeError(f"unsupported fuse reasons: {sorted(reasons)}")
    if int(fuse.get("attempt") or 0) != 3 or not file_name:
        raise RuntimeError("recovery requires one named photo stopped on call three")

    retry_state = _read_json(retry_file)
    if Path(str(retry_state.get("image_dir") or "")).resolve() != staging_dir:
        raise RuntimeError("retry state belongs to a different staging directory")
    if int((retry_state.get("auto_attempts") or {}).get(file_name) or 0) != 3:
        raise RuntimeError("retry state does not prove exactly three calls")
    history = list((retry_state.get("auto_result_history") or {}).get(file_name) or [])
    if len(history) != 2:
        raise RuntimeError("recovery requires exactly two persisted valid calls")

    source_map = _read_json(source_map_file)
    source_info = (source_map.get("items") or {}).get(file_name) or {}
    source_item_id = str(source_info.get("source_item_id") or "")
    original_source = Path(str(source_info.get("original_source_path") or "")).resolve()
    period = str(source_info.get("period") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", source_item_id):
        raise RuntimeError("source map has no stable source_item_id")
    if not original_source.is_file() or not re.fullmatch(r"20\d{4}", period):
        raise RuntimeError("source map has no valid original file/period")

    current = dict(history[-1])
    current.update(
        {
            "file_name": file_name,
            "source_path": str((staging_dir / file_name).resolve()),
            "original_source_path": str(original_source),
            "source_item_id": source_item_id,
            "period": period,
            "ocr_attempt": 3,
            "run_id": str(fuse.get("run_id") or ""),
            "timestamp": datetime.now().isoformat(),
            "request_binding_enforced": True,
            "request_id_verified": False,
            "independent_pass": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
            "runtime_health": {
                "healthy": False,
                "allow_processing": True,
                "allow_upload": False,
                "reasons": sorted(reasons),
            },
        }
    )
    decision = finalize_three_pass_outcome(
        current,
        history,
        {
            "attempt": 3,
            "retry": False,
            "unresolved": True,
            "verified": False,
            "technical_retry_required": True,
            "technical_retry_reason": "request_binding_unverified",
            "reasons": sorted(reasons),
        },
        3,
    )
    if decision.get("verified") is not True or decision.get("adjudication_rule") != RECOVERY_RULE:
        raise RuntimeError(f"two-bound-pass recovery proof failed: {decision}")

    valid, errors, normalized = validate_evidence_contract(current)
    if not valid:
        raise RuntimeError("recovered result failed evidence contract: " + ";".join(errors))
    current.update(
        {
            "normalized_evidence": normalized,
            "evidence_contract_version": EVIDENCE_CONTRACT_VERSION,
            "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
            "evidence_contract_valid": True,
            "auto_verified": True,
            "auto_review_required": False,
            "review_status": "已完成判讀",
            "technical_retry_required": False,
            "technical_retry_exhausted": False,
            "auto_retry_reasons": "",
        }
    )

    tasks = _read_json(result_file)
    if not isinstance(tasks, list):
        raise RuntimeError("result file is not a Label Studio task list")
    existing = [
        task
        for task in tasks
        if Path(str((task.get("data") or {}).get("image") or "")).name == file_name
    ]
    if existing:
        raise RuntimeError("result file already contains the fused photo")
    task_id = max((int(task.get("id") or 0) for task in tasks), default=0) + 1

    report = {
        "status": "would_recover" if not apply else "recovered",
        "file_name": file_name,
        "source_item_id": source_item_id,
        "period": period,
        "model": current.get("model"),
        "price": current.get("price"),
        "input_image_sha256": current.get("input_image_sha256"),
        "valid_bound_calls": 2,
        "discarded_unbound_call": 3,
        "fourth_call_made": False,
        "adjudication_rule": RECOVERY_RULE,
    }
    if not apply:
        return report

    # Queue first.  The uploader remains fail-closed while the fuse exists.
    queued = enqueue_finalized_result(current, output_dir=upload_output_dir)
    if queued is None:
        raise RuntimeError("recovered result did not pass the upload queue gate")

    tasks.append(_result_task(current, task_id))
    _atomic_json(result_file, tasks)

    attempts = retry_state.get("auto_attempts") or {}
    result_history = retry_state.get("auto_result_history") or {}
    attempts.pop(file_name, None)
    result_history.pop(file_name, None)
    retry_state["auto_attempts"] = attempts
    retry_state["auto_result_history"] = result_history
    retry_state["retry_queue"] = [
        item for item in (retry_state.get("retry_queue") or []) if item != file_name
    ]
    retry_state["priority_queue"] = [
        item for item in (retry_state.get("priority_queue") or []) if item != file_name
    ]
    retry_state["updated_at"] = datetime.now().isoformat()
    _atomic_json(retry_file, retry_state)

    cleared_at = datetime.now().isoformat()
    receipt = {
        **report,
        "status": "recovered",
        "queued_job": str(queued),
        "result_file": str(result_file),
        "cleared_at": cleared_at,
    }
    audit_dir = fuse_file.parent
    receipt_path = audit_dir / "request_binding_recovery" / (
        f"{datetime.now():%Y%m%d_%H%M%S}_{source_item_id[:12]}.json"
    )
    _atomic_json(receipt_path, receipt)
    fuse_history = audit_dir / "runtime_health_fuse_history" / (
        f"{datetime.now():%Y%m%d_%H%M%S}_{source_item_id[:12]}.json"
    )
    _atomic_json(
        fuse_history,
        {
            **fuse,
            "active": False,
            "cleared_at": cleared_at,
            "clearance": RECOVERY_RULE,
            "recovery_receipt": str(receipt_path),
        },
    )
    fuse_file.unlink()
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--result-file", type=Path, required=True)
    parser.add_argument("--fuse-file", type=Path, required=True)
    parser.add_argument("--upload-output-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = recover(
        staging_dir=args.staging_dir,
        result_file=args.result_file,
        fuse_file=args.fuse_file,
        upload_output_dir=args.upload_output_dir,
        apply=args.apply,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

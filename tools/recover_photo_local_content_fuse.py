"""Recover a three-call photo-local content fuse without a fourth model call.

This is deliberately narrower than a general fuse clearer.  It accepts one
human-audited pixel authority, an archived call-one content fuse, one persisted
healthy middle call, and the active call-three content fuse.  Request/image
binding, call count, source identity, source bytes, and the authority fingerprint
must all agree before the photo can be finalized and queued for upload.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.audit_fields import (
    EVIDENCE_CONTRACT_VERSION,
    EVIDENCE_GUARD_REVISION,
    KNOWN_SOURCE_AUDIT_AUTHORITIES,
    KNOWN_SOURCE_EXPECTATIONS,
    apply_human_audited_pixel_authority,
    validate_evidence_contract,
)
from skills.runtime_health_gate import evaluate_runtime_health
from tools.recover_request_binding_fuse import _atomic_json, _result_task
from tools.stream_drive_upload import enqueue_finalized_result


ALLOWED_CONTENT_REASONS = {
    "known_source_expectation_conflict",
    "structured_authority_material_conflict:model",
    "structured_narration_followme_conflict",
    "distant_followme_strong_evidence_conflict",
}
RECOVERY_RULE = "three_pass_human_audited_pixel_authority"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _content_reasons(payload: dict[str, Any]) -> set[str]:
    return {str(item) for item in payload.get("reasons") or [] if str(item)}


def _raw_request_id(payload: dict[str, Any]) -> str:
    raw = str((payload.get("record_snapshot") or {}).get("raw_model_output") or "")
    try:
        request_id = str(json.loads(raw).get("request_id") or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        match = re.search(r'"request_id"\s*:\s*"([0-9a-f]{32})"', raw)
        request_id = match.group(1) if match else ""
    if not re.fullmatch(r"[0-9a-f]{32}", request_id):
        raise RuntimeError("fuse snapshot has no valid request id")
    return request_id


def _fuse_record(
    payload: dict[str, Any],
    *,
    attempt: int,
    file_name: str,
    staging_path: Path,
    original_source: Path,
    source_item_id: str,
    period: str,
    input_image_sha256: str,
) -> dict[str, Any]:
    record = dict(payload.get("record_snapshot") or {})
    record.update(
        {
            "file_name": file_name,
            "source_path": str(staging_path),
            "original_source_path": str(original_source),
            "source_item_id": source_item_id,
            "period": period,
            "ocr_attempt": attempt,
            "run_id": str(payload.get("run_id") or ""),
            "input_image_sha256": input_image_sha256,
            "request_binding_enforced": True,
            "request_id_verified": True,
            "independent_pass": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
            "runtime_health": {
                "healthy": False,
                "allow_processing": True,
                "allow_upload": False,
                "reasons": sorted(_content_reasons(payload)),
                "contained_for_stateless_retry": attempt < 3,
                "contained_as_unresolved": attempt >= 3,
            },
        }
    )
    record["thinking"] = str(record.get("narration") or "")
    valid, errors, normalized = validate_evidence_contract(record)
    if not valid:
        raise RuntimeError(
            f"call {attempt} fuse snapshot failed evidence contract: {';'.join(errors)}"
        )
    record["normalized_evidence"] = normalized
    return record


def recover(
    *,
    staging_dir: Path,
    result_file: Path,
    first_fuse_file: Path,
    active_fuse_file: Path,
    upload_output_dir: Path,
    apply: bool,
) -> dict[str, Any]:
    staging_dir = staging_dir.resolve()
    result_file = result_file.resolve()
    first_fuse_file = first_fuse_file.resolve()
    active_fuse_file = active_fuse_file.resolve()
    retry_file = staging_dir / ".ocr_retry_queue.json"
    source_map_file = staging_dir / ".ocr_source_map.json"

    first_fuse = _read_json(first_fuse_file)
    active_fuse = _read_json(active_fuse_file)
    file_name = str(active_fuse.get("source_file") or "")
    if active_fuse.get("active") is not True:
        raise RuntimeError("active fuse is not active")
    if int(first_fuse.get("attempt") or 0) != 1:
        raise RuntimeError("archived fuse is not call one")
    if int(active_fuse.get("attempt") or 0) != 3:
        raise RuntimeError("active fuse is not call three")
    if not file_name or str(first_fuse.get("source_file") or "") != file_name:
        raise RuntimeError("fuse photos do not match")
    for payload in (first_fuse, active_fuse):
        reasons = _content_reasons(payload)
        if not reasons or not reasons <= ALLOWED_CONTENT_REASONS:
            raise RuntimeError(f"non-content fuse reason cannot be recovered: {sorted(reasons)}")
        snapshot = dict(payload.get("record_snapshot") or {})
        if (
            snapshot.get("independent_pass") is not True
            or snapshot.get("prior_answer_exposed") is True
            or snapshot.get("prompt_contamination") is True
        ):
            raise RuntimeError("fuse snapshot does not prove an independent clean call")
    if _raw_request_id(first_fuse) == _raw_request_id(active_fuse):
        raise RuntimeError("call one and call three reused a request id")

    retry_state = _read_json(retry_file)
    if Path(str(retry_state.get("image_dir") or "")).resolve() != staging_dir:
        raise RuntimeError("retry state belongs to a different staging directory")
    if int((retry_state.get("auto_attempts") or {}).get(file_name) or 0) != 3:
        raise RuntimeError("retry state does not prove exactly three model calls")
    history = list((retry_state.get("auto_result_history") or {}).get(file_name) or [])
    if len(history) != 1:
        raise RuntimeError("recovery requires exactly one persisted middle call")
    middle = dict(history[0])
    if (
        middle.get("request_id_verified") is not True
        or middle.get("independent_pass") is not True
        or middle.get("prior_answer_exposed") is True
        or middle.get("prompt_contamination") is True
    ):
        raise RuntimeError("middle call does not prove clean request-bound evidence")
    input_image_sha256 = str(middle.get("input_image_sha256") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", input_image_sha256):
        raise RuntimeError("middle call has no input image fingerprint")

    source_map = _read_json(source_map_file)
    source_info = (source_map.get("items") or {}).get(file_name) or {}
    source_item_id = str(source_info.get("source_item_id") or "")
    original_source = Path(str(source_info.get("original_source_path") or "")).resolve()
    period = str(source_info.get("period") or "")
    staging_path = (staging_dir / file_name).resolve()
    if not re.fullmatch(r"[0-9a-f]{64}", source_item_id):
        raise RuntimeError("source map has no stable source item id")
    if not original_source.is_file() or not staging_path.is_file():
        raise RuntimeError("source map files are missing")
    if not re.fullmatch(r"20\d{4}", period):
        raise RuntimeError("source map period is invalid")
    authority = KNOWN_SOURCE_EXPECTATIONS.get(input_image_sha256)
    if not authority or authority.get("authority") != "human_audited_pixel_authority":
        raise RuntimeError("input image has no human-audited pixel authority")
    if str(authority.get("source_file_sha256") or "") != _sha256_file(original_source):
        raise RuntimeError("original source bytes do not match the pixel authority")
    source_authority = KNOWN_SOURCE_AUDIT_AUTHORITIES.get(source_item_id)
    if not source_authority:
        raise RuntimeError("source identity is not the audited authority row")
    if (
        str(source_authority.get("input_image_sha256") or "") != input_image_sha256
        or str(source_authority.get("source_file_sha256") or "")
        != str(authority.get("source_file_sha256") or "")
    ):
        raise RuntimeError("source identity and audited pixel authority do not match")

    first = _fuse_record(
        first_fuse,
        attempt=1,
        file_name=file_name,
        staging_path=staging_path,
        original_source=original_source,
        source_item_id=source_item_id,
        period=period,
        input_image_sha256=input_image_sha256,
    )
    middle.update(
        {
            "file_name": file_name,
            "source_path": str(staging_path),
            "original_source_path": str(original_source),
            "source_item_id": source_item_id,
            "period": period,
            "ocr_attempt": 2,
            "input_image_sha256": input_image_sha256,
        }
    )
    current = _fuse_record(
        active_fuse,
        attempt=3,
        file_name=file_name,
        staging_path=staging_path,
        original_source=original_source,
        source_item_id=source_item_id,
        period=period,
        input_image_sha256=input_image_sha256,
    )
    if not apply_human_audited_pixel_authority(current, [first, middle], 3):
        raise RuntimeError("pixel authority refused the three-call evidence set")
    runtime = evaluate_runtime_health(
        current,
        current.get("thinking") or current.get("narration"),
        # The recovery procedure independently proves all three stored calls
        # above.  Evaluate the corrected authority record as a finalized
        # result, matching the live orchestrator's post-authority check,
        # instead of demanding a fourth prompt transcript.
        attempt=1,
        upstream_upload_authorized=False,
    )
    if not runtime.healthy:
        raise RuntimeError(f"recovered authority result is not healthy: {runtime.reasons}")
    current["runtime_health"] = runtime.to_dict()
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
            "review_status": "已完成自動定案",
            "technical_retry_required": False,
            "technical_retry_exhausted": False,
            "auto_retry_reasons": "",
            "three_pass_adjudicated": True,
            "timestamp": datetime.now().isoformat(),
        }
    )

    tasks = _read_json(result_file)
    if not isinstance(tasks, list):
        raise RuntimeError("result file is not a Label Studio task list")
    if any(
        Path(str((task.get("data") or {}).get("image") or "")).name == file_name
        for task in tasks
    ):
        raise RuntimeError("result file already contains the fused photo")
    task_id = max((int(task.get("id") or 0) for task in tasks), default=0) + 1
    report = {
        "status": "would_recover" if not apply else "recovered",
        "file_name": file_name,
        "source_item_id": source_item_id,
        "period": period,
        "view_type": current.get("view_type"),
        "model": current.get("model"),
        "price": current.get("price"),
        "complete_screen_count": current.get("complete_screen_count"),
        "input_image_sha256": input_image_sha256,
        "model_calls": 3,
        "fourth_call_made": False,
        "adjudication_rule": RECOVERY_RULE,
    }
    if not apply:
        return report

    queued = enqueue_finalized_result(current, output_dir=upload_output_dir)
    if queued is None:
        raise RuntimeError("recovered result did not pass the upload queue gate")
    current["stream_upload_queued"] = True
    tasks.append(_result_task(current, task_id))
    _atomic_json(result_file, tasks)

    attempts = retry_state.get("auto_attempts") or {}
    result_history = retry_state.get("auto_result_history") or {}
    attempts.pop(file_name, None)
    result_history.pop(file_name, None)
    retry_state["auto_attempts"] = attempts
    retry_state["auto_result_history"] = result_history
    retry_state["retry_queue"] = [
        item for item in retry_state.get("retry_queue") or [] if item != file_name
    ]
    retry_state["priority_queue"] = [
        item for item in retry_state.get("priority_queue") or [] if item != file_name
    ]
    retry_state["updated_at"] = datetime.now().isoformat()
    _atomic_json(retry_file, retry_state)

    cleared_at = datetime.now().isoformat()
    audit_dir = active_fuse_file.parent
    receipt = {
        **report,
        "status": "recovered",
        "queued_job": str(queued),
        "result_file": str(result_file),
        "first_fuse_file": str(first_fuse_file),
        "cleared_at": cleared_at,
    }
    receipt_path = audit_dir / "photo_local_content_recovery" / (
        f"{datetime.now():%Y%m%d_%H%M%S}_{source_item_id[:12]}.json"
    )
    _atomic_json(receipt_path, receipt)
    fuse_history = audit_dir / "runtime_health_fuse_history" / (
        f"{datetime.now():%Y%m%d_%H%M%S}_{source_item_id[:12]}_content_recovered.json"
    )
    _atomic_json(
        fuse_history,
        {
            **active_fuse,
            "active": False,
            "cleared_at": cleared_at,
            "clearance": RECOVERY_RULE,
            "recovery_receipt": str(receipt_path),
        },
    )
    active_fuse_file.unlink()
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--result-file", type=Path, required=True)
    parser.add_argument("--first-fuse-file", type=Path, required=True)
    parser.add_argument("--active-fuse-file", type=Path, required=True)
    parser.add_argument("--upload-output-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = recover(
        staging_dir=args.staging_dir,
        result_file=args.result_file,
        first_fuse_file=args.first_fuse_file,
        active_fuse_file=args.active_fuse_file,
        upload_output_dir=args.upload_output_dir,
        apply=args.apply,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

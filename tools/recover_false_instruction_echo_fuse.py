"""Recover one false instruction-echo fuse after call three, without call four.

The recovery is deliberately narrow: the active fuse must contain only
``ui_narration_instruction_echo``; all three outputs must be request-bound,
stateless and tied to the same image; the narration must be healthy under the
fixed detector; and the source must have a byte-bound human pixel authority.
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


FUSE_REASON = "ui_narration_instruction_echo"
RECOVERY_RULE = "three_bound_calls_false_instruction_echo_pixel_authority"
ALLOWED_PRIOR_LOCAL_REASONS = {"structured_narration_followme_conflict"}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prove_prior_calls(history: list[dict[str, Any]], image_hash: str) -> None:
    if len(history) != 2:
        raise RuntimeError("recovery requires exactly two durable earlier calls")
    local_conflicts = 0
    for row in history:
        if not (
            str(row.get("input_image_sha256") or "").strip().lower() == image_hash
            and row.get("request_id_verified") is True
            and row.get("request_binding_enforced") is True
            and row.get("independent_pass") is True
            and row.get("prior_answer_exposed") is not True
            and row.get("prompt_contamination") is not True
        ):
            raise RuntimeError("earlier call is not cleanly bound to the same image")
        runtime = row.get("runtime_health") or {}
        reasons = {str(item) for item in runtime.get("reasons") or [] if str(item)}
        if runtime.get("healthy") is True and not reasons:
            continue
        if runtime.get("healthy") is False and reasons and reasons <= ALLOWED_PRIOR_LOCAL_REASONS:
            local_conflicts += 1
            continue
        raise RuntimeError("earlier call contains an unsupported runtime failure")
    if local_conflicts > 1:
        raise RuntimeError("more than one earlier call carried a local content conflict")


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
    if fuse.get("active") is not True or reasons != {FUSE_REASON}:
        raise RuntimeError(f"unsupported active fuse: {sorted(reasons)}")
    if int(fuse.get("attempt") or 0) != 3 or not file_name:
        raise RuntimeError("recovery requires one named photo stopped after call three")

    retry = _read_json(retry_file)
    if Path(str(retry.get("image_dir") or "")).resolve() != staging_dir:
        raise RuntimeError("retry state belongs to another staging directory")
    attempts = retry.get("auto_attempts") or {}
    histories = retry.get("auto_result_history") or {}
    if int(attempts.get(file_name) or 0) != 3:
        raise RuntimeError("retry state does not prove exactly three model calls")
    history = [dict(item) for item in histories.get(file_name) or []]

    source_map = _read_json(source_map_file)
    source_info = (source_map.get("items") or {}).get(file_name) or {}
    source_item_id = str(source_info.get("source_item_id") or "")
    original_source = Path(str(source_info.get("original_source_path") or "")).resolve()
    staged_source = (staging_dir / file_name).resolve()
    period = str(source_info.get("period") or "")
    if not (
        re.fullmatch(r"[0-9a-f]{64}", source_item_id)
        and original_source.is_file()
        and staged_source.is_file()
        and re.fullmatch(r"20\d{4}", period)
    ):
        raise RuntimeError("source identity is incomplete")

    authority = KNOWN_SOURCE_AUDIT_AUTHORITIES.get(source_item_id)
    if not authority or authority.get("authority") != "human_audited_pixel_authority":
        raise RuntimeError("source has no exact human pixel authority")
    image_hash = str(authority.get("input_image_sha256") or "").strip().lower()
    if (
        KNOWN_SOURCE_EXPECTATIONS.get(image_hash) is not authority
        or str(authority.get("source_file_sha256") or "") != _sha256_file(original_source)
        or _sha256_file(staged_source) != _sha256_file(original_source)
    ):
        raise RuntimeError("source bytes or pixel authority do not match")
    _prove_prior_calls(history, image_hash)

    snapshot = dict(fuse.get("record_snapshot") or {})
    try:
        raw = json.loads(str(snapshot.get("raw_model_output") or ""))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("fuse has no parseable third output") from exc
    if not re.fullmatch(r"[0-9a-f]{32}", str(raw.get("request_id") or "")):
        raise RuntimeError("third output has no request id")
    current = dict(raw)
    current.update(snapshot)
    current.update(
        {
            "file_name": file_name,
            "source_path": str(staged_source),
            "original_source_path": str(original_source),
            "source_item_id": source_item_id,
            "period": period,
            "ocr_attempt": 3,
            "run_id": str(fuse.get("run_id") or ""),
            "timestamp": datetime.now().isoformat(),
            "thinking": str(snapshot.get("narration") or raw.get("narration") or ""),
            "input_image_sha256": image_hash,
            "request_binding_enforced": True,
            "request_id_verified": True,
            "independent_pass": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
        }
    )
    # The live call could only set the bound/independent flags after its prompt
    # passed the attempt-three preflight. Recheck the fixed narration detector
    # as attempt one here so the absent historical prompt transcript cannot be
    # mistaken for a new fourth-call preflight failure.
    repaired_runtime = evaluate_runtime_health(
        current,
        current["thinking"],
        attempt=1,
        upstream_upload_authorized=False,
    )
    repaired_reasons = set(repaired_runtime.reasons)
    # Registering the audited pixels intentionally makes the third model vote
    # conflict with that authority.  That one photo-local reason is expected;
    # every presentation/prompt/binding reason, especially the old false echo,
    # must be gone before authority may be applied.
    if repaired_reasons != {"known_source_expectation_conflict"}:
        raise RuntimeError(f"third output remains unexpectedly unhealthy: {repaired_runtime.reasons}")
    current["runtime_health"] = repaired_runtime.to_dict()

    if not apply_human_audited_pixel_authority(current, history, 3):
        raise RuntimeError("pixel authority refused the three bound calls")
    final_runtime = evaluate_runtime_health(
        current,
        current.get("thinking"),
        attempt=1,
        upstream_upload_authorized=False,
    )
    if not final_runtime.healthy:
        raise RuntimeError(f"authoritative result remains unhealthy: {final_runtime.reasons}")
    current["runtime_health"] = final_runtime.to_dict()
    valid, errors, normalized = validate_evidence_contract(current)
    if not valid:
        raise RuntimeError("authoritative result failed contract: " + ";".join(errors))
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
            "adjudication_rule": RECOVERY_RULE,
            "adjudication_summary": (
                "三次無記憶綁圖呼叫已用滿；第三輪自然語句的假提示詞熔斷修正後，"
                "依原圖雜湊綁定的像素權威結案，沒有第四次模型呼叫。"
            ),
        }
    )

    tasks = _read_json(result_file) if result_file.is_file() else []
    if not isinstance(tasks, list):
        raise RuntimeError("result file is not a Label Studio task list")
    if any(
        Path(str((task.get("data") or {}).get("image") or "")).name == file_name
        for task in tasks
        if isinstance(task, dict)
    ):
        raise RuntimeError("result file already contains the fused photo")

    report = {
        "status": "would_recover" if not apply else "recovered",
        "file_name": file_name,
        "source_item_id": source_item_id,
        "period": period,
        "view_type": current.get("view_type"),
        "complete_screen_count": current.get("complete_screen_count"),
        "model": current.get("model"),
        "price": current.get("price"),
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
    task_id = max((int(task.get("id") or 0) for task in tasks), default=0) + 1
    tasks.append(_result_task(current, task_id))
    _atomic_json(result_file, tasks)

    attempts.pop(file_name, None)
    histories.pop(file_name, None)
    retry["auto_attempts"] = attempts
    retry["auto_result_history"] = histories
    retry["retry_queue"] = [item for item in retry.get("retry_queue") or [] if item != file_name]
    retry["priority_queue"] = [item for item in retry.get("priority_queue") or [] if item != file_name]
    retry["updated_at"] = datetime.now().isoformat()
    _atomic_json(retry_file, retry)

    cleared_at = datetime.now().astimezone().isoformat()
    audit_dir = fuse_file.parent
    receipt = {
        **report,
        "status": "recovered",
        "queued_job": str(queued),
        "result_file": str(result_file),
        "cleared_at": cleared_at,
    }
    receipt_path = audit_dir / "false_instruction_echo_recovery" / (
        f"{datetime.now():%Y%m%d_%H%M%S}_{source_item_id[:12]}.json"
    )
    history_path = audit_dir / "runtime_health_fuse_history" / (
        f"instruction_echo_{datetime.now():%Y%m%d_%H%M%S}_{source_item_id[:12]}.json"
    )
    _atomic_json(receipt_path, receipt)
    _atomic_json(
        history_path,
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
    print(json.dumps(recover(**vars(args)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

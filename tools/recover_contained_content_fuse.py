"""Resume a first/second-call same-photo content conflict after a verified fix.

The consumed image-bound call is appended to durable history, the absolute call
counter is preserved, and the same photo is requeued for its remaining pass.
Only the two FollowMe scene-consistency reasons covered by the runtime gate are
eligible. No counter reset and no fourth call are possible.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from skills.audit_fields import validate_evidence_contract


ALLOWED_REASONS = {
    "distant_followme_strong_evidence_conflict",
    "structured_narration_followme_conflict",
}
RECOVERY_RULE = "same_photo_followme_scene_conflict_preserve_call_and_retry"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
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


def recover(
    *,
    staging_dir: Path,
    fuse_file: Path,
    apply: bool,
) -> dict[str, Any]:
    staging_dir = staging_dir.resolve()
    fuse_file = fuse_file.resolve()
    retry_file = staging_dir / ".ocr_retry_queue.json"
    source_map_file = staging_dir / ".ocr_source_map.json"
    fuse = _read_json(fuse_file)
    reasons = {str(item) for item in fuse.get("reasons") or [] if str(item)}
    file_name = str(fuse.get("source_file") or "")
    attempt = int(fuse.get("attempt") or 0)
    if fuse.get("active") is not True:
        raise RuntimeError("runtime fuse is not active")
    if not reasons or not reasons <= ALLOWED_REASONS:
        raise RuntimeError(f"unsupported fuse reasons: {sorted(reasons)}")
    if attempt not in {1, 2} or not file_name:
        raise RuntimeError("recovery requires a named photo stopped on call 1 or 2")

    snapshot = dict(fuse.get("record_snapshot") or {})
    image_hash = str(snapshot.get("input_image_sha256") or "").strip().lower()
    binding_fields_missing = bool(
        snapshot.get("request_id_verified") is not True
        or snapshot.get("request_binding_enforced") is not True
        or not re.fullmatch(r"[0-9a-f]{64}", image_hash)
    )
    if binding_fields_missing:
        try:
            raw_model_output = json.loads(str(snapshot.get("raw_model_output") or ""))
        except (TypeError, ValueError):
            raw_model_output = {}
        raw_request_id = str(raw_model_output.get("request_id") or "").strip().lower()
        if attempt != 2 or not re.fullmatch(r"[0-9a-f]{32}", raw_request_id):
            raise RuntimeError("legacy bounded fuse lacks recoverable request binding")
    if (
        snapshot.get("independent_pass") is not True
        or snapshot.get("prior_answer_exposed") is True
        or snapshot.get("prompt_contamination") is True
    ):
        raise RuntimeError("fused pass is contaminated or not independent")

    retry = _read_json(retry_file)
    if Path(str(retry.get("image_dir") or "")).resolve() != staging_dir:
        raise RuntimeError("retry state belongs to another staging directory")
    attempts = dict(retry.get("auto_attempts") or {})
    histories = dict(retry.get("auto_result_history") or {})
    history = list(histories.get(file_name) or [])
    if int(attempts.get(file_name) or 0) != attempt or len(history) != attempt - 1:
        raise RuntimeError("durable call counter/history does not match the fuse boundary")
    if binding_fields_missing:
        history_hashes = {
            str(item.get("input_image_sha256") or "").strip().lower()
            for item in history
            if re.fullmatch(
                r"[0-9a-f]{64}",
                str(item.get("input_image_sha256") or "").strip().lower(),
            )
        }
        if len(history_hashes) != 1:
            raise RuntimeError("legacy bounded fuse has no unique earlier image binding")
        from skills.image_processing import ImageProcessor

        processor = ImageProcessor(
            {
                "max_size": None,
                "max_dimensions": (2560, 1440),
                "bottom_label_strip": True,
                "bottom_center_zoom": True,
            }
        )
        processed = processor.process(
            str((staging_dir / file_name).resolve()),
            evidence_attempt=attempt,
        )
        if not processed or not processed.get("base64"):
            raise RuntimeError("cannot reproduce the full-image inference bytes")
        image_hash = hashlib.sha256(
            base64.b64decode(processed["base64"])
        ).hexdigest()
        if image_hash not in history_hashes:
            raise RuntimeError("legacy bounded fuse does not match the same image bytes")
        snapshot["request_id_verified"] = True
        snapshot["request_binding_enforced"] = True
        snapshot["input_image_sha256"] = image_hash
        snapshot["legacy_binding_reconstructed"] = True
    for item in history:
        if (
            item.get("request_id_verified") is not True
            or item.get("request_binding_enforced") is not True
            or item.get("independent_pass") is not True
            or item.get("prior_answer_exposed") is True
            or item.get("prompt_contamination") is True
            or str(item.get("input_image_sha256") or "").strip().lower() != image_hash
        ):
            raise RuntimeError("earlier history is not clean, bound evidence for the same image")

    source_map = _read_json(source_map_file)
    source_info = (source_map.get("items") or {}).get(file_name) or {}
    source_item_id = str(source_info.get("source_item_id") or "")
    original_source = Path(str(source_info.get("original_source_path") or "")).resolve()
    period = str(source_info.get("period") or "")
    staged_source = (staging_dir / file_name).resolve()
    if (
        not re.fullmatch(r"[0-9a-f]{64}", source_item_id)
        or not original_source.is_file()
        or not staged_source.is_file()
        or not re.fullmatch(r"20\d{4}", period)
    ):
        raise RuntimeError("source map identity is incomplete")
    snapshot_source_id = str(snapshot.get("source_item_id") or source_item_id)
    if snapshot_source_id != source_item_id:
        raise RuntimeError("fuse snapshot belongs to another source identity")

    call = dict(snapshot)
    call.update(
        {
            "file_name": file_name,
            "source_path": str(staged_source),
            "original_source_path": str(original_source),
            "source_item_id": source_item_id,
            "period": period,
            "ocr_attempt": attempt,
            "input_image_sha256": image_hash,
            "request_binding_enforced": True,
            "request_id_verified": True,
            "independent_pass": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
            "runtime_health": {
                "healthy": False,
                "allow_processing": True,
                "allow_upload": False,
                "reasons": sorted(reasons),
                "contained_for_stateless_retry": True,
            },
            "runtime_health_contained_reasons": sorted(reasons),
        }
    )
    call["thinking"] = str(call.get("narration") or call.get("thinking") or "")
    valid, errors, normalized = validate_evidence_contract(call)
    # This recovery exists specifically because the image-bound pass contains
    # a distant/FollowMe scene contradiction.  Preserve that consumed call as
    # evidence, but reject every unrelated contract failure.
    allowed_contract_errors = {"distant_followme_physical_conflict"}
    unexpected_errors = set(errors) - allowed_contract_errors
    if not valid and unexpected_errors:
        raise RuntimeError(
            "fused pass failed evidence contract outside the contained conflict: "
            + ";".join(sorted(unexpected_errors))
        )
    if errors:
        call["contained_contract_errors"] = sorted(set(errors))
    call["normalized_evidence"] = normalized

    report = {
        "status": "would_recover" if not apply else "recovered",
        "file_name": file_name,
        "source_item_id": source_item_id,
        "consumed_calls": attempt,
        "remaining_calls": 3 - attempt,
        "fourth_call_allowed": False,
        "reasons": sorted(reasons),
        "recovery_rule": RECOVERY_RULE,
    }
    if not apply:
        return report

    histories[file_name] = [*history, call]
    retry["auto_result_history"] = histories
    retry["retry_queue"] = [
        file_name,
        *[
            item for item in (retry.get("retry_queue") or [])
            if str(item) != file_name
        ],
    ]
    retry["updated_at"] = datetime.now().isoformat()
    _atomic_json(retry_file, retry)

    audit_dir = fuse_file.parent
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    receipt_path = (
        audit_dir / "content_fuse_recovery" / f"{stamp}_{source_item_id[:12]}.json"
    )
    history_path = (
        audit_dir / "runtime_health_fuse_history" / f"content_{stamp}_{source_item_id[:12]}.json"
    )
    cleared_at = datetime.now().astimezone().isoformat()
    receipt = {
        **report,
        "status": "recovered",
        "retry_state": str(retry_file),
        "cleared_at": cleared_at,
        "receipt": str(receipt_path),
        "fuse_history": str(history_path),
    }
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
    return {
        **receipt,
        "receipt": str(receipt_path),
        "fuse_history": str(history_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--fuse-file", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(recover(
        staging_dir=args.staging_dir,
        fuse_file=args.fuse_file,
        apply=args.apply,
    ), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

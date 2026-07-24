"""Resume one legacy post-inference instruction-echo fuse without losing a call.

This is intentionally *not* a general runtime-fuse clearer.  It accepts only a
request-bound call-1/call-2 ``ui_narration_instruction_echo`` incident which the
currently installed runtime gate classifies as photo-local technical.  The
echoed output remains discarded, the consumed model-call count is preserved,
and the same source is placed at the front of the durable retry queue.

Dry-run is the default.  No model, result, or upload surface is called here.
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

from skills.runtime_health_gate import (
    RUNTIME_HEALTH_FUSE_SCHEMA,
    narration_contains_instruction_echo,
    response_narration_format_failure_is_photo_local,
)


FUSE_REASON = "ui_narration_instruction_echo"
RECOVERY_SCHEMA = "samsung-ocr-legacy-instruction-echo-recovery/v1"
RECOVERY_RULE = "photo_local_post_inference_echo_preserve_attempt_and_retry"
_HEX32 = re.compile(r"[0-9a-f]{32}")
_HEX64 = re.compile(r"[0-9a-f]{64}")


def _read_object(path: Path) -> dict[str, Any]:
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


def _clean_bound_record(
    record: dict[str, Any],
    *,
    source_item_id: str,
    image_hash: str,
) -> bool:
    return bool(
        record.get("request_id_verified") is True
        and record.get("request_binding_enforced") is True
        and record.get("independent_pass") is True
        and record.get("prior_answer_exposed") is False
        and record.get("prompt_contamination") is False
        and str(record.get("source_item_id") or "").strip().lower() == source_item_id
        and str(record.get("input_image_sha256") or "").strip().lower() == image_hash
    )


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
    if not staging_dir.is_dir() or not retry_file.is_file() or not source_map_file.is_file():
        raise RuntimeError("staging directory, retry state, and source map are required")
    if not fuse_file.is_file():
        raise RuntimeError("active runtime fuse is required")

    fuse = _read_object(fuse_file)
    reasons = list(fuse.get("reasons") or [])
    file_name = str(fuse.get("source_file") or "")
    run_id = str(fuse.get("run_id") or "")
    attempt = int(fuse.get("attempt") or 0)
    if (
        fuse.get("schema") != RUNTIME_HEALTH_FUSE_SCHEMA
        or fuse.get("active") is not True
        or reasons != [FUSE_REASON]
        or attempt not in {1, 2}
        or not run_id
        or not file_name
        or Path(file_name).name != file_name
    ):
        raise RuntimeError("fuse is not the exact active legacy call-1/call-2 echo shape")

    # Clearance is permitted only after the installed containment code proves
    # this exact presentation-format fault is photo-local, while mixed binding
    # or substantive failures remain fail-closed.
    if (
        not response_narration_format_failure_is_photo_local(reasons)
        or response_narration_format_failure_is_photo_local(
            [FUSE_REASON, "request_id_mismatch"]
        )
    ):
        raise RuntimeError("installed runtime gate does not narrowly contain this reason")

    snapshot = dict(fuse.get("record_snapshot") or {})
    source_item_id = str(snapshot.get("source_item_id") or "").strip().lower()
    image_hash = str(snapshot.get("input_image_sha256") or "").strip().lower()
    if (
        not _HEX64.fullmatch(source_item_id)
        or not _HEX64.fullmatch(image_hash)
        or not _clean_bound_record(
            snapshot, source_item_id=source_item_id, image_hash=image_hash
        )
    ):
        raise RuntimeError("fused response is not cleanly request-bound and source-bound")

    try:
        raw = json.loads(str(snapshot.get("raw_model_output") or ""))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("fuse has no parseable post-inference raw output") from exc
    if not isinstance(raw, dict):
        raise RuntimeError("raw model output must be one JSON object")
    request_id = str(raw.get("request_id") or "").strip().lower()
    raw_narration = str(raw.get("narration") or "")
    if not _HEX32.fullmatch(request_id):
        raise RuntimeError("raw model output has no 32-character request id")
    if not narration_contains_instruction_echo(raw_narration):
        raise RuntimeError("raw model output does not prove an actual instruction echo")

    retry = _read_object(retry_file)
    if Path(str(retry.get("image_dir") or "")).resolve() != staging_dir:
        raise RuntimeError("retry state belongs to another staging directory")
    attempts = dict(retry.get("auto_attempts") or {})
    histories = dict(retry.get("auto_result_history") or {})
    history = [dict(item) for item in histories.get(file_name) or []]
    if int(attempts.get(file_name) or 0) != attempt:
        raise RuntimeError("persisted attempt does not equal the consumed fused attempt")
    if len(history) != attempt - 1:
        raise RuntimeError("history length must equal attempt minus one")
    for row in history:
        if not _clean_bound_record(
            row, source_item_id=source_item_id, image_hash=image_hash
        ):
            raise RuntimeError("earlier history is not cleanly bound to the same source")

    source_map = _read_object(source_map_file)
    source_info = dict((source_map.get("items") or {}).get(file_name) or {})
    original_source = Path(str(source_info.get("original_source_path") or "")).resolve()
    if (
        str(source_info.get("source_item_id") or "").strip().lower() != source_item_id
        or not original_source.is_file()
        or not (staging_dir / file_name).is_file()
    ):
        raise RuntimeError("source map or source photo does not match the fused identity")

    clearance_dir = fuse_file.parent / "runtime_health_fuse_clearance"
    for prior_path in clearance_dir.glob("legacy_echo_*.json"):
        try:
            prior = _read_object(prior_path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            continue
        if (
            prior.get("source_file") == file_name
            and prior.get("run_id") == run_id
            and prior.get("recovery") == RECOVERY_RULE
        ):
            raise RuntimeError("this source/run already used legacy echo recovery")

    report = {
        "schema": RECOVERY_SCHEMA,
        "status": "recovered" if apply else "would_recover",
        "recovery": RECOVERY_RULE,
        "source_file": file_name,
        "source_item_id": source_item_id,
        "input_image_sha256": image_hash,
        "run_id": run_id,
        "request_id": request_id,
        "consumed_attempt_before": attempt,
        "consumed_attempt_after": attempt,
        "remaining_calls": 3 - attempt,
        "fourth_call_allowed": False,
        "discarded_output": True,
        "model_called": False,
        "result_written": False,
        "upload_enqueued": False,
    }
    if not apply:
        return report

    # Preserve the consumed post-inference call.  Only queue ordering changes.
    attempts[file_name] = attempt
    retry["auto_attempts"] = attempts
    retry["retry_queue"] = [
        file_name,
        *[item for item in retry.get("retry_queue") or [] if item != file_name],
    ]
    retry["updated_at"] = datetime.now().astimezone().isoformat()
    _atomic_json(retry_file, retry)

    cleared_at = datetime.now().astimezone().isoformat()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    receipt_path = clearance_dir / f"legacy_echo_{stamp}_{source_item_id[:12]}.json"
    history_path = (
        fuse_file.parent
        / "runtime_health_fuse_history"
        / f"legacy_echo_{stamp}_{source_item_id[:12]}.json"
    )
    receipt = {**report, "cleared_at": cleared_at, "retry_state": str(retry_file)}
    archived = {
        **fuse,
        "active": False,
        "cleared_at": cleared_at,
        "clearance": RECOVERY_RULE,
        "recovery_receipt": str(receipt_path),
    }
    # Both artifacts are written with replace-on-complete semantics.  The
    # active marker is deleted only after both durable artifacts exist; any
    # write failure therefore remains fail-closed.
    _atomic_json(receipt_path, receipt)
    _atomic_json(history_path, archived)
    fuse_file.unlink()
    return {
        **receipt,
        "receipt": str(receipt_path),
        "archived_fuse": str(history_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--fuse-file", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(recover(**vars(args)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

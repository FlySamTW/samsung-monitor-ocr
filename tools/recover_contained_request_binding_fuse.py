"""Resume one photo-local incomplete request echo without resetting its call count.

This recovery is deliberately narrower than a normal fuse clearance. It accepts
only a first- or second-call missing/unverified request echo, preserves the
already consumed call count, places the same photo back at the front of the
durable retry queue, archives the fuse, and records an auditable receipt.
Explicit request-ID mismatches and third-call failures are never accepted.
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


ALLOWED_REASONS = {"request_id_missing", "request_binding_unverified"}
RECOVERY_RULE = "photo_local_unbound_call_preserve_attempt_and_retry"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
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
    if "request_id_mismatch" in reasons:
        raise RuntimeError("explicit request-ID mismatch cannot use local recovery")
    if attempt not in (1, 2) or not file_name:
        raise RuntimeError("local recovery requires one named photo stopped on call 1 or 2")

    snapshot = dict(fuse.get("record_snapshot") or {})
    if snapshot.get("prior_answer_exposed") is True:
        raise RuntimeError("prior-answer exposure cannot use local recovery")
    if snapshot.get("prompt_contamination") is True:
        raise RuntimeError("prompt contamination cannot use local recovery")

    retry_state = _read_json(retry_file)
    if Path(str(retry_state.get("image_dir") or "")).resolve() != staging_dir:
        raise RuntimeError("retry state belongs to a different staging directory")
    attempts = dict(retry_state.get("auto_attempts") or {})
    if int(attempts.get(file_name) or 0) != attempt:
        raise RuntimeError("persisted call count does not match the fuse")
    history = list((retry_state.get("auto_result_history") or {}).get(file_name) or [])
    if len(history) > attempt - 1:
        raise RuntimeError("valid history exceeds the number of earlier bound calls")
    for row in history:
        if (
            row.get("request_id_verified") is not True
            or row.get("independent_pass") is not True
            or row.get("prior_answer_exposed") is True
            or row.get("prompt_contamination") is True
            or not re.fullmatch(
                r"[0-9a-f]{64}",
                str(row.get("input_image_sha256") or "").strip().lower(),
            )
        ):
            raise RuntimeError("earlier persisted history is not healthy and request-bound")

    source_map = _read_json(source_map_file)
    source_info = (source_map.get("items") or {}).get(file_name) or {}
    source_item_id = str(source_info.get("source_item_id") or "")
    original_source = Path(str(source_info.get("original_source_path") or "")).resolve()
    if not re.fullmatch(r"[0-9a-f]{64}", source_item_id):
        raise RuntimeError("source map has no stable source_item_id")
    if not original_source.is_file() or not (staging_dir / file_name).is_file():
        raise RuntimeError("source or staged photo is missing")

    report = {
        "status": "would_recover" if not apply else "recovered",
        "file_name": file_name,
        "source_item_id": source_item_id,
        "consumed_calls": attempt,
        "remaining_calls": 3 - attempt,
        "fourth_call_allowed": False,
        "recovery_rule": RECOVERY_RULE,
    }
    if not apply:
        return report

    retry_queue = [item for item in retry_state.get("retry_queue") or [] if item != file_name]
    retry_state["retry_queue"] = [file_name, *retry_queue]
    retry_state["updated_at"] = datetime.now().isoformat()
    _atomic_json(retry_file, retry_state)

    cleared_at = datetime.now().isoformat()
    audit_dir = fuse_file.parent
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    receipt_path = (
        audit_dir
        / "request_binding_recovery"
        / f"local_{stamp}_{source_item_id[:12]}.json"
    )
    history_path = (
        audit_dir
        / "runtime_health_fuse_history"
        / f"local_{stamp}_{source_item_id[:12]}.json"
    )
    receipt = {
        **report,
        "status": "recovered",
        "retry_state": str(retry_file),
        "cleared_at": cleared_at,
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
    return {**receipt, "receipt": str(receipt_path), "fuse_history": str(history_path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--fuse-file", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            recover(
                staging_dir=args.staging_dir,
                fuse_file=args.fuse_file,
                apply=args.apply,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

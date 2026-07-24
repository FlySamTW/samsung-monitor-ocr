"""Clear a pre-inference review-metadata false fuse without losing a model call.

This recovery is deliberately narrow.  It applies only when attempt two or
three was blocked before inference because a prior numeric value appeared
solely in transport metadata (source filename, RequestID or bbox coordinates).
Every earlier call must be healthy, stateless and request-bound for the same
source, run and full-image hash.  The persisted attempt counter is rolled back
by exactly one so the fixed runtime can make the call that never happened.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.audit_fields import EVIDENCE_GUARD_REVISION
from skills.runtime_health_gate import review_prompt_leak_reasons


RECOVERY_SCHEMA = "samsung-ocr-review-metadata-fuse-recovery/v1"
RECOVERY_RULE = "rollback_pre_inference_metadata_false_positive_one_slot"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return payload


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _trace_calls(
    trace_path: Path,
    *,
    file_name: str,
    run_id: str,
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    with trace_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if row.get("file_name") != file_name or row.get("run_id") != run_id:
                continue
            parsed = row.get("parsed_output")
            if isinstance(parsed, dict):
                calls.append(dict(parsed))
    return sorted(calls, key=lambda item: int(item.get("ocr_attempt") or 0))


def recover(
    *,
    staging_dir: Path,
    trace_path: Path,
    fuse_file: Path,
    apply: bool,
) -> dict[str, Any]:
    staging_dir = staging_dir.resolve()
    trace_path = trace_path.resolve()
    fuse_file = fuse_file.resolve()
    retry_file = staging_dir / ".ocr_retry_queue.json"
    if not staging_dir.is_dir() or not trace_path.is_file() or not retry_file.is_file():
        raise RuntimeError("staging, evidence trace and retry state are required")
    if not fuse_file.is_file():
        raise RuntimeError("active runtime fuse is required")

    fuse = _read_json(fuse_file)
    file_name = str(fuse.get("source_file") or "")
    run_id = str(fuse.get("run_id") or "")
    snapshot = fuse.get("record_snapshot") or {}
    if (
        fuse.get("schema") != "samsung-ocr-runtime-health-fuse/v1"
        or fuse.get("active") is not True
        or fuse.get("reasons") != ["review_prior_value_present"]
        or int(fuse.get("attempt") or 0) not in {2, 3}
        or not file_name
        or not run_id
        or snapshot.get("view_type") != "失敗"
        or snapshot.get("model") is not None
        or snapshot.get("price") is not None
        or str(snapshot.get("raw_model_output") or "")
    ):
        raise RuntimeError("fuse is not the exact pre-inference metadata false-positive shape")

    # Refuse recovery unless the currently installed guard demonstrably ignores
    # the fixed-format transport binding that caused the known false fuse.
    # This prevents a supervisor from clearing the marker while old code is
    # still loaded or before the root cause has actually been fixed.
    sanitizer_probe = review_prompt_leak_reasons(
        2,
        [{
            "role": "user",
            "content": (
                "這是全新照片，只根據當前影像獨立判讀。\n"
                "本次只輸出一個 JSON 物件；其中 request_id 必須逐字等於"
                "以下本次識別碼：aabb2590ccddeeff0011223344556677"
            ),
        }],
        injected_prior_results=[],
        prior_results_for_leak_check=[{"model": "S24F332EAC", "price": "2590"}],
    )
    if sanitizer_probe:
        raise RuntimeError("current runtime guard has not fixed the transport-binding collision")

    blocked_attempt = int(fuse.get("attempt") or 0)
    completed_attempts = list(range(1, blocked_attempt))
    retry_state = _read_json(retry_file)
    attempts = retry_state.get("auto_attempts") or {}
    histories = retry_state.get("auto_result_history") or {}
    history = histories.get(file_name) or []
    if (
        int(attempts.get(file_name) or 0) != blocked_attempt
        or len(history) != len(completed_attempts)
    ):
        raise RuntimeError(
            "persisted attempt/history state does not match the pre-inference block"
        )

    calls = _trace_calls(trace_path, file_name=file_name, run_id=run_id)
    if [int(item.get("ocr_attempt") or 0) for item in calls] != completed_attempts:
        raise RuntimeError(
            "evidence trace must contain exactly the calls completed before the block"
        )
    source_ids = {str(item.get("source_item_id") or "") for item in calls}
    image_hashes = {
        str(item.get("input_image_sha256") or "").strip().lower() for item in calls
    }
    if (
        len(source_ids) != 1
        or not re.fullmatch(r"[0-9a-f]{64}", next(iter(source_ids), ""))
        or len(image_hashes) != 1
        or not re.fullmatch(r"[0-9a-f]{64}", next(iter(image_hashes), ""))
    ):
        raise RuntimeError("source identity or full-image hash is not stable")
    for call in calls:
        runtime = call.get("runtime_health") or {}
        if (
            call.get("request_id_verified") is not True
            or call.get("independent_pass") is not True
            or call.get("prior_answer_exposed") is True
            or call.get("prompt_contamination") is True
            or runtime.get("healthy") is not True
        ):
            raise RuntimeError("an existing call is not healthy, bound and stateless")

    source_item_id = next(iter(source_ids))
    image_hash = next(iter(image_hashes))
    clearance_dir = fuse_file.parent / "runtime_health_fuse_clearance"
    for prior_receipt in clearance_dir.glob("review_metadata_*.json"):
        try:
            prior = _read_json(prior_receipt)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            continue
        if (
            prior.get("source_file") == file_name
            and prior.get("run_id") == run_id
            and prior.get("recovery") == RECOVERY_RULE
        ):
            raise RuntimeError("this source/run already used the one-time metadata recovery")
    report = {
        "schema": RECOVERY_SCHEMA,
        "status": "would_recover" if not apply else "recovered",
        "recovery": RECOVERY_RULE,
        "source_file": file_name,
        "source_item_id": source_item_id,
        "run_id": run_id,
        "input_image_sha256": image_hash,
        "trace_attempts": completed_attempts,
        "persisted_attempt_before": blocked_attempt,
        "persisted_attempt_after": blocked_attempt - 1,
        "fourth_call_allowed": False,
        "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
    }
    if not apply:
        return report

    attempts[file_name] = blocked_attempt - 1
    retry_state["auto_attempts"] = attempts
    retry_state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _atomic_json(retry_file, retry_state)

    cleared_at = datetime.now().isoformat(timespec="seconds")
    audit_dir = fuse_file.parent
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    receipt_path = clearance_dir / f"review_metadata_{stamp}_{source_item_id[:12]}.json"
    receipt = {**report, "cleared_at": cleared_at}
    _atomic_json(receipt_path, receipt)
    archived = (
        audit_dir
        / "runtime_health_fuse_history"
        / f"review_metadata_{stamp}_{source_item_id[:12]}.json"
    )
    _atomic_json(
        archived,
        {
            **fuse,
            "active": False,
            "cleared_at": cleared_at,
            "clearance": RECOVERY_RULE,
            "recovery_receipt": str(receipt_path),
            "evidence_guard_revision_after_fix": EVIDENCE_GUARD_REVISION,
        },
    )
    fuse_file.unlink()
    return {**receipt, "receipt": str(receipt_path), "archived_fuse": str(archived)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--fuse-file", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = recover(
        staging_dir=args.staging_dir,
        trace_path=args.trace,
        fuse_file=args.fuse_file,
        apply=args.apply,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

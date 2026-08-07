"""Resume one photo after old code fused a containable first-pass conflict.

The first model call remains consumed.  Its request-bound evidence is restored
to the durable same-photo history, the active fuse is archived, and the photo
is placed at the front of the retry queue.  No model call or upload occurs in
this recovery.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.audit_fields import EVIDENCE_GUARD_REVISION, validate_evidence_contract
from skills.image_processing import ImageProcessor
from skills.runtime_health_gate import first_pass_content_conflict_can_retry
from tools.recover_request_binding_fuse import _atomic_json


RECOVERY_SCHEMA = "samsung-ocr-first-pass-photo-local-fuse-recovery/v1"
RECOVERY_RULE = "restore_consumed_first_pass_then_stateless_retry"


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def _prepared_input_sha256(path: Path, attempt: int = 1) -> str:
    """Recreate the exact full-frame bytes sent to the local VLM."""
    processor = ImageProcessor({
        "max_size": None,
        "max_dimensions": (2560, 1440),
    })
    processed = processor.process(str(path), evidence_attempt=attempt)
    if not processed or not processed.get("base64"):
        raise RuntimeError("staged photo cannot be prepared for input hash verification")
    try:
        return hashlib.sha256(base64.b64decode(processed["base64"])).hexdigest()
    except (TypeError, ValueError) as exc:
        raise RuntimeError("prepared photo bytes are not decodable") from exc


def recover(*, staging_dir: Path, fuse_file: Path, apply: bool) -> dict[str, Any]:
    staging_dir = staging_dir.resolve()
    fuse_file = fuse_file.resolve()
    retry_file = staging_dir / ".ocr_retry_queue.json"
    source_map_file = staging_dir / ".ocr_source_map.json"
    if not all(path.is_file() for path in (fuse_file, retry_file, source_map_file)):
        raise RuntimeError("active fuse, retry state and source map are required")

    fuse = _read(fuse_file)
    reasons = [str(item) for item in fuse.get("reasons") or [] if str(item)]
    file_name = str(fuse.get("source_file") or "")
    snapshot = dict(fuse.get("record_snapshot") or {})
    if (
        fuse.get("schema") != "samsung-ocr-runtime-health-fuse/v1"
        or fuse.get("active") is not True
        or int(fuse.get("attempt") or 0) != 1
        or not file_name
    ):
        raise RuntimeError("fuse is not a currently containable first-pass content conflict")

    try:
        raw = json.loads(str(snapshot.get("raw_model_output") or ""))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("first-pass raw response is not parseable") from exc
    if not re.fullmatch(r"[0-9a-f]{32}", str(raw.get("request_id") or "")):
        raise RuntimeError("first-pass response has no valid request binding")
    raw_payload = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    raw_model = raw_payload.get("model") if isinstance(raw_payload, dict) else None
    blocked_fields = {
        str(value or "").strip().lower()
        for value in snapshot.get("structured_authority_blocked_fields") or []
    }
    exact_suppressed_model_conflict = bool(
        len(reasons) == 1
        and reasons[0] == "structured_authority_material_conflict:model"
        and blocked_fields == {"model"}
        and raw_model not in (None, "")
        and str(snapshot.get("model") or "").strip().casefold()
        == str(raw_model).strip().casefold()
    )
    eligibility_snapshot = dict(snapshot)
    if exact_suppressed_model_conflict:
        # Old code revived the exact model that structured authority had
        # already suppressed. Preserve it only as provenance and retry the
        # photo with a fresh stateless call; never treat it as an accepted SKU.
        eligibility_snapshot["raw_structured_model"] = raw_model
        eligibility_snapshot["model"] = None
        suppressions = list(eligibility_snapshot.get("field_suppression_reasons") or [])
        if "model:structured_authority_material_conflict" not in suppressions:
            suppressions.append("model:structured_authority_material_conflict")
        eligibility_snapshot["field_suppression_reasons"] = suppressions
    if not first_pass_content_conflict_can_retry(1, reasons, eligibility_snapshot):
        raise RuntimeError("fuse is not a currently containable first-pass content conflict")
    if (
        eligibility_snapshot.get("request_binding_enforced") is not True
        or eligibility_snapshot.get("request_id_verified") is not True
        or eligibility_snapshot.get("independent_pass") is not True
        or eligibility_snapshot.get("prior_answer_exposed") is True
        or eligibility_snapshot.get("prompt_contamination") is True
        or not re.fullmatch(
            r"[0-9a-f]{64}",
            str(eligibility_snapshot.get("input_image_sha256") or "").strip().lower(),
        )
        or not re.fullmatch(
            r"[0-9a-f]{64}",
            str(eligibility_snapshot.get("source_item_id") or "").strip().lower(),
        )
    ):
        raise RuntimeError("first pass is not bound, independent and source identified")

    retry = _read(retry_file)
    if Path(str(retry.get("image_dir") or "")).resolve() != staging_dir:
        raise RuntimeError("retry state belongs to another staging directory")
    attempts = dict(retry.get("auto_attempts") or {})
    histories = dict(retry.get("auto_result_history") or {})
    if int(attempts.get(file_name) or 0) != 1 or histories.get(file_name):
        raise RuntimeError("retry state does not prove one consumed call with empty history")

    source_map = _read(source_map_file)
    source_info = dict((source_map.get("items") or {}).get(file_name) or {})
    source_item_id = str(eligibility_snapshot["source_item_id"]).lower()
    staged_path = staging_dir / file_name
    if (
        str(source_info.get("source_item_id") or "").lower() != source_item_id
        or not staged_path.is_file()
    ):
        raise RuntimeError("source map does not match the fused photo")
    prepared_sha256 = _prepared_input_sha256(staged_path, attempt=1)
    expected_sha256 = str(eligibility_snapshot.get("input_image_sha256") or "").lower()
    if prepared_sha256 != expected_sha256:
        raise RuntimeError("staged photo bytes do not match the fused model input")

    record = dict(raw_payload)
    record.update(eligibility_snapshot)
    record.update(
        {
            "file_name": file_name,
            "source_path": str(staged_path),
            "original_source_path": str(source_info.get("original_source_path") or ""),
            "period": str(source_info.get("period") or ""),
            "ocr_attempt": 1,
            "run_id": str(fuse.get("run_id") or ""),
            "thinking": str(snapshot.get("narration") or raw.get("narration") or ""),
            "runtime_health": {
                "healthy": False,
                "allow_processing": True,
                "allow_upload": False,
                "reasons": reasons,
                "contained_for_stateless_retry": True,
            },
            "runtime_health_contained_reasons": reasons,
            "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
        }
    )
    valid, errors, normalized = validate_evidence_contract(record)
    if not valid:
        raise RuntimeError("first pass failed evidence contract: " + ";".join(errors))
    record["normalized_evidence"] = normalized

    report = {
        "schema": RECOVERY_SCHEMA,
        "status": "recovered" if apply else "would_recover",
        "recovery": RECOVERY_RULE,
        "source_file": file_name,
        "source_item_id": source_item_id,
        "consumed_calls": 1,
        "remaining_calls": 2,
        "fourth_call_allowed": False,
        "reasons": reasons,
        "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
        "input_image_sha256": prepared_sha256,
        "suppressed_raw_model": raw_model if exact_suppressed_model_conflict else None,
    }
    if not apply:
        return report

    histories[file_name] = [record]
    retry["auto_result_history"] = histories
    retry_queue = [item for item in retry.get("retry_queue") or [] if item != file_name]
    retry["retry_queue"] = [file_name, *retry_queue]
    retry["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _atomic_json(retry_file, retry)

    cleared_at = datetime.now().astimezone().isoformat()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    receipt_path = (
        fuse_file.parent
        / "runtime_health_fuse_clearance"
        / f"first_pass_content_{stamp}_{source_item_id[:12]}.json"
    )
    archive_path = (
        fuse_file.parent
        / "runtime_health_fuse_history"
        / f"first_pass_content_{stamp}_{source_item_id[:12]}.json"
    )
    receipt = {**report, "cleared_at": cleared_at}
    _atomic_json(receipt_path, receipt)
    _atomic_json(
        archive_path,
        {
            **fuse,
            "active": False,
            "cleared_at": cleared_at,
            "clearance": RECOVERY_RULE,
            "recovery_receipt": str(receipt_path),
        },
    )
    fuse_file.unlink()
    return {**receipt, "receipt": str(receipt_path), "archived_fuse": str(archive_path)}


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

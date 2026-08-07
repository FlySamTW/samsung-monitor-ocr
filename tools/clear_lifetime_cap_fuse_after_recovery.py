"""Clear one lifetime-call fuse after deterministic zero-model finalization.

The active photo must already have a current-revision verified result, a
content-bound consumed-cap recovery receipt, and a queued upload job.  This
tool never changes model-call counts or authorizes another model request.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.audit_fields import EVIDENCE_GUARD_REVISION


ALLOWED_RULES = {
    "three_bound_raw_structured_single_consensus",
    "three_bound_raw_structured_distant_consensus",
    "three_bound_cross_run_raw_structured_single_consensus",
    "three_bound_cross_run_raw_structured_distant_consensus",
}
CLEARANCE_RULE = "deterministic_consumed_cap_recovery"


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
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


def clear_after_recovery(
    *,
    audit_dir: Path,
    result_file: Path,
    file_name: str,
    source_item_id: str,
    apply: bool,
) -> dict[str, Any]:
    audit_dir = audit_dir.resolve()
    result_file = result_file.resolve()
    fuse_path = audit_dir / "runtime_health_fuse.json"
    fuse = _read(fuse_path)
    if (
        fuse.get("active") is not True
        or str(fuse.get("source_file") or "") != file_name
        or set(str(item) for item in fuse.get("reasons") or [])
        != {"lifetime_model_call_ledger_blocked"}
    ):
        raise RuntimeError("active fuse is not the exact recovered lifetime-cap stop")

    tasks = _read(result_file)
    matching = [
        task
        for task in tasks
        if Path(str((task.get("data") or {}).get("image") or "")).name == file_name
    ]
    if len(matching) != 1:
        raise RuntimeError("result file does not contain exactly one recovered photo")
    meta = (matching[0].get("data") or {}).get("ocr_meta") or {}
    rule = str(meta.get("adjudication_rule") or "")
    if (
        meta.get("auto_verified") is not True
        or meta.get("auto_review_required") is not False
        or str(meta.get("evidence_guard_revision") or "") != EVIDENCE_GUARD_REVISION
        or int(meta.get("ocr_attempt") or 0) != 3
        or meta.get("three_pass_adjudicated") is not True
        or rule not in ALLOWED_RULES
    ):
        raise RuntimeError("recovered result is not a current verified cap finalization")

    receipts = []
    receipt_dir = audit_dir / "consumed_cap_missing_result_recovery"
    for path in receipt_dir.glob("*.json"):
        try:
            item = _read(path)
        except (OSError, ValueError):
            continue
        if (
            item.get("status") == "recovered"
            and str(item.get("file_name") or "") == file_name
            and str(item.get("source_item_id") or "") == source_item_id
            and item.get("fourth_call_made") is False
            and str(item.get("adjudication_rule") or "") == rule
            and Path(str(item.get("result_file") or "")).resolve() == result_file
        ):
            receipts.append((path, item))
    if len(receipts) != 1:
        raise RuntimeError("exact consumed-cap recovery receipt is missing or ambiguous")

    queued_job = Path(str(receipts[0][1].get("queued_job") or "")).resolve()
    if not queued_job.is_file():
        # The uploader may already have claimed or completed the immutable job.
        stream_root = audit_dir.parent / "_drive_upload_stream"
        working = stream_root / "working" / f"{source_item_id}.json"
        if not working.is_file():
            receipt_matches = list(
                (audit_dir / "stream_drive_receipts").rglob(f"*{source_item_id}*.json")
            )
            if not receipt_matches:
                raise RuntimeError("recovered upload job is neither queued nor receipted")

    report = {
        "status": "would_clear" if not apply else "cleared",
        "clearance_rule": CLEARANCE_RULE,
        "file_name": file_name,
        "source_item_id": source_item_id,
        "adjudication_rule": rule,
        "model_calls_consumed": 3,
        "fourth_call_authorized": False,
        "result_file": str(result_file),
        "recovery_receipt": str(receipts[0][0]),
    }
    if not apply:
        return report

    cleared_at = datetime.now().astimezone().isoformat()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    clearance_path = (
        audit_dir
        / "runtime_health_fuse_clearance"
        / f"consumed_cap_{stamp}_{source_item_id[:12]}.json"
    )
    history_path = (
        audit_dir
        / "runtime_health_fuse_history"
        / f"consumed_cap_{stamp}_{source_item_id[:12]}.json"
    )
    _atomic_json(clearance_path, {**report, "cleared_at": cleared_at})
    _atomic_json(
        history_path,
        {
            **fuse,
            "active": False,
            "cleared_at": cleared_at,
            "clearance": CLEARANCE_RULE,
            "clearance_receipt": str(clearance_path),
            "source_item_id": source_item_id,
            "fourth_call_authorized": False,
        },
    )
    fuse_path.unlink()
    return {
        **report,
        "cleared_at": cleared_at,
        "clearance_receipt": str(clearance_path),
        "fuse_history": str(history_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument("--result-file", type=Path, required=True)
    parser.add_argument("--file-name", required=True)
    parser.add_argument("--source-item-id", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            clear_after_recovery(
                audit_dir=args.audit_dir,
                result_file=args.result_file,
                file_name=args.file_name,
                source_item_id=args.source_item_id,
                apply=args.apply,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

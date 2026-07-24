"""Clear the exact .76 terminal-review-loop fuse after deterministic closure.

This recovery never changes retry counters and never authorizes another model
call.  It only accepts the known systemic fuse after every existing result row
has become a current-revision verified terminal result.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

EXPECTED_REASONS = {
    "systemic_cross_staging_three_call_terminal_review_loop",
    "terminal_review_after_three_calls_requires_deterministic_closure",
}
RECOVERY_RULE = "all_existing_terminal_reviews_closed_without_additional_model_calls"


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def recover(*, audit_dir: Path, result_file: Path, expected_revision: str, apply: bool) -> dict:
    audit_dir = audit_dir.resolve()
    result_file = result_file.resolve()
    fuse_file = audit_dir / "runtime_health_fuse.json"
    fuse = _read(fuse_file)
    if (
        fuse.get("schema") != "samsung-ocr-runtime-health-fuse/v1"
        or fuse.get("active") is not True
        or set(fuse.get("reasons") or []) != EXPECTED_REASONS
    ):
        raise RuntimeError("active fuse is not the exact terminal review loop")

    tasks = _read(result_file)
    if not isinstance(tasks, list) or not tasks:
        raise RuntimeError("non-empty result list required")
    terminals: list[dict] = []
    for task in tasks:
        meta = ((task.get("data") or {}).get("ocr_meta") or {})
        name = Path(str((task.get("data") or {}).get("image") or "")).name
        if (
            meta.get("auto_verified") is not True
            or meta.get("auto_review_required") is True
            or str(meta.get("evidence_guard_revision") or "") != expected_revision
        ):
            raise RuntimeError(f"result still unresolved: {name}")
        terminals.append(
            {
                "file_name": name,
                "ocr_attempt": int(meta.get("ocr_attempt") or 0),
                "adjudication_rule": meta.get("adjudication_rule"),
            }
        )

    required = {
        "M-台中市-大雅區-TK3C-新東海-958.jpg":
            "three_pass_subthree_single_content_closure",
        "M-台中市-北屯區-TK3C-東山-1140.jpg":
            "three_pass_human_audited_pixel_authority",
        "M-台中市-北屯區-TK3C-中清-1530.jpg":
            "three_pass_human_audited_pixel_authority",
    }
    actual = {item["file_name"]: item["adjudication_rule"] for item in terminals}
    for name, rule in required.items():
        if actual.get(name) != rule:
            raise RuntimeError(f"required deterministic closure absent: {name}")

    report = {
        "schema": "samsung-ocr-terminal-review-loop-recovery/v1",
        "status": "recovered" if apply else "would_recover",
        "recovery": RECOVERY_RULE,
        "evidence_guard_revision": expected_revision,
        "verified_result_rows": len(terminals),
        "remaining_review_rows": 0,
        "additional_model_calls": 0,
        "fourth_call_authorized": False,
        "result_file": str(result_file),
    }
    if not apply:
        return report

    now = datetime.now().astimezone().isoformat()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    receipt = audit_dir / "runtime_health_fuse_clearance" / f"terminal_loop_{stamp}.json"
    history = audit_dir / "runtime_health_fuse_history" / f"terminal_loop_{stamp}.json"
    _atomic(receipt, {**report, "cleared_at": now})
    _atomic(
        history,
        {
            **fuse,
            "active": False,
            "cleared_at": now,
            "clearance": RECOVERY_RULE,
            "clearance_receipt": str(receipt),
        },
    )
    fuse_file.unlink()
    return {**report, "cleared_at": now, "receipt": str(receipt), "fuse_history": str(history)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument("--result-file", type=Path, required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(recover(
        audit_dir=args.audit_dir,
        result_file=args.result_file,
        expected_revision=args.expected_revision,
        apply=args.apply,
    ), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

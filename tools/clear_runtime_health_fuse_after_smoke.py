"""Clear one runtime fuse only after a bounded, request-bound smoke proof.

This is the manual-after-fix clearance path promised by the runtime fuse.  It
does not alter retry state or model-call counts.  The stopped formal photo can
therefore resume at its existing boundary after the archived fuse and receipt
have been written.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


CLEARANCE_RULE = "manual_after_fix_regression_and_bound_smoke"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
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


def _result_rows(smoke_dir: Path) -> list[dict[str, Any]]:
    files = sorted(smoke_dir.glob("*OCR成功.json"))
    if len(files) != 1:
        raise RuntimeError("smoke must contain exactly one success result file")
    tasks = _read_json(files[0])
    if not isinstance(tasks, list):
        raise RuntimeError("smoke success file is not a task list")
    rows: list[dict[str, Any]] = []
    for task in tasks:
        data = task.get("data") or {}
        meta = data.get("ocr_meta") or {}
        annotation = (task.get("annotations") or [{}])[-1]
        fields: dict[str, Any] = {}
        for item in annotation.get("result") or []:
            name = str(item.get("from_name") or "")
            value = item.get("value") or {}
            if name == "category":
                fields["view_type"] = (value.get("choices") or [None])[0]
            elif name in {"model", "price"}:
                raw = (value.get("text") or [None])[0]
                fields[name] = None if raw in (None, "", "null") else str(raw)
        rows.append(
            {
                "file_name": Path(str(data.get("image") or "")).name,
                "meta": meta,
                "adjudication_rule": str(meta.get("adjudication_rule") or ""),
                **fields,
            }
        )
    return rows


def verify_and_clear(
    *,
    audit_dir: Path,
    smoke_dir: Path,
    expected_revision: str,
    audited_file: str,
    apply: bool,
) -> dict[str, Any]:
    audit_dir = audit_dir.resolve()
    smoke_dir = smoke_dir.resolve()
    fuse_file = audit_dir / "runtime_health_fuse.json"
    lock_file = audit_dir / "model_benchmark.lock"
    trace_file = audit_dir / "v1945_evidence_trace.jsonl"
    if "runtime_health_smoke" not in smoke_dir.name or not smoke_dir.is_dir():
        raise RuntimeError("clearance requires a fresh runtime_health_smoke directory")
    if not lock_file.is_file():
        raise RuntimeError("benchmark lock must remain active during clearance")
    fuse = _read_json(fuse_file)
    if fuse.get("active") is not True:
        raise RuntimeError("runtime fuse is not active")

    marker = _read_json(smoke_dir / ".ocr_presentation_run.json")
    run_id = str(marker.get("run_id") or "")
    started_at = str(marker.get("started_at") or "")
    if not run_id or not started_at or started_at <= str(fuse.get("tripped_at") or ""):
        raise RuntimeError("smoke run does not postdate the active fuse")

    rows = _result_rows(smoke_dir)
    if not (1 <= len(rows) <= 15):
        raise RuntimeError("smoke result count must be between 1 and 15")
    if len({row["file_name"] for row in rows}) != len(rows):
        raise RuntimeError("smoke result identities are not unique")
    for row in rows:
        meta = row["meta"]
        if meta.get("auto_verified") is not True:
            raise RuntimeError(f"smoke result is not verified: {row['file_name']}")
        if str(meta.get("evidence_guard_revision") or "") != expected_revision:
            raise RuntimeError(f"smoke result revision mismatch: {row['file_name']}")

    audited = next((row for row in rows if row["file_name"] == audited_file), None)
    if audited is None:
        raise RuntimeError("human-audited smoke file is absent")
    audited_rule = audited.get("adjudication_rule")
    audited_view = audited.get("view_type")
    if audited_rule == "three_pass_human_audited_pixel_authority":
        audited_result_valid = (
            audited_view in {"單機", "遠景"}
            and not audited.get("model")
            and not audited.get("price")
        )
    else:
        audited_result_valid = (
            audited_view == "單機"
            and not audited.get("model")
            and not audited.get("price")
        )
    if not audited_result_valid:
        raise RuntimeError("human-audited smoke case did not finish conservatively")

    traces: list[dict[str, Any]] = []
    for line in trace_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if str(item.get("run_id") or "") == run_id:
            traces.append(item)
    if len(traces) < len(rows):
        raise RuntimeError("smoke trace is incomplete")

    hashes: dict[str, set[str]] = defaultdict(set)
    contained_audited_content_reasons = {
        "known_source_expectation_conflict",
        "structured_narration_followme_conflict",
        "evidence_thinking_conflict",
    }
    for item in traces:
        parsed = item.get("parsed_output") or {}
        runtime = parsed.get("runtime_health") or {}
        file_name = str(item.get("file_name") or "")
        image_hash = str(parsed.get("input_image_sha256") or "").strip().lower()
        runtime_reasons = {
            str(reason) for reason in runtime.get("reasons") or [] if str(reason)
        }
        audited_content_conflict = bool(
            file_name == audited_file
            and audited.get("adjudication_rule")
            == "three_pass_human_audited_pixel_authority"
            and runtime.get("healthy") is not True
            and runtime_reasons
            and runtime_reasons <= contained_audited_content_reasons
        )
        if (
            str(item.get("evidence_guard_revision") or "") != expected_revision
            or parsed.get("request_id_verified") is not True
            or parsed.get("request_binding_enforced") is not True
            or parsed.get("independent_pass") is not True
            or parsed.get("prior_answer_exposed") is True
            or parsed.get("prompt_contamination") is True
            or (runtime.get("healthy") is not True and not audited_content_conflict)
            or len(image_hash) != 64
        ):
            raise RuntimeError(f"unsafe smoke trace: {file_name} attempt {item.get('attempt')}")
        hashes[file_name].add(image_hash)
    if set(hashes) != {row["file_name"] for row in rows}:
        raise RuntimeError("smoke trace/result identity set mismatch")
    if any(len(values) != 1 for values in hashes.values()):
        raise RuntimeError("smoke image hash changed across independent calls")

    report = {
        "status": "would_clear" if not apply else "cleared",
        "clearance_rule": CLEARANCE_RULE,
        "active_fuse_source": fuse.get("source_file"),
        "active_fuse_reasons": fuse.get("reasons") or [],
        "smoke_dir": str(smoke_dir),
        "smoke_run_id": run_id,
        "smoke_results": len(rows),
        "smoke_model_calls": len(traces),
        "smoke_bad_invariants": 0,
        "audited_file": audited_file,
        "audited_final": {"view_type": audited_view, "model": None, "price": None},
        "evidence_guard_revision": expected_revision,
        "formal_retry_state_changed": False,
        "fourth_call_authorized": False,
    }
    if not apply:
        return report

    cleared_at = datetime.now().astimezone().isoformat()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    receipt_path = audit_dir / "runtime_health_fuse_clearance" / f"smoke_{stamp}.json"
    history_path = audit_dir / "runtime_health_fuse_history" / f"smoke_{stamp}.json"
    receipt = {**report, "status": "cleared", "cleared_at": cleared_at}
    _atomic_json(receipt_path, receipt)
    _atomic_json(
        history_path,
        {
            **fuse,
            "active": False,
            "cleared_at": cleared_at,
            "clearance": CLEARANCE_RULE,
            "clearance_receipt": str(receipt_path),
            "smoke_run_id": run_id,
            "evidence_guard_revision": expected_revision,
        },
    )
    fuse_file.unlink()
    return {**receipt, "receipt": str(receipt_path), "fuse_history": str(history_path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument("--smoke-dir", type=Path, required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--audited-file", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = verify_and_clear(
        audit_dir=args.audit_dir,
        smoke_dir=args.smoke_dir,
        expected_revision=args.expected_revision,
        audited_file=args.audited_file,
        apply=args.apply,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

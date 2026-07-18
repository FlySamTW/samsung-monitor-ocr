"""Reopen one wrong finalized photo without discarding consumed model calls.

The normal dashboard rerun intentionally starts from zero.  That is unsafe for
an audited photo that already consumed one or two real model calls because it
could exceed the absolute three-call cap.  This tool is deliberately narrower:

* the exact inference-image SHA must already have a human pixel authority;
* every preserved call must be request-bound, independent and uncontaminated;
* attempts must be consecutive from one and fewer than three;
* the current finalized row must still match the last preserved call;
* the backend must be stopped at a photo boundary;
* the old Drive receipt is preserved for later exact-ID replacement.

Dry-run is the default.  ``--apply`` atomically removes only the selected
finalized row and restores its consumed calls to the durable retry queue.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.audit_fields import KNOWN_SOURCE_EXPECTATIONS


SUCCESS_SUFFIX = "OCR成功.json"
FAILURE_SUFFIX = "OCR失敗.json"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _atomic_json(path: Path, payload: Any) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _task_file_name(task: dict[str, Any]) -> str:
    data = task.get("data") or {}
    return Path(str(data.get("image") or task.get("file_name") or task.get("filename") or "")).name


def _task_identity(task: dict[str, Any]) -> tuple[str, str, str]:
    annotations = task.get("annotations") or []
    result_items = (annotations[-1].get("result") or []) if annotations else []
    values: dict[str, str] = {}
    for item in result_items:
        field = str(item.get("from_name") or "")
        value = item.get("value") or {}
        if field == "category":
            choices = value.get("choices") or []
            values[field] = str(choices[0]) if choices else ""
        elif field in {"model", "price"}:
            text = value.get("text") or []
            values[field] = str(text[0]) if text else ""
    return values.get("category", ""), values.get("model", ""), values.get("price", "")


def _canonical(value: Any) -> str:
    return "".join(char for char in str(value or "").upper() if char.isalnum())


def _load_bound_calls(trace_path: Path, file_name: str) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if str(row.get("file_name") or "") != file_name:
            continue
        parsed = row.get("parsed_output")
        if not isinstance(parsed, dict):
            continue
        call = dict(parsed)
        call.setdefault("ocr_attempt", row.get("attempt"))
        call.setdefault("run_id", row.get("run_id"))
        call.setdefault("source_item_id", row.get("source_item_id"))
        call.setdefault("input_image_sha256", (row.get("parsed_output") or {}).get("input_image_sha256"))
        calls.append(call)
    if not calls:
        raise RuntimeError("no evidence trace calls found for selected photo")

    latest_run = str(calls[-1].get("run_id") or "")
    calls = [call for call in calls if str(call.get("run_id") or "") == latest_run]
    calls.sort(key=lambda item: int(item.get("ocr_attempt") or 0))
    attempts = [int(item.get("ocr_attempt") or 0) for item in calls]
    if attempts != list(range(1, len(calls) + 1)) or len(calls) not in {1, 2}:
        raise RuntimeError(f"preserved calls must be consecutive attempts 1..N with N<3; got {attempts}")

    source_ids = {str(item.get("source_item_id") or "") for item in calls}
    image_hashes = {str(item.get("input_image_sha256") or "").lower() for item in calls}
    if len(source_ids) != 1 or "" in source_ids or len(image_hashes) != 1 or "" in image_hashes:
        raise RuntimeError("preserved calls do not share one source identity and image hash")
    for item in calls:
        if (
            item.get("request_id_verified") is not True
            or item.get("request_binding_enforced") is not True
            or item.get("independent_pass") is not True
            or item.get("prior_answer_exposed") is True
            or item.get("prompt_contamination") is True
        ):
            raise RuntimeError("a preserved call is unbound, non-independent, or contaminated")
    return calls


def _assert_backend_stopped(status_url: str) -> dict[str, Any]:
    with urllib.request.urlopen(status_url, timeout=10) as response:
        status = json.loads(response.read().decode("utf-8"))
    if status.get("is_running") is not False:
        raise RuntimeError("backend is not stopped at a photo boundary")
    return status


def build_plan(
    *,
    image_dir: Path,
    audit_dir: Path,
    output_dir: Path,
    file_name: str,
) -> dict[str, Any]:
    image_dir = image_dir.resolve()
    audit_dir = audit_dir.resolve()
    output_dir = output_dir.resolve()
    source_path = (image_dir / file_name).resolve()
    if source_path.parent != image_dir or not source_path.is_file():
        raise RuntimeError("selected source photo is missing or outside image directory")

    trace_path = audit_dir / "v1945_evidence_trace.jsonl"
    calls = _load_bound_calls(trace_path, file_name)
    last = calls[-1]
    image_hash = str(last.get("input_image_sha256") or "").lower()
    expected = KNOWN_SOURCE_EXPECTATIONS.get(image_hash)
    if not expected or expected.get("authority") != "human_audited_pixel_authority":
        raise RuntimeError("selected inference pixels have no human-audited authority")
    source_sha = _sha256(source_path)
    if source_sha != str(expected.get("source_file_sha256") or "").lower():
        raise RuntimeError("source bytes do not match audited authority")

    matches: list[tuple[Path, int, dict[str, Any]]] = []
    for path in sorted(image_dir.glob(f"*{SUCCESS_SUFFIX}")):
        payload = _read_json(path)
        if not isinstance(payload, list):
            continue
        for index, task in enumerate(payload):
            if isinstance(task, dict) and _task_file_name(task) == file_name:
                matches.append((path, index, task))
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one finalized success row; found {len(matches)}")
    if any(
        isinstance(task, dict) and _task_file_name(task) == file_name
        for path in image_dir.glob(f"*{FAILURE_SUFFIX}")
        for task in (_read_json(path) if isinstance(_read_json(path), list) else [])
    ):
        raise RuntimeError("selected photo also has a failure row")

    success_path, success_index, task = matches[0]
    category, model, price = _task_identity(task)
    last_category = str(last.get("view_type") or last.get("category") or "")
    if (
        category != last_category
        or _canonical(model) != _canonical(last.get("model"))
        or _canonical(price) != _canonical(last.get("price"))
    ):
        raise RuntimeError("finalized row no longer matches the last preserved model call")

    retry_state_path = image_dir / ".ocr_retry_queue.json"
    retry_state = _read_json(retry_state_path) if retry_state_path.is_file() else {
        "image_dir": str(image_dir),
        "priority_queue": [],
        "retry_queue": [],
        "auto_attempts": {},
        "auto_result_history": {},
        "runtime_health_incident_sources": {},
    }
    if str(Path(str(retry_state.get("image_dir") or "")).resolve()) != str(image_dir):
        raise RuntimeError("retry-state image directory mismatch")

    source_item_id = str(last.get("source_item_id") or "")
    receipt_path = output_dir / "_drive_upload_stream" / "receipts" / f"{source_item_id}.json"
    receipt = _read_json(receipt_path) if receipt_path.is_file() else {}
    if receipt and str(receipt.get("original_source_path") or "") != str(last.get("original_source_path") or ""):
        raise RuntimeError("existing Drive receipt belongs to another source")

    return {
        "image_dir": image_dir,
        "audit_dir": audit_dir,
        "output_dir": output_dir,
        "file_name": file_name,
        "source_path": source_path,
        "source_sha256": source_sha,
        "source_item_id": source_item_id,
        "input_image_sha256": image_hash,
        "calls": calls,
        "success_path": success_path,
        "success_index": success_index,
        "success_task": task,
        "retry_state_path": retry_state_path,
        "retry_state": retry_state,
        "old_receipt_path": receipt_path if receipt else None,
        "old_receipt": receipt,
        "expected": expected,
    }


def apply_plan(plan: dict[str, Any]) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    archive = Path(plan["audit_dir"]) / "targeted_reopen_history" / (
        f"{stamp}_{plan['source_item_id'][:12]}"
    )
    archive.mkdir(parents=True, exist_ok=False)
    success_path = Path(plan["success_path"])
    retry_state_path = Path(plan["retry_state_path"])
    shutil.copy2(success_path, archive / success_path.name)
    if retry_state_path.is_file():
        shutil.copy2(retry_state_path, archive / retry_state_path.name)
    if plan.get("old_receipt_path"):
        shutil.copy2(Path(plan["old_receipt_path"]), archive / "old_drive_receipt.json")

    success_payload = _read_json(success_path)
    updated_success = [
        item for item in success_payload if not (
            isinstance(item, dict) and _task_file_name(item) == plan["file_name"]
        )
    ]
    if len(success_payload) - len(updated_success) != 1:
        raise RuntimeError("success row changed after planning")

    retry = dict(plan["retry_state"])
    name = str(plan["file_name"])
    retry["priority_queue"] = [
        item for item in (retry.get("priority_queue") or []) if str(item) != name
    ]
    retry["retry_queue"] = [
        name,
        *[item for item in (retry.get("retry_queue") or []) if str(item) != name],
    ]
    attempts = dict(retry.get("auto_attempts") or {})
    histories = dict(retry.get("auto_result_history") or {})
    attempts[name] = len(plan["calls"])
    histories[name] = plan["calls"]
    retry["auto_attempts"] = attempts
    retry["auto_result_history"] = histories
    retry["updated_at"] = datetime.now().isoformat()

    manifest = {
        "schema": "samsung-ocr-targeted-reopen/v1",
        "file_name": name,
        "source_item_id": plan["source_item_id"],
        "source_sha256": plan["source_sha256"],
        "input_image_sha256": plan["input_image_sha256"],
        "preserved_actual_calls": len(plan["calls"]),
        "remaining_call_cap": 3 - len(plan["calls"]),
        "expected_final": {
            key: plan["expected"].get(key)
            for key in (
                "view_type", "complete_screen_count", "model", "price",
                "label_ownership", "followme_physical_expected",
            )
        },
        "old_drive_receipt": str(plan.get("old_receipt_path") or ""),
        "old_drive_file_id": (plan.get("old_receipt") or {}).get("drive_file_id"),
        "old_remote_path": (plan.get("old_receipt") or {}).get("remote_path"),
        "archive_dir": str(archive),
        "applied_at": datetime.now().astimezone().isoformat(),
    }
    _atomic_json(success_path, updated_success)
    _atomic_json(retry_state_path, retry)
    _atomic_json(archive / "manifest.json", manifest)
    return archive / "manifest.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image-dir", required=True)
    parser.add_argument("--audit-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--file-name", required=True)
    parser.add_argument("--status-url", default="http://127.0.0.1:5002/api/status")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    status = _assert_backend_stopped(args.status_url)
    plan = build_plan(
        image_dir=Path(args.image_dir),
        audit_dir=Path(args.audit_dir),
        output_dir=Path(args.output_dir),
        file_name=args.file_name,
    )
    report = {
        "status": "would_reopen",
        "file_name": args.file_name,
        "backend_current_file": status.get("current_file"),
        "preserved_actual_calls": len(plan["calls"]),
        "remaining_call_cap": 3 - len(plan["calls"]),
        "old_drive_file_id": (plan.get("old_receipt") or {}).get("drive_file_id"),
        "expected_final": {
            key: plan["expected"].get(key)
            for key in ("view_type", "model", "price", "complete_screen_count")
        },
    }
    if args.apply:
        report["status"] = "reopened"
        report["manifest"] = str(apply_plan(plan))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

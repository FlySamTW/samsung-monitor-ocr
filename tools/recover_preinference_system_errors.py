"""Recover failures that occurred before a model request was sent.

Only exact, audited software faults are eligible. The durable bound-call
history remains authoritative: its length is the number of actually consumed
model calls, while the orchestration counter may have advanced before the
exception. Recovery removes only the matching failure rows, resets each
counter to the proven history length, and requeues the photo.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


ALLOWED_REASON_FRAGMENTS = (
    "完整 OCR 提示詞超過正式上限；請提高模型 context 或整理重複規則，不可自動切換短提示詞。",
    "name 'EVIDENCE_GUARD_REVISION' is not defined",
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _atomic_json(path: Path, payload: Any) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _task_name(row: dict[str, Any]) -> str:
    return Path(str(
        row.get("filename")
        or row.get("file_name")
        or ((row.get("data") or {}).get("image"))
        or ""
    )).name


def _allowed_failure(row: dict[str, Any]) -> bool:
    reason = str(row.get("reason") or "")
    return str(row.get("error_type") or "") == "system_error" and any(
        fragment in reason for fragment in ALLOWED_REASON_FRAGMENTS
    )


def _validate_history(name: str, history: Any) -> list[dict[str, Any]]:
    if history in (None, []):
        return []
    if not isinstance(history, list) or len(history) > 2:
        raise RuntimeError(f"{name}: durable actual-call history is not 0..2 entries")
    hashes = {
        str(item.get("input_image_sha256") or "").strip().lower()
        for item in history if isinstance(item, dict)
    }
    if len(hashes) != 1 or "" in hashes:
        raise RuntimeError(f"{name}: history does not share one full-image hash")
    for item in history:
        if not isinstance(item, dict) or (
            item.get("request_id_verified") is not True
            or item.get("request_binding_enforced") is not True
            or item.get("independent_pass") is not True
            or item.get("prior_answer_exposed") is True
            or item.get("prompt_contamination") is True
        ):
            raise RuntimeError(f"{name}: history contains unbound or contaminated evidence")
    return history


def _assert_backend_stopped(status_url: str) -> dict[str, Any]:
    with urllib.request.urlopen(status_url, timeout=10) as response:
        status = json.loads(response.read().decode("utf-8"))
    if status.get("is_running") is not False:
        raise RuntimeError("backend is not stopped at a photo boundary")
    return status


def build_plan(
    image_dir: Path,
    audit_dir: Path,
    file_names: list[str],
) -> dict[str, Any]:
    image_dir = image_dir.resolve()
    audit_dir = audit_dir.resolve()
    targets = list(dict.fromkeys(file_names))
    if not targets:
        raise RuntimeError("no target filenames supplied")
    for name in targets:
        if Path(name).name != name or not (image_dir / name).is_file():
            raise RuntimeError(f"{name}: source photo is missing or unsafe")

    success_names: set[str] = set()
    for path in image_dir.glob("*OCR成功.json"):
        payload = _read_json(path)
        if isinstance(payload, list):
            success_names.update(
                _task_name(row) for row in payload if isinstance(row, dict)
            )
    overlap = [name for name in targets if name in success_names]
    if overlap:
        raise RuntimeError(f"target still has a success row: {overlap}")

    failure_files: dict[Path, list[dict[str, Any]]] = {}
    matches: dict[str, list[tuple[Path, dict[str, Any]]]] = {name: [] for name in targets}
    for path in sorted(image_dir.glob("*OCR失敗.json")):
        payload = _read_json(path)
        if not isinstance(payload, list):
            continue
        failure_files[path] = payload
        for row in payload:
            if not isinstance(row, dict):
                continue
            name = _task_name(row)
            if name in matches:
                matches[name].append((path, row))
    for name, rows in matches.items():
        if len(rows) != 1 or not _allowed_failure(rows[0][1]):
            raise RuntimeError(f"{name}: expected one exact eligible pre-inference failure")

    retry_path = image_dir / ".ocr_retry_queue.json"
    retry = _read_json(retry_path)
    if str(Path(str(retry.get("image_dir") or "")).resolve()) != str(image_dir):
        raise RuntimeError("retry-state image directory mismatch")
    histories = dict(retry.get("auto_result_history") or {})
    attempts = dict(retry.get("auto_attempts") or {})
    actual_counts: dict[str, int] = {}
    for name in targets:
        history = _validate_history(name, histories.get(name))
        actual_counts[name] = len(history)
        if int(attempts.get(name, 0)) < len(history):
            raise RuntimeError(f"{name}: counter is below durable actual-call history")

    return {
        "image_dir": image_dir,
        "audit_dir": audit_dir,
        "targets": targets,
        "failure_files": failure_files,
        "retry_path": retry_path,
        "retry": retry,
        "actual_counts": actual_counts,
    }


def apply_plan(plan: dict[str, Any]) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    archive = Path(plan["audit_dir"]) / "preinference_system_error_recovery" / stamp
    archive.mkdir(parents=True, exist_ok=False)
    retry_path = Path(plan["retry_path"])
    shutil.copy2(retry_path, archive / retry_path.name)
    for path in plan["failure_files"]:
        shutil.copy2(path, archive / path.name)

    targets = set(plan["targets"])
    for path, payload in plan["failure_files"].items():
        updated = [
            row for row in payload
            if not (
                isinstance(row, dict)
                and _task_name(row) in targets
                and _allowed_failure(row)
            )
        ]
        _atomic_json(path, updated)

    retry = dict(plan["retry"])
    attempts = dict(retry.get("auto_attempts") or {})
    for name, count in plan["actual_counts"].items():
        attempts[name] = count
    retry["auto_attempts"] = attempts
    retry["priority_queue"] = [
        item for item in (retry.get("priority_queue") or []) if str(item) not in targets
    ]
    retry["retry_queue"] = [
        *plan["targets"],
        *[
            item for item in (retry.get("retry_queue") or [])
            if str(item) not in targets
        ],
    ]
    retry["updated_at"] = datetime.now().isoformat()
    _atomic_json(retry_path, retry)

    manifest = {
        "schema": "samsung-ocr-preinference-system-error-recovery/v1",
        "targets": [
            {
                "file_name": name,
                "preserved_actual_calls": plan["actual_counts"][name],
                "remaining_call_cap": 3 - plan["actual_counts"][name],
            }
            for name in plan["targets"]
        ],
        "archive_dir": str(archive),
        "applied_at": datetime.now().astimezone().isoformat(),
    }
    manifest_path = archive / "manifest.json"
    _atomic_json(manifest_path, manifest)
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image-dir", required=True)
    parser.add_argument("--audit-dir", required=True)
    parser.add_argument("--file-name", action="append", required=True)
    parser.add_argument("--status-url", default="http://127.0.0.1:5002/api/status")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    status = _assert_backend_stopped(args.status_url)
    plan = build_plan(Path(args.image_dir), Path(args.audit_dir), args.file_name)
    report = {
        "status": "would_recover",
        "backend_current_file": status.get("current_file"),
        "targets": [
            {
                "file_name": name,
                "preserved_actual_calls": plan["actual_counts"][name],
                "remaining_call_cap": 3 - plan["actual_counts"][name],
            }
            for name in plan["targets"]
        ],
    }
    if args.apply:
        report["status"] = "recovered"
        report["manifest"] = str(apply_plan(plan))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

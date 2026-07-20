"""Keep bounded three-pass repairs visible while an older live backend runs.

This is a zero-model-call bridge for a single evidence revision.  It never
stops OCR, the Dashboard, or the uploader.  New deterministic adjudication is
performed against a temporary copy of the active result file, then merged into
the latest live file with an optimistic compare-before-replace.  The live
backend may rewrite its in-memory session on every completed photo; previously
proved repairs are therefore re-applied without re-queuing or duplicating
presentation events.

The bridge exits when the backend evidence revision changes.  A fresh backend
already contains the adjudication rules and must not need this compatibility
worker.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.request
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.audit_fields import EVIDENCE_GUARD_REVISION
from tools.finalize_existing_three_pass_reviews import (
    _review_required,
    _task_file_name,
    finalize_file,
    load_authority_manifest,
)


SCHEMA = "samsung-ocr-active-three-pass-repair-bridge/v1"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _status(backend_url: str) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(
            f"{backend_url.rstrip('/')}/api/status",
            timeout=5,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _result_file(status: dict[str, Any]) -> Path | None:
    active_dir = Path(
        str(status.get("current_relative_dir") or status.get("image_dir") or "")
    )
    if not active_dir.is_dir() or "_ocr_staging" not in str(active_dir):
        return None
    candidates = sorted(
        active_dir.glob("*OCR成功.json"),
        key=lambda item: item.stat().st_mtime_ns,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _repair_key(result_path: Path, file_name: str) -> str:
    return f"{result_path.resolve()}::{file_name}"


def _load_store(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    try:
        payload = _read_json(path)
    except Exception:
        return {}
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        return {}
    repairs = payload.get("repairs")
    return dict(repairs) if isinstance(repairs, dict) else {}


def _save_store(path: Path, repairs: dict[str, dict[str, Any]]) -> None:
    _atomic_json(
        path,
        {
            "schema": SCHEMA,
            "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
            "updated_at": datetime.now().isoformat(),
            "repairs": repairs,
        },
    )


def _append_event(path: Path, event: str, **details: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "schema": SCHEMA,
        "timestamp": datetime.now().isoformat(),
        "event": event,
        **details,
    }
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        stream.write("\n")


def _merge_repaired_tasks(
    tasks: list[dict[str, Any]],
    *,
    result_path: Path,
    repairs: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    merged = list(tasks)
    applied = 0
    for index, task in enumerate(merged):
        if not isinstance(task, dict):
            continue
        file_name = _task_file_name(task)
        repair = repairs.get(_repair_key(result_path, file_name))
        if not isinstance(repair, dict):
            continue
        repaired_meta = ((repair.get("data") or {}).get("ocr_meta") or {})
        current_meta = ((task.get("data") or {}).get("ocr_meta") or {})
        already_present = bool(
            current_meta.get("auto_verified") is True
            and current_meta.get("auto_review_required") is not True
            and current_meta.get("adjudication_rule")
            == repaired_meta.get("adjudication_rule")
            and str(current_meta.get("review_status") or "")
            == str(repaired_meta.get("review_status") or "")
        )
        if already_present:
            continue
        merged[index] = deepcopy(repair)
        applied += 1
    return merged, applied


def _merge_live_file(
    result_path: Path,
    repairs: dict[str, dict[str, Any]],
    *,
    attempts: int = 8,
) -> int:
    for _ in range(max(1, attempts)):
        try:
            before = result_path.read_bytes()
            tasks = json.loads(before.decode("utf-8"))
        except Exception:
            time.sleep(0.1)
            continue
        if not isinstance(tasks, list):
            return 0
        merged, applied = _merge_repaired_tasks(
            tasks,
            result_path=result_path,
            repairs=repairs,
        )
        if applied == 0:
            return 0
        # Never replace a newer backend write with an older snapshot.
        try:
            if _sha256_bytes(result_path.read_bytes()) != _sha256_bytes(before):
                time.sleep(0.1)
                continue
            _atomic_json(result_path, merged)
            return applied
        except Exception:
            time.sleep(0.1)
    return 0


def _new_review_names(
    result_path: Path,
    repairs: dict[str, dict[str, Any]],
) -> set[str]:
    try:
        tasks = _read_json(result_path)
    except Exception:
        return set()
    if not isinstance(tasks, list):
        return set()
    return {
        _task_file_name(task)
        for task in tasks
        if isinstance(task, dict)
        and _review_required(task)
        and _repair_key(result_path, _task_file_name(task)) not in repairs
    }


def _collect_stale_status_repairs(
    result_path: Path,
    repairs: dict[str, dict[str, Any]],
) -> int:
    """Clear provisional review text from already verified live rows."""
    try:
        tasks = _read_json(result_path)
    except Exception:
        return 0
    if not isinstance(tasks, list):
        return 0
    added = 0
    for task in tasks:
        if not isinstance(task, dict):
            continue
        meta = ((task.get("data") or {}).get("ocr_meta") or {})
        if not (
            meta.get("auto_verified") is True
            and meta.get("auto_review_required") is not True
            and str(meta.get("review_status") or "") == "review_required"
        ):
            continue
        repaired = deepcopy(task)
        repaired["data"]["ocr_meta"]["review_status"] = "已完成"
        key = _repair_key(result_path, _task_file_name(task))
        repairs[key] = repaired
        added += 1
    return added


def _prove_new_repairs(
    *,
    result_path: Path,
    trace_path: Path,
    output_dir: Path,
    names: set[str],
    repairs: dict[str, dict[str, Any]],
    work_dir: Path,
) -> list[dict[str, Any]]:
    if not names:
        return []
    work_dir.mkdir(parents=True, exist_ok=True)
    snapshot = work_dir / (
        f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{result_path.name}"
    )
    snapshot.write_bytes(result_path.read_bytes())
    report = finalize_file(
        snapshot,
        trace_path,
        output_dir,
        apply=True,
        only_file_names=set(names),
    )
    repaired_tasks = _read_json(snapshot)
    finalized_names = {
        str(row.get("file") or "")
        for row in report
        if row.get("status") == "finalized"
    }
    for task in repaired_tasks if isinstance(repaired_tasks, list) else []:
        name = _task_file_name(task)
        if name in finalized_names:
            repairs[_repair_key(result_path, name)] = deepcopy(task)
    return report


def _claim_lock(lock_path: Path) -> None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.is_file():
        try:
            old = _read_json(lock_path)
            old_pid = int(old.get("pid") or 0)
        except Exception:
            old_pid = 0
        if old_pid and psutil.pid_exists(old_pid):
            raise RuntimeError(f"active repair bridge already running: pid={old_pid}")
        lock_path.unlink(missing_ok=True)
    handle = os.open(
        lock_path,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
    )
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump(
            {
                "schema": SCHEMA,
                "pid": os.getpid(),
                "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
                "started_at": datetime.now().isoformat(),
            },
            stream,
            ensure_ascii=False,
        )


def run(args: argparse.Namespace) -> int:
    audit_dir = args.output_dir / "_ocr_audit" / "active_three_pass_repairs"
    lock_path = audit_dir / "worker.lock"
    store_path = audit_dir / "repair_store.json"
    event_path = audit_dir / "events.jsonl"
    work_dir = audit_dir / "snapshots"
    _claim_lock(lock_path)
    repairs = _load_store(store_path)
    authority_entries = 0
    if args.authority_manifest:
        authority_entries = load_authority_manifest(args.authority_manifest)
    _append_event(
        event_path,
        "worker_started",
        pid=os.getpid(),
        evidence_guard_revision=EVIDENCE_GUARD_REVISION,
        authority_manifest=str(args.authority_manifest or ""),
        authority_entries=authority_entries,
    )
    try:
        while True:
            status = _status(args.backend_url)
            if status is None:
                time.sleep(args.poll_seconds)
                continue
            live_revision = str(status.get("evidence_guard_revision") or "")
            if live_revision != EVIDENCE_GUARD_REVISION:
                _append_event(
                    event_path,
                    "worker_stopped_revision_changed",
                    live_revision=live_revision,
                )
                return 0
            result_path = _result_file(status)
            if result_path is None:
                time.sleep(args.poll_seconds)
                continue

            stale_status_added = _collect_stale_status_repairs(
                result_path,
                repairs,
            )
            if stale_status_added:
                _save_store(store_path, repairs)
                _append_event(
                    event_path,
                    "stale_verified_status_repaired",
                    result_file=str(result_path),
                    count=stale_status_added,
                )
            applied = _merge_live_file(result_path, repairs)
            if applied:
                _append_event(
                    event_path,
                    "known_repairs_reapplied",
                    result_file=str(result_path),
                    count=applied,
                )

            names = _new_review_names(result_path, repairs)
            if names:
                report = _prove_new_repairs(
                    result_path=result_path,
                    trace_path=args.trace,
                    output_dir=args.output_dir,
                    names=names,
                    repairs=repairs,
                    work_dir=work_dir,
                )
                finalized = [
                    row for row in report if row.get("status") == "finalized"
                ]
                if finalized:
                    _save_store(store_path, repairs)
                    _merge_live_file(result_path, repairs)
                    _append_event(
                        event_path,
                        "new_repairs_finalized",
                        result_file=str(result_path),
                        rows=finalized,
                    )
            time.sleep(args.poll_seconds)
    finally:
        try:
            if lock_path.is_file():
                payload = _read_json(lock_path)
                if int(payload.get("pid") or 0) == os.getpid():
                    lock_path.unlink(missing_ok=True)
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument(
        "--backend-url",
        default="http://127.0.0.1:5002",
    )
    parser.add_argument(
        "--authority-manifest",
        type=Path,
        help=(
            "Optional hash-bound pixel authority manifest for terminal "
            "three-pass rows; never a filename-only rule."
        ),
    )
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args()
    args.output_dir = args.output_dir.resolve()
    args.trace = args.trace.resolve()
    if args.authority_manifest:
        args.authority_manifest = args.authority_manifest.resolve()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())

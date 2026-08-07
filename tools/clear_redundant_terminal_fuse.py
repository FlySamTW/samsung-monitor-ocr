"""Clear a fuse caused only by re-running a source with a confirmed terminal receipt.

The command never changes OCR fields or model-call accounting.  It accepts only
an idle, exact staging checkpoint whose source map resolves to an immutable
source and an already-confirmed, current/compatible stream-upload receipt.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.audit_fields import EVIDENCE_GUARD_REVISION
from tools.build_v1945_evidence_backfill import stable_source_id
from tools.rclone_drive_upload import sha256_file
from tools.stream_drive_upload import COMPATIBLE_PENDING_REVISION_MIGRATIONS


SCHEMA = "samsung-ocr-redundant-terminal-fuse-clearance/v1"


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return payload


def _write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _status(backend_url: str) -> dict[str, Any]:
    with urllib.request.urlopen(backend_url.rstrip("/") + "/api/status", timeout=20) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError("backend status must be an object")
    return payload


def _assert_no_runner(repo_root: Path) -> None:
    needles = (
        "rerun_staged_candidates.py",
        "recursive_ocr_flat_export.py",
        "auto_rerun_questionable_after_recursive.ps1",
    )
    repo = str(repo_root.resolve()).casefold()
    matches: list[int] = []
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            command = " ".join(process.info.get("cmdline") or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        folded = command.casefold()
        if repo in folded and any(needle.casefold() in folded for needle in needles):
            matches.append(int(process.info["pid"]))
    if matches:
        raise RuntimeError(f"OCR runner still active: {matches}")


def recover(
    *,
    repo_root: Path,
    output_dir: Path,
    staging_dir: Path,
    backend_url: str,
    apply: bool,
    status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    output_dir = output_dir.resolve()
    staging_dir = staging_dir.resolve()
    audit_dir = output_dir / "_ocr_audit"
    fuse_path = audit_dir / "runtime_health_fuse.json"
    if not staging_dir.is_dir() or not fuse_path.is_file():
        raise RuntimeError("exact staging directory and active fuse are required")

    live = _status(backend_url) if status is None else dict(status)
    if live.get("is_running") is True:
        raise RuntimeError("backend is not at an idle photo boundary")
    if Path(str(live.get("image_dir") or "")).resolve() != staging_dir:
        raise RuntimeError("backend checkpoint does not match staging directory")
    _assert_no_runner(repo_root)

    fuse = _read(fuse_path)
    file_name = str(fuse.get("source_file") or "").strip()
    run_id = str(fuse.get("run_id") or "").strip()
    if fuse.get("active") is not True or not file_name or not run_id:
        raise RuntimeError("fuse identity is incomplete")

    source_map = _read(staging_dir / ".ocr_source_map.json")
    info = dict((source_map.get("items") or {}).get(file_name) or {})
    source_id = str(info.get("source_item_id") or "").strip().lower()
    original = Path(str(info.get("original_source_path") or "")).resolve()
    if not original.is_file() or stable_source_id(original) != source_id:
        raise RuntimeError("staging source identity is invalid")

    receipt_path = output_dir / "_drive_upload_stream" / "receipts" / f"{source_id}.json"
    receipt = _read(receipt_path)
    receipt_revision = str(receipt.get("evidence_guard_revision") or "")
    revision_ok = (
        receipt_revision == EVIDENCE_GUARD_REVISION
        or COMPATIBLE_PENDING_REVISION_MIGRATIONS.get(receipt_revision)
        == EVIDENCE_GUARD_REVISION
    )
    source_hash = sha256_file(original)
    published = Path(str(receipt.get("published_path") or "")).resolve()
    if (
        receipt.get("schema") != "samsung-ocr-stream-receipt-v1"
        or receipt.get("source_item_id") != source_id
        or Path(str(receipt.get("original_source_path") or "")).resolve() != original
        or receipt.get("source_sha256") != source_hash
        or not revision_ok
        or not receipt.get("drive_file_id")
        or not receipt.get("remote_path")
        or not receipt.get("confirmed_at")
        or not published.is_file()
        or receipt.get("published_sha256") != sha256_file(published)
        or receipt.get("run_id") == run_id
    ):
        raise RuntimeError("confirmed terminal receipt contract is not satisfied")

    ledger_path = audit_dir / "model_call_lifetime_ledger_v1" / source_id[:2] / f"{source_id}.json"
    ledger = _read(ledger_path)
    if not any(str(row.get("run_id") or "") == run_id for row in ledger.get("reservations") or []):
        raise RuntimeError("redundant run is absent from the lifetime call ledger")

    report: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "would_clear" if not apply else "cleared",
        "source_file": file_name,
        "source_item_id": source_id,
        "redundant_run_id": run_id,
        "terminal_run_id": receipt.get("run_id"),
        "terminal_receipt": str(receipt_path),
        "drive_file_id": receipt.get("drive_file_id"),
        "source_sha256": source_hash,
        "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
        "model_calls_rolled_back": 0,
        "ocr_result_changed": False,
    }
    if not apply:
        return report

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    cleared_at = datetime.now().isoformat(timespec="seconds")
    clearance = audit_dir / "runtime_health_fuse_clearance" / f"redundant_terminal_{stamp}_{source_id[:12]}.json"
    archived = audit_dir / "runtime_health_fuse_history" / f"redundant_terminal_{stamp}_{source_id[:12]}.json"
    _write_atomic(clearance, {**report, "cleared_at": cleared_at})
    _write_atomic(
        archived,
        {
            **fuse,
            "active": False,
            "cleared_at": cleared_at,
            "clearance": "preexisting_confirmed_terminal_receipt",
            "clearance_receipt": str(clearance),
            "terminal_receipt": str(receipt_path),
        },
    )
    fuse_path.unlink()
    return {**report, "clearance_receipt": str(clearance), "archived_fuse": str(archived)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--backend-url", default="http://127.0.0.1:5002")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = recover(
        repo_root=args.repo_root,
        output_dir=args.output_dir,
        staging_dir=args.staging_dir,
        backend_url=args.backend_url,
        apply=args.apply,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

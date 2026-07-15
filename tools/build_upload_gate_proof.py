#!/usr/bin/env python3
"""Build and validate the exact content-bound upload gate proof.

This is the shared authority used before any uploader or historical-year
continuation may start.  A stale proof is removed on execute failure.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = "samsung-ocr-upload-gate/v1"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"not a JSON object: {path}")
    return value


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def as_int(value: object, default: int = -1) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def build_proof(output_dir: Path, year: int) -> tuple[dict | None, list[str]]:
    audit_dir = output_dir / "_ocr_audit"
    manifest_dir = output_dir / "_drive_upload"
    runtime_health_fuse = audit_dir / "runtime_health_fuse.json"
    if runtime_health_fuse.exists():
        return None, [f"runtime_health_fuse_active:{runtime_health_fuse}"]
    risk_json = audit_dir / f"distant_followme_risk_{year}_latest.json"
    risk_csv = audit_dir / f"distant_followme_risk_{year}_latest.csv"
    summary_path = manifest_dir / "drive_upload_summary.json"
    pending_csv = manifest_dir / "drive_upload_ready_pending.csv"
    next_batch_csv = manifest_dir / "drive_upload_next_batch.csv"
    required = (risk_json, risk_csv, summary_path, pending_csv, next_batch_csv)
    errors = [f"missing:{path}" for path in required if not path.is_file()]
    if errors:
        return None, errors

    try:
        risk = read_json(risk_json)
        summary = read_json(summary_path)
        pending_rows = read_csv(pending_csv)
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return None, [f"unreadable_authority:{exc}"]

    finalization = risk.get("finalization_proof") or {}
    if risk.get("audit_complete") is not True:
        errors.append("risk_audit_incomplete")
    if finalization.get("audit_complete") is not True or finalization.get("complete") is not True:
        errors.append("finalization_incomplete")
    audit_input = str(risk.get("audit_input_sha256") or "")
    if not audit_input or audit_input != str(finalization.get("audit_input_sha256") or ""):
        errors.append("audit_input_identity_mismatch")
    try:
        if str(risk.get("risk_output_sha256") or "").lower() != file_sha256(risk_csv):
            errors.append("risk_output_hash_mismatch")
    except OSError as exc:
        errors.append(f"risk_hash_error:{exc}")

    if summary.get("current_year_risk_audit_fresh") is not True:
        errors.append("manifest_risk_audit_stale")
    if summary.get("current_year_upload_gate_open") is not True:
        errors.append("manifest_gate_closed")
    if str(summary.get("current_audit_input_sha256") or "") != audit_input:
        errors.append("manifest_audit_input_mismatch")
    try:
        next_batch_hash = file_sha256(next_batch_csv)
        if str(summary.get("next_batch_sha256") or "").lower() != next_batch_hash:
            errors.append("next_batch_hash_mismatch")
    except OSError as exc:
        next_batch_hash = ""
        errors.append(f"next_batch_hash_error:{exc}")

    blocked = [row for row in pending_rows if row.get("status") != "ready" or str(row.get("reasons") or "").strip()]
    if blocked:
        errors.append(f"pending_contains_blocked:{len(blocked)}")
    if as_int(summary.get("ready_pending")) != len(pending_rows):
        errors.append("pending_count_mismatch")
    if as_int(summary.get("next_batch")) != len(pending_rows):
        errors.append("next_batch_count_mismatch")
    if summary_path.stat().st_mtime_ns < risk_json.stat().st_mtime_ns:
        errors.append("manifest_predates_risk_audit")

    proof_inputs: list[dict[str, str]] = []
    input_values = [
        ("candidate_csv", str(finalization.get("candidate_csv") or "")),
        ("candidate_summary_json", str(finalization.get("candidate_summary_json") or "")),
    ]
    if as_int(finalization.get("candidate_rows"), 0) > 0:
        input_values.extend([
            ("result_csv", str(finalization.get("result_csv") or "")),
            ("run_summary_csv", str(finalization.get("run_summary_csv") or "")),
        ])
    for name, raw_path in input_values:
        if not raw_path:
            errors.append(f"finalization_input_missing:{name}")
            continue
        path = Path(raw_path)
        if not path.is_file():
            errors.append(f"finalization_input_missing:{path}")
            continue
        proof_inputs.append({"path": str(path.resolve()), "sha256": file_sha256(path)})

    if errors:
        return None, errors
    proof = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "gate_open": True,
        "audit_summary_path": str(risk_json.resolve()),
        "audit_summary_sha256": file_sha256(risk_json),
        "risk_csv_path": str(risk_csv.resolve()),
        "risk_output_sha256": file_sha256(risk_csv),
        "audit_input_sha256": audit_input,
        "audit_inputs": proof_inputs,
        "backfill_run_id": str(finalization.get("backfill_run_id") or ""),
        "manifest_summary_path": str(summary_path.resolve()),
        "manifest_summary_sha256": file_sha256(summary_path),
        "pending_csv_path": str(pending_csv.resolve()),
        "pending_sha256": file_sha256(pending_csv),
        "pending_count": len(pending_rows),
        "next_batch_csv_path": str(next_batch_csv.resolve()),
        "next_batch_sha256": next_batch_hash,
    }
    return proof, []


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp.{os.getpid()}")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def run(output_dir: Path, year: int, *, execute: bool = False) -> dict:
    proof_path = output_dir / "_drive_upload" / "upload_gate_proof.json"
    proof, errors = build_proof(output_dir, year)
    if execute:
        if proof is None:
            proof_path.unlink(missing_ok=True)
        else:
            write_atomic(proof_path, proof)
    return {
        "valid": proof is not None,
        "executed": bool(execute and proof is not None),
        "proof_path": str(proof_path.resolve()),
        "pending_count": int((proof or {}).get("pending_count") or 0),
        "audit_input_sha256": str((proof or {}).get("audit_input_sha256") or ""),
        "backfill_run_id": str((proof or {}).get("backfill_run_id") or ""),
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the content-bound upload gate proof.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--year", type=int, default=datetime.now().year)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    summary = run(Path(args.output_dir).resolve(), args.year, execute=args.execute)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

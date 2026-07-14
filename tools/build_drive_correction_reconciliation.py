"""Rebuild the Drive replacement ledger from UTF-8 local authorities.

The pre-v19.45 ledger is not trusted: some paths were written with mojibake and
its gate evidence predates the evidence backfill.  This builder joins stale
uploaded rows to the current copied.csv mapping by exact image content (with a
strict filename-identity fallback only when the old local copy is gone), then
requires a current manifest row before emitting an actionable record.

Dry-run is the default.  ``--execute`` replaces the JSONL atomically only when
every stale row has one unambiguous current source mapping and manifest row.
Missing historical Drive IDs are allowed; the reconciler's read-only
``discover-old`` phase can fill those later by unique remote path.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
from collections import defaultdict
from pathlib import Path

from build_v1945_evidence_backfill import stable_source_id


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def period_from_name(name: str) -> str:
    match = re.search(r"(?:^|-)M?-(20\d{4})(?:-|$)", name)
    if not match:
        match = re.search(r"M-(20\d{4})-", name)
    return match.group(1) if match else ""


def sequence_from_name(name: str) -> str:
    match = re.search(r"-(\d+)(?:_\d+)?\.[^.]+$", name)
    return match.group(1) if match else ""


def subject_prefix(name: str) -> str:
    for marker in ("-單機-", "-遠景-", "-照片不清楚-", "-它牌-"):
        if marker in name:
            return name.split(marker, 1)[0].casefold()
    return ""


def _unique_uploaded_ids(rows: list[dict[str, str]]) -> dict[tuple[str, str], list[str]]:
    values: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in rows:
        year = str(row.get("year") or "").strip()
        name = str(row.get("file_name") or "").strip()
        file_id = str(row.get("drive_file_id") or "").strip()
        if year and name and file_id:
            values[(year, name)].add(file_id)
    return {key: sorted(ids) for key, ids in values.items()}


def _manifest_index(rows: list[dict[str, str]]) -> dict[tuple[str, str], list[dict[str, str]]]:
    values: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        year = str(row.get("year") or "").strip()
        name = str(row.get("file_name") or "").strip()
        if year and name:
            values[(year, name)].append(row)
    return values


def _load_current_outputs(audit_dir: Path, year: str) -> tuple[list[dict[str, str]], list[str]]:
    outputs: list[dict[str, str]] = []
    errors: list[str] = []
    for copied_path in sorted(audit_dir.glob("*/copied.csv"), key=lambda item: str(item).casefold()):
        for row in read_csv(copied_path):
            period = str(row.get("period") or "").strip()
            if not period.startswith(year):
                continue
            original = Path(str(row.get("original_path") or "")).resolve()
            target = Path(str(row.get("target_path") or "")).resolve()
            target_name = str(row.get("target_name") or target.name).strip()
            if not original.is_file() or not target.is_file() or not target_name:
                errors.append(f"invalid current mapping: {copied_path} {target_name or '?'}")
                continue
            outputs.append({
                "period": period,
                "original_path": str(original),
                "target_path": str(target),
                "target_name": target_name,
                "content_sha256": sha256(target),
                "sequence": sequence_from_name(target_name),
                "prefix": subject_prefix(target_name),
            })
    return outputs, errors


def build_rows(
    *,
    output_dir: Path,
    year: str,
    stale_rows: list[dict[str, str]],
    uploaded_rows: list[dict[str, str]],
    manifest_rows: list[dict[str, str]],
    current_outputs: list[dict[str, str]],
    current_mapping_errors: list[str] | None = None,
) -> tuple[list[dict], dict]:
    uploaded = _unique_uploaded_ids(uploaded_rows)
    manifest = _manifest_index(manifest_rows)
    by_period_hash: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    by_period_identity: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for item in current_outputs:
        by_period_hash[(item["period"], item["content_sha256"])].append(item)
        if item["prefix"] and item["sequence"]:
            by_period_identity[(item["period"], item["prefix"], item["sequence"])].append(item)

    records: list[dict] = []
    errors = list(current_mapping_errors or [])
    counts = defaultdict(int)
    for index, stale in enumerate(stale_rows, start=1):
        old_name = str(stale.get("file_name") or "").strip()
        row_year = str(stale.get("year") or year).strip()
        period = str(stale.get("period") or period_from_name(old_name)).strip()
        old_path = Path(str(stale.get("source_path") or output_dir / old_name)).resolve()
        old_hash = sha256(old_path) if old_path.is_file() else ""
        if old_hash:
            candidates = list(by_period_hash.get((period, old_hash), []))
            if len(candidates) > 1:
                prefix, sequence = subject_prefix(old_name), sequence_from_name(old_name)
                narrowed = [item for item in candidates if item["prefix"] == prefix and item["sequence"] == sequence]
                if narrowed:
                    candidates = narrowed
        else:
            counts["missing_old_local"] += 1
            candidates = list(by_period_identity.get((period, subject_prefix(old_name), sequence_from_name(old_name)), []))

        mapping_error = ""
        current: dict[str, str] | None = None
        if len(candidates) == 1:
            current = candidates[0]
        elif not candidates:
            mapping_error = "current_source_mapping_missing"
        else:
            mapping_error = "current_source_mapping_ambiguous"

        current_name = current["target_name"] if current else ""
        manifest_matches = manifest.get((row_year, current_name), []) if current_name else []
        manifest_row = manifest_matches[0] if len(manifest_matches) == 1 else None
        if current and not manifest_row:
            mapping_error = "current_manifest_missing" if not manifest_matches else "current_manifest_ambiguous"

        ids = uploaded.get((row_year, old_name), [])
        if len(ids) > 1:
            mapping_error = "old_drive_id_ambiguous"
        old_id = ids[0] if len(ids) == 1 else ""
        if old_id:
            counts["unique_old_drive_id"] += 1
        else:
            counts["old_drive_id_discovery_required"] += 1

        gate_evidence = ""
        if manifest_row:
            manifest_status = str(manifest_row.get("status") or "").strip()
            reasons = str(manifest_row.get("reasons") or "").strip()
            if manifest_status != "ready" or reasons:
                gate_evidence = reasons or f"manifest_status={manifest_status or 'missing'}"
        elif mapping_error:
            gate_evidence = mapping_error

        if mapping_error:
            errors.append(f"row {index} {old_name}: {mapping_error}")
            counts["mapping_errors"] += 1
        elif gate_evidence:
            counts["gate_blocked"] += 1
        else:
            counts["new_ready"] += 1

        local_path = Path(current["target_path"]) if current else None
        replacement_mode = ""
        if current:
            replacement_mode = "verify_unchanged" if old_name == current_name else "replace_name"
            counts[replacement_mode] += 1
        records.append({
            "status": "new_ready" if current and not mapping_error and not gate_evidence else "detected",
            "source_identity": stable_source_id(current["original_path"]) if current else "",
            "original_source_path": current["original_path"] if current else "",
            "year": row_year,
            "period": period,
            "replacement_mode": replacement_mode,
            "old_file_name": old_name,
            "old_remote_path": str(stale.get("remote_path") or f"{row_year}/{old_name}"),
            "old_drive_file_id": old_id,
            "local_path": str(local_path) if local_path else "",
            "corrected_file_name": current_name,
            "local_size": local_path.stat().st_size if local_path and local_path.is_file() else None,
            "local_sha256": current["content_sha256"] if current else "",
            "gate_evidence": gate_evidence,
            "mapping_error": mapping_error,
            "new_drive_file_id": "",
            "new_remote_path": "",
            "new_remote_size": None,
            "new_remote_md5": "",
            "new_upload_receipt": None,
            "old_disposal_receipt": None,
        })

    summary = {
        "year": year,
        "stale_rows": len(stale_rows),
        "ledger_rows": len(records),
        **{key: counts[key] for key in (
            "new_ready", "gate_blocked", "mapping_errors", "missing_old_local",
            "unique_old_drive_id", "old_drive_id_discovery_required",
            "replace_name", "verify_unchanged",
        )},
        "error_samples": errors[:30],
    }
    return records, summary


def write_atomic_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp.{os.getpid()}")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def run(output_dir: Path, year: str, ledger: Path, *, execute: bool = False) -> dict:
    output_dir = output_dir.resolve()
    upload_dir = output_dir / "_drive_upload"
    audit_dir = output_dir / "_ocr_audit"
    required = {
        "stale": upload_dir / "drive_upload_stale_uploaded_review_required.csv",
        "uploaded": upload_dir / "drive_upload_uploaded.csv",
        "manifest": upload_dir / "drive_upload_all.csv",
    }
    missing_inputs = [str(path) for path in required.values() if not path.is_file()]
    current_outputs, current_errors = _load_current_outputs(audit_dir, year)
    rows, summary = build_rows(
        output_dir=output_dir,
        year=year,
        stale_rows=read_csv(required["stale"]),
        uploaded_rows=read_csv(required["uploaded"]),
        manifest_rows=read_csv(required["manifest"]),
        current_outputs=current_outputs,
        current_mapping_errors=current_errors + [f"missing input: {path}" for path in missing_inputs],
    )
    safe = not missing_inputs and summary["mapping_errors"] == 0 and not current_errors
    summary.update({
        "output_dir": str(output_dir),
        "ledger": str(ledger.resolve()),
        "execute_requested": execute,
        "executed": bool(execute and safe),
        "safe_to_replace": safe,
        "current_outputs": len(current_outputs),
        "current_mapping_errors": len(current_errors),
        "missing_inputs": missing_inputs,
    })
    if execute and safe:
        write_atomic_jsonl(ledger, rows)
        summary_path = ledger.with_suffix(ledger.suffix + ".summary.json")
        temp = summary_path.with_name(summary_path.name + f".tmp.{os.getpid()}")
        temp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, summary_path)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a fail-closed UTF-8 Drive replacement ledger.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--year", default="2026")
    parser.add_argument("--ledger", default="")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    ledger = Path(args.ledger) if args.ledger else output_dir / "_drive_upload" / "drive_correction_reconciliation.jsonl"
    summary = run(output_dir, str(args.year), ledger, execute=args.execute)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["safe_to_replace"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

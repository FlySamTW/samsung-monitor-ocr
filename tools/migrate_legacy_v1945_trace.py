"""Migrate legacy repo-root v19.45 traces into the durable audit location.

The old live backend recorded staging paths but no stable source identity.  This
tool resolves each row through the staged-rerun candidate CSV, enriches it, and
writes the destination atomically.  It is fail-closed: any unresolved or
ambiguous row prevents mutation.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Iterable


PERIOD_RE = re.compile(r"(?<!\d)(20\d{4})(?!\d)")


def infer_period(value: object) -> str:
    match = PERIOD_RE.search(str(value or ""))
    return match.group(1) if match else ""


def stable_source_id(path: str) -> str:
    resolved = str(Path(path).resolve())
    return hashlib.sha256(resolved.casefold().encode("utf-8")).hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def build_candidate_index(paths: Iterable[Path]) -> tuple[dict[tuple[str, str], dict[str, str]], set[tuple[str, str]]]:
    candidates: dict[tuple[str, str], dict[str, str]] = {}
    ambiguous: set[tuple[str, str]] = set()
    for path in paths:
        for row in _read_csv(path):
            original = str(row.get("source_path") or "").strip()
            file_name = str(row.get("file_name") or Path(original).name).strip()
            period = str(row.get("period") or infer_period(original)).strip()
            if not original or not file_name or not period:
                continue
            key = (period, file_name.casefold())
            candidate = {
                "original_source_path": str(Path(original).resolve()),
                "period": period,
                "audit_folder": str(row.get("audit_folder") or "").strip(),
            }
            previous = candidates.get(key)
            if previous and previous["original_source_path"].casefold() != candidate["original_source_path"].casefold():
                ambiguous.add(key)
                candidates.pop(key, None)
                continue
            if key not in ambiguous:
                candidates[key] = candidate
    return candidates, ambiguous


def _read_jsonl(path: Path) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    errors: list[str] = []
    if not path.exists():
        return rows, errors
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"line {line_number}: {exc.msg}")
                continue
            if not isinstance(item, dict):
                errors.append(f"line {line_number}: JSON value is not an object")
                continue
            rows.append(item)
    return rows, errors


def _trace_key(item: dict) -> str:
    key = str(item.get("trace_id") or "").strip()
    if key:
        return key
    canonical = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "legacy-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def enrich_legacy_row(item: dict, candidates: dict[tuple[str, str], dict[str, str]], ambiguous: set[tuple[str, str]]) -> tuple[dict | None, str]:
    row = dict(item)
    legacy_path = str(row.get("staging_source_path") or row.get("source_path") or "").strip()
    file_name = str(row.get("file_name") or Path(legacy_path).name).strip()
    period = str(row.get("period") or infer_period(legacy_path)).strip()
    key = (period, file_name.casefold())
    if key in ambiguous:
        return None, f"ambiguous candidate mapping: {period}/{file_name}"

    original = str(row.get("original_source_path") or "").strip()
    audit_folder = str(row.get("audit_folder") or "").strip()
    if not original:
        candidate = candidates.get(key)
        if candidate:
            original = candidate["original_source_path"]
            audit_folder = audit_folder or candidate["audit_folder"]
            period = period or candidate["period"]
        elif legacy_path and "_ocr_staging" not in legacy_path.casefold() and Path(legacy_path).is_file():
            original = str(Path(legacy_path).resolve())
    if not original or not period or not file_name:
        return None, f"unresolved source identity: {period or '?'} / {file_name or legacy_path or '?'}"

    source_id = stable_source_id(original)
    row["trace_id"] = _trace_key(row)
    row["trace_version"] = str(row.get("trace_version") or "v19.45")
    row["source_identity"] = source_id
    row["source_item_id"] = source_id
    row["staging_source_path"] = legacy_path
    row["source_path"] = str(Path(original).resolve())
    row["original_source_path"] = str(Path(original).resolve())
    row["file_name"] = file_name
    row["period"] = period
    row["audit_folder"] = audit_folder
    row["legacy_trace_migrated"] = True
    return row, ""


def migrate_trace(source: Path, destination: Path, candidate_csvs: list[Path], *, execute: bool = False) -> dict:
    candidates, ambiguous = build_candidate_index(candidate_csvs)
    source_rows, source_errors = _read_jsonl(source)
    destination_rows, destination_errors = _read_jsonl(destination)
    existing: dict[str, dict] = {_trace_key(row): row for row in destination_rows}
    unresolved = list(source_errors) + [f"destination {error}" for error in destination_errors]
    resolved = 0
    duplicates = 0

    for item in source_rows:
        enriched, error = enrich_legacy_row(item, candidates, ambiguous)
        if error:
            unresolved.append(error)
            continue
        assert enriched is not None
        key = _trace_key(enriched)
        if key in existing:
            duplicates += 1
            continue
        existing[key] = enriched
        resolved += 1

    summary = {
        "source": str(source.resolve()),
        "destination": str(destination.resolve()),
        "candidate_csvs": [str(path.resolve()) for path in candidate_csvs],
        "candidate_keys": len(candidates),
        "ambiguous_candidate_keys": len(ambiguous),
        "source_rows": len(source_rows),
        "destination_existing_rows": len(destination_rows),
        "resolved_new_rows": resolved,
        "duplicate_rows": duplicates,
        "unresolved_rows": len(unresolved),
        "unresolved_samples": unresolved[:20],
        "destination_rows_after": len(existing),
        "executed": bool(execute and not unresolved),
    }
    if execute and not unresolved:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_name(destination.name + f".tmp.{os.getpid()}")
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            for row in existing.values():
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, destination)
        summary_path = destination.parent / "v1945_evidence_trace_migration_summary.json"
        summary_temp = summary_path.with_name(summary_path.name + f".tmp.{os.getpid()}")
        summary_temp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(summary_temp, summary_path)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed migration of legacy v19.45 evidence traces.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--candidate-csv", action="append", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    summary = migrate_trace(
        Path(args.source),
        Path(args.destination),
        [Path(value) for value in args.candidate_csv],
        execute=args.execute,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["unresolved_rows"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Build a resumable current-year v19.45 evidence backfill candidate CSV.

The durable copied.csv files are the authority for original source identity.
Only sources without a trace verified by the current guard revision are emitted.  The build is
fail-closed: missing source files or conflicting source metadata prevent the
candidate CSV from being replaced.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from skills.audit_fields import EVIDENCE_GUARD_REVISION


FIELDS = ("source_path", "file_name", "period", "audit_folder", "reason", "source_item_id")


def stable_source_id(path: str | Path) -> str:
    resolved = str(Path(path).resolve())
    return hashlib.sha256(resolved.casefold().encode("utf-8")).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def load_verified_source_ids(audit_dir: Path) -> set[str]:
    verified: set[str] = set()
    for path in audit_dir.rglob("v1945_evidence_trace.jsonl"):
        try:
            with path.open("r", encoding="utf-8-sig") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    decision = item.get("guard_decision") or {}
                    if (
                        item.get("trace_version") != "v19.45"
                        or item.get("evidence_guard_revision") != EVIDENCE_GUARD_REVISION
                        or decision.get("verified") is not True
                    ):
                        continue
                    source_id = str(item.get("source_item_id") or item.get("source_identity") or "").strip()
                    original = str(item.get("original_source_path") or item.get("source_path") or "").strip()
                    if not source_id and original:
                        source_id = stable_source_id(original)
                    if source_id:
                        verified.add(source_id)
        except (OSError, UnicodeError, ValueError, TypeError):
            continue
    return verified


def build_candidates(audit_dir: Path, year: str) -> tuple[list[dict[str, str]], dict]:
    verified = load_verified_source_ids(audit_dir)
    candidates: dict[str, dict[str, str]] = {}
    seen: dict[str, dict[str, str]] = {}
    missing: list[str] = []
    conflicts: list[str] = []
    invalid: list[str] = []
    copied_rows = 0
    year_rows = 0

    for copied_path in sorted(audit_dir.glob("*/copied.csv"), key=lambda path: str(path).casefold()):
        for row in read_csv(copied_path):
            copied_rows += 1
            period = str(row.get("period") or "").strip()
            if not period.startswith(year):
                continue
            year_rows += 1
            original = str(row.get("original_path") or "").strip()
            file_name = str(row.get("original_name") or (Path(original).name if original else "")).strip()
            if not original or not file_name or len(period) != 6 or not period.isdigit():
                invalid.append(f"{copied_path}: incomplete row for {file_name or original or '?'}")
                continue
            source = Path(original).resolve()
            if not source.is_file():
                missing.append(str(source))
                continue
            source_id = stable_source_id(source)
            item = {
                "source_path": str(source),
                "file_name": file_name,
                "period": period,
                "audit_folder": str(copied_path.parent.resolve()),
                "reason": "v1945_evidence_backfill",
                "source_item_id": source_id,
            }
            previous = seen.get(source_id)
            if previous and previous != item:
                conflicts.append(f"{source_id}: {previous['source_path']} <> {item['source_path']}")
                candidates.pop(source_id, None)
                continue
            seen[source_id] = item
            if source_id not in verified:
                candidates[source_id] = item

    rows = sorted(candidates.values(), key=lambda row: (row["period"], row["source_path"].casefold()))
    summary = {
        "audit_dir": str(audit_dir.resolve()),
        "year": year,
        "copied_rows_scanned": copied_rows,
        "year_source_rows": year_rows,
        "unique_year_sources": len(seen),
        "verified_source_ids": len(verified),
        "already_verified_year_sources": sum(1 for source_id in seen if source_id in verified),
        "candidate_rows": len(rows),
        "missing_sources": len(missing),
        "conflicting_sources": len(conflicts),
        "invalid_rows": len(invalid),
        "error_samples": (missing + conflicts + invalid)[:20],
    }
    return rows, summary


def write_atomic_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp.{os.getpid()}")
    with temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def run(audit_dir: Path, year: str, output: Path, *, execute: bool = False) -> dict:
    rows, summary = build_candidates(audit_dir, year)
    errors = summary["missing_sources"] + summary["conflicting_sources"] + summary["invalid_rows"]
    summary.update({"output": str(output.resolve()), "executed": bool(execute and errors == 0)})
    if execute and errors == 0:
        write_atomic_csv(output, rows)
        summary_path = output.with_suffix(output.suffix + ".summary.json")
        temp = summary_path.with_name(summary_path.name + f".tmp.{os.getpid()}")
        temp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, summary_path)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a fail-closed v19.45 evidence backfill CSV.")
    parser.add_argument("--audit-dir", required=True)
    parser.add_argument("--year", default="2026")
    parser.add_argument("--output", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    summary = run(Path(args.audit_dir), str(args.year), Path(args.output), execute=args.execute)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    errors = summary["missing_sources"] + summary["conflicting_sources"] + summary["invalid_rows"]
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

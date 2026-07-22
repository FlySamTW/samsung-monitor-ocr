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
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from skills.audit_fields import (
    EVIDENCE_GUARD_REVISION,
    KNOWN_SOURCE_AUDIT_AUTHORITIES,
    adjudication_field_invariant_reasons,
)


FIELDS = ("source_path", "file_name", "period", "audit_folder", "reason", "source_item_id")
AUTHORITY_MANIFEST_SCHEMA = "samsung-ocr-bound-visual-authorities/v1"
# These revisions belong to the same accepted .64 evidence contract.  Later
# increments only added exact source-bound authorities, recovery bookkeeping,
# and process interlocks; they did not invalidate already verified .64+ rows.
# Keep this explicit: a future content-rule revision is not compatible unless
# it is reviewed and added here.
BACKFILL_COMPATIBLE_GUARD_REVISIONS = frozenset(
    {
        "20260721.64",
        "20260721.65",
        "20260721.66",
        "20260721.67",
        "20260721.68",
        "20260721.69",
        "20260721.70",
        "20260721.71",
        "20260722.72",
    }
)


def has_v71_verified_field_erasure(item: dict) -> bool:
    """Identify .71 finals that erased a value still present in the bound call.

    Revision .71 could clear a same-price ``市價`` observation and could also
    publish empty identity fields after a repeated pair was blocked by a
    narration/ownership conflict.  Such rows are not compatible evidence for
    .72 and must be revalidated rather than silently inherited.
    """
    if item.get("evidence_guard_revision") != "20260721.71":
        return False
    parsed = item.get("parsed_output") or {}
    if parsed.get("three_pass_adjudicated") is not True:
        return False
    if adjudication_field_invariant_reasons(parsed):
        return True
    raw_objects = item.get("raw_objects") or []
    for raw in raw_objects:
        try:
            raw_item = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            continue
        if not isinstance(raw_item, dict):
            continue
        raw_price = re.sub(r"[^0-9]", "", str(raw_item.get("price") or ""))
        narration = str(raw_item.get("narration") or raw_item.get("thinking") or "")
        if raw_price and not parsed.get("price"):
            formatted = f"{int(raw_price):,}"
            price_pattern = rf"(?:{re.escape(raw_price)}|{re.escape(formatted)})"
            if re.search(rf"(?:市價|原價|參考價).{{0,12}}{price_pattern}", narration) and re.search(
                rf"(?:會員售價|會員價|促銷價|現金價|特價|優惠價|現價|(?<!建議)售價).{{0,12}}{price_pattern}",
                narration,
            ):
                return True
    return False


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
                        or item.get("evidence_guard_revision")
                        not in BACKFILL_COMPATIBLE_GUARD_REVISIONS
                        or decision.get("verified") is not True
                    ):
                        continue
                    if has_v71_verified_field_erasure(item):
                        continue
                    parsed = item.get("parsed_output") or {}
                    if (
                        item.get("evidence_guard_revision") == "20260721.70"
                        and parsed.get("adjudication_rule")
                        == "two_wide_geometry_votes_veto_single_identity_outlier"
                    ):
                        # .70 had one narrow defect: a pass with
                        # unique_main=true could be counted as an identity-free
                        # wide vote. Do not invalidate unrelated .70 photos or
                        # spend a fourth call; only this exact outcome requires
                        # a hash-bound authority or zero-model revalidation.
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


def load_bound_visual_authorities(audit_dir: Path) -> dict[str, dict]:
    """Load immutable pixel authorities from dedicated audit locations.

    The manifests are accepted only when the source identity, source bytes,
    inference hash, authority type, and schema are all present.  Conflicting
    decisions for one source fail closed instead of silently choosing one.
    """
    authorities: dict[str, dict] = dict(KNOWN_SOURCE_AUDIT_AUTHORITIES)
    candidates = list(audit_dir.glob("*bound_visual_authorities*.json"))
    for directory_name in (
        "visual_authorities",
        "visual_authority",
        "active_three_pass_repairs",
        "bound_visual_authorities",
    ):
        directory = audit_dir / directory_name
        if directory.is_dir():
            candidates.extend(directory.glob("*.json"))

    for path in sorted(set(candidates), key=lambda item: str(item).casefold()):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, ValueError, TypeError):
            continue
        if payload.get("schema") != AUTHORITY_MANIFEST_SCHEMA:
            continue
        entries = list(payload.get("entries") or [])
        if int(payload.get("entry_count") or 0) != len(entries) or not entries:
            raise RuntimeError(f"invalid visual authority manifest: {path}")
        for entry in entries:
            source_id = str(entry.get("source_item_id") or "").strip().lower()
            source_hash = str(entry.get("source_file_sha256") or "").strip().lower()
            input_hash = str(entry.get("input_image_sha256") or "").strip().lower()
            original = Path(str(entry.get("original_source_path") or "")).resolve()
            if (
                not re.fullmatch(r"[0-9a-f]{64}", source_id)
                or not re.fullmatch(r"[0-9a-f]{64}", source_hash)
                or not re.fullmatch(r"[0-9a-f]{64}", input_hash)
                or entry.get("authority") != "human_audited_pixel_authority"
                or str(entry.get("view_type") or "") not in {"單機", "遠景"}
                or not original.is_file()
                or stable_source_id(original) != source_id
                or file_sha256(original) != source_hash
            ):
                raise RuntimeError(f"stale or invalid visual authority: {path}")
            previous = authorities.get(source_id)
            if previous and any(
                str(previous.get(field)) != str(entry.get(field))
                for field in (
                    "source_file_sha256",
                    "input_image_sha256",
                    "view_type",
                    "model",
                    "price",
                )
            ):
                raise RuntimeError(
                    f"conflicting visual authorities for {source_id}: {path}"
                )
            authorities[source_id] = dict(entry)
    return authorities


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_candidates(
    audit_dir: Path,
    year: str,
    known_source_authorities: dict[str, dict[str, str]] | None = None,
) -> tuple[list[dict[str, str]], dict]:
    verified = load_verified_source_ids(audit_dir)
    authorities = dict(
        load_bound_visual_authorities(audit_dir)
        if known_source_authorities is None
        else known_source_authorities
    )
    candidates: dict[str, dict[str, str]] = {}
    seen: dict[str, dict[str, str]] = {}
    missing: list[str] = []
    conflicts: list[str] = []
    invalid: list[str] = []
    human_audited: set[str] = set()
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
            authority = authorities.get(source_id)
            if authority:
                expected_hash = str(authority.get("source_file_sha256") or "").strip().lower()
                expected_view = str(authority.get("view_type") or "").strip()
                if not expected_hash or not expected_view or file_sha256(source) != expected_hash:
                    conflicts.append(f"{source_id}: human-audit authority mismatch for {source}")
                    candidates.pop(source_id, None)
                    continue
                human_audited.add(source_id)
            elif source_id not in verified:
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
        "human_audited_year_sources": len(human_audited),
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

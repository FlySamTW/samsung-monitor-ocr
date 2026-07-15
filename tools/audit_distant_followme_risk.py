#!/usr/bin/env python3
"""Audit false-distant-view risk for current-year distant records.

This is a read-only guard. It scans audit success records and reports photos
classified as distant view while the evidence still contains Samsung FollowMe
or strong single-unit clues. Those rows should be staged for rerun before upload.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

try:
    from tools.build_v1945_evidence_backfill import build_candidates
except ImportError:
    from build_v1945_evidence_backfill import build_candidates


DEFAULT_OUTPUT_DIR = Path("D:/00_\u5546\u5316/00_\u5df2OCR\u7167\u7247")
DISTANT_VIEW = "\u9060\u666f"
CURRENT_YEAR = datetime.now().year
FINALIZATION_PROOF_VERSION = "current-year-v1945-finalization-v1"

FOLLOWME_TERMS = (
    "followme",
    "follow me",
    "samsung follow",
    "samsung followme",
    "followme 4k",
    "followme pro",
    "s32fm",
    "s43fm",
)

PHYSICAL_TERMS = (
    "\u7acb\u5f0f\u87a2\u5e55",  # standing display
    "\u5c55\u793a\u87a2\u5e55",
    "\u76f4\u7acb\u87a2\u5e55",
    "\u7368\u7acb\u87a2\u5e55",
    "\u767d\u8272\u7acb\u67f1",
    "\u767d\u8272\u652f\u67b6",
    "\u5782\u76f4\u652f\u67b6",
    "\u76f4\u7acb\u652f\u67b6",
    "\u76f4\u687f",
    "\u5713\u5f62\u5e95\u5ea7",
    "\u767d\u8272\u5e95\u5ea7",
    "\u6258\u76e4",
    "\u79fb\u52d5\u5f0f",
)

FOLLOWME_DISPLAY_FIXTURE_TERMS = (
    "\u7acb\u5f0f\u87a2\u5e55",
    "\u5c55\u793a\u87a2\u5e55",
    "\u986f\u793a\u87a2\u5e55",
    "\u76f4\u7acb\u87a2\u5e55",
    "\u7368\u7acb\u87a2\u5e55",
    "\u5c55\u793a\u7528",
    "\u7acb\u5f0f\u5c55\u793a",
    "\u76f4\u7acb\u5c55\u793a",
    "\u79fb\u52d5\u5f0f",
)

FOLLOWME_DISPLAY_LABEL_TERMS = (
    "\u6a19\u7c64",
    "\u6a19\u724c",
    "\u724c\u9762",
    "\u7522\u54c1\u6a19\u793a",
    "\u4e0a\u65b9",
    "\u5074\u6a19",
    "\u65c1\u908a",
    "\u5beb\u8457",
    "\u986f\u793a",
)

SINGLE_UNIT_TERMS = (
    "\u5224\u65b7\u662f\u55ae\u6a5f",
    "\u9019\u5f35\u5df2\u5b8c\u6210\u8fa8\u8b58\uff1a\u55ae\u6a5f",
    "\u55ae\u4e00\u4e3b\u89d2",
    "\u4e3b\u89d2\u662f",
    "\u4e3b\u9ad4\u662f",
    "\u4e3b\u87a2\u5e55",
    "\u4e00\u53f0",
    "\u55ae\u53f0",
    "\u55ae\u6a5f",
)

SIDE_LABEL_TERMS = (
    "\u5074\u6a19",
    "\u5074\u908a\u6a19\u7c64",
    "\u5074\u908a\u898f\u683c",
    "\u5074\u908a\u578b\u865f",
    "\u87a2\u5e55\u5074\u6a19",
)

MODEL_CODE_RE = re.compile(r"\b(?:S|C|F|U|G|LS|LC|LU)[A-Z0-9]{5,}\b", re.IGNORECASE)
PRICE_RE = re.compile(r"(?:NT\$?|[$\uff04])?\s?\d{1,3}(?:,\d{3})+")

NEGATION_TERMS = (
    "\u6c92\u6709",
    "\u6c92\u770b\u5230",
    "\u672a\u770b\u5230",
    "\u770b\u4e0d\u5230",
    "\u4e0d\u662f",
    "\u4e26\u975e",
    "\u7121",
    "no ",
    "not ",
    "without",
)


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def finalization_input_paths(
    output_dir: Path,
    year: int,
    candidate_csv: Path,
    result_csv: Path,
    run_summary_csv: Path,
) -> list[Path]:
    audit_root = output_dir / "_ocr_audit"
    paths = [candidate_csv, candidate_csv.with_suffix(candidate_csv.suffix + ".summary.json"), result_csv, run_summary_csv]
    year_folders: set[Path] = set()
    for copied_path in sorted(audit_root.glob("*/copied.csv"), key=lambda item: str(item).casefold()):
        if any(str(row.get("period") or "").startswith(str(year)) for row in read_csv(copied_path)):
            year_folders.add(copied_path.parent.resolve())
    for folder in sorted(year_folders, key=lambda item: str(item).casefold()):
        for name in ("success_records.csv", "rename_plan.csv", "copied.csv"):
            path = folder / name
            if path.is_file():
                paths.append(path)
    paths.extend(sorted(audit_root.rglob("v1945_evidence_trace.jsonl"), key=lambda item: str(item).casefold()))
    unique = {str(path.resolve()).casefold(): path.resolve() for path in paths}
    return [unique[key] for key in sorted(unique)]


def finalization_input_sha256(
    output_dir: Path,
    year: int,
    candidate_csv: Path,
    result_csv: Path,
    run_summary_csv: Path,
) -> str:
    digest = hashlib.sha256()
    for path in finalization_input_paths(output_dir, year, candidate_csv, result_csv, run_summary_csv):
        digest.update(str(path).casefold().encode("utf-8"))
        digest.update(b"\0")
        if not path.is_file():
            digest.update(b"MISSING")
        else:
            digest.update(file_sha256(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _safe_int(value: object, default: int = -1) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def _source_identity(row: dict[str, str]) -> str:
    supplied = str(row.get("source_item_id") or row.get("source_identity") or "").strip().casefold()
    if re.fullmatch(r"[0-9a-f]{64}", supplied):
        return supplied
    source = str(row.get("source_path") or row.get("original_path") or "").strip()
    if not source:
        return ""
    resolved = str(Path(source).resolve()).casefold()
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def validate_canonical_inventory(output_dir: Path, year: int) -> dict[str, object]:
    """Prove every copied current-year source still has canonical rows and an output image."""
    audit_root = output_dir / "_ocr_audit"
    seen: dict[str, tuple[str, str]] = {}
    errors: list[str] = []
    duplicate_source_identities = 0
    valid_source_ids: set[str] = set()

    for copied_path in sorted(audit_root.glob("*/copied.csv"), key=lambda path: str(path).casefold()):
        copied_rows = [
            row for row in read_csv(copied_path)
            if str(row.get("period") or "").startswith(str(year))
        ]
        if not copied_rows:
            continue
        success_path = copied_path.parent / "success_records.csv"
        rename_path = copied_path.parent / "rename_plan.csv"
        success_rows = read_csv(success_path) if success_path.is_file() else []
        rename_rows = read_csv(rename_path) if rename_path.is_file() else []
        success_names = {
            str(row.get("file_name") or row.get("filename") or "").strip()
            for row in success_rows
        }
        rename_names = {
            str(row.get("original_name") or row.get("file_name") or "").strip()
            for row in rename_rows
        }

        for row in copied_rows:
            source_id = _source_identity(row)
            original_name = str(row.get("original_name") or Path(str(row.get("original_path") or "")).name).strip()
            target_name = str(row.get("target_name") or Path(str(row.get("target_path") or "")).name).strip()
            target_path = Path(str(row.get("target_path") or "")) if row.get("target_path") else output_dir / target_name
            identity_value = (str(copied_path.parent.resolve()).casefold(), original_name.casefold())
            prior = seen.get(source_id) if source_id else None
            if prior is not None:
                duplicate_source_identities += 1
                errors.append(f"duplicate_source_identity:{source_id}")
                continue
            if source_id:
                seen[source_id] = identity_value
            row_errors: list[str] = []
            if not source_id or not original_name or not target_name:
                row_errors.append("identity_missing")
            if original_name not in success_names:
                row_errors.append("success_record_missing")
            if original_name not in rename_names:
                row_errors.append("rename_plan_missing")
            if not target_path.is_file() or target_path.stat().st_size <= 0:
                row_errors.append("target_missing_or_empty")
            if row_errors:
                errors.append(f"{copied_path.parent.name}:{original_name}:{','.join(row_errors)}")
            elif source_id:
                valid_source_ids.add(source_id)

    return {
        "expected_source_count": len(seen),
        "valid_source_count": len(valid_source_ids),
        "duplicate_source_identities": duplicate_source_identities,
        "missing_or_invalid": errors,
    }


def _group_key(period: str, audit_folder: str, source_folder: str) -> tuple[str, str, str]:
    return (
        str(period or "").strip(),
        str(Path(audit_folder).resolve()).casefold() if audit_folder else "",
        str(Path(source_folder).resolve()).casefold() if source_folder else "",
    )


def validate_finalization_proof(
    output_dir: Path,
    year: int,
    candidate_csv: Path,
    result_csv: Path,
    run_summary_csv: Path,
) -> dict[str, object]:
    """Prove the complete current-year v19.45 run before any risk report can be fresh."""
    candidate_summary_path = candidate_csv.with_suffix(candidate_csv.suffix + ".summary.json")
    candidates = read_csv(candidate_csv) if candidate_csv.is_file() else []
    results = read_csv(result_csv) if result_csv.is_file() else []
    summaries = read_csv(run_summary_csv) if run_summary_csv.is_file() else []
    candidate_summary = _read_json(candidate_summary_path)
    required_inputs = [candidate_csv, candidate_summary_path]
    if candidates:
        required_inputs.extend([result_csv, run_summary_csv])
    missing_inputs = [str(path.resolve()) for path in required_inputs if not path.is_file()]

    candidate_ids = [
        _source_identity(row)
        for row in candidates
    ]
    result_ids = [
        _source_identity(row)
        for row in results
    ]
    duplicate_source_identities = len(candidate_ids) - len(set(candidate_ids)) if candidate_ids else 0
    result_source_set_matches = (
        not candidates and not results
    ) or (
        bool(candidate_ids)
        and all(candidate_ids)
        and all(result_ids)
        and set(result_ids) == set(candidate_ids)
        and len(result_ids) == len(candidate_ids)
    )

    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in candidates:
        source_path = str(row.get("source_path") or "").strip()
        key = _group_key(
            str(row.get("period") or ""),
            str(row.get("audit_folder") or ""),
            str(Path(source_path).parent) if source_path else "",
        )
        grouped.setdefault(key, []).append(row)

    summary_by_group: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in summaries:
        key = _group_key(
            str(row.get("period") or ""),
            str(row.get("audit_folder") or ""),
            str(row.get("folder") or ""),
        )
        summary_by_group.setdefault(key, []).append(row)

    missing_groups: list[str] = []
    invalid_groups: list[str] = []
    finalized_rows = 0
    finalized_group_count = 0
    finalized_periods: set[str] = set()
    scanned_success_count = 0
    for key, group_rows in grouped.items():
        period, audit_folder_text, _source_folder = key
        matches = summary_by_group.get(key, [])
        if len(matches) != 1:
            missing_groups.append(f"{period}:{audit_folder_text}:summary_count={len(matches)}")
            continue
        summary = matches[0]
        expected = len(group_rows)
        valid_counts = all(
            _safe_int(summary.get(field)) == expected
            for field in ("queued", "staged", "processed")
        )
        valid_summary = (
            valid_counts
            and _safe_int(summary.get("aborted"), 1) == 0
            and _safe_int(summary.get("failed_replacements"), 0) == 0
        )
        audit_folder = Path(audit_folder_text)
        canonical_rows: dict[str, list[dict[str, str]]] = {}
        for name in ("success_records.csv", "rename_plan.csv", "copied.csv"):
            path = audit_folder / name
            canonical_rows[name] = read_csv(path) if path.is_file() else []
        candidate_names = {str(row.get("file_name") or Path(str(row.get("source_path") or "")).name) for row in group_rows}
        success_names = {str(row.get("file_name") or row.get("filename") or "") for row in canonical_rows["success_records.csv"]}
        valid_canonical = (
            all(canonical_rows.values())
            and candidate_names.issubset(success_names)
        )
        if not valid_summary or not valid_canonical:
            invalid_groups.append(
                f"{period}:{audit_folder_text}:summary={valid_summary}:canonical={valid_canonical}"
            )
            continue
        finalized_rows += expected
        finalized_group_count += 1
        finalized_periods.add(period)
        scanned_success_count += len(canonical_rows["success_records.csv"])

    remaining_rows, backfill = build_candidates(output_dir / "_ocr_audit", str(year))
    backfill_errors = sum(
        _safe_int(backfill.get(field), 0)
        for field in ("missing_sources", "conflicting_sources", "invalid_rows")
    )
    expected_periods = sorted({key[0] for key in grouped if key[0]})
    inventory = validate_canonical_inventory(output_dir, year)
    expected_source_count = _safe_int(backfill.get("unique_year_sources"), 0)
    verified_source_count = max(0, expected_source_count - len(remaining_rows))
    candidate_summary_valid = (
        candidate_summary.get("executed") is True
        and _safe_int(candidate_summary.get("candidate_rows"), -1) == len(candidates)
        and _safe_int(candidate_summary.get("unique_year_sources"), -1) == expected_source_count
        and sum(
            _safe_int(candidate_summary.get(field), 0)
            for field in ("missing_sources", "conflicting_sources", "invalid_rows")
        ) == 0
        and str(Path(str(candidate_summary.get("output") or "")).resolve()).casefold()
        == str(candidate_csv.resolve()).casefold()
    )
    input_hash = finalization_input_sha256(output_dir, year, candidate_csv, result_csv, run_summary_csv)
    proof_valid = (
        not missing_inputs
        and candidate_summary_valid
        and expected_source_count > 0
        and duplicate_source_identities == 0
        and result_source_set_matches
        and len(summaries) == len(grouped)
        and not missing_groups
        and not invalid_groups
        and finalized_rows == len(candidates)
        and backfill_errors == 0
        and not remaining_rows
        and expected_source_count >= len(candidates)
        and _safe_int(inventory.get("expected_source_count"), -1) == expected_source_count
        and _safe_int(inventory.get("valid_source_count"), -1) == expected_source_count
        and _safe_int(inventory.get("duplicate_source_identities"), 1) == 0
        and not inventory.get("missing_or_invalid")
    )
    run_id_seed = "|".join(
        file_sha256(path) if path.is_file() else "missing"
        for path in (candidate_csv, result_csv, run_summary_csv)
    )
    proof_errors = missing_inputs + missing_groups + invalid_groups + list(inventory.get("missing_or_invalid") or [])
    if not candidate_summary_valid:
        proof_errors.append("candidate_builder_summary_invalid")
    if not result_source_set_matches:
        proof_errors.append("candidate_result_source_set_mismatch")
    if duplicate_source_identities:
        proof_errors.append("duplicate_candidate_source_identity")
    if backfill_errors:
        proof_errors.append(f"backfill_inventory_errors:{backfill_errors}")
    if remaining_rows:
        proof_errors.append(f"remaining_unverified_sources:{len(remaining_rows)}")
    if verified_source_count != expected_source_count:
        proof_errors.append(f"verified_source_count_mismatch:{verified_source_count}/{expected_source_count}")
    return {
        "proof_schema_version": FINALIZATION_PROOF_VERSION,
        "backfill_run_id": hashlib.sha256(run_id_seed.encode("ascii")).hexdigest()[:24],
        "backfill_run_summary_sha256": file_sha256(run_summary_csv) if run_summary_csv.is_file() else "",
        "candidate_csv": str(candidate_csv.resolve()),
        "candidate_summary_json": str(candidate_summary_path.resolve()),
        "result_csv": str(result_csv.resolve()),
        "run_summary_csv": str(run_summary_csv.resolve()),
        "expected_periods": expected_periods,
        "finalized_periods": sorted(finalized_periods),
        "expected_group_count": len(grouped),
        "finalized_group_count": finalized_group_count,
        "candidate_rows": len(candidates),
        "finalized_rows": finalized_rows,
        "expected_source_count": expected_source_count,
        "verified_source_count": verified_source_count,
        "expected_candidate_count": expected_source_count,
        "scanned_result_count": verified_source_count,
        "scanned_success_count": scanned_success_count,
        "remaining_unverified_sources": len(remaining_rows),
        "missing_audit_folders": missing_groups,
        "invalid_audit_folders": invalid_groups,
        "duplicate_source_identities": duplicate_source_identities,
        "duplicate_source_identity": duplicate_source_identities + _safe_int(inventory.get("duplicate_source_identities"), 0),
        "result_source_set_matches": result_source_set_matches,
        "missing_inputs": missing_inputs,
        "candidate_summary_valid": candidate_summary_valid,
        "canonical_inventory": inventory,
        "missing_or_invalid": list(dict.fromkeys(proof_errors)),
        "backfill_errors": backfill_errors,
        "audit_input_sha256": input_hash,
        "complete": proof_valid,
        "audit_complete": proof_valid,
    }


def write_csv(path: Path, rows: list[dict[str, str]], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def period_from_text(*parts: str) -> str:
    text = " ".join(part or "" for part in parts)
    for index in range(max(0, len(text) - 5)):
        candidate = text[index : index + 6]
        if candidate.startswith("20") and candidate.isdigit():
            return candidate
    return ""


def audit_period_from_folder(folder: Path) -> str:
    for part in folder.name.split("_"):
        if len(part) == 6 and part.startswith("20") and part.isdigit():
            return part
    return period_from_text(folder.name)


def text_has_unnegated(text: str, token: str) -> bool:
    lower = text.lower()
    token_lower = token.lower()
    start = 0
    while True:
        index = lower.find(token_lower, start)
        if index < 0:
            return False
        before = lower[max(0, index - 28) : index]
        if not any(negation in before for negation in NEGATION_TERMS):
            return True
        start = index + len(token_lower)


def hit_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    return [term for term in terms if text_has_unnegated(text, term)]


def classify_risk(evidence: str) -> tuple[str, list[str]]:
    followme_hits = hit_terms(evidence, FOLLOWME_TERMS)
    physical_hits = hit_terms(evidence, PHYSICAL_TERMS)
    display_fixture_hits = hit_terms(evidence, FOLLOWME_DISPLAY_FIXTURE_TERMS)
    display_label_hits = hit_terms(evidence, FOLLOWME_DISPLAY_LABEL_TERMS)
    single_hits = hit_terms(evidence, SINGLE_UNIT_TERMS)
    side_label_hits = hit_terms(evidence, SIDE_LABEL_TERMS)
    has_samsung = "samsung" in evidence.lower() or "\u4e09\u661f" in evidence
    has_model_code = bool(MODEL_CODE_RE.search(evidence))
    has_price = bool(PRICE_RE.search(evidence))

    if followme_hits and has_samsung and (display_fixture_hits or display_label_hits):
        return "critical_followme_display_fixture", followme_hits + display_fixture_hits + display_label_hits
    if followme_hits:
        return "critical_followme_text", followme_hits + physical_hits
    if has_samsung and physical_hits:
        return "high_samsung_physical_clue", physical_hits
    if side_label_hits and (has_model_code or has_samsung):
        return "high_side_label_model_clue", side_label_hits
    if single_hits and (has_model_code or has_price or side_label_hits):
        return "high_single_unit_conflict", single_hits + side_label_hits
    if physical_hits:
        return "medium_physical_clue", physical_hits
    return "", []


def classify_final_followme_conflict(record: dict[str, str], plan_row: dict[str, str], evidence: str) -> tuple[str, list[str]]:
    """Catch rows rescued to FollowMe whose narration still says distant/not FollowMe."""
    final_text = " ".join(
        str(value or "")
        for value in (
            record.get("model", ""),
            record.get("human_model", ""),
            plan_row.get("target_name", ""),
        )
    ).lower()
    if "followme" not in final_text and "follow me" not in final_text:
        return "", []

    thinking = str(record.get("thinking", "") or "")
    thinking_upper = thinking.upper().replace(" ", "")
    corrected_distant_negation = any(
        token in thinking
        for token in (
            "\u4e0d\u80fd\u56e0",
            "\u4e0d\u53ef\u56e0",
            "\u4e0d\u80fd\u5224\u70ba\u9060\u666f",
            "\u4e0d\u53ef\u5224\u9060\u666f",
            "\u4e0d\u662f\u9060\u666f",
            "\u4e0d\u5c6c\u65bc\u9060\u666f",
            "\u4e0d\u7b26\u5408\u9060\u666f",
            "\u6700\u7d42\u6821\u6b63",
        )
    )
    hits: list[str] = []
    if "整體符合「遠景」條件" in thinking or ("遠景" in thinking and not corrected_distant_negation):
        hits.append("final_followme_but_distant_narration")
    if any(token in thinking_upper for token in ("不是FOLLOWME", "非FOLLOWME", "沒有FOLLOWME", "無FOLLOWME")):
        hits.append("final_followme_but_negative_narration")
    if hits:
        return "critical_followme_result_conflict", hits
    return "", []


def stable_sample_key(*parts: str) -> str:
    text = "|".join(part or "" for part in parts)
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:12]


def is_distant(record: dict[str, str], plan_row: dict[str, str] | None) -> bool:
    fields = [
        record.get("view_type", ""),
        record.get("category", ""),
        record.get("human_category", ""),
        (plan_row or {}).get("category", ""),
        (plan_row or {}).get("target_name", ""),
    ]
    return any(DISTANT_VIEW in str(field or "") for field in fields)


def load_uploaded_names(manifest_dir: Path) -> set[str]:
    uploaded = read_csv(manifest_dir / "drive_upload_uploaded.csv")
    return {row.get("file_name", "") for row in uploaded if row.get("file_name")}


def build_output_row(
    period: str,
    folder: Path,
    record: dict[str, str],
    plan_row: dict[str, str],
    risk_level: str,
    hits: list[str],
    uploaded_names: set[str],
) -> dict[str, str]:
    target_name = plan_row.get("target_name", "")
    source_path = plan_row.get("original_path", "")
    uploaded = "yes" if target_name in uploaded_names else "no"
    return {
        "period": period,
        "audit_folder": str(folder),
        "source_folder": str(Path(source_path).parent) if source_path else "",
        "source_path": source_path,
        "file_name": record.get("file_name", ""),
        "target_name": target_name,
        "original_path": source_path,
        "target_path": plan_row.get("target_path", ""),
        "view_type": record.get("view_type", ""),
        "category": record.get("category", ""),
        "model": record.get("model", ""),
        "price": record.get("price", ""),
        "reason": risk_level,
        "risk_level": risk_level,
        "hit_terms": ";".join(hits),
        "uploaded": uploaded,
        "thinking_excerpt": (record.get("thinking", "") or "")[:500],
    }


def scan_audit(output_dir: Path, year: int, include_medium: bool) -> tuple[list[dict[str, str]], list[dict[str, str]], dict[str, object]]:
    audit_root = output_dir / "_ocr_audit"
    manifest_dir = output_dir / "_drive_upload"
    uploaded_names = load_uploaded_names(manifest_dir)
    rows: list[dict[str, str]] = []
    sample_rows: list[dict[str, str]] = []
    counters: Counter[str] = Counter()

    for folder in sorted(audit_root.glob("*")):
        if not folder.is_dir():
            continue
        period = audit_period_from_folder(folder)
        if not period.startswith(str(year)):
            continue
        success_records = read_csv(folder / "success_records.csv")
        if not success_records:
            continue
        plan_rows = {
            row.get("original_name", ""): row
            for row in read_csv(folder / "rename_plan.csv")
            if row.get("original_name")
        }
        for record in success_records:
            file_name = record.get("file_name", "")
            plan_row = plan_rows.get(file_name, {})
            evidence = " ".join(
                str(value or "")
                for value in (
                    file_name,
                    plan_row.get("target_name", ""),
                    record.get("model", ""),
                    record.get("thinking", ""),
                    record.get("human_notes", ""),
                )
            )
            final_conflict, final_hits = classify_final_followme_conflict(record, plan_row, evidence)
            if final_conflict:
                counters["final_followme_result_total"] += 1
                counters[final_conflict] += 1
                row = build_output_row(period, folder, record, plan_row, final_conflict, final_hits, uploaded_names)
                if row["uploaded"] == "yes":
                    counters[f"{final_conflict}_uploaded"] += 1
                rows.append(row)
                sample_rows.append(row)
                continue

            if not is_distant(record, plan_row):
                continue
            counters["distant_total"] += 1
            risk_level, hits = classify_risk(evidence)
            if not risk_level:
                counters["distant_no_followme_risk"] += 1
                sample_rows.append(
                    build_output_row(
                        period,
                        folder,
                        record,
                        plan_row,
                        "distant_no_followme_risk",
                        [],
                        uploaded_names,
                    )
                )
                continue
            if risk_level == "medium_physical_clue" and not include_medium:
                counters["distant_medium_ignored"] += 1
                continue
            row = build_output_row(period, folder, record, plan_row, risk_level, hits, uploaded_names)
            counters[risk_level] += 1
            if row["uploaded"] == "yes":
                counters[f"{risk_level}_uploaded"] += 1
            rows.append(row)
            sample_rows.append(row)

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "output_dir": str(output_dir),
        "year": year,
        "include_medium": include_medium,
        "risk_rows": len(rows),
        "sample_rows": len(sample_rows),
        "risk_rate": round(
            len(rows) / (counters["distant_total"] + counters["final_followme_result_total"]),
            4,
        ) if (counters["distant_total"] + counters["final_followme_result_total"]) else 0,
        "counts": dict(sorted(counters.items())),
    }
    return rows, sample_rows, summary


def write_sample_csv(path: Path, rows: list[dict[str, str]], sample_size: int) -> None:
    if sample_size <= 0:
        return
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row.get("risk_level", "") or "unknown", []).append(row)
    sample_rows: list[dict[str, str]] = []
    for bucket, bucket_rows in sorted(grouped.items()):
        ordered = sorted(
            bucket_rows,
            key=lambda row: stable_sample_key(
                row.get("file_name", ""),
                row.get("target_name", ""),
                row.get("risk_level", ""),
            ),
        )
        for row in ordered[:sample_size]:
            sampled = dict(row)
            sampled["sample_bucket"] = bucket
            sample_rows.append(sampled)
    if not sample_rows:
        return
    headers = [
        "sample_bucket",
        "period",
        "audit_folder",
        "source_folder",
        "source_path",
        "file_name",
        "target_name",
        "target_path",
        "view_type",
        "category",
        "model",
        "price",
        "reason",
        "risk_level",
        "hit_terms",
        "uploaded",
        "thinking_excerpt",
    ]
    write_csv(path, sample_rows, headers)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit distant-view records that still look like FollowMe.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--year", type=int, default=CURRENT_YEAR)
    parser.add_argument("--include-medium", action="store_true", help="Include physical-clue-only rows.")
    parser.add_argument("--output-csv", default="")
    parser.add_argument("--summary-json", default="")
    parser.add_argument("--sample-csv", default="")
    parser.add_argument("--sample-size", type=int, default=20, help="Deterministic sample rows per risk bucket.")
    parser.add_argument("--candidate-csv", default="", help="Authoritative v19.45 candidate set for completion proof.")
    parser.add_argument("--result-csv", default="", help="Runner-selected result set that must match candidates exactly.")
    parser.add_argument("--run-summary-csv", default="", help="Per-folder finalized runner summaries.")
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    audit_root = output_dir / "_ocr_audit"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_csv = Path(args.output_csv) if args.output_csv else audit_root / f"distant_followme_risk_{args.year}_{stamp}.csv"
    summary_json = Path(args.summary_json) if args.summary_json else audit_root / f"distant_followme_risk_{args.year}_{stamp}.json"
    sample_csv = Path(args.sample_csv) if args.sample_csv else audit_root / f"distant_followme_risk_{args.year}_{stamp}_sample.csv"

    candidate_csv = Path(args.candidate_csv) if args.candidate_csv else audit_root / f"v1945_evidence_backfill_{args.year}.csv"
    result_csv = Path(args.result_csv) if args.result_csv else audit_root / f"v1945_evidence_backfill_{args.year}_results.csv"
    run_summary_csv = Path(args.run_summary_csv) if args.run_summary_csv else audit_root / f"v1945_evidence_backfill_{args.year}_run_summary.csv"
    proof = validate_finalization_proof(output_dir, args.year, candidate_csv, result_csv, run_summary_csv)
    if proof.get("audit_complete") is not True:
        failed_summary = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "output_dir": str(output_dir),
            "year": args.year,
            "audit_complete": False,
            "finalization_proof": proof,
            "failure": "current_year_finalization_proof_incomplete",
        }
        summary_json.parent.mkdir(parents=True, exist_ok=True)
        summary_json.write_text(json.dumps(failed_summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(failed_summary, ensure_ascii=False, indent=2))
        return 2

    rows, sample_rows, summary = scan_audit(output_dir, args.year, args.include_medium)
    headers = [
        "period",
        "audit_folder",
        "source_folder",
        "source_path",
        "file_name",
        "target_name",
        "original_path",
        "target_path",
        "view_type",
        "category",
        "model",
        "price",
        "reason",
        "risk_level",
        "hit_terms",
        "uploaded",
        "thinking_excerpt",
    ]
    write_csv(output_csv, rows, headers)
    write_sample_csv(sample_csv, sample_rows, args.sample_size)
    summary["audit_complete"] = True
    summary["finalization_proof"] = proof
    summary["audit_input_sha256"] = proof["audit_input_sha256"]
    summary["backfill_run_id"] = proof["backfill_run_id"]
    summary["risk_output_sha256"] = file_sha256(output_csv)
    summary["output_csv"] = str(output_csv)
    summary["summary_json"] = str(summary_json)
    summary["sample_csv"] = str(sample_csv)
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

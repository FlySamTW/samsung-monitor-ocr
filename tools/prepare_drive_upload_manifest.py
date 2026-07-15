#!/usr/bin/env python3
"""Prepare safe Google Drive upload manifests for flat OCR output photos.

This script separates deliverable files from records that should be rerun or
reviewed before going to Drive. It also prepares an ASCII-only staging folder
for connector-based uploads and can skip files already recorded as uploaded.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from skills.audit_fields import EVIDENCE_GUARD_REVISION, is_followme_model

try:
    from tools.audit_distant_followme_risk import (
        FINALIZATION_PROOF_VERSION,
        file_sha256,
        finalization_input_sha256,
    )
except ImportError:
    from audit_distant_followme_risk import (
        FINALIZATION_PROOF_VERSION,
        file_sha256,
        finalization_input_sha256,
    )


IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
PERIOD_RE = re.compile(r"^M-(\d{6})-")
PERIOD_ANYWHERE_RE = re.compile(r"(?<!\d)(20\d{4})(?!\d)")
PRICE_TOKEN_RE = re.compile(
    r"-(?P<symbol>[\u2191\u2193\u2713\u2714\?\uff1f]?)[\uff04$](?P<price>\d+)-"
)
DISTANT_VIEW_TOKEN = "\u9060\u666f"
OTHER_BRAND_TOKEN = "\u5b83\u724c("  # 它牌(
REVIEW_NAME_TOKENS = [
    "\u505c\u7522",  # discontinued
    "\u7121\u578b\u865f",  # no model
    "\u578b\u865f\u672a\u8fa8\u8b58",  # model not recognized
    "\u7121\u50f9\u683c",  # no price
    "\u4e0d\u5408\u683c",  # rejected
    "\u7167\u7247\u4e0d\u6e05\u695a",  # unclear photo
    "\u7167\u4e0d\u6e05\u695a",  # unclear photo
    "\u6c92\u6709\u898f\u683c",  # no spec label
    "\u6c92\u6709\u50f9\u683c",  # no price label
    "\u6c92\u6709\u898f\u683c\u548c\u50f9\u683c\u724c",  # no spec and price label
    "\u9ed1\u5c4f",  # black screen
    "SXXTEST",  # prompt/test placeholder model
    "XXTEST",  # prompt/test placeholder model variant
    "FollowMe MX",  # prompt placeholder FollowMe name
]
COMPARE_SYMBOLS = {"\u2191", "\u2193", "\u2713", "\u2714"}
UNKNOWN_SYMBOLS = {"?", "\uff1f"}
CURRENT_YEAR = datetime.now().year


@dataclass
class ManifestRow:
    source_path: str
    file_name: str
    year: str
    period: str
    drive_folder: str
    size_bytes: int
    content_sha256: str
    status: str
    reasons: str


def infer_period(file_name: str) -> str:
    match = PERIOD_RE.match(file_name)
    return match.group(1) if match else ""


def infer_period_from_text(*values: object) -> str:
    match = PERIOD_ANYWHERE_RE.search(" ".join(str(value or "") for value in values))
    return match.group(1) if match else ""


def load_copied_target_index(output_root: Path) -> dict[tuple[str, str], set[str]]:
    """Map (period, original source filename) to published flat filenames."""
    index: dict[tuple[str, str], set[str]] = {}
    audit_root = output_root / "_ocr_audit"
    if not audit_root.is_dir():
        return index
    for copied_path in audit_root.glob("*/copied.csv"):
        try:
            with copied_path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    original = str(row.get("original_name") or "").strip()
                    target = str(row.get("target_name") or "").strip()
                    if not target and row.get("target_path"):
                        target = Path(str(row["target_path"])).name
                    period = infer_period(target) or infer_period_from_text(copied_path.parent.name)
                    if original and target and period:
                        index.setdefault((period, original), set()).add(target)
        except (OSError, UnicodeError, csv.Error):
            continue
    return index


def load_uploaded(uploaded_log: Path | None) -> tuple[set[str], set[tuple[str, str]]]:
    uploaded_names: set[str] = set()
    uploaded_pairs: set[tuple[str, str]] = set()
    if not uploaded_log or not uploaded_log.exists():
        return uploaded_names, uploaded_pairs

    with uploaded_log.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            file_name = (row.get("file_name") or "").strip()
            source_path = (row.get("source_path") or "").strip()
            if file_name:
                uploaded_names.add(file_name)
            if source_path and file_name:
                uploaded_pairs.add((str(Path(source_path).resolve()), file_name))
    return uploaded_names, uploaded_pairs


def load_current_year_risk_summary(output_root: Path, year: int) -> dict:
    path = output_root / "_ocr_audit" / f"distant_followme_risk_{year}_latest.json"
    try:
        item = json.loads(path.read_text(encoding="utf-8"))
        return item if isinstance(item, dict) else {}
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def _stable_source_identity(path: str | Path) -> str:
    return hashlib.sha256(str(Path(path).resolve()).casefold().encode("utf-8")).hexdigest()


def load_target_source_identities(output_root: Path) -> dict[str, set[str]]:
    identities: dict[str, set[str]] = {}
    audit_root = output_root / "_ocr_audit"
    for copied_path in audit_root.glob("*/copied.csv"):
        try:
            for row in read_csv_rows(copied_path):
                target_name = str(row.get("target_name") or "").strip()
                if not target_name and row.get("target_path"):
                    target_name = Path(str(row.get("target_path"))).name
                original = str(row.get("original_path") or "").strip()
                if target_name and original:
                    identities.setdefault(target_name, set()).add(_stable_source_identity(original))
        except (OSError, UnicodeError, csv.Error):
            continue
    return identities


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def load_visual_accepted_distant_names(
    output_root: Path,
    risk_summary: dict | None = None,
) -> set[str]:
    """Load explicitly approved current-year distant rows.

    Visual spot-check files are intentionally not used for upload approval. A
    spot-check estimates whether the OCR rules are improving; it does not prove
    every current-year distant-view file is safe to send to Drive.
    """
    audit_root = output_root / "_ocr_audit"
    accepted: set[str] = set()
    risk_summary = risk_summary or {}
    proof = risk_summary.get("finalization_proof") or {}
    if risk_summary.get("audit_complete") is not True or proof.get("audit_complete") is not True:
        return accepted
    expected_run_id = str(risk_summary.get("backfill_run_id") or proof.get("backfill_run_id") or "")
    expected_input_hash = str(risk_summary.get("audit_input_sha256") or proof.get("audit_input_sha256") or "")
    if not expected_run_id or not expected_input_hash:
        return accepted
    target_identities = load_target_source_identities(output_root)
    approval_files = [
        audit_root / "current_year_distant_upload_approval.csv",
        audit_root / "current_year_distant_upload_approval_latest.csv",
    ]
    approval_files.extend(sorted(audit_root.glob("current_year_distant_upload_approval_*.csv")))
    for path in approval_files:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                verdict = (
                    row.get("upload_approved")
                    or row.get("verified_status")
                    or row.get("visual_judgment")
                    or ""
                ).strip()
                if verdict not in {"true_distant", "approved", "yes", "1", "Y", "y"}:
                    continue
                target_name = (
                    row.get("target_name")
                    or row.get("file_name")
                    or row.get("current_target_name")
                    or ""
                ).strip()
                source_identity = str(row.get("source_identity") or "").strip().casefold()
                target_hash = str(row.get("target_content_sha256") or "").strip().casefold()
                approved_at = str(row.get("approved_at") or "").strip()
                row_run_id = str(row.get("backfill_run_id") or "").strip()
                row_input_hash = str(row.get("risk_audit_input_sha256") or "").strip().casefold()
                target_path = output_root / target_name
                if (
                    target_name
                    and source_identity
                    and target_hash
                    and approved_at
                    and row_run_id == expected_run_id
                    and row_input_hash == expected_input_hash.casefold()
                    and source_identity in target_identities.get(target_name, set())
                    and target_path.is_file()
                    and file_sha256(target_path).casefold() == target_hash
                ):
                    accepted.add(target_name)
    return accepted


def load_current_year_risk_names(output_root: Path, accepted_distant_names: set[str] | None = None) -> set[str]:
    """Load current-year FollowMe/distant quality risks that must not upload."""
    audit_root = output_root / "_ocr_audit"
    accepted_distant_names = accepted_distant_names or set()
    risk_names: set[str] = set()
    for path in audit_root.glob("distant_followme_risk_*_latest.csv"):
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                target_name = (row.get("target_name") or "").strip()
                if target_name and target_name not in accepted_distant_names:
                    risk_names.add(target_name)
    return risk_names


def load_audit_review_required_names(output_root: Path) -> set[str]:
    """Map unresolved OCR records back to any already-published flat filename."""
    audit_root = output_root / "_ocr_audit"
    blocked_names: set[str] = set()
    if not audit_root.is_dir():
        return blocked_names

    truthy = {"1", "true", "yes", "y"}
    for folder in audit_root.iterdir():
        if not folder.is_dir():
            continue
        copied_by_original: dict[str, str] = {}
        copied_path = folder / "copied.csv"
        if copied_path.is_file():
            with copied_path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    original = (row.get("original_name") or "").strip()
                    target = (row.get("target_name") or "").strip()
                    if not target and row.get("target_path"):
                        target = Path(row["target_path"]).name
                    if original and target:
                        copied_by_original[original] = target

        for blocked_path in folder.glob("blocked_after*.csv"):
            with blocked_path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    target = (row.get("target_name") or "").strip()
                    original = (row.get("original_name") or row.get("file_name") or "").strip()
                    if target:
                        blocked_names.add(target)
                    if original and original in copied_by_original:
                        blocked_names.add(copied_by_original[original])

        success_path = folder / "success_records.csv"
        if success_path.is_file():
            with success_path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    unresolved = any(
                        str(row.get(field) or "").strip().lower() in truthy
                        for field in ("auto_review_required", "model_validation_failed", "price_conflict_detected")
                    )
                    if not unresolved:
                        continue
                    original = (row.get("file_name") or "").strip()
                    if original in copied_by_original:
                        blocked_names.add(copied_by_original[original])
    return blocked_names

def load_complete_auto_verified_names(output_root: Path) -> set[str]:
    """Return published names whose category-specific v19.45 verification completed."""
    names: set[str] = set()
    truthy = {"1", "true", "yes", "y"}
    copied_index = load_copied_target_index(output_root)
    for path in (output_root / "_ocr_audit").glob("*/success_records.csv"):
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if str(row.get("auto_verified") or "").strip().lower() not in truthy:
                    continue
                if str(row.get("auto_review_required") or "").strip().lower() in truthy:
                    continue
                view = " ".join(str(row.get(key) or "") for key in ("view_type", "category"))
                model = str(row.get("model") or "")
                required_attempts = 3 if "遠景" in view or "DISTANT" in view.upper() else (2 if is_followme_model(model) else 1)
                try:
                    if int(str(row.get("ocr_attempt") or "0")) < required_attempts:
                        continue
                except ValueError:
                    continue
                evidence = " ".join(str(row.get(key) or "").strip() for key in ("thinking", "stream_buffer", "raw_response"))
                trace = str(row.get("run_id") or row.get("timestamp") or "").strip()
                strict_distant = True
                if "遠景" in view or "DISTANT" in view.upper():
                    upper = evidence.upper()
                    strict_distant = ("3" in evidence or "三" in evidence) and any(
                        token in upper for token in ("NO UNIQUE", "MULTIPLE", "無唯一", "無法鎖定", "無法確定")
                    )
                original = str(row.get("file_name") or "").strip()
                period = infer_period(original) or infer_period_from_text(
                    row.get("period"), row.get("original_source_path"), row.get("source_path"), path.parent.name
                )
                if period.startswith("2026") and str(row.get("evidence_guard_revision") or "").strip() != EVIDENCE_GUARD_REVISION:
                    continue
                if evidence and trace and strict_distant and original:
                    targets = copied_index.get((period, original), set())
                    if targets:
                        names.update(targets)
                    elif infer_period(original):
                        names.add(original)
    return names


def load_v1945_trace_names(output_root: Path) -> set[str]:
    names: set[str] = set()
    copied_index = load_copied_target_index(output_root)
    for path in (output_root / "_ocr_audit").rglob("v1945_evidence_trace.jsonl"):
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    item = json.loads(line)
                    decision = item.get("guard_decision") or {}
                    if (
                        item.get("trace_version") != "v19.45"
                        or item.get("evidence_guard_revision") != EVIDENCE_GUARD_REVISION
                        or decision.get("verified") is not True
                    ):
                        continue
                    original = str(item.get("file_name") or "").strip()
                    period = str(item.get("period") or "") or infer_period_from_text(
                        item.get("original_source_path"), item.get("source_path"), path.parent.name
                    )
                    targets = copied_index.get((period, original), set())
                    if targets:
                        names.update(targets)
                    elif infer_period(original):
                        names.add(original)
        except (OSError, ValueError, TypeError):
            continue
    return names


def current_year_risk_audit_is_fresh(output_root: Path, year: int) -> bool:
    """Require a content-bound full-year completion proof, never an mtime guess."""
    audit_root = output_root / "_ocr_audit"
    latest_risk = audit_root / f"distant_followme_risk_{year}_latest.csv"
    latest_summary = audit_root / f"distant_followme_risk_{year}_latest.json"
    if not latest_risk.is_file() or not latest_summary.is_file():
        return False
    try:
        summary = json.loads(latest_summary.read_text(encoding="utf-8"))
        proof = summary.get("finalization_proof") or {}
        if (
            summary.get("audit_complete") is not True
            or proof.get("audit_complete") is not True
            or proof.get("complete") is not True
        ):
            return False
        if proof.get("proof_schema_version") != FINALIZATION_PROOF_VERSION:
            return False
        expected_count = _safe_nonnegative_int(proof.get("expected_candidate_count"))
        scanned_count = _safe_nonnegative_int(proof.get("scanned_result_count"))
        if (
            expected_count <= 0
            or scanned_count != expected_count
            or bool(proof.get("missing_or_invalid"))
            or _safe_nonnegative_int(proof.get("duplicate_source_identity")) != 0
        ):
            return False
        candidate_csv = Path(str(proof.get("candidate_csv") or ""))
        result_csv = Path(str(proof.get("result_csv") or ""))
        run_summary_csv = Path(str(proof.get("run_summary_csv") or ""))
        current_hash = finalization_input_sha256(
            output_root, year, candidate_csv, result_csv, run_summary_csv
        )
        return (
            bool(current_hash)
            and current_hash == str(proof.get("audit_input_sha256") or "")
            and current_hash == str(summary.get("audit_input_sha256") or "")
            and file_sha256(latest_risk) == str(summary.get("risk_output_sha256") or "")
        )
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return False


def _safe_nonnegative_int(value: object) -> int:
    try:
        parsed = int(float(str(value)))
        return parsed if parsed >= 0 else 0
    except (TypeError, ValueError):
        return 0


def normalize_current_year_finalization_proof(risk_summary: dict) -> dict[str, object]:
    proof = risk_summary.get("finalization_proof") or {}
    if not isinstance(proof, dict):
        proof = {}
    missing = proof.get("missing_or_invalid") or []
    if not isinstance(missing, (list, tuple)):
        missing = [str(missing)] if missing else []
    return {
        "complete": proof.get("complete") is True and proof.get("audit_complete") is True,
        "proof_schema_version": str(proof.get("proof_schema_version") or ""),
        "expected_candidate_count": _safe_nonnegative_int(proof.get("expected_candidate_count")),
        "scanned_result_count": _safe_nonnegative_int(proof.get("scanned_result_count")),
        "missing_or_invalid": [str(item) for item in missing if str(item).strip()],
        "duplicate_source_identity": _safe_nonnegative_int(proof.get("duplicate_source_identity")),
        "audit_input_sha256": str(proof.get("audit_input_sha256") or ""),
        "backfill_run_id": str(proof.get("backfill_run_id") or ""),
        "candidate_rows": _safe_nonnegative_int(proof.get("candidate_rows")),
        "finalized_rows": _safe_nonnegative_int(proof.get("finalized_rows")),
    }


def current_audit_input_sha256(output_root: Path, year: int, risk_summary: dict) -> str:
    proof = risk_summary.get("finalization_proof") or {}
    if not isinstance(proof, dict):
        return ""
    try:
        return finalization_input_sha256(
            output_root,
            year,
            Path(str(proof.get("candidate_csv") or "")),
            Path(str(proof.get("result_csv") or "")),
            Path(str(proof.get("run_summary_csv") or "")),
        )
    except (OSError, UnicodeError, ValueError, TypeError):
        return ""


def classify_file(
    path: Path,
    output_root: Path,
    max_bytes: int,
    risk_names: set[str] | None = None,
    accepted_distant_names: set[str] | None = None,
    current_year_risk_fresh: bool = True,
    audit_review_names: set[str] | None = None,
    auto_verified_names: set[str] | None = None,
    v1945_trace_names: set[str] | None = None,
) -> ManifestRow:
    file_name = path.name
    period = infer_period(file_name)
    year = period[:4] if period else ""
    reasons: list[str] = []
    risk_names = risk_names or set()
    accepted_distant_names = accepted_distant_names or set()
    audit_review_names = audit_review_names or set()
    auto_verified_names = auto_verified_names or set()
    v1945_trace_names = v1945_trace_names or set()

    if not period:
        reasons.append("missing_period")

    if path.suffix.lower() not in IMAGE_EXTS:
        reasons.append("not_image")

    size_bytes = path.stat().st_size
    if size_bytes <= 0:
        reasons.append("empty_file")
    if size_bytes > max_bytes:
        reasons.append("oversize")

    is_distant_view = f"-{DISTANT_VIEW_TOKEN}-" in file_name
    is_other_brand = OTHER_BRAND_TOKEN in file_name
    for token in REVIEW_NAME_TOKENS:
        if token in file_name:
            reasons.append(f"name_contains_{token}")

    if "\uff1f" in file_name or "?" in file_name:
        reasons.append("unknown_marker")

    if file_name in risk_names:
        reasons.append("current_year_followme_or_distant_risk_needs_rerun")
    if file_name in audit_review_names:
        reasons.append("ocr_auto_review_required")

    price_match = PRICE_TOKEN_RE.search(file_name)
    if period:
        period_year = int(year)
        symbol = price_match.group("symbol") if price_match else ""
        # Scope freshness to rows whose safety depends on the risk audit. A
        # stale current-year report must not globally downgrade unrelated rows.
        risk_sensitive = (
            is_distant_view
            or "FollowMe" in file_name
            or file_name in risk_names
            or file_name in auto_verified_names
        )
        if period_year == CURRENT_YEAR and not current_year_risk_fresh:
            reasons.append("current_year_finalization_proof_missing_or_stale")
        if period_year == CURRENT_YEAR and not current_year_risk_fresh and risk_sensitive:
            reasons.append("current_year_risk_audit_missing_or_stale")
        if period_year >= 2026 and file_name not in auto_verified_names:
            reasons.append("auto_verified_evidence_missing")
        if period_year >= 2026 and file_name not in v1945_trace_names:
            reasons.append("v1945_evidence_trace_missing")
        if period_year >= 2026:
            if is_distant_view:
                if file_name not in accepted_distant_names:
                    reasons.append("current_year_distant_view_needs_rerun")
            elif not price_match:
                reasons.append("current_year_missing_price")
            elif (not is_other_brand) and symbol not in COMPARE_SYMBOLS:
                reasons.append("current_year_missing_compare_symbol")
        if period_year < 2026 and price_match and symbol in (COMPARE_SYMBOLS | UNKNOWN_SYMBOLS):
            reasons.append("historical_has_compare_symbol")

    try:
        rel_parts = path.relative_to(output_root).parts
        if any(part.startswith("_") for part in rel_parts):
            reasons.append("internal_folder")
    except ValueError:
        pass

    status = "ready" if not reasons else "review"
    drive_folder = year if year else "_needs_review"
    return ManifestRow(
        source_path=str(path.resolve()),
        file_name=file_name,
        year=year,
        period=period,
        drive_folder=drive_folder,
        size_bytes=size_bytes,
        content_sha256="",
        status=status,
        reasons=";".join(reasons),
    )


def write_csv(path: Path, rows: list[ManifestRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(ManifestRow.__dataclass_fields__.keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def write_stale_uploaded_review_csv(path: Path, rows: list[ManifestRow], uploaded_names: set[str]) -> int:
    """List files already uploaded earlier but now blocked by stricter review gates."""
    stale_rows: list[ManifestRow] = []
    for row in rows:
        try:
            is_current_year = bool(row.year) and int(row.year) >= 2026
        except ValueError:
            is_current_year = False
        if row.file_name in uploaded_names and is_current_year:
            stale_rows.append(row)
    fieldnames = [
        "year",
        "drive_folder",
        "file_name",
        "source_path",
        "reasons",
        "status",
        "remote_path",
        "action",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in stale_rows:
            writer.writerow(
                {
                    "year": row.year,
                    "drive_folder": row.drive_folder,
                    "file_name": row.file_name,
                    "source_path": row.source_path,
                    "reasons": row.reasons,
                    "status": "uploaded_but_now_review_required",
                    "remote_path": f"{row.drive_folder}/{row.file_name}",
                    "action": "remove_remote_or_replace_after_rerun",
                }
            )
    return len(stale_rows)


def newest_first_key(row: ManifestRow) -> tuple[int, str]:
    try:
        period_rank = int(row.period)
    except (TypeError, ValueError):
        period_rank = -1
    return (-period_rank, row.file_name.casefold())


def stage_upload_batch(rows: list[ManifestRow], stage_dir: Path) -> Path:
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True, exist_ok=True)

    map_path = stage_dir.parent / "staging_map.csv"
    with map_path.open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = [
            "index", "stage_file", "source_path", "file_name", "year", "drive_folder", "content_sha256"
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for index, row in enumerate(rows, start=1):
            year_dir = stage_dir / row.drive_folder
            year_dir.mkdir(parents=True, exist_ok=True)
            stage_path = year_dir / row.file_name
            shutil.copy2(row.source_path, stage_path)
            writer.writerow(
                {
                    "index": index,
                    "stage_file": str(stage_path.resolve()),
                    "source_path": row.source_path,
                    "file_name": row.file_name,
                    "year": row.year,
                    "drive_folder": row.drive_folder,
                    "content_sha256": row.content_sha256,
                }
            )
    return map_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare Google Drive upload manifests for OCR output photos.")
    parser.add_argument("--output-dir", default=r"D:\00_商化\00_已OCR照片", help="Flat OCR output folder.")
    parser.add_argument("--manifest-dir", default=None, help="Where to write CSV/JSON manifests.")
    parser.add_argument("--uploaded-log", default=None, help="CSV of already uploaded files to skip.")
    parser.add_argument("--max-bytes", type=int, default=2_500_000, help="Mark larger files for review.")
    parser.add_argument("--limit-ready", type=int, default=0, help="Optional pending ready-row limit for upload.")
    parser.add_argument("--years", default="", help="Optional comma-separated upload scope, for example 2026 or 2025,2024.")
    parser.add_argument("--no-stage", action="store_true", help="Do not copy the next batch into staging.")
    args = parser.parse_args()

    output_root = Path(args.output_dir).resolve()
    if not output_root.exists():
        raise SystemExit(f"Output folder not found: {output_root}")

    manifest_dir = Path(args.manifest_dir).resolve() if args.manifest_dir else output_root / "_drive_upload"
    default_uploaded_log = manifest_dir / "drive_upload_uploaded.csv"
    uploaded_log = Path(args.uploaded_log).resolve() if args.uploaded_log else default_uploaded_log
    uploaded_names, uploaded_pairs = load_uploaded(uploaded_log)
    risk_summary = load_current_year_risk_summary(output_root, CURRENT_YEAR)
    current_year_risk_fresh = current_year_risk_audit_is_fresh(output_root, CURRENT_YEAR)
    current_input_hash = current_audit_input_sha256(output_root, CURRENT_YEAR, risk_summary)
    finalization_proof = normalize_current_year_finalization_proof(risk_summary)
    accepted_distant_names = load_visual_accepted_distant_names(
        output_root,
        risk_summary if current_year_risk_fresh else None,
    )
    risk_names = load_current_year_risk_names(output_root, accepted_distant_names)
    audit_review_names = load_audit_review_required_names(output_root)
    auto_verified_names = load_complete_auto_verified_names(output_root)
    v1945_trace_names = load_v1945_trace_names(output_root)

    all_rows: list[ManifestRow] = []
    for path in sorted(output_root.rglob("*")):
        if not path.is_file():
            continue
        try:
            rel_parts = path.relative_to(output_root).parts
        except ValueError:
            rel_parts = path.parts
        if any(part.startswith("_") for part in rel_parts):
            continue
        if path.suffix.lower() not in IMAGE_EXTS:
            continue
        all_rows.append(
            classify_file(
                path,
                output_root,
                args.max_bytes,
                risk_names,
                accepted_distant_names,
                current_year_risk_fresh,
                audit_review_names,
                auto_verified_names,
                v1945_trace_names,
            )
        )

    all_rows.sort(key=newest_first_key)
    ready_rows = sorted((row for row in all_rows if row.status == "ready"), key=newest_first_key)
    review_rows = sorted((row for row in all_rows if row.status != "ready"), key=newest_first_key)
    pending_rows_all = [
        row
        for row in ready_rows
        if (row.source_path, row.file_name) not in uploaded_pairs
    ]
    scoped_years = {item.strip() for item in args.years.split(",") if item.strip()}
    pending_rows = [row for row in pending_rows_all if not scoped_years or row.year in scoped_years]
    upload_rows = pending_rows[: args.limit_ready] if args.limit_ready else pending_rows
    for row in upload_rows:
        row.content_sha256 = file_sha256(Path(row.source_path))

    write_csv(manifest_dir / "drive_upload_all.csv", all_rows)
    write_csv(manifest_dir / "drive_upload_ready.csv", ready_rows)
    write_csv(manifest_dir / "drive_upload_ready_pending.csv", pending_rows)
    write_csv(manifest_dir / "drive_upload_review_required.csv", review_rows)
    next_batch_path = manifest_dir / "drive_upload_next_batch.csv"
    write_csv(next_batch_path, upload_rows)
    next_batch_sha256 = file_sha256(next_batch_path)
    stale_uploaded_review = write_stale_uploaded_review_csv(
        manifest_dir / "drive_upload_stale_uploaded_review_required.csv",
        review_rows,
        uploaded_names,
    )

    staging_map = ""
    if upload_rows and not args.no_stage:
        staging_map = str(stage_upload_batch(upload_rows, manifest_dir / "staging").resolve())

    by_year: dict[str, int] = {}
    pending_by_year: dict[str, int] = {}
    for row in ready_rows:
        by_year[row.drive_folder] = by_year.get(row.drive_folder, 0) + 1
    for row in pending_rows:
        pending_by_year[row.drive_folder] = pending_by_year.get(row.drive_folder, 0) + 1

    expected_count = int(finalization_proof.get("expected_candidate_count") or 0)
    scanned_count = int(finalization_proof.get("scanned_result_count") or 0)
    proof_input_hash = str(finalization_proof.get("audit_input_sha256") or "")
    upload_gate_fail_reasons: list[str] = []
    if (output_root / "_ocr_audit" / "runtime_health_fuse.json").exists():
        upload_gate_fail_reasons.append("runtime_health_fuse_active")
    if not current_year_risk_fresh:
        upload_gate_fail_reasons.append("current_year_risk_audit_missing_or_stale")
    if finalization_proof.get("complete") is not True:
        upload_gate_fail_reasons.append("current_year_finalization_incomplete")
    if expected_count <= 0 or scanned_count != expected_count:
        upload_gate_fail_reasons.append("current_year_source_count_mismatch")
    if finalization_proof.get("missing_or_invalid"):
        upload_gate_fail_reasons.append("current_year_missing_or_invalid_sources")
    if int(finalization_proof.get("duplicate_source_identity") or 0) != 0:
        upload_gate_fail_reasons.append("current_year_duplicate_source_identity")
    if not re.fullmatch(r"[0-9a-f]{64}", proof_input_hash) or current_input_hash != proof_input_hash:
        upload_gate_fail_reasons.append("current_year_audit_input_hash_mismatch")
    current_year_upload_gate_open = not upload_gate_fail_reasons

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "output_dir": str(output_root),
        "manifest_dir": str(manifest_dir),
        "uploaded_log": str(uploaded_log) if uploaded_log.exists() else "",
        "total_images": len(all_rows),
        "ready": len(ready_rows),
        "uploaded_skipped": len(ready_rows) - len(pending_rows_all),
        "out_of_scope_ready": len(pending_rows_all) - len(pending_rows),
        "ready_pending": len(pending_rows),
        "ready_pending_all_years": len(pending_rows_all),
        "upload_scope_years": sorted(scoped_years),
        "review_required": len(review_rows),
        "stale_uploaded_review_required": stale_uploaded_review,
        "next_batch": len(upload_rows),
        "next_batch_sha256": next_batch_sha256,
        "staging_map": staging_map,
        "max_bytes": args.max_bytes,
        "visual_accepted_distant": len(accepted_distant_names),
        "current_year_risk_audit_fresh": current_year_risk_fresh,
        "current_year_generation_complete": current_year_upload_gate_open,
        "current_year_upload_gate_open": current_year_upload_gate_open,
        "current_year_finalization_proof": finalization_proof,
        "current_audit_input_sha256": current_input_hash,
        "current_year_upload_gate_fail_reasons": list(dict.fromkeys(upload_gate_fail_reasons)),
        "ocr_auto_review_required": len(audit_review_names),
        "ready_by_year": dict(sorted(by_year.items(), reverse=True)),
        "pending_by_year": dict(sorted(pending_by_year.items(), reverse=True)),
    }
    (manifest_dir / "drive_upload_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

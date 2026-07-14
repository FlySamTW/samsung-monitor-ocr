#!/usr/bin/env python3
"""Prepare safe Google Drive upload manifests for flat OCR output photos.

This script separates deliverable files from records that should be rerun or
reviewed before going to Drive. It also prepares an ASCII-only staging folder
for connector-based uploads and can skip files already recorded as uploaded.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


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


def load_visual_accepted_distant_names(output_root: Path) -> set[str]:
    """Load explicitly approved current-year distant rows.

    Visual spot-check files are intentionally not used for upload approval. A
    spot-check estimates whether the OCR rules are improving; it does not prove
    every current-year distant-view file is safe to send to Drive.
    """
    audit_root = output_root / "_ocr_audit"
    accepted: set[str] = set()
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
                if target_name:
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
                model = str(row.get("model") or "").upper().replace(" ", "")
                required_attempts = 3 if "遠景" in view or "DISTANT" in view.upper() else (2 if "FOLLOWME" in model else 1)
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
                    if item.get("trace_version") != "v19.45" or decision.get("verified") is not True:
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
    """Fail closed when the current-year risk report is missing or stale."""
    audit_root = output_root / "_ocr_audit"
    latest_risk = audit_root / f"distant_followme_risk_{year}_latest.csv"
    if not latest_risk.is_file():
        return False

    newest_record_mtime = 0.0
    for folder in audit_root.glob(f"*{year}*"):
        if not folder.is_dir():
            continue
        for name in ("success_records.csv", "rename_plan.csv"):
            path = folder / name
            if path.is_file():
                newest_record_mtime = max(newest_record_mtime, path.stat().st_mtime)
    return latest_risk.stat().st_mtime >= newest_record_mtime


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
        fieldnames = ["index", "stage_file", "source_path", "file_name", "year", "drive_folder"]
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
    parser.add_argument("--no-stage", action="store_true", help="Do not copy the next batch into staging.")
    args = parser.parse_args()

    output_root = Path(args.output_dir).resolve()
    if not output_root.exists():
        raise SystemExit(f"Output folder not found: {output_root}")

    manifest_dir = Path(args.manifest_dir).resolve() if args.manifest_dir else output_root / "_drive_upload"
    default_uploaded_log = manifest_dir / "drive_upload_uploaded.csv"
    uploaded_log = Path(args.uploaded_log).resolve() if args.uploaded_log else default_uploaded_log
    uploaded_names, uploaded_pairs = load_uploaded(uploaded_log)
    accepted_distant_names = load_visual_accepted_distant_names(output_root)
    risk_names = load_current_year_risk_names(output_root, accepted_distant_names)
    current_year_risk_fresh = current_year_risk_audit_is_fresh(output_root, CURRENT_YEAR)
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
    pending_rows = [
        row
        for row in ready_rows
        if (row.source_path, row.file_name) not in uploaded_pairs
    ]
    upload_rows = pending_rows[: args.limit_ready] if args.limit_ready else pending_rows

    write_csv(manifest_dir / "drive_upload_all.csv", all_rows)
    write_csv(manifest_dir / "drive_upload_ready.csv", ready_rows)
    write_csv(manifest_dir / "drive_upload_ready_pending.csv", pending_rows)
    write_csv(manifest_dir / "drive_upload_review_required.csv", review_rows)
    write_csv(manifest_dir / "drive_upload_next_batch.csv", upload_rows)
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

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "output_dir": str(output_root),
        "manifest_dir": str(manifest_dir),
        "uploaded_log": str(uploaded_log) if uploaded_log.exists() else "",
        "total_images": len(all_rows),
        "ready": len(ready_rows),
        "uploaded_skipped": len(ready_rows) - len(pending_rows),
        "ready_pending": len(pending_rows),
        "review_required": len(review_rows),
        "stale_uploaded_review_required": stale_uploaded_review,
        "next_batch": len(upload_rows),
        "staging_map": staging_map,
        "max_bytes": args.max_bytes,
        "visual_accepted_distant": len(accepted_distant_names),
        "current_year_risk_audit_fresh": current_year_risk_fresh,
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

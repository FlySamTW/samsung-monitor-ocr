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


DEFAULT_OUTPUT_DIR = Path("D:/00_\u5546\u5316/00_\u5df2OCR\u7167\u7247")
DISTANT_VIEW = "\u9060\u666f"
CURRENT_YEAR = datetime.now().year

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
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    audit_root = output_dir / "_ocr_audit"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_csv = Path(args.output_csv) if args.output_csv else audit_root / f"distant_followme_risk_{args.year}_{stamp}.csv"
    summary_json = Path(args.summary_json) if args.summary_json else audit_root / f"distant_followme_risk_{args.year}_{stamp}.json"
    sample_csv = Path(args.sample_csv) if args.sample_csv else audit_root / f"distant_followme_risk_{args.year}_{stamp}_sample.csv"

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
    summary["output_csv"] = str(output_csv)
    summary["summary_json"] = str(summary_json)
    summary["sample_csv"] = str(sample_csv)
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

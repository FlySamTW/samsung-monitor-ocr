#!/usr/bin/env python3
"""Audit false-distant-view risk for Samsung FollowMe records.

This is a read-only guard. It scans audit success records and reports photos
classified as distant view while the evidence still contains Samsung FollowMe
or FollowMe physical clues. Those rows should be staged for rerun before upload.
"""

from __future__ import annotations

import argparse
import csv
import json
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
    has_samsung = "samsung" in evidence.lower() or "\u4e09\u661f" in evidence

    if followme_hits:
        return "critical_followme_text", followme_hits + physical_hits
    if has_samsung and physical_hits:
        return "high_samsung_physical_clue", physical_hits
    if physical_hits:
        return "medium_physical_clue", physical_hits
    return "", []


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


def scan_audit(output_dir: Path, year: int, include_medium: bool) -> tuple[list[dict[str, str]], dict[str, object]]:
    audit_root = output_dir / "_ocr_audit"
    manifest_dir = output_dir / "_drive_upload"
    uploaded_names = load_uploaded_names(manifest_dir)
    rows: list[dict[str, str]] = []
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
            if not is_distant(record, plan_row):
                continue
            counters["distant_total"] += 1
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
            risk_level, hits = classify_risk(evidence)
            if not risk_level:
                counters["distant_no_followme_risk"] += 1
                continue
            if risk_level == "medium_physical_clue" and not include_medium:
                counters["distant_medium_ignored"] += 1
                continue
            target_name = plan_row.get("target_name", "")
            uploaded = "yes" if target_name in uploaded_names else "no"
            counters[risk_level] += 1
            if uploaded == "yes":
                counters[f"{risk_level}_uploaded"] += 1
            rows.append(
                {
                    "period": period,
                    "audit_folder": str(folder),
                    "file_name": file_name,
                    "target_name": target_name,
                    "original_path": plan_row.get("original_path", ""),
                    "target_path": plan_row.get("target_path", ""),
                    "view_type": record.get("view_type", ""),
                    "category": record.get("category", ""),
                    "model": record.get("model", ""),
                    "price": record.get("price", ""),
                    "risk_level": risk_level,
                    "hit_terms": ";".join(hits),
                    "uploaded": uploaded,
                    "thinking_excerpt": (record.get("thinking", "") or "")[:500],
                }
            )

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "output_dir": str(output_dir),
        "year": year,
        "include_medium": include_medium,
        "risk_rows": len(rows),
        "counts": dict(sorted(counters.items())),
    }
    return rows, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit distant-view records that still look like FollowMe.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--year", type=int, default=CURRENT_YEAR)
    parser.add_argument("--include-medium", action="store_true", help="Include physical-clue-only rows.")
    parser.add_argument("--output-csv", default="")
    parser.add_argument("--summary-json", default="")
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    audit_root = output_dir / "_ocr_audit"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_csv = Path(args.output_csv) if args.output_csv else audit_root / f"distant_followme_risk_{args.year}_{stamp}.csv"
    summary_json = Path(args.summary_json) if args.summary_json else audit_root / f"distant_followme_risk_{args.year}_{stamp}.json"

    rows, summary = scan_audit(output_dir, args.year, args.include_medium)
    headers = [
        "period",
        "audit_folder",
        "file_name",
        "target_name",
        "original_path",
        "target_path",
        "view_type",
        "category",
        "model",
        "price",
        "risk_level",
        "hit_terms",
        "uploaded",
        "thinking_excerpt",
    ]
    write_csv(output_csv, rows, headers)
    summary["output_csv"] = str(output_csv)
    summary["summary_json"] = str(summary_json)
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

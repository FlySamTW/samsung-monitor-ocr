#!/usr/bin/env python3
"""Classify unknown price records for targeted handling."""
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

AUDIT_DIR = Path(r"D:\00_商化\00_已OCR照片\_ocr_audit")
OUTPUT_DIR = Path(r"D:\00_商化\samsung-monitor-ocr\tools\classified_unknowns")

DISTANT_VIEW_KEYWORDS = [
    "遠景", "多台", "展示區", "展示牆", "貨架", "海報", "廣告",
    "整排", "一排", "一整排", "牆上", "多支", "多螢幕", "陳列架",
    "非三星", "其他品牌", "多品牌",
]

MODEL_PATTERNS = [
    r"S\d{2}[A-Z]\d{3,4}[A-Z]{0,3}",
    r"FollowMe\s*(?:Pro\s*)?M[57]\s*\d+\s*吋?",
    r"Odyssey\s+[A-Z]\d+",
]


def read_all_unknowns():
    """Read all unknown records from 2026 audit folders."""
    all_unknown = []
    for folder in sorted(AUDIT_DIR.glob("*_2026*")):
        success_csv = folder / "success_records.csv"
        if not success_csv.exists():
            continue
        with success_csv.open("r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                price_status = row.get("price_status", "")
                price = row.get("price", "")
                if price_status == "unknown" and price:
                    row["_audit_folder"] = folder.name
                    all_unknown.append(row)
    return all_unknown


def extract_model_from_thinking(thinking):
    """Try to extract a model number from thinking text."""
    if not thinking:
        return None
    for pattern in MODEL_PATTERNS:
        match = re.search(pattern, thinking, re.IGNORECASE)
        if match:
            return match.group(0)
    return None


def has_distant_view_keywords(thinking):
    """Check if thinking contains distant-view indicators."""
    if not thinking:
        return False
    return any(kw in thinking for kw in DISTANT_VIEW_KEYWORDS)


def classify_record(record):
    """Classify a record into handling category."""
    model = (record.get("model") or "").strip()
    thinking = record.get("thinking") or ""

    if model and model.lower() not in ("null", "none", ""):
        if "FOLLOW" in model.upper():
            return "followme_pchome"
        elif model[0] == "S" and len(model) >= 6:
            return "samsung_pchome"
        else:
            return "invalid_model_rerun"

    if has_distant_view_keywords(thinking):
        return "distant_view_reclassify"

    extracted_model = extract_model_from_thinking(thinking)
    if extracted_model:
        return "thinking_has_model_rerun"

    return "manual_review"


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Reading all unknown records...")
    records = read_all_unknowns()
    print(f"Found {len(records)} unknown records")

    categories = defaultdict(list)
    for r in records:
        cat = classify_record(r)
        categories[cat].append(r)

    print("\n=== Classification Summary ===")
    for cat, items in sorted(categories.items(), key=lambda x: -len(x[1])):
        print(f"  {cat}: {len(items)}")

    headers = ["file_name", "model", "price", "view_type", "thinking_excerpt", "_audit_folder"]

    for cat, items in categories.items():
        output_file = OUTPUT_DIR / f"{cat}.csv"
        with output_file.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            for item in items:
                row = dict(item)
                row["thinking_excerpt"] = (row.get("thinking") or "")[:100]
                writer.writerow(row)
        print(f"Written {len(items)} records to {output_file.name}")

    print("\n=== Next Steps ===")
    print("1. followme_pchome + samsung_pchome: Will be resolved by improved PChome fallback")
    print("2. distant_view_reclassify: Run repair script to reclassify as 遠景")
    print("3. thinking_has_model_rerun: Targeted OCR rerun with thinking rescue")
    print("4. invalid_model_rerun: Targeted OCR rerun")
    print("5. manual_review: Output to price_review_required.csv for human review")


if __name__ == "__main__":
    main()

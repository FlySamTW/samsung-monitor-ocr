#!/usr/bin/env python3
"""Test repair logic for 202605 folder."""
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parents[1]))

from tools.repair_current_year_price_compare_outputs import (
    enrich_record, read_dict_csv, selected_value
)

def main():
    success_csv = Path(r"D:\00_商化\00_已OCR照片\_ocr_audit\0001_202605_商化照片-202605_7fcffe68\success_records.csv")
    period = "202605"
    current_year = str(datetime.now().year)

    print(f"Period: {period}, Current year: {current_year}")
    print(f"Period starts with current year: {period.startswith(current_year)}")

    raw_records = read_dict_csv(success_csv)
    print(f"\nTotal records: {len(raw_records)}")

    records = [enrich_record(record) for record in raw_records]

    unknown_price_rows = [
        record
        for record in records
        if period.startswith(current_year)
        and selected_value(record, "human_price", "price")
        and (record.get("price_status") or "") == "unknown"
    ]

    print(f"\nUnknown price rows: {len(unknown_price_rows)}")

    if unknown_price_rows:
        print("\nFirst 5 unknown records:")
        for r in unknown_price_rows[:5]:
            print(f"  {r.get('file_name')}: model={r.get('model')}, price={r.get('price')}, status={r.get('price_status')}")

    # Count by model type
    followme_unknown = [r for r in unknown_price_rows if "FOLLOWME" in (r.get("model") or "").upper()]
    samsung_unknown = [r for r in unknown_price_rows if (r.get("model") or "").startswith("S") and "FOLLOWME" not in (r.get("model") or "").upper()]
    null_unknown = [r for r in unknown_price_rows if not r.get("model") or r.get("model").lower() in ("null", "none", "")]

    print(f"\nBreakdown:")
    print(f"  FollowMe: {len(followme_unknown)}")
    print(f"  Samsung: {len(samsung_unknown)}")
    print(f"  Null/empty: {len(null_unknown)}")

if __name__ == "__main__":
    main()

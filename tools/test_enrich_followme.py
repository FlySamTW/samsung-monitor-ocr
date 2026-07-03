#!/usr/bin/env python3
"""Test enrich_record for a FollowMe record."""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from tools.repair_current_year_price_compare_outputs import enrich_record, read_dict_csv

def main():
    success_csv = Path(r"D:\00_商化\00_已OCR照片\_ocr_audit\0001_202605_商化照片-202605_7fcffe68\success_records.csv")
    records = read_dict_csv(success_csv)

    # Find a FollowMe record
    followme_records = [r for r in records if "FOLLOWME" in (r.get("model") or "").upper()]
    if not followme_records:
        print("No FollowMe records found")
        return

    print(f"Found {len(followme_records)} FollowMe records")
    print("\nFirst record BEFORE enrich_record:")
    r = followme_records[0]
    print(f"  file_name: {r.get('file_name')}")
    print(f"  model: {r.get('model')}")
    print(f"  price: {r.get('price')}")
    print(f"  price_status: {r.get('price_status')}")
    print(f"  price_symbol: {r.get('price_symbol')}")

    enriched = enrich_record(r)
    print("\nAFTER enrich_record:")
    print(f"  model: {enriched.get('model')}")
    print(f"  price: {enriched.get('price')}")
    print(f"  price_status: {enriched.get('price_status')}")
    print(f"  price_symbol: {enriched.get('price_symbol')}")
    print(f"  official_price: {enriched.get('official_price')}")

if __name__ == "__main__":
    main()

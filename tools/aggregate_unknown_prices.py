#!/usr/bin/env python3
"""Aggregate unknown price records from all 2026 audit directories."""
import csv
import sys
from collections import defaultdict
from pathlib import Path

AUDIT_DIR = Path(r"D:\00_商化\00_已OCR照片\_ocr_audit")
OUTPUT_FILE = Path(r"D:\00_商化\samsung-monitor-ocr\unknown_price_summary.csv")


def read_success_records():
    """Read all success_records.csv from 2026 audit folders."""
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
                    all_unknown.append(row)
    return all_unknown


def aggregate(records):
    """Group by model + store_price."""
    groups = defaultdict(lambda: {"count": 0, "examples": [], "thinking": ""})
    for r in records:
        model = (r.get("model") or "").strip()
        price = (r.get("price") or "").strip()
        key = (model, price)
        groups[key]["count"] += 1
        if len(groups[key]["examples"]) < 3:
            groups[key]["examples"].append(r.get("file_name", ""))
        if not groups[key]["thinking"] and r.get("thinking"):
            groups[key]["thinking"] = r["thinking"][:80]
    return groups


def write_summary(groups):
    """Write aggregated summary to CSV."""
    rows = []
    for (model, price), data in sorted(groups.items(), key=lambda x: -x[1]["count"]):
        rows.append({
            "model": model,
            "store_price": price,
            "count": data["count"],
            "example_files": " | ".join(data["examples"]),
            "thinking_excerpt": data["thinking"],
        })
    with OUTPUT_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["model", "store_price", "count", "example_files", "thinking_excerpt"])
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main():
    print("Reading success_records.csv from 2026 audit folders...")
    records = read_success_records()
    print(f"Found {len(records)} unknown price records")

    groups = aggregate(records)
    count = write_summary(groups)
    print(f"Written {count} groups to {OUTPUT_FILE}")

    # Print summary by model category
    followme = sum(d["count"] for (m, _), d in groups.items() if "FOLLOW" in m.upper())
    samsung = sum(d["count"] for (m, _), d in groups.items() if m and m[0] == "S" and "FOLLOW" not in m.upper())
    null_model = sum(d["count"] for (m, _), d in groups.items() if not m or m.lower() in ("null", "none", ""))
    other = sum(d["count"] for (m, _), d in groups.items() if m and m not in ("null", "none", "") and "FOLLOW" not in m.upper() and m[0] != "S")

    print(f"\nSummary by model category:")
    print(f"  FollowMe series: {followme}")
    print(f"  Samsung models (S*): {samsung}")
    print(f"  Null/empty model: {null_model}")
    print(f"  Other: {other}")


if __name__ == "__main__":
    main()

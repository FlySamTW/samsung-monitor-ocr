#!/usr/bin/env python3
"""Generate price_review_required CSVs for all 2026 periods with auto-lookup."""
import csv
import sys
from collections import defaultdict
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parents[1]))

from tools.repair_current_year_price_compare_outputs import (
    enrich_record, read_dict_csv, selected_value, write_dict_csv, digits_or_none
)
from tools.photo_rename_planner import build_target_name
from skills.official_price import get_price_manager, validate_ocr_price

OUTPUT_DIR = Path(r"D:\00_商化\00_已OCR照片\_ocr_audit")
REPO_ROOT = Path(r"D:\00_商化\samsung-monitor-ocr")
SOURCE_ROOT = Path(r"D:\00_商化\00_未整理商化照片")

REVIEW_HEADERS = [
    "period",
    "source_file",
    "source_path",
    "current_target_name",
    "model",
    "store_price",
    "price_status",
    "price_symbol",
    "official_price",
    "thinking",
    "review_action",
    "manual_reference_price",
    "manual_symbol",
    "notes",
]

DISTANT_VIEW_KEYWORDS = [
    "遠景", "多台", "展示區", "展示牆", "貨架", "海報", "廣告",
    "整排", "一排", "一整排", "牆上", "多支", "多螢幕", "陳列架",
    "非三星", "其他品牌", "多品牌", "沒有規格牌", "非主角",
]

SUMMARY_HEADERS = [
    "model",
    "store_price",
    "count",
    "periods",
    "sample_source_file",
    "review_action",
    "manual_reference_price",
    "manual_symbol",
]


def infer_source_path(source_file: str, period: str) -> str:
    """Infer source path from filename and period."""
    # Try common patterns
    candidates = [
        SOURCE_ROOT / f"商化照片-{period}" / source_file,
        SOURCE_ROOT / f"商化照片-{period}" / f"SAM{period}-門市" / source_file,
        SOURCE_ROOT / f"SAM{period}-門市" / source_file,
        SOURCE_ROOT / f"SAM{period}-轉檔" / source_file,
    ]
    # Find first existing path
    for c in candidates:
        if c.exists():
            return str(c)
    # Fallback
    return str(SOURCE_ROOT / f"商化照片-{period}" / source_file)


def get_store_price_symbol(store_price: int, ref_price: int) -> str:
    """Compute comparison symbol."""
    if store_price > ref_price:
        return "↑"
    elif store_price < ref_price:
        return "↓"
    return "✓"


def auto_lookup_price(model: str, store_price: int) -> tuple[str, int]:
    """Retry official price lookup with strict matching."""
    if not model or model.lower() in ("null", "none", ""):
        return "", 0
    
    manager = get_price_manager()
    official_price = manager.get_official_price(model)
    
    if official_price and official_price > 0:
        symbol = get_store_price_symbol(store_price, official_price)
        return symbol, official_price
    
    return "", 0


def compute_review_action(model: str, thinking: str) -> str:
    """Determine review_action based on model."""
    if not model or model.lower() in ("null", "none", ""):
        # Check if thinking has distant-view clues
        if thinking and any(kw in thinking for kw in DISTANT_VIEW_KEYWORDS):
            return "reclassify_to_distant_view"
        return "targeted_rerun"
    if model.startswith("S") and len(model) >= 6:
        return "manual_reference_price"
    return "manual_review"


def compute_notes(record: dict, review_action: str) -> str:
    """Generate helpful notes for the reviewer."""
    model = selected_value(record, "human_model", "model")
    thinking = record.get("thinking", "")
    notes = []
    
    if review_action == "manual_reference_price":
        notes.append(f"Samsung 型號 {model} 查無官網/PChome 價格，需人工查詢參考價")
    elif review_action == "reclassify_to_distant_view":
        notes.append("無型號且獨白含遠景線索，建議改分類為遠景")
    elif review_action == "targeted_rerun":
        notes.append("無型號但無明顯遠景線索，建議 target rerun 或手動確認")
    else:
        notes.append("需人工確認")
    
    return "; ".join(notes)


def process_period(period: str) -> tuple[list[dict], list[dict], list[dict]]:
    """Process one period and return review rows, reclassify candidates, rerun candidates."""
    audit_folders = list(OUTPUT_DIR.glob(f"*_{period}_*"))
    if not audit_folders:
        print(f"No audit folder found for {period}")
        return [], [], []
    
    audit_folder = audit_folders[0]
    success_csv = audit_folder / "success_records.csv"
    
    raw_records = read_dict_csv(success_csv)
    records = [enrich_record(record) for record in raw_records]
    current_year = str(datetime.now().year)
    
    unknown_price_rows = [
        record
        for record in records
        if period.startswith(current_year)
        and selected_value(record, "human_price", "price")
        and (record.get("price_status") or "") == "unknown"
    ]
    
    review_rows = []
    reclassify_candidates = []
    rerun_candidates = []
    
    for record in unknown_price_rows:
        source_file = record.get("file_name", "")
        model = selected_value(record, "human_model", "model")
        store_price_str = selected_value(record, "human_price", "price")
        store_price = digits_or_none(store_price_str) or 0
        thinking = record.get("thinking", "")
        
        source_path = infer_source_path(source_file, period)
        source_path_obj = Path(source_path)
        
        try:
            current_target_name = build_target_name(
                source_path_obj, record, period, "＄", int(current_year)
            )
        except Exception as e:
            current_target_name = f"ERROR: {e}"
        
        review_action = compute_review_action(model, thinking)
        notes = compute_notes(record, review_action)
        manual_price = ""
        manual_symbol = ""
        
        if review_action == "manual_reference_price":
            # Try auto lookup
            symbol, price = auto_lookup_price(model, store_price)
            if price > 0:
                manual_price = str(price)
                manual_symbol = symbol
                notes = f"自動查價：參考價 NT${price:,}，店內價 NT${store_price:,}"
        
        review_row = {
            "period": period,
            "source_file": source_file,
            "source_path": source_path,
            "current_target_name": current_target_name,
            "model": model,
            "store_price": store_price_str,
            "price_status": record.get("price_status", "unknown"),
            "price_symbol": record.get("price_symbol", "?"),
            "official_price": record.get("official_price", ""),
            "thinking": thinking[:200] if thinking else "",
            "review_action": review_action,
            "manual_reference_price": manual_price,
            "manual_symbol": manual_symbol,
            "notes": notes,
        }
        review_rows.append(review_row)
        
        if review_action == "reclassify_to_distant_view":
            reclassify_candidates.append(review_row)
        elif review_action == "targeted_rerun":
            rerun_candidates.append(review_row)
    
    return review_rows, reclassify_candidates, rerun_candidates


def generate_summary(all_review_rows: list[dict]) -> list[dict]:
    """Group by model + store_price for summary."""
    groups = defaultdict(lambda: {"count": 0, "periods": set(), "sample": "", "review_action": "", "manual_price": "", "manual_symbol": ""})
    
    for row in all_review_rows:
        model = row["model"]
        price = row["store_price"]
        key = (model, price)
        groups[key]["count"] += 1
        groups[key]["periods"].add(row["period"])
        if not groups[key]["sample"]:
            groups[key]["sample"] = row["source_file"]
        if not groups[key]["review_action"]:
            groups[key]["review_action"] = row["review_action"]
        if not groups[key]["manual_price"] and row.get("manual_reference_price"):
            groups[key]["manual_price"] = row["manual_reference_price"]
            groups[key]["manual_symbol"] = row["manual_symbol"]
    
    summary_rows = []
    for (model, price), data in sorted(groups.items(), key=lambda x: -x[1]["count"]):
        summary_rows.append({
            "model": model,
            "store_price": price,
            "count": data["count"],
            "periods": ",".join(sorted(data["periods"])),
            "sample_source_file": data["sample"],
            "review_action": data["review_action"],
            "manual_reference_price": data["manual_price"],
            "manual_symbol": data["manual_symbol"],
        })
    
    return summary_rows


def main():
    periods = ["202605", "202604", "202603", "202602", "202601"]
    all_review_rows = []
    all_reclassify = []
    all_rerun = []
    
    for period in periods:
        print(f"Processing {period}...")
        review_rows, reclassify, rerun = process_period(period)
        
        if review_rows:
            output_csv = REPO_ROOT / f"price_review_required_{period}.csv"
            write_dict_csv(output_csv, review_rows, REVIEW_HEADERS)
            print(f"  Written {len(review_rows)} rows to {output_csv.name}")
            print(f"    manual_reference_price: {sum(1 for r in review_rows if r['review_action'] == 'manual_reference_price')}")
            print(f"    reclassify_to_distant_view: {len(reclassify)}")
            print(f"    targeted_rerun: {len(rerun)}")
        
        all_review_rows.extend(review_rows)
        all_reclassify.extend(reclassify)
        all_rerun.extend(rerun)
    
    # Master CSV
    if all_review_rows:
        master_csv = REPO_ROOT / "price_review_required_all_2026.csv"
        write_dict_csv(master_csv, all_review_rows, REVIEW_HEADERS)
        print(f"\nMaster CSV: {master_csv} ({len(all_review_rows)} rows)")
    
    # Summary CSV
    if all_review_rows:
        summary_rows = generate_summary(all_review_rows)
        summary_csv = REPO_ROOT / "unknown_price_summary_2026.csv"
        write_dict_csv(summary_csv, summary_rows, SUMMARY_HEADERS)
        print(f"Summary CSV: {summary_csv} ({len(summary_rows)} groups)")
    
    # Candidate CSVs
    if all_reclassify:
        reclassify_csv = REPO_ROOT / "reclassify_to_distant_view_candidates_2026.csv"
        write_dict_csv(reclassify_csv, all_reclassify, REVIEW_HEADERS)
        print(f"Distant-view candidates: {reclassify_csv} ({len(all_reclassify)} rows)")
    
    if all_rerun:
        rerun_csv = REPO_ROOT / "targeted_rerun_candidates_2026.csv"
        write_dict_csv(rerun_csv, all_rerun, REVIEW_HEADERS)
        print(f"Targeted rerun candidates: {rerun_csv} ({len(all_rerun)} rows)")
    
    # Final summary
    resolved = sum(1 for r in all_review_rows if r.get("manual_reference_price"))
    unresolved = len(all_review_rows) - resolved
    print(f"\nFinal: {len(all_review_rows)} unknown rows")
    print(f"  Auto-resolved by price lookup: {resolved}")
    print(f"  Still unresolved: {unresolved}")


if __name__ == "__main__":
    main()

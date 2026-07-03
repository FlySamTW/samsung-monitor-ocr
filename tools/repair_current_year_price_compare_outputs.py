import argparse
import csv
import io
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from rich.console import Console

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import skills.official_price as official_price_module  # noqa: E402
from skills.official_price import get_price_manager, validate_ocr_price  # noqa: E402
from tools.photo_rename_planner import (  # noqa: E402
    CONFLICT_STATUS,
    MISSING_RESULT_STATUS,
    MISSING_SOURCE_STATUS,
    copy_plan_to_flat_output,
    make_plan,
    summarize,
    write_csv,
)

official_price_module.console = Console(file=io.StringIO(), force_terminal=False, no_color=True)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
SUCCESS_HEADERS = [
    "timestamp",
    "file_name",
    "category",
    "view_type",
    "screen_status",
    "quality_issue",
    "model",
    "price",
    "price_status",
    "price_symbol",
    "official_price",
    "price_diff_percent",
    "duration",
    "run_id",
    "review_status",
    "human_category",
    "human_model",
    "human_price",
    "human_notes",
    "thinking",
]


def read_dict_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_dict_csv(path: Path, rows: Iterable[Dict[str, str]], headers: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})


def digits_or_none(value: object) -> Optional[int]:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return int(digits) if digits else None


def selected_value(row: Dict[str, str], human_field: str, model_field: str) -> str:
    human = str(row.get(human_field) or "").strip()
    if human and human.lower() not in {"null", "none", "nan"}:
        return human
    model = str(row.get(model_field) or "").strip()
    if model and model.lower() not in {"null", "none", "nan"}:
        return model
    return ""


def refresh_official_price_cache() -> None:
    manager = get_price_manager()
    manager.clear_and_init()
    fetch = getattr(manager, "_bulk_fetch_prices_from_searchapi", None)
    rewrite = getattr(manager, "_rewrite_cache_file", None)
    if not callable(fetch):
        return
    prices = fetch()
    if not prices:
        return
    for model, price in prices.items():
        if price and price > 0:
            manager.price_cache[str(model).upper()] = int(price)
    if callable(rewrite):
        rewrite()


def load_manual_reference_models(repo_root: Path) -> set:
    """[v19.8] Load models marked as manual_reference_price from the master review CSV.

    These are valid Samsung models that currently have no Samsung/PChome reference price.
    Pre-marking them prevents slow repeated network lookups during repair.
    """
    master_csv = repo_root / "price_review_required_all_2026.csv"
    models = set()
    if not master_csv.exists():
        return models
    try:
        with master_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("review_action") == "manual_reference_price":
                    model = str(row.get("model") or "").strip().upper()
                    if model:
                        models.add(model)
    except Exception as e:
        print(f"[WARN] Could not load manual reference models: {e}", flush=True)
    return models


def premark_unpriced_models(models: set) -> None:
    """[v19.8] Mark models as already fetched so get_official_price skips network lookup."""
    if not models:
        return
    manager = get_price_manager()
    for model in models:
        manager.session_fetched.add(model.upper())
    print(f"[INFO] Pre-marked {len(models)} models as no-online-price to skip network lookup", flush=True)


def enrich_record(row: Dict[str, str]) -> Dict[str, str]:
    enriched = dict(row)
    if not selected_value(enriched, "human_price", "price"):
        thinking = str(enriched.get("thinking") or "")
        if selected_value(enriched, "human_model", "model"):
            price_match = re.search(r"(?:NT\$|NT\.|價格|售價|市價|會員售價)?\s*([2-9]\d{0,2},\d{3}|[2-9]\d{3,4})\s*(?:元|塊)?", thinking)
            if price_match:
                price = price_match.group(1).replace(",", "")
                if 2000 <= int(price) < 200000:
                    enriched["price"] = price
    model = selected_value(enriched, "human_model", "model").upper()
    price = digits_or_none(selected_value(enriched, "human_price", "price"))
    if model and price:
        lookup_model = normalize_model_for_price_lookup(model)
        result = validate_ocr_price(lookup_model, price)
        status = str(result.get("status") or "unknown")
        symbol = str(result.get("symbol") or "?")
        if status == "discontinued" or symbol in {"-", "停產"}:
            status = "unknown"
            symbol = "?"
        enriched["price_status"] = status
        enriched["price_symbol"] = symbol
        # If normalization resolved the price, update the model too.
        if lookup_model != model and status not in {"unknown", "not_compared"}:
            enriched["model"] = lookup_model
            if selected_value(enriched, "human_model", "model") == model:
                enriched["human_model"] = lookup_model
        official_price = result.get("official_price")
        diff_percent = result.get("diff_percent")
        enriched["official_price"] = "" if official_price in (None, -1) else str(official_price)
        enriched["price_diff_percent"] = "" if diff_percent is None else str(diff_percent)
    elif price:
        enriched["price_status"] = "unknown"
        enriched["price_symbol"] = "?"
        enriched["official_price"] = ""
        enriched["price_diff_percent"] = ""
    return enriched


DISTANT_VIEW_EXPLICIT = [
    "遠景", "多台", "展示區", "展示牆", "貨架", "海報", "廣告",
    "整排", "一排", "一整排", "牆上", "多支", "多螢幕", "陳列架",
    "非三星", "其他品牌", "多品牌", "LG", "ASUS", "BENQ",
]

DISTANT_VIEW_SINGLE_EXCLUSION = [
    "同一台", "只有一台", "一台", "清晰可讀", "主角", "清楚",
]


# [v19.8] Known OCR misreads: Samsung monitor models that PChome actually lists under a similar code.
MODEL_OCR_NORMALIZATION = {
    "S32DM703UC": "S32FM703UC",
    "S43DM703UC": "S43FM703UC",
    "S32DM702UC": "S32FM702UC",
}


def normalize_model_for_price_lookup(model: str) -> str:
    if not model:
        return model
    upper = model.upper().strip()
    return MODEL_OCR_NORMALIZATION.get(upper, model)


def should_reclassify_to_distant_view(record: Dict[str, str]) -> bool:
    model = selected_value(record, "human_model", "model")
    if model and model.lower() not in ("null", "none", ""):
        return False
    thinking = str(record.get("thinking") or "")
    if not thinking:
        return False
    if any(excl in thinking for excl in DISTANT_VIEW_SINGLE_EXCLUSION):
        return False
    return any(kw in thinking for kw in DISTANT_VIEW_EXPLICIT)


def fix_distant_view_misclass(records: List[Dict[str, str]]) -> int:
    fixed = 0
    for record in records:
        view_type = str(record.get("view_type") or "").strip()
        if view_type == "遠景":
            continue
        if not should_reclassify_to_distant_view(record):
            continue
        record["view_type"] = "遠景"
        record["category"] = "遠景"
        record["model"] = ""
        record["price"] = ""
        record["quality_issue"] = ""
        record["price_status"] = "not_compared"
        record["price_symbol"] = ""
        record["official_price"] = ""
        record["price_diff_percent"] = ""
        fixed += 1
    return fixed


def records_to_map(records: Iterable[Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    mapped: Dict[str, Dict[str, str]] = {}
    for row in records:
        file_name = (row.get("file_name") or row.get("filename") or "").strip()
        if file_name:
            mapped[file_name] = row
    return mapped


def load_review_csv(audit_dir: Path, period: str) -> Dict[str, Dict[str, str]]:
    """Load user-completed review CSV for a period if it exists."""
    review_path = Path(__file__).resolve().parents[1] / f"price_review_required_{period}.csv"
    if not review_path.exists():
        return {}
    rows = read_dict_csv(review_path)
    result: Dict[str, Dict[str, str]] = {}
    for row in rows:
        source_file = (row.get("source_file") or "").strip()
        if source_file:
            result[source_file] = row
    return result


def apply_review_corrections(records: List[Dict[str, str]], review_map: Dict[str, Dict[str, str]]) -> int:
    """Apply manual/auto review corrections to records."""
    applied = 0
    for record in records:
        file_name = (record.get("file_name") or "").strip()
        review = review_map.get(file_name)
        if not review:
            continue
        action = review.get("review_action", "").strip()
        
        if action == "manual_reference_price":
            manual_price_str = (review.get("manual_reference_price") or "").strip()
            manual_symbol = (review.get("manual_symbol") or "").strip()
            if manual_price_str and manual_symbol and manual_symbol in {"↑", "↓", "✓"}:
                record["price_status"] = "manual_reviewed"
                record["price_symbol"] = manual_symbol
                record["official_price"] = manual_price_str
                # Compute diff percent
                try:
                    store_price = digits_or_none(record.get("price")) or 0
                    ref_price = int(manual_price_str)
                    if ref_price > 0:
                        diff = round(((store_price - ref_price) / ref_price) * 100, 1)
                        record["price_diff_percent"] = str(diff)
                except (ValueError, TypeError):
                    record["price_diff_percent"] = ""
                applied += 1
        
        elif action == "reclassify_to_distant_view":
            record["view_type"] = "遠景"
            record["category"] = "遠景"
            record["model"] = ""
            record["price"] = ""
            record["quality_issue"] = ""
            record["price_status"] = "not_compared"
            record["price_symbol"] = ""
            record["official_price"] = ""
            record["price_diff_percent"] = ""
            applied += 1
    return applied


def backup_existing_outputs(output_dir: Path, period_prefix: str, dry_run: bool) -> tuple[Path, int]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = output_dir / f"_bad_no_compare_{period_prefix}_backup_{stamp}"
    pattern = re.compile(rf"^M-{re.escape(period_prefix)}\d{{2}}-", re.IGNORECASE)
    candidates = [
        path
        for path in output_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS and pattern.match(path.name)
    ]
    if dry_run:
        return backup_dir, len(candidates)
    if candidates:
        backup_dir.mkdir(parents=True, exist_ok=True)
    for path in candidates:
        shutil.move(str(path), str(backup_dir / path.name))
    return backup_dir, len(candidates)


def image_count(folder: Path) -> int:
    return sum(
        1
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )


def audit_rows_for_period(audit_dir: Path, period_prefix: str) -> List[Dict[str, str]]:
    rows = read_dict_csv(audit_dir / "folder_summary.csv")
    selected = {
        str(row.get("period") or ""): row
        for row in rows
        if str(row.get("period") or "").startswith(period_prefix)
        and str(row.get("status") or "") in {"copied", "planned", "skipped_copied"}
    }
    for success_path in sorted(audit_dir.glob(f"*_{period_prefix}*/success_records.csv")):
        match = re.search(r"(20\d{4})", success_path.parent.name)
        if not match:
            continue
        period = match.group(1)
        if period in selected:
            continue
        copied_path = success_path.parent / "copied.csv"
        copied_rows = read_dict_csv(copied_path)
        folder = ""
        if copied_rows:
            first_source = copied_rows[0].get("original_path") or ""
            if first_source:
                folder = str(Path(first_source).parent)
        if folder:
            selected[period] = {
                "folder": folder,
                "period": period,
                "status": "copied",
                "copied_path": str(copied_path),
            }
    return list(selected.values())


def repair_folder(row: Dict[str, str], audit_dir: Path, output_dir: Path, price_symbol: str, dry_run: bool, allow_no_symbol_for_unknown: bool = False) -> Dict[str, object]:
    folder = Path(row["folder"])
    period = row["period"]
    token_dir = Path(row.get("copied_path") or "")
    if token_dir.name.lower() == "copied.csv":
        token_dir = token_dir.parent
    if not token_dir:
        token_dir = next(audit_dir.glob(f"*_{period}_*"), Path())
    success_path = token_dir / "success_records.csv"
    if not folder.exists():
        raise FileNotFoundError(f"source folder missing: {folder}")
    if not success_path.exists():
        raise FileNotFoundError(f"success_records.csv missing: {success_path}")

    raw_records = read_dict_csv(success_path)
    dv_fixed = fix_distant_view_misclass(raw_records)
    if dv_fixed and not dry_run:
        print(f"  distant_view_fixed={dv_fixed}")
    records = [enrich_record(record) for record in raw_records]
    
    # Apply user-completed review CSV corrections
    review_map = load_review_csv(audit_dir, period)
    review_applied = apply_review_corrections(records, review_map)
    if review_applied and not dry_run:
        print(f"  review_corrections_applied={review_applied}")
    
    current_year = str(datetime.now().year)
    unknown_price_rows = [
        record
        for record in records
        if period.startswith(current_year)
        and selected_value(record, "human_price", "price")
        and (record.get("price_status") or "") == "unknown"
    ]
    if unknown_price_rows and not allow_no_symbol_for_unknown:
        review_path = Path(__file__).resolve().parents[1] / f"price_review_required_{period}.csv"
        return {
            "period": period,
            "folder": str(folder),
            "records": len(records),
            "images": image_count(folder),
            "copied": 0,
            "with_price_symbol": 0,
            "blocked": True,
            "copy_error": f"{len(unknown_price_rows)} current-year prices without Samsung/PChome reference: {review_path}",
            "review_path": str(review_path),
        }
    # [v19.8] When allowed, clear price_symbol for unknown-price current-year records
    # so filenames do not silently contain "？".
    if unknown_price_rows and allow_no_symbol_for_unknown and not dry_run:
        for record in unknown_price_rows:
            record["price_symbol"] = ""
            record["official_price"] = ""
    results_map = records_to_map(records)
    plan = make_plan(folder, results_map, period, price_symbol)
    counts = summarize(plan)
    unsafe = counts.get(MISSING_RESULT_STATUS, 0) + counts.get(MISSING_SOURCE_STATUS, 0) + counts.get(CONFLICT_STATUS, 0)
    if unsafe:
        raise RuntimeError(f"{folder} has unsafe rows: {counts}")

    copied_count = 0
    if not dry_run:
        write_dict_csv(success_path, records, SUCCESS_HEADERS)
        write_csv(token_dir / "rename_plan.csv", plan)
        write_csv(token_dir / "conflicts.csv", [item for item in plan if item["status"] == CONFLICT_STATUS])
        copied = copy_plan_to_flat_output(plan, output_dir)
        write_csv(token_dir / "copied.csv", copied)
        copied_count = len(copied)

    return {
        "period": period,
        "folder": str(folder),
        "records": len(records),
        "images": image_count(folder),
        "copied": copied_count,
        "with_price_symbol": sum(
            1
            for item in plan
            if re.search(r"-[\u2191\u2193\u2713\uff1f]?\uff04\d+", item.get("target_name", ""))
        ),
        "blocked": False,
        "copy_error": "",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair current-year flat OCR outputs with official price compare symbols.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--audit-dir")
    parser.add_argument("--period-prefix", default=str(datetime.now().year))
    parser.add_argument("--price-symbol", default="\uff04", choices=["\uff04", "$", ""])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--allow-no-symbol-for-unknown",
        action="store_true",
        help="[v19.8] 當年度查無 Samsung/PChome 參考價時，不阻塞輸出，改為不加比價符號",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    audit_dir = Path(args.audit_dir).resolve() if args.audit_dir else output_dir / "_ocr_audit"
    rows = audit_rows_for_period(audit_dir, args.period_prefix)
    if not rows:
        raise RuntimeError(f"no copied/planned audit rows found for {args.period_prefix}")

    print(f"period_prefix={args.period_prefix}")
    print(f"folders={len(rows)}")

    blocked_rows = []
    active_rows = []
    if not args.dry_run:
        refresh_official_price_cache()
    manual_models = load_manual_reference_models(REPO_ROOT)
    premark_unpriced_models(manual_models)
    time.sleep(0.5)
    print("preflight=checking current-year unknown price references")
    for row in rows:
        result = repair_folder(row, audit_dir, output_dir, args.price_symbol, dry_run=True, allow_no_symbol_for_unknown=args.allow_no_symbol_for_unknown)
        if result.get("blocked"):
            blocked_rows.append(row)
            print(f"BLOCKED {row['folder']}: {result.get('copy_error', '')}", flush=True)
        else:
            active_rows.append(row)

    if blocked_rows and not active_rows:
        print(f"all_folders_blocked={len(blocked_rows)}")
        print("no folders can be repaired until price_review_required.csv is resolved")
        summary_path = audit_dir / "folder_summary.csv"
        if summary_path.exists() and not args.dry_run:
            _update_summary_blocked(summary_path, blocked_rows)
        return 1

    backup_dir, moved = backup_existing_outputs(output_dir, args.period_prefix, args.dry_run)
    print(f"backup_dir={backup_dir}")
    print(f"moved_bad_outputs={moved}")

    total_copied = 0
    for row in active_rows:
        result = repair_folder(row, audit_dir, output_dir, args.price_symbol, args.dry_run, allow_no_symbol_for_unknown=args.allow_no_symbol_for_unknown)
        total_copied += int(result["copied"])
        status_tag = "BLOCKED" if result.get("blocked") else "ok"
        print(
            f"[{status_tag}] folder period={result['period']} records={result['records']} "
            f"images={result['images']} copied={result['copied']} "
            f"price_segments={result['with_price_symbol']}",
            flush=True,
        )

    print(f"total_copied={total_copied}")
    print(f"blocked_folders={len(blocked_rows)}")

    summary_path = audit_dir / "folder_summary.csv"
    if blocked_rows and summary_path.exists() and not args.dry_run:
        _update_summary_blocked(summary_path, blocked_rows)

    if blocked_rows:
        return 1
    return 0


def _update_summary_blocked(summary_path: Path, blocked_rows: List[Dict[str, str]]) -> None:
    all_rows = read_dict_csv(summary_path)
    blocked_folders = {str(row.get("folder") or "") for row in blocked_rows}
    for row in all_rows:
        if str(row.get("folder") or "") in blocked_folders:
            row["status"] = "blocked"
            row["copy_error"] = "price_review_required: current-year prices without Samsung/PChome reference"
    write_dict_csv(summary_path, all_rows, list(all_rows[0].keys()) if all_rows else [])


if __name__ == "__main__":
    raise SystemExit(main())

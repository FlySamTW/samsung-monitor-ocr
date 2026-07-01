import argparse
import csv
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from PIL import Image, ImageOps


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
OUTPUT_MAX_LONG_EDGE = 2560
OUTPUT_JPEG_QUALITY = 88
UNKNOWN_MODEL = "型號未辨識"
UNKNOWN_PRICE = "無價格"
MISSING_RESULT_STATUS = "missing_result"
READY_STATUS = "ready"
NO_CHANGE_STATUS = "no_change"
CONFLICT_STATUS = "conflict"
MISSING_SOURCE_STATUS = "missing_source"
INVALID_FILENAME_CHARS = '<>:"/\\|?*'
COMPARE_SYMBOLS_FOR_FILENAME = {
    "↑": "↑",
    "↓": "↓",
    "✓": "✓",
    "?": "？",
    "？": "？",
    "-": "停產",
    "停產": "停產",
}


def read_results_csv(path: Path) -> Dict[str, Dict[str, str]]:
    rows: Dict[str, Dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            file_name = (row.get("file_name") or row.get("filename") or "").strip()
            if file_name:
                rows[file_name] = row
    return rows


def iter_images(image_dir: Path) -> Iterable[Path]:
    for path in sorted(image_dir.iterdir(), key=lambda item: item.name.casefold()):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            yield path


def infer_period(image_dir: Path, explicit_period: Optional[str]) -> str:
    if explicit_period:
        return explicit_period.strip()
    folder_name = image_dir.name
    month_match = re.search(r"(20\d{4})", folder_name)
    if month_match:
        return month_match.group(1)
    year_match = re.search(r"(20\d{2})", folder_name)
    if year_match:
        return year_match.group(1)
    raise ValueError(
        "無法從資料夾名稱推得年月，請用 --period 指定，例如 --period 202603。"
    )


def value_or_none(value: object) -> Optional[str]:
    text = str(value or "").strip()
    if not text or text.lower() in {"null", "none", "nan"}:
        return None
    return text


def choose_field(row: Dict[str, str], human_field: str, model_field: str) -> Optional[str]:
    return value_or_none(row.get(human_field)) or value_or_none(row.get(model_field))


def period_year(period: str) -> Optional[int]:
    match = re.search(r"(20\d{2})", str(period or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def should_include_price_compare_symbol(
    period: str,
    current_year: Optional[int] = None,
) -> bool:
    year = period_year(period)
    if year is None:
        return True
    baseline_year = current_year or datetime.now().year
    return year >= baseline_year


def display_category(row: Dict[str, str]) -> str:
    human_category = value_or_none(row.get("human_category"))
    view_type = value_or_none(row.get("view_type"))
    category = human_category or value_or_none(row.get("category")) or view_type
    combined = " ".join(part for part in [category, view_type] if part)
    if "遠景" in combined:
        return "遠景"
    if "失敗" in combined:
        return "失敗"
    return "單機"


def canonical_followme_model(model: str) -> Optional[str]:
    upper = model.upper()
    if "FOLLOW" not in upper:
        return None
    if "PRO" in upper or "43" in upper or "S43FM" in upper:
        return 'FollowMe Pro M7 43"'
    if "M5" in upper or "FHD" in upper or "S32FM50" in upper:
        return 'FollowMe M5 32"'
    if "M7" in upper or "4K" in upper or "S32FM70" in upper or "S32DM70" in upper:
        return 'FollowMe M7 32"'
    return "FollowMe 型號未細分"


def model_segment(row: Dict[str, str]) -> str:
    model = choose_field(row, "human_model", "model")
    if not model:
        return UNKNOWN_MODEL
    followme = canonical_followme_model(model)
    return followme or model.strip().upper()


def price_segment(
    row: Dict[str, str],
    price_symbol: str,
    period: Optional[str] = None,
    current_year: Optional[int] = None,
) -> str:
    price = choose_field(row, "human_price", "price")
    digits = "".join(ch for ch in str(price or "") if ch.isdigit())
    if not digits:
        return UNKNOWN_PRICE
    compare_symbol = value_or_none(row.get("price_symbol")) or ""
    price_status = value_or_none(row.get("price_status")) or ""
    if price_status in {"", "not_compared", "未比價"}:
        compare_symbol = ""
    if period and not should_include_price_compare_symbol(period, current_year):
        compare_symbol = ""
    compare_symbol = COMPARE_SYMBOLS_FOR_FILENAME.get(compare_symbol, compare_symbol)
    if compare_symbol not in {"↑", "↓", "✓", "？", "停產"}:
        compare_symbol = ""
    return f"{compare_symbol}{price_symbol}{digits}" if price_symbol else f"{compare_symbol}{digits}"


def sanitize_segment(segment: str) -> str:
    text = segment.strip().replace('"', "吋").replace("'", "")
    text = re.sub(r"\s+", "_", text)
    cleaned = []
    for char in text:
        codepoint = ord(char)
        if char in INVALID_FILENAME_CHARS or codepoint < 32:
            cleaned.append("_")
        else:
            cleaned.append(char)
    text = "".join(cleaned)
    text = re.sub(r"_+", "_", text).strip(" ._")
    return text or "未命名"


def split_source_name(path: Path) -> Tuple[str, List[str], str]:
    stem = path.stem
    parts = stem.split("-")
    marker = "M"

    if parts and parts[0] == "M":
        parts = parts[1:]

    if parts and re.fullmatch(r"20\d{2}(?:\d{2})?", parts[0]):
        parts = parts[1:]

    if len(parts) >= 2:
        serial = parts[-1]
        store_parts = parts[:-1]
    else:
        serial = stem
        store_parts = []

    return marker, store_parts, serial


def build_target_name(
    source_path: Path,
    row: Dict[str, str],
    period: str,
    price_symbol: str,
    current_year: Optional[int] = None,
) -> str:
    marker, store_parts, serial = split_source_name(source_path)
    segments = [
        marker,
        period,
        *store_parts,
        display_category(row),
        model_segment(row),
        price_segment(row, price_symbol, period, current_year),
        serial,
    ]
    safe_segments = [sanitize_segment(segment) for segment in segments]
    return "-".join(safe_segments) + source_path.suffix.lower()


def make_plan(
    image_dir: Path,
    results: Dict[str, Dict[str, str]],
    period: str,
    price_symbol: str,
    current_year: Optional[int] = None,
) -> List[Dict[str, str]]:
    plan: List[Dict[str, str]] = []
    image_by_name = {path.name: path for path in iter_images(image_dir)}

    for image_name, image_path in image_by_name.items():
        row = results.get(image_name)
        if not row:
            plan.append(
                {
                    "status": MISSING_RESULT_STATUS,
                    "reason": "找不到此照片的 OCR 結果，避免直接改名",
                    "period": period,
                    "original_name": image_name,
                    "target_name": "",
                    "category": "",
                    "model": "",
                    "price": "",
                    "original_path": str(image_path),
                    "target_path": "",
                }
            )
            continue

        target_name = build_target_name(image_path, row, period, price_symbol, current_year)
        target_path = image_dir / target_name
        status = NO_CHANGE_STATUS if target_name == image_name else READY_STATUS
        reason = ""
        if target_path.exists() and target_name != image_name:
            status = CONFLICT_STATUS
            reason = "目標檔名已存在"

        plan.append(
            {
                "status": status,
                "reason": reason,
                "period": period,
                "original_name": image_name,
                "target_name": target_name,
                "category": display_category(row),
                "model": model_segment(row),
                "price": price_segment(row, price_symbol, period, current_year),
                "original_path": str(image_path),
                "target_path": str(target_path),
            }
        )

    for result_name in sorted(set(results) - set(image_by_name), key=str.casefold):
        plan.append(
            {
                "status": MISSING_SOURCE_STATUS,
                "reason": "OCR 結果有此檔名，但照片資料夾內找不到來源照片",
                "period": period,
                "original_name": result_name,
                "target_name": "",
                "category": display_category(results[result_name]),
                "model": model_segment(results[result_name]),
                "price": price_segment(results[result_name], price_symbol, period, current_year),
                "original_path": str(image_dir / result_name),
                "target_path": "",
            }
        )

    target_counts: Dict[str, int] = {}
    for row in plan:
        target_name = row.get("target_name") or ""
        if target_name:
            target_counts[target_name.casefold()] = target_counts.get(target_name.casefold(), 0) + 1
    for row in plan:
        target_name = row.get("target_name") or ""
        if target_name and target_counts.get(target_name.casefold(), 0) > 1:
            row["status"] = CONFLICT_STATUS
            row["reason"] = "多張照片會產生同一個目標檔名"

    return plan


def write_csv(path: Path, rows: List[Dict[str, str]]) -> None:
    headers = [
        "status",
        "reason",
        "period",
        "original_name",
        "target_name",
        "category",
        "model",
        "price",
        "original_path",
        "target_path",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})


def apply_plan(plan: List[Dict[str, str]]) -> List[Dict[str, str]]:
    unsafe = [row for row in plan if row["status"] not in {READY_STATUS, NO_CHANGE_STATUS}]
    if unsafe:
        raise RuntimeError(
            f"仍有 {len(unsafe)} 筆不是 ready/no_change，請先處理 rename_plan.csv。"
        )

    applied: List[Dict[str, str]] = []
    for row in plan:
        if row["status"] == NO_CHANGE_STATUS:
            continue
        source = Path(row["original_path"])
        target = Path(row["target_path"])
        source.rename(target)
        applied.append(
            {
                "status": "renamed",
                "reason": "",
                "period": row["period"],
                "original_name": row["original_name"],
                "target_name": row["target_name"],
                "category": row["category"],
                "model": row["model"],
                "price": row["price"],
                "original_path": str(source),
                "target_path": str(target),
            }
        )
    return applied


def unique_target_path(output_dir: Path, target_name: str) -> Path:
    candidate = output_dir / target_name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 2
    while True:
        candidate = output_dir / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def copy_image_for_flat_output(source: Path, target: Path) -> None:
    try:
        with Image.open(source) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((OUTPUT_MAX_LONG_EDGE, OUTPUT_MAX_LONG_EDGE), Image.Resampling.LANCZOS)
            suffix = target.suffix.lower()
            if suffix in {".jpg", ".jpeg"}:
                if image.mode not in {"RGB", "L"}:
                    image = image.convert("RGB")
                image.save(
                    target,
                    format="JPEG",
                    quality=OUTPUT_JPEG_QUALITY,
                    optimize=True,
                    progressive=True,
                )
            elif suffix == ".png":
                image.save(target, format="PNG", optimize=True)
            else:
                shutil.copy2(source, target)
    except Exception:
        shutil.copy2(source, target)


def copy_plan_to_flat_output(plan: List[Dict[str, str]], output_dir: Path) -> List[Dict[str, str]]:
    unsafe = [row for row in plan if row["status"] not in {READY_STATUS, NO_CHANGE_STATUS}]
    if unsafe:
        raise RuntimeError(
            f"仍有 {len(unsafe)} 筆不是 ready/no_change，請先處理 rename_plan.csv。"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    copied: List[Dict[str, str]] = []
    for row in plan:
        source = Path(row["original_path"])
        target_name = row["target_name"] or row["original_name"]
        target = unique_target_path(output_dir, target_name)
        copy_image_for_flat_output(source, target)
        copied.append(
            {
                "status": "copied",
                "reason": "",
                "period": row["period"],
                "original_name": row["original_name"],
                "target_name": target.name,
                "category": row["category"],
                "model": row["model"],
                "price": row["price"],
                "original_path": str(source),
                "target_path": str(target),
            }
        )
    return copied


def summarize(plan: List[Dict[str, str]]) -> Dict[str, int]:
    summary: Dict[str, int] = {}
    for row in plan:
        status = row["status"]
        summary[status] = summary.get(status, 0) + 1
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="根據 Samsung OCR 結果產生照片檔名改名計畫；預設只產生 CSV，不改照片。"
    )
    parser.add_argument("--image-dir", required=True, help="照片資料夾，例如 D:\\00_歷年商化照片\\商化照片-202603")
    parser.add_argument("--results", required=True, help="OCR results.csv")
    parser.add_argument("--period", help="覆寫檔名前綴年月，例如 202603")
    parser.add_argument(
        "--price-symbol",
        default="＄",
        choices=["＄", "$", ""],
        help="價格欄位前綴；預設使用全形＄，避免 PowerShell 把 $ 當變數。",
    )
    parser.add_argument("--output-dir", help="輸出 rename_plan.csv 的資料夾；預設建立在照片資料夾內")
    parser.add_argument("--apply", action="store_true", help="正式原地改名。沒有這個參數時只產生計畫表。")
    parser.add_argument("--copy-to", help="把改名後照片複製到指定單一資料夾，不原地改名。")
    args = parser.parse_args()

    if args.apply and args.copy_to:
        parser.error("--apply 與 --copy-to 不能同時使用；請選擇原地改名或複製到新資料夾。")

    image_dir = Path(args.image_dir).resolve()
    results_path = Path(args.results).resolve()
    period = infer_period(image_dir, args.period)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir).resolve() if args.output_dir else image_dir / f"rename_plan_{stamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    results = read_results_csv(results_path)
    plan = make_plan(image_dir, results, period, args.price_symbol)
    plan_path = output_dir / "rename_plan.csv"
    conflict_path = output_dir / "conflicts.csv"
    rollback_path = output_dir / "rollback.csv"

    write_csv(plan_path, plan)
    write_csv(conflict_path, [row for row in plan if row["status"] == CONFLICT_STATUS])

    applied: List[Dict[str, str]] = []
    if args.apply:
        applied = apply_plan(plan)
    if args.copy_to:
        copied = copy_plan_to_flat_output(plan, Path(args.copy_to).resolve())
        copy_path = output_dir / "copied.csv"
        write_csv(copy_path, copied)
    write_csv(rollback_path, applied)

    print(f"period={period}")
    print(f"plan={plan_path}")
    print(f"conflicts={conflict_path}")
    print(f"rollback={rollback_path}")
    for status, count in sorted(summarize(plan).items()):
        print(f"{status}={count}")

    if args.copy_to:
        print("mode=copy")
    elif not args.apply:
        print("mode=dry-run")
    else:
        print(f"mode=apply renamed={len(applied)}")
    if args.copy_to:
        print(f"copy_to={Path(args.copy_to).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

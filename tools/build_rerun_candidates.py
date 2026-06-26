import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skills.audit_fields import enrich_result_for_review


REVIEW_HEADERS = [
    "file_name",
    "image_path",
    "category",
    "view_type",
    "screen_status",
    "quality_issue",
    "model",
    "price",
    "price_status",
    "official_price",
    "price_diff_percent",
    "rerun_priority",
    "rerun_reason",
    "rerun_recommended_model",
    "review_status",
    "human_is_correct",
    "human_category",
    "human_model",
    "human_price",
    "human_notes",
    "timestamp",
    "run_id",
    "duration",
]


def read_csv_records(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        yield from csv.DictReader(f)


def image_path_for(record, image_dir: Path | None):
    existing = record.get("image_path") or record.get("file") or ""
    if existing:
        return existing
    file_name = record.get("file_name") or record.get("filename") or ""
    if image_dir and file_name:
        return str(image_dir / file_name)
    return file_name


def main():
    parser = argparse.ArgumentParser(description="Build human-review CSV and 8B rerun candidates from OCR results.")
    parser.add_argument("--input", required=True, help="OCR results CSV, usually runs/<run_id>/results.csv")
    parser.add_argument("--image-dir", default="", help="Original photo folder used to build absolute image paths.")
    parser.add_argument("--review-output", default="", help="CSV for human review. Defaults to <input>_review.csv")
    parser.add_argument("--rerun-output", default="", help="JSON cases for 8B rerun. Defaults to <input>_rerun_candidates.json")
    parser.add_argument("--priority", default="P1", choices=["P1", "P2", "all"], help="Minimum rerun priority to export.")
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    image_dir = Path(args.image_dir).resolve() if args.image_dir else None
    review_output = Path(args.review_output).resolve() if args.review_output else input_path.with_name(f"{input_path.stem}_review.csv")
    rerun_output = Path(args.rerun_output).resolve() if args.rerun_output else input_path.with_name(f"{input_path.stem}_rerun_candidates.json")

    records = []
    for row in read_csv_records(input_path):
        row["file_name"] = row.get("file_name") or row.get("filename") or ""
        row["image_path"] = image_path_for(row, image_dir)
        records.append(enrich_result_for_review(row))

    review_output.parent.mkdir(parents=True, exist_ok=True)
    with review_output.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REVIEW_HEADERS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)

    priority_rank = {"": 0, "P2": 1, "P1": 2}
    min_rank = 1 if args.priority == "P2" else 2
    if args.priority == "all":
        min_rank = 1

    rerun_cases = []
    for record in records:
        if priority_rank.get(record.get("rerun_priority", ""), 0) < min_rank:
            continue
        file_path = record.get("image_path") or record.get("file_name")
        if not file_path:
            continue
        rerun_cases.append({
            "name": f"rerun-{Path(file_path).stem}",
            "file": file_path,
            "note": record.get("rerun_reason", ""),
            "previous": {
                "category": record.get("category", ""),
                "view_type": record.get("view_type", ""),
                "quality_issue": record.get("quality_issue", ""),
                "model": record.get("model", ""),
                "price": record.get("price", ""),
            },
        })

    rerun_output.parent.mkdir(parents=True, exist_ok=True)
    rerun_output.write_text(json.dumps(rerun_cases, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"review_csv={review_output}")
    print(f"rerun_json={rerun_output}")
    print(f"records={len(records)}")
    print(f"rerun_candidates={len(rerun_cases)}")


if __name__ == "__main__":
    main()

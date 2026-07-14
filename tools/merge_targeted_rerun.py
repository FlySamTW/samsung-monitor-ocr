#!/usr/bin/env python3
"""Merge targeted rerun results back into original period audit folders."""
import csv
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CANDIDATES_CSV = REPO_ROOT / "targeted_rerun_candidates_2026.csv"
RUNS_DIR = REPO_ROOT / "runs"
AUDIT_DIR = Path(r"D:\00_商化\00_已OCR照片\_ocr_audit")
SUCCESS_HEADERS = [
    "timestamp", "file_name", "category", "view_type", "screen_status",
    "quality_issue", "model", "price", "price_status", "price_symbol",
    "official_price", "price_diff_percent", "duration", "run_id",
    "review_status", "human_category", "human_model", "human_price", "human_notes",
    "ocr_attempt", "auto_retry_reasons", "auto_verified", "auto_review_required",
    "model_validation_failed", "rejected_model", "price_conflict_detected", "thinking",
]


def read_dict_csv(path: Path) -> list:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_dict_csv(path: Path, rows: list, headers: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})


def extract_period(file_name: str) -> str:
    # Files are renamed like M-202605-... or source paths contain 商化照片-202605
    m = re.search(r"\b(2026\d{2})\b", file_name)
    if m:
        return m.group(1)
    return ""


def find_audit_folder(period: str) -> Path:
    if not AUDIT_DIR.exists():
        raise FileNotFoundError(f"Audit dir missing: {AUDIT_DIR}")
    # Prefer exact period token in folder name
    candidates = [p for p in AUDIT_DIR.iterdir() if p.is_dir() and f"_{period}_" in p.name]
    if not candidates:
        # Fallback: period appears anywhere
        candidates = [p for p in AUDIT_DIR.iterdir() if p.is_dir() and period in p.name]
    if not candidates:
        raise FileNotFoundError(f"No audit folder for period {period}")
    # Return latest by mtime
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def find_temp_run() -> Path:
    candidates = [p for p in RUNS_DIR.iterdir() if p.is_dir()]
    if not candidates:
        raise FileNotFoundError(f"No run dir under {RUNS_DIR}")
    # Prefer dirs whose manifest mentions the temp folder, else latest by mtime
    temp_candidates = []
    for p in candidates:
        manifest = p / "manifest.json"
        if manifest.exists():
            try:
                import json
                data = json.loads(manifest.read_text(encoding="utf-8"))
                cfg = data.get("config", "")
                if "targeted_rerun_2026_temp" in str(cfg):
                    temp_candidates.append(p)
            except Exception:
                pass
    if temp_candidates:
        return sorted(temp_candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def main():
    if not CANDIDATES_CSV.exists():
        raise FileNotFoundError(f"Missing candidates CSV: {CANDIDATES_CSV}")

    # Build filename -> period map from candidates CSV
    filename_to_period = {}
    with CANDIDATES_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            source = Path(row["source_path"])
            period = row.get("period") or extract_period(source.name)
            filename_to_period[source.name] = period

    temp_run = find_temp_run()
    results_csv = temp_run / "results.csv"
    if not results_csv.exists():
        raise FileNotFoundError(f"Missing results.csv in {temp_run}")

    new_results = read_dict_csv(results_csv)
    print(f"[INFO] Read {len(new_results)} results from {temp_run}")

    # Group by period
    updates_by_period = {}
    for row in new_results:
        fname = row.get("file_name", "")
        period = filename_to_period.get(fname) or extract_period(fname)
        if not period:
            print(f"[WARN] Cannot determine period for {fname}, skip")
            continue
        updates_by_period.setdefault(period, []).append(row)

    updated_folders = []
    for period, rows in sorted(updates_by_period.items()):
        try:
            audit_folder = find_audit_folder(period)
        except FileNotFoundError as e:
            print(f"[WARN] {e}")
            continue

        success_csv = audit_folder / "success_records.csv"
        backup_csv = audit_folder / f"success_records.csv.targeted_rerun_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        if success_csv.exists():
            shutil.copy2(success_csv, backup_csv)

        existing = {r["file_name"]: r for r in read_dict_csv(success_csv)}
        for row in rows:
            fname = row["file_name"]
            # Convert results.csv fields to success_records.csv schema
            mapped = {h: row.get(h, "") for h in SUCCESS_HEADERS}
            # Fill any missing with existing values
            if fname in existing:
                for h in SUCCESS_HEADERS:
                    if not mapped.get(h):
                        mapped[h] = existing[fname].get(h, "")
            # Update timestamp and run_id to reflect rerun
            mapped["timestamp"] = row.get("timestamp", datetime.now().isoformat())
            mapped["run_id"] = row.get("run_id", temp_run.name)
            existing[fname] = mapped

        write_dict_csv(success_csv, list(existing.values()), SUCCESS_HEADERS)
        updated_folders.append((period, audit_folder.name, len(rows)))
        print(f"[OK] Updated {audit_folder.name} with {len(rows)} records")

    print("\n[INFO] Updated audit folders:")
    for period, folder, count in updated_folders:
        print(f"  {period} -> {folder}: {count} records")


if __name__ == "__main__":
    main()

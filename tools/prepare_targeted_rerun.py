#!/usr/bin/env python3
"""Prepare temp folder for targeted rerun of null-model records."""
import csv
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = REPO_ROOT / "targeted_rerun_candidates_2026.csv"
TEMP_DIR = REPO_ROOT / "targeted_rerun_2026_temp"


def main():
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"Missing {CSV_PATH}")

    # Clean and recreate temp dir
    if TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    copied = 0
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            source = Path(row["source_path"])
            # [v19.8] Some periods store photos in subfolders; search by filename.
            if not source.exists():
                period_folder = source.parent
                if period_folder.exists():
                    matches = list(period_folder.rglob(source.name))
                    if matches:
                        source = matches[0]
            if not source.exists():
                print(f"[WARN] Source missing, skip: {source}")
                continue
            dest = TEMP_DIR / source.name
            # Avoid collisions by appending counter if needed
            counter = 1
            original_dest = dest
            while dest.exists():
                stem = original_dest.stem
                suffix = original_dest.suffix
                dest = TEMP_DIR / f"{stem}_{counter:03d}{suffix}"
                counter += 1
            shutil.copy2(source, dest)
            copied += 1

    print(f"[OK] Copied {copied} files to {TEMP_DIR}")


if __name__ == "__main__":
    main()

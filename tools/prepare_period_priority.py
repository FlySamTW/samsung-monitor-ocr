#!/usr/bin/env python3
"""Prepare one newly-arrived period without switching the live OCR backend.

The command copies a source period into its own immutable staging leaf, writes
the source identity map used by the runtime/upload contract, and atomically
adds the period to folder_discovery.csv. It never calls the backend API.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path


SUPPORTED = {".jpg", ".jpeg", ".png"}
DISCOVERY_FIELDS = [
    "order",
    "folder_id",
    "folder",
    "period",
    "image_count",
    "latest_mtime",
    "source_inventory_sha256",
]
CANDIDATE_FIELDS = [
    "period",
    "audit_folder",
    "source_folder",
    "source_path",
    "file_name",
    "reason",
    "view_type",
    "category",
    "model",
    "price",
    "price_status",
]


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", value).strip("_") or "period"


def _write_json_atomic(path: Path, payload: dict) -> None:
    temp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _write_csv_atomic(path: Path, rows: list[dict], fields: list[str]) -> None:
    temp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp, path)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _resolved_child(root: Path, child: Path) -> Path:
    root = root.resolve()
    child = child.resolve()
    try:
        child.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"source folder is outside source root: {child}") from exc
    return child


def _discovery_rows(
    discovery_path: Path,
    source_root: Path,
    source_folder: Path,
    period: str,
    image_count: int,
    latest_mtime: float,
) -> list[dict]:
    rows = _read_csv(discovery_path)
    source_key = str(source_folder).casefold()
    retained = [
        row for row in rows
        if str(Path(str(row.get("folder") or "")).resolve()).casefold() != source_key
    ]
    relative = source_folder.relative_to(source_root).as_posix()
    current = {
        "order": 0,
        "folder_id": _sha256_text(relative.casefold()),
        "folder": str(source_folder),
        "period": period,
        "image_count": image_count,
        "latest_mtime": datetime.fromtimestamp(latest_mtime).isoformat(timespec="seconds"),
        "source_inventory_sha256": "",
    }
    combined = [current, *retained]
    combined.sort(
        key=lambda row: (
            int(str(row.get("period") or "0")) if str(row.get("period") or "").isdigit() else 0,
            str(row.get("folder") or "").casefold(),
        ),
        reverse=True,
    )
    for index, row in enumerate(combined, start=1):
        row["order"] = index
        for field in DISCOVERY_FIELDS:
            row.setdefault(field, "")
    return combined


def prepare(
    source_root: Path,
    source_folder: Path,
    output_dir: Path,
    period: str,
    *,
    execute: bool,
    stamp: str | None = None,
) -> dict:
    if not re.fullmatch(r"20\d{4}", period):
        raise ValueError(f"invalid period: {period}")
    source_root = source_root.resolve()
    source_folder = _resolved_child(source_root, source_folder)
    output_dir = output_dir.resolve()
    if not source_folder.is_dir():
        raise FileNotFoundError(source_folder)
    if period not in source_folder.name:
        raise ValueError(f"period {period} is not present in source folder name")

    images = sorted(
        (
            item for item in source_folder.iterdir()
            if item.is_file() and item.suffix.lower() in SUPPORTED
        ),
        key=lambda item: item.name.casefold(),
    )
    if not images:
        raise ValueError(f"no supported images: {source_folder}")
    duplicate_names = {item.name for item in images if sum(other.name == item.name for other in images) > 1}
    if duplicate_names:
        raise ValueError(f"duplicate filenames: {sorted(duplicate_names)[:3]}")

    relative = source_folder.relative_to(source_root).as_posix()
    digest12 = _sha256_text(relative.casefold())[:12]
    digest8 = digest12[:8]
    stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    audit_dir = output_dir / "_ocr_audit" / f"{period}_{digest12}_{_safe_name(source_folder.name)[:80]}"
    staging_dir = (
        output_dir
        / "_ocr_staging"
        / "period_priority"
        / stamp
        / f"{period}_{_safe_name(source_folder.name)}_{digest8}"
    )
    candidates_path = audit_dir / f"period_priority_candidates_{period}.csv"
    discovery_path = output_dir / "_ocr_audit" / "folder_discovery.csv"
    total_bytes = sum(item.stat().st_size for item in images)
    planned = {
        "schema": "samsung-ocr-period-priority/v1",
        "complete": False,
        "period": period,
        "source_root": str(source_root),
        "source_folder": str(source_folder),
        "image_count": len(images),
        "total_bytes": total_bytes,
        "audit_folder": str(audit_dir),
        "staging_dir": str(staging_dir),
        "candidate_csv": str(candidates_path),
        "discovery_csv": str(discovery_path),
        "execute": execute,
    }
    if not execute:
        return planned
    if staging_dir.exists():
        raise FileExistsError(f"staging directory already exists: {staging_dir}")

    audit_dir.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(parents=True, exist_ok=False)
    incomplete = staging_dir / ".period_priority_incomplete"
    incomplete.write_text(datetime.now().isoformat(), encoding="utf-8")
    source_map: dict[str, dict[str, str]] = {}
    candidates: list[dict] = []
    try:
        for source in images:
            target = staging_dir / source.name
            part = target.with_name(f".{target.name}.part")
            shutil.copy2(source, part)
            if part.stat().st_size != source.stat().st_size:
                raise OSError(f"copy size mismatch: {source}")
            os.replace(part, target)
            original = str(source.resolve())
            source_map[target.name] = {
                "source_item_id": _sha256_text(original.casefold()),
                "original_source_path": original,
                "period": period,
                "audit_folder": str(audit_dir),
            }
            candidates.append({
                "period": period,
                "audit_folder": str(audit_dir),
                "source_folder": str(source_folder),
                "source_path": original,
                "file_name": source.name,
                "reason": "new_period_priority",
            })

        map_path = staging_dir / ".ocr_source_map.json"
        _write_json_atomic(map_path, {"version": 1, "items": source_map})
        _write_csv_atomic(candidates_path, candidates, CANDIDATE_FIELDS)
        discovery_rows = _discovery_rows(
            discovery_path,
            source_root,
            source_folder,
            period,
            len(images),
            max(item.stat().st_mtime for item in images),
        )
        discovery_path.parent.mkdir(parents=True, exist_ok=True)
        _write_csv_atomic(discovery_path, discovery_rows, DISCOVERY_FIELDS)
        map_sha256 = hashlib.sha256(map_path.read_bytes()).hexdigest()
        manifest = {
            **planned,
            "complete": True,
            "execute": True,
            "source_map": str(map_path),
            "source_map_sha256": map_sha256,
            "completed_at": datetime.now().isoformat(timespec="seconds"),
        }
        _write_json_atomic(staging_dir / ".period_priority_manifest.json", manifest)
        incomplete.unlink()
        return manifest
    except Exception:
        for part in staging_dir.glob(".*.part"):
            part.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare a newly-arrived period without switching live OCR.")
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--source-folder", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--period", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    result = prepare(
        Path(args.source_root),
        Path(args.source_folder),
        Path(args.output_dir),
        args.period,
        execute=args.execute,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

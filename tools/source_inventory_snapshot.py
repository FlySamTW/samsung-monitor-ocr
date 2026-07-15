"""Frozen, per-photo source inventory for historical recursive OCR."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


SCHEMA = "samsung-ocr-source-inventory/v1"
CSV_NAME = "source_inventory_v1.csv"
SUMMARY_NAME = "source_inventory_v1.json"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png"}
UNSUPPORTED_EXTENSIONS = {".heic", ".heif", ".webp"}
CSV_FIELDS = [
    "folder_id", "folder", "period", "relative_path", "size_bytes", "mtime_ns", "content_sha256",
]


class SourceInventoryError(RuntimeError):
    pass


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_folder_id(source_root: Path, folder: Path) -> str:
    relative = folder.resolve().relative_to(source_root.resolve()).as_posix().casefold()
    return hashlib.sha256(relative.encode("utf-8")).hexdigest()


def _period(folder: Path, newest_mtime_ns: int) -> str:
    for part in reversed(folder.parts):
        months = re.findall(r"20\d{4}", part)
        if months:
            return months[-1]
        years = re.findall(r"20\d{2}", part)
        if years:
            return years[-1]
    return datetime.fromtimestamp(newest_mtime_ns / 1_000_000_000).strftime("%Y%m")


def scan_source(source_root: Path) -> Tuple[List[dict], List[dict]]:
    """Read every supported file once and bind identity to its actual bytes."""
    source_root = source_root.resolve()
    grouped: Dict[Path, List[Tuple[Path, os.stat_result]]] = defaultdict(list)
    unsupported: List[dict] = []
    for current, directories, filenames in os.walk(source_root):
        directories.sort(key=str.casefold)
        folder = Path(current)
        for name in sorted(filenames, key=str.casefold):
            path = folder / name
            suffix = path.suffix.lower()
            if suffix in SUPPORTED_EXTENSIONS:
                grouped[folder].append((path, path.stat()))
            elif suffix in UNSUPPORTED_EXTENSIONS:
                unsupported.append({
                    "folder": str(folder), "path": str(path), "extension": suffix,
                    "reason": "unsupported_source_format",
                })
    folder_meta = []
    for folder, items in grouped.items():
        newest = max(stat.st_mtime_ns for _path, stat in items)
        folder_meta.append((folder, _period(folder, newest), newest, items))
    folder_meta.sort(
        key=lambda item: (
            int(item[1]) if re.fullmatch(r"20\d{4}", item[1]) else 0,
            item[2], str(item[0]).casefold(),
        ),
        reverse=True,
    )
    rows: List[dict] = []
    for folder, period, _newest, items in folder_meta:
        folder_id = stable_folder_id(source_root, folder)
        for path, stat in sorted(items, key=lambda item: item[0].name.casefold()):
            rows.append({
                "folder_id": folder_id,
                "folder": str(folder.resolve()),
                "period": period,
                "relative_path": path.resolve().relative_to(source_root).as_posix(),
                "size_bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "content_sha256": file_sha256(path),
            })
    return rows, unsupported


def _write_csv_atomic(path: Path, rows: Iterable[dict]) -> None:
    temp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    with temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp, path)


def _write_json_atomic(path: Path, payload: dict) -> None:
    temp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _normalize_row(row: dict) -> dict:
    return {
        "folder_id": str(row.get("folder_id") or ""),
        "folder": str(row.get("folder") or ""),
        "period": str(row.get("period") or ""),
        "relative_path": str(row.get("relative_path") or ""),
        "size_bytes": int(row.get("size_bytes") or 0),
        "mtime_ns": int(row.get("mtime_ns") or 0),
        "content_sha256": str(row.get("content_sha256") or "").lower(),
    }


def _validate_rows(source_root: Path, rows: List[dict]) -> None:
    identities = set()
    for row in rows:
        relative = Path(row["relative_path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise SourceInventoryError(f"unsafe_relative_path:{relative}")
        if row["relative_path"].casefold() in identities:
            raise SourceInventoryError(f"duplicate_relative_path:{relative}")
        identities.add(row["relative_path"].casefold())
        expected_folder = (source_root / relative).parent.resolve()
        if Path(row["folder"]).resolve() != expected_folder:
            raise SourceInventoryError(f"folder_path_mismatch:{relative}")
        if row["folder_id"] != stable_folder_id(source_root, expected_folder):
            raise SourceInventoryError(f"folder_id_mismatch:{relative}")
        if not re.fullmatch(r"[0-9a-f]{64}", row["content_sha256"]):
            raise SourceInventoryError(f"invalid_content_sha256:{relative}")
        if row["size_bytes"] < 0 or row["mtime_ns"] <= 0:
            raise SourceInventoryError(f"invalid_file_metadata:{relative}")


def write_snapshot(audit_dir: Path, source_root: Path, rows: List[dict]) -> dict:
    audit_dir.mkdir(parents=True, exist_ok=True)
    csv_path = audit_dir / CSV_NAME
    summary_path = audit_dir / SUMMARY_NAME
    normalized = [_normalize_row(row) for row in rows]
    _validate_rows(source_root.resolve(), normalized)
    _write_csv_atomic(csv_path, normalized)
    summary = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "source_root": str(source_root.resolve()),
        "inventory_csv_path": str(csv_path.resolve()),
        "inventory_csv_sha256": file_sha256(csv_path),
        "row_count": len(normalized),
        "folder_count": len({row["folder_id"] for row in normalized}),
    }
    _write_json_atomic(summary_path, summary)
    return summary


def load_snapshot(audit_dir: Path, source_root: Path) -> Tuple[dict, List[dict]]:
    csv_path = audit_dir / CSV_NAME
    summary_path = audit_dir / SUMMARY_NAME
    if not csv_path.is_file() or not summary_path.is_file():
        raise SourceInventoryError("source_inventory_snapshot_incomplete")
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = [_normalize_row(row) for row in csv.DictReader(handle)]
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError, csv.Error) as exc:
        raise SourceInventoryError(f"source_inventory_unreadable:{exc}") from exc
    if summary.get("schema") != SCHEMA:
        raise SourceInventoryError("source_inventory_schema_mismatch")
    if os.path.normcase(str(Path(str(summary.get("source_root") or ".")).resolve())) != os.path.normcase(str(source_root.resolve())):
        raise SourceInventoryError("source_inventory_root_mismatch")
    if summary.get("inventory_csv_sha256") != file_sha256(csv_path):
        raise SourceInventoryError("source_inventory_csv_hash_mismatch")
    if int(summary.get("row_count") or -1) != len(rows):
        raise SourceInventoryError("source_inventory_row_count_mismatch")
    if int(summary.get("folder_count") or -1) != len({row["folder_id"] for row in rows}):
        raise SourceInventoryError("source_inventory_folder_count_mismatch")
    _validate_rows(source_root.resolve(), rows)
    return summary, rows


def compare_rows(expected: List[dict], actual: List[dict]) -> List[str]:
    expected_map = {row["relative_path"].casefold(): _normalize_row(row) for row in expected}
    actual_map = {row["relative_path"].casefold(): _normalize_row(row) for row in actual}
    errors = []
    for identity in sorted(expected_map.keys() - actual_map.keys()):
        errors.append(f"source_missing:{expected_map[identity]['relative_path']}")
    for identity in sorted(actual_map.keys() - expected_map.keys()):
        errors.append(f"source_added_or_renamed:{actual_map[identity]['relative_path']}")
    for identity in sorted(expected_map.keys() & actual_map.keys()):
        if expected_map[identity] != actual_map[identity]:
            errors.append(f"source_content_or_metadata_changed:{expected_map[identity]['relative_path']}")
    return errors


def ensure_frozen_snapshot(audit_dir: Path, source_root: Path) -> Tuple[dict, List[dict], List[dict]]:
    csv_path = audit_dir / CSV_NAME
    summary_path = audit_dir / SUMMARY_NAME
    if not csv_path.exists() and not summary_path.exists():
        current_rows, unsupported = scan_source(source_root)
        write_snapshot(audit_dir, source_root, current_rows)
        summary, rows = load_snapshot(audit_dir, source_root)
        return summary, rows, unsupported
    summary, rows = load_snapshot(audit_dir, source_root)
    current_rows, unsupported = scan_source(source_root)
    errors = compare_rows(rows, current_rows)
    if errors:
        raise SourceInventoryError(";".join(errors[:20]))
    return summary, rows, unsupported


def folder_rows(rows: List[dict]) -> List[dict]:
    grouped: Dict[str, List[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["folder_id"]].append(row)
    result = []
    for group in grouped.values():
        newest_ns = max(row["mtime_ns"] for row in group)
        first = group[0]
        result.append({
            "folder_id": first["folder_id"],
            "folder": Path(first["folder"]),
            "period": first["period"],
            "image_count": len(group),
            "latest_mtime": newest_ns / 1_000_000_000,
        })
    result.sort(
        key=lambda row: (
            int(row["period"]) if re.fullmatch(r"20\d{4}", row["period"]) else 0,
            row["latest_mtime"], str(row["folder"]).casefold(),
        ),
        reverse=True,
    )
    return result


def verify_folder(source_root: Path, inventory_rows: List[dict], folder_id: str) -> List[str]:
    expected = [row for row in inventory_rows if row["folder_id"] == folder_id]
    if not expected:
        return [f"inventory_folder_missing:{folder_id}"]
    folder = Path(expected[0]["folder"])
    actual = []
    for path in sorted(folder.iterdir(), key=lambda item: item.name.casefold()):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            stat = path.stat()
            actual.append({
                "folder_id": stable_folder_id(source_root, folder),
                "folder": str(folder.resolve()),
                "period": expected[0]["period"],
                "relative_path": path.resolve().relative_to(source_root.resolve()).as_posix(),
                "size_bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "content_sha256": file_sha256(path),
            })
    return compare_rows(expected, actual)


def verify_all(source_root: Path, inventory_rows: List[dict]) -> List[str]:
    current_rows, _unsupported = scan_source(source_root)
    return compare_rows(inventory_rows, current_rows)

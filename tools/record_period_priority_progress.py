"""Persist a processed period-priority OCR batch into the global progress ledger.

The period-priority runner uses an isolated staging directory, so completing
that batch does not automatically add a row to ``folder_summary.csv``.  The
Dashboard reads that ledger for the all-project OCR counter.  This tool closes
only that accounting gap: it never claims a Drive upload and refuses to write
unless the source map, frozen source folder, staged images, and one durable
processed task per source are an exact set match.  Current-guard finality is
reported separately and never inferred from the processed count.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


MIN_GUARD_REVISION = 2026071852
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
SUMMARY_FIELDS = [
    "folder",
    "period",
    "image_count",
    "source_latest_mtime",
    "success_records",
    "status",
    "copied_count",
    "missing_result",
    "missing_source",
    "conflict",
    "ready",
    "no_change",
    "copy_error",
    "processed",
    "success",
    "failed",
    "plan_path",
    "copied_path",
    "start_response",
]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _guard_revision(value: Any) -> int:
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    return int(digits) if digits else 0


def _normalized(path: Path | str) -> Path:
    return Path(path).resolve()


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _atomic_csv(
    path: Path,
    *,
    fields: list[str],
    rows: list[dict[str, str]],
) -> None:
    fd, raw_tmp = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _task_name(task: dict[str, Any]) -> str:
    return Path(str((task.get("data") or {}).get("image") or "")).name


def _load_verified_tasks(
    staging_dir: Path,
) -> tuple[
    dict[str, dict[str, Any]],
    list[Path],
    list[dict[str, str]],
    list[dict[str, Any]],
    int,
]:
    tasks: dict[str, dict[str, Any]] = {}
    stale_guard_tasks: list[dict[str, str]] = []
    nonfinal_tasks: list[dict[str, Any]] = []
    current_guard_final_tasks = 0
    result_paths = sorted(staging_dir.glob("*OCR成功.json"))
    if not result_paths:
        raise RuntimeError("period-priority staging has no success result files")
    for result_path in result_paths:
        payload = _read_json(result_path)
        if not isinstance(payload, list):
            raise RuntimeError(f"result payload is not a task list: {result_path}")
        for task in payload:
            if not isinstance(task, dict):
                raise RuntimeError(f"result payload contains a non-object task: {result_path}")
            name = _task_name(task)
            if not name or name in tasks:
                raise RuntimeError(f"missing or duplicate terminal task: {name or result_path}")
            meta = dict((task.get("data") or {}).get("ocr_meta") or {})
            revision = str(meta.get("evidence_guard_revision") or "")
            revision_is_current = _guard_revision(revision) >= MIN_GUARD_REVISION
            blockers: list[str] = []
            if meta.get("auto_verified") is not True:
                blockers.append("auto_verified_false")
            if meta.get("auto_review_required") is True:
                blockers.append("auto_review_required")
            if meta.get("evidence_contract_valid") is not True:
                blockers.append("evidence_contract_invalid")
            if not revision_is_current:
                blockers.append("stale_evidence_guard")
                stale_guard_tasks.append(
                    {
                        "file_name": name,
                        "evidence_guard_revision": revision,
                    }
                )
            if blockers:
                nonfinal_tasks.append(
                    {
                        "file_name": name,
                        "evidence_guard_revision": revision,
                        "reasons": blockers,
                    }
                )
            else:
                current_guard_final_tasks += 1
            tasks[name] = task
    return (
        tasks,
        result_paths,
        stale_guard_tasks,
        nonfinal_tasks,
        current_guard_final_tasks,
    )


def record_progress(
    *,
    output_dir: Path,
    staging_dir: Path,
    source_folder: Path,
    period: str,
    apply: bool,
) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    staging_dir = staging_dir.resolve()
    source_folder = source_folder.resolve()
    if not re.fullmatch(r"20\d{4}", period):
        raise RuntimeError(f"invalid period: {period}")
    if not staging_dir.is_dir() or not source_folder.is_dir():
        raise RuntimeError("staging or source folder is missing")

    source_map_path = staging_dir / ".ocr_source_map.json"
    source_map = _read_json(source_map_path)
    source_items = dict(source_map.get("items") or {})
    if not source_items:
        raise RuntimeError("source map is empty")
    source_ids: set[str] = set()
    originals: set[Path] = set()
    for name, raw_info in source_items.items():
        info = dict(raw_info or {})
        source_id = str(info.get("source_item_id") or "")
        original = _normalized(str(info.get("original_source_path") or ""))
        if (
            not re.fullmatch(r"[0-9a-f]{64}", source_id)
            or source_id in source_ids
            or info.get("period") != period
            or original.parent != source_folder
            or not original.is_file()
            or original in originals
            or not (staging_dir / name).is_file()
        ):
            raise RuntimeError(f"invalid source-map binding: {name}")
        source_ids.add(source_id)
        originals.add(original)

    staged_names = {
        path.name
        for path in staging_dir.iterdir()
        if path.is_file() and path.suffix.casefold() in IMAGE_SUFFIXES
    }
    source_names = {
        path.name
        for path in source_folder.iterdir()
        if path.is_file() and path.suffix.casefold() in IMAGE_SUFFIXES
    }
    mapped_names = set(source_items)
    if staged_names != mapped_names or source_names != mapped_names:
        raise RuntimeError(
            "source map, source folder, and staged image sets are not identical"
        )

    (
        tasks,
        result_paths,
        stale_guard_tasks,
        nonfinal_tasks,
        current_guard_final_tasks,
    ) = _load_verified_tasks(staging_dir)
    if set(tasks) != mapped_names:
        raise RuntimeError("terminal task set does not equal the frozen source map")

    priority_manifest_path = staging_dir / ".period_priority_manifest.json"
    priority_manifest = _read_json(priority_manifest_path)
    if (
        priority_manifest.get("schema") != "samsung-ocr-period-priority/v1"
        or priority_manifest.get("complete") is not True
        or priority_manifest.get("period") != period
        or _normalized(str(priority_manifest.get("source_folder") or ""))
        != source_folder
        or _normalized(str(priority_manifest.get("staging_dir") or ""))
        != staging_dir
        or int(priority_manifest.get("image_count") or 0) != len(mapped_names)
        or _normalized(str(priority_manifest.get("source_map") or ""))
        != source_map_path
        or priority_manifest.get("source_map_sha256") != _sha256_file(source_map_path)
    ):
        raise RuntimeError("period-priority completion manifest is not exact")

    audit_dir = output_dir / "_ocr_audit"
    discovery_path = audit_dir / "folder_discovery.csv"
    summary_path = audit_dir / "folder_summary.csv"
    discovery_fields, discovery_rows = _read_csv(discovery_path)
    del discovery_fields
    discovery_matches = [
        row
        for row in discovery_rows
        if _normalized(str(row.get("folder") or "")) == source_folder
        and str(row.get("period") or "") == period
    ]
    if len(discovery_matches) != 1:
        raise RuntimeError("folder discovery does not contain exactly one matching period")
    discovery = discovery_matches[0]
    expected_count = int(discovery.get("image_count") or 0)
    if (
        expected_count != len(mapped_names)
        or not re.fullmatch(r"[0-9a-f]{64}", str(discovery.get("folder_id") or ""))
    ):
        raise RuntimeError(
            f"discovery/source count mismatch: {expected_count} != {len(mapped_names)}"
        )

    summary_fields, summary_rows = _read_csv(summary_path)
    if summary_fields != SUMMARY_FIELDS:
        raise RuntimeError("folder summary schema does not match the expected ledger")
    matching_indexes = [
        index
        for index, row in enumerate(summary_rows)
        if _normalized(str(row.get("folder") or "")) == source_folder
    ]
    if len(matching_indexes) > 1:
        raise RuntimeError("folder summary contains duplicate source-folder rows")

    manifest_path = (
        audit_dir
        / "period_priority_progress"
        / f"{period}_{next(iter(sorted(source_ids)))[:12]}.json"
    )
    row = {field: "" for field in SUMMARY_FIELDS}
    row.update(
        {
            "folder": str(source_folder),
            "period": period,
            "image_count": str(expected_count),
            "source_latest_mtime": str(discovery.get("latest_mtime") or ""),
            "success_records": "0",
            "status": "period_priority_processed_unexported",
            "copied_count": "0",
            "missing_result": "0",
            "missing_source": "0",
            "conflict": "0",
            "ready": "0",
            "no_change": "0",
            "copy_error": (
                "period-priority staging OCR complete; canonical export/copy/"
                "Drive receipts remain independent; "
                f"current_guard_final={current_guard_final_tasks}; "
                f"nonfinal_tasks={len(nonfinal_tasks)}"
            ),
            "processed": str(expected_count),
            "success": str(expected_count),
            "failed": "0",
            "plan_path": "",
            "copied_path": "",
            "start_response": (
                f"period_priority_manifest={priority_manifest_path};"
                f"sha256={_sha256_file(priority_manifest_path)}"
            ),
        }
    )
    if matching_indexes:
        current = summary_rows[matching_indexes[0]]
        if (
            str(current.get("status") or "") in {"copied", "skipped_existing"}
            and int(current.get("processed") or 0) >= expected_count
        ):
            row = current
        else:
            summary_rows[matching_indexes[0]] = row
    else:
        summary_rows.append(row)

    manifest = {
        "schema": "samsung-ocr-period-priority-progress/v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "period": period,
        "source_folder": str(source_folder),
        "staging_dir": str(staging_dir),
        "source_map": str(source_map_path),
        "source_map_sha256": _sha256_file(source_map_path),
        "period_priority_manifest": str(priority_manifest_path),
        "period_priority_manifest_sha256": _sha256_file(priority_manifest_path),
        "image_count": expected_count,
        "processed_tasks": len(tasks),
        "current_guard_final_tasks": current_guard_final_tasks,
        "stale_guard_tasks": stale_guard_tasks,
        "nonfinal_tasks": nonfinal_tasks,
        "source_item_ids_sha256": hashlib.sha256(
            "\n".join(sorted(source_ids)).encode("utf-8")
        ).hexdigest(),
        "result_files": [
            {
                "path": str(path),
                "sha256": _sha256_file(path),
            }
            for path in result_paths
        ],
        "drive_upload_complete": False,
        "claim": "ocr_progress_only",
    }
    if apply:
        _atomic_json(manifest_path, manifest)
        _atomic_csv(summary_path, fields=summary_fields, rows=summary_rows)
    return {
        "status": "written" if apply else "would_write",
        "period": period,
        "image_count": expected_count,
        "processed_tasks": len(tasks),
        "current_guard_final_tasks": current_guard_final_tasks,
        "nonfinal_tasks": len(nonfinal_tasks),
        "stale_guard_tasks": len(stale_guard_tasks),
        "summary_path": str(summary_path),
        "manifest_path": str(manifest_path),
        "drive_upload_complete": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--source-folder", type=Path, required=True)
    parser.add_argument("--period", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = record_progress(
        output_dir=args.output_dir,
        staging_dir=args.staging_dir,
        source_folder=args.source_folder,
        period=args.period,
        apply=args.apply,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

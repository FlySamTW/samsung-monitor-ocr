"""Defer historical per-photo upload jobs while the current-year gate is open.

The operation is lossless and auditable: each immutable pending job is moved
to an archive with its original SHA-256. Nothing is deleted or rewritten.
Dry-run is the default.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

import psutil


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _live_stream_workers() -> list[int]:
    workers: list[int] = []
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            command = " ".join(process.info.get("cmdline") or []).lower()
        except (psutil.Error, OSError):
            continue
        if "stream_drive_upload.py" in command:
            workers.append(int(process.info["pid"]))
    return workers


def defer_jobs(
    output_dir: Path,
    *,
    current_year: str,
    execute: bool,
) -> dict:
    output_dir = output_dir.resolve()
    pending_dir = output_dir / "_drive_upload_stream" / "pending"
    workers = _live_stream_workers()
    if workers:
        raise RuntimeError(f"stream upload worker is active: {workers}")

    selected: list[dict] = []
    for path in sorted(pending_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        period = str(payload.get("period") or "")
        source_item_id = str(payload.get("source_item_id") or "")
        if not period.startswith("20") or len(period) != 6:
            raise RuntimeError(f"invalid pending period: {path}")
        if period.startswith(current_year):
            continue
        if path.stem != source_item_id:
            raise RuntimeError(f"pending source identity mismatch: {path}")
        selected.append(
            {
                "source": str(path),
                "file_name": path.name,
                "source_item_id": source_item_id,
                "period": period,
                "sha256": _sha256(path),
            }
        )

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive = (
        output_dir
        / "_ocr_audit"
        / "deferred_historical_stream_jobs"
        / stamp
    )
    manifest = {
        "schema": "samsung-ocr-deferred-stream-jobs/v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "current_year": current_year,
        "execute": execute,
        "selected_count": len(selected),
        "archive": str(archive),
        "jobs": selected,
    }
    if execute:
        archive_pending = archive / "pending"
        archive_pending.mkdir(parents=True, exist_ok=False)
        for item in selected:
            source = Path(item["source"])
            target = archive_pending / item["file_name"]
            os.replace(source, target)
            if _sha256(target) != item["sha256"]:
                raise RuntimeError(f"archived job hash mismatch: {target}")
            item["archived_path"] = str(target)
        _atomic_json(archive / "manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--current-year", default=str(datetime.now().year))
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    result = defer_jobs(
        args.output_dir,
        current_year=args.current_year,
        execute=args.execute,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

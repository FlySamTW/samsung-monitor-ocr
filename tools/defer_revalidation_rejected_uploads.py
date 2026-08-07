"""Hold stale upload jobs that a current guard revalidation rejected.

The stream uploader must fail closed on an unknown evidence revision, but one
stale job must not prevent unrelated current-revision jobs from uploading.
This helper accepts only an applied frozen-guard revalidation manifest, proves
the exact source binding for every affected old-revision job, and atomically
moves those jobs out of the active outbox.  It never calls a model, contacts
Drive, changes an OCR result, or resets a retry budget.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.audit_fields import EVIDENCE_GUARD_REVISION
from tools.stream_drive_upload import STREAM_SCHEMA


MANIFEST_SCHEMA = "samsung-ocr-frozen-guard-revalidation/v1"
HOLD_SCHEMA = "samsung-ocr-revalidation-upload-hold/v1"
ALLOWED_DISPOSITIONS = {
    ("queued_with_preserved_budget", ""),
    ("not_queued", "three_call_hard_limit_reached"),
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def defer_rejected_uploads(
    *,
    manifest_path: Path,
    output_dir: Path,
    old_revision: str,
    apply: bool = False,
) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    output_dir = output_dir.resolve()
    manifest = _read_json(manifest_path)
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != MANIFEST_SCHEMA
        or manifest.get("mode") != "apply"
        or str(manifest.get("old_revision") or "") != old_revision
        or str(manifest.get("current_revision") or "")
        != EVIDENCE_GUARD_REVISION
    ):
        raise RuntimeError("revalidation manifest is not an applied current revision")

    rejected_by_name: dict[str, dict[str, Any]] = {}
    for raw in manifest.get("rejected") or []:
        if not isinstance(raw, dict):
            raise RuntimeError("revalidation manifest has an invalid rejected row")
        disposition = str(raw.get("rerun_disposition") or "")
        blocked = str(raw.get("rerun_blocked_reason") or "")
        if (disposition, blocked) not in ALLOWED_DISPOSITIONS:
            continue
        name = str(raw.get("file_name") or "")
        if not name or Path(name).name != name or name in rejected_by_name:
            raise RuntimeError("revalidation rejected filename is invalid or duplicated")
        rejected_by_name[name] = raw

    root = output_dir / "_drive_upload_stream"
    pending_dir = root / "pending"
    hold_dir = root / "revalidation_holds"
    candidates: list[dict[str, Any]] = []
    unapproved: list[str] = []
    for job_path in sorted(pending_dir.glob("*.json")):
        job = _read_json(job_path)
        if str(job.get("evidence_guard_revision") or "") != old_revision:
            continue
        if job.get("schema") != STREAM_SCHEMA:
            raise RuntimeError(f"stale upload job schema is invalid: {job_path.name}")
        source_id = str(job.get("source_item_id") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", source_id) or job_path.stem != source_id:
            raise RuntimeError(f"stale upload source identity mismatch: {job_path.name}")
        original = Path(str(job.get("original_source_path") or "")).resolve()
        file_name = original.name
        rejected = rejected_by_name.get(file_name)
        if rejected is None:
            unapproved.append(file_name or job_path.name)
            continue
        if (
            not original.is_file()
            or _sha256(original) != str(job.get("source_sha256") or "")
        ):
            raise RuntimeError(f"stale upload source bytes changed: {file_name}")
        candidates.append(
            {
                "job_path": str(job_path),
                "source_item_id": source_id,
                "file_name": file_name,
                "target_name": str(job.get("target_name") or ""),
                "rerun_disposition": str(rejected.get("rerun_disposition") or ""),
                "rerun_blocked_reason": str(
                    rejected.get("rerun_blocked_reason") or ""
                ),
            }
        )
    if unapproved:
        raise RuntimeError(
            "old-revision pending jobs lack an approved revalidation rejection: "
            + ",".join(sorted(unapproved)[:10])
        )

    report: dict[str, Any] = {
        "schema": HOLD_SCHEMA,
        "generated_at": datetime.now().astimezone().isoformat(),
        "mode": "apply" if apply else "dry_run",
        "revalidation_manifest": str(manifest_path),
        "old_revision": old_revision,
        "current_revision": EVIDENCE_GUARD_REVISION,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }
    if not apply:
        return report

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    hold_dir.mkdir(parents=True, exist_ok=True)
    moved: list[dict[str, Any]] = []
    skipped_after_current_replacement: list[str] = []
    for candidate in candidates:
        source = Path(candidate["job_path"])
        if not source.exists():
            skipped_after_current_replacement.append(candidate["source_item_id"])
            continue
        live_job = _read_json(source)
        if (
            str(live_job.get("evidence_guard_revision") or "")
            != old_revision
            or str(live_job.get("source_item_id") or "").strip().lower()
            != candidate["source_item_id"]
            or str(live_job.get("target_name") or "") != candidate["target_name"]
        ):
            skipped_after_current_replacement.append(candidate["source_item_id"])
            continue
        target = hold_dir / (
            f"{candidate['source_item_id']}.{old_revision}.{stamp}.json"
        )
        if target.exists():
            raise RuntimeError(f"revalidation hold target already exists: {target.name}")
        os.replace(source, target)
        moved.append({**candidate, "hold_path": str(target)})
    report["moved_count"] = len(moved)
    report["moved"] = moved
    report["skipped_after_current_replacement"] = (
        skipped_after_current_replacement
    )
    receipt = hold_dir / f"hold_{stamp}.manifest.json"
    report["receipt"] = str(receipt)
    _atomic_json(receipt, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--old-revision", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = defer_rejected_uploads(
        manifest_path=args.manifest,
        output_dir=args.output_dir,
        old_revision=args.old_revision,
        apply=args.apply,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

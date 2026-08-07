"""Move one legacy lifetime-cap fuse into the zero-model adjudication queue.

This is deliberately a narrow state migration.  It proves that the fused
photo is the same staged/original/prepared image bound to an exhausted
lifetime-call ledger, proves that no verified result already exists, writes
the durable capped-adjudication queue, archives the fuse, and only then removes
the active fuse.  It never writes an OCR result, upload job, or ledger entry,
and never contacts LM Studio or any running service.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.image_processing import ImageProcessor
from skills.model_call_ledger import (
    LEDGER_SCHEMA,
    MAX_LIFETIME_MODEL_CALLS,
    build_source_image_binding,
)


QUEUE_SCHEMA = "samsung-ocr-capped-adjudication-queue/v1"
FUSE_SCHEMA = "samsung-ocr-runtime-health-fuse/v1"
FUSE_REASON = "lifetime_model_call_ledger_blocked"
PHOTO_LOCAL_EXHAUSTED_FUSE_REASONS = {
    FUSE_REASON,
    "structured_narration_invalid",
    "structured_authority_material_conflict:model",
}
CLEARANCE_RULE = "lifetime_cap_deferred_without_model_call"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read trusted JSON: {path}: {exc}") from exc


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


@contextmanager
def _exclusive_lock(path: Path, timeout_seconds: float = 15.0) -> Iterator[None]:
    """Serialize queue migration with another invocation of this tool."""

    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.1, float(timeout_seconds))
    with path.open("a+b", buffering=0) as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        acquired = False
        while not acquired:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"timed out acquiring migration lock: {path}")
                time.sleep(0.025)
        try:
            yield
        finally:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


def _same_path(left: object, right: object) -> bool:
    try:
        return os.path.normcase(str(Path(str(left)).resolve())) == os.path.normcase(
            str(Path(str(right)).resolve())
        )
    except (OSError, ValueError):
        return False


def _prepared_input_sha256(image_path: Path) -> str:
    """Reproduce the production full-frame bytes without making a model call."""

    processor = ImageProcessor(
        {
            "max_size": None,
            "max_dimensions": (2560, 1440),
            "detect_label_card": False,
            "auto_high_res_crops": False,
            "bottom_label_strip": False,
            "bottom_center_zoom": False,
        }
    )
    processed = processor.process(str(image_path), evidence_attempt=1)
    if not isinstance(processed, dict) or not processed.get("base64"):
        raise RuntimeError("cannot reproduce the prepared full-image binding")
    try:
        prepared = base64.b64decode(str(processed["base64"]), validate=True)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("prepared full-image bytes are invalid") from exc
    return hashlib.sha256(prepared).hexdigest()


def _verified_result_paths(staging_dir: Path, file_name: str) -> list[str]:
    matches: list[str] = []
    for result_path in sorted(staging_dir.glob("*OCR*.json")):
        payload = _read_json(result_path)
        if not isinstance(payload, list):
            continue
        for row in payload:
            if not isinstance(row, dict):
                continue
            data = row.get("data") or {}
            if Path(str(data.get("image") or "")).name != file_name:
                continue
            meta = data.get("ocr_meta") or {}
            if meta.get("auto_verified") is True:
                matches.append(str(result_path))
                break
    return matches


def _validated_queue(
    *,
    queue_path: Path,
    staging_dir: Path,
    record: dict[str, Any],
    now: str,
) -> tuple[dict[str, Any], bool]:
    if queue_path.is_file():
        payload = _read_json(queue_path)
        if not isinstance(payload, dict) or payload.get("schema") != QUEUE_SCHEMA:
            raise RuntimeError("capped adjudication queue schema is invalid")
        if not _same_path(payload.get("image_dir"), staging_dir):
            raise RuntimeError("capped adjudication queue belongs to another staging")
        items = payload.get("items")
        if not isinstance(items, list):
            raise RuntimeError("capped adjudication queue items are invalid")
    else:
        payload = {
            "schema": QUEUE_SCHEMA,
            "image_dir": str(staging_dir),
            "items": [],
        }
        items = payload["items"]

    existing: dict[str, Any] | None = None
    for raw in items:
        if not isinstance(raw, dict):
            raise RuntimeError("capped adjudication queue contains a non-object item")
        same_name = str(raw.get("file_name") or "") == record["file_name"]
        same_source = (
            str(raw.get("source_item_id") or "") == record["source_item_id"]
        )
        if same_name or same_source:
            if not (same_name and same_source):
                raise RuntimeError("capped adjudication queue has a binding collision")
            if existing is not None:
                raise RuntimeError("capped adjudication queue has duplicate binding")
            existing = raw

    inserted = existing is None
    if existing is not None:
        required_equal = (
            "source_path",
            "original_source_path",
            "source_item_id",
            "source_file_sha256",
            "input_image_sha256",
            "binding_key",
        )
        for field in required_equal:
            if field.endswith("_path"):
                matches = _same_path(existing.get(field), record[field])
            else:
                matches = str(existing.get(field) or "") == str(record[field])
            if not matches:
                raise RuntimeError(
                    f"existing capped adjudication item conflicts on {field}"
                )
        if (
            str(existing.get("state") or "")
            != "awaiting_zero_model_adjudication"
            or existing.get("verified") is not False
            or existing.get("uploaded") is not False
            or int(existing.get("consumed_calls") or 0)
            < MAX_LIFETIME_MODEL_CALLS
        ):
            raise RuntimeError("existing capped adjudication item is not safely deferred")
        record["queued_at"] = str(existing.get("queued_at") or record["queued_at"])
        items = [
            record if item is existing else item
            for item in items
        ]
    else:
        items.append(record)

    payload["items"] = sorted(
        items,
        key=lambda item: (
            str(item.get("file_name") or ""),
            str(item.get("source_item_id") or ""),
        ),
    )
    payload["updated_at"] = now
    return payload, inserted


def migrate_lifetime_cap_fuse(
    *,
    audit_dir: Path,
    staging_dir: Path,
    apply: bool,
) -> dict[str, Any]:
    audit_dir = audit_dir.resolve()
    staging_dir = staging_dir.resolve()
    if not audit_dir.is_dir():
        raise RuntimeError(f"audit directory is unavailable: {audit_dir}")
    if not staging_dir.is_dir():
        raise RuntimeError(f"staging directory is unavailable: {staging_dir}")

    fuse_path = audit_dir / "runtime_health_fuse.json"
    fuse = _read_json(fuse_path)
    if (
        not isinstance(fuse, dict)
        or fuse.get("schema") != FUSE_SCHEMA
        or fuse.get("active") is not True
        or len(fuse.get("reasons") or []) != 1
        or str((fuse.get("reasons") or [""])[0])
        not in PHOTO_LOCAL_EXHAUSTED_FUSE_REASONS
    ):
        raise RuntimeError(
            "active fuse is not an exhausted photo-local stop eligible for deferral"
        )

    file_name = str(fuse.get("source_file") or "").strip()
    if not file_name or Path(file_name).name != file_name:
        raise RuntimeError("active fuse source_file is invalid")
    staged_path = (staging_dir / file_name).resolve()
    if staged_path.parent != staging_dir or not staged_path.is_file():
        raise RuntimeError("active fuse source_file is absent from the specified staging")

    source_map_path = staging_dir / ".ocr_source_map.json"
    source_map = _read_json(source_map_path)
    if (
        not isinstance(source_map, dict)
        or int(source_map.get("version") or 0) != 1
        or not isinstance(source_map.get("items"), dict)
    ):
        raise RuntimeError("source map is missing or unsupported")
    metadata = source_map["items"].get(file_name)
    if not isinstance(metadata, dict):
        raise RuntimeError("source map has no exact entry for the fused photo")

    source_item_id = str(metadata.get("source_item_id") or "").strip().lower()
    if not _SHA256_RE.fullmatch(source_item_id):
        raise RuntimeError("source map source_item_id is incomplete")
    original_source_path = Path(
        str(metadata.get("original_source_path") or "")
    ).resolve()
    if not original_source_path.is_file():
        raise RuntimeError("source map original source image is unavailable")
    if hashlib.sha256(staged_path.read_bytes()).hexdigest() != hashlib.sha256(
        original_source_path.read_bytes()
    ).hexdigest():
        raise RuntimeError("staged photo does not byte-bind to the original source")

    input_image_sha256 = _prepared_input_sha256(staged_path)
    binding = build_source_image_binding(
        source_item_id=source_item_id,
        original_source_path=original_source_path,
        input_image_sha256=input_image_sha256,
    )
    ledger_path = (
        audit_dir
        / "model_call_lifetime_ledger_v1"
        / source_item_id[:2]
        / f"{source_item_id}.json"
    )
    ledger = _read_json(ledger_path)
    if not isinstance(ledger, dict) or ledger.get("schema") != LEDGER_SCHEMA:
        raise RuntimeError("lifetime ledger entry is missing or unsupported")
    for field, expected in binding.as_dict().items():
        actual = ledger.get(field)
        matches = (
            _same_path(actual, expected)
            if field == "original_source_path"
            else str(actual or "") == str(expected)
        )
        if not matches:
            raise RuntimeError(f"lifetime ledger binding mismatch: {field}")
    try:
        consumed_calls = int(ledger.get("reserved_calls"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("lifetime ledger consumed call count is invalid") from exc
    if consumed_calls < MAX_LIFETIME_MODEL_CALLS:
        raise RuntimeError("lifetime ledger has not consumed three calls")

    verified_paths = _verified_result_paths(staging_dir, file_name)
    if verified_paths:
        raise RuntimeError("fused photo already has a verified result")

    now = datetime.now().astimezone().isoformat()
    record = {
        "file_name": file_name,
        "source_path": str(staged_path),
        "original_source_path": str(original_source_path),
        **binding.as_dict(),
        "consumed_calls": consumed_calls,
        "attempt": consumed_calls,
        "run_id": str(fuse.get("run_id") or ""),
        "state": "awaiting_zero_model_adjudication",
        "message": "三次本機模型額度已滿；已移交零模型確定性定案佇列。",
        "error": str((fuse.get("reasons") or [FUSE_REASON])[0]),
        "verified": False,
        "uploaded": False,
        "queued_at": now,
        "updated_at": now,
        "migration": CLEARANCE_RULE,
        "fuse_tripped_at": str(fuse.get("tripped_at") or ""),
    }
    queue_path = staging_dir / ".ocr_capped_adjudication_queue.json"
    queue_payload, inserted = _validated_queue(
        queue_path=queue_path,
        staging_dir=staging_dir,
        record=record,
        now=now,
    )

    fuse_fingerprint = hashlib.sha256(
        json.dumps(
            fuse, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    stamp = f"{source_item_id[:12]}_{fuse_fingerprint[:12]}"
    clearance_path = (
        audit_dir
        / "runtime_health_fuse_clearance"
        / f"deferred_{stamp}.json"
    )
    history_path = (
        audit_dir
        / "runtime_health_fuse_history"
        / f"deferred_{stamp}.json"
    )
    report = {
        "status": "would_defer" if not apply else "deferred",
        "clearance_rule": CLEARANCE_RULE,
        "file_name": file_name,
        "source_item_id": source_item_id,
        "source_file_sha256": binding.source_file_sha256,
        "input_image_sha256": binding.input_image_sha256,
        "binding_key": binding.binding_key,
        "model_calls_consumed": consumed_calls,
        "fourth_call_authorized": False,
        "queue_item_inserted": inserted,
        "queue_path": str(queue_path),
        "ledger_path": str(ledger_path),
        "result_written": False,
        "upload_written": False,
        "ledger_written": False,
        "service_touched": False,
    }
    if not apply:
        return report

    lock_path = staging_dir / ".ocr_capped_adjudication_queue.lock"
    with _exclusive_lock(lock_path):
        if _read_json(fuse_path) != fuse:
            raise RuntimeError("active fuse changed during migration")
        # Re-read the queue under the lock so another migration cannot be lost.
        queue_payload, inserted = _validated_queue(
            queue_path=queue_path,
            staging_dir=staging_dir,
            record=record,
            now=now,
        )
        report["queue_item_inserted"] = inserted
        _atomic_json(queue_path, queue_payload)

        cleared_at = datetime.now().astimezone().isoformat()
        clearance = {
            **report,
            "cleared_at": cleared_at,
            "fuse_fingerprint": fuse_fingerprint,
            "fuse_history": str(history_path),
        }
        _atomic_json(clearance_path, clearance)
        _atomic_json(
            history_path,
            {
                **fuse,
                "active": False,
                "cleared_at": cleared_at,
                "clearance": CLEARANCE_RULE,
                "clearance_receipt": str(clearance_path),
                "queue_path": str(queue_path),
                "source_item_id": source_item_id,
                "source_file_sha256": binding.source_file_sha256,
                "input_image_sha256": binding.input_image_sha256,
                "binding_key": binding.binding_key,
                "model_calls_consumed": consumed_calls,
                "fourth_call_authorized": False,
            },
        )
        if _read_json(fuse_path) != fuse:
            raise RuntimeError("active fuse changed before final unlink")
        fuse_path.unlink()

    return {
        **report,
        "cleared_at": cleared_at,
        "clearance_receipt": str(clearance_path),
        "fuse_history": str(history_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            migrate_lifetime_cap_fuse(
                audit_dir=args.audit_dir,
                staging_dir=args.staging_dir,
                apply=args.apply,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

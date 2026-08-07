"""Resolve the lifetime-call-cap queue from existing bound evidence only.

This is a deterministic background resolver.  It never imports an LM client,
never calls a model, and defaults to a read-only dry run.  For each queued
photo it:

1. scans the complete trace history and records every binding/health anomaly;
2. selects the latest three clean, distinct request-bound observations for the
   current production input-image hash;
3. delegates the business decision to the established three-pass finalizer;
4. on ``--apply``, enqueues every accepted upload first, atomically appends the
   verified terminal results second, and atomically removes only those exact
   queue bindings last.

Unsupported or insufficient evidence remains in the queue with a durable
reason.  A fourth model call is neither made nor authorized.

``--apply`` additionally requires the exact port-5002 backend to report an
idle, source-bound ``pipeline_pause``.  The backend must be reloaded before
resume so its in-memory queue/results rehydrate from these atomic files.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.audit_fields import (
    EVIDENCE_CONTRACT_VERSION,
    EVIDENCE_GUARD_REVISION,
    adjudication_field_invariant_reasons,
    clear_superseded_terminal_content_flags,
    finalize_three_pass_outcome,
    is_followme_model,
    normalize_terminal_quality_issue,
    validate_evidence_contract,
)
from skills.model_call_ledger import build_source_image_binding
from skills.model_validation import normalize_model_token
from tools.recover_request_binding_fuse import _result_task
from tools.stream_drive_upload import enqueue_finalized_result


QUEUE_SCHEMA = "samsung-ocr-capped-adjudication-queue/v1"
RECEIPT_SCHEMA = "samsung-ocr-capped-adjudication-resolution/v1"
RESOLUTION_RULE = "latest_three_distinct_clean_bound_requests_majority"
EXHAUSTED_RESOLUTION_RULE = "three_call_exhausted_conservative_terminal"
_PRIOR_ANSWER_PHRASES = (
    "感謝提醒",
    "感謝您的提醒",
    "感謝指正",
    "您指正",
    "你指正",
    "經您提醒",
    "經你提醒",
    "先前答案",
    "先前的答案",
    "上一輪答案",
    "前一輪答案",
    "依照您的指正",
    "依照你的指正",
    "thanks for pointing",
    "you are correct",
    "previous answer",
    "prior answer",
    "as you noted",
)
RECOVERABLE_RAW_HEALTH_REASONS = frozenset(
    {
        # These guards are raised after the immutable structured model output
        # has already been recorded.  They protect presentation/narration or
        # suppress one disputed material field; they do not invalidate the
        # request/image binding itself.  The raw-vote rebuild below keeps the
        # deterministic field suppression before adjudication.
        "ui_narration_contains_raw_structure",
        "structured_narration_followme_conflict",
        "structured_authority_material_conflict:model",
        "distant_followme_strong_evidence_conflict",
    }
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUEST_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _emit_progress(
    progress_fn: Callable[[Mapping[str, Any]], None] | None,
    **payload: Any,
) -> None:
    """Publish best-effort UI progress without joining the data transaction."""

    if progress_fn is None:
        return
    try:
        progress_fn(payload)
    except Exception:
        # This heartbeat is observability only. A temporary UI/status write
        # failure must never corrupt, repeat, or stop deterministic evidence
        # adjudication.
        return


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read trusted JSON: {path}: {exc}") from exc


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
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
def _exclusive_lock(path: Path, timeout_seconds: float = 30.0) -> Iterator[None]:
    """Serialize this resolver with another resolver invocation."""

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
                    raise RuntimeError(f"timed out acquiring resolver lock: {path}")
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _production_input_sha256(image_path: Path) -> str:
    """Reproduce the production full-frame ImageProcessor bytes exactly.

    The production configuration uses ``max_dimensions=(2560, 1440)``.  The
    implementation intentionally turns that into a 2560x2560 thumbnail box.
    Small images retain their original file bytes; only oversized images are
    EXIF-transposed, resized, converted to RGB, and JPEG-encoded at quality 95.
    """

    original = image_path.read_bytes()
    with Image.open(io.BytesIO(original)) as opened:
        image = ImageOps.exif_transpose(opened)
        if max(image.width, image.height) <= 2560:
            model_bytes = original
        else:
            resized = image.copy()
            resized.thumbnail((2560, 2560))
            buffer = io.BytesIO()
            resized.convert("RGB").save(buffer, format="JPEG", quality=95)
            model_bytes = buffer.getvalue()
    return hashlib.sha256(model_bytes).hexdigest()


def _require_quiesced_backend(
    *,
    staging_dir: Path,
    result_file: Path,
    upload_output_dir: Path,
    status_url: str = "http://127.0.0.1:5002/api/status",
) -> dict[str, Any]:
    """Fail closed unless the live writer is paused at a photo boundary.

    The backend owns in-memory copies of the capped queue and result list, so
    an external apply is safe only while `/api/stop` has created a persistent
    pause and the worker reports idle.  The caller must reload that same
    backend before resuming so it rehydrates the newly written files.
    """

    pause_path = upload_output_dir / "_ocr_audit" / "pipeline_pause.json"
    pause = _read_json(pause_path)
    if (
        not isinstance(pause, dict)
        or pause.get("schema") != "samsung-ocr-pipeline-pause/v1"
        or not _same_path(pause.get("current_dir"), staging_dir)
    ):
        raise RuntimeError(
            "apply requires a source-bound pipeline pause created by /api/stop"
        )
    try:
        request = urllib.request.Request(
            status_url,
            headers={"Accept": "application/json"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            status = json.loads(response.read().decode("utf-8"))
    except (
        OSError,
        UnicodeError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        urllib.error.URLError,
    ) as exc:
        raise RuntimeError(f"cannot prove quiesced backend: {exc}") from exc
    if (
        not isinstance(status, dict)
        or status.get("is_running") is not False
        or str(status.get("current_file") or "").strip() not in {"", "None"}
        or not _same_path(status.get("image_dir"), staging_dir)
        or result_file.parent != staging_dir
        or not result_file.is_file()
    ):
        raise RuntimeError(
            "backend is not idle on the exact staging/result binding"
        )
    live_pause = status.get("pipeline_pause") or {}
    if (
        not isinstance(live_pause, dict)
        or live_pause.get("schema") != "samsung-ocr-pipeline-pause/v1"
        or not _same_path(live_pause.get("current_dir"), staging_dir)
    ):
        raise RuntimeError("backend does not report the exact pipeline pause")
    return {
        "status_url": status_url,
        "version": str(status.get("version") or ""),
        "image_dir": str(status.get("image_dir") or ""),
        # API latest_result_file is the last photo filename, not the durable
        # Label Studio JSON path; the target is therefore bound locally.
        "target_result_file": str(result_file),
        "pipeline_pause": dict(live_pause),
        "backend_reload_required_before_resume": True,
    }


def _queue_payload(queue_path: Path, staging_dir: Path) -> dict[str, Any]:
    payload = _read_json(queue_path)
    if not isinstance(payload, dict) or payload.get("schema") != QUEUE_SCHEMA:
        raise RuntimeError("capped adjudication queue schema is invalid")
    if not _same_path(payload.get("image_dir"), staging_dir):
        raise RuntimeError("capped adjudication queue belongs to another staging")
    items = payload.get("items")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise RuntimeError("capped adjudication queue items are invalid")
    seen_names: set[str] = set()
    seen_sources: set[str] = set()
    for item in items:
        name = str(item.get("file_name") or "").strip()
        source_id = str(item.get("source_item_id") or "").strip().lower()
        if (
            not name
            or Path(name).name != name
            or not _SHA256_RE.fullmatch(source_id)
            or name in seen_names
            or source_id in seen_sources
        ):
            raise RuntimeError("capped adjudication queue has invalid/duplicate binding")
        seen_names.add(name)
        seen_sources.add(source_id)
    return payload


def _source_map(staging_dir: Path) -> dict[str, Any]:
    payload = _read_json(staging_dir / ".ocr_source_map.json")
    if (
        not isinstance(payload, dict)
        or int(payload.get("version") or 0) != 1
        or not isinstance(payload.get("items"), dict)
    ):
        raise RuntimeError("source map is missing or unsupported")
    return payload


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _trace_paths_for_source_map(
    primary_trace_path: Path,
    source_items: Mapping[str, Any],
) -> list[Path]:
    """Return every trusted trace shard referenced by this exact staging map.

    Historical passes are stored beside their original audit folder while the
    current backend also appends to the global audit trace.  Reading only the
    global file made valid earlier calls look missing and caused the same
    lifetime-capped photos to be staged again forever.  Audit folders are
    accepted only beneath the primary trace's audit root; an escaped path is a
    source-map integrity failure, not a shard to follow.
    """

    primary = primary_trace_path.resolve()
    if not primary.is_file():
        raise RuntimeError("primary evidence trace is unavailable")
    audit_root = primary.parent.resolve()
    discovered: dict[str, Path] = {os.path.normcase(str(primary)): primary}
    for value in source_items.values():
        if not isinstance(value, Mapping):
            continue
        folder_text = str(value.get("audit_folder") or "").strip()
        if not folder_text:
            continue
        folder = Path(folder_text).resolve()
        if not _is_within(folder, audit_root):
            raise RuntimeError("source map audit_folder escapes evidence audit root")
        shard = (folder / primary.name).resolve()
        if shard.is_file():
            discovered.setdefault(os.path.normcase(str(shard)), shard)
    return [primary] + sorted(
        (path for path in discovered.values() if path != primary),
        key=lambda path: os.path.normcase(str(path)),
    )


def _trace_record(payload: Mapping[str, Any], file_name: str, source_id: str) -> dict[str, Any]:
    row = dict(payload.get("parsed_output") or {})
    row.update(
        {
            "file_name": file_name,
            "source_item_id": source_id,
            "run_id": str(payload.get("run_id") or row.get("run_id") or ""),
            "ocr_attempt": int(
                payload.get("attempt") or row.get("ocr_attempt") or 0
            ),
            "timestamp": str(
                payload.get("timestamp") or row.get("timestamp") or ""
            ),
            "_trace_source_path": str(payload.get("source_path") or ""),
            "_trace_original_source_path": str(
                payload.get("original_source_path") or ""
            ),
            "_trace_raw_objects": list(payload.get("raw_objects") or []),
        }
    )
    return row


def _scan_trace(
    trace_path: Path,
    queue_by_source: Mapping[str, Mapping[str, Any]],
    progress_fn: Callable[[Mapping[str, Any]], None] | None = None,
    phase: str = "trace_scan",
) -> tuple[dict[str, list[dict[str, Any]]], int, int]:
    rows: dict[str, list[dict[str, Any]]] = {
        source_id: [] for source_id in queue_by_source
    }
    scanned = 0
    matched = 0
    with trace_path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            scanned += 1
            if scanned == 1 or scanned % 5000 == 0:
                _emit_progress(
                    progress_fn,
                    phase=phase,
                    processed=scanned,
                    total=0,
                    matched=matched,
                    unit="trace_lines",
                )
            try:
                payload = json.loads(line)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            source_id = str(
                payload.get("source_item_id")
                or payload.get("source_identity")
                or ""
            ).strip().lower()
            item = queue_by_source.get(source_id)
            if not item:
                continue
            file_name = str(item.get("file_name") or "")
            if str(payload.get("file_name") or "") != file_name:
                continue
            matched += 1
            rows[source_id].append(_trace_record(payload, file_name, source_id))
    _emit_progress(
        progress_fn,
        phase=phase,
        processed=scanned,
        total=scanned,
        matched=matched,
        unit="trace_lines",
    )
    return rows, scanned, matched


def _scan_traces(
    trace_paths: list[Path],
    queue_by_source: Mapping[str, Mapping[str, Any]],
    progress_fn: Callable[[Mapping[str, Any]], None] | None = None,
    phase: str = "trace_scan",
) -> tuple[dict[str, list[dict[str, Any]]], int, int]:
    """Scan all trusted shards and remove only byte-equivalent duplicates."""

    combined: dict[str, list[dict[str, Any]]] = {
        source_id: [] for source_id in queue_by_source
    }
    seen: dict[str, set[str]] = {source_id: set() for source_id in queue_by_source}
    scanned = 0
    matched = 0
    for index, trace_path in enumerate(trace_paths, start=1):
        shard_phase = (
            phase
            if len(trace_paths) == 1
            else f"{phase}_{index}_of_{len(trace_paths)}"
        )
        rows, shard_scanned, shard_matched = _scan_trace(
            trace_path,
            queue_by_source,
            progress_fn=progress_fn,
            phase=shard_phase,
        )
        scanned += shard_scanned
        matched += shard_matched
        for source_id, source_rows in rows.items():
            for row in source_rows:
                fingerprint = hashlib.sha256(
                    json.dumps(
                        row,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ).encode("utf-8")
                ).hexdigest()
                if fingerprint in seen[source_id]:
                    continue
                seen[source_id].add(fingerprint)
                combined[source_id].append(row)
    return combined, scanned, matched


def _raw_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    raw_objects = row.get("_trace_raw_objects")
    if not isinstance(raw_objects, list) or len(raw_objects) != 1:
        raise RuntimeError("trace call must contain exactly one raw structured output")
    try:
        payload = json.loads(str(raw_objects[0]))
    except (TypeError, ValueError, json.JSONDecodeError):
        raise RuntimeError("trace raw structured output is not valid JSON")
    if not isinstance(payload, dict):
        raise RuntimeError("trace raw structured output is not an object")
    if isinstance(payload.get("data"), dict):
        payload = dict(payload["data"])
    return payload


def _sanitize_legacy_raw_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Conservatively repair pre-contract raw JSON without inventing fields."""

    cleaned = dict(payload)
    ownership = str(cleaned.get("label_ownership") or "").strip()
    if ownership != "matched":
        # A legacy model sometimes emitted a number while simultaneously
        # saying the card was not bound to the subject.  Keep the truthful
        # missing field; never promote that number.
        cleaned["model"] = None
        cleaned["price"] = None
    view = str(cleaned.get("view_type") or cleaned.get("category") or "").strip()
    count = cleaned.get("complete_screen_count")
    if (
        view == "遠景"
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count in {1, 2}
    ):
        # Project geometry is authoritative: one or two complete monitors is
        # never a distant view.  Preserve any physical clues for the normal
        # FollowMe adjudicator, but do not invent an identity or price.
        cleaned.update(
            {
                "view_type": "單機",
                "category": "單機",
                "screen_status": cleaned.get("screen_status") or "正常",
                "unique_main": True,
                "model": None,
                "price": None,
                "label_ownership": "not_visible",
            }
        )
        view = "單機"
    if (
        view == "單機"
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count >= 3
        and cleaned.get("unique_main") is not True
        and ownership != "matched"
        and not cleaned.get("model")
        and not cleaned.get("price")
    ):
        cleaned.update(
            {
                "view_type": "遠景",
                "category": "遠景",
                "screen_status": None,
                "quality_issue": None,
                "model": None,
                "price": None,
                "unique_main": False,
                "label_ownership": "not_visible",
                "followme_physical_evidence": [],
            }
        )
    return cleaned


def _raw_request_id(row: Mapping[str, Any]) -> str:
    try:
        payload = _raw_payload(row)
    except RuntimeError:
        return ""
    value = str(payload.get("request_id") or "").strip().lower()
    return value if _REQUEST_ID_RE.fullmatch(value) else ""


def _raw_vote_from_trace_row(
    row: Mapping[str, Any],
    *,
    request_id_override: str = "",
    sanitize_legacy: bool = True,
) -> dict[str, Any]:
    """Rebuild one independent vote without trusting a synthesized terminal.

    ``parsed_output`` is the live guard result and can legitimately contain a
    three-pass terminal synthesized after the third request.  That terminal is
    not a fourth independent observation and therefore must never be counted as
    a vote.  The material observation comes from the immutable raw JSON object;
    request binding, image identity, runtime health, and per-call suppression
    flags remain sourced from the trusted trace metadata.
    """

    payload = _raw_payload(row)
    if sanitize_legacy:
        payload = _sanitize_legacy_raw_payload(payload)
    request_id = str(
        payload.get("request_id") or request_id_override or ""
    ).strip().lower()
    if not _REQUEST_ID_RE.fullmatch(request_id):
        raise RuntimeError("trace raw structured output has no valid request ID")

    narration = str(
        payload.get("narration")
        or payload.get("thinking")
        or ""
    ).strip()
    trusted_trace = {
        key: deepcopy(row.get(key))
        for key in (
            "file_name",
            "source_item_id",
            "run_id",
            "ocr_attempt",
            "timestamp",
            "period",
            "source_path",
            "original_source_path",
            "source_file_sha256",
            "input_image_sha256",
            "request_id_verified",
            "request_binding_enforced",
            "independent_pass",
            "prior_answer_exposed",
            "prompt_contamination",
            "human_pixel_authority_applied",
            "runtime_health",
            "model_validation_failed",
            "brand_evidence_conflict",
            "price_conflict_detected",
            "price_label_mismatch",
            "structured_authority_blocked_fields",
            "_trace_source_path",
            "_trace_original_source_path",
            "_trace_raw_objects",
        )
        if key in row
    }
    vote = dict(row)
    # Remove terminal-only fields that may have been synthesized after this
    # model call.  All material visual fields below are then restored from the
    # immutable request-bound raw object.
    for field in (
        "retry",
        "unresolved",
        "verified",
        "auto_verified",
        "auto_review_required",
        "three_pass_adjudicated",
        "adjudication_rule",
        "adjudication_summary",
        "adjudication_pass_summaries",
        "adjudication_original_current",
        "adjudication_narration_synthesized",
        "independent_field_majority",
        "followme_family_confirmed",
        "discarded_unbound_call",
        "runtime_health_contained_reasons",
        "technical_retry_required",
        "technical_retry_exhausted",
        "hard_cap_consumed_attempts",
        "model_outputs_available",
        "model_outputs_observed",
        "zero_model_recovery",
        "zero_model_deferred_resolution",
        "deferred_resolution_rule",
        "deferred_history_audit",
    ):
        vote.pop(field, None)
    vote.update(payload)
    # Model output may never override trace-owned binding/health metadata.
    vote.update(trusted_trace)
    vote["request_id"] = request_id
    vote["thinking"] = narration
    vote["narration"] = narration
    vote["raw_objects"] = list(row.get("_trace_raw_objects") or [])
    vote["category"] = str(payload.get("view_type") or payload.get("category") or "")

    # A legacy reply can contain a useful, internally coherent single/distant
    # geometry and an owned price while hallucinating a FollowMe identity from
    # text alone.  The current evidence contract correctly rejects that
    # identity without same-subject physical evidence, but rejecting the whole
    # immutable reply would also erase its independent geometry/price and can
    # leave an already exhausted photo in a permanent queue.  Suppress only the
    # unsupported identity before validation and retain the exact raw text as
    # audit evidence.  A genuine FollowMe reply with direct physical evidence
    # is unchanged.
    observed_model = str(vote.get("model") or "").strip()
    if (
        observed_model
        and is_followme_model(observed_model)
        and not _direct_followme_evidence(vote)
    ):
        vote["unsupported_followme_raw_model"] = observed_model
        vote["model"] = None

    valid, errors, normalized = validate_evidence_contract(vote)
    if not valid:
        joined = ",".join(sorted(set(str(item) for item in errors)))
        raise RuntimeError(f"raw vote evidence contract invalid: {joined}")
    vote["evidence_contract_valid"] = True
    vote["evidence_contract_errors"] = []
    vote["normalized_evidence"] = normalized

    # Preserve per-call deterministic suppression.  A raw hallucination does
    # not become admissible merely because terminal synthesis has been removed.
    blocked = {
        str(item)
        for item in (row.get("structured_authority_blocked_fields") or [])
    }
    if (
        row.get("model_validation_failed") is True
        or row.get("brand_evidence_conflict") is True
        or "model" in blocked
    ):
        vote["model"] = None
    if (
        row.get("price_conflict_detected") is True
        or row.get("price_label_mismatch") is True
        or "price" in blocked
    ):
        vote["price"] = None
    runtime_reasons = {
        str(reason)
        for reason in ((row.get("runtime_health") or {}).get("reasons") or [])
    }
    physical = list(vote.get("followme_physical_evidence") or [])
    direct_branding = any(
        isinstance(cue, Mapping)
        and cue.get("cue") == "direct_followme_branding_on_unit"
        and cue.get("same_subject") is True
        for cue in physical
    )
    if (
        "structured_narration_followme_conflict" in runtime_reasons
        and vote.get("model")
        and not is_followme_model(vote.get("model"))
        and not direct_branding
    ):
        # The immutable raw identity repeated an ordinary SKU while a
        # generated explanation hallucinated FollowMe geometry.  Suppress only
        # those conflicting geometry cues; never manufacture a FollowMe label.
        vote["followme_physical_evidence"] = []
    return vote


def _clean_bound(row: Mapping[str, Any]) -> bool:
    image_hash = str(row.get("input_image_sha256") or "").strip().lower()
    runtime = row.get("runtime_health") or {}
    return bool(
        _SHA256_RE.fullmatch(image_hash)
        and row.get("request_id_verified") is True
        and row.get("request_binding_enforced") is True
        and row.get("independent_pass") is True
        and row.get("prior_answer_exposed") is not True
        and row.get("prompt_contamination") is not True
        and row.get("human_pixel_authority_applied") is not True
        and isinstance(runtime, dict)
        and runtime.get("healthy") is True
        and not (runtime.get("reasons") or [])
    )


def _request_bound_stateless(row: Mapping[str, Any]) -> bool:
    """Return whether the immutable raw response is safely image-bound.

    This is intentionally narrower than accepting an unhealthy terminal.  It
    permits only post-response presentation/material-field guards whose raw
    structured response remains immutable and independently request-bound.
    Any disputed model/price field is still removed by
    ``_raw_vote_from_trace_row`` before the three-pass finalizer sees it.
    """

    image_hash = str(row.get("input_image_sha256") or "").strip().lower()
    runtime = row.get("runtime_health") or {}
    reasons = {
        str(reason).strip()
        for reason in (runtime.get("reasons") or [])
        if str(reason).strip()
    }
    return bool(
        _SHA256_RE.fullmatch(image_hash)
        and row.get("request_id_verified") is True
        and row.get("request_binding_enforced") is True
        and row.get("independent_pass") is True
        and row.get("prior_answer_exposed") is not True
        and row.get("prompt_contamination") is not True
        and row.get("human_pixel_authority_applied") is not True
        and isinstance(runtime, dict)
        and reasons.issubset(RECOVERABLE_RAW_HEALTH_REASONS)
    )


def _mark_raw_vote_rebound(
    vote: dict[str, Any],
    *,
    input_image_sha256: str,
    recovery_mode: str,
) -> None:
    """Mark an immutable raw reply usable after deterministic containment.

    The original unhealthy/legacy metadata remains nested for audit.  Only the
    rebuilt raw vote receives healthy status; no model response or synthesized
    narration is promoted.
    """

    vote["deferred_original_runtime_health"] = deepcopy(
        vote.get("runtime_health") or {}
    )
    vote.update(
        {
            "input_image_sha256": input_image_sha256,
            "request_binding_enforced": True,
            "request_id_verified": True,
            "independent_pass": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
            "human_pixel_authority_applied": False,
            "evidence_contract_version": EVIDENCE_CONTRACT_VERSION,
            "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
            "deferred_raw_vote_rebound": True,
            "deferred_raw_vote_recovery_mode": recovery_mode,
            "runtime_health": {
                "healthy": True,
                "allow_processing": True,
                "allow_upload": False,
                "reasons": [],
                "recovered_from_immutable_raw_response": True,
            },
        }
    )
    valid, errors, normalized = validate_evidence_contract(vote)
    if not valid:
        raise RuntimeError(
            "rebound raw vote evidence contract invalid: "
            + ",".join(sorted(set(str(item) for item in errors)))
        )
    vote["normalized_evidence"] = normalized
    vote["evidence_contract_valid"] = True
    vote["evidence_contract_errors"] = []


def _history_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    clean = [row for row in rows if _clean_bound(row)]
    request_ids = [_raw_request_id(row) for row in rows]
    run_ids = [str(row.get("run_id") or "") for row in rows]
    input_hashes = [
        str(row.get("input_image_sha256") or "").strip().lower() for row in rows
    ]
    binding_invalid = sum(
        row.get("request_id_verified") is not True
        or row.get("request_binding_enforced") is not True
        or row.get("independent_pass") is not True
        for row in rows
    )
    prior_exposure = sum(row.get("prior_answer_exposed") is True for row in rows)
    prompt_contamination = sum(row.get("prompt_contamination") is True for row in rows)
    unhealthy = sum(
        not isinstance(row.get("runtime_health"), dict)
        or (row.get("runtime_health") or {}).get("healthy") is not True
        or bool((row.get("runtime_health") or {}).get("reasons") or [])
        for row in rows
    )
    invalid_raw_request = sum(not value for value in request_ids)
    valid_request_ids = [value for value in request_ids if value]
    duplicate_requests = len(valid_request_ids) - len(set(valid_request_ids))
    valid_hashes = sorted({value for value in input_hashes if _SHA256_RE.fullmatch(value)})
    invalid_input_hashes = sum(
        not _SHA256_RE.fullmatch(value) for value in input_hashes
    )
    human_authority = sum(
        row.get("human_pixel_authority_applied") is True for row in rows
    )
    return {
        "total_trace_rows": len(rows),
        "clean_bound_rows": len(clean),
        "distinct_runs": len({value for value in run_ids if value}),
        "input_hashes": valid_hashes,
        "binding_invalid_rows": binding_invalid,
        "prior_answer_exposed_rows": prior_exposure,
        "prompt_contamination_rows": prompt_contamination,
        "runtime_unhealthy_rows": unhealthy,
        "invalid_raw_request_rows": invalid_raw_request,
        "duplicate_raw_request_ids": duplicate_requests,
        "invalid_input_hash_rows": invalid_input_hashes,
        "human_pixel_authority_rows": human_authority,
        "historical_binding_or_contamination_conflict": bool(
            binding_invalid
            or prior_exposure
            or prompt_contamination
            or unhealthy
            or invalid_raw_request
            or duplicate_requests
            or invalid_input_hashes
            or human_authority
            or len(valid_hashes) > 1
        ),
    }


def _select_latest_three_distinct_runs(
    rows: list[dict[str, Any]],
    *,
    expected_input_hash: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    audit = _history_audit(rows)
    clean = sorted(
        [row for row in rows if _clean_bound(row)],
        key=lambda row: (
            str(row.get("timestamp") or ""),
            int(row.get("ocr_attempt") or 0),
        ),
    )
    if not clean:
        raise RuntimeError("trace has no clean request-bound stateless rows")
    latest_hash = str(clean[-1].get("input_image_sha256") or "").strip().lower()
    audit["latest_clean_input_hash"] = latest_hash
    audit["current_input_hash"] = expected_input_hash
    if latest_hash != expected_input_hash:
        raise RuntimeError("latest clean trace input hash is not the current model input")

    by_request: dict[str, dict[str, Any]] = {}
    invalid_raw_votes = 0
    for row in clean:
        if str(row.get("input_image_sha256") or "").strip().lower() != expected_input_hash:
            continue
        run_id = str(row.get("run_id") or "").strip()
        request_id = _raw_request_id(row)
        if not request_id:
            continue
        try:
            enriched = _raw_vote_from_trace_row(row)
        except RuntimeError:
            invalid_raw_votes += 1
            continue
        enriched["_bound_request_id"] = request_id
        enriched["_bound_run_id"] = run_id
        by_request[request_id] = enriched
    representatives = sorted(
        by_request.values(),
        key=lambda row: (
            str(row.get("timestamp") or ""),
            int(row.get("ocr_attempt") or 0),
        ),
    )
    if len(representatives) < 3:
        raise RuntimeError(
            f"only {len(representatives)} distinct clean-bound requests are available"
        )
    selected = [deepcopy(row) for row in representatives[-3:]]
    request_ids = [str(row.pop("_bound_request_id")) for row in selected]
    run_ids = [str(row.pop("_bound_run_id")) for row in selected]
    if len(set(request_ids)) != 3:
        raise RuntimeError("latest three request representatives reuse a request ID")
    for index, row in enumerate(selected, start=1):
        row["ocr_attempt"] = index
    audit.update(
        {
            "same_input_distinct_clean_requests": len(representatives),
            # Retained for receipt/backward compatibility.  Multiple requests
            # from one stateless run are valid and therefore may repeat here.
            "selected_run_ids": run_ids,
            "selected_request_ids": request_ids,
            "invalid_raw_vote_rows": invalid_raw_votes,
            "selected_original_attempts": [
                int(representatives[-3 + index].get("ocr_attempt") or 0)
                for index in range(3)
            ],
        }
    )
    return selected, audit


def _select_latest_three_request_bound_raw_votes(
    rows: list[dict[str, Any]],
    *,
    expected_input_hash: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Recover three bound raw replies rejected only after model response.

    The normal selector remains authoritative.  This fallback is used only
    when all request/image/stateless bindings are intact and every unhealthy
    reason belongs to ``RECOVERABLE_RAW_HEALTH_REASONS``.  It never trusts the
    synthesized terminal or the UI narration.
    """

    audit = _history_audit(rows)
    eligible = sorted(
        [
            row
            for row in rows
            if _request_bound_stateless(row)
            and str(row.get("input_image_sha256") or "").strip().lower()
            == expected_input_hash
        ],
        key=lambda row: (
            str(row.get("timestamp") or ""),
            int(row.get("ocr_attempt") or 0),
        ),
    )
    by_request: dict[str, dict[str, Any]] = {}
    invalid_raw_votes = 0
    for row in eligible:
        request_id = _raw_request_id(row)
        if not request_id:
            invalid_raw_votes += 1
            continue
        try:
            vote = _raw_vote_from_trace_row(row)
        except RuntimeError:
            invalid_raw_votes += 1
            continue
        _mark_raw_vote_rebound(
            vote,
            input_image_sha256=expected_input_hash,
            recovery_mode="request_bound_raw_post_response_guard",
        )
        vote["_bound_request_id"] = request_id
        vote["_bound_run_id"] = str(row.get("run_id") or "")
        by_request[request_id] = vote
    representatives = sorted(
        by_request.values(),
        key=lambda row: (
            str(row.get("timestamp") or ""),
            int(row.get("ocr_attempt") or 0),
        ),
    )
    if len(representatives) < 3:
        raise RuntimeError(
            f"only {len(representatives)} recoverable request-bound raw votes are available"
        )
    selected = [deepcopy(row) for row in representatives[-3:]]
    request_ids = [str(row.pop("_bound_request_id")) for row in selected]
    run_ids = [str(row.pop("_bound_run_id")) for row in selected]
    if len(set(request_ids)) != 3:
        raise RuntimeError("recoverable raw votes reuse a request ID")
    for index, row in enumerate(selected, start=1):
        row["ocr_attempt"] = index
    audit.update(
        {
            "recovery_mode": "request_bound_raw_post_response_guard",
            "current_input_hash": expected_input_hash,
            "same_input_distinct_recoverable_requests": len(representatives),
            "selected_run_ids": run_ids,
            "selected_request_ids": request_ids,
            "invalid_raw_vote_rows": invalid_raw_votes,
        }
    )
    return selected, audit


def _select_two_current_bound_identity_tail(
    rows: list[dict[str, Any]],
    *,
    expected_input_hash: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Close a consumed-cap 2/3 tail when both owned identities are exact."""

    votes: list[dict[str, Any]] = []
    request_ids: list[str] = []
    for row in sorted(
        rows,
        key=lambda item: (
            str(item.get("timestamp") or ""),
            int(item.get("ocr_attempt") or 0),
        ),
    ):
        attempt = int(row.get("ocr_attempt") or row.get("attempt") or 0)
        runtime_health = row.get("runtime_health") or {}
        if (
            attempt not in {2, 3}
            or runtime_health.get("healthy") is not True
            or not _request_bound_stateless(row)
            or str(row.get("input_image_sha256") or "").strip().lower()
            != expected_input_hash
        ):
            continue
        request_id = _raw_request_id(row)
        if not request_id or request_id in request_ids:
            continue
        try:
            vote = _raw_vote_from_trace_row(row)
            _mark_raw_vote_rebound(
                vote,
                input_image_sha256=expected_input_hash,
                recovery_mode="two_current_bound_owned_identity_tail",
            )
        except RuntimeError:
            continue
        votes.append(vote)
        request_ids.append(request_id)
    if len(votes) != 2:
        raise RuntimeError(
            f"only {len(votes)} recoverable two-call identity-tail votes are available"
        )
    attempts = {int(item.get("ocr_attempt") or 0) for item in votes}
    if attempts != {2, 3}:
        raise RuntimeError("recoverable two-call identity tail is not attempts 2 and 3")
    eligible: list[dict[str, Any]] = []
    pairs: set[tuple[str, str]] = set()
    for item in votes:
        evidence = item.get("normalized_evidence") or item
        model = normalize_model_token(item.get("model"))
        price = re.sub(r"[^0-9]", "", str(item.get("price") or ""))
        if (
            str(item.get("view_type") or item.get("category") or "").strip()
            != "單機"
            or evidence.get("unique_main") is not True
            or evidence.get("label_ownership") != "matched"
            or item.get("model_validation_failed") is True
            or item.get("price_conflict_detected") is True
            or item.get("brand_evidence_conflict") is True
            or not model
            or not price
        ):
            continue
        # A FollowMe identity is publishable only when each of the two
        # independent tail votes binds it to the same physical subject.  This
        # closes an already-consumed 2/3 agreement without weakening the
        # long-standing rule that text-only FollowMe guesses remain blocked.
        if is_followme_model(item.get("model")) and not _direct_followme_evidence(item):
            continue
        eligible.append(item)
        pairs.add((model, price))
    if len(eligible) != 2 or len(pairs) != 1 or not all(all(pair) for pair in pairs):
        raise RuntimeError("two-call identity tail has no exact owned SKU/price pair")
    followme_tail = all(is_followme_model(item.get("model")) for item in eligible)
    recovery_mode = (
        "two_current_bound_followme_identity_tail"
        if followme_tail
        else "two_current_bound_owned_identity_tail"
    )
    audit = _history_audit(rows)
    audit.update(
        {
            "recovery_mode": recovery_mode,
            "current_input_hash": expected_input_hash,
            "selected_run_ids": [str(item.get("run_id") or "") for item in votes],
            "selected_request_ids": request_ids,
            "direct_followme_tail": followme_tail,
        }
    )
    return [deepcopy(item) for item in votes], audit


def _select_legacy_three_raw_votes(
    rows: list[dict[str, Any]],
    *,
    original_path: Path,
    expected_input_hash: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rebind one legacy three-attempt run without trusting old synthesis.

    Early traces predate request IDs and prepared-image hashes.  They are
    accepted only as a complete 1/2/3 run for the exact unchanged source path,
    with source mtime predating every trace row and no recorded contamination.
    The immutable raw JSON is rebuilt into three votes; old parsed terminals
    and old narration are ignored.  This is a one-way migration and never
    authorizes another model call.
    """

    candidates = [
        row
        for row in rows
        if not str(row.get("input_image_sha256") or "").strip()
        and row.get("prior_answer_exposed") is not True
        and row.get("prompt_contamination") is not True
        and row.get("human_pixel_authority_applied") is not True
        and _same_path(row.get("_trace_original_source_path"), original_path)
    ]
    by_run: dict[str, dict[int, dict[str, Any]]] = {}
    for row in candidates:
        run_id = str(row.get("run_id") or "").strip()
        attempt = int(row.get("ocr_attempt") or 0)
        if run_id and attempt in {1, 2, 3}:
            by_run.setdefault(run_id, {})[attempt] = row
    complete_runs = [
        (run_id, attempts)
        for run_id, attempts in by_run.items()
        if set(attempts) == {1, 2, 3}
    ]
    if not complete_runs:
        raise RuntimeError("legacy trace has no complete exact-path three-attempt run")
    run_id, attempts = sorted(
        complete_runs,
        key=lambda item: max(
            str(row.get("timestamp") or "") for row in item[1].values()
        ),
    )[-1]
    source_mtime = original_path.stat().st_mtime
    selected: list[dict[str, Any]] = []
    surrogate_ids: list[str] = []
    for attempt in (1, 2, 3):
        row = attempts[attempt]
        timestamp = str(row.get("timestamp") or "").strip()
        try:
            trace_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            trace_epoch = trace_time.timestamp()
        except (TypeError, ValueError, OSError):
            raise RuntimeError("legacy trace timestamp is invalid")
        if source_mtime > trace_epoch:
            raise RuntimeError("legacy source file changed after the recorded model call")
        raw_fingerprint = hashlib.sha256(
            json.dumps(
                list(row.get("_trace_raw_objects") or []),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        surrogate = hashlib.sha256(
            f"legacy|{run_id}|{attempt}|{raw_fingerprint}".encode("utf-8")
        ).hexdigest()[:32]
        vote = _raw_vote_from_trace_row(
            row,
            request_id_override=surrogate,
            sanitize_legacy=True,
        )
        _mark_raw_vote_rebound(
            vote,
            input_image_sha256=expected_input_hash,
            recovery_mode="legacy_exact_path_unchanged_raw_three_attempts",
        )
        vote["ocr_attempt"] = attempt
        vote["legacy_request_id_surrogate"] = True
        selected.append(vote)
        surrogate_ids.append(surrogate)
    audit = _history_audit(rows)
    audit.update(
        {
            "recovery_mode": "legacy_exact_path_unchanged_raw_three_attempts",
            "selected_run_ids": [run_id, run_id, run_id],
            "selected_request_ids": surrogate_ids,
            "legacy_source_mtime": source_mtime,
            "legacy_parsed_terminal_ignored": True,
        }
    )
    return selected, audit


def _trace_narration_text(row: Mapping[str, Any]) -> str:
    """Return same-call prose used only to reject explicit answer carry-over."""

    parts = [str(row.get("narration") or ""), str(row.get("thinking") or "")]
    try:
        raw = _raw_payload(row)
    except RuntimeError:
        raw = {}
    parts.extend(
        [str(raw.get("narration") or ""), str(raw.get("thinking") or "")]
    )
    return "\n".join(part for part in parts if part).strip()


def _explicit_prior_answer_contamination(row: Mapping[str, Any]) -> bool:
    text = _trace_narration_text(row).casefold()
    return any(phrase.casefold() in text for phrase in _PRIOR_ANSWER_PHRASES)


def _strong_view_geometry(vote: Mapping[str, Any]) -> str:
    """Return a view only when immutable geometry is internally coherent."""

    evidence = vote.get("normalized_evidence") or vote
    view = str(vote.get("view_type") or vote.get("category") or "").strip()
    count = evidence.get("complete_screen_count")
    unique = evidence.get("unique_main")
    if (
        view == "單機"
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count in {1, 2}
        and unique is True
    ):
        return "單機"
    if (
        view == "遠景"
        and isinstance(count, int)
        and not isinstance(count, bool)
        and (count == 0 or count >= 3)
        and unique is False
    ):
        return "遠景"
    return ""


def _select_exhausted_existing_raw_votes(
    rows: list[dict[str, Any]],
    *,
    original_path: Path,
    expected_input_hash: str,
    consumed_calls: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Recover the first three immutable replies without authorizing call four.

    Old traces may predate request/hash metadata and may also omit the historic
    contamination flag.  Exact unchanged source bytes and trace-owned raw JSON
    are therefore the only authorities here.  The first three recorded calls
    consume the three available slots; an explicitly answer-aware reply is
    retained in the audit but excluded from the decision.
    """

    if int(consumed_calls or 0) < 3:
        raise RuntimeError("conservative hard-cap finalization requires three consumed calls")

    source_mtime = original_path.stat().st_mtime
    candidates: list[tuple[dict[str, Any], str, bool]] = []
    invalid_raw = 0
    changed_after_trace = 0
    wrong_input_hash = 0
    for row in sorted(
        rows,
        key=lambda item: (
            str(item.get("timestamp") or ""),
            int(item.get("ocr_attempt") or 0),
            str(item.get("run_id") or ""),
        ),
    ):
        if not _same_path(row.get("_trace_original_source_path"), original_path):
            continue
        if row.get("human_pixel_authority_applied") is True:
            continue
        recorded_hash = str(row.get("input_image_sha256") or "").strip().lower()
        if recorded_hash and recorded_hash != expected_input_hash:
            wrong_input_hash += 1
            continue
        timestamp = str(row.get("timestamp") or "").strip()
        try:
            trace_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            trace_epoch = trace_time.timestamp()
        except (TypeError, ValueError, OSError):
            invalid_raw += 1
            continue
        if source_mtime > trace_epoch:
            changed_after_trace += 1
            continue
        try:
            raw = _raw_payload(row)
            raw_fingerprint = hashlib.sha256(
                json.dumps(
                    raw,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            request_id = _raw_request_id(row)
            surrogate = False
            if not request_id:
                request_id = hashlib.sha256(
                    (
                        "legacy-exhausted|"
                        + str(row.get("run_id") or "")
                        + "|"
                        + str(row.get("ocr_attempt") or "")
                        + "|"
                        + timestamp
                        + "|"
                        + raw_fingerprint
                    ).encode("utf-8")
                ).hexdigest()[:32]
                surrogate = True
            rebound_row = dict(row)
            rebound_row.update(
                {
                    "input_image_sha256": expected_input_hash,
                    "request_id_verified": True,
                    "request_binding_enforced": True,
                    "independent_pass": True,
                    "prior_answer_exposed": False,
                    "prompt_contamination": False,
                    "human_pixel_authority_applied": False,
                    "runtime_health": {
                        "healthy": True,
                        "allow_processing": True,
                        "allow_upload": False,
                        "reasons": [],
                    },
                }
            )
            vote = _raw_vote_from_trace_row(
                rebound_row,
                request_id_override=request_id,
                sanitize_legacy=True,
            )
            _mark_raw_vote_rebound(
                vote,
                input_image_sha256=expected_input_hash,
                recovery_mode="three_call_exhausted_immutable_raw",
            )
            vote["legacy_request_id_surrogate"] = surrogate
            vote["_exhausted_original_attempt"] = int(
                row.get("ocr_attempt") or 0
            )
            vote["_exhausted_original_run_id"] = str(row.get("run_id") or "")
            polluted = bool(
                row.get("prior_answer_exposed") is True
                or row.get("prompt_contamination") is True
                or _explicit_prior_answer_contamination(row)
            )
            candidates.append((vote, request_id, polluted))
        except RuntimeError:
            invalid_raw += 1

    # The ledger is a lifetime limit.  Later accidental calls may exist in old
    # data, but they cannot become extra votes now; only the first three raw
    # replies are eligible and every polluted slot stays consumed.
    first_three = candidates[:3]
    if not first_three:
        raise RuntimeError("no immutable raw model output remains for hard-cap finalization")
    selected = [deepcopy(vote) for vote, _request, polluted in first_three if not polluted]
    if not selected:
        raise RuntimeError("all three immutable raw model outputs contain prior-answer contamination")
    for index, vote in enumerate(selected, start=1):
        vote["ocr_attempt"] = index
    audit = _history_audit(rows)
    audit.update(
        {
            "recovery_mode": "three_call_exhausted_immutable_raw",
            "current_input_hash": expected_input_hash,
            "hard_cap_consumed_attempts": int(consumed_calls),
            "raw_slots_observed": len(first_three),
            "raw_slots_accepted": len(selected),
            "raw_slots_polluted": sum(polluted for _vote, _request, polluted in first_three),
            "selected_request_ids": [
                request for _vote, request, polluted in first_three if not polluted
            ],
            "selected_run_ids": [
                str(vote.get("_exhausted_original_run_id") or "")
                for vote, _request, polluted in first_three
                if not polluted
            ],
            "invalid_raw_rows": invalid_raw,
            "source_changed_after_trace_rows": changed_after_trace,
            "wrong_input_hash_rows": wrong_input_hash,
            "later_accidental_calls_ignored": max(0, len(candidates) - 3),
            "legacy_source_mtime": source_mtime,
            "legacy_parsed_terminal_ignored": True,
        }
    )
    return selected, audit


def _direct_followme_evidence(vote: Mapping[str, Any]) -> bool:
    evidence = vote.get("normalized_evidence") or vote
    for cue in evidence.get("followme_physical_evidence") or []:
        if not isinstance(cue, Mapping) or cue.get("same_subject") is not True:
            continue
        if (
            cue.get("cue")
            in {"direct_followme_branding_on_unit", "attached_followme_product_card"}
            and cue.get("strength") in {"strong", "direct"}
        ):
            return True
    return False


def _explicit_followme_model_text(value: object) -> bool:
    return "FOLLOWME" in str(value or "").replace(" ", "").upper()


def _unique_supported_field(
    votes: list[dict[str, Any]],
    *,
    field: str,
    first_vote: Mapping[str, Any] | None,
) -> str | None:
    values: list[str] = []
    display_by_key: dict[str, str] = {}
    for vote in votes:
        evidence = vote.get("normalized_evidence") or vote
        if (
            str(vote.get("view_type") or vote.get("category") or "").strip()
            != "單機"
            or evidence.get("unique_main") is not True
            or evidence.get("label_ownership") != "matched"
        ):
            continue
        if field == "model":
            if vote.get("model_validation_failed") is True or vote.get(
                "brand_evidence_conflict"
            ) is True:
                continue
            display = str(vote.get("model") or "").strip()
            value = normalize_model_token(display)
        else:
            if vote.get("price_conflict_detected") is True or vote.get(
                "price_label_mismatch"
            ) is True:
                continue
            value = re.sub(r"[^0-9]", "", str(vote.get("price") or ""))
            display = value
        if value:
            values.append(value)
            display_by_key.setdefault(value, display)
    counts = Counter(values)
    repeated = [value for value, count in counts.items() if count >= 2]
    if len(repeated) == 1:
        return display_by_key[repeated[0]]
    if repeated or first_vote is None:
        return None
    evidence = first_vote.get("normalized_evidence") or first_vote
    if evidence.get("unique_main") is not True or evidence.get("label_ownership") != "matched":
        return None
    if field == "model":
        first_display = str(first_vote.get("model") or "").strip()
        first_value = normalize_model_token(first_display)
    else:
        first_value = re.sub(r"[^0-9]", "", str(first_vote.get("price") or ""))
        first_display = first_value
    return (
        first_display
        if first_value and set(values).issubset({first_value})
        else None
    )


def _conservative_exhausted_terminal(
    passes: list[dict[str, Any]],
    *,
    history_audit: Mapping[str, Any],
) -> dict[str, Any]:
    """Produce a truthful terminal from exhausted immutable evidence only."""

    if not passes:
        raise RuntimeError("no accepted immutable raw vote is available")
    first = passes[0]
    first_view = _strong_view_geometry(first)
    if first_view:
        final_view = first_view
        decision_source = "strong_first_pass_geometry"
        support = first
    else:
        view_votes = Counter(
            str(vote.get("view_type") or vote.get("category") or "").strip()
            for vote in passes
            if str(vote.get("view_type") or vote.get("category") or "").strip()
            in {"單機", "遠景"}
        )
        majority = [view for view, count in view_votes.items() if count >= 2]
        if len(majority) == 1:
            final_view = majority[0]
            decision_source = "unpolluted_raw_view_majority"
            support = next(
                vote
                for vote in passes
                if str(vote.get("view_type") or vote.get("category") or "").strip()
                == final_view
            )
        else:
            strong_later = [
                (view, vote)
                for vote in passes[1:]
                if (view := _strong_view_geometry(vote))
            ]
            if not strong_later:
                raise RuntimeError("no coherent view geometry remains after contamination removal")
            final_view, support = strong_later[0]
            decision_source = "single_strong_later_geometry"

    result = deepcopy(support)
    result["view_type"] = final_view
    result["category"] = final_view
    result["three_pass_adjudicated"] = True
    result["adjudication_rule"] = EXHAUSTED_RESOLUTION_RULE
    result["adjudication_decision_source"] = decision_source
    result["adjudication_narration_synthesized"] = True

    if final_view == "遠景":
        result.update(
            {
                "screen_status": "",
                "quality_issue": None,
                "model": None,
                "price": None,
                "unique_main": False,
                "label_ownership": "not_visible",
                "followme_physical_evidence": [],
                "followme_family_confirmed": False,
            }
        )
        narration = (
            "本張已在三次本機模型額度內完成仲裁：遠景，無型號、無價格。"
            "結論來自未受前輪答案污染的既有原始幾何證據；不再呼叫模型。"
        )
    else:
        single_votes = [
            vote
            for vote in passes
            if str(vote.get("view_type") or vote.get("category") or "").strip()
            == "單機"
        ]
        first_single = first if first_view == "單機" else None
        followme_votes = [vote for vote in single_votes if _direct_followme_evidence(vote)]
        discarded_unsupported_followme = any(
            bool(str(vote.get("unsupported_followme_raw_model") or "").strip())
            for vote in single_votes
        )
        if followme_votes:
            exact_variants = Counter()
            variant_display: dict[str, str] = {}
            for vote in followme_votes:
                display = str(vote.get("model") or "").strip()
                key = normalize_model_token(display)
                if (
                    is_followme_model(display)
                    and key not in {"FOLLOWME", "FOLLOWME型號未細分"}
                ):
                    exact_variants[key] += 1
                    variant_display.setdefault(key, display)
            winners = [value for value, count in exact_variants.items() if count >= 2]
            model = (
                variant_display[winners[0]]
                if len(winners) == 1
                else "FollowMe 型號未細分"
            )
            family = True
        else:
            model = _unique_supported_field(
                single_votes, field="model", first_vote=first_single
            )
            family = False
            if model and _explicit_followme_model_text(model):
                model_key = normalize_model_token(model)
                repeated_text_identity = sum(
                    normalize_model_token(vote.get("model")) == model_key
                    and (vote.get("normalized_evidence") or vote).get(
                        "label_ownership"
                    )
                    == "matched"
                    for vote in single_votes
                ) >= 2
                if repeated_text_identity:
                    # The repeated legacy text is retained in the audit, but
                    # the current contract correctly requires same-subject
                    # physical evidence before publishing FollowMe identity.
                    # Clear only that unsupported field; do not stall or call
                    # the model a fourth time.
                    model = None
                    family = False
                    discarded_unsupported_followme = True
                else:
                    # One text-only FollowMe guess is not sufficient after an
                    # exhausted, historically contaminated run.
                    model = None
        price = _unique_supported_field(
            single_votes, field="price", first_vote=first_single
        )
        retained_physical = (
            list((followme_votes[0].get("normalized_evidence") or followme_votes[0]).get(
                "followme_physical_evidence"
            ) or [])
            if followme_votes
            else []
        )
        result.update(
            {
                "screen_status": str(result.get("screen_status") or "正常"),
                "quality_issue": str(result.get("quality_issue") or "無"),
                "model": model,
                "price": price,
                "unique_main": True,
                "label_ownership": "matched" if model or price else "not_visible",
                "followme_physical_evidence": retained_physical,
                "followme_family_confirmed": family,
                "adjudication_discarded_unsupported_followme_identity": (
                    discarded_unsupported_followme
                ),
            }
        )
        model_text = model or "無型號"
        price_text = f"{int(price):,} 元" if price else "無價格"
        narration = (
            f"本張已在三次本機模型額度內完成仲裁：單機，{model_text}，{price_text}。"
            "只保留未受前輪答案污染且歸屬同一主體的既有欄位；"
            "有衝突或證據不足的欄位保持空白，不再呼叫模型。"
        )

    result["thinking"] = narration
    result["narration"] = narration
    result["adjudication_summary"] = narration
    result["adjudication_pass_summaries"] = [
        {
            "attempt": int(vote.get("_exhausted_original_attempt") or index),
            "view_type": str(vote.get("view_type") or vote.get("category") or ""),
            "model": vote.get("model"),
            "price": vote.get("price"),
            "complete_screen_count": (vote.get("normalized_evidence") or vote).get(
                "complete_screen_count"
            ),
            "unique_main": (vote.get("normalized_evidence") or vote).get("unique_main"),
            "label_ownership": (vote.get("normalized_evidence") or vote).get(
                "label_ownership"
            ),
        }
        for index, vote in enumerate(passes, start=1)
    ]
    result["deferred_history_audit"] = dict(history_audit)
    return result


def _validate_item_binding(
    *,
    staging_dir: Path,
    queue_item: Mapping[str, Any],
    source_info: Mapping[str, Any],
    prepared_hash_fn: Callable[[Path], str],
) -> tuple[Path, Path, str, str]:
    file_name = str(queue_item.get("file_name") or "")
    source_id = str(queue_item.get("source_item_id") or "").strip().lower()
    if str(source_info.get("source_item_id") or "").strip().lower() != source_id:
        raise RuntimeError("queue and source map source_item_id disagree")

    staged_path = (staging_dir / file_name).resolve()
    original_path = Path(
        str(source_info.get("original_source_path") or "")
    ).resolve()
    if not staged_path.is_file() or not original_path.is_file():
        raise RuntimeError("current staged or original source file is missing")
    if queue_item.get("source_path") and not _same_path(
        queue_item.get("source_path"), staged_path
    ):
        raise RuntimeError("queue source_path does not match current staging")
    if queue_item.get("original_source_path") and not _same_path(
        queue_item.get("original_source_path"), original_path
    ):
        raise RuntimeError("queue original_source_path does not match source map")

    source_hash = _sha256_file(original_path)
    if _sha256_file(staged_path) != source_hash:
        raise RuntimeError("staged and original source bytes do not match")
    if queue_item.get("source_file_sha256") and str(
        queue_item.get("source_file_sha256")
    ).strip().lower() != source_hash:
        raise RuntimeError("queue source_file_sha256 does not match current bytes")

    input_hash = prepared_hash_fn(staged_path)
    if not _SHA256_RE.fullmatch(input_hash):
        raise RuntimeError("current model input hash is invalid")
    if queue_item.get("input_image_sha256") and str(
        queue_item.get("input_image_sha256")
    ).strip().lower() != input_hash:
        raise RuntimeError("queue input_image_sha256 does not match current input")

    binding = build_source_image_binding(
        source_item_id=source_id,
        original_source_path=original_path,
        input_image_sha256=input_hash,
    )
    if queue_item.get("binding_key") and str(
        queue_item.get("binding_key")
    ).strip().lower() != binding.binding_key:
        raise RuntimeError("queue binding_key does not match current binding")
    return staged_path, original_path, source_hash, input_hash


def _apply_independent_single_field_majority(
    record: dict[str, Any],
    passes: list[dict[str, Any]],
) -> None:
    """Retain independently repeated model and price evidence for one subject.

    The shared three-pass finalizer is intentionally conservative and normally
    requires the same model/price pair to repeat.  The deferred-queue contract
    is field based instead: after three clean bound runs have already settled
    an ordinary single-subject view, a model and a price each survive only when
    that field independently has at least two ``matched`` votes.  This function
    never combines FollowMe variants; the established FollowMe-family branches
    remain authoritative for those photos.
    """

    if str(record.get("view_type") or record.get("category") or "").strip() != "單機":
        return
    if is_followme_model(record.get("model")) or any(
        is_followme_model(item.get("model")) for item in passes
    ):
        return

    eligible: list[dict[str, Any]] = []
    for item in passes:
        evidence = item.get("normalized_evidence") or item
        if (
            str(item.get("view_type") or item.get("category") or "").strip()
            == "單機"
            and evidence.get("unique_main") is True
            and evidence.get("label_ownership") == "matched"
            and item.get("model_validation_failed") is not True
            and item.get("brand_evidence_conflict") is not True
        ):
            eligible.append(item)

    model_votes: Counter[str] = Counter()
    price_votes: Counter[str] = Counter()
    for item in eligible:
        model_key = normalize_model_token(item.get("model"))
        if model_key and not is_followme_model(item.get("model")):
            model_votes[model_key] += 1
        price_key = re.sub(r"[^0-9]", "", str(item.get("price") or ""))
        if price_key and item.get("price_conflict_detected") is not True:
            price_votes[price_key] += 1

    model_winners = [key for key, votes in model_votes.items() if votes >= 2]
    price_winners = [key for key, votes in price_votes.items() if votes >= 2]
    final_model = model_winners[0] if len(model_winners) == 1 else None
    final_price = price_winners[0] if len(price_winners) == 1 else None
    if final_model is None and final_price is None:
        return

    record["model"] = final_model
    record["price"] = final_price
    record["label_ownership"] = "matched"
    record["three_pass_adjudicated"] = True
    record["adjudication_rule"] = (
        "three_pass_single_subject_independent_field_majority"
    )
    record["independent_field_majority"] = {
        "eligible_passes": len(eligible),
        "model_votes": dict(model_votes),
        "price_votes": dict(price_votes),
        "model": final_model,
        "price": final_price,
    }
    model_text = final_model or "無型號"
    price_text = f"{final_price} 元" if final_price else "無價格"
    narration = (
        f"我看到本輪結論：單機，{model_text}，{price_text}。"
        "三次同圖、無記憶且標籤歸屬相符的既有判讀，"
        "已分別以至少兩票確立可保留欄位；未達兩票的欄位保持空白。"
    )
    record["thinking"] = narration
    record["narration"] = narration
    record["adjudication_narration_synthesized"] = True


def _owned_identity_consensus_after_cap(
    passes: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Return a conservative single result for an exact repeated owned pair.

    This closes the narrow case where screen-count geometry is inconsistent
    but two independently bound raw replies repeat the same ordinary SKU and
    price on a uniquely owned card.  A FollowMe pair is accepted only when
    every supporting vote also binds strong/direct physical FollowMe evidence
    to that same subject. Another material identity or any non-single vote
    rejects this path.
    """

    if len(passes) not in {2, 3} or any(
        str(item.get("view_type") or item.get("category") or "").strip()
        != "單機"
        for item in passes
    ):
        return None
    source_ids = {str(item.get("source_item_id") or "").strip() for item in passes}
    input_hashes = {
        str(item.get("input_image_sha256") or "").strip().lower()
        for item in passes
    }
    if (
        "" in source_ids
        or len(source_ids) != 1
        or "" in input_hashes
        or len(input_hashes) != 1
        or any(
            item.get("request_binding_enforced") is not True
            or item.get("request_id_verified") is not True
            or item.get("independent_pass") is not True
            or item.get("prior_answer_exposed") is True
            or item.get("prompt_contamination") is True
            for item in passes
        )
    ):
        return None
    pair_votes: Counter[tuple[str, str]] = Counter()
    supporting: list[dict[str, Any]] = []
    identity_kinds: set[str] = set()
    for item in passes:
        evidence = item.get("normalized_evidence") or item
        physical = list(evidence.get("followme_physical_evidence") or [])
        direct_branding = any(
            isinstance(cue, Mapping)
            and cue.get("cue") == "direct_followme_branding_on_unit"
            and cue.get("same_subject") is True
            for cue in physical
        )
        model = normalize_model_token(item.get("model"))
        price = re.sub(r"[^0-9]", "", str(item.get("price") or ""))
        followme_identity = is_followme_model(item.get("model"))
        if followme_identity:
            if not _direct_followme_evidence(item):
                return None
            identity_kinds.add("followme")
        else:
            if direct_branding:
                return None
            identity_kinds.add("ordinary")
        if model or price:
            if (
                not model
                or not price
                or evidence.get("unique_main") is not True
                or evidence.get("label_ownership") != "matched"
                or item.get("model_validation_failed") is True
                or item.get("price_conflict_detected") is True
                or item.get("brand_evidence_conflict") is True
            ):
                return None
            pair_votes[(model, price)] += 1
            supporting.append(item)
    if len(identity_kinds) != 1:
        return None
    winners = [pair for pair, count in pair_votes.items() if count >= 2]
    if len(winners) != 1 or len(pair_votes) != 1:
        return None
    model, price = winners[0]
    followme_identity = identity_kinds == {"followme"}
    display_model = (
        str(supporting[-1].get("model") or "").strip()
        if followme_identity
        else model
    )
    result = deepcopy(supporting[-1])
    result.update(
        {
            "view_type": "單機",
            "category": "單機",
            "model": display_model,
            "price": price,
            "unique_main": True,
            "label_ownership": "matched",
            "followme_physical_evidence": (
                list(
                    (supporting[-1].get("normalized_evidence") or supporting[-1]).get(
                        "followme_physical_evidence"
                    )
                    or []
                )
                if followme_identity
                else []
            ),
            "followme_family_confirmed": followme_identity,
            "three_pass_adjudicated": True,
            "adjudication_rule": (
                (
                    "three_bound_followme_identity_consensus_after_cap"
                    if len(passes) == 3
                    else "two_bound_followme_identity_tail_after_cap"
                )
                if followme_identity
                else (
                    "three_bound_owned_identity_consensus_after_cap"
                    if len(passes) == 3
                    else "two_bound_owned_identity_tail_after_cap"
                )
            ),
            "adjudication_narration_synthesized": True,
            "adjudication_summary": (
                f"已用現有獨立原始結構結果完成仲裁：單機，"
                f"型號 {model}，價格 {int(price):,} 元。"
            ),
            "thinking": (
                f"本張已完成仲裁：單機，型號 {model}，"
                f"價格 {int(price):,} 元。型號與價格在至少兩次"
                "獨立、同圖且歸屬主角的原始結構結果一致。"
            ),
        }
    )
    result["narration"] = result["thinking"]
    result["adjudication_pass_summaries"] = [
        {
            "attempt": int(item.get("ocr_attempt") or index),
            "view_type": str(item.get("view_type") or item.get("category") or ""),
            "model": item.get("model"),
            "price": item.get("price"),
            "complete_screen_count": (item.get("normalized_evidence") or item).get(
                "complete_screen_count"
            ),
            "unique_main": (item.get("normalized_evidence") or item).get(
                "unique_main"
            ),
            "label_ownership": (item.get("normalized_evidence") or item).get(
                "label_ownership"
            ),
        }
        for index, item in enumerate(passes, start=1)
    ]
    valid, _errors, normalized = validate_evidence_contract(result)
    if not valid:
        return None
    result["normalized_evidence"] = normalized
    return result


def _stamp_capped_terminal(
    current: dict[str, Any],
    *,
    selected: list[dict[str, Any]],
    file_name: str,
    source_id: str,
    staged_path: Path,
    original_path: Path,
    source_hash: str,
    input_hash: str,
    period: str,
    history_audit: Mapping[str, Any],
    resolution_rule: str,
) -> dict[str, Any]:
    """Apply the one terminal/upload authority shared by strict and fallback paths."""

    now = datetime.now().astimezone().isoformat()
    available = len(selected)
    observed = int(history_audit.get("raw_slots_observed") or available)
    current.update(
        {
            "file_name": file_name,
            "source_path": str(staged_path),
            "original_source_path": str(original_path),
            "source_item_id": source_id,
            "source_file_sha256": source_hash,
            "input_image_sha256": input_hash,
            "period": period,
            "ocr_attempt": 3,
            "timestamp": now,
            "request_binding_enforced": True,
            "request_id_verified": True,
            "independent_pass": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
            "hard_cap_consumed_attempts": 3,
            "model_outputs_available": available,
            "model_outputs_observed": observed,
            "zero_model_recovery": True,
            "zero_model_deferred_resolution": True,
            "deferred_resolution_rule": resolution_rule,
            "deferred_history_audit": dict(history_audit),
            "evidence_contract_version": EVIDENCE_CONTRACT_VERSION,
            "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
            "auto_verified": True,
            "auto_review_required": False,
            "verified": True,
            "retry": False,
            "unresolved": False,
            "review_status": "已完成",
            "technical_retry_required": False,
            "technical_retry_exhausted": False,
            "auto_retry_reasons": "",
        }
    )
    if current.get("view_type") == "遠景":
        current["screen_status"] = ""
    elif not str(current.get("screen_status") or "").strip():
        current["screen_status"] = "正常"
    clear_superseded_terminal_content_flags(current)
    normalize_terminal_quality_issue(current)
    _carry_bound_price_comparison(current, selected)
    current["runtime_health"] = {
        "healthy": True,
        "allow_processing": True,
        "allow_upload": True,
        "reasons": [],
        "display_narration": str(
            current.get("narration") or current.get("thinking") or ""
        ),
        "resolved_by_capped_zero_model_adjudication": True,
    }

    valid, errors, normalized = validate_evidence_contract(current)
    if not valid:
        raise RuntimeError(
            "adjudicated result failed evidence contract: " + ";".join(errors)
        )
    current["normalized_evidence"] = normalized
    current["evidence_contract_valid"] = True
    current["evidence_contract_errors"] = []
    invariant_reasons = adjudication_field_invariant_reasons(current)
    if invariant_reasons:
        raise RuntimeError(invariant_reasons[0])
    return current


def _finalize_selected_passes(
    *,
    selected: list[dict[str, Any]],
    file_name: str,
    source_id: str,
    staged_path: Path,
    original_path: Path,
    source_hash: str,
    input_hash: str,
    period: str,
    history_audit: Mapping[str, Any],
) -> dict[str, Any]:
    passes = [deepcopy(row) for row in selected]
    field_vote_passes = deepcopy(passes)
    current = passes[-1]
    decision = {
        "attempt": 3,
        "retry": False,
        "unresolved": True,
        "verified": False,
        "reasons": ["lifetime_model_call_ledger_blocked"],
    }
    outcome = finalize_three_pass_outcome(
        current,
        passes[:-1],
        decision,
        max_attempts=3,
    )
    if not (
        outcome.get("verified") is True
        and outcome.get("unresolved") is False
        and outcome.get("retry") is False
        and current.get("three_pass_adjudicated") is True
        and bool(str(current.get("adjudication_rule") or "").strip())
    ):
        consensus = _owned_identity_consensus_after_cap(passes)
        if consensus is None:
            reasons = list(outcome.get("reasons") or [])
            reason = (
                str(outcome.get("technical_retry_reason") or "").strip()
                or (str(reasons[0]) if reasons else "")
                or "three-pass finalizer did not produce a verified terminal result"
            )
            raise RuntimeError(reason)
        current = consensus

    _apply_independent_single_field_majority(current, field_vote_passes)

    return _stamp_capped_terminal(
        current,
        selected=selected,
        file_name=file_name,
        source_id=source_id,
        staged_path=staged_path,
        original_path=original_path,
        source_hash=source_hash,
        input_hash=input_hash,
        period=period,
        history_audit=history_audit,
        resolution_rule=RESOLUTION_RULE,
    )


def _carry_bound_price_comparison(
    record: dict[str, Any],
    selected: list[dict[str, Any]],
) -> None:
    """Reuse an already-bound comparison without network or model activity.

    The adjudicator may select the repeated identity from an older one of the
    three passes.  Re-running the official-price lookup here would make a dry
    run impure and needlessly slow.  Instead, carry metadata only from the
    newest selected pass whose model and price exactly match the terminal
    consensus.  If no such bound comparison exists, state that honestly.
    """

    keys = ("price_status", "price_symbol", "official_price", "price_diff_percent")
    for key in keys:
        record.pop(key, None)
    model = str(record.get("model") or "").strip().upper()
    price = re.sub(r"\D", "", str(record.get("price") or ""))
    if not model or not price:
        record.update(
            {
                "price_status": "not_compared",
                "price_symbol": "",
                "official_price": "",
                "price_diff_percent": None,
            }
        )
        return

    for item in reversed(selected):
        item_model = str(item.get("model") or "").strip().upper()
        item_price = re.sub(r"\D", "", str(item.get("price") or ""))
        status = str(item.get("price_status") or "").strip()
        if item_model != model or item_price != price or not status:
            continue
        for key in keys:
            record[key] = item.get(key)
        return

    record.update(
        {
            "price_status": "unknown",
            "price_symbol": "?",
            "official_price": None,
            "price_diff_percent": None,
        }
    )


def _resolve_capped_item_record(
    *,
    rows: list[dict[str, Any]],
    queue_item: Mapping[str, Any],
    file_name: str,
    source_id: str,
    staged_path: Path,
    original_path: Path,
    source_hash: str,
    input_hash: str,
    period: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve one item through strict gates, then the exhausted terminal gate."""

    strict_errors: list[str] = []
    selectors = (
        lambda: _select_latest_three_distinct_runs(
            rows, expected_input_hash=input_hash
        ),
        lambda: _select_latest_three_request_bound_raw_votes(
            rows, expected_input_hash=input_hash
        ),
        lambda: _select_two_current_bound_identity_tail(
            rows, expected_input_hash=input_hash
        ),
        lambda: _select_legacy_three_raw_votes(
            rows,
            original_path=original_path,
            expected_input_hash=input_hash,
        ),
    )
    for selector in selectors:
        try:
            selected, audit = selector()
        except RuntimeError as exc:
            strict_errors.append(str(exc))
            continue
        try:
            return (
                _finalize_selected_passes(
                    selected=selected,
                    file_name=file_name,
                    source_id=source_id,
                    staged_path=staged_path,
                    original_path=original_path,
                    source_hash=source_hash,
                    input_hash=input_hash,
                    period=period,
                    history_audit=audit,
                ),
                audit,
            )
        except RuntimeError as exc:
            strict_errors.append(str(exc))

    if int(queue_item.get("consumed_calls") or 0) < 3:
        raise RuntimeError("; ".join(strict_errors))

    selected, audit = _select_exhausted_existing_raw_votes(
        rows,
        original_path=original_path,
        expected_input_hash=input_hash,
        consumed_calls=int(queue_item.get("consumed_calls") or 0),
    )
    audit["strict_resolution_failures"] = strict_errors
    terminal = _conservative_exhausted_terminal(
        selected,
        history_audit=audit,
    )
    return (
        _stamp_capped_terminal(
            terminal,
            selected=selected,
            file_name=file_name,
            source_id=source_id,
            staged_path=staged_path,
            original_path=original_path,
            source_hash=source_hash,
            input_hash=input_hash,
            period=period,
            history_audit=audit,
            resolution_rule=EXHAUSTED_RESOLUTION_RULE,
        ),
        audit,
    )


def _task_file_name(task: Mapping[str, Any]) -> str:
    image = str((task.get("data") or {}).get("image") or "").replace("\\", "/")
    return image.rsplit("/", 1)[-1]


def _prediction_fields(task: Mapping[str, Any]) -> tuple[str, str | None, str | None]:
    values: dict[str, Any] = {}
    annotations = task.get("annotations") or []
    if annotations and isinstance(annotations[0], dict):
        for item in annotations[0].get("result") or []:
            if not isinstance(item, dict):
                continue
            field = str(item.get("from_name") or "")
            value = item.get("value") or {}
            if field == "category":
                choices = value.get("choices") or []
                values[field] = str(choices[0]) if choices else ""
            elif field in {"model", "price"}:
                text = value.get("text") or []
                raw = str(text[0]) if text else ""
                values[field] = None if raw in {"", "null", "None"} else raw
    return (
        str(values.get("category") or ""),
        values.get("model"),
        values.get("price"),
    )


def _same_terminal(task: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
    meta = (task.get("data") or {}).get("ocr_meta") or {}
    view, model, price = _prediction_fields(task)
    return bool(
        meta.get("auto_verified") is True
        and meta.get("auto_review_required") is not True
        and str(meta.get("evidence_guard_revision") or "")
        == EVIDENCE_GUARD_REVISION
        and view == str(record.get("category") or record.get("view_type") or "")
        and str(model or "") == str(record.get("model") or "")
        and re.sub(r"\D", "", str(price or ""))
        == re.sub(r"\D", "", str(record.get("price") or ""))
        and str(meta.get("source_item_id") or "").strip().lower()
        == str(record.get("source_item_id") or "").strip().lower()
        and str(meta.get("source_file_sha256") or "").strip().lower()
        == str(record.get("source_file_sha256") or "").strip().lower()
        and str(meta.get("input_image_sha256") or "").strip().lower()
        == str(record.get("input_image_sha256") or "").strip().lower()
    )


def _terminal_task(record: dict[str, Any], task_id: int) -> dict[str, Any]:
    """Build the established task shape and retain this resolver's audit."""

    task = _result_task(record, task_id)
    meta = task["data"]["ocr_meta"]
    for key in (
        "period",
        "source_item_id",
        "source_file_sha256",
        "input_image_sha256",
        "request_binding_enforced",
        "request_id_verified",
        "independent_pass",
        "prior_answer_exposed",
        "prompt_contamination",
        "hard_cap_consumed_attempts",
        "model_outputs_available",
        "model_outputs_observed",
        "zero_model_recovery",
        "zero_model_deferred_resolution",
        "deferred_resolution_rule",
        "deferred_history_audit",
        "runtime_health",
    ):
        meta[key] = record.get(key)
    return task


def _partition_terminal_conflicts(
    result_file: Path,
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Exclude conflicts before any upload intent is created."""

    payload = _read_json(result_file) if result_file.is_file() else []
    if not isinstance(payload, list):
        raise RuntimeError("result file is not a Label Studio task list")
    existing = {
        _task_file_name(task): task
        for task in payload
        if isinstance(task, dict) and _task_file_name(task)
    }
    accepted: list[dict[str, Any]] = []
    conflicts: list[dict[str, str]] = []
    for candidate in candidates:
        name = str(candidate["file_name"])
        prior = existing.get(name)
        if prior is not None and not _same_terminal(prior, candidate["record"]):
            conflicts.append(
                {
                    "file_name": name,
                    "source_item_id": str(candidate["source_item_id"]),
                    "reason": "result file has a different or unbound terminal result",
                    "reason_key": "existing_terminal_binding_conflict",
                }
            )
            continue
        accepted.append(candidate)
    return accepted, conflicts


def _append_terminal_results(
    result_file: Path,
    records: list[dict[str, Any]],
    *,
    attempts: int = 8,
) -> tuple[int, list[str]]:
    """Optimistically merge results without overwriting a newer backend write."""

    if not records:
        return 0, []
    for _ in range(max(1, attempts)):
        before = result_file.read_bytes() if result_file.is_file() else b"[]"
        try:
            tasks = json.loads(before.decode("utf-8-sig"))
        except (UnicodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"result file is unreadable: {result_file}") from exc
        if not isinstance(tasks, list):
            raise RuntimeError("result file is not a Label Studio task list")

        existing = {
            _task_file_name(task): task
            for task in tasks
            if isinstance(task, dict) and _task_file_name(task)
        }
        additions: list[dict[str, Any]] = []
        completed: list[str] = []
        next_id = max(
            (int(task.get("id") or 0) for task in tasks if isinstance(task, dict)),
            default=0,
        )
        for record in records:
            name = str(record["file_name"])
            prior = existing.get(name)
            if prior is not None:
                if not _same_terminal(prior, record):
                    raise RuntimeError(
                        f"result file already has a different terminal result: {name}"
                    )
                completed.append(name)
                continue
            next_id += 1
            additions.append(_terminal_task(record, next_id))
            completed.append(name)
        if not additions:
            return 0, completed
        current = result_file.read_bytes() if result_file.is_file() else b"[]"
        if hashlib.sha256(current).digest() != hashlib.sha256(before).digest():
            time.sleep(0.05)
            continue
        _atomic_json(result_file, tasks + additions)
        return len(additions), completed
    raise RuntimeError("result file kept changing during optimistic merge")


def _remove_resolved_queue_items(
    *,
    queue_path: Path,
    staging_dir: Path,
    resolved: Mapping[str, str],
    unresolved_reasons: Mapping[str, Mapping[str, str]] | None = None,
) -> int:
    payload = _queue_payload(queue_path, staging_dir)
    kept: list[dict[str, Any]] = []
    removed = 0
    checked_at = datetime.now().astimezone().isoformat()
    unresolved_reasons = unresolved_reasons or {}
    for item in payload["items"]:
        name = str(item.get("file_name") or "")
        expected_source = resolved.get(name)
        if expected_source is None:
            reason = unresolved_reasons.get(name)
            if reason and str(item.get("source_item_id") or "").strip().lower() == str(
                reason.get("source_item_id") or ""
            ).strip().lower():
                annotated = dict(item)
                annotated.update(
                    {
                        "deferred_resolution_status": "awaiting_safe_existing_evidence",
                        "deferred_resolution_reason": str(reason.get("reason") or ""),
                        "deferred_resolution_reason_key": str(
                            reason.get("reason_key") or ""
                        ),
                        "deferred_resolution_checked_at": checked_at,
                    }
                )
                kept.append(annotated)
            else:
                kept.append(item)
            continue
        if str(item.get("source_item_id") or "").strip().lower() != expected_source:
            raise RuntimeError(f"queue binding changed before removal: {name}")
        removed += 1
    payload["items"] = kept
    payload["updated_at"] = datetime.now().astimezone().isoformat()
    _atomic_json(queue_path, payload)
    return removed


def _reason_key(exc: BaseException) -> str:
    message = str(exc).strip()
    prefixes = (
        "only ",
        "three_healthy_bound_passes_required",
        "three_pass_view_majority_missing",
        "three_pass_current_integrity_invalid",
        "adjudication_repeated_identity_pair_erased_or_changed",
        "adjudicated_result_contract_invalid",
        "repeated_identity_pair_blocked_by_narration_conflict",
        "latest clean trace input hash",
        "trace has no clean",
    )
    for prefix in prefixes:
        if message.startswith(prefix):
            return (
                "insufficient_distinct_clean_bound_runs"
                if prefix == "only "
                else prefix
            )
    return message or type(exc).__name__


def _revalidate_candidates_for_apply(
    *,
    staging_dir: Path,
    trace_path: Path,
    candidates: list[dict[str, Any]],
    prepared_hash_fn: Callable[[Path], str],
    progress_fn: Callable[[Mapping[str, Any]], None] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], int, int]:
    """Rebuild every candidate under the apply lock from current bytes/trace."""

    live_queue = _queue_payload(
        staging_dir / ".ocr_capped_adjudication_queue.json",
        staging_dir,
    )
    live_by_source = {
        str(item.get("source_item_id") or "").strip().lower(): item
        for item in live_queue["items"]
    }
    requested = {
        str(item["source_item_id"]): live_by_source.get(str(item["source_item_id"]))
        for item in candidates
    }
    requested = {key: value for key, value in requested.items() if value is not None}
    source_items = _source_map(staging_dir)["items"]
    trace_paths = _trace_paths_for_source_map(trace_path, source_items)
    rows_by_source, trace_lines, matched_rows = _scan_traces(
        trace_paths,
        requested,
        progress_fn=progress_fn,
        phase="apply_trace_scan",
    )
    refreshed: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for index, candidate in enumerate(candidates, start=1):
        name = str(candidate["file_name"])
        source_id = str(candidate["source_item_id"])
        try:
            queue_item = live_by_source.get(source_id)
            if not isinstance(queue_item, dict) or str(
                queue_item.get("file_name") or ""
            ) != name:
                raise RuntimeError("queue binding changed before apply")
            info = source_items.get(name)
            if not isinstance(info, dict):
                raise RuntimeError("source map has no exact queued photo")
            staged, original, source_hash, input_hash = _validate_item_binding(
                staging_dir=staging_dir,
                queue_item=queue_item,
                source_info=info,
                prepared_hash_fn=prepared_hash_fn,
            )
            rows = rows_by_source.get(source_id) or []
            for row in rows:
                if not _same_path(row.get("_trace_original_source_path"), original):
                    raise RuntimeError(
                        "trace original source path does not match source map"
                    )
                if Path(str(row.get("_trace_source_path") or "")).name != name:
                    raise RuntimeError(
                        "trace staged source filename does not match queue"
                    )
            period = str(info.get("period") or "")
            if not re.fullmatch(r"20\d{4}", period):
                raise RuntimeError("source map period is invalid")
            record, audit = _resolve_capped_item_record(
                rows=rows,
                queue_item=queue_item,
                file_name=name,
                source_id=source_id,
                staged_path=staged,
                original_path=original,
                source_hash=source_hash,
                input_hash=input_hash,
                period=period,
            )
            refreshed.append(
                {
                    "file_name": name,
                    "source_item_id": source_id,
                    "record": record,
                    "history_audit": audit,
                }
            )
        except (OSError, RuntimeError, ValueError) as exc:
            failures.append(
                {
                    "file_name": name,
                    "source_item_id": source_id,
                    "reason": str(exc),
                    "reason_key": "apply_revalidation_failed",
                }
            )
        if index == 1 or index % 10 == 0 or index == len(candidates):
            _emit_progress(
                progress_fn,
                phase="apply_revalidation",
                processed=index,
                total=len(candidates),
                safe=len(refreshed),
                unresolved=len(failures),
                unit="photos",
            )
    return refreshed, failures, trace_lines, matched_rows


def resolve_queue(
    *,
    staging_dir: Path,
    trace_path: Path,
    result_file: Path,
    upload_output_dir: Path,
    apply: bool = False,
    include_items: bool = False,
    prepared_hash_fn: Callable[[Path], str] = _production_input_sha256,
    enqueue_fn: Callable[..., Path | None] = enqueue_finalized_result,
    apply_guard_fn: Callable[..., Mapping[str, Any]] = _require_quiesced_backend,
    backend_status_url: str = "http://127.0.0.1:5002/api/status",
    progress_fn: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Dry-run or apply every currently safe capped-queue resolution."""

    staging_dir = staging_dir.resolve()
    trace_path = trace_path.resolve()
    result_file = result_file.resolve()
    upload_output_dir = upload_output_dir.resolve()
    queue_path = staging_dir / ".ocr_capped_adjudication_queue.json"
    if not staging_dir.is_dir() or not trace_path.is_file():
        raise RuntimeError("staging directory or trace file is unavailable")
    queue = _queue_payload(queue_path, staging_dir)
    source_map = _source_map(staging_dir)
    source_items = source_map["items"]
    trace_paths = _trace_paths_for_source_map(trace_path, source_items)
    queue_items = [dict(item) for item in queue["items"]]
    queue_by_source = {
        str(item["source_item_id"]).strip().lower(): item for item in queue_items
    }
    _emit_progress(
        progress_fn,
        phase="starting",
        processed=0,
        total=len(queue_items),
        safe=0,
        unresolved=0,
        unit="photos",
    )
    rows_by_source, trace_lines, matched_rows = _scan_traces(
        trace_paths,
        queue_by_source,
        progress_fn=progress_fn,
        phase="preflight_trace_scan" if not apply else "apply_initial_trace_scan",
    )

    candidates: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    for index, item in enumerate(queue_items, start=1):
        file_name = str(item["file_name"])
        source_id = str(item["source_item_id"]).strip().lower()
        try:
            info = source_items.get(file_name)
            if not isinstance(info, dict):
                raise RuntimeError("source map has no exact queued photo")
            staged, original, source_hash, input_hash = _validate_item_binding(
                staging_dir=staging_dir,
                queue_item=item,
                source_info=info,
                prepared_hash_fn=prepared_hash_fn,
            )
            rows = rows_by_source.get(source_id) or []
            for row in rows:
                if not _same_path(row.get("_trace_original_source_path"), original):
                    raise RuntimeError("trace original source path does not match source map")
                if Path(str(row.get("_trace_source_path") or "")).name != file_name:
                    raise RuntimeError("trace staged source filename does not match queue")
            period = str(info.get("period") or "")
            if not re.fullmatch(r"20\d{4}", period):
                raise RuntimeError("source map period is invalid")
            record, audit = _resolve_capped_item_record(
                rows=rows,
                queue_item=item,
                file_name=file_name,
                source_id=source_id,
                staged_path=staged,
                original_path=original,
                source_hash=source_hash,
                input_hash=input_hash,
                period=period,
            )
            candidates.append(
                {
                    "file_name": file_name,
                    "source_item_id": source_id,
                    "record": record,
                    "history_audit": audit,
                }
            )
        except (OSError, RuntimeError, ValueError) as exc:
            reason = _reason_key(exc)
            reasons[reason] += 1
            unresolved.append(
                {
                    "file_name": file_name,
                    "source_item_id": source_id,
                    "reason": str(exc),
                    "reason_key": reason,
                    "history_audit": _history_audit(
                        rows_by_source.get(source_id) or []
                    ),
                }
            )
        if index == 1 or index % 10 == 0 or index == len(queue_items):
            _emit_progress(
                progress_fn,
                phase="preflight_evidence" if not apply else "apply_evidence",
                processed=index,
                total=len(queue_items),
                safe=len(candidates),
                unresolved=len(unresolved),
                unit="photos",
            )

    applied_records: list[dict[str, Any]] = []
    enqueue_failures: list[dict[str, str]] = []
    appended_count = 0
    removed_count = 0
    receipt_path: Path | None = None
    backend_proof: dict[str, Any] = {}
    apply_revalidation_failures: list[dict[str, str]] = []
    apply_trace_lines = 0
    apply_matched_rows = 0
    if apply and (candidates or unresolved):
        # Share the canonical queue lock with the lifetime-cap migration tool.
        # A resolver-only lock would allow two read/modify/replace writers to
        # race on the same durable queue.
        lock_path = staging_dir / ".ocr_capped_adjudication_queue.lock"
        with _exclusive_lock(lock_path):
            backend_proof = dict(
                apply_guard_fn(
                    staging_dir=staging_dir,
                    result_file=result_file,
                    upload_output_dir=upload_output_dir,
                    status_url=backend_status_url,
                )
            )

            def recheck_backend_pause() -> None:
                proof = dict(
                    apply_guard_fn(
                        staging_dir=staging_dir,
                        result_file=result_file,
                        upload_output_dir=upload_output_dir,
                        status_url=backend_status_url,
                    )
                )
                expected_pause = backend_proof.get("pipeline_pause")
                current_pause = proof.get("pipeline_pause")
                if (
                    isinstance(expected_pause, Mapping)
                    and dict(expected_pause) != dict(current_pause or {})
                ):
                    raise RuntimeError(
                        "backend pipeline pause changed during resolver transaction"
                    )

            (
                refreshed_candidates,
                apply_revalidation_failures,
                apply_trace_lines,
                apply_matched_rows,
            ) = _revalidate_candidates_for_apply(
                staging_dir=staging_dir,
                trace_path=trace_path,
                candidates=candidates,
                prepared_hash_fn=prepared_hash_fn,
                progress_fn=progress_fn,
            )
            recheck_backend_pause()
            (
                apply_candidates,
                terminal_conflicts,
            ) = _partition_terminal_conflicts(
                result_file,
                refreshed_candidates,
            )
            apply_revalidation_failures.extend(terminal_conflicts)
            enqueue_successes: list[dict[str, Any]] = []
            for index, candidate in enumerate(apply_candidates):
                # Keep the persistent pause observable throughout a large
                # transaction.  The supervisor respects this pause; checking
                # every small chunk also fails closed if an operator starts
                # the backend while upload intents are being prepared.
                if index % 25 == 0:
                    recheck_backend_pause()
                name = str(candidate["file_name"])
                record = dict(candidate["record"])
                try:
                    queued = enqueue_fn(record, output_dir=upload_output_dir)
                    if queued is None:
                        raise RuntimeError("verified terminal did not pass upload gate")
                    record["stream_upload_queued"] = True
                    enqueue_successes.append(
                        {**candidate, "record": record, "queued_job": str(queued)}
                    )
                except (OSError, RuntimeError, ValueError) as exc:
                    enqueue_failures.append(
                        {
                            "file_name": name,
                            "source_item_id": str(candidate["source_item_id"]),
                            "reason": str(exc),
                            "reason_key": "upload_enqueue_failed",
                        }
                    )
                completed = index + 1
                if (
                    completed == 1
                    or completed % 10 == 0
                    or completed == len(apply_candidates)
                ):
                    _emit_progress(
                        progress_fn,
                        phase="upload_enqueue",
                        processed=completed,
                        total=len(apply_candidates),
                        safe=len(enqueue_successes),
                        unresolved=len(enqueue_failures),
                        unit="photos",
                    )

            # All accepted upload intents are durable before the first terminal
            # result is written.
            recheck_backend_pause()
            appended_count, completed_names = _append_terminal_results(
                result_file,
                [item["record"] for item in enqueue_successes],
            )
            completed = set(completed_names)
            applied_records = [
                item for item in enqueue_successes if item["file_name"] in completed
            ]
            resolved_bindings = {
                str(item["file_name"]): str(item["source_item_id"])
                for item in applied_records
            }
            unresolved_by_name: dict[str, dict[str, str]] = {
                str(item["file_name"]): {
                    "source_item_id": str(item["source_item_id"]),
                    "reason": str(item["reason"]),
                    "reason_key": str(item["reason_key"]),
                }
                for item in unresolved
            }
            for failure in [
                *apply_revalidation_failures,
                *enqueue_failures,
            ]:
                unresolved_by_name[str(failure["file_name"])] = {
                    "source_item_id": str(failure["source_item_id"]),
                    "reason": str(failure["reason"]),
                    "reason_key": str(failure["reason_key"]),
                }
            # Queue removal is the final durable mutation.
            recheck_backend_pause()
            removed_count = _remove_resolved_queue_items(
                queue_path=queue_path,
                staging_dir=staging_dir,
                resolved=resolved_bindings,
                unresolved_reasons=unresolved_by_name,
            )
            if removed_count != len(resolved_bindings):
                raise RuntimeError("not every persisted terminal was removed from queue")

            receipt = {
                "schema": RECEIPT_SCHEMA,
                "status": (
                    "partial_failure"
                    if enqueue_failures or apply_revalidation_failures
                    else "resolved"
                ),
                "resolution_rule": RESOLUTION_RULE,
                "resolved_at": datetime.now().astimezone().isoformat(),
                "staging_dir": str(staging_dir),
                "queue_path": str(queue_path),
                "trace_path": str(trace_path),
                "trace_paths": [str(path) for path in trace_paths],
                "result_file": str(result_file),
                "upload_output_dir": str(upload_output_dir),
                "model_calls_made": 0,
                "fourth_call_authorized": False,
                "backend_quiescence_proof": backend_proof,
                "backend_reload_required_before_resume": True,
                "queue_count_before": len(queue_items),
                "safe_count": len(candidates),
                "apply_revalidated_count": len(refreshed_candidates),
                "enqueued_count": len(enqueue_successes),
                "terminal_appended_count": appended_count,
                "queue_removed_count": removed_count,
                "unresolved_count": len(unresolved),
                "apply_revalidation_failures": apply_revalidation_failures,
                "enqueue_failures": enqueue_failures,
                "resolved": [
                    {
                        "file_name": item["file_name"],
                        "source_item_id": item["source_item_id"],
                        "queued_job": item["queued_job"],
                        "view_type": item["record"].get("view_type"),
                        "model": item["record"].get("model"),
                        "price": item["record"].get("price"),
                        "adjudication_rule": item["record"].get(
                            "adjudication_rule"
                        ),
                        "history_audit": item["history_audit"],
                    }
                    for item in applied_records
                ],
                "unresolved": [
                    *unresolved,
                    *apply_revalidation_failures,
                    *enqueue_failures,
                ],
            }
            receipt_path = (
                upload_output_dir
                / "_ocr_audit"
                / "capped_adjudication_resolution"
                / f"{datetime.now():%Y%m%d_%H%M%S_%f}.json"
            )
            _atomic_json(receipt_path, receipt)

    report: dict[str, Any] = {
        "status": (
            "partial_failure"
            if apply and (enqueue_failures or apply_revalidation_failures)
            else "resolved"
            if apply
            else "dry_run"
        ),
        "queue_count": len(queue_items),
        "safe_count": len(candidates),
        "unresolved_count": len(unresolved),
        "enqueued_count": len(applied_records) if apply else 0,
        "terminal_appended_count": appended_count if apply else 0,
        "queue_removed_count": removed_count if apply else 0,
        "queue_remaining_count": (
            len(_queue_payload(queue_path, staging_dir)["items"])
            if apply
            else len(queue_items)
        ),
        "trace_scan_passes": 2 if apply and candidates else 1,
        "trace_file_count": len(trace_paths),
        "trace_paths": [str(path) for path in trace_paths],
        "trace_lines_scanned": trace_lines + apply_trace_lines,
        "trace_rows_matched": matched_rows + apply_matched_rows,
        "model_calls_made": 0,
        "fourth_call_authorized": False,
        "service_or_fuse_touched": False,
        "unresolved_reasons": dict(reasons.most_common()),
        "safe_price_statuses": dict(
            Counter(
                str(item["record"].get("price_status") or "")
                for item in candidates
            ).most_common()
        ),
        "receipt_path": str(receipt_path) if receipt_path else "",
        "backend_reload_required_before_resume": bool(apply),
    }
    if apply:
        report["backend_quiescence_proof"] = backend_proof
        report["apply_revalidation_failures"] = apply_revalidation_failures
        report["enqueue_failures"] = enqueue_failures
    if include_items:
        report["candidates"] = [
            {
                "file_name": item["file_name"],
                "source_item_id": item["source_item_id"],
                "view_type": item["record"].get("view_type"),
                "model": item["record"].get("model"),
                "price": item["record"].get("price"),
                "price_status": item["record"].get("price_status"),
                "official_price": item["record"].get("official_price"),
                "adjudication_rule": item["record"].get("adjudication_rule"),
                "adjudication_decision_source": item["record"].get(
                    "adjudication_decision_source"
                ),
                "hard_cap_consumed_attempts": item["record"].get(
                    "hard_cap_consumed_attempts"
                ),
                "model_outputs_available": item["record"].get(
                    "model_outputs_available"
                ),
                "model_outputs_observed": item["record"].get(
                    "model_outputs_observed"
                ),
                "history_audit": item["history_audit"],
            }
            for item in candidates
        ]
        report["unresolved"] = unresolved
    _emit_progress(
        progress_fn,
        phase="complete",
        processed=len(queue_items),
        total=len(queue_items),
        safe=len(candidates),
        unresolved=report["queue_remaining_count"],
        enqueued=report["enqueued_count"],
        unit="photos",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--trace-path", type=Path, required=True)
    parser.add_argument("--result-file", type=Path, required=True)
    parser.add_argument("--upload-output-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--include-items", action="store_true")
    parser.add_argument(
        "--backend-status-url",
        default="http://127.0.0.1:5002/api/status",
    )
    args = parser.parse_args()
    report = resolve_queue(
        staging_dir=args.staging_dir,
        trace_path=args.trace_path,
        result_file=args.result_file,
        upload_output_dir=args.upload_output_dir,
        apply=args.apply,
        include_items=args.include_items,
        backend_status_url=args.backend_status_url,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if report["status"] == "partial_failure" else 0


if __name__ == "__main__":
    raise SystemExit(main())

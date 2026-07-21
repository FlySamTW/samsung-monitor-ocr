"""Deterministically close old three-call review rows without a fourth model call."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.audit_fields import (
    EVIDENCE_GUARD_REVISION,
    KNOWN_SOURCE_EXPECTATIONS,
    apply_human_audited_pixel_authority,
    clear_superseded_terminal_content_flags,
    finalize_three_pass_outcome,
    has_sufficient_followme_physical_evidence,
    refresh_authoritative_price_comparison,
    validate_evidence_contract,
)
from skills.model_catalog_rules import normalize_samsung_model
from skills.model_validation import normalize_model_token, strict_known_model
from tools.stream_drive_upload import enqueue_finalized_result


AUTHORITY_MANIFEST_SCHEMA = "samsung-ocr-bound-visual-authorities/v1"
MODEL_LIST_PATH = Path(__file__).resolve().parents[1] / "型號表.txt"


def _atomic_json(path: Path, payload: Any) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _task_file_name(task: dict[str, Any]) -> str:
    return Path(str((task.get("data") or {}).get("image") or "")).name


def _sync_label_studio_annotation(
    task: dict[str, Any],
    row: dict[str, Any],
) -> None:
    """Keep the legacy Label Studio payload aligned with ``ocr_meta``.

    Dashboard history still reads category/model/price from the first Label
    Studio annotation.  Offline adjudication therefore must update both
    representations atomically; updating only ``ocr_meta`` can display and
    reload the pre-adjudication answer even though the upload row is correct.
    """
    category = str(row.get("view_type") or row.get("category") or "單機").strip()
    model = row.get("model")
    price = row.get("price")
    result_items = [
        {
            "from_name": "category",
            "to_name": "image",
            "type": "choices",
            "origin": "prediction",
            "value": {"choices": [category]},
        },
        {
            "from_name": "model",
            "to_name": "image",
            "type": "textarea",
            "origin": "prediction",
            "value": {"text": [str(model) if model else "null"]},
        },
        {
            "from_name": "price",
            "to_name": "image",
            "type": "textarea",
            "origin": "prediction",
            "value": {"text": [str(price) if price else "null"]},
        },
    ]
    annotations = task.get("annotations")
    if not isinstance(annotations, list):
        annotations = []
        task["annotations"] = annotations
    if annotations and isinstance(annotations[0], dict):
        annotation = annotations[0]
    else:
        annotation = {
            "id": task.get("id") or 1,
            "created_at": row.get("timestamp") or row.get("completed_at") or "",
        }
        annotations.insert(0, annotation)
    annotation["result"] = result_items
    annotation.setdefault("was_cancelled", False)
    annotation.setdefault("ground_truth", False)


def _append_final_presentation_events(
    output_dir: Path,
    rows: list[dict[str, Any]],
) -> None:
    """Persist a terminal replacement card without counting a fourth pass.

    Pass cards are immutable audit records.  A later deterministic
    adjudication therefore gets its own terminal event, reusing the last real
    model-call sequence for this source.  It is written to a separate JSONL so
    a live backend can keep appending its normal presentation stream without a
    cross-process write race.
    """
    candidates: dict[str, tuple[dict[str, Any], str]] = {}
    for row in rows:
        source_item_id = str(row.get("source_item_id") or "").strip()
        file_name = str(row.get("file_name") or "").strip()
        if not source_item_id or not file_name:
            raise RuntimeError(f"terminal presentation identity missing for {file_name}")
        identity_seed = "|".join(
            (
                source_item_id,
                str(row.get("evidence_guard_revision") or ""),
                str(row.get("adjudication_rule") or ""),
                str(row.get("view_type") or ""),
                str(row.get("model") or ""),
                str(row.get("price") or ""),
            )
        )
        presentation_id = "p-final-" + hashlib.sha256(
            identity_seed.encode("utf-8")
        ).hexdigest()[:18]
        candidates[source_item_id] = (row, presentation_id)
    if not candidates:
        return

    history_dir = output_dir.resolve() / "_ocr_audit" / "presentation_history"
    history_dir.mkdir(parents=True, exist_ok=True)
    latest_source_events: dict[str, dict[str, Any]] = {}
    existing_ids: set[str] = set()
    for path in sorted(
        history_dir.glob("presentation_*.jsonl*"),
        key=lambda item: item.stat().st_mtime,
    ):
        try:
            opener = gzip.open if path.suffix.lower() == ".gz" else open
            with opener(path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    if '"source_item_id"' not in line:
                        continue
                    item = json.loads(line)
                    source_item_id = str(item.get("source_item_id") or "")
                    candidate = candidates.get(source_item_id)
                    if candidate is None:
                        continue
                    if item.get("presentation_id") == candidate[1]:
                        existing_ids.add(candidate[1])
                    latest_source_event = latest_source_events.get(source_item_id) or {}
                    if (
                        int(item.get("presentation_sequence") or 0),
                        str(item.get("completed_at") or item.get("started_at") or ""),
                    ) >= (
                        int(latest_source_event.get("presentation_sequence") or 0),
                        str(
                            latest_source_event.get("completed_at")
                            or latest_source_event.get("started_at")
                            or ""
                        ),
                    ):
                        latest_source_events[source_item_id] = item
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue

    result_keys = (
        "view_type", "screen_status", "quality_issue", "model", "price",
        "category", "price_symbol", "price_status", "official_price",
        "price_diff_percent", "auto_review_required", "review_status",
        "auto_verified", "technical_retry_exhausted",
        "three_pass_adjudicated", "adjudication_rule",
        "adjudication_summary",
    )
    events: list[dict[str, Any]] = []
    for source_item_id, (row, presentation_id) in candidates.items():
        if presentation_id in existing_ids:
            continue
        latest_source_event = latest_source_events.get(source_item_id) or {}
        timestamp = datetime.now().isoformat(timespec="microseconds")
        narration = str(
            row.get("adjudication_summary")
            or row.get("thinking")
            or row.get("narration")
            or ""
        ).strip()
        result = {key: row.get(key) for key in result_keys}
        events.append({
            "presentation_id": presentation_id,
            "presentation_sequence": int(
                latest_source_event.get("presentation_sequence") or 0
            ),
            "source_item_id": source_item_id,
            "run_id": str(
                row.get("run_id") or latest_source_event.get("run_id") or ""
            ),
            "file_name": str(row.get("file_name") or ""),
            "source_path": str(
                row.get("original_source_path") or row.get("source_path") or ""
            ),
            "pass_index": 3,
            "pass_label": "第三輪終局定案",
            "ocr_attempt": 3,
            "retry_reason": [],
            "model_id": str(row.get("model_id") or ""),
            "accuracy_profile": str(row.get("accuracy_profile") or "strict"),
            "evidence_contract_version": str(
                row.get("evidence_contract_version") or ""
            ),
            "evidence_guard_revision": str(
                row.get("evidence_guard_revision") or ""
            ),
            "started_at": timestamp,
            "completed_at": timestamp,
            "previous_result_summary": {},
            "full_ai_narration": narration,
            "narration": narration,
            "stream_buffer": narration,
            "structured_result": result,
            "decision": "accepted",
            "result": result,
        })
    if not events:
        return
    path = history_dir / (
        f"presentation_finalization_{datetime.now().strftime('%Y%m%d')}.jsonl"
    )
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for event in events:
            handle.write(
                json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
        handle.flush()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_authority_manifest(path: Path) -> int:
    """Load exact hash-bound visual authorities for this offline run only."""
    payload = json.loads(path.resolve().read_text(encoding="utf-8-sig"))
    if payload.get("schema") != AUTHORITY_MANIFEST_SCHEMA:
        raise RuntimeError("unexpected visual authority manifest schema")
    entries = list(payload.get("entries") or [])
    if int(payload.get("entry_count") or 0) != len(entries) or not entries:
        raise RuntimeError("visual authority manifest is empty or inconsistent")
    seen_sources: set[str] = set()
    seen_hashes: set[str] = set()
    for entry in entries:
        source_id = str(entry.get("source_item_id") or "")
        image_hash = str(entry.get("input_image_sha256") or "").strip().lower()
        source_hash = str(entry.get("source_file_sha256") or "").strip().lower()
        original = Path(str(entry.get("original_source_path") or "")).resolve()
        if (
            not re.fullmatch(r"[0-9a-f]{64}", source_id)
            or not re.fullmatch(r"[0-9a-f]{64}", image_hash)
            or not re.fullmatch(r"[0-9a-f]{64}", source_hash)
            or source_id in seen_sources
            or image_hash in seen_hashes
            or not original.is_file()
            or _sha256_file(original) != source_hash
            or entry.get("authority") != "human_audited_pixel_authority"
        ):
            raise RuntimeError(
                f"invalid or stale visual authority: {entry.get('file_name')}"
            )
        KNOWN_SOURCE_EXPECTATIONS[image_hash] = entry
        seen_sources.add(source_id)
        seen_hashes.add(image_hash)
    return len(entries)


def _review_required(task: dict[str, Any]) -> bool:
    meta = (task.get("data") or {}).get("ocr_meta") or {}
    return meta.get("auto_review_required") is True or meta.get("auto_verified") is not True


def _inject_cleared_photo_local_fuse_calls(
    trace_path: Path,
    grouped: dict[tuple[str, str], list[dict[str, Any]]],
    source_grouped: dict[tuple[str, str], list[dict[str, Any]]],
) -> None:
    """Restore a consumed bound call archived before its trace append.

    Only an explicit clearance receipt created after code/test verification may
    bridge the missing trace.  The receipt, archived fuse, neighboring trace
    rows, source identity, full-image hash, run, attempt and request ID must all
    agree.  This reconstructs audit evidence; it never performs another model
    call.
    """
    audit_dir = trace_path.parent.resolve()
    clearance_dir = audit_dir / "runtime_health_fuse_clearance"
    history_dir = (audit_dir / "runtime_health_fuse_history").resolve()
    if not clearance_dir.is_dir() or not history_dir.is_dir():
        return

    for receipt_path in sorted(clearance_dir.glob("*.json")):
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
            if receipt.get("schema") != "samsung-ocr-runtime-fuse-clearance/v1":
                continue
            if receipt.get("recovery") != (
                "persist_fused_bound_pass_as_photo_local_history_then_resume_call_3"
            ):
                continue
            name = str(receipt.get("source_file") or "")
            source_id = str(receipt.get("source_item_id") or "")
            image_hash = str(receipt.get("input_image_sha256") or "").strip().lower()
            request_id = str(receipt.get("recovered_request_id") or "")
            archive = Path(str(receipt.get("archived_fuse") or "")).resolve()
            if (
                not name
                or not re.fullmatch(r"[0-9a-f]{64}", source_id)
                or not re.fullmatch(r"[0-9a-f]{64}", image_hash)
                or not re.fullmatch(r"[0-9a-f]{32}", request_id)
                or archive.parent != history_dir
                or not archive.is_file()
            ):
                continue
            fuse = json.loads(archive.read_text(encoding="utf-8-sig"))
            attempt = int(fuse.get("attempt") or 0)
            run_id = str(fuse.get("run_id") or "")
            reasons = [str(item) for item in fuse.get("reasons") or [] if str(item)]
            snapshot = fuse.get("record_snapshot") or {}
            raw = json.loads(str(snapshot.get("raw_model_output") or ""))
            if (
                fuse.get("source_file") != name
                or attempt not in {1, 2}
                or reasons != ["structured_authority_material_conflict:model"]
                or not run_id
                or str(raw.get("request_id") or "") != request_id
            ):
                continue

            anchors = [
                item
                for item in grouped.get((name, run_id), [])
                if str(item.get("source_item_id") or "") == source_id
                and str(item.get("input_image_sha256") or "").strip().lower()
                == image_hash
            ]
            if not anchors or any(
                int(item.get("ocr_attempt") or 0) == attempt for item in anchors
            ):
                continue
            anchor = anchors[-1]
            narration = str(snapshot.get("narration") or "")
            call = {
                "view_type": snapshot.get("view_type"),
                "category": snapshot.get("category") or snapshot.get("view_type"),
                "model": snapshot.get("model"),
                "price": snapshot.get("price"),
                "screen_status": raw.get("screen_status"),
                "quality_issue": raw.get("quality_issue"),
                "complete_screen_count": snapshot.get("complete_screen_count"),
                "unique_main": snapshot.get("unique_main"),
                "label_ownership": snapshot.get("label_ownership"),
                "followme_physical_evidence": (
                    snapshot.get("followme_physical_evidence") or []
                ),
                "structured_authority_blocked_fields": (
                    snapshot.get("structured_authority_blocked_fields") or []
                ),
                "normalized_evidence": {
                    "complete_screen_count": snapshot.get("complete_screen_count"),
                    "unique_main": snapshot.get("unique_main"),
                    "label_ownership": snapshot.get("label_ownership"),
                    "followme_physical_evidence": (
                        snapshot.get("followme_physical_evidence") or []
                    ),
                },
                "thinking": narration,
                "narration": narration,
                "raw_model_output": snapshot.get("raw_model_output"),
                "run_id": run_id,
                "timestamp": fuse.get("tripped_at"),
                "file_name": name,
                "source_item_id": source_id,
                "source_path": anchor.get("source_path"),
                "original_source_path": anchor.get("original_source_path"),
                "period": anchor.get("period"),
                "ocr_attempt": attempt,
                "input_image_sha256": image_hash,
                "request_binding_enforced": True,
                "request_id_verified": True,
                "independent_pass": True,
                "prior_answer_exposed": False,
                "prompt_contamination": False,
                "requires_structured_retry": False,
                "runtime_health": {
                    "healthy": False,
                    "allow_processing": True,
                    "allow_upload": False,
                    "reasons": reasons,
                    "contained_for_stateless_retry": True,
                },
                "recovered_from_archived_photo_local_fuse": True,
                "runtime_fuse_clearance_receipt": str(receipt_path),
                "evidence_guard_revision": receipt.get(
                    "evidence_guard_revision"
                ),
            }
            valid, _errors, normalized = validate_evidence_contract(call)
            if not valid:
                continue
            call["normalized_evidence"] = normalized
            grouped[(name, run_id)].append(call)
            source_grouped[(name, source_id)].append(call)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue


def _inject_contained_followme_scene_fuse_calls(
    trace_path: Path,
    grouped: dict[tuple[str, str], list[dict[str, Any]]],
    source_grouped: dict[tuple[str, str], list[dict[str, Any]]],
) -> None:
    """Restore a consumed FollowMe/scene-conflict call from its recovery proof."""
    audit_dir = trace_path.parent.resolve()
    recovery_dir = audit_dir / "content_fuse_recovery"
    history_dir = (audit_dir / "runtime_health_fuse_history").resolve()
    if not recovery_dir.is_dir() or not history_dir.is_dir():
        return
    allowed_reasons = {
        "distant_followme_strong_evidence_conflict",
        "structured_narration_followme_conflict",
    }

    for receipt_path in sorted(recovery_dir.glob("*.json")):
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
            if (
                receipt.get("status") != "recovered"
                or receipt.get("recovery_rule")
                != "same_photo_followme_scene_conflict_preserve_call_and_retry"
            ):
                continue
            name = str(receipt.get("file_name") or "")
            source_id = str(receipt.get("source_item_id") or "")
            attempt = int(receipt.get("consumed_calls") or 0)
            archive_text = str(receipt.get("fuse_history") or "")
            archive = Path(archive_text).resolve() if archive_text else None
            if archive is None or not archive.is_file():
                archive = None
                for candidate in sorted(
                    history_dir.glob(f"content_*_{source_id[:12]}.json")
                ):
                    candidate_payload = json.loads(
                        candidate.read_text(encoding="utf-8-sig")
                    )
                    recovery_receipt = Path(
                        str(candidate_payload.get("recovery_receipt") or "")
                    )
                    if (
                        recovery_receipt.name == receipt_path.name
                        and candidate_payload.get("clearance")
                        == "same_photo_followme_scene_conflict_preserve_call_and_retry"
                    ):
                        archive = candidate.resolve()
                        break
            if (
                not name
                or not re.fullmatch(r"[0-9a-f]{64}", source_id)
                or attempt not in {1, 2}
                or archive is None
                or archive.parent != history_dir
                or not archive.is_file()
            ):
                continue
            fuse = json.loads(archive.read_text(encoding="utf-8-sig"))
            run_id = str(fuse.get("run_id") or "")
            reasons = {
                str(item) for item in fuse.get("reasons") or [] if str(item)
            }
            snapshot = dict(fuse.get("record_snapshot") or {})
            raw = json.loads(str(snapshot.get("raw_model_output") or ""))
            request_id = str(raw.get("request_id") or "").strip().lower()
            if (
                fuse.get("source_file") != name
                or int(fuse.get("attempt") or 0) != attempt
                or not run_id
                or not reasons
                or not reasons <= allowed_reasons
                or not re.fullmatch(r"[0-9a-f]{32}", request_id)
            ):
                continue

            anchors = [
                item
                for item in source_grouped.get((name, source_id), [])
                if item.get("request_id_verified") is True
                and item.get("request_binding_enforced") is True
                and item.get("independent_pass") is True
                and item.get("prior_answer_exposed") is not True
                and item.get("prompt_contamination") is not True
            ]
            if not anchors or any(
                int(item.get("ocr_attempt") or 0) == attempt for item in anchors
            ):
                continue
            image_hashes = {
                str(item.get("input_image_sha256") or "").strip().lower()
                for item in anchors
            }
            if (
                len(image_hashes) != 1
                or not re.fullmatch(r"[0-9a-f]{64}", next(iter(image_hashes), ""))
            ):
                continue
            image_hash = next(iter(image_hashes))
            anchor = anchors[-1]
            narration = str(snapshot.get("narration") or "")
            call = {
                "view_type": snapshot.get("view_type"),
                "category": snapshot.get("category") or snapshot.get("view_type"),
                "model": snapshot.get("model"),
                "price": snapshot.get("price"),
                "screen_status": raw.get("screen_status"),
                "quality_issue": raw.get("quality_issue"),
                "complete_screen_count": snapshot.get("complete_screen_count"),
                "unique_main": snapshot.get("unique_main"),
                "label_ownership": snapshot.get("label_ownership"),
                "followme_physical_evidence": (
                    snapshot.get("followme_physical_evidence") or []
                ),
                "structured_authority_blocked_fields": (
                    snapshot.get("structured_authority_blocked_fields") or []
                ),
                "thinking": narration,
                "narration": narration,
                "raw_model_output": snapshot.get("raw_model_output"),
                "run_id": run_id,
                "timestamp": fuse.get("tripped_at"),
                "file_name": name,
                "source_item_id": source_id,
                "source_path": anchor.get("source_path"),
                "original_source_path": anchor.get("original_source_path"),
                "period": anchor.get("period"),
                "ocr_attempt": attempt,
                "input_image_sha256": image_hash,
                "request_binding_enforced": True,
                "request_id_verified": True,
                "independent_pass": True,
                "prior_answer_exposed": False,
                "prompt_contamination": False,
                "requires_structured_retry": False,
                "runtime_health": {
                    "healthy": False,
                    "allow_processing": True,
                    "allow_upload": False,
                    "reasons": sorted(reasons),
                    "contained_for_stateless_retry": True,
                },
                "runtime_health_contained_reasons": sorted(reasons),
                "recovered_from_contained_followme_scene_fuse": True,
                "content_fuse_recovery_receipt": str(receipt_path),
                "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
            }
            valid, errors, normalized = validate_evidence_contract(call)
            if set(errors) - {"distant_followme_physical_conflict"}:
                continue
            call["evidence_contract_valid"] = valid
            call["contained_contract_errors"] = sorted(set(errors))
            call["normalized_evidence"] = normalized
            grouped[(name, run_id)].append(call)
            source_grouped[(name, source_id)].append(call)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue


def _load_three_call_groups(trace_path: Path) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    source_grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    with trace_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except (TypeError, ValueError):
                continue
            name = str(row.get("file_name") or "")
            run_id = str(row.get("run_id") or "")
            parsed = row.get("parsed_output") or {}
            if name and run_id and isinstance(parsed, dict):
                item = dict(parsed)
                item.setdefault("run_id", run_id)
                item.setdefault("timestamp", row.get("timestamp"))
                item.setdefault("source_item_id", row.get("source_item_id"))
                # Finalization is an upload-producing repair. Trial/smoke
                # traces intentionally have no canonical period and must never
                # outrank the formal source merely because they are newer.
                if not re.fullmatch(r"20\d{4}", str(item.get("period") or "")):
                    continue
                grouped[(name, run_id)].append(item)
                source_id = str(item.get("source_item_id") or "")
                if source_id:
                    source_grouped[(name, source_id)].append(item)

    _inject_cleared_photo_local_fuse_calls(trace_path, grouped, source_grouped)
    _inject_contained_followme_scene_fuse_calls(
        trace_path,
        grouped,
        source_grouped,
    )

    latest: dict[str, list[dict[str, Any]]] = {}
    for (name, _run_id), rows in grouped.items():
        rows = sorted(
            rows,
            key=lambda item: (
                str(item.get("timestamp") or ""),
                int(item.get("ocr_attempt") or 0),
            ),
        )
        if len(rows) < 2:
            continue
        candidate = rows[-3:]
        if int(candidate[-1].get("ocr_attempt") or 0) != 3:
            continue
        previous = latest.get(name)
        if previous is None or str(candidate[-1].get("timestamp") or "") >= str(previous[-1].get("timestamp") or ""):
            latest[name] = candidate

    # A durable fuse may end one process after a call is consumed but before
    # that call's trace append, then resume the exact same source at call 3.
    # Join only the latest adjacent bound tail ending at call 3
    # when source identity and full-image hash are identical; this cannot mix a
    # smoke copy or an older photo revision into the recovery evidence.
    for (name, _source_id), rows in source_grouped.items():
        ordered = sorted(rows, key=lambda item: str(item.get("timestamp") or ""))
        recovered_three_call_group = False
        for index in range(len(ordered) - 1, 1, -1):
            candidate = ordered[index - 2 : index + 1]
            attempts = [int(item.get("ocr_attempt") or 0) for item in candidate]
            hashes = {
                str(item.get("input_image_sha256") or "").strip().lower()
                for item in candidate
            }
            if attempts != [1, 2, 3] or "" in hashes or len(hashes) != 1:
                continue
            previous = latest.get(name)
            if previous is None or (
                str(candidate[-1].get("timestamp") or "")
                > str(previous[-1].get("timestamp") or "")
            ):
                latest[name] = candidate
            recovered_three_call_group = True
            break
        if recovered_three_call_group:
            continue
        for index in range(len(ordered) - 1, 0, -1):
            candidate = ordered[index - 1 : index + 1]
            attempts = [int(item.get("ocr_attempt") or 0) for item in candidate]
            hashes = {
                str(item.get("input_image_sha256") or "").strip().lower()
                for item in candidate
            }
            if attempts not in ([1, 3], [2, 3]) or "" in hashes or len(hashes) != 1:
                continue
            previous = latest.get(name)
            if previous is None or (
                str(candidate[-1].get("timestamp") or "")
                > str(previous[-1].get("timestamp") or "")
            ):
                latest[name] = candidate
            break
    return latest


def _recover_full_official_sku_consensus(
    calls: list[dict[str, Any]],
) -> str | None:
    """Restore a catalog SKU lost only by full-code/short-code normalization.

    The immutable pass trace remains untouched.  Recovery requires at least
    two independent, request-bound single-unit calls whose raw JSON names the
    same catalog model and whose label belongs to that one complete subject.
    This closes a post-processing defect without performing a fourth model
    call or accepting a nearby/background label.
    """
    try:
        valid_models = [
            line.strip()
            for line in MODEL_LIST_PATH.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeError):
        return None
    if not valid_models:
        return None

    votes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for call in calls:
        evidence = call.get("normalized_evidence") or call
        if (
            call.get("request_id_verified") is not True
            or call.get("prior_answer_exposed") is True
            or call.get("prompt_contamination") is True
            or str(call.get("view_type") or "") != "單機"
            or evidence.get("complete_screen_count") != 1
            or evidence.get("unique_main") is not True
            or str(evidence.get("label_ownership") or "") != "matched"
        ):
            continue
        call_models: set[str] = set()
        for raw in call.get("raw_objects") or []:
            try:
                payload = json.loads(raw) if isinstance(raw, str) else raw
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            raw_model = payload.get("model")
            normalized_raw = normalize_samsung_model(raw_model)
            catalog_model = strict_known_model(raw_model, valid_models)
            if (
                not catalog_model
                or not normalized_raw.startswith("S")
                or payload.get("complete_screen_count") != 1
                or payload.get("unique_main") is not True
                or str(payload.get("label_ownership") or "") != "matched"
                or str(payload.get("view_type") or "") != "單機"
            ):
                continue
            call_models.add(catalog_model)
        if len(call_models) == 1:
            votes[next(iter(call_models))].append(call)

    winners = [
        (model, rows)
        for model, rows in votes.items()
        if len(rows) >= 2
    ]
    if len(winners) != 1:
        return None
    model, rows = winners[0]
    for call in rows:
        call["model"] = model
        call["model_validation_failed"] = False
        call["rejected_model"] = ""
        call["structured_authority_blocked_fields"] = [
            field
            for field in call.get("structured_authority_blocked_fields") or []
            if field != "model"
        ]
        if call.get("price"):
            call["quality_issue"] = "無"
    return model


def _raw_single_model_price_consensus(
    calls: list[dict[str, Any]],
) -> tuple[str, str] | None:
    """Recover a model/price pair lost only after clean structured output.

    This is deliberately narrower than ordinary majority voting.  All three
    calls must be healthy, independently request-bound views of the same full
    image, and every usable raw pair must agree.  At least two raw JSON objects
    must identify the same catalog Samsung SKU and price on the matched unique
    subject.  Prices at or below 2,000, multiple current-price amounts, and any
    contradictory raw pair are refused.  A sole ``建議售價`` is accepted because
    it is the only store-card amount visible in that call.
    """
    if len(calls) != 3:
        return None
    image_hashes = {
        str(call.get("input_image_sha256") or "").strip().lower()
        for call in calls
    }
    attempts = sorted(int(call.get("ocr_attempt") or 0) for call in calls)
    if "" in image_hashes or len(image_hashes) != 1 or attempts != [1, 2, 3]:
        return None
    try:
        valid_models = [
            line.strip()
            for line in MODEL_LIST_PATH.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeError):
        return None
    if not valid_models:
        return None

    pair_votes: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for call in calls:
        runtime = call.get("runtime_health") or {}
        if (
            call.get("request_id_verified") is not True
            or call.get("independent_pass") is not True
            or call.get("prior_answer_exposed") is True
            or call.get("prompt_contamination") is True
            or not isinstance(runtime, dict)
            or runtime.get("healthy") is not True
        ):
            return None
        call_pairs: set[tuple[str, str]] = set()
        for raw in call.get("raw_objects") or []:
            try:
                payload = json.loads(raw) if isinstance(raw, str) else raw
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            if (
                str(payload.get("view_type") or "").strip() != "單機"
                or payload.get("complete_screen_count") not in {1, 2}
                or payload.get("unique_main") is not True
                or str(payload.get("label_ownership") or "").strip() != "matched"
            ):
                continue
            model = strict_known_model(payload.get("model"), valid_models)
            price = re.sub(r"[^0-9]", "", str(payload.get("price") or ""))
            if not model or not price or not price.isdigit() or int(price) <= 2000:
                continue
            if model.startswith("FollowMe") and not has_sufficient_followme_physical_evidence(
                payload
            ):
                continue
            narration = str(payload.get("narration") or "")
            formatted = f"{int(price):,}"
            if not re.search(
                rf"(?:{re.escape(price)}|{re.escape(formatted)})", narration
            ):
                continue
            current_amounts = {
                re.sub(r"[^0-9]", "", match.group(1))
                for match in re.finditer(
                    r"(?:促銷價|會員價|現金價|特價|優惠價|現價|(?<!建議)售價)"
                    r".{0,12}(\d{1,3}(?:,\d{3})+|\d{4,6})",
                    narration,
                    re.IGNORECASE,
                )
            }
            if any(amount and amount != price for amount in current_amounts):
                continue
            call_pairs.add((model, price))
        if len(call_pairs) > 1:
            return None
        if len(call_pairs) == 1:
            pair_votes[next(iter(call_pairs))].append(call)

    # Any clean alternative pair is a real conflict, even if another pair has
    # a two-to-one majority.  Such a photo needs visual authority, not guessing.
    if len(pair_votes) != 1:
        return None
    pair, rows = next(iter(pair_votes.items()))
    if len(rows) < 2:
        return None
    return pair


def _calls_bind_to_result_source(
    calls: list[dict[str, Any]], result_path: Path, file_name: str
) -> bool:
    """Prove that cross-staging traces contain the exact current photo bytes."""
    if len(calls) != 3 or not file_name:
        return False
    target = (result_path.resolve().parent / file_name).resolve()
    source_paths = [Path(str(call.get("source_path") or "")).resolve() for call in calls]
    original_paths = {
        Path(str(call.get("original_source_path") or "")).resolve()
        for call in calls
    }
    source_ids = {str(call.get("source_item_id") or "") for call in calls}
    if (
        not target.is_file()
        or any(not path.is_file() or path.name != file_name for path in source_paths)
        or len(original_paths) != 1
        or len(source_ids) != 1
        or "" in source_ids
    ):
        return False
    original = next(iter(original_paths))
    source_id = next(iter(source_ids))
    if not original.is_file() or original.name != file_name:
        return False
    expected_source_id = hashlib.sha256(
        str(original).casefold().encode("utf-8")
    ).hexdigest()
    if source_id != expected_source_id:
        return False
    hashes = {_sha256_file(path) for path in [target, original, *source_paths]}
    return len(hashes) == 1


def _recover_known_authority_after_restart(
    current: dict[str, Any], calls: list[dict[str, Any]], meta: dict[str, Any]
) -> bool:
    """Recover one process-boundary missing trace without a fourth model call.

    The scheduler's persisted attempt numbers prove that attempt 1 occurred
    before the process-boundary restart.  Recovery is restricted to an exact
    human-audited image hash, clean bound attempts 2 and 3, and a stored
    three-call hard-limit result.  It never generalizes from a filename.
    """
    attempts = [int(item.get("ocr_attempt") or 0) for item in calls]
    if len(calls) != 2 or attempts not in ([1, 3], [2, 3]):
        return False
    image_hash = str(current.get("input_image_sha256") or "").strip().lower()
    expected = KNOWN_SOURCE_EXPECTATIONS.get(image_hash)
    reasons = str(meta.get("auto_retry_reasons") or "")
    if (
        not expected
        or expected.get("authority") != "human_audited_pixel_authority"
        or "three_call_hard_limit_reached" not in reasons
    ):
        return False
    for item in calls:
        if str(item.get("input_image_sha256") or "").strip().lower() != image_hash:
            return False
        if item.get("request_id_verified") is not True or item.get("independent_pass") is not True:
            return False
        if item.get("prior_answer_exposed") is True or item.get("prompt_contamination") is True:
            return False
    expected_view = expected["view_type"]
    is_distant = expected_view == "遠景"
    expected_followme = bool(expected.get("followme_physical_expected") is True)
    expected_followme_evidence = [
        dict(item) for item in expected.get("followme_physical_evidence") or []
    ]
    current.update({
        "view_type": expected_view,
        "category": expected_view,
        "complete_screen_count": expected.get("complete_screen_count"),
        "unique_main": not is_distant,
        "model": expected.get("model"),
        "price": expected.get("price"),
        "label_ownership": expected.get("label_ownership", "matched"),
        "followme_physical_evidence": expected_followme_evidence,
        "followme_family_confirmed": expected_followme,
        "wide_scene_followme_present": bool(
            expected.get("wide_scene_followme_present") is True
        ),
        "screen_status": "" if is_distant else "正常",
        "quality_issue": "無",
        "human_pixel_authority_applied": True,
        "human_pixel_authority_sha256": image_hash,
        "three_pass_adjudicated": True,
        "adjudication_rule": "three_call_known_pixel_authority_restart_recovery",
        "restart_recovery_missing_call_trace": True,
        "thinking": (
            "三次模型呼叫已由持久化輪次計數完成；其中一輪在停機邊界前未寫入 trace。"
            + (
                "依人工核對且綁定完整影像雜湊的像素事實，定案為遠景、無型號、無價格，"
                if is_distant
                else (
                    "依人工核對且綁定完整影像雜湊的像素事實，確認實體 FollowMe 主角；"
                    + (
                        f"型號為 {expected.get('model')}，"
                        if expected.get("model")
                        else "型號沒有足夠證據，"
                    )
                    + (
                        f"價格為 {int(expected.get('price')):,} 元，"
                        if expected.get("price")
                        else "價格沒有足夠證據，"
                    )
                    if expected_followme
                    else (
                        f"依人工核對且綁定完整影像雜湊的像素事實定案為 {expected.get('model')}／{expected.get('price')} 元，"
                    )
                )
            )
            + "沒有進行第 4 次呼叫。"
        ),
    })
    current["narration"] = current["thinking"]
    valid, _errors, normalized = validate_evidence_contract(current)
    if not valid:
        return False
    current["normalized_evidence"] = normalized
    return True


def _recover_clean_single_tail_after_restart(
    current: dict[str, Any], calls: list[dict[str, Any]], meta: dict[str, Any]
) -> bool:
    """Finalize two clean tail traces when persisted numbering proves call 3.

    This handles a stop arriving after call 1 consumed its durable budget but
    before its trace append.  It preserves only fields independently repeated
    in both remaining traces and never creates another model call.
    """
    if len(calls) != 2 or [int(item.get("ocr_attempt") or 0) for item in calls] != [2, 3]:
        return False
    if "three_call_hard_limit_reached" not in str(meta.get("auto_retry_reasons") or ""):
        return False
    image_hashes = {
        str(item.get("input_image_sha256") or "").strip().lower() for item in calls
    }
    if "" in image_hashes or len(image_hashes) != 1:
        return False
    for item in calls:
        runtime = item.get("runtime_health") or {}
        if (
            item.get("request_id_verified") is not True
            or item.get("independent_pass") is not True
            or item.get("prior_answer_exposed") is True
            or item.get("prompt_contamination") is True
            or not isinstance(runtime, dict)
            or runtime.get("healthy") is not True
            or str(item.get("view_type") or item.get("category") or "").strip() != "單機"
            or (item.get("normalized_evidence") or item).get("unique_main") is not True
        ):
            return False
    model_keys = [normalize_model_token(item.get("model")) for item in calls]
    model = calls[-1].get("model") if model_keys[0] and model_keys[0] == model_keys[1] else None
    price_keys = [re.sub(r"[^0-9]", "", str(item.get("price") or "")) for item in calls]
    price = calls[-1].get("price") if price_keys[0] and price_keys[0] == price_keys[1] else None
    matched_votes = sum(
        (item.get("normalized_evidence") or item).get("label_ownership") == "matched"
        for item in calls
    )
    if matched_votes < 2:
        model = None
        price = None
    counts = [
        (item.get("normalized_evidence") or item).get("complete_screen_count")
        for item in calls
    ]
    current.update({
        "view_type": "單機",
        "category": "單機",
        "complete_screen_count": min(
            (value for value in counts if isinstance(value, int) and value in {1, 2}),
            default=1,
        ),
        "unique_main": True,
        "model": model,
        "price": price,
        "label_ownership": "matched" if matched_votes >= 2 else "ambiguous",
        "followme_physical_evidence": [],
        "followme_family_confirmed": False,
        "screen_status": "正常",
        "quality_issue": "無",
        "three_pass_adjudicated": True,
        "adjudication_rule": "two_clean_tail_calls_after_persisted_attempt_one",
        "restart_recovery_missing_attempt_one_trace": True,
        "thinking": (
            "模型呼叫總數已由持久化輪次計數到第 3 輪；第 1 輪在停止邊界前未寫入 trace。"
            "現存第 2、3 輪均為同圖、無記憶且確認唯一單機；只保留兩輪共同支持的欄位，"
            "沒有進行第 4 次呼叫。"
        ),
    })
    current["narration"] = current["thinking"]
    valid, _errors, normalized = validate_evidence_contract(current)
    if not valid:
        return False
    current["normalized_evidence"] = normalized
    return True


def finalize_file(
    result_path: Path,
    trace_path: Path,
    output_dir: Path,
    *,
    apply: bool,
    only_file_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    tasks = json.loads(result_path.read_text(encoding="utf-8"))
    if not isinstance(tasks, list):
        raise RuntimeError("Label Studio result must be a JSON list")
    groups = _load_three_call_groups(trace_path)
    report: list[dict[str, Any]] = []
    finalized_rows: list[dict[str, Any]] = []

    for task in tasks:
        if not isinstance(task, dict):
            continue
        name = _task_file_name(task)
        if only_file_names is not None and name not in only_file_names:
            continue
        existing_meta = (task.get("data") or {}).get("ocr_meta") or {}
        calls = groups.get(name) or []
        recovered_official_sku = _recover_full_official_sku_consensus(calls)
        recovered_raw_pair = _raw_single_model_price_consensus(calls)
        raw_calls_bind_same_source = _calls_bind_to_result_source(
            calls, result_path, name
        )
        raw_pair_matches_existing_model = bool(
            recovered_raw_pair
            and normalize_model_token(existing_meta.get("model"))
            in {"", normalize_model_token(recovered_raw_pair[0])}
        )
        raw_model_price_repair = bool(
            apply
            and recovered_raw_pair
            and raw_pair_matches_existing_model
            and existing_meta.get("auto_verified") is True
            and existing_meta.get("auto_review_required") is not True
            and existing_meta.get("evidence_guard_revision") == "20260721.56"
            and len(calls) == 3
            and raw_calls_bind_same_source
        )
        official_sku_repair = bool(
            apply
            and recovered_official_sku
            and existing_meta.get("auto_verified") is True
            and existing_meta.get("auto_review_required") is not True
            and existing_meta.get("evidence_guard_revision") == EVIDENCE_GUARD_REVISION
            and int(existing_meta.get("ocr_attempt") or 0) == 3
            and len(calls) == 3
        )
        completed_current_adjudication = bool(
            apply
            and existing_meta.get("auto_verified") is True
            and existing_meta.get("auto_review_required") is not True
            and existing_meta.get("evidence_guard_revision") == EVIDENCE_GUARD_REVISION
            and existing_meta.get("adjudication_rule")
        )
        stale_terminal_blocker_repair = bool(
            apply
            and existing_meta.get("auto_verified") is True
            and existing_meta.get("auto_review_required") is not True
            and existing_meta.get("evidence_guard_revision") == EVIDENCE_GUARD_REVISION
            and int(existing_meta.get("ocr_attempt") or 0) == 3
            and (
                existing_meta.get("model_validation_failed") is True
                or existing_meta.get("price_conflict_detected") is True
            )
            and len(calls) == 3
            and calls[-1].get("three_pass_adjudicated") is True
            and bool(calls[-1].get("adjudication_rule"))
        )
        completed_current_adjudication = (
            completed_current_adjudication
            or stale_terminal_blocker_repair
            or official_sku_repair
            or raw_model_price_repair
        )
        known_pixel_repair = bool(
            (apply or only_file_names is not None)
            and existing_meta.get("auto_verified") is True
            and existing_meta.get("auto_review_required") is not True
            and calls
            and (
                KNOWN_SOURCE_EXPECTATIONS.get(
                    str(calls[-1].get("input_image_sha256") or "").strip().lower()
                )
                or {}
            ).get("authority") == "human_audited_pixel_authority"
        )
        completed_current_adjudication = completed_current_adjudication or known_pixel_repair
        if not _review_required(task) and not completed_current_adjudication:
            continue
        if len(calls) not in {2, 3}:
            report.append({"file": name, "status": "unchanged", "reason": "bounded_call_evidence_missing"})
            continue
        current = dict(calls[-1])
        if completed_current_adjudication:
            for field in (
                "view_type", "model", "price", "complete_screen_count", "unique_main",
                "label_ownership", "followme_physical_evidence", "followme_family_confirmed",
                "three_pass_adjudicated", "adjudication_rule", "adjudication_summary",
                "price_status", "price_symbol", "official_price", "price_diff_percent",
                "evidence_guard_revision", "evidence_contract_valid", "ocr_attempt",
                "auto_verified", "auto_review_required", "review_status", "auto_retry_reasons",
                "technical_retry_required", "technical_retry_exhausted",
            ):
                if field in existing_meta:
                    current[field] = existing_meta.get(field)
            # A prior guard may have accepted this exact source on pass one
            # even though an earlier clean, request-bound run already consumed
            # all three calls.  The hash-bound visual authority is explicitly
            # tied to that capped run.  Do not let the provisional task's
            # ``ocr_attempt=1`` erase the three-call proof and make the
            # authority silently fall back to the old wrong adjudication.
            if known_pixel_repair:
                current["ocr_attempt"] = 3
            if official_sku_repair:
                current["model"] = recovered_official_sku
                if current.get("price"):
                    current["quality_issue"] = "無"
                current["three_pass_adjudicated"] = True
                current["adjudication_rule"] = (
                    "three_pass_full_official_sku_normalization_repair"
                )
                current["adjudication_summary"] = (
                    "三輪原始結構化答案均讀到同一完整官方料號；"
                    "已將完整料號正規化為型號表短碼，不增加第 4 次模型呼叫。"
                )
            if raw_model_price_repair:
                recovered_model, recovered_price = recovered_raw_pair
                current.update({
                    "view_type": "單機",
                    "category": "單機",
                    "model": recovered_model,
                    "price": recovered_price,
                    "quality_issue": "無",
                    "unique_main": True,
                    "label_ownership": "matched",
                    "three_pass_adjudicated": True,
                    "adjudication_rule": (
                        "three_pass_raw_model_price_consensus_repair"
                    ),
                    "adjudication_summary": (
                        "三輪均為同圖、無記憶且綁定請求；至少兩輪原始結構化答案"
                        "一致讀到同一主角的同一型號與價格，已修復後處理誤清空，"
                        "沒有增加第 4 次模型呼叫。"
                    ),
                })
                refresh_authoritative_price_comparison(
                    current, recovered_model, recovered_price
                )
            current["category"] = current.get("view_type")
            decision = {
                "attempt": 3,
                "retry": False,
                "unresolved": False,
                "verified": True,
                "reasons": [],
            }
        else:
            recovered_restart_authority = _recover_known_authority_after_restart(
                current, calls, existing_meta
            )
            recovered_clean_tail = False
            if not recovered_restart_authority:
                recovered_clean_tail = _recover_clean_single_tail_after_restart(
                    current, calls, existing_meta
                )
        if completed_current_adjudication:
            authority_reapplied = apply_human_audited_pixel_authority(
                current, calls[:-1], 3
            )
            if authority_reapplied:
                current["three_pass_adjudicated"] = True
                current["adjudication_rule"] = "three_call_known_pixel_authority_repair"
                current["adjudication_summary"] = (
                    "三輪獨立判讀已完成；依人工核對且以完整影像雜湊綁定的像素事實修正，"
                    "沒有增加第 4 次模型呼叫。"
                )
            elif known_pixel_repair:
                expected = KNOWN_SOURCE_EXPECTATIONS.get(
                    str(current.get("input_image_sha256") or "").strip().lower()
                ) or {}
                refresh_authoritative_price_comparison(
                    current,
                    expected.get("model"),
                    expected.get("price"),
                )
        elif (
            recovered_restart_authority
            or recovered_clean_tail
            or apply_human_audited_pixel_authority(current, calls[:-1], 3)
        ):
            current["three_pass_adjudicated"] = True
            current["adjudication_summary"] = (
                "三輪獨立判讀已完成；依人工核對且以完整影像雜湊綁定的像素事實定案，"
                "沒有增加第 4 次模型呼叫。"
            )
            decision = {
                "attempt": 3,
                "retry": False,
                "unresolved": False,
                "verified": True,
                "reasons": [],
            }
        else:
            decision = finalize_three_pass_outcome(
                current,
                calls[:-1],
                {
                    "attempt": 3,
                    "retry": False,
                    "unresolved": True,
                    "verified": False,
                    "reasons": ["bounded_existing_review_adjudication"],
                },
            )
        if decision.get("verified") is not True:
            report.append({
                "file": name,
                "status": "unchanged",
                "reason": decision.get("technical_retry_reason") or "bounded_consensus_missing",
            })
            continue

        contained_reasons = list(
            ((current.get("runtime_health") or {}).get("reasons") or [])
            if isinstance(current.get("runtime_health"), dict)
            else []
        )
        if contained_reasons:
            current["runtime_health_contained_reasons"] = contained_reasons
        current["runtime_health"] = {
            "healthy": True,
            "allow_processing": True,
            "allow_upload": True,
            "reasons": [],
            "display_narration": str(current.get("thinking") or current.get("narration") or ""),
            "resolved_by_bounded_three_pass_adjudication": True,
        }

        current.update({
            "auto_verified": True,
            "auto_review_required": False,
            "review_status": "已完成",
            "auto_retry_reasons": "",
            "technical_retry_required": False,
            "technical_retry_exhausted": False,
            "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
            "evidence_contract_valid": True,
            "ocr_attempt": 3,
        })
        clear_superseded_terminal_content_flags(current)
        if not current.get("model") or not current.get("price"):
            current.update({
                "price_status": "not_compared",
                "price_symbol": "",
                "official_price": "",
                "price_diff_percent": None,
            })
        meta = (task.setdefault("data", {}).setdefault("ocr_meta", {}))
        for field in (
            "view_type", "model", "price", "complete_screen_count", "unique_main",
            "label_ownership", "followme_physical_evidence", "followme_family_confirmed",
            "three_pass_adjudicated", "adjudication_rule", "adjudication_summary",
            "price_status", "price_symbol", "official_price", "price_diff_percent",
            "evidence_guard_revision", "evidence_contract_valid", "ocr_attempt",
            "auto_verified", "auto_review_required", "review_status", "auto_retry_reasons",
            "technical_retry_required", "technical_retry_exhausted",
            "model_validation_failed", "rejected_model", "price_conflict_detected",
            "requires_structured_retry", "structured_authority_blocked_fields",
            "unlisted_model_candidate", "official_model_unverified",
            "unlisted_model_photo_consensus",
        ):
            meta[field] = current.get(field)
        _sync_label_studio_annotation(task, current)
        finalized_rows.append(current)
        report.append({
            "file": name,
            "status": "finalized" if apply else "would_finalize",
            "rule": current.get("adjudication_rule"),
            "view_type": current.get("view_type"),
            "model": current.get("model"),
            "price": current.get("price"),
        })

    if apply and finalized_rows:
        for row in finalized_rows:
            try:
                queued = enqueue_finalized_result(row, output_dir=output_dir)
            except RuntimeError as exc:
                raise RuntimeError(
                    f"finalized row queue failed for {row.get('file_name')}: "
                    f"view={row.get('view_type')} category={row.get('category')}; {exc}"
                ) from exc
            if queued is None:
                raise RuntimeError(f"finalized row was not queued: {row.get('file_name')}")
        # Queue first, then expose the durable completed result. Enqueue is
        # idempotent, so a write failure can be retried without a half-complete
        # verified row that never entered the upload stream.
        _atomic_json(result_path, tasks)
        _append_final_presentation_events(output_dir, finalized_rows)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-file", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--authority-manifest",
        type=Path,
        help="Exact source/hash-bound visual authority manifest for this run.",
    )
    parser.add_argument(
        "--file-name",
        action="append",
        default=[],
        help="Only finalize this exact source filename; may be repeated.",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.authority_manifest:
        load_authority_manifest(args.authority_manifest)
    report = finalize_file(
        args.result_file.resolve(),
        args.trace.resolve(),
        args.output_dir.resolve(),
        apply=args.apply,
        only_file_names=set(args.file_name) if args.file_name else None,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

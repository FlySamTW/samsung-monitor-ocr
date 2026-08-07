"""Close a missing result after the three-call budget is already consumed.

This recovery performs no model inference. It preserves the human pixel-authority
path for two durable request-bound outputs and also permits exactly three
source/hash/request-bound raw structured outputs to close one ordinary matched
single unit when all three independently agree on its exact model and price.
No call four occurs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.audit_fields import (
    EVIDENCE_CONTRACT_VERSION,
    EVIDENCE_GUARD_REVISION,
    KNOWN_SOURCE_AUDIT_AUTHORITIES,
    KNOWN_SOURCE_EXPECTATIONS,
    clear_superseded_terminal_content_flags,
    refresh_authoritative_price_comparison,
    validate_evidence_contract,
)
from skills.model_catalog_rules import extract_samsung_models
from tools.recover_request_binding_fuse import _atomic_json, _result_task
from tools.stream_drive_upload import enqueue_finalized_result


RECOVERY_RULE = "two_clean_outputs_plus_consumed_cap_visual_authority"
CONTAINED_RECOVERY_RULE = (
    "one_clean_plus_one_contained_output_plus_consumed_cap_visual_authority"
)
RAW_CONSENSUS_RECOVERY_RULE = "three_bound_raw_structured_single_consensus"
RAW_DISTANT_CONSENSUS_RECOVERY_RULE = (
    "three_bound_raw_structured_distant_consensus"
)
CROSS_RUN_RAW_CONSENSUS_RECOVERY_RULE = (
    "three_bound_cross_run_raw_structured_single_consensus"
)
CROSS_RUN_RAW_DISTANT_CONSENSUS_RECOVERY_RULE = (
    "three_bound_cross_run_raw_structured_distant_consensus"
)
ALLOWED_CONTAINED_RUNTIME_REASONS = frozenset(
    {
        "structured_narration_followme_conflict",
        "ui_narration_contains_raw_structure",
    }
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _base_bound_call(row: dict[str, Any], image_hash: str) -> bool:
    return bool(
        str(row.get("input_image_sha256") or "").strip().lower() == image_hash
        and row.get("request_id_verified") is True
        and row.get("request_binding_enforced") is True
        and row.get("independent_pass") is True
        and row.get("prior_answer_exposed") is not True
        and row.get("prompt_contamination") is not True
    )


def _clean_bound_call(row: dict[str, Any], image_hash: str) -> bool:
    runtime = row.get("runtime_health") or {}
    return bool(
        _base_bound_call(row, image_hash)
        and isinstance(runtime, dict)
        and runtime.get("healthy") is True
        and not (runtime.get("reasons") or [])
    )


def _contained_bound_call(
    row: dict[str, Any],
    image_hash: str,
    authority: dict[str, Any] | None = None,
) -> bool:
    runtime = row.get("runtime_health") or {}
    reasons = {
        str(reason).strip()
        for reason in (runtime.get("reasons") or [])
        if str(reason).strip()
    }
    authority_allows_conservative_empty_model = bool(
        reasons == {"structured_authority_material_conflict:model"}
        and isinstance(authority, dict)
        and authority.get("authority") == "human_audited_pixel_authority"
        and authority.get("model") is None
        and row.get("model") in (None, "")
    )
    return bool(
        _base_bound_call(row, image_hash)
        and isinstance(runtime, dict)
        and runtime.get("healthy") is False
        and reasons
        and (
            reasons <= ALLOWED_CONTAINED_RUNTIME_REASONS
            or authority_allows_conservative_empty_model
        )
    )


def _classify_bound_calls(
    rows: list[dict[str, Any]],
    image_hash: str,
    authority: dict[str, Any] | None = None,
) -> tuple[int, int]:
    clean_count = sum(_clean_bound_call(row, image_hash) for row in rows)
    contained_count = sum(
        _contained_bound_call(row, image_hash, authority) for row in rows
    )
    if clean_count + contained_count != len(rows):
        raise RuntimeError(
            "outputs include contamination, identity failure, or an unapproved runtime failure"
        )
    if clean_count < 1 or contained_count > 1:
        raise RuntimeError("recovery requires at least one clean and at most one contained output")
    return clean_count, contained_count


def _raw_structured_single_consensus(
    rows: list[dict[str, Any]],
    *,
    allow_count_variation: bool = False,
) -> dict[str, Any]:
    if len(rows) < 3:
        raise RuntimeError("raw consensus recovery requires at least three trace calls")
    structured: list[dict[str, Any]] = []
    request_ids: set[str] = set()
    for row in rows:
        raw_objects = row.get("_trace_raw_objects")
        if not isinstance(raw_objects, list) or len(raw_objects) != 1:
            raise RuntimeError("each trace call must contain exactly one raw structured output")
        try:
            raw = json.loads(str(raw_objects[0]))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("raw structured output is not valid JSON") from exc
        if not isinstance(raw, dict):
            raise RuntimeError("raw structured output is not an object")
        request_id = str(raw.get("request_id") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{32}", request_id) or request_id in request_ids:
            raise RuntimeError("raw structured request IDs must be unique bound tokens")
        request_ids.add(request_id)
        count = raw.get("complete_screen_count")
        count_is_allowed = bool(
            isinstance(count, int)
            and not isinstance(count, bool)
            and (
                count >= 1
                if allow_count_variation
                else count == 1
            )
        )
        if (
            str(raw.get("view_type") or "").strip() != "單機"
            or not count_is_allowed
            or raw.get("unique_main") is not True
            or str(raw.get("label_ownership") or "").strip() != "matched"
            or list(raw.get("followme_physical_evidence") or [])
            or str(raw.get("screen_status") or "").strip() not in {"", "正常"}
            or str(raw.get("quality_issue") or "").strip() not in {"", "無"}
        ):
            raise RuntimeError("raw structured outputs are not ordinary matched single units")
        price = "".join(ch for ch in str(raw.get("price") or "") if ch.isdigit())
        # The structured model field is the owned-subject answer.  Narration
        # may legitimately mention a neighboring display's side strip, so use
        # narration only when the structured model itself cannot yield one
        # exact Samsung SKU.
        models = extract_samsung_models(str(raw.get("model") or ""))
        if len(models) != 1:
            models = extract_samsung_models(
                f"{raw.get('model') or ''} {raw.get('narration') or ''}"
            )
        if len(models) != 1 or not price:
            raise RuntimeError("raw structured output lacks one exact Samsung model and price")
        structured.append(
            {
                "model": models[0],
                "price": price,
                "request_id": request_id,
            }
        )
    if len({(item["model"], item["price"]) for item in structured}) != 1:
        raise RuntimeError("raw structured outputs disagree on model or price")
    return {
        "view_type": "單機",
        "complete_screen_count": 1,
        "model": structured[0]["model"],
        "price": structured[0]["price"],
        "label_ownership": "matched",
        "followme_physical_expected": False,
    }


def _raw_structured_distant_consensus(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Close one exhausted wide scene from three clean raw observations.

    Complete-screen counts may vary because an edge monitor can be borderline,
    but every material field must independently agree that this is a distant
    scene with no owned SKU, price, or same-subject FollowMe evidence.
    """
    if len(rows) < 3:
        raise RuntimeError(
            "raw distant consensus recovery requires at least three trace calls"
        )

    counts: list[int] = []
    ownership: set[str] = set()
    request_ids: set[str] = set()
    for row in rows:
        raw_objects = row.get("_trace_raw_objects")
        if not isinstance(raw_objects, list) or len(raw_objects) != 1:
            raise RuntimeError(
                "each trace call must contain exactly one raw structured output"
            )
        try:
            raw = json.loads(str(raw_objects[0]))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("raw structured output is not valid JSON") from exc
        if not isinstance(raw, dict):
            raise RuntimeError("raw structured output is not an object")

        request_id = str(raw.get("request_id") or "").strip().lower()
        if (
            not re.fullmatch(r"[0-9a-f]{32}", request_id)
            or request_id in request_ids
        ):
            raise RuntimeError(
                "raw structured request IDs must be unique bound tokens"
            )
        request_ids.add(request_id)

        count = raw.get("complete_screen_count")
        owner = str(raw.get("label_ownership") or "").strip()
        if (
            str(raw.get("view_type") or "").strip() != "遠景"
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 3
            or raw.get("unique_main") is not False
            or owner not in {"not_visible", "not_applicable"}
            or raw.get("model") not in (None, "")
            or raw.get("price") not in (None, "")
            or raw.get("wide_scene_followme_present") is True
            or raw.get("followme_family_confirmed") is True
        ):
            raise RuntimeError(
                "raw structured outputs do not agree on one unresolved distant scene"
            )

        for evidence in raw.get("followme_physical_evidence") or []:
            if not isinstance(evidence, dict):
                raise RuntimeError("raw FollowMe evidence item is not an object")
            if (
                evidence.get("same_subject") is True
                and str(evidence.get("strength") or "").strip()
                in {"strong", "direct"}
            ):
                raise RuntimeError(
                    "raw distant output contains same-subject strong FollowMe evidence"
                )

        counts.append(count)
        ownership.add(owner)

    if len(ownership) != 1:
        raise RuntimeError("raw distant outputs disagree on label ownership")
    return {
        "view_type": "遠景",
        "complete_screen_count": min(counts),
        "model": None,
        "price": None,
        "label_ownership": next(iter(ownership)),
        "followme_physical_expected": False,
        "followme_physical_evidence": [],
        "wide_scene_followme_present": False,
    }


def _validate_three_trace_bindings(
    rows: list[dict[str, Any]],
    *,
    staged_path: Path,
    original_source: Path,
    allow_cross_run: bool = False,
) -> str:
    attempts = sorted(int(row.get("ocr_attempt") or 0) for row in rows)
    run_ids = {str(row.get("run_id") or "") for row in rows}
    if allow_cross_run:
        if len(rows) != 3 or len(run_ids) != 3 or "" in run_ids:
            raise RuntimeError(
                "cross-run recovery requires three calls from three distinct runs"
            )
    elif len(rows) != 3 or attempts != [1, 2, 3] or len(run_ids) != 1:
        raise RuntimeError(
            "trace does not contain one exact run at attempts one, two, and three"
        )
    image_hashes = {
        str(row.get("input_image_sha256") or "").strip().lower() for row in rows
    }
    if len(image_hashes) != 1:
        raise RuntimeError("trace calls do not share one model-input image hash")
    image_hash = next(iter(image_hashes))
    if not re.fullmatch(r"[0-9a-f]{64}", image_hash):
        raise RuntimeError("trace calls have no valid model-input image hash")
    if not all(_clean_bound_call(row, image_hash) for row in rows):
        raise RuntimeError(
            "trace calls are not clean request-bound stateless same-image calls"
        )

    staged_path = staged_path.resolve()
    original_source = original_source.resolve()
    if not staged_path.is_file() or not original_source.is_file():
        raise RuntimeError("current staged or original source file is missing")
    source_hash = _sha256_file(original_source)
    if _sha256_file(staged_path) != source_hash:
        raise RuntimeError("staged and original source bytes do not match")

    for row in rows:
        trace_original_text = str(
            row.get("_trace_original_source_path") or ""
        ).strip()
        trace_source_text = str(row.get("_trace_source_path") or "").strip()
        if (
            not trace_original_text
            or Path(trace_original_text).resolve() != original_source
        ):
            raise RuntimeError(
                "trace original source path does not match the current source map"
            )
        if not trace_source_text:
            raise RuntimeError("trace source path is missing")
        historical_source = Path(trace_source_text).resolve()
        if (
            not historical_source.is_file()
            or historical_source.name != staged_path.name
        ):
            raise RuntimeError("historical trace source file is missing")
        if _sha256_file(historical_source) != source_hash:
            raise RuntimeError(
                "historical trace source bytes do not match current source bytes"
            )
    return image_hash


def _load_trace_calls(
    trace_path: Path,
    *,
    source_item_id: str,
    file_name: str,
    attempts: tuple[int, ...] = (1, 2),
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    with trace_path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if source_item_id not in line or file_name not in line:
                continue
            payload = json.loads(line)
            if (
                str(payload.get("source_item_id") or payload.get("source_identity") or "")
                != source_item_id
                or str(payload.get("file_name") or "") != file_name
            ):
                continue
            row = dict(payload.get("parsed_output") or {})
            row.update(
                {
                    "file_name": file_name,
                    "source_item_id": source_item_id,
                    "run_id": str(payload.get("run_id") or row.get("run_id") or ""),
                    "ocr_attempt": int(payload.get("attempt") or row.get("ocr_attempt") or 0),
                    "timestamp": str(payload.get("timestamp") or row.get("timestamp") or ""),
                    "_trace_source_path": str(payload.get("source_path") or ""),
                    "_trace_original_source_path": str(
                        payload.get("original_source_path") or ""
                    ),
                    "_trace_raw_objects": list(payload.get("raw_objects") or []),
                }
            )
            grouped.setdefault(row["run_id"], []).append(row)

    candidates: list[list[dict[str, Any]]] = []
    for rows in grouped.values():
        ordered = sorted(rows, key=lambda item: int(item.get("ocr_attempt") or 0))
        if [int(item.get("ocr_attempt") or 0) for item in ordered] == list(attempts):
            candidates.append(ordered)
    if not candidates:
        expected = ", ".join(str(item) for item in attempts)
        raise RuntimeError(f"trace lacks one exact run at attempts {expected}")
    candidates.sort(key=lambda rows: str(rows[-1].get("timestamp") or ""))
    return candidates[-1]


def _load_cross_run_trace_calls(
    trace_path: Path,
    *,
    source_item_id: str,
    file_name: str,
) -> list[dict[str, Any]]:
    """Select three latest clean same-input observations across restarts.

    Older batches sometimes restarted the same photo as visible pass one, so a
    lifetime three-call cap can be real even though no single run contains
    attempts 1/2/3.  Cross-run recovery is allowed only when *every* clean,
    fully bound observation for one model-input hash agrees materially.  This
    prevents cherry-picking three convenient answers from a contradictory
    history.
    """

    rows: list[dict[str, Any]] = []
    with trace_path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if source_item_id not in line or file_name not in line:
                continue
            payload = json.loads(line)
            if (
                str(payload.get("source_item_id") or payload.get("source_identity") or "")
                != source_item_id
                or str(payload.get("file_name") or "") != file_name
            ):
                continue
            row = dict(payload.get("parsed_output") or {})
            row.update(
                {
                    "file_name": file_name,
                    "source_item_id": source_item_id,
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
            image_hash = str(row.get("input_image_sha256") or "").strip().lower()
            if re.fullmatch(r"[0-9a-f]{64}", image_hash) and _clean_bound_call(
                row, image_hash
            ):
                rows.append(row)

    by_hash: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_hash.setdefault(
            str(row.get("input_image_sha256") or "").strip().lower(), []
        ).append(row)

    candidates: list[list[dict[str, Any]]] = []
    for same_hash_rows in by_hash.values():
        deduped: dict[str, dict[str, Any]] = {}
        for row in sorted(
            same_hash_rows, key=lambda item: str(item.get("timestamp") or "")
        ):
            raw_objects = row.get("_trace_raw_objects")
            if not isinstance(raw_objects, list) or len(raw_objects) != 1:
                continue
            try:
                raw = json.loads(str(raw_objects[0]))
            except (TypeError, ValueError):
                continue
            request_id = str(raw.get("request_id") or "").strip().lower()
            if not re.fullmatch(r"[0-9a-f]{32}", request_id):
                continue
            deduped[request_id] = row
        bound = sorted(
            deduped.values(), key=lambda item: str(item.get("timestamp") or "")
        )
        if len(bound) < 3 or len({str(item.get("run_id") or "") for item in bound}) < 3:
            continue
        # Validate the whole same-input history before selecting the latest
        # three calls.  Any material disagreement makes this hash ineligible.
        single_with_legacy_count_variation = False
        try:
            _raw_structured_single_consensus(bound)
        except RuntimeError:
            try:
                # Older geometry prompts occasionally counted partial edge
                # neighbors as complete while still agreeing on the exact
                # owned single/model/price.  Permit that historical count
                # variation only when the latest three calls independently
                # satisfy the current strict count-one contract below.
                _raw_structured_single_consensus(
                    bound,
                    allow_count_variation=True,
                )
                single_with_legacy_count_variation = True
            except RuntimeError:
                try:
                    _raw_structured_distant_consensus(bound)
                except RuntimeError:
                    continue
        selected: list[dict[str, Any]] = []
        seen_runs: set[str] = set()
        for row in reversed(bound):
            run_id = str(row.get("run_id") or "")
            if not run_id or run_id in seen_runs:
                continue
            selected.append(row)
            seen_runs.add(run_id)
            if len(selected) == 3:
                break
        if len(selected) == 3:
            selected = list(reversed(selected))
            if single_with_legacy_count_variation:
                try:
                    _raw_structured_single_consensus(selected)
                except RuntimeError:
                    continue
            candidates.append(selected)

    if not candidates:
        raise RuntimeError(
            "trace lacks three clean same-input consensus calls across distinct runs"
        )
    candidates.sort(key=lambda rows: str(rows[-1].get("timestamp") or ""))
    return candidates[-1]


def _apply_authority(
    current: dict[str, Any],
    authority: dict[str, Any],
    *,
    image_hash: str,
    clean_count: int,
    contained_count: int,
) -> None:
    recovery_rule = CONTAINED_RECOVERY_RULE if contained_count else RECOVERY_RULE
    view = str(authority["view_type"])
    current.update(
        {
            "view_type": view,
            "category": view,
            "complete_screen_count": authority.get("complete_screen_count"),
            "unique_main": view == "單機",
            "model": authority.get("model"),
            "price": authority.get("price"),
            "label_ownership": authority.get("label_ownership", "matched"),
            "followme_family_confirmed": bool(
                authority.get("followme_physical_expected") is True
            ),
            "screen_status": "" if view == "遠景" else "正常",
            "human_pixel_authority_applied": True,
            "human_pixel_authority_sha256": image_hash,
            "three_pass_adjudicated": True,
            "adjudication_rule": recovery_rule,
            "hard_cap_consumed_attempts": 3,
            "model_outputs_available": clean_count,
            "model_outputs_observed": clean_count + contained_count,
            "contained_failed_outputs": contained_count,
            "third_output_missing_at_process_boundary": True,
        }
    )
    if "followme_physical_evidence" in authority:
        current["followme_physical_evidence"] = [
            dict(item) for item in authority.get("followme_physical_evidence") or []
        ]
    elif authority.get("followme_physical_expected") is False:
        current["followme_physical_evidence"] = []
    for field in (
        "unlisted_model_candidate",
        "official_model_unverified",
        "unlisted_model_photo_consensus",
    ):
        if field in authority:
            current[field] = bool(authority.get(field))
    clear_superseded_terminal_content_flags(current)

    if view == "遠景" or (current.get("model") and current.get("price")):
        current["quality_issue"] = "無"
    elif not current.get("model") and not current.get("price"):
        current["quality_issue"] = "不合格-沒有規格和價格牌"
    elif not current.get("model"):
        current["quality_issue"] = "不合格-沒有規格牌"
    else:
        current["quality_issue"] = "不合格-沒有價格牌"

    refresh_authoritative_price_comparison(
        current,
        authority.get("model"),
        authority.get("price"),
    )
    if view == "遠景":
        narration = (
            "我看到完整原圖中至少三台螢幕完整入鏡，沒有可唯一歸屬同一主體的"
            "型號與價格，因此定案為遠景、無型號、無價格。第三個模型呼叫名額已"
            "在程序邊界消耗但沒有留下可用輸出；本結論由兩份乾淨綁圖證據與像素"
            "稽核完成，沒有進行第四次呼叫。"
        )
    else:
        narration = (
            "我看到完整原圖的唯一主體與標籤歸屬已由兩份乾淨綁圖證據及像素稽核"
            "確認；第三個模型呼叫名額已在程序邊界消耗但沒有留下可用輸出，沒有"
            "進行第四次呼叫。"
        )
    current["thinking"] = narration
    current["narration"] = narration
    if contained_count:
        narration = (
            "本張已用滿三次模型呼叫額度；其中一份輸出通過完整守門，"
            "一份同照片輸出因結構與敘述衝突被隔離，第三份輸出於程序邊界遺失。"
            "未進行第四輪；最終結果依原圖雜湊綁定的人工像素權威結案。"
        )
        current["thinking"] = narration
        current["narration"] = narration


def recover(
    *,
    staging_dir: Path,
    trace_path: Path,
    result_file: Path,
    upload_output_dir: Path,
    file_name: str,
    apply: bool,
) -> dict[str, Any]:
    staging_dir = staging_dir.resolve()
    trace_path = trace_path.resolve()
    result_file = result_file.resolve()
    retry_path = staging_dir / ".ocr_retry_queue.json"
    source_map_path = staging_dir / ".ocr_source_map.json"
    staged_path = (staging_dir / file_name).resolve()
    if not staged_path.is_file():
        raise FileNotFoundError(staged_path)

    source_map = _read_json(source_map_path)
    source_info = (source_map.get("items") or {}).get(file_name) or {}
    source_item_id = str(source_info.get("source_item_id") or "")
    original_source = Path(str(source_info.get("original_source_path") or "")).resolve()
    period = str(source_info.get("period") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", source_item_id):
        raise RuntimeError("source map has no stable source_item_id")
    if not original_source.is_file() or not re.fullmatch(r"20\d{4}", period):
        raise RuntimeError("source map has no valid original file/period")

    retry_state = _read_json(retry_path)
    if Path(str(retry_state.get("image_dir") or "")).resolve() != staging_dir:
        raise RuntimeError("retry state belongs to a different staging directory")
    attempts = retry_state.get("auto_attempts") or {}
    histories = retry_state.get("auto_result_history") or {}
    authority = KNOWN_SOURCE_AUDIT_AUTHORITIES.get(source_item_id)
    uses_human_authority = bool(
        authority and authority.get("authority") == "human_audited_pixel_authority"
    )
    if uses_human_authority:
        image_hash = str(authority.get("input_image_sha256") or "").strip().lower()
        if (
            KNOWN_SOURCE_EXPECTATIONS.get(image_hash) is not authority
            or str(authority.get("source_file_sha256") or "")
            != _sha256_file(original_source)
        ):
            raise RuntimeError("source bytes or authority registry identity do not match")
        if int(attempts.get(file_name) or 0) != 3:
            raise RuntimeError("retry state does not prove exactly three consumed calls")
        durable_history = list(histories.get(file_name) or [])
        if len(durable_history) != 2:
            raise RuntimeError("retry state does not contain exactly two bound outputs")
        durable_clean_count, durable_contained_count = _classify_bound_calls(
            [dict(item) for item in durable_history], image_hash, authority
        )
        calls = _load_trace_calls(
            trace_path,
            source_item_id=source_item_id,
            file_name=file_name,
        )
        trace_clean_count, trace_contained_count = _classify_bound_calls(
            calls, image_hash, authority
        )
        if (trace_clean_count, trace_contained_count) != (
            durable_clean_count,
            durable_contained_count,
        ):
            raise RuntimeError("trace and durable history output classifications disagree")
        recovery_authority = authority
        available_count = durable_clean_count
        observed_count = durable_clean_count + durable_contained_count
        contained_count = durable_contained_count
        recovery_rule = (
            CONTAINED_RECOVERY_RULE if durable_contained_count else RECOVERY_RULE
        )
    else:
        cross_run_recovery = False
        try:
            calls = _load_trace_calls(
                trace_path,
                source_item_id=source_item_id,
                file_name=file_name,
                attempts=(1, 2, 3),
            )
            image_hash = _validate_three_trace_bindings(
                calls,
                staged_path=staged_path,
                original_source=original_source,
            )
            try:
                recovery_authority = _raw_structured_single_consensus(calls)
                recovery_rule = RAW_CONSENSUS_RECOVERY_RULE
            except RuntimeError:
                recovery_authority = _raw_structured_distant_consensus(calls)
                recovery_rule = RAW_DISTANT_CONSENSUS_RECOVERY_RULE
        except RuntimeError:
            calls = _load_cross_run_trace_calls(
                trace_path,
                source_item_id=source_item_id,
                file_name=file_name,
            )
            image_hash = _validate_three_trace_bindings(
                calls,
                staged_path=staged_path,
                original_source=original_source,
                allow_cross_run=True,
            )
            cross_run_recovery = True
            try:
                recovery_authority = _raw_structured_single_consensus(calls)
                recovery_rule = CROSS_RUN_RAW_CONSENSUS_RECOVERY_RULE
            except RuntimeError:
                recovery_authority = _raw_structured_distant_consensus(calls)
                recovery_rule = CROSS_RUN_RAW_DISTANT_CONSENSUS_RECOVERY_RULE
        recorded_attempts = attempts.get(file_name)
        if recorded_attempts is not None and int(recorded_attempts) != 3:
            raise RuntimeError("retry state contradicts the three consumed trace calls")
        available_count = 3
        observed_count = 3
        contained_count = 0
    if uses_human_authority and len(
        {str(item.get("run_id") or "") for item in calls}
    ) != 1:
        raise RuntimeError("trace calls do not belong to one run")

    for existing_path in staging_dir.glob("*-OCR成功.json"):
        existing_tasks = _read_json(existing_path)
        if any(
            Path(str((task.get("data") or {}).get("image") or "")).name == file_name
            for task in existing_tasks
            if isinstance(task, dict)
        ):
            raise RuntimeError(f"photo already exists in result file: {existing_path.name}")

    current = dict(calls[-1])
    current.pop("_trace_source_path", None)
    current.pop("_trace_original_source_path", None)
    current.pop("_trace_raw_objects", None)
    current.update(
        {
            "file_name": file_name,
            "source_path": str(staged_path),
            "original_source_path": str(original_source),
            "source_item_id": source_item_id,
            "period": period,
            "source_file_sha256": _sha256_file(original_source),
            "input_image_sha256": image_hash,
            "ocr_attempt": 3,
            "timestamp": datetime.now().isoformat(),
            "request_binding_enforced": True,
            "request_id_verified": True,
            "independent_pass": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
        }
    )
    _apply_authority(
        current,
        recovery_authority,
        image_hash=image_hash,
        clean_count=available_count,
        contained_count=contained_count,
    )
    if not uses_human_authority:
        current.update(
            {
                "human_pixel_authority_applied": False,
                "human_pixel_authority_sha256": "",
                "adjudication_rule": recovery_rule,
                "model_outputs_available": 3,
                "model_outputs_observed": 3,
                "contained_failed_outputs": 0,
                "third_output_missing_at_process_boundary": False,
                "zero_model_recovery": True,
            }
        )
        if current.get("view_type") == "遠景":
            narration = (
                "我看到本輪結論：遠景，無型號，無價格。三份獨立、請求綁定、"
                "同圖雜湊且來源位元組一致的原始結構化輸出都確認至少三台完整"
                "螢幕，沒有唯一主體或同主體 FollowMe 強證據；本次只做零模型"
                "確定性結案，沒有進行第四次呼叫。"
            )
        else:
            narration = (
                "我看到本輪結論：單機，型號 "
                f"{current['model']}，價格 {int(current['price']):,}。三份獨立、"
                "請求綁定且同圖雜湊的原始結構化輸出一致確認唯一主體、標籤"
                "歸屬、型號與價格；本次只做零模型確定性結案，沒有進行第四次"
                "呼叫。"
            )
        current["thinking"] = narration
        current["narration"] = narration
    current["runtime_health"] = {
        "healthy": True,
        "allow_processing": True,
        "allow_upload": True,
        "reasons": [],
        "display_narration": current["narration"],
        "resolved_by_consumed_cap_visual_authority": True,
    }
    valid, errors, normalized = validate_evidence_contract(current)
    if not valid:
        raise RuntimeError("recovered result failed evidence contract: " + ";".join(errors))
    current.update(
        {
            "normalized_evidence": normalized,
            "evidence_contract_version": EVIDENCE_CONTRACT_VERSION,
            "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
            "evidence_contract_valid": True,
            "auto_verified": True,
            "auto_review_required": False,
            "review_status": "已完成判讀",
            "technical_retry_required": False,
            "technical_retry_exhausted": False,
            "auto_retry_reasons": "",
            "adjudication_summary": (
                "模型呼叫預算已用滿三次；三份同來源、同圖雜湊、請求綁定的"
                "原始結構化輸出一致，零模型確定性結案，沒有進行第四次呼叫。"
                if not uses_human_authority
                else "模型呼叫預算已用滿三次；兩份乾淨綁圖輸出加上完整像素"
                "稽核定案，第三份輸出缺失被明確記錄，沒有進行第四次呼叫。"
            ),
        }
    )

    report = {
        "status": "would_recover" if not apply else "recovered",
        "file_name": file_name,
        "source_item_id": source_item_id,
        "period": period,
        "view_type": current.get("view_type"),
        "model": current.get("model"),
        "price": current.get("price"),
        "complete_screen_count": current.get("complete_screen_count"),
        "model_calls_consumed": 3,
        "model_outputs_available": available_count,
        "model_outputs_observed": observed_count,
        "contained_failed_outputs": contained_count,
        "fourth_call_made": False,
        "adjudication_rule": recovery_rule,
    }
    if not apply:
        return report

    tasks = _read_json(result_file) if result_file.is_file() else []
    if not isinstance(tasks, list):
        raise RuntimeError("result file is not a Label Studio task list")
    queued = enqueue_finalized_result(current, output_dir=upload_output_dir)
    if queued is None:
        raise RuntimeError("recovered result did not pass the upload queue gate")
    current["stream_upload_queued"] = True
    task_id = max((int(task.get("id") or 0) for task in tasks), default=0) + 1
    tasks.append(_result_task(current, task_id))
    _atomic_json(result_file, tasks)

    attempts.pop(file_name, None)
    histories.pop(file_name, None)
    retry_state["auto_attempts"] = attempts
    retry_state["auto_result_history"] = histories
    retry_state["retry_queue"] = [
        item for item in retry_state.get("retry_queue") or [] if item != file_name
    ]
    retry_state["priority_queue"] = [
        item for item in retry_state.get("priority_queue") or [] if item != file_name
    ]
    incident_sources = retry_state.get("runtime_health_incident_sources") or {}
    retry_state["runtime_health_incident_sources"] = {
        str(reason): [
            item for item in (sources or []) if str(item) != file_name
        ]
        for reason, sources in incident_sources.items()
        if any(str(item) != file_name for item in (sources or []))
    }
    retry_state["request_binding_incident_events"] = [
        item
        for item in (retry_state.get("request_binding_incident_events") or [])
        if str(item.get("source_id") or "") != source_item_id
    ]
    retry_state["updated_at"] = datetime.now().isoformat()
    _atomic_json(retry_path, retry_state)

    receipt = {
        **report,
        "status": "recovered",
        "queued_job": str(queued),
        "result_file": str(result_file),
        "recovered_at": datetime.now().isoformat(),
    }
    receipt_path = (
        upload_output_dir.resolve()
        / "_ocr_audit"
        / "consumed_cap_missing_result_recovery"
        / f"{datetime.now():%Y%m%d_%H%M%S}_{source_item_id[:12]}.json"
    )
    _atomic_json(receipt_path, receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--trace-path", type=Path, required=True)
    parser.add_argument("--result-file", type=Path, required=True)
    parser.add_argument("--upload-output-dir", type=Path, required=True)
    parser.add_argument("--file-name", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = recover(
        staging_dir=args.staging_dir,
        trace_path=args.trace_path,
        result_file=args.result_file,
        upload_output_dir=args.upload_output_dir,
        file_name=args.file_name,
        apply=args.apply,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

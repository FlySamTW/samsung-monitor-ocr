"""Small fail-closed runtime health gate for OCR presentation and handoffs.

This module deliberately does not start work, persist results, or upload files.
Callers receive one bounded decision that can only authorize an upload when a
separate upstream upload authority was already proven.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from skills.audit_fields import (
    KNOWN_SOURCE_EXPECTATIONS,
    has_sufficient_followme_physical_evidence,
    is_followme_model,
    material_structured_authority_fields,
    narration_evidence_consistency_reasons,
    known_source_expectation_conflict,
)


BLOCKED_NARRATION = "AI 判讀文字已由健康閘收回；這張照片必須重新獨立判讀。"

RUNTIME_HEALTH_FUSE_FILENAME = "runtime_health_fuse.json"
RUNTIME_HEALTH_FUSE_SCHEMA = "samsung-ocr-runtime-health-fuse/v1"
_FIRST_PASS_CONTAINABLE_CONTENT_REASONS = {
    "distant_followme_strong_evidence_conflict",
    "structured_authority_material_conflict:view_type",
    "structured_narration_followme_conflict",
    "known_source_expectation_conflict",
}

_RAW_FIELD_PATTERN = re.compile(
    r"(?:[\"']?(?:view_type|category|model|price|quality_issue|screen_status|"
    r"complete_screen_count|unique_main|label_ownership|followme_physical_evidence|"
    r"guard_decision|raw_model_output)[\"']?\s*[:=])",
    re.IGNORECASE,
)
_CORRECTION_CONTEXT_PATTERN = re.compile(
    r"(?:上一輪|前一輪|前輪|先前|前次|上次|剛才|第一次答案|"
    r"原答案|原判斷|待推翻|對話歷程|previous\s+(?:answer|result)|"
    r"prior\s+(?:answer|result)|(?:修正|更正|改正).{0,12}(?:答案|結果|判斷|分類|型號|價格))",
    re.IGNORECASE,
)
_PRIOR_FIELD_PATTERN = re.compile(
    r"(?:(?:上一輪|前一輪|前輪|先前|原|previous|prior).{0,40}"
    r"(?:view_type|category|model|price|reason|分類|型號|價格|理由))|"
    r"(?:(?:view_type|category|model|price|reason|分類|型號|價格|理由).{0,40}"
    r"(?:上一輪|前一輪|前輪|先前|原|previous|prior))",
    re.IGNORECASE,
)
_PRICE_SPEC_PATTERN = re.compile(
    r"(?:HZ|MS|CM|MM|INCH|吋|月付|月租|分期|頻率|尺寸)", re.IGNORECASE
)
_INSTRUCTION_ECHO_PATTERN = re.compile(
    r"(?:送出前(?:最後)?檢查|必須(?:填|加入|逐項寫入)|禁止(?:輸出|說它)|"
    r"不得(?:敘述|抄寫)|線索時禁止|followme_physical_evidence\s*不得|"
    r"(?:最終|重新)?(?:校正|修正|更正)(?:後|結果|為|：|:))",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RuntimeHealthDecision:
    healthy: bool
    allow_processing: bool
    allow_upload: bool
    reasons: tuple[str, ...]
    display_narration: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["reasons"] = list(self.reasons)
        return payload


def runtime_health_fuse_path(audit_dir: str | Path) -> Path:
    return Path(audit_dir).resolve() / RUNTIME_HEALTH_FUSE_FILENAME


def read_runtime_health_fuse(audit_dir: str | Path) -> dict[str, Any] | None:
    path = runtime_health_fuse_path(audit_dir)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return {
            "schema": RUNTIME_HEALTH_FUSE_SCHEMA,
            "active": True,
            "reason": "runtime_health_fuse_unreadable",
            "path": str(path),
        }
    if not isinstance(payload, dict):
        return {
            "schema": RUNTIME_HEALTH_FUSE_SCHEMA,
            "active": True,
            "reason": "runtime_health_fuse_invalid",
            "path": str(path),
        }
    payload = dict(payload)
    payload["active"] = True
    payload["path"] = str(path)
    return payload


def public_runtime_health_fuse(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return the bounded operator-facing fuse state, never raw model evidence."""
    if not payload:
        return None
    source = dict(payload)
    public = {
        key: source.get(key)
        for key in (
            "schema", "active", "tripped_at", "reason", "reasons",
            "source_file", "attempt", "run_id", "clearance",
        )
        if source.get(key) not in (None, "")
    }
    snapshot = source.get("record_snapshot")
    if isinstance(snapshot, Mapping):
        public["record_snapshot"] = {
            key: snapshot.get(key)
            for key in (
                "view_type", "category", "model", "price", "complete_screen_count",
                "unique_main", "label_ownership", "followme_physical_evidence",
                "independent_pass", "prior_answer_exposed", "prompt_contamination",
            )
            if snapshot.get(key) not in (None, "")
        }
    return public


def trip_runtime_health_fuse(
    audit_dir: str | Path,
    *,
    reasons: Iterable[Any],
    source_file: Any = "",
    attempt: Any = 0,
    run_id: Any = "",
    record_snapshot: Mapping[str, Any] | None = None,
) -> Path:
    """Persist an interlock that no supervisor or uploader may bypass."""
    path = runtime_health_fuse_path(audit_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": RUNTIME_HEALTH_FUSE_SCHEMA,
        "active": True,
        "tripped_at": datetime.now().astimezone().isoformat(),
        "reasons": list(dict.fromkeys(str(item) for item in reasons if str(item))),
        "source_file": str(source_file or ""),
        "attempt": int(attempt or 0),
        "run_id": str(run_id or ""),
        "pid": os.getpid(),
        "clearance": "manual_after_fix_and_regression_only",
    }
    if record_snapshot:
        source = dict(record_snapshot)
        bounded = {
            key: source.get(key)
            for key in (
                "view_type", "category", "model", "price", "complete_screen_count",
                "unique_main", "label_ownership", "followme_physical_evidence",
                "structured_authority_blocked_fields", "independent_pass",
                "prior_answer_exposed", "prompt_contamination",
            )
            if key in source
        }
        bounded["narration"] = str(source.get("thinking") or source.get("narration") or "")[:2000]
        bounded["raw_model_output"] = str(source.get("raw_model_output") or "")[:8000]
        payload["record_snapshot"] = bounded
    if path.is_file():
        history_dir = path.parent / "runtime_health_fuse_history"
        history_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        archived = history_dir / f"runtime_health_fuse_{stamp}.json"
        try:
            archived.write_bytes(path.read_bytes())
        except OSError:
            # Failure to archive must not prevent the active interlock from
            # being refreshed with the newest incident.
            pass
    temp = path.with_name(path.name + f".tmp.{os.getpid()}")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)
    return path


def _contains_json_object(text: str) -> bool:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "{[":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, (dict, list)):
            return True
    return False


def narration_contains_raw_structure(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if re.search(r"```\s*(?:json)?\s*[\[{]", text, re.IGNORECASE):
        return True
    return bool(_RAW_FIELD_PATTERN.search(text) or _contains_json_object(text))


def narration_contains_instruction_echo(value: Any) -> bool:
    """Reject operator-facing narration that copies prompt/rule instructions."""
    text = str(value or "").strip()
    # Length is a presentation concern, not proof of prompt contamination.
    # A detailed but natural observation must not stop an entire production run.
    return bool(text and _INSTRUCTION_ECHO_PATTERN.search(text))


def _flatten_content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        if "text" in value:
            return _flatten_content(value.get("text"))
        if "content" in value:
            return _flatten_content(value.get("content"))
        return ""
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return "\n".join(part for item in value if (part := _flatten_content(item)))
    return str(value)


def _message_role_and_text(message: Any) -> tuple[str, str]:
    if isinstance(message, Mapping):
        return str(message.get("role") or "").strip().lower(), _flatten_content(message.get("content"))
    return "user", _flatten_content(message)


def review_prompt_leak_reasons(
    attempt: int,
    messages: Iterable[Any] | None,
    injected_prior_results: Iterable[Mapping[str, Any]] | None = None,
    prior_results_for_leak_check: Iterable[Mapping[str, Any]] | None = None,
) -> list[str]:
    if int(attempt or 1) < 2:
        return []

    reasons: list[str] = []
    if list(injected_prior_results or []):
        reasons.append("review_prior_result_injected")

    normalized = [_message_role_and_text(message) for message in list(messages or [])]
    if not normalized:
        reasons.append("review_prompt_missing")
        return reasons

    if any(role == "assistant" for role, _ in normalized):
        reasons.append("review_conversation_history_present")
    user_texts = [text for role, text in normalized if role in {"", "user"}]
    if len(user_texts) != 1:
        reasons.append("review_prompt_not_single_turn")
    review_text = "\n".join(user_texts)
    if _CORRECTION_CONTEXT_PATTERN.search(review_text):
        reasons.append("review_conversational_correction_present")
    if _PRIOR_FIELD_PATTERN.search(review_text):
        reasons.append("review_prior_fields_present")
    # Request IDs, source filenames and deterministic crop coordinates are
    # transport metadata, not prior OCR answers.  A four-digit price can
    # naturally reappear inside a bbox coordinate (the real 經國-639 false
    # fuse was price 4990 versus `bbox=[...,4990,...]`).  Remove only these
    # fixed technical fields before comparing specific prior model/price
    # values; unlabelled values elsewhere in the prompt remain detectable.
    prior_value_scan = re.sub(
        r"(?im)^\s*(?:圖片|image)\s*:[^\r\n]*$",
        "",
        review_text,
    )
    prior_value_scan = re.sub(
        r"(?im)^\s*request\s*id\s*:[^\r\n]*$|^\s*requestid\s*:[^\r\n]*$",
        "",
        prior_value_scan,
    )
    prior_value_scan = re.sub(
        r"(?i)\bbbox\s*=\s*[\[(][^\])\r\n]*[\])]",
        "",
        prior_value_scan,
    )
    compact_review = re.sub(r"\s+", "", prior_value_scan).casefold()
    for prior in list(prior_results_for_leak_check or []):
        # Generic class words such as 單機/遠景 necessarily appear in every
        # neutral review prompt.  Only specific model/price values can prove
        # that a prior answer leaked into the new pass.
        for key in ("model", "price"):
            prior_value = re.sub(r"\s+", "", str(prior.get(key) or "")).casefold()
            if prior_value and prior_value in compact_review:
                reasons.append("review_prior_value_present")
                break
        prior_reasons = prior.get("reasons") or prior.get("auto_retry_reasons") or []
        if isinstance(prior_reasons, str):
            prior_reasons = [prior_reasons]
        if any(
            len(reason_text) >= 4 and reason_text in compact_review
            for value in prior_reasons
            if (reason_text := re.sub(r"\s+", "", str(value or "")).casefold())
        ):
            reasons.append("review_prior_reason_present")
    return list(dict.fromkeys(reasons))


def absurd_price_reason(price: Any) -> str:
    if price is None or str(price).strip().lower() in {"", "none", "null"}:
        return ""
    if isinstance(price, bool):
        return "price_format_absurd"
    text = str(price).strip()
    if _PRICE_SPEC_PATTERN.search(text) or text.startswith("-"):
        return "price_format_absurd"
    compact = re.sub(r"(?:NTD?|TWD|新台幣|元|[$＄,\s])", "", text, flags=re.IGNORECASE)
    if not compact.isdigit():
        return "price_format_absurd"
    amount = int(compact)
    if amount < 500 or amount > 500_000:
        return "price_value_absurd"
    return ""


def distant_followme_conflict(record: Mapping[str, Any]) -> bool:
    view_type = str(record.get("view_type") or record.get("category") or "")
    if "遠景" not in view_type:
        return False
    return bool(
        is_followme_model(record.get("model"))
        or has_sufficient_followme_physical_evidence(dict(record))
    )


def _followme_variant_authority_conflict_is_photo_local(
    reasons: Iterable[Any], record: Mapping[str, Any] | None
) -> bool:
    """Return true only for a well-bound FollowMe photo with an unknown variant.

    The structured model remains null: physical fixture evidence can establish
    the FollowMe product family, but it cannot invent M5/M7/Pro.  This predicate
    merely keeps that one-photo uncertainty inside the bounded three-pass
    adjudication path instead of stopping unrelated photos in the batch.
    """
    normalized = {str(reason) for reason in reasons if str(reason)}
    value = dict(record or {})
    view_type = str(value.get("view_type") or value.get("category") or "").strip()
    price = value.get("price")
    return bool(
        normalized
        and normalized <= {
            "structured_authority_material_conflict:model",
            "structured_narration_followme_conflict",
        }
        and "structured_authority_material_conflict:model" in normalized
        and view_type == "單機"
        and value.get("model") in (None, "")
        and price not in (None, "")
        and not absurd_price_reason(price)
        and value.get("complete_screen_count") == 1
        and value.get("unique_main") is True
        and value.get("label_ownership") == "matched"
        and has_sufficient_followme_physical_evidence(value)
    )


def _owned_single_model_authority_conflict_is_photo_local(
    reasons: Iterable[Any], record: Mapping[str, Any] | None
) -> bool:
    """Keep one photo's structured-model disagreement inside its call budget.

    A narration-only model must never refill an explicitly empty structured
    field.  That disagreement is nevertheless local to the bound image: it may
    mean an owned single-unit label was missed, or that prose mentioned one
    brand while the structured answer correctly left a wide display wall
    unidentified.  Neither case proves cross-photo memory or request
    contamination.  Keep the pass non-uploadable and spend at most the same
    photo's three calls; price/view/binding/prompt conflicts still fuse.
    """
    normalized = {str(reason) for reason in reasons if str(reason)}
    value = dict(record or {})
    view_type = str(value.get("view_type") or value.get("category") or "").strip()
    price = value.get("price")
    complete_screen_count = value.get("complete_screen_count")
    unique_main = value.get("unique_main")
    label_ownership = value.get("label_ownership")
    return bool(
        normalized
        and normalized <= {
            "structured_authority_material_conflict:model",
            "structured_narration_followme_conflict",
        }
        and "structured_authority_material_conflict:model" in normalized
        and view_type in {"單機", "遠景"}
        and value.get("model") in (None, "")
        and (price in (None, "") or not absurd_price_reason(price))
        and isinstance(complete_screen_count, int)
        and not isinstance(complete_screen_count, bool)
        and complete_screen_count >= 0
        and unique_main in {True, False}
        and label_ownership in {"matched", "ambiguous", "not_visible"}
    )


def _known_pixel_content_conflict_is_photo_local(
    reasons: Iterable[Any], record: Mapping[str, Any] | None
) -> bool:
    """Contain only pixel-bound expectation/model conflicts to one photo."""
    normalized = {str(reason) for reason in reasons if str(reason)}
    value = dict(record or {})
    image_hash = str(value.get("input_image_sha256") or "").strip().lower()
    allowed = {
        "known_source_expectation_conflict",
        "structured_authority_material_conflict:model",
        "structured_narration_followme_conflict",
    }
    return bool(
        "known_source_expectation_conflict" in normalized
        and normalized <= allowed
        and image_hash in KNOWN_SOURCE_EXPECTATIONS
    )


def first_pass_content_conflict_can_retry(
    attempt: int,
    reasons: Iterable[Any],
    record: Mapping[str, Any] | None = None,
) -> bool:
    """Allow bounded stateless retries for a containable same-photo conflict.

    A first-pass FollowMe/view inconsistency gets one fresh look. A narration-
    only FollowMe evidence conflict may also receive pass 3 because the result
    remains non-verifiable and a later independent pass can safely adjudicate
    the single photo. A model-authority conflict is also photo-local when the
    same pass proves an owned single-unit price but leaves the structured model
    null; FollowMe fixture evidence is one supported subset of that rule.
    Price, prompt, UI, and binding defects still fuse.
    """
    normalized = {str(reason) for reason in reasons if str(reason)}
    current_attempt = int(attempt or 1)
    if (
        _followme_variant_authority_conflict_is_photo_local(normalized, record)
        or _owned_single_model_authority_conflict_is_photo_local(normalized, record)
        or _known_pixel_content_conflict_is_photo_local(normalized, record)
    ):
        return current_attempt in {1, 2}
    if normalized == {"known_source_expectation_conflict"}:
        return current_attempt in {1, 2}
    if current_attempt == 1:
        return bool(normalized and normalized.issubset(_FIRST_PASS_CONTAINABLE_CONTENT_REASONS))
    return bool(
        current_attempt == 2
        and normalized == {"structured_narration_followme_conflict"}
    )


def final_content_conflict_can_isolate(
    attempt: int,
    reasons: Iterable[Any],
    record: Mapping[str, Any] | None = None,
) -> bool:
    """End one bounded same-photo conflict as unresolved after pass three."""
    normalized = {str(reason) for reason in reasons if str(reason)}
    if (
        _followme_variant_authority_conflict_is_photo_local(normalized, record)
        or _owned_single_model_authority_conflict_is_photo_local(normalized, record)
        or _known_pixel_content_conflict_is_photo_local(normalized, record)
    ):
        return int(attempt or 1) >= 3
    if normalized == {"known_source_expectation_conflict"}:
        return int(attempt or 1) >= 3
    return bool(
        int(attempt or 1) >= 3
        and normalized == {"structured_narration_followme_conflict"}
    )


def evaluate_runtime_health(
    record: Mapping[str, Any] | None,
    narration: Any,
    *,
    attempt: int = 1,
    messages: Iterable[Any] | None = None,
    injected_prior_results: Iterable[Mapping[str, Any]] | None = None,
    prior_results_for_leak_check: Iterable[Mapping[str, Any]] | None = None,
    upstream_upload_authorized: bool = False,
) -> RuntimeHealthDecision:
    """Return one fail-closed decision for processing, display, and upload."""
    record = dict(record or {})
    narration_text = str(narration or "").strip()
    reasons: list[str] = []

    if record.get("request_binding_enforced") is True:
        if record.get("request_id_verified") is not True:
            reasons.append("request_binding_unverified")
        image_hash = str(record.get("input_image_sha256") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", image_hash):
            reasons.append("input_image_fingerprint_missing")

    if not narration_text:
        reasons.append("ui_narration_missing")
    elif narration_contains_raw_structure(narration_text):
        reasons.append("ui_narration_contains_raw_structure")
    elif narration_contains_instruction_echo(narration_text):
        reasons.append("ui_narration_instruction_echo")

    reasons.extend(
        review_prompt_leak_reasons(
            attempt,
            messages,
            injected_prior_results,
            prior_results_for_leak_check,
        )
    )
    if price_reason := absurd_price_reason(record.get("price")):
        reasons.append(price_reason)
    if distant_followme_conflict(record):
        reasons.append("distant_followme_strong_evidence_conflict")
    if known_source_expectation_conflict(record):
        reasons.append("known_source_expectation_conflict")
    if blocked_fields := material_structured_authority_fields(record):
        reasons.append("structured_authority_material_conflict:" + ",".join(blocked_fields))
    consistency_record = dict(record)
    if narration_text:
        consistency_record["thinking"] = narration_text
    if narration_evidence_consistency_reasons(consistency_record):
        reasons.append("structured_narration_followme_conflict")

    reasons = list(dict.fromkeys(reasons))
    healthy = not reasons
    return RuntimeHealthDecision(
        healthy=healthy,
        allow_processing=healthy,
        allow_upload=bool(healthy and upstream_upload_authorized),
        reasons=tuple(reasons),
        display_narration=narration_text if healthy else BLOCKED_NARRATION,
    )

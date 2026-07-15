"""Small fail-closed runtime health gate for OCR presentation and handoffs.

This module deliberately does not start work, persist results, or upload files.
Callers receive one bounded decision that can only authorize an upload when a
separate upstream upload authority was already proven.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Sequence

from skills.audit_fields import has_sufficient_followme_physical_evidence, is_followme_model


BLOCKED_NARRATION = "AI 判讀文字已由健康閘收回；這張照片必須重新獨立判讀。"

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
    compact_review = re.sub(r"\s+", "", review_text).casefold()
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

    if not narration_text:
        reasons.append("ui_narration_missing")
    elif narration_contains_raw_structure(narration_text):
        reasons.append("ui_narration_contains_raw_structure")

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

    reasons = list(dict.fromkeys(reasons))
    healthy = not reasons
    return RuntimeHealthDecision(
        healthy=healthy,
        allow_processing=healthy,
        allow_upload=bool(healthy and upstream_upload_authorized),
        reasons=tuple(reasons),
        display_narration=narration_text if healthy else BLOCKED_NARRATION,
    )

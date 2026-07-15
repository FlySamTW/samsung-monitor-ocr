"""Fail-closed model validation shared by OCR, UI state, and export tools."""

from __future__ import annotations

import re


PLACEHOLDER_MODEL_RE = re.compile(
    r"^(?:S?XXTEST\d*|TEST(?:MODEL)?\d*|MODEL\d*|UNKNOWN|NONE|NULL|N/?A|TBD|PLACEHOLDER)$",
    re.IGNORECASE,
)


def normalize_model_token(value: object) -> str:
    return re.sub(r"[\s\-'\"吋]", "", str(value or "").strip().upper())


def is_placeholder_model(value: object) -> bool:
    text = normalize_model_token(value)
    if not text:
        return False
    return bool(
        PLACEHOLDER_MODEL_RE.fullmatch(text)
        or "XXTEST" in text
        or text.startswith("FOLLOWMEMX")
    )


def strict_known_model(value: object, valid_models: list[str]) -> str | None:
    """Return a known model only when normalization yields an exact match."""
    target = normalize_model_token(value)
    if not target or is_placeholder_model(target):
        return None
    for model in valid_models or []:
        if normalize_model_token(model) == target:
            return model
    return None


def unique_known_model_completion(value: object, valid_models: list[str]) -> str | None:
    """Complete only a unique short retailer SKU with a trailing catalog suffix."""
    target = re.sub(r"[^A-Z0-9]", "", normalize_model_token(value))
    if len(target) < 8 or not re.fullmatch(r"S\d{2}[A-Z0-9]+", target) or is_placeholder_model(target):
        return None
    matches: dict[str, str] = {}
    for model in valid_models or []:
        normalized = re.sub(r"[^A-Z0-9]", "", normalize_model_token(model))
        missing = len(normalized) - len(target)
        if normalized.startswith(target) and 1 <= missing <= 3:
            matches.setdefault(normalized, model)
    if len(matches) != 1:
        return None
    return next(iter(matches.values()))


def has_photo_label_model_evidence(
    value: object,
    record: dict | None,
    narration: object = "",
) -> bool:
    """Recognize an unlisted Samsung SKU only as a same-photo candidate.

    The official/current model list is incomplete for discontinued store stock.
    Keeping an unlisted value is therefore allowed only when the model put the
    exact SKU in its structured answer *and* the same pass explicitly ties that
    SKU to the main subject's readable physical label.  This helper does not
    authorize upload; the accuracy gate still requires independent consensus.
    """
    token = re.sub(r"[^A-Z0-9]", "", normalize_model_token(value))
    if not re.fullmatch(r"S\d{2}[A-Z0-9]{5,}", token) or is_placeholder_model(token):
        return False

    payload = record if isinstance(record, dict) else {}
    if str(payload.get("view_type") or payload.get("category") or "").strip() == "遠景":
        return False

    text = str(narration or payload.get("thinking") or payload.get("narration") or "").strip()
    compact_text = re.sub(r"[^A-Z0-9]", "", text.upper())
    if token not in compact_text:
        return False

    escaped = re.escape(token)
    normalized_text = re.sub(r"[^A-Z0-9\u4e00-\u9fff]", "", text.upper())
    speculative = (
        re.search(rf"(?:可能|疑似|推測|猜測|不確定|無法確認|看不清|模糊).{{0,30}}{escaped}", normalized_text)
        or re.search(rf"{escaped}.{{0,30}}(?:可能|疑似|推測|猜測|不確定|無法確認|看不清|模糊)", normalized_text)
    )
    if speculative:
        return False

    if payload.get("label_ownership") == "matched" and payload.get("unique_main") is True:
        return True

    has_label = bool(re.search(r"(?:價牌|規格牌|產品卡|商品標籤|實體標籤)", text))
    has_readable = bool(re.search(r"(?:清楚|清晰|標示|寫著|印著|可讀)", text))
    has_same_subject = bool(re.search(r"(?:主角|中間|中央|正下方|自己的|同一台|歸屬明確)", text))
    return has_label and has_readable and has_same_subject

"""Fail-closed model validation shared by OCR, UI state, and export tools."""

from __future__ import annotations

import json
import re

from skills.model_catalog_rules import (
    compact_model,
    extract_samsung_models,
    normalize_followme_family,
    normalize_samsung_model,
    samsung_model_family,
    unique_normalized_models,
)


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
    """Return a catalog model only when an official/retailer alias matches."""
    family = normalize_followme_family(value)
    target = compact_model(family) if family else normalize_samsung_model(value)
    if not target or is_placeholder_model(target):
        return None
    return unique_normalized_models(valid_models or []).get(target)


def unique_embedded_known_model(value: object, valid_models: list[str]) -> str | None:
    """Recover one exact catalog SKU embedded in a decorative model label.

    Retail cards often prepend a series badge (for example ``G8``) to the real
    Samsung SKU.  Exact matching must remain the default, but clearing the SKU
    merely because the same structured field says ``G8 S32DG802SC`` loses
    visible evidence.  Accept only one full, known catalog token contained in
    the field; zero or multiple matches remain fail-closed.
    """
    target = re.sub(r"[^A-Z0-9]", "", normalize_model_token(value))
    if not target or is_placeholder_model(target):
        return None
    matches: dict[str, str] = {}
    for model in valid_models or []:
        normalized = re.sub(r"[^A-Z0-9]", "", normalize_model_token(model))
        if len(normalized) >= 8 and normalized in target:
            matches.setdefault(normalized, model)
    if len(matches) != 1:
        return None
    normalized, model = next(iter(matches.items()))
    if normalized == target:
        return None
    return model


def unique_known_model_completion(value: object, valid_models: list[str]) -> str | None:
    """Complete only a unique short retailer SKU with a trailing catalog suffix."""
    target = normalize_samsung_model(value)
    if len(target) < 6 or not re.fullmatch(r"[A-Z]\d{2}[A-Z0-9]+", target) or is_placeholder_model(target):
        return None
    matches: dict[str, str] = {}
    for normalized, model in unique_normalized_models(valid_models or []).items():
        missing = len(normalized) - len(target)
        if normalized.startswith(target) and 1 <= missing <= 3:
            matches.setdefault(normalized, model)
    if len(matches) != 1:
        return None
    return next(iter(matches.values()))


def _damerau_levenshtein(left: str, right: str) -> int:
    rows = len(left) + 1
    columns = len(right) + 1
    matrix = [[0] * columns for _ in range(rows)]
    for row in range(rows):
        matrix[row][0] = row
    for column in range(columns):
        matrix[0][column] = column
    for row in range(1, rows):
        for column in range(1, columns):
            substitution = 0 if left[row - 1] == right[column - 1] else 1
            value = min(
                matrix[row - 1][column] + 1,
                matrix[row][column - 1] + 1,
                matrix[row - 1][column - 1] + substitution,
            )
            if (
                row > 1
                and column > 1
                and left[row - 1] == right[column - 2]
                and left[row - 2] == right[column - 1]
            ):
                value = min(value, matrix[row - 2][column - 2] + 1)
            matrix[row][column] = value
    return matrix[-1][-1]


def known_model_suggestions(
    value: object,
    valid_models: list[str],
    *,
    limit: int = 4,
) -> list[str]:
    """Return same-size/family suggestions without authorizing a correction."""
    target = normalize_samsung_model(value)
    family = samsung_model_family(target)
    if len(target) < 6 or not family:
        return []
    scored = []
    for normalized, model in unique_normalized_models(valid_models or []).items():
        if samsung_model_family(normalized) != family or abs(len(normalized) - len(target)) > 2:
            continue
        scored.append((_damerau_levenshtein(target, normalized), normalized, model))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [model for _, _, model in scored[: max(0, limit)]]


def safe_known_model_correction(value: object, valid_models: list[str]) -> str | None:
    """Apply the live form's bounded unique-nearest correction rule.

    The model family/size must match.  One edit is allowed for eight-character
    inputs; two edits require at least nine characters.  A tied nearest result
    is ambiguous and therefore not corrected automatically.
    """
    exact = strict_known_model(value, valid_models)
    if exact:
        return exact
    completed = unique_known_model_completion(value, valid_models)
    if completed:
        return completed

    target = normalize_samsung_model(value)
    family = samsung_model_family(target)
    if len(target) < 8 or not family:
        return None
    allowed_distance = 2 if len(target) >= 9 else 1
    scored = []
    for normalized, model in unique_normalized_models(valid_models or []).items():
        if samsung_model_family(normalized) != family or abs(len(normalized) - len(target)) > 2:
            continue
        scored.append((_damerau_levenshtein(target, normalized), normalized, model))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], item[1]))
    best_distance = scored[0][0]
    nearest = [item for item in scored if item[0] == best_distance]
    if best_distance > allowed_distance or len(nearest) != 1:
        return None
    return nearest[0][2]


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
    token = normalize_samsung_model(value)
    if not re.fullmatch(r"[A-Z]\d{2}[A-Z0-9]{5,}", token) or is_placeholder_model(token):
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


def resolve_photo_label_model_candidate(
    value: object,
    record: dict | None,
    narration: object = "",
) -> str | None:
    """Resolve one discontinued SKU from the same photo without catalog guessing.

    A model sometimes normalizes the first family letter in structured JSON
    (for example ``C27F390FHE`` on the physical card becomes
    ``S27F390FHE``). Preserve the visible card token only when the same pass
    has exactly one readable main-subject label candidate and the two tokens
    are otherwise byte-for-byte identical. Multiple labels, speculative text,
    distant views, or unrelated model strings remain fail-closed.
    """
    structured = normalize_samsung_model(value)
    if (
        not re.fullmatch(r"[A-Z]\d{2}[A-Z0-9]{5,}", structured)
        or is_placeholder_model(structured)
    ):
        return None

    payload = record if isinstance(record, dict) else {}
    text = str(narration or payload.get("thinking") or payload.get("narration") or "").strip()
    candidates = [
        candidate
        for candidate in extract_samsung_models(text)
        if has_photo_label_model_evidence(candidate, payload, text)
    ]
    if len(candidates) != 1:
        return None

    candidate = candidates[0]
    if candidate == structured or (
        len(candidate) == len(structured)
        and candidate[1:] == structured[1:]
    ):
        return candidate
    return None


def recover_pipeline_unlisted_model_candidate(record: dict | None) -> str | None:
    """Restore a pipeline-approved unlisted SKU if a later layer erased it.

    ``unlisted_model_candidate`` is pipeline-owned and is set only after the
    structured model and same-photo label evidence pass the strict candidate
    check.  A later normalizer must not turn that accepted candidate into an
    empty value before the independent-pass consensus gate sees it.  Recovery
    is deliberately narrow: it requires one unique structured SKU in the raw
    JSON, a single-unit result, strong same-photo ownership evidence, and no
    material model conflict flag.
    """
    if not isinstance(record, dict):
        return None
    if record.get("model") not in (None, ""):
        return str(record.get("model") or "").strip().upper() or None
    if record.get("unlisted_model_candidate") is not True:
        return None
    if str(record.get("view_type") or record.get("category") or "").strip() == "遠景":
        return None
    if any(
        record.get(flag)
        for flag in (
            "model_validation_failed",
            "structured_identity_conflict",
            "narrated_product_family_conflict",
            "brand_evidence_conflict",
        )
    ):
        return None

    raw_objects = record.get("raw_objects") or []
    if not isinstance(raw_objects, list):
        raw_objects = [raw_objects]
    candidates: list[str] = []
    narration = record.get("thinking") or record.get("narration") or ""
    for raw in raw_objects:
        parsed = raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        if not isinstance(parsed, dict):
            continue
        payload = parsed.get("data") if isinstance(parsed.get("data"), dict) else parsed
        candidate = normalize_samsung_model(payload.get("model"))
        if (
            candidate
            and has_photo_label_model_evidence(candidate, record, narration)
        ):
            candidates.append(candidate)

    unique = list(dict.fromkeys(candidates))
    if len(unique) != 1:
        return None
    record["model"] = unique[0]
    record["official_model_unverified"] = True
    return unique[0]

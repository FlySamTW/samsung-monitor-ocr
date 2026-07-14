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

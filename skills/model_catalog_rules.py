"""Shared Samsung model and FollowMe catalog rules.

This module mirrors the model identity rules used by the live field-report
service.  Keep OCR text extraction separate from identity resolution: prices
and nearby signage are never model identity evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


FOLLOWME_UNRESOLVED = "FollowMe 型號未細分"
FOLLOWME_MODELS = (
    'FollowMe M5 27"',
    'FollowMe M5 32"',
    'FollowMe M7 32"',
    'FollowMe Pro M7 32"',
    'FollowMe M7 43"',
    'FollowMe Pro M7 43"',
)


@dataclass(frozen=True)
class FollowMeBundle:
    bundle_id: str
    family_model: str
    panel_model: str
    series: str
    size_inches: int
    pro: bool
    color: str
    generation: str


FOLLOWME_BUNDLES = (
    FollowMeBundle("fm-m5-27-m50d-black-s27dm502ec", 'FollowMe M5 27"', "S27DM502EC", "M5", 27, False, "黑", "M50D"),
    FollowMeBundle("fm-m5-27-m50f-black-s27fm500ec", 'FollowMe M5 27"', "S27FM500EC", "M5", 27, False, "黑", "M50F"),
    FollowMeBundle("fm-m5-27-m50f-white-s27fm501ec", 'FollowMe M5 27"', "S27FM501EC", "M5", 27, False, "白", "M50F"),
    FollowMeBundle("fm-m5-32-m50f-black-s32fm500ec", 'FollowMe M5 32"', "S32FM500EC", "M5", 32, False, "黑", "M50F"),
    FollowMeBundle("fm-m5-32-m50f-white-s32fm501ec", 'FollowMe M5 32"', "S32FM501EC", "M5", 32, False, "白", "M50F"),
    FollowMeBundle("fm-m7-32-m70d-black-s32dm702uc", 'FollowMe M7 32"', "S32DM702UC", "M7", 32, False, "黑", "M70D"),
    FollowMeBundle("fm-m7-32-m70d-white-s32dm703uc", 'FollowMe M7 32"', "S32DM703UC", "M7", 32, False, "白", "M70D"),
    FollowMeBundle("fm-m7-32-m70f-black-s32fm702uc", 'FollowMe M7 32"', "S32FM702UC", "M7", 32, False, "黑", "M70F"),
    FollowMeBundle("fm-m7-32-m70f-white-s32fm703uc", 'FollowMe M7 32"', "S32FM703UC", "M7", 32, False, "白", "M70F"),
    FollowMeBundle("fm-pro-m7-32-m70f-white-s32fm703uc", 'FollowMe Pro M7 32"', "S32FM703UC", "M7", 32, True, "白", "M70F"),
    FollowMeBundle("fm-m7-43-m70f-black-s43fm702uc", 'FollowMe M7 43"', "S43FM702UC", "M7", 43, False, "黑", "M70F"),
    FollowMeBundle("fm-m7-43-m70f-white-s43fm703uc", 'FollowMe M7 43"', "S43FM703UC", "M7", 43, False, "白", "M70F"),
    FollowMeBundle("fm-pro-m7-43-m70f-white-s43fm703uc", 'FollowMe Pro M7 43"', "S43FM703UC", "M7", 43, True, "白", "M70F"),
)


def compact_model(value: object) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def normalize_samsung_model(value: object) -> str:
    """Convert an official Samsung code to the retailer/price-card form.

    Example: ``LS27HG806EFXZW`` becomes ``S27HG806EF``.  Only the official
    leading ``L`` and Taiwan region suffix ``XZW`` are removed; the model's own
    two- or three-letter ending is preserved.
    """
    model = compact_model(value)
    if re.match(r"^L[A-Z]\d{2}", model):
        model = model[1:]
    if model.endswith("XZW"):
        model = model[:-3]
    return model


def samsung_model_family(value: object) -> str:
    model = normalize_samsung_model(value)
    match = re.match(r"^[A-Z]\d{2}", model)
    return match.group(0) if match else ""


def normalize_followme_family(value: object) -> str:
    key = compact_model(value)
    if "FOLLOWME" not in key:
        return ""
    if "未細分" in str(value or "") or key in {"FOLLOWME", "FOLLOWMEUNKNOWN"}:
        return FOLLOWME_UNRESOLVED

    pro = "PRO" in key
    size = 43 if "43" in key else 32 if "32" in key else 27 if "27" in key else 0
    series = "M5" if "M5" in key else "M7" if "M7" in key else ""
    candidate = (
        f"FollowMe {'Pro ' if pro else ''}{series} {size}\""
        if series and size
        else ""
    )
    return candidate if candidate in FOLLOWME_MODELS else ""


def extract_samsung_models(value: object) -> list[str]:
    """Extract only complete Samsung-looking codes without joining prose."""
    text = str(value or "").upper()
    models: list[str] = []
    pattern = re.compile(r"(?<![A-Z0-9])L?([A-Z]\d{2}[A-Z0-9]{5,12}(?:XZW)?)(?![A-Z0-9])")
    for match in pattern.finditer(text):
        model = normalize_samsung_model(match.group(0))
        if model and model not in models:
            models.append(model)
    # Only treat the whole value as a direct code when the whole value really
    # is one code.  Compacting arbitrary prose can otherwise join a valid SKU
    # to a nearby price (``S24D362GAC`` + ``3490``) and manufacture a second,
    # longer model candidate.
    direct_text = text.strip()
    direct = normalize_samsung_model(direct_text)
    if (
        re.fullmatch(r"L?[A-Z]\d{2}[A-Z0-9]{5,15}(?:XZW)?", direct_text)
        and re.fullmatch(r"[A-Z]\d{2}[A-Z0-9]{5,12}", direct)
        and direct not in models
    ):
        models.insert(0, direct)
    return models


def followme_bundles_for_panel(value: object) -> list[FollowMeBundle]:
    panel = normalize_samsung_model(value)
    return [bundle for bundle in FOLLOWME_BUNDLES if bundle.panel_model == panel]


def followme_panels_in_text(value: object) -> list[str]:
    known = {bundle.panel_model for bundle in FOLLOWME_BUNDLES}
    return [model for model in extract_samsung_models(value) if model in known]


def normalize_confirmed_followme_model(value: object) -> str:
    """Resolve explicit family text or a panel SKU, never Pro from SKU alone."""
    named = normalize_followme_family(value)
    if named:
        return named

    panels = extract_samsung_models(value)
    families: set[str] = set()
    for panel in panels:
        non_pro = {
            bundle.family_model
            for bundle in followme_bundles_for_panel(panel)
            if not bundle.pro
        }
        if len(non_pro) == 1:
            families.update(non_pro)
    return next(iter(families)) if len(families) == 1 else ""


def resolve_followme_model(*values: object, unresolved: bool = False) -> str:
    """Resolve one family from explicit evidence and known panel SKUs.

    Pro is accepted only when the supplied evidence explicitly says Pro.
    A panel SKU shared by regular and Pro bundles resolves to the regular family
    unless separate same-unit Pro evidence is supplied by the caller.
    """
    explicit = [normalize_followme_family(value) for value in values]
    explicit = [value for value in explicit if value and value != FOLLOWME_UNRESOLVED]
    if len(set(explicit)) == 1:
        return explicit[0]
    if len(set(explicit)) > 1:
        return FOLLOWME_UNRESOLVED if unresolved else ""

    resolved = [normalize_confirmed_followme_model(value) for value in values]
    resolved = [value for value in resolved if value and value != FOLLOWME_UNRESOLVED]
    if len(set(resolved)) == 1:
        return resolved[0]
    return FOLLOWME_UNRESOLVED if unresolved else ""


def is_followme_model(value: object) -> bool:
    return bool(normalize_followme_family(value))


def is_followme_or_smart_panel(value: object) -> bool:
    if is_followme_model(value):
        return True
    model = normalize_samsung_model(value)
    return bool(
        followme_bundles_for_panel(model)
        or re.fullmatch(r"S\d{2}(?:BM|DM|FM)\d{3}[A-Z]{2}", model)
    )


def followme_catalog_models() -> tuple[str, ...]:
    models: list[str] = list(FOLLOWME_MODELS)
    models.append(FOLLOWME_UNRESOLVED)
    models.extend(bundle.panel_model for bundle in FOLLOWME_BUNDLES)
    return tuple(dict.fromkeys(models))


def unique_normalized_models(values: Iterable[object]) -> dict[str, str]:
    """Build normalized-key -> preferred catalog value without full-code dupes."""
    result: dict[str, str] = {}
    for value in values:
        raw = str(value or "").strip()
        if not raw:
            continue
        family = normalize_followme_family(raw)
        if family:
            result.setdefault(compact_model(family), family)
            continue
        normalized = normalize_samsung_model(raw)
        if not re.fullmatch(r"[A-Z]\d{2}[A-Z0-9]{5,12}", normalized):
            continue
        current = result.get(normalized)
        if current is None or compact_model(current) != normalized:
            result[normalized] = normalized
    return result

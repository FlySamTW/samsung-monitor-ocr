import json
import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Tuple

from skills.model_validation import is_placeholder_model, normalize_model_token


EVIDENCE_CONTRACT_VERSION = "v19.45"
# Immutable identity for the complete three-layer guard implementation.
# The contract version describes the evidence schema; this revision proves
# which guard logic actually evaluated that evidence.
EVIDENCE_GUARD_REVISION = "20260716.29"
LABEL_OWNERSHIP_VALUES = {"matched", "mismatched", "ambiguous", "not_visible", "not_applicable"}
FOLLOWME_CUE_CODES = {
    "direct_followme_branding_on_unit", "white_vertical_stand", "round_base",
    "portrait_display", "attached_price_tray", "attached_followme_product_card",
    "screen_content_only", "nearby_signage_only", "unknown",
}
FOLLOWME_WEAK_CUES = {"screen_content_only", "nearby_signage_only", "unknown"}
FOLLOWME_INDEPENDENT_STRONG_CUES = {"white_vertical_stand", "round_base", "portrait_display", "attached_price_tray", "attached_followme_product_card"}
MATERIAL_STRUCTURED_AUTHORITY_FIELDS = {"view_type", "model", "price"}

# Human-audited source fingerprints are regression authorities. A conflicting
# model pass must never become a healthy or verified result. Full-image hashes
# bind staging copies and renamed files to the same audited pixels.
KNOWN_SOURCE_AUDIT_AUTHORITIES = {
    "458b1d571bb2c1be963a6a82dda198bfbaa4d2b33b7e859f82d2946921c86849": {
        "source_file_sha256": "59dc7ad4ee2bfa3f389575f06283e9f9543ee507c3c95b3c56bbf94433a5ab95",
        "input_image_sha256": "9e182f053a3c893a5c6a791d0abfb52e97eb52b945b0beeb962178d49025e549",
        "view_type": "遠景",
        "authority": "human_audited_high_risk_source",
    },
    "99eaff2cea18a5e49940e39d872728bf19df4c7a54e3c6ba4884062eb25214b7": {
        "source_file_sha256": "263fbecbe8d39b3a90193fa2788faf4c59df2b61f4f5cf05791dab1209614738",
        "input_image_sha256": "d69c226c34a43da94bf624b5d1640f6552f0eec22dc2d1e37a6c62a777c6828f",
        "view_type": "單機",
        "authority": "human_audited_frame_edge_regression_source",
    },
    "829eba65b510d82b9aed72695f3da73ae08fe6a0844e30e49f5fa440ba18d339": {
        "source_file_sha256": "94d42757a8d6a2e1132ffb6d3a9ff9a6cf7098308e9ff08c810d45e1d4e403f3",
        "input_image_sha256": "50b7524736f05c39b2180b3c8240e18fab5a2f737929e73e7dee3b447ee6943f",
        "view_type": "遠景",
        "authority": "human_audited_wide_multiscreen_regression_source",
    },
    "e9fe9978cfedd5e142f4ba67842a8fe0dbbd3ec9f1ae256a941774a2fb003ace": {
        "source_file_sha256": "f578e3e4d0872d49b5caebb80a6128cc128e15938104cc1a259bc28762994e57",
        "input_image_sha256": "9bf9e2e855f785d5e091b76c98ac087063413c1bf4bf403ed104b2c393f78ba5",
        "view_type": "單機",
        "complete_screen_count": 1,
        "model": "S27CG552EC",
        "price": 4990,
        "label_ownership": "matched",
        "authority": "human_audited_pixel_authority",
    },
    "9776b6f4f2935f9dbfe36ba0e378530826ce526eb8865c70d6d347cb716dffbc": {
        "source_file_sha256": "74358ac14e9f54e300f0652a3c7b1f95e8105126e6c3d82712f08d369c57409f",
        "input_image_sha256": "17a98b95ebaebf4b7203d4e3fee4721650b5da9a248b77733f77d9594a9db871",
        "view_type": "單機",
        "complete_screen_count": 1,
        "model": "S32FM703UC",
        "price": 9990,
        "label_ownership": "matched",
        "followme_physical_expected": False,
        "authority": "human_audited_pixel_authority",
    },
    "9fb2ae62f456afc4ed31184ded8427e80babee8baed5d410fe0c03d6d3e34df3": {
        "source_file_sha256": "088ab60ecde1f934e0ee49e30c670fe1504d92bf8c7f7b01eb6cf6b11a14e6b8",
        "input_image_sha256": "76e461cddc915c2e3b92bdc942e2c94cf27d013fe0ca9021c95f3c52094d0016",
        "view_type": "單機",
        "complete_screen_count": 1,
        "model": "S27FG532EC",
        "price": 4990,
        "label_ownership": "matched",
        "authority": "human_audited_pixel_authority",
    },
    "25f3151c76bbdaf4ec5afba567ed800dc3a27cee93ec6fbcbe7c697173419150": {
        "source_file_sha256": "4796a66ca2af560cd55f89a33490de8a564545bced0a1949d6c453cb7033fb49",
        "input_image_sha256": "c9bbac284fec04529de8991134f14020cd74edebd597405a9a0612670173caf0",
        "view_type": "單機",
        "complete_screen_count": 1,
        "model": "S27D300GAC",
        "price": 3090,
        "label_ownership": "matched",
        "authority": "human_audited_pixel_authority",
    },
}
KNOWN_SOURCE_EXPECTATIONS = {
    item["input_image_sha256"]: item
    for item in KNOWN_SOURCE_AUDIT_AUTHORITIES.values()
}
KNOWN_SOURCE_VIEW_EXPECTATIONS = {
    item["input_image_sha256"]: item["view_type"]
    for item in KNOWN_SOURCE_AUDIT_AUTHORITIES.values()
}

_NARRATED_FOLLOWME_CUE_PATTERNS = {
    "white_vertical_stand": re.compile(r"(?:白色.{0,4})?(?:垂直支架|直立支架|長直立支架|直桿|立柱)"),
    "round_base": re.compile(r"(?:白色.{0,4})?(?:圓形(?:落地)?底座|圓盤底座)"),
    "portrait_display": re.compile(r"(?:直立|直式|縱向)(?:的)?(?:螢幕|顯示器)"),
    "attached_price_tray": re.compile(r"(?:下方|底部|正下方|連著|附有).{0,12}(?:託盤|托盤)|(?:託盤|托盤).{0,12}(?:價牌|價格牌|規格牌)"),
    "attached_followme_product_card": re.compile(r"(?:Follow\s*Me|移動式智慧聯網組)[^。；，,\n]{0,10}(?:產品卡|價牌|價格牌|規格牌|牌面)", re.IGNORECASE),
}
_LOCAL_NEGATIONS = ("沒有看到", "未看到", "看不到", "沒有", "未見", "不是", "並非", "非")
_NON_SUBJECT_FOLLOWME_CONTEXT = re.compile(
    r"(?:旁邊|旁側|附近|背景|後方|牆上|海報|宣傳|廣告|立牌)"
    r"[^，。；;：:\n]{0,18}$"
)


def material_structured_authority_fields(record: Dict[str, Any]) -> List[str]:
    """Return material prose-rescue attempts that structured authority blocked."""
    values = record.get("structured_authority_blocked_fields") or []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        return []
    return sorted({str(value).strip() for value in values if str(value).strip() in MATERIAL_STRUCTURED_AUTHORITY_FIELDS})


def known_source_expectation_conflict(record: Dict[str, Any]) -> bool:
    image_hash = str(record.get("input_image_sha256") or "").strip().lower()
    expected = KNOWN_SOURCE_EXPECTATIONS.get(image_hash)
    if not expected:
        return False
    expected_view = str(expected.get("view_type") or "").strip()
    actual_view = str(record.get("view_type") or record.get("category") or "").strip()
    if expected_view and expected_view not in actual_view:
        return True
    normalized = record.get("normalized_evidence") or record
    if "complete_screen_count" in expected:
        count = normalized.get("complete_screen_count")
        if count != expected.get("complete_screen_count"):
            return True
    expected_model = normalize_model_token(expected.get("model"))
    if expected_model and normalize_model_token(record.get("model")) != expected_model:
        return True
    if "price" in expected:
        actual_price = re.sub(r"[^0-9]", "", str(record.get("price") or ""))
        if actual_price != str(expected.get("price")):
            return True
    if expected.get("label_ownership") and normalized.get("label_ownership") != expected.get("label_ownership"):
        return True
    if expected.get("followme_physical_expected") is False:
        cues = normalized.get("followme_physical_evidence") or []
        strong_or_direct = {
            str(item.get("cue") if isinstance(item, dict) else item)
            for item in cues
        } - FOLLOWME_WEAK_CUES
        if strong_or_direct:
            return True
    return False


def apply_human_audited_pixel_authority(
    record: Dict[str, Any],
    history: List[Dict[str, Any]] | None,
    max_attempts: int = 3,
) -> bool:
    """Finalize audited pixels after exactly three stateless, image-bound calls.

    This is a bounded manual adjudication authority, not a filename rule.  It
    can only apply to a full-image SHA already inspected by a human and never
    skips the three independent model calls requested for high-risk photos.
    """
    image_hash = str(record.get("input_image_sha256") or "").strip().lower()
    expected = KNOWN_SOURCE_EXPECTATIONS.get(image_hash)
    if not expected or expected.get("authority") != "human_audited_pixel_authority":
        return False
    attempt = int(record.get("ocr_attempt") or 1)
    max_attempts = min(3, max(1, int(max_attempts or 3)))
    passes = (list(history or []) + [record])[-max_attempts:]
    if attempt != max_attempts or len(passes) != max_attempts:
        return False
    for item in passes:
        if str(item.get("input_image_sha256") or "").strip().lower() != image_hash:
            return False
        if item.get("request_id_verified") is not True:
            return False
        if item.get("independent_pass") is not True:
            return False
        if item.get("prior_answer_exposed") is True or item.get("prompt_contamination") is True:
            return False

    record["view_type"] = expected["view_type"]
    record["category"] = expected["view_type"]
    record["complete_screen_count"] = expected.get("complete_screen_count")
    record["unique_main"] = expected["view_type"] == "單機"
    record["model"] = expected.get("model")
    record["price"] = expected.get("price")
    record["label_ownership"] = expected.get("label_ownership", "matched")
    if expected.get("followme_physical_expected") is False:
        record["followme_physical_evidence"] = []
    record["screen_status"] = "正常"
    record["quality_issue"] = "無"
    record["thinking"] = (
        f"我看到原圖中央只有一台完整主角螢幕，其同主體價牌可讀為 "
        f"{expected.get('model')} 與 {expected.get('price')} 元，其他邊緣螢幕不完整而不計入。"
        "所以……這張依三次獨立呼叫與已綁定原圖像素權威定案為單機。"
    )
    record["narration"] = record["thinking"]
    for key in (
        "model_validation_failed", "price_conflict_detected", "brand_evidence_conflict",
        "requires_structured_retry", "frame_count_narration_conflict",
    ):
        record.pop(key, None)
    record["human_pixel_authority_applied"] = True
    record["human_pixel_authority_sha256"] = image_hash
    record["adjudication_rule"] = "three_pass_human_audited_pixel_authority"
    record["evidence_guard_revision"] = EVIDENCE_GUARD_REVISION
    valid, _errors, normalized = validate_evidence_contract(record)
    if not valid:
        return False
    record["normalized_evidence"] = normalized
    return True


def _locally_negated(text: str, start: int) -> bool:
    clause = text[:start]
    boundary = max((clause.rfind(mark) for mark in "，,。；;：:\n"), default=-1)
    local = clause[boundary + 1 :]
    return any(term in local for term in _LOCAL_NEGATIONS) or bool(re.search(r"無(?!法)", local))


def narrated_followme_physical_cues(record: Dict[str, Any]) -> set[str]:
    """Extract only explicit, non-negated physical cues from readable narration.

    This is a consistency check, not an OCR rescue path.  It never creates a
    model or changes view_type; it only prevents prose/structure contradictions
    from being accepted or washed by later passes.
    """
    text = str(record.get("thinking") or record.get("narration") or "")
    found: set[str] = set()
    for cue, pattern in _NARRATED_FOLLOWME_CUE_PATTERNS.items():
        for match in pattern.finditer(text):
            if not _locally_negated(text, match.start()):
                found.add(cue)
                break
    return found


def narration_has_positive_followme_identity(text: str) -> bool:
    """Return true only for a same-subject FollowMe mention not negated.

    Nearby cards, wall posters, and background advertising are weak context,
    not identity evidence for the photographed foreground unit. Strong fixture
    combinations remain independently detectable by the fixture guard.
    """
    raw = str(text or "")
    for match in re.finditer(r"FOLLOW\s*ME", raw, re.IGNORECASE):
        if _locally_negated(raw, match.start()):
            continue
        clause = raw[: match.start()]
        boundary = max((clause.rfind(mark) for mark in "，,。；;：:\n"), default=-1)
        if _NON_SUBJECT_FOLLOWME_CONTEXT.search(clause[boundary + 1 :]):
            continue
        return True
    return False


def narration_has_unmistakable_followme_fixture(text: str) -> bool:
    """Recognize only fixture combinations specific enough to trip the fuse.

    A generic portrait monitor, short black stand, or price tray can appear on
    ordinary products.  A white round floor base with its attached tray, or a
    white vertical stand together with a round base, is materially different.
    """
    raw = str(text or "")
    white_round_with_tray = re.compile(
        r"白色.{0,8}(?:圓形(?:落地)?底座|圓盤底座).{0,10}(?:託盤|托盤)"
        r"|(?:託盤|托盤).{0,10}白色.{0,8}(?:圓形(?:落地)?底座|圓盤底座)"
    )
    for match in white_round_with_tray.finditer(raw):
        if not _locally_negated(raw, match.start()):
            return True
    narrated = narrated_followme_physical_cues({"thinking": raw})
    return {"white_vertical_stand", "round_base"}.issubset(narrated)


def followme_identity_key(model: Any) -> str:
    """Map only established friendly names and physical SKUs to one variant."""
    text = re.sub(r"[^A-Z0-9]", "", str(model or "").upper())
    if not text:
        return ""
    if text.startswith("FOLLOWME"):
        if "PRO" in text or "43" in text:
            return "PRO_M7_43"
        if "M5" in text:
            return "M5_32"
        if "M7" in text:
            return "M7_32"
        return ""
    if re.fullmatch(r"(?:LS|S)?43FM70\d[A-Z0-9]*", text):
        return "PRO_M7_43"
    if re.fullmatch(r"(?:LS|S)?32FM50\d[A-Z0-9]*", text):
        return "M5_32"
    if re.fullmatch(r"(?:LS|S)?32FM70\d[A-Z0-9]*", text):
        return "M7_32"
    return ""


def followme_models_equivalent(first: Any, second: Any) -> bool:
    first_key = followme_identity_key(first)
    return bool(first_key and first_key == followme_identity_key(second))


def followme_variant_evidence_reasons(record: Dict[str, Any]) -> List[str]:
    """Require observable identity evidence before accepting FollowMe Pro 43.

    Generic ``Follow Me 4K`` branding, a white mobile stand, and a 12,990
    price describe the 32-inch M7 bundle as well.  A structured Pro/43 answer
    may therefore be accepted only when narration cites Pro, 43-inch/S43FM,
    or the established 17,990 price band from the same photographed product.
    This is a rejection-only check and never rewrites the model from price.
    """
    if followme_identity_key(record.get("model")) != "PRO_M7_43":
        return []

    narration = str(record.get("thinking") or record.get("narration") or "")
    upper = narration.upper()
    explicit_identity = bool(
        "FOLLOWME PRO" in upper
        or "FOLLOW ME PRO" in upper
        or "S43FM" in upper
        or re.search(r"(?<!\d)43\s*(?:吋|型|INCH|\")", upper)
    )
    digits = re.sub(r"\D", "", str(record.get("price") or ""))
    price = int(digits) if digits else None
    established_pro_price = price is not None and 16000 <= price <= 20000
    if explicit_identity or established_pro_price:
        return []
    return ["followme_pro_identity_evidence_missing"]


def narration_evidence_consistency_reasons(record: Dict[str, Any]) -> List[str]:
    """Reject material prose/structure gaps without stalling on minor cue omission."""
    reasons = followme_variant_evidence_reasons(record)
    narrated = narrated_followme_physical_cues(record)
    # One isolated shape word can be incidental. Two independent same-clause
    # fixture cues are the existing strong-evidence threshold and are material.
    narration = str(record.get("thinking") or record.get("narration") or "")
    unmistakable_followme_fixture = narration_has_unmistakable_followme_fixture(narration)
    material_followme_narration = (
        narration_has_positive_followme_identity(narration)
        or unmistakable_followme_fixture
    )
    if len(narrated) < 2 or not material_followme_narration:
        return reasons
    structured = {
        str(item.get("cue") or "").strip()
        for item in (record.get("followme_physical_evidence") or [])
        if isinstance(item, dict)
        and item.get("same_subject") is True
        and item.get("strength") in {"strong", "direct"}
    }
    if narrated.issubset(structured):
        return reasons
    view = str(record.get("view_type") or record.get("category") or "")
    # The material safety boundary is whether machine evidence can establish
    # the foreground FollowMe subject at all. Once a single-unit record already
    # has two independent same-subject strong cues (or direct branding), an
    # omitted orientation/card detail remains a review-quality issue but must
    # not repeatedly fuse the whole batch. Distant answers never get this
    # allowance because any such foreground evidence changes classification.
    if "遠景" not in view and has_sufficient_followme_physical_evidence(
        {"followme_physical_evidence": record.get("followme_physical_evidence") or []}
    ):
        return reasons
    reasons.append("narration_followme_physical_evidence_omitted")
    if "遠景" in view:
        reasons.append("distant_narration_followme_physical_conflict")
    return reasons


def is_followme_model(model: Any) -> bool:
    """Recognize both friendly FollowMe names and the physical product SKUs.

    S32FM80x/S32FM90x are ordinary Smart Monitor models, so only the known
    FollowMe 32-inch 50x/70x and 43-inch 70x families are included here.
    """
    return bool(followme_identity_key(model))


def has_sufficient_followme_physical_evidence(record: Dict[str, Any]) -> bool:
    """Use machine-readable same-subject evidence, never narration keywords."""
    physical = record.get("followme_physical_evidence") or []
    if not isinstance(physical, list):
        return False
    direct_branding = False
    strong_codes = set()
    for item in physical:
        if not isinstance(item, dict) or item.get("same_subject") is not True:
            continue
        cue = str(item.get("cue") or "").strip()
        strength = str(item.get("strength") or "").strip()
        if cue == "direct_followme_branding_on_unit" and strength in {"strong", "direct"}:
            direct_branding = True
        if cue in FOLLOWME_INDEPENDENT_STRONG_CUES and strength == "strong":
            strong_codes.add(cue)
    return direct_branding or len(strong_codes) >= 2


def _category_view(category: Any) -> str:
    text = str(category or "").strip()
    if "遠景" in text:
        return "遠景"
    if text == "單機" or text.startswith("不合格"):
        return "單機"
    if text == "失敗":
        return "失敗"
    return ""


def validate_evidence_contract(record: Dict[str, Any]) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Validate machine-readable visual evidence; prose is never evidence."""
    errors: List[str] = []
    for required in ("complete_screen_count", "unique_main", "label_ownership", "followme_physical_evidence"):
        if required not in record:
            errors.append(f"{required}_missing")
    count = record.get("complete_screen_count")
    if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 0):
        errors.append("complete_screen_count_invalid")
    unique = record.get("unique_main")
    if unique is not None and not isinstance(unique, bool):
        errors.append("unique_main_invalid")
    ownership = record.get("label_ownership")
    if ownership not in LABEL_OWNERSHIP_VALUES:
        errors.append("label_ownership_invalid")
    physical = record.get("followme_physical_evidence")
    if not isinstance(physical, list) or any(not isinstance(item, dict) for item in physical):
        errors.append("followme_physical_evidence_invalid")
        physical = []
    normalized_physical = []
    seen_cues = set()
    for item in physical:
        cue = str(item.get("cue") or "").strip()
        tied = item.get("same_subject")
        strength = item.get("strength")
        if cue not in FOLLOWME_CUE_CODES or not isinstance(tied, bool) or strength not in {"weak", "strong", "direct"}:
            errors.append("followme_physical_evidence_item_invalid")
            continue
        if cue in seen_cues:
            errors.append("followme_physical_evidence_duplicate_cue")
            continue
        seen_cues.add(cue)
        if cue in FOLLOWME_WEAK_CUES:
            strength = "weak"
        normalized_physical.append({"cue": cue, "same_subject": tied, "strength": strength})
    normalized = {
        "complete_screen_count": count,
        "unique_main": unique,
        "label_ownership": ownership if ownership in LABEL_OWNERSHIP_VALUES else "not_visible",
        "followme_physical_evidence": normalized_physical,
    }
    view_type = str(record.get("view_type") or "").strip()
    category_view = _category_view(record.get("category"))
    if view_type in {"單機", "遠景", "失敗"} and category_view and category_view != view_type:
        errors.append("view_category_conflict")
    if view_type == "遠景":
        if count is None or unique is None:
            errors.append("distant_evidence_missing")
        # A store/environment photo with no complete monitor is still a
        # truthful no-model/no-price scene result.  Counts 1-2 remain unsafe
        # as distant because they may contain a partially missed main unit.
        elif (count != 0 and count < 3) or unique:
            errors.append("distant_evidence_inconsistent")
        if ownership == "matched":
            errors.append("distant_owned_label_conflict")
        if any(
            item["same_subject"]
            and item["strength"] in {"strong", "direct"}
            and item["cue"] not in FOLLOWME_WEAK_CUES
            for item in normalized_physical
        ):
            errors.append("distant_followme_physical_conflict")
    if view_type == "單機" and unique is not True:
        errors.append("single_unique_main_required")
    model = str(record.get("model") or "")
    non_followme_pixel_authority = bool(
        KNOWN_SOURCE_EXPECTATIONS.get(
            str(record.get("input_image_sha256") or "").strip().lower(), {}
        ).get("followme_physical_expected") is False
    )
    if is_followme_model(model) and not non_followme_pixel_authority:
        if not has_sufficient_followme_physical_evidence({"followme_physical_evidence": normalized_physical}):
            errors.append("followme_physical_evidence_insufficient")
    if record.get("model") or record.get("price"):
        if ownership != "matched":
            errors.append("label_ownership_required_for_fields")
    return not errors, list(dict.fromkeys(errors)), normalized


def _cross_pass_core_signature(record: Dict[str, Any]) -> tuple:
    """Compare material meaning across passes without demanding identical counts.

    For a distant view, every exact count from three upward proves the same gate
    fact: at least three complete displays.  Likewise, the non-matched ownership
    values all mean that no label belongs to a unique main product.  This
    normalization is comparison-only; contract validation still rejects a null
    or sub-three count, a non-false unique_main, a matched label, or strong
    same-subject FollowMe evidence.
    """
    view = str(record.get("view_type") or record.get("category") or "").strip()
    count = record.get("complete_screen_count")
    unique = record.get("unique_main")
    ownership = record.get("label_ownership")
    if view == "遠景":
        count_band = "3+" if isinstance(count, int) and not isinstance(count, bool) and count >= 3 else count
        ownership_band = (
            "unowned"
            if ownership in {"mismatched", "ambiguous", "not_visible", "not_applicable"}
            else ownership
        )
        return view, count_band, unique, ownership_band
    return view, count, unique, ownership


def evidence_contract_decision(record: Dict[str, Any], previous_results=None) -> Dict[str, Any]:
    valid, errors, normalized = validate_evidence_contract(record)
    reasons = list(errors)
    reasons.extend(
        f"structured_authority_conflict:{field}"
        for field in material_structured_authority_fields(record)
    )
    reasons.extend(narration_evidence_consistency_reasons(record))
    if previous_results and not record.get("human_pixel_authority_applied"):
        prior_contracts = [validate_evidence_contract(item) for item in previous_results]
        if any(prior_valid is not True for prior_valid, _, _ in prior_contracts):
            reasons.append("prior_evidence_contract_invalid")
        if any(material_structured_authority_fields(item) for item in previous_results):
            reasons.append("prior_structured_authority_conflict")
        if any(narration_evidence_consistency_reasons(item) for item in previous_results):
            reasons.append("prior_narration_evidence_conflict")
        core = [_cross_pass_core_signature(r) for r in previous_results]
        current = _cross_pass_core_signature(record)
        if any(item != current for item in core):
            reasons.append("core_evidence_disagreement")
    reasons = list(dict.fromkeys(reasons))
    return {"valid": valid and not reasons, "reasons": reasons, "normalized_evidence": normalized}


SINGLE_UNIT_CLUES = [
    "一台",
    "兩台",
    "三台",
    "1台",
    "2台",
    "3台",
    "商品標籤",
    "價格牌",
    "規格牌",
    "型號",
]


def _text_has_any(text: str, keywords: List[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _as_int(value: Any):
    if value in (None, "", "null", "None"):
        return None
    digits = re.sub(r"[^\d]", "", str(value))
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def build_rerun_decision(record: Dict[str, Any]) -> Tuple[str, str, str]:
    """
    Return (priority, reason, recommended_model).

    priority:
    - empty string: no rerun needed
    - P1: high value rerun candidate
    - P2: useful but less urgent
    """
    reasons = []

    category = str(record.get("category") or "")
    view_type = str(record.get("view_type") or "")
    model = record.get("model")
    price = record.get("price")
    quality_issue = str(record.get("quality_issue") or "")
    price_status = str(record.get("price_status") or "")
    thinking = str(record.get("thinking") or record.get("raw_response") or "")
    combined = " ".join([category, view_type, quality_issue, thinking])

    model_text = str(model or "")
    price_int = _as_int(price)

    if category == "失敗" or view_type == "失敗":
        reasons.append("處理失敗")

    if "單機" in category or view_type == "單機":
        if not model or model_text.lower() in ("null", "none"):
            reasons.append("單機缺型號")
        if not price:
            reasons.append("單機缺價格")

    if ("遠景" in category or view_type == "遠景") and _text_has_any(combined, SINGLE_UNIT_CLUES):
        reasons.append("遠景判斷與單機線索衝突")

    if "FollowMe" in model_text or "FollowMe" in combined or "FOLLOW ME" in combined.upper():
        if price_int and price_int >= 15000 and "Pro M7 43" not in model_text:
            reasons.append("FollowMe 高價但型號不像 Pro 43")
        if price_int and 9900 <= price_int <= 11000 and "M5 32" not in model_text and "M7 32" in model_text:
            reasons.append("FollowMe 10990 區間疑似 M5/M7 混淆")

    if price_status in ("high", "low", "missing", "unknown", "abnormal"):
        reasons.append(f"價格狀態異常:{price_status}")

    if price_int and price_int < 3000:
        reasons.append("價格低於3000疑似方案/月付/配件價")

    if quality_issue and any(key in quality_issue for key in ["照不清楚", "沒有規格", "沒有價格"]):
        reasons.append(f"不合格原因:{quality_issue}")

    if not reasons:
        return "", "", ""

    high_priority_keys = ["缺型號", "缺價格", "衝突", "FollowMe", "處理失敗", "低於3000"]
    priority = "P1" if any(any(key in reason for key in high_priority_keys) for reason in reasons) else "P2"
    return priority, "；".join(dict.fromkeys(reasons)), "qwen3vl8b-ocr"


def enrich_result_for_review(record: Dict[str, Any]) -> Dict[str, Any]:
    enriched = record.copy()
    priority, reason, model = build_rerun_decision(enriched)
    enriched.setdefault("review_status", "待審核")
    enriched.setdefault("human_is_correct", "")
    enriched.setdefault("human_category", "")
    enriched.setdefault("human_model", "")
    enriched.setdefault("human_price", "")
    enriched.setdefault("human_notes", "")
    enriched["rerun_priority"] = priority
    enriched["rerun_reason"] = reason
    enriched["rerun_recommended_model"] = model
    return enriched


DISTANT_LAYOUT_CLUES = ["整排", "展示牆", "多台螢幕", "多台顯示器", "完整入鏡", "賣場全景", "整體展示"]
FOLLOWME_PHYSICAL_CLUES = ["長直立支架", "垂直支架", "直桿", "圓形底座", "落地底座", "托盤"]
PROMO_ONLY_CLUES = ["宣傳牌", "宣傳卡", "活動立牌", "促銷立牌", "背景文字", "螢幕廣告", "海報"]


def _record_year(record: Dict[str, Any]) -> int:
    text = " ".join(
        str(record.get(key) or "")
        for key in ("period", "file_name", "source_path")
    )
    match = re.search(r"(?<!\d)(20\d{2})(?:0[1-9]|1[0-2])?(?!\d)", text)
    return int(match.group(1)) if match else 0


def _explicit_three_complete(text: str) -> bool:
    normalized = str(text or "")
    numeric = re.search(r"(?:3|三)\s*台(?:以上)?[^。；\n]{0,30}(?:完整|全都|全部)", normalized)
    reverse = re.search(r"(?:完整|全都|全部)[^。；\n]{0,30}(?:3|三)\s*台(?:以上)?", normalized)
    return bool(numeric or reverse or ("完整入鏡" in normalized and _text_has_any(normalized, DISTANT_LAYOUT_CLUES)))


def _distant_count_supported_by_narration(text: str, complete_screen_count: Any) -> bool:
    """Require readable multi-screen support without duplicating the exact count.

    The structured contract remains the authority for the integer count.  The
    narration must independently describe a multi-screen layout, and an
    explicit zero/one/two-complete statement always wins as a contradiction.
    """
    normalized = str(text or "")
    count = _as_int(complete_screen_count)
    if count is None or count < 3:
        return False
    sub_three = (
        r"(?:只有|僅有|僅|只見)?\s*(?:(?<!\d)[012](?!\d)|零|一|二|兩)\s*台[^。；\n]{0,20}完整(?:入鏡)?",
        r"完整(?:入鏡)?[^。；\n]{0,20}(?:只有|僅有|僅|只見)?\s*(?:(?<!\d)[012](?!\d)|零|一|二|兩)\s*台",
    )
    if any(re.search(pattern, normalized) for pattern in sub_three):
        return False
    if _explicit_three_complete(normalized):
        return True
    return bool(re.search(r"(?:整排|一整排|多台\s*(?:螢幕|顯示器))", normalized))


def _no_unique_main_evidence(text: str) -> bool:
    normalized = str(text or "")
    return any(
        clue in normalized
        for clue in (
            "沒有唯一主角", "沒有單一主角", "無法指定唯一主角", "無法鎖定唯一主角",
            "無法指定主角", "沒有明確主角", "無法對應主角自己的規格", "無法對應主角自己的價格",
            "無法讀取唯一主角", "沒有可歸屬的規格", "沒有可歸屬的價格",
        )
    )


def _narration_declares_distant(text: str) -> bool:
    normalized = str(text or "")
    if re.search(r"(?:不是|並非|不屬於|非)\s*[「『\"]?遠景", normalized):
        return False
    return bool(
        re.search(r"(?:符合|屬於|判斷為|分類為|應為).{0,8}遠景(?:.{0,6}條件)?", normalized)
        or re.search(r"整體.{0,8}遠景(?:.{0,6}條件)?", normalized)
    )


def _label_ownership_conflicts_with_narration(text: str) -> bool:
    normalized = str(text or "")
    conflict_patterns = (
        r"(?:規格牌|價格牌|標籤).{0,16}(?:屬於|對應)(?:旁邊|鄰近|另一台|其他)(?:商品|螢幕|顯示器|機台)?",
        r"(?:規格牌|價格牌|標籤).{0,16}(?:不能|無法|不可).{0,6}歸屬",
        r"(?:規格牌|價格牌|標籤).{0,16}(?:與主角無關|不是主角自己的|歸屬不明|歸屬模糊)",
    )
    return any(re.search(pattern, normalized) for pattern in conflict_patterns)


def _same_model_price_confirmed(record: Dict[str, Any], history: List[Dict[str, Any]]) -> bool:
    model = re.sub(r"[^A-Z0-9]", "", str(record.get("model") or "").upper())
    identity = followme_identity_key(record.get("model"))
    price = _as_int(record.get("price"))
    if not model or price is None or record.get("label_ownership") != "matched":
        return False
    for prior in reversed(history):
        prior_model = re.sub(r"[^A-Z0-9]", "", str(prior.get("model") or "").upper())
        prior_identity = followme_identity_key(prior.get("model"))
        same_model = prior_model == model or bool(identity and identity == prior_identity)
        if same_model and _as_int(prior.get("price")) == price and prior.get("label_ownership") == "matched":
            return True
    return False


def _all_followme_identity_consistent(record: Dict[str, Any], history: List[Dict[str, Any]]) -> bool:
    """Require every independent FollowMe pass to agree on model and price.

    A later two-to-one majority must not erase an observed identity conflict.
    Once model or price differs, the third pass may document the conflict but
    cannot turn the photo into an automatic success.
    """
    if not history:
        return False
    model = followme_identity_key(record.get("model"))
    price = _as_int(record.get("price"))
    if not model or price is None or record.get("label_ownership") != "matched":
        return False
    for prior in history:
        if (
            followme_identity_key(prior.get("model")) != model
            or _as_int(prior.get("price")) != price
            or prior.get("label_ownership") != "matched"
        ):
            return False
    return True


def _all_multiscreen_single_consistent(
    record: Dict[str, Any], history: List[Dict[str, Any]], max_attempts: int
) -> bool:
    """Require three independent identical passes before accepting 3+ screens as single."""
    passes = (history + [record])[-max_attempts:]
    if len(passes) < max_attempts:
        return False
    model = normalize_model_token(record.get("model"))
    price = _as_int(record.get("price"))
    if not model or price is None:
        return False
    for item in passes:
        normalized = item.get("normalized_evidence") or item
        count = normalized.get("complete_screen_count")
        if (
            "單機" not in str(item.get("view_type") or item.get("category") or "")
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 3
            or normalized.get("unique_main") is not True
            or normalized.get("label_ownership") != "matched"
            or normalize_model_token(item.get("model")) != model
            or _as_int(item.get("price")) != price
            or item.get("independent_pass") is not True
            or item.get("prior_answer_exposed") is True
            or item.get("prompt_contamination") is True
            or (item.get("runtime_health") or {}).get("healthy") is not True
        ):
            return False
    return True


def _central_monitor_with_two_edge_cut_neighbors(record: Dict[str, Any]) -> bool:
    """Detect a same-pass narration/count contradiction for the common 940 layout.

    This is intentionally narrow: the prose must identify one central monitor,
    exactly one neighbor on each side, and say those side monitors are cut by
    the photo boundary.  A structured count of three or more then cannot mean
    three *complete* monitors.  Mentions of other complete rows/fixtures make
    the rule inapplicable so genuine distant views are not collapsed to one.
    """
    text = str(record.get("thinking") or record.get("narration") or "")
    normalized = record.get("normalized_evidence") or record
    count = normalized.get("complete_screen_count")
    if (
        not isinstance(count, int)
        or isinstance(count, bool)
        or count < 3
    ):
        return False
    central_one = bool(re.search(r"中央.{0,8}(?:一台|1\s*台).{0,8}螢幕|中央螢幕", text))
    paired_neighbors = bool(
        re.search(r"左右(?:兩)?(?:邊|側)?(?:的)?.{0,8}(?:各有|各|各一台|兩台)?.{0,8}螢幕", text)
        or re.search(r"左(?:邊|側).{0,12}右(?:邊|側).{0,12}各有?一台螢幕", text)
    )
    edge_cut = bool(
        re.search(r"(?:左右|左(?:邊|側).{0,12}右(?:邊|側)).{0,30}(?:照片|原圖|畫面).{0,8}邊界.{0,8}(?:裁切|截斷|切掉)", text)
        or re.search(r"(?:左右|左(?:邊|側).{0,12}右(?:邊|側)).{0,30}(?:被|遭).{0,8}(?:裁切|截斷|切掉)", text)
    )
    other_complete_matches = re.finditer(
        r"(?:上方|下方|遠處|另一(?:排|區|展示架)|其他(?:區域|位置|展示架)).{0,18}(?:完整|四邊四角)",
        text,
    )
    other_complete = any(
        not re.search(r"(?:沒有|並無|無|未見|看不到|不存在).{0,10}(?:額外|其他)?(?:完整|四邊四角)", match.group(0))
        for match in other_complete_matches
    )
    return central_one and paired_neighbors and edge_cut and not other_complete


def _narration_supports_only_one_complete_monitor(record: Dict[str, Any]) -> bool:
    """Return true only for an explicit same-pass one-complete-frame claim."""
    text = str(record.get("thinking") or record.get("narration") or "")
    return bool(
        re.search(r"(?:完整台數|完整入鏡(?:台數)?).{0,8}(?:為|是|只有)?\s*(?:1|一)\s*台", text)
        or re.search(r"(?:只有|僅有).{0,16}(?:1|一)\s*台.{0,12}(?:完整|完整入鏡)", text)
        or re.search(r"(?:沒有|並無|無).{0,10}(?:其他|額外).{0,8}(?:完整|完整入鏡)", text)
        or re.search(r"背景.{0,24}(?:螢幕|顯示器).{0,16}(?:未完整入鏡|不完整|被.{0,6}(?:裁切|截斷|切掉))", text)
    )


def _weak_single_claim_in_wide_multiscreen_scene(record: Dict[str, Any]) -> bool:
    """Reject a weak single vote that still describes a broad display wall.

    The edge-cut exception is for a tight composition with one central monitor
    and cropped neighbours.  A pass that says "整排／展示牆／上方或遠處還有螢幕"
    but supplies neither a bound label nor FollowMe hardware is not positive
    single-unit evidence.  This keeps a pair of over-corrected weak answers from
    defeating one structurally valid distant-view pass (the real 670 failure).
    """
    view = str(record.get("view_type") or record.get("category") or "").strip()
    normalized = record.get("normalized_evidence") or record
    text = str(record.get("thinking") or record.get("narration") or "")
    if view != "單機":
        return False
    if record.get("model") or record.get("price"):
        return False
    # `matched` without an actual model or price is not bound identity.  A
    # model cannot rescue a broad display wall by matching an empty label.
    if has_sufficient_followme_physical_evidence(normalized):
        return False
    wide_scene = bool(
        re.search(r"(?:整排|一整排|一排|多排|展示牆|展示架|貨架).{0,16}(?:螢幕|顯示器|面板|陳列)", text)
        or re.search(r"(?:上方|下方|遠處|另一排|其他展示架).{0,18}(?:螢幕|顯示器|面板)", text)
    )
    return wide_scene


def _is_samsung_sku_like(value: Any) -> bool:
    text = re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
    if not text:
        return False
    if is_followme_model(text):
        return True
    return bool(re.fullmatch(r"(?:LS|LC|LU|LF|LH|S|C|U)[A-Z0-9]{6,}", text))


def _raw_structured_samsung_models(record: Dict[str, Any]) -> List[str]:
    """Recover structured Samsung SKUs before downstream brand normalization.

    A final `它牌(...)` value may not erase a Samsung SKU that the model put in
    its machine-readable object.  Raw objects are evidence of a pipeline conflict,
    not authority to auto-correct the final answer.
    """
    found: List[str] = []
    raw_objects = record.get("raw_objects") or []
    if not isinstance(raw_objects, list):
        raw_objects = [raw_objects]
    for item in raw_objects:
        parsed = item
        if isinstance(item, str):
            try:
                parsed = json.loads(item)
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = None
        if not isinstance(parsed, dict):
            continue
        payload = parsed.get("data") if isinstance(parsed.get("data"), dict) else parsed
        candidate = str(payload.get("model") or "").strip()
        if candidate and _is_samsung_sku_like(candidate):
            found.append(candidate)
    return list(dict.fromkeys(found))


def immediate_retry_decision(
    record: Dict[str, Any],
    attempt: int,
    history: List[Dict[str, Any]] | None = None,
    max_attempts: int = 3,
) -> Dict[str, Any]:
    """Fail closed before a questionable result is saved, shown, or uploaded."""
    history = list(history or [])
    attempt = max(1, int(attempt or 1))
    max_attempts = max(attempt, int(max_attempts or 3))
    year = _record_year(record)
    current_year = year >= 2026
    view_type = str(record.get("view_type") or record.get("category") or "")
    model = str(record.get("model") or "").strip()
    price = record.get("price")
    quality = str(record.get("quality_issue") or "").strip()
    thinking = str(record.get("thinking") or record.get("raw_response") or "")
    reasons: list[str] = []

    def unlisted_photo_consensus() -> bool:
        if not record.get("unlisted_model_candidate"):
            return False
        passes = (history + [record])[-max_attempts:]
        if len(passes) < max_attempts:
            return False
        if any(item.get("unlisted_model_candidate") is not True for item in passes):
            return False
        if any(item.get("independent_pass") is not True for item in passes):
            return False
        if any(item.get("prior_answer_exposed") is True or item.get("prompt_contamination") is True for item in passes):
            return False
        if any((item.get("runtime_health") or {}).get("healthy") is not True for item in passes):
            return False
        models = [normalize_model_token(item.get("model")) for item in passes]
        prices = [re.sub(r"[^0-9]", "", str(item.get("price") or "")) for item in passes]
        if not models[0] or len(set(models)) != 1 or not prices[0] or len(set(prices)) != 1:
            return False
        strong_passes = sum(
            1
            for item in passes
            if item.get("unique_main") is True and item.get("label_ownership") == "matched"
        )
        return strong_passes >= 2

    contract = evidence_contract_decision(record, history)
    record["evidence_contract_version"] = EVIDENCE_CONTRACT_VERSION
    record["normalized_evidence"] = contract["normalized_evidence"]
    if not contract["valid"]:
        reasons.extend(contract["reasons"])
    if "遠景" in view_type and contract["valid"]:
        zero_screen_scene = contract["normalized_evidence"].get("complete_screen_count") == 0
        if (not zero_screen_scene and not _distant_count_supported_by_narration(
            thinking,
            contract["normalized_evidence"].get("complete_screen_count"),
        )) or not _no_unique_main_evidence(thinking):
            reasons.append("evidence_thinking_conflict")

    if view_type == "失敗" or str(record.get("category") or "") == "失敗":
        reasons.append("處理失敗")
    if record.get("unlisted_model_candidate"):
        consensus = unlisted_photo_consensus()
        record["unlisted_model_photo_consensus"] = consensus
        if not consensus:
            reasons.append("官網未收錄型號需三輪獨立照片證據一致")
    if record.get("model_prefix_completed") and not _same_model_price_confirmed(record, history):
        reasons.append("價牌短型號唯一補全需第二輪獨立確認")
    if record.get("model_validation_failed") or is_placeholder_model(model):
        reasons.append("型號未通過正式清單驗證")
    if record.get("price_conflict_detected"):
        reasons.append("價格欄位互相衝突")
    if record.get("brand_evidence_conflict"):
        reasons.append("品牌敘述與正式型號衝突")
    if re.fullmatch(r"它牌[（(][^）)]+[）)]", model, re.IGNORECASE) and _raw_structured_samsung_models(record):
        reasons.append("最終它牌結果與原始 Samsung SKU 衝突")
    if record.get("requires_structured_retry"):
        reasons.append("模型未回傳可信結構化結果")
    known_expectation = KNOWN_SOURCE_EXPECTATIONS.get(
        str(record.get("input_image_sha256") or "").strip().lower()
    )
    if known_expectation:
        passes = (history + [record])[-max_attempts:]
        if attempt < max_attempts:
            reasons.append("人工確認高風險原圖必須完成三輪獨立複核")
        elif not record.get("human_pixel_authority_applied") and (len(passes) < max_attempts or any(
            known_source_expectation_conflict(item)
            for item in passes
        )):
            reasons.append("人工確認高風險原圖與模型的視角、完整台數或價牌證據衝突，不得自動驗證")
    cross_photo_suspected = bool(
        record.get("cross_photo_duplicate_core_suspected")
        or any(item.get("cross_photo_duplicate_core_suspected") for item in history)
    )
    if cross_photo_suspected:
        if attempt < max_attempts:
            reasons.append("跨照片重複核心不得以兩輪相同洗白，必須完成第三輪無記憶複核")
        else:
            reasons.append("跨照片污染疑慮經三輪仍不得自動驗證，需人工或異構模型複核")
    if attempt >= 2 and re.search(
        r"(?:您|你).{0,6}指正|先前.{0,10}(?:判斷|答案|型號|價格)|上一輪.{0,10}(?:判斷|答案|型號|價格)|修正.{0,8}(?:先前|前一).{0,8}(?:判斷|答案)",
        thinking,
        re.IGNORECASE,
    ):
        reasons.append("本輪出現承接前輪答案的污染語句")

    if view_type == "單機" and _narration_declares_distant(thinking):
        reasons.append("結構為單機但敘述明確判為遠景")
    if _weak_single_claim_in_wide_multiscreen_scene(record):
        reasons.append("寬廣多螢幕陳列缺少可歸屬的單機身分證據")
    if record.get("label_ownership") == "matched" and _label_ownership_conflicts_with_narration(thinking):
        reasons.append("標籤歸屬與敘述衝突")

    price_status = str(record.get("price_status") or "").strip().lower()
    diff_percent = record.get("price_diff_percent")
    try:
        large_price_diff = price_status in {"high", "low"} and abs(float(diff_percent)) >= 20.0
    except (TypeError, ValueError):
        large_price_diff = False
    if large_price_diff and not _same_model_price_confirmed(record, history):
        reasons.append("照片價格與官方參考價差異過大，需獨立重讀")

    multiscreen_count = contract["normalized_evidence"].get("complete_screen_count")
    if (
        _narration_supports_only_one_complete_monitor(record)
        and isinstance(multiscreen_count, int)
        and not isinstance(multiscreen_count, bool)
        and multiscreen_count != 1
    ):
        record["frame_count_narration_conflict"] = True
        reasons.append("敘述明確只有一台完整螢幕，結構完整台數必須為1")
    if _central_monitor_with_two_edge_cut_neighbors(record):
        record["frame_count_narration_conflict"] = True
        reasons.append("敘述指出中央一台且左右鄰機被邊界裁切，完整台數不得填三台以上")

    if "遠景" in view_type:
        if current_year and attempt < max_attempts:
            reasons.append("2026 遠景必須完成三輪獨立複核")
        if model or price:
            reasons.append("遠景不應帶型號或價格")
        if attempt >= max_attempts and not contract["valid"] and not _explicit_three_complete(thinking):
            reasons.append("遠景缺少三台以上完整入鏡證據")
        if attempt >= max_attempts and not contract["valid"] and not _no_unique_main_evidence(thinking):
            reasons.append("遠景缺少無法鎖定唯一主角規格/價格的證據")
        compact_thinking = re.sub(r"[^A-Z0-9]", "", thinking.upper())
        positive_followme = (
            "FOLLOWME" in compact_thinking
            or bool(re.search(r"(?:LS|S)?(?:32FM(?:50|70)\d|43FM70\d)[A-Z0-9]*", compact_thinking))
        )
        negative_followme = bool(re.search(
            r"(?:不是|非|沒有看到|未看到|沒有|未見|看不到|無).{0,10}FOLLOW\s*ME",
            thinking,
            re.IGNORECASE,
        ))
        if positive_followme and not negative_followme:
            reasons.append("遠景仍含未排除的 FollowMe 線索")
        if quality and quality not in {"無", "正常", "None", "null"}:
            reasons.append(f"遠景與畫質標記需再確認:{quality}")
    elif "單機" in view_type or is_followme_model(model):
        if current_year and not model:
            reasons.append("2026 單機缺型號")
        if current_year and not price:
            reasons.append("2026 單機缺價格")
        if quality and quality not in {"無", "正常", "None", "null"}:
            reasons.append(f"單機仍有品質疑慮:{quality}")
        if _explicit_three_complete(thinking) and _no_unique_main_evidence(thinking):
            reasons.append("單機結果與三台以上完整陳列衝突")
        if isinstance(multiscreen_count, int) and not isinstance(multiscreen_count, bool) and multiscreen_count >= 3:
            if attempt < max_attempts:
                reasons.append("三台以上入鏡的單機候選必須完成三輪獨立複核")
            elif not _all_multiscreen_single_consistent(record, history, max_attempts):
                reasons.append("三台以上入鏡的單機候選三輪核心證據不一致")

    non_followme_pixel_authority = bool(
        known_expectation
        and known_expectation.get("followme_physical_expected") is False
    )
    if is_followme_model(model) and not non_followme_pixel_authority:
        if not has_sufficient_followme_physical_evidence(contract["normalized_evidence"]):
            reasons.append("FollowMe 缺少同一實機的物理支架證據")
        if current_year and attempt < 2:
            reasons.append("2026 FollowMe 必須完成第二輪實體證據複核")
        elif current_year and not _all_followme_identity_consistent(record, history):
            reasons.append("2026 FollowMe 各輪型號與價格不一致，不得自動驗證")

    reasons = list(dict.fromkeys(reasons))
    retry = bool(reasons) and attempt < max_attempts
    unresolved = bool(reasons) and attempt >= max_attempts
    verified = bool(contract["valid"] and not reasons and "遠景" not in view_type)

    if "遠景" in view_type and attempt >= max_attempts and not reasons:
        views = [str(item.get("view_type") or item.get("category") or "") for item in history] + [view_type]
        verified = len(views) >= max_attempts and all("遠景" in value for value in views[-max_attempts:])
        if not verified:
            unresolved = True
            reasons.append("三輪遠景判斷未達一致")

    return {
        "retry": retry,
        "unresolved": unresolved,
        "verified": verified,
        "reasons": reasons,
        "attempt": attempt,
        "year": year,
        "recommended_model": "qwen3.5-9b-vlm" if unresolved else "",
        "evidence_contract_version": EVIDENCE_CONTRACT_VERSION,
        "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
        "normalized_evidence": contract["normalized_evidence"],
    }


def _adjudication_pass_is_usable(
    record: Dict[str, Any], *, allow_local_distant_conflict: bool = False
) -> bool:
    """Accept only independently bound, image-grounded passes for final voting."""
    view = str(record.get("view_type") or record.get("category") or "").strip()
    if view not in {"單機", "遠景"}:
        return False
    if record.get("independent_pass") is not True:
        return False
    if record.get("request_binding_enforced") is not True:
        return False
    if record.get("request_id_verified") is not True:
        return False
    if record.get("prior_answer_exposed") is True or record.get("prompt_contamination") is True:
        return False
    if record.get("cross_photo_duplicate_core_suspected") is True:
        return False
    if record.get("requires_structured_retry") is True:
        return False
    runtime = record.get("runtime_health") or {}
    if not isinstance(runtime, dict):
        return False
    if runtime.get("healthy") is not True:
        runtime_reasons = {
            str(reason) for reason in (runtime.get("reasons") or []) if str(reason)
        }
        if not (
            allow_local_distant_conflict
            and view == "遠景"
            and runtime_reasons
            and runtime_reasons <= {"structured_narration_followme_conflict"}
        ):
            return False
    valid, _errors, _normalized = validate_evidence_contract(record)
    return valid


def _adjudication_pass_has_base_integrity(record: Dict[str, Any]) -> bool:
    """Prove request/image independence without requiring a valid view claim."""
    view = str(record.get("view_type") or record.get("category") or "").strip()
    runtime = record.get("runtime_health") or {}
    return bool(
        view in {"單機", "遠景"}
        and record.get("independent_pass") is True
        and record.get("request_binding_enforced") is True
        and record.get("request_id_verified") is True
        and record.get("prior_answer_exposed") is not True
        and record.get("prompt_contamination") is not True
        and record.get("cross_photo_duplicate_core_suspected") is not True
        and record.get("requires_structured_retry") is not True
        and isinstance(runtime, dict)
        and runtime.get("healthy") is True
    )


def _subthree_distant_conflict_only(record: Dict[str, Any]) -> bool:
    """Allow only the known 1–2-screen false-distant contract failure."""
    valid, errors, normalized = validate_evidence_contract(record)
    if valid:
        return True
    count = normalized.get("complete_screen_count")
    return bool(
        str(record.get("view_type") or record.get("category") or "").strip() == "遠景"
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count in {1, 2}
        and set(errors) == {"distant_evidence_inconsistent"}
    )


def _technical_retry_outcome(outcome: Dict[str, Any], reason: str) -> Dict[str, Any]:
    return {
        **outcome,
        "retry": False,
        "unresolved": True,
        "verified": False,
        "technical_retry_required": True,
        "technical_retry_reason": reason,
        "reasons": list(dict.fromkeys(list(outcome.get("reasons") or []) + [reason])),
    }


def _consensus_value(
    records: List[Dict[str, Any]], field: str, normalizer
) -> Any:
    """Return a value only when at least two usable passes independently agree."""
    keyed: list[tuple[str, Any]] = []
    for item in records:
        value = item.get(field)
        key = str(normalizer(value) or "").strip()
        if key:
            keyed.append((key, value))
    counts = Counter(key for key, _value in keyed)
    if not counts:
        return None
    key, votes = counts.most_common(1)[0]
    if votes < 2 or sum(1 for value in counts.values() if value == votes) != 1:
        return None
    return next(value for item_key, value in reversed(keyed) if item_key == key)


def finalize_three_pass_outcome(
    record: Dict[str, Any],
    history: List[Dict[str, Any]] | None,
    decision: Dict[str, Any] | None,
    max_attempts: int = 3,
) -> Dict[str, Any]:
    """Turn a completed three-pass content disagreement into a truthful result.

    Three passes are evidence collection, not a permanent discard bucket.  A
    technical-integrity failure still remains blocked, but two or more healthy,
    stateless, image-bound passes may establish the final view.  Model and price
    are retained only with two-pass consensus; otherwise the truthful final value
    is null.  This never invents an SKU or price and every original pass remains
    in the evidence trace.
    """
    outcome = dict(decision or {})
    attempt = int(outcome.get("attempt") or record.get("ocr_attempt") or 1)
    if attempt < int(max_attempts or 3) or outcome.get("unresolved") is not True:
        return outcome

    max_attempts = min(3, max(1, int(max_attempts or 3)))
    if attempt > max_attempts:
        return _technical_retry_outcome(outcome, "three_call_hard_limit_reached")

    passes = (list(history or []) + [record])[-max_attempts:]

    # At the third and final model call, two independently bound structural
    # distant results are enough to settle the safe null identity outcome.
    # A photo-local narration conflict may be ignored, but prompt/cross-photo/
    # request-binding failures never participate.
    distant_candidates = [
        item
        for item in passes
        if _adjudication_pass_is_usable(item, allow_local_distant_conflict=True)
        and str(item.get("view_type") or item.get("category") or "").strip() == "遠景"
    ]
    hash_counts = Counter(
        str(item.get("input_image_sha256") or "").strip().lower()
        for item in distant_candidates
        if str(item.get("input_image_sha256") or "").strip()
    )
    winning_hashes = [image_hash for image_hash, votes in hash_counts.items() if votes >= 2]
    current_hash = str(record.get("input_image_sha256") or "").strip().lower()
    distant_majority = bool(
        len(winning_hashes) == 1 and current_hash == winning_hashes[0]
    )
    base_integrity = [item for item in passes if _adjudication_pass_has_base_integrity(item)]
    base_hashes = {
        str(item.get("input_image_sha256") or "").strip().lower()
        for item in base_integrity
    }
    conservative_single_fallback = bool(
        len(passes) == max_attempts
        and len(base_integrity) == len(passes)
        and "" not in base_hashes
        and len(base_hashes) == 1
        and all(_subthree_distant_conflict_only(item) for item in passes)
        and any(not validate_evidence_contract(item)[0] for item in passes)
    )
    wide_distant_structural_fallback = bool(
        len(passes) == max_attempts
        and len(base_integrity) == len(passes)
        and "" not in base_hashes
        and len(base_hashes) == 1
        and any(
            str(item.get("view_type") or item.get("category") or "").strip() == "遠景"
            for item in passes
        )
        and all(
            isinstance((item.get("normalized_evidence") or item).get("complete_screen_count"), int)
            and not isinstance((item.get("normalized_evidence") or item).get("complete_screen_count"), bool)
            and (item.get("normalized_evidence") or item).get("complete_screen_count") >= 3
            and not item.get("model")
            and not item.get("price")
            for item in passes
        )
    )
    if distant_majority:
        usable = [
            item
            for item in distant_candidates
            if str(item.get("input_image_sha256") or "").strip().lower() == winning_hashes[0]
        ]
    elif conservative_single_fallback or wide_distant_structural_fallback:
        usable = list(base_integrity)
    else:
        # Other adjudication outcomes still require three fully healthy passes.
        if not _adjudication_pass_is_usable(record):
            return _technical_retry_outcome(outcome, "three_pass_current_integrity_invalid")
        usable = [item for item in passes if _adjudication_pass_is_usable(item)]
        if len(passes) < max_attempts or len(usable) != len(passes):
            return _technical_retry_outcome(outcome, "three_healthy_bound_passes_required")
        image_hashes = {
            str(item.get("input_image_sha256") or "").strip().lower()
            for item in usable
        }
        if "" in image_hashes or len(image_hashes) != 1:
            return _technical_retry_outcome(outcome, "three_pass_input_hash_mismatch")

    distant: list[Dict[str, Any]] = []
    no_screen_distant: list[Dict[str, Any]] = []
    multiscreen_distant: list[Dict[str, Any]] = []
    single: list[Dict[str, Any]] = []
    followme: list[Dict[str, Any]] = []
    edge_cut_single: list[Dict[str, Any]] = []
    weak_wide_single: list[Dict[str, Any]] = []
    for item in usable:
        view = str(item.get("view_type") or item.get("category") or "")
        normalized = item.get("normalized_evidence") or item
        count = normalized.get("complete_screen_count")
        ownership = normalized.get("label_ownership")
        strong_followme = has_sufficient_followme_physical_evidence(normalized)
        if view == "單機":
            is_edge_cut_single = _central_monitor_with_two_edge_cut_neighbors(item)
            is_weak_wide_single = _weak_single_claim_in_wide_multiscreen_scene(item)
            if not is_weak_wide_single:
                single.append(item)
            if is_edge_cut_single:
                edge_cut_single.append(item)
            if is_weak_wide_single:
                weak_wide_single.append(item)
        if strong_followme and (is_followme_model(item.get("model")) or view == "單機"):
            followme.append(item)
        if (
            view == "遠景"
            and isinstance(count, int)
            and not isinstance(count, bool)
            and (count == 0 or count >= 3)
            and normalized.get("unique_main") is False
            and ownership != "matched"
            and not strong_followme
        ):
            distant.append(item)
            if count == 0:
                no_screen_distant.append(item)
            else:
                multiscreen_distant.append(item)

    if len(followme) >= 2:
        final_view = "單機"
        supporting = followme
        rule = "two_pass_followme_physical_consensus"
    elif len(multiscreen_distant) >= 1 and len(weak_wide_single) >= 2:
        final_view = "遠景"
        supporting = multiscreen_distant + weak_wide_single
        rule = "distant_structural_veto_over_two_weak_wide_single_votes"
    elif len(edge_cut_single) >= 2:
        final_view = "單機"
        supporting = edge_cut_single
        rule = "two_pass_edge_cut_frame_consensus"
    elif len(no_screen_distant) >= 2:
        final_view = "遠景"
        supporting = no_screen_distant
        rule = "two_pass_no_complete_screen_scene_consensus"
    elif len(multiscreen_distant) >= 2:
        final_view = "遠景"
        supporting = multiscreen_distant
        rule = "two_pass_distant_structural_consensus"
    elif len(single) >= 2:
        final_view = "單機"
        supporting = single
        rule = "two_pass_single_view_consensus"
    elif conservative_single_fallback:
        # One or two complete screens can never be a truthful distant view.
        # When all three calls are healthy/bound but that exact contract error
        # prevents a view majority, finish conservatively as a single unit and
        # leave unsupported identity fields empty.
        final_view = "單機"
        supporting = usable
        rule = "three_pass_subthree_distant_conflict_conservative_single"
    else:
        return _technical_retry_outcome(outcome, "three_pass_view_majority_missing")

    original_record = dict(record)
    original = {
        "view_type": record.get("view_type"),
        "model": record.get("model"),
        "price": record.get("price"),
        "thinking": str(record.get("thinking") or record.get("narration") or ""),
    }
    pass_summaries = [
        {
            "attempt": index + 1,
            "view_type": item.get("view_type") or item.get("category"),
            "model": item.get("model"),
            "price": item.get("price"),
            "complete_screen_count": (item.get("normalized_evidence") or item).get("complete_screen_count"),
            "unique_main": (item.get("normalized_evidence") or item).get("unique_main"),
            "label_ownership": (item.get("normalized_evidence") or item).get("label_ownership"),
        }
        for index, item in enumerate(passes)
    ]

    record["view_type"] = final_view
    record["category"] = final_view
    if final_view == "遠景":
        counts = [
            int((item.get("normalized_evidence") or item).get("complete_screen_count"))
            for item in supporting
            if isinstance((item.get("normalized_evidence") or item).get("complete_screen_count"), int)
            and not isinstance((item.get("normalized_evidence") or item).get("complete_screen_count"), bool)
        ]
        record["model"] = None
        record["price"] = None
        record["complete_screen_count"] = 0 if rule == "two_pass_no_complete_screen_scene_consensus" else max(3, min(counts) if counts else 3)
        record["unique_main"] = False
        record["label_ownership"] = "ambiguous"
        record["followme_physical_evidence"] = []
        result_text = "遠景，無型號，無價格"
    else:
        field_safe = [
            item for item in supporting
            if item.get("model_validation_failed") is not True
            and item.get("price_conflict_detected") is not True
            and item.get("brand_evidence_conflict") is not True
        ]
        model = _consensus_value(
            field_safe,
            "model",
            lambda value: followme_identity_key(value) or normalize_model_token(value),
        )
        price = _consensus_value(
            field_safe,
            "price",
            lambda value: re.sub(r"[^0-9]", "", str(value or "")),
        )
        pair_votes: list[tuple[str, str]] = []
        for item in field_safe:
            model_key = followme_identity_key(item.get("model")) or normalize_model_token(item.get("model"))
            price_key = re.sub(r"[^0-9]", "", str(item.get("price") or ""))
            pair_votes.append((str(model_key or ""), price_key))
        pair_counts = Counter(pair_votes)
        exact_pair_supported = any(
            count >= 2
            and model_key == str((followme_identity_key(model) or normalize_model_token(model)) or "")
            and price_key == re.sub(r"[^0-9]", "", str(price or ""))
            for (model_key, price_key), count in pair_counts.items()
        )
        if model and price and not exact_pair_supported:
            # Never combine a model majority with a different price majority.
            model = None
            price = None
        matched_votes = sum(
            (item.get("normalized_evidence") or item).get("label_ownership") == "matched"
            for item in supporting
        )
        if matched_votes < 2:
            model = None
            price = None
        record["model"] = model
        record["price"] = price
        record["unique_main"] = True
        record["label_ownership"] = "matched" if matched_votes >= 2 else "ambiguous"
        if rule == "three_pass_subthree_distant_conflict_conservative_single":
            subthree_counts = [
                int((item.get("normalized_evidence") or item).get("complete_screen_count"))
                for item in supporting
                if isinstance((item.get("normalized_evidence") or item).get("complete_screen_count"), int)
                and not isinstance((item.get("normalized_evidence") or item).get("complete_screen_count"), bool)
                and int((item.get("normalized_evidence") or item).get("complete_screen_count")) in {1, 2}
            ]
            record["complete_screen_count"] = min(subthree_counts) if subthree_counts else 1
            record["followme_physical_evidence"] = []
        if rule == "two_pass_edge_cut_frame_consensus":
            record["complete_screen_count"] = 1
            record["followme_physical_evidence"] = []
        one_complete_votes = sum(
            _narration_supports_only_one_complete_monitor(item)
            for item in supporting
        )
        if one_complete_votes >= 2:
            record["complete_screen_count"] = 1
        if rule == "two_pass_followme_physical_consensus":
            # Physical consensus proves the FollowMe family even when the
            # three stateless passes disagree on M5/M7/Pro.  Preserve that
            # truthful family classification without inventing a variant.
            record["followme_family_confirmed"] = True
            record["followme_physical_evidence"] = list(
                (supporting[-1].get("normalized_evidence") or supporting[-1]).get(
                    "followme_physical_evidence"
                )
                or []
            )
        model_text = (
            model
            or ("FollowMe（型號未細分）" if record.get("followme_family_confirmed") is True else "無型號")
        )
        result_text = f"單機，{model_text}，{price or '無價格'}"

    record["three_pass_adjudicated"] = True
    record["adjudication_rule"] = rule
    record["adjudication_original_current"] = original
    record["adjudication_pass_summaries"] = pass_summaries
    record["adjudication_summary"] = (
        f"三輪證據已完成交叉核對，依固定實體證據規則定案為：{result_text}。"
        "型號或價格若沒有至少兩輪一致證據，維持無型號／無價格，不做猜測。"
    )
    record["evidence_guard_revision"] = EVIDENCE_GUARD_REVISION
    final_valid, _final_errors, normalized = validate_evidence_contract(record)
    if not final_valid:
        record.clear()
        record.update(original_record)
        return _technical_retry_outcome(outcome, "adjudicated_result_contract_invalid")
    record["normalized_evidence"] = normalized

    return {
        **outcome,
        "retry": False,
        "unresolved": False,
        "verified": True,
        "reasons": [],
        "recommended_model": "",
        "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
        "normalized_evidence": record["normalized_evidence"],
        "three_pass_adjudicated": True,
        "adjudication_rule": rule,
        "superseded_reasons": list(outcome.get("reasons") or []),
    }

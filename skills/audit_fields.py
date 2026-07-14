import re
from datetime import datetime
from typing import Any, Dict, List, Tuple

from skills.model_validation import is_placeholder_model


EVIDENCE_CONTRACT_VERSION = "v19.45"
LABEL_OWNERSHIP_VALUES = {"matched", "mismatched", "ambiguous", "not_visible", "not_applicable"}
FOLLOWME_CUE_CODES = {
    "direct_followme_branding_on_unit", "white_vertical_stand", "round_base",
    "portrait_display", "attached_price_tray", "attached_followme_product_card",
    "screen_content_only", "nearby_signage_only", "unknown",
}
FOLLOWME_WEAK_CUES = {"screen_content_only", "nearby_signage_only", "unknown"}
FOLLOWME_INDEPENDENT_STRONG_CUES = {"white_vertical_stand", "round_base", "portrait_display", "attached_price_tray", "attached_followme_product_card"}


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
    if record.get("view_type") == "遠景":
        if count is None or unique is None:
            errors.append("distant_evidence_missing")
        elif count < 3 or unique:
            errors.append("distant_evidence_inconsistent")
        if ownership == "matched":
            errors.append("distant_owned_label_conflict")
    if record.get("view_type") == "單機" and unique is not True:
        errors.append("single_unique_main_required")
    model = str(record.get("model") or "")
    if model.upper().startswith("FOLLOWME"):
        direct_branding = any(x["cue"] == "direct_followme_branding_on_unit" and x["same_subject"] and x["strength"] in {"strong", "direct"} for x in normalized_physical)
        strong_codes = {x["cue"] for x in normalized_physical if x["cue"] in FOLLOWME_INDEPENDENT_STRONG_CUES and x["same_subject"] and x["strength"] == "strong"}
        if not (direct_branding or len(strong_codes) >= 2):
            errors.append("followme_physical_evidence_insufficient")
    if record.get("model") or record.get("price"):
        if ownership != "matched":
            errors.append("label_ownership_required_for_fields")
    return not errors, list(dict.fromkeys(errors)), normalized


def evidence_contract_decision(record: Dict[str, Any], previous_results=None) -> Dict[str, Any]:
    valid, errors, normalized = validate_evidence_contract(record)
    reasons = list(errors)
    if previous_results:
        core = [(r.get("view_type"), r.get("complete_screen_count"), r.get("unique_main"), r.get("label_ownership")) for r in previous_results]
        current = (record.get("view_type"), record.get("complete_screen_count"), record.get("unique_main"), record.get("label_ownership"))
        if any(item != current for item in core):
            reasons.append("core_evidence_disagreement")
    return {"valid": valid and not any(x == "core_evidence_disagreement" for x in reasons), "reasons": list(dict.fromkeys(reasons)), "normalized_evidence": normalized}


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

    contract = evidence_contract_decision(record, history)
    record["evidence_contract_version"] = EVIDENCE_CONTRACT_VERSION
    record["normalized_evidence"] = contract["normalized_evidence"]
    if not contract["valid"]:
        reasons.extend(contract["reasons"])
    if "?" in view_type and contract["valid"]:
        if not _explicit_three_complete(thinking) or not _no_unique_main_evidence(thinking):
            reasons.append("evidence_thinking_conflict")

    if view_type == "失敗" or str(record.get("category") or "") == "失敗":
        reasons.append("處理失敗")
    if record.get("model_validation_failed") or is_placeholder_model(model):
        reasons.append("型號未通過正式清單驗證")
    if record.get("price_conflict_detected"):
        reasons.append("價格欄位互相衝突")
    if record.get("requires_structured_retry"):
        reasons.append("模型未回傳可信結構化結果")

    if "遠景" in view_type:
        if current_year and attempt < max_attempts:
            reasons.append("2026 遠景必須完成三輪獨立複核")
        if model or price:
            reasons.append("遠景不應帶型號或價格")
        if attempt >= max_attempts and not contract["valid"] and not _explicit_three_complete(thinking):
            reasons.append("遠景缺少三台以上完整入鏡證據")
        if attempt >= max_attempts and not contract["valid"] and not _no_unique_main_evidence(thinking):
            reasons.append("遠景缺少無法鎖定唯一主角規格/價格的證據")
        positive_followme = "FOLLOWME" in thinking.upper().replace(" ", "")
        negative_followme = bool(re.search(r"(?:不是|非|沒有|未見|看不到).{0,10}FOLLOW\s*ME", thinking, re.IGNORECASE))
        if positive_followme and not negative_followme:
            reasons.append("遠景仍含未排除的 FollowMe 線索")
        if quality and quality not in {"無", "正常", "None", "null"}:
            reasons.append(f"遠景與畫質標記需再確認:{quality}")
    elif "單機" in view_type or "FOLLOWME" in model.upper().replace(" ", ""):
        if current_year and not model:
            reasons.append("2026 單機缺型號")
        if current_year and not price:
            reasons.append("2026 單機缺價格")
        if quality and quality not in {"無", "正常", "None", "null"}:
            reasons.append(f"單機仍有品質疑慮:{quality}")
        if _explicit_three_complete(thinking) and _no_unique_main_evidence(thinking):
            reasons.append("單機結果與三台以上完整陳列衝突")

    if "FOLLOWME" in model.upper().replace(" ", ""):
        physical_count = sum(1 for clue in FOLLOWME_PHYSICAL_CLUES if clue in thinking)
        promo_only = _text_has_any(thinking, PROMO_ONLY_CLUES)
        if physical_count < 2 or (promo_only and physical_count < 3):
            reasons.append("FollowMe 缺少同一實機的物理支架證據")

    reasons = list(dict.fromkeys(reasons))
    retry = bool(reasons) and attempt < max_attempts
    unresolved = bool(reasons) and attempt >= max_attempts
    verified = False

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
        "normalized_evidence": contract["normalized_evidence"],
    }

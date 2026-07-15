import json
import re
from datetime import datetime
from typing import Any, Dict, List, Tuple

from skills.model_validation import is_placeholder_model, normalize_model_token


EVIDENCE_CONTRACT_VERSION = "v19.45"
# Immutable identity for the complete three-layer guard implementation.
# The contract version describes the evidence schema; this revision proves
# which guard logic actually evaluated that evidence.
EVIDENCE_GUARD_REVISION = "20260715.8"
LABEL_OWNERSHIP_VALUES = {"matched", "mismatched", "ambiguous", "not_visible", "not_applicable"}
FOLLOWME_CUE_CODES = {
    "direct_followme_branding_on_unit", "white_vertical_stand", "round_base",
    "portrait_display", "attached_price_tray", "attached_followme_product_card",
    "screen_content_only", "nearby_signage_only", "unknown",
}
FOLLOWME_WEAK_CUES = {"screen_content_only", "nearby_signage_only", "unknown"}
FOLLOWME_INDEPENDENT_STRONG_CUES = {"white_vertical_stand", "round_base", "portrait_display", "attached_price_tray", "attached_followme_product_card"}


def is_followme_model(model: Any) -> bool:
    """Recognize both friendly FollowMe names and the physical product SKUs.

    S32FM80x/S32FM90x are ordinary Smart Monitor models, so only the known
    FollowMe 32-inch 50x/70x and 43-inch 70x families are included here.
    """
    text = re.sub(r"[^A-Z0-9]", "", str(model or "").upper())
    if not text:
        return False
    if text.startswith("FOLLOWME"):
        return True
    return bool(re.fullmatch(r"(?:LS|S)?(?:32FM(?:50|70)\d|43FM70\d)[A-Z0-9]*", text))


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
        elif count < 3 or unique:
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
    if is_followme_model(model):
        if not has_sufficient_followme_physical_evidence({"followme_physical_evidence": normalized_physical}):
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
    price = _as_int(record.get("price"))
    if not model or price is None or record.get("label_ownership") != "matched":
        return False
    for prior in reversed(history):
        prior_model = re.sub(r"[^A-Z0-9]", "", str(prior.get("model") or "").upper())
        if prior_model == model and _as_int(prior.get("price")) == price and prior.get("label_ownership") == "matched":
            return True
    return False


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
        if not _explicit_three_complete(thinking) or not _no_unique_main_evidence(thinking):
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
    if attempt >= 2 and re.search(
        r"(?:您|你).{0,6}指正|先前.{0,10}(?:判斷|答案|型號|價格)|上一輪.{0,10}(?:判斷|答案|型號|價格)|修正.{0,8}(?:先前|前一).{0,8}(?:判斷|答案)",
        thinking,
        re.IGNORECASE,
    ):
        reasons.append("本輪出現承接前輪答案的污染語句")

    if view_type == "單機" and _narration_declares_distant(thinking):
        reasons.append("結構為單機但敘述明確判為遠景")
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
        negative_followme = bool(re.search(r"(?:不是|非|沒有|未見|看不到).{0,10}FOLLOW\s*ME", thinking, re.IGNORECASE))
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

    if is_followme_model(model):
        if not has_sufficient_followme_physical_evidence(contract["normalized_evidence"]):
            reasons.append("FollowMe 缺少同一實機的物理支架證據")
        if current_year and attempt < 2:
            reasons.append("2026 FollowMe 必須完成第二輪實體證據複核")

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

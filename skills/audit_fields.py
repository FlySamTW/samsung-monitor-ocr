import re
from typing import Any, Dict, List, Tuple


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

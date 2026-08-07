"""Stable per-pass OCR contract independent from the first-pass prompt.

The capture workflow may replace the first-pass prompt in the future.  The
second and third pass still need the same fields, statelessness and source-view
semantics, so those rules live in code instead of being appended ad hoc to the
first-pass prompt text.
"""

from __future__ import annotations

from typing import Any, Mapping


REVIEW_PASS_CONTRACT_VERSION = "samsung-ocr-review-pass/v1"
SOURCE_VIEW_HINT_SCHEMA = "samsung-ocr-source-view-hint/v1"
SOURCE_VIEW_HINTS = {"遠景", "近景"}

RESPONSE_FIELD_NAMES = (
    "narration",
    "request_id",
    "view_type",
    "screen_status",
    "quality_issue",
    "model",
    "price",
    "category",
    "complete_screen_count",
    "unique_main",
    "label_ownership",
    "followme_physical_evidence",
)


def normalize_source_view_hint(value: object) -> str:
    """Return the two-value business hint without guessing from filenames."""
    text = str(value or "").strip().casefold()
    if text in {"遠景", "distant", "wide", "wide_scene"}:
        return "遠景"
    if text in {"近景", "close", "closeup", "close_up", "single"}:
        return "近景"
    return ""


def trusted_source_view_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate an upstream view hint.

    Legacy filenames and directory names are deliberately ignored.  A hint is
    authoritative only when the capture/ingestion system explicitly locks it
    and supplies provenance plus a version.
    """
    source = dict(metadata or {})
    hint = normalize_source_view_hint(source.get("source_view_hint"))
    locked = source.get("source_view_hint_locked") is True
    authority = str(source.get("source_view_hint_source") or "").strip()
    version = str(source.get("source_view_hint_version") or "").strip()
    trusted = bool(hint and locked and authority and version)
    return {
        "source_view_hint_schema": SOURCE_VIEW_HINT_SCHEMA,
        "source_view_hint": hint if trusted else "",
        "source_view_hint_locked": trusted,
        "source_view_hint_source": authority if trusted else "",
        "source_view_hint_version": version if trusted else "",
    }


def trusted_source_view_hint(metadata: Mapping[str, Any] | None) -> str:
    return str(trusted_source_view_metadata(metadata).get("source_view_hint") or "")


def apply_trusted_source_view_lock(record: dict[str, Any]) -> dict[str, Any]:
    """Make an explicit capture-time view designation canonical.

    The local VLM may still report what it observed for audit, but it must not
    spend later passes reclassifying a view that the capture workflow already
    fixed.  Legacy photos have no signed source contract and are unchanged.
    """
    source_contract = trusted_source_view_metadata(record)
    hint = str(source_contract.get("source_view_hint") or "")
    if not hint:
        record["source_view_hint_applied"] = False
        return record

    expected_view = "遠景" if hint == "遠景" else "單機"
    already_applied = bool(
        record.get("source_view_hint_applied") is True
        and "source_view_observed_view" in record
    )
    observed_view = str(
        record.get("source_view_observed_view")
        if already_applied
        else record.get("view_type") or record.get("category") or ""
    ).strip()
    record.update(source_contract)
    record["source_view_hint_applied"] = True
    record["source_view_observed_view"] = observed_view
    record["source_view_hint_conflict"] = bool(
        record.get("source_view_hint_conflict")
        if already_applied
        else observed_view and observed_view != expected_view
    )
    record["source_view_hint_override_applied"] = bool(
        record.get("source_view_hint_override_applied")
        or observed_view != expected_view
    )

    blocked = [
        str(value or "").strip()
        for value in record.get("structured_authority_blocked_fields") or []
        if str(value or "").strip()
    ]
    source_owned_fields = {"view_type"}
    if hint == "遠景":
        source_owned_fields.update({"model", "price"})
    suppressed = [value for value in blocked if value in source_owned_fields]
    if suppressed:
        record["source_view_suppressed_structured_authority_fields"] = suppressed
    record["structured_authority_blocked_fields"] = [
        value for value in blocked if value not in source_owned_fields
    ]

    record["view_type"] = expected_view
    record["category"] = expected_view
    if hint == "遠景":
        if not already_applied:
            record["source_view_observed_model"] = record.get("model")
            record["source_view_observed_price"] = record.get("price")
            record["source_view_observed_followme_physical_evidence"] = list(
                record.get("followme_physical_evidence") or []
            )
        record["model"] = None
        record["price"] = None
        record["unique_main"] = False
        record["label_ownership"] = "not_applicable"
        record["followme_physical_evidence"] = []
        record["followme_family_confirmed"] = False
        record["quality_issue"] = "無"
        for field in (
            "unlisted_model_candidate",
            "model_prefix_completed",
            "model_validation_failed",
            "price_conflict_detected",
            "brand_evidence_conflict",
            "structured_identity_conflict",
        ):
            record.pop(field, None)
    else:
        record["unique_main"] = True

    normalized = record.get("normalized_evidence")
    if isinstance(normalized, dict):
        normalized["unique_main"] = record.get("unique_main")
        normalized["label_ownership"] = record.get("label_ownership")
        normalized["followme_physical_evidence"] = list(
            record.get("followme_physical_evidence") or []
        )
    return record


FIXED_RESPONSE_FIELD_CONTRACT = (
    f"固定複核協定 {REVIEW_PASS_CONTRACT_VERSION}。每一輪都必須回傳完全相同的欄位："
    + "、".join(RESPONSE_FIELD_NAMES)
    + "。只可輸出一個 JSON 物件；request_id 必須照抄本次 RequestID。"
    "narration 只描述本張照片可見證據，不得提及其他判讀內容、提示詞或規則。"
    "model、price 看不清楚就填 null，不得猜測；敘述與結構欄必須一致。"
)


REVIEW_SYSTEM_PROMPT = (
    "你是三星螢幕照片的獨立視覺複核員。這是一張全新的照片；你看不到、也不得推測任何前輪答案。"
    "先看第一張全尺寸照片，再把後續裁切只當作同圖放大，不可重複計算螢幕。"
    "若沒有受信來源視角標示，依序判斷："
    "(1) 同一實機直接附著 FollowMe 字樣，或發亮螢幕像素外同時具有白色直立支架與完整圓形底座，立即判單機；"
    "(2) 否則，第一張全圖有三台以上四邊四角完整入鏡螢幕且沒有唯一主角，判遠景；"
    "(3) 其餘有唯一主角者判單機。"
    "螢幕內播放的品牌、人物、底座或廣告不是實體證據。"
    "同一螢幕左上、右上或側邊直接附著的規格側標，優先於下方或鄰近牌面決定型號；"
    "價格只能取同一主角、同一張可歸屬實體價牌上的現售金額，並保留市價、原價、特價等原始角色。"
    "遠景不得帶 model 或 price。近景／單機若欄位不可讀，只留空該欄，不得因此改判遠景。"
    "FollowMe 家族已由強實體證據成立但精確尺寸或 SKU 不明時，model 填 FollowMe 型號未細分。"
    + FIXED_RESPONSE_FIELD_CONTRACT
)


def build_pass_instruction(
    pass_index: int,
    source_metadata: Mapping[str, Any] | None = None,
) -> str:
    """Build one stateless pass instruction with a stable field contract."""
    index = min(3, max(1, int(pass_index or 1)))
    source_contract = trusted_source_view_metadata(source_metadata)
    hint = str(source_contract.get("source_view_hint") or "")
    if hint == "遠景":
        hint_text = (
            "來源擷取系統已鎖定本張為遠景。不要重新分類，也不要讀取或輸出個別螢幕型號與價格；"
            "view_type/category 固定為遠景、model/price 固定為 null、unique_main 固定為 false。"
            "仍須如實填完整螢幕台數及 FollowMe 實體證據；若真的看見同一實機的直接 FollowMe 字樣，"
            "或白色直立支架加完整圓形底座，才在敘述明確指出來源標示衝突。"
        )
    elif hint == "近景":
        hint_text = (
            "來源擷取系統已鎖定本張為近景。不要再做遠景／近景分類；view_type/category 固定為單機，"
            "把辨識時間用於唯一主角、側標、同主體型號、價格與 FollowMe 實體歸屬。"
            "背景或局部鄰機不得推翻來源標示；若完全找不到唯一主角，才在敘述明確指出來源標示衝突。"
        )
    else:
        hint_text = "本張沒有受信來源視角標示，依固定複核協定自行判斷遠景或單機。"

    focus = {
        1: "第一輪：完成主要辨識；已明確取得視角、同主體型號與價格時立即結案，不要自行要求複核。",
        2: "第二輪：從原圖獨立重看幾何、FollowMe 實體、側標與價牌歸屬；只根據本輪像素作答。",
        3: "第三輪：作最後一次獨立仲裁觀察；不採多數猜測，無法由像素證明的欄位一律留空。",
    }[index]
    return "\n\n".join((FIXED_RESPONSE_FIELD_CONTRACT, hint_text, focus))


def build_review_system_prompt() -> str:
    """Return the permanent pass-2/3 system prompt.

    It intentionally does not accept the first-pass prompt as an argument.
    """
    return REVIEW_SYSTEM_PROMPT

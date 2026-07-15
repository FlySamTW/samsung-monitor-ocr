import os
import sys
import importlib
import argparse
import csv
import hashlib
from pathlib import Path
import time
import json
import base64
import logging
import re
import subprocess
import psutil
from flask import Flask, jsonify, request, send_file, send_from_directory, Response
from flask_cors import CORS
from rich.console import Console
from rich.logging import RichHandler
from openai import OpenAI

REPO_ROOT = Path(__file__).resolve().parent

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# === 鐵律：確保模組重載 ===
# 強制清除快取，確保每次執行都使用最新程式碼
def force_reload_skills():
    """徹底重載 skills 模組，避免快取問題"""
    skills_modules = [
        'skills.batch_orchestrator',
        'skills.prompt_versioning',
        'skills.image_processing',
        'skills.model_matching',
        'skills.field_extraction',
        'skills.evaluation',
        'skills.official_price',  # [v18.67] 官方價格驗證
        'skills.followme_reference',
        'skills.runtime_health_gate',
    ]

    for module_name in skills_modules:
        if module_name in sys.modules:
            importlib.reload(sys.modules[module_name])

# 執行強制重載
force_reload_skills()

# Import Skills (確保使用最新版本)
from skills.batch_orchestrator import BatchOrchestrator
from skills.prompt_versioning import PromptManager
from skills.official_price import get_price_manager, validate_ocr_price, try_discover_model, set_price_log_callback  # [v18.70]
from skills.followme_reference import build_followme_prompt_section, get_followme_products, reference_is_stale
from skills.model_validation import is_placeholder_model, strict_known_model
from skills.audit_fields import (
    immediate_retry_decision,
    validate_evidence_contract,
    EVIDENCE_CONTRACT_VERSION,
    has_sufficient_followme_physical_evidence,
    is_followme_model,
)
from skills.runtime_health_gate import (
    review_prompt_leak_reasons,
    read_runtime_health_fuse,
    BLOCKED_NARRATION,
)


def finalize_evidence_contract(result, raw_output=""):
    """Attach bounded machine evidence state; prose is never promoted to evidence."""
    result = dict(result or {})
    valid, errors, normalized = validate_evidence_contract(result)
    result["evidence_contract_version"] = EVIDENCE_CONTRACT_VERSION
    result["normalized_evidence"] = normalized
    result["evidence_contract_valid"] = bool(valid)
    result["evidence_contract_errors"] = errors[:20]
    if raw_output:
        result["raw_model_output"] = str(raw_output)[:12000]
    if not valid or result.get("merge_rejected_reason"):
        result["auto_review_required"] = True
        result["review_status"] = "review_required"
        result["evidence_unresolved"] = True
    return result


def attach_spatial_evidence_trace(result, scene_tiles, attempt):
    """Keep bounded crop provenance without retaining image payloads."""
    result = dict(result or {})
    result["spatial_evidence_crops"] = [
        {"label": str(tile.get("label")), "bbox": tuple(tile.get("bbox")), "size": tuple(tile.get("size")), "attempt": int(tile.get("attempt", attempt))}
        for tile in (scene_tiles or [])[:3]
    ]
    result["spatial_evidence_attempt"] = int(attempt)
    return result

VERSION = "v19.45 (accuracy-first evidence contract)"

# Accuracy is a production invariant, not a selectable speed profile.  The
# iterated prompt must never be silently replaced or truncated to fit a model.
RUNTIME_SYSTEM_PROMPT_MAX_CHARS = int(os.environ.get("OCR_SYSTEM_PROMPT_MAX_CHARS", "24000"))
ACCURACY_PROFILE = "strict"

_frontend_asset_cache = {"stamp": None, "fingerprint": ""}


def get_frontend_asset_fingerprint():
    """Fingerprint the built dashboard assets without touching OCR state."""
    index_path = REPO_ROOT / "dashboard" / "dist" / "index.html"
    try:
        stat = index_path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
        if _frontend_asset_cache["stamp"] == stamp:
            return _frontend_asset_cache["fingerprint"]
        html = index_path.read_text(encoding="utf-8")
        assets = sorted(set(re.findall(r'(?:src|href)=["\']([^"\']+\.(?:js|css)(?:\?[^"\']*)?)["\']', html)))
        digest = hashlib.sha256("\n".join(assets).encode("utf-8")).hexdigest()[:16]
        _frontend_asset_cache.update({"stamp": stamp, "fingerprint": digest})
        return digest
    except (OSError, UnicodeError):
        return ""

# Presentation identity is separate from OCR identity.  The UI must never
# reconstruct a card from filenames or rolling result lists.
_presentation_next_id = 0
_presentation_by_source = {}
_presentation_events = []
STATUS_PRESENTATION_WINDOW = max(3, min(24, int(os.environ.get("OCR_STATUS_PRESENTATION_WINDOW", "12"))))

_STATUS_EVENT_FIELDS = (
    "presentation_id", "presentation_sequence", "source_item_id", "file_name", "source_path",
    "run_id", "evidence_guard_revision",
    "pass_index", "pass_label", "ocr_attempt", "retry_reason", "model_id", "accuracy_profile",
    "evidence_contract_version", "started_at", "completed_at", "previous_result_summary",
    "decision", "full_ai_narration", "narration", "stream_buffer",
)
_STATUS_RESULT_FIELDS = (
    "file_name", "source_path", "view_type", "category", "model", "price", "screen_status",
    "quality_issue", "price_symbol", "price_status", "official_price", "price_diff_percent",
    "auto_verified", "auto_review_required", "review_status", "duration", "timestamp",
)


def _presentation_snapshot(raw, source_hint=""):
    global _presentation_next_id
    item = dict(raw or {})
    result = dict(item.get("result") or {})
    item["result"] = result
    presentation_id = str(item.get("presentation_id") or "")
    if not presentation_id:
        identity = f"backend-{source_hint}-{item.get('completed_at', '')}-{item.get('file_name', '')}"
        if identity not in _presentation_by_source:
            _presentation_next_id += 1
            _presentation_by_source[identity] = f"p-{_presentation_next_id:09d}"
        presentation_id = _presentation_by_source[identity]
    item["presentation_id"] = presentation_id
    sequence = int(item.get("presentation_sequence") or 0)
    if sequence <= 0:
        _presentation_next_id += 1
        sequence = _presentation_next_id
    else:
        _presentation_next_id = max(_presentation_next_id, sequence)
    item["presentation_sequence"] = sequence
    return item


def _compact_status_result(raw):
    """Expose only fields that the live dashboard renders; never inline images or raw evidence."""
    source = dict(raw or {})
    return {key: source[key] for key in _STATUS_RESULT_FIELDS if source.get(key) not in (None, "")}


def _compact_status_presentation(raw):
    source = dict(raw or {})
    compact = {key: source[key] for key in _STATUS_EVENT_FIELDS if source.get(key) not in (None, "")}
    result = _compact_status_result(source.get("result") or source.get("structured_result") or {})
    compact["result"] = result
    return compact


def _presentation_payload(orchestrator):
    """Return a small immutable live window; full per-photo history stays on the history API."""
    global _presentation_events
    if not getattr(orchestrator, "is_running", False):
        _presentation_events = []
        return []
    live = list(getattr(orchestrator, "display_queue", []) or [])
    current_events = []
    seen = set()
    for index, raw in enumerate(live):
        item = _presentation_snapshot(raw, f"live-{index}")
        if item["presentation_id"] not in seen:
            current_events.append(item)
            seen.add(item["presentation_id"])
    _presentation_events = current_events[-200:]
    return [_compact_status_presentation(event) for event in _presentation_events[-STATUS_PRESENTATION_WINDOW:]]


def _status_flag_true(value):
    return value is True or str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _status_needs_review(record):
    """Let explicit verification state override legacy placeholder wording."""
    if _status_flag_true(record.get("auto_review_required")):
        return True
    if _status_flag_true(record.get("auto_verified")):
        return False
    status = str(record.get("review_status") or "").strip().lower()
    return status in {"review_required", "待審核", "需慢模型或人工校正"}


V1945_OUTPUT_CONTRACT = (
    "FINAL OUTPUT CONTRACT (v19.45): Return exactly one JSON object and no prose. "
    "It must contain narration: a 60-300 character Traditional Chinese first-person observation of only the current image; "
    "narration must explain visible evidence and must not mention prior answers, corrections, prompts, JSON, or rounds. "
    "It must also contain core keys view_type, screen_status, quality_issue, model, price, category "
    "and evidence keys complete_screen_count (integer or null), unique_main (boolean or null), "
    "label_ownership (matched|mismatched|ambiguous|not_visible|not_applicable), "
    "followme_physical_evidence (one item per cue; cue is one of direct_followme_branding_on_unit, white_vertical_stand, round_base, portrait_display, attached_price_tray, attached_followme_product_card, screen_content_only, nearby_signage_only, unknown; each item has same_subject and strength weak|strong|direct). "
    "Use [] when no FollowMe physical evidence exists. Keep narration, core, and evidence in this same object."
)

REVIEW_FOCUS_PROMPTS = {
    2: (
        "只根據所附影像逐項計算完整入鏡螢幕台數，確認 FollowMe 文字是否只是"
        "背景宣傳牌，並確認型號與價格是否屬於同一台唯一主角。"
        "直接記錄當前影像的可見證據；沒有把握的欄位留空。"
    ),
    3: (
        "只根據所附影像逐項判斷完整入鏡台數、唯一主角、FollowMe 實體支架歸屬、"
        "型號清單有效性與價牌空間歸屬。不確定就留空，不可猜測；"
        "直接記錄當前影像的可見證據。"
    ),
}


def build_runtime_system_prompt(prompt_template, followme_daily_reference):
    """Return the complete production prompt or fail closed."""
    full_prompt = f"{prompt_template}{followme_daily_reference}\n\n{V1945_OUTPUT_CONTRACT}"
    limit = max(16000, RUNTIME_SYSTEM_PROMPT_MAX_CHARS)
    if len(full_prompt) > limit:
        raise RuntimeError(
            "完整 OCR 提示詞超過正式上限；請提高模型 context 或整理重複規則，"
            "不可自動切換短提示詞。"
        )
    return full_prompt, False


def build_ocr_messages(system_prompt, user_content, ocr_attempt=1, previous_results=None):
    """Build a stateless pass; no earlier answer may enter the model context."""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


import random, string
from datetime import datetime
SESSION_ID = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))

# [OCG-v2.3] Per-model token pricing (USD per 1M tokens) for OpenCode Go / Zen
# Local models (LM Studio) are treated as $0. Update via env vars if prices change.
OPENCODE_GO_PRICING = {
    "qwen3.7-plus": {"input": 0.40, "output": 1.60},
    "qwen3.7-max": {"input": 2.50, "output": 7.50},
    "qwen3.6-plus": {"input": 0.50, "output": 3.00},
    "qwen3.5-plus": {"input": 0.20, "output": 1.20},
    "claude-sonnet-4": {"input": 3.00, "output": 15.00},
    "claude-opus-4-1": {"input": 15.00, "output": 75.00},
    "gpt-5": {"input": 1.07, "output": 8.50},
    "gpt-5.4": {"input": 2.50, "output": 15.00},
}

def calculate_image_cost(model_name, input_tokens, output_tokens):
    """Calculate estimated USD cost for one image inference."""
    if not model_name:
        return None
    # Local models (LM Studio) have no cloud cost
    if model_name in {"qwen/qwen3-vl-8b", "qwen3vl8b-ocr"} or "lm-studio" in str(model_name).lower():
        return 0.0
    rates = OPENCODE_GO_PRICING.get(model_name)
    if not rates:
        # Try to load from env as fallback: OCR_PRICE_INPUT_1M / OCR_PRICE_OUTPUT_1M
        try:
            rates = {
                "input": float(os.environ.get("OCR_PRICE_INPUT_1M", "0")),
                "output": float(os.environ.get("OCR_PRICE_OUTPUT_1M", "0"))
            }
        except Exception:
            return None
    if input_tokens is None or output_tokens is None:
        return None
    cost = (input_tokens / 1_000_000.0) * rates["input"] + (output_tokens / 1_000_000.0) * rates["output"]
    return round(cost, 6)


def infer_period_from_text(*parts):
    for part in parts:
        text = str(part or "")
        month_match = re.search(r"(20\d{4})", text)
        if month_match:
            return month_match.group(1)
        year_match = re.search(r"(20\d{2})", text)
        if year_match:
            return year_match.group(1)
    return ""


def should_compare_official_price(fname=""):
    override = os.environ.get("OCR_COMPARE_OFFICIAL_PRICE", "").strip().lower()
    if override in {"1", "true", "yes", "on"}:
        return True
    if override in {"0", "false", "no", "off"}:
        return False

    active_dir = getattr(orchestrator, "image_dir", "") if "orchestrator" in globals() else ""
    period = infer_period_from_text(fname, active_dir)
    if not period:
        return True
    try:
        return int(period[:4]) >= datetime.now().year
    except ValueError:
        return True


def simulate_streaming_buffer(text, orchestrator_ref, char_delay=0.04):
    """Gradually populate orchestrator.stream_buffer to mimic real-time typing."""
    if not text or not orchestrator_ref:
        return
    import threading
    import time

    target = text[:800]

    def _type():
        displayed = ""
        for ch in target:
            # Stop if stream_buffer was reset (e.g., new image started)
            if orchestrator_ref.stream_buffer == "":
                return
            displayed += ch
            orchestrator_ref.stream_buffer = displayed
            time.sleep(char_delay)

    threading.Thread(target=_type, daemon=True).start()


def clean_stream_display(text):
    """[v19.15] 即時清理 stream 顯示文字，濾掉模型 echo 指令的殘留。"""
    if not text:
        return ""
    text = text.strip()
    # 移除常見的指令 echo
    for phrase in [
        "描述畫面內容，然後輸出JSON",
        "描述畫面內容，然後輸出 JSON",
        "描述畫面，然後輸出JSON",
        "描述畫面，然後輸出 JSON",
        "然後輸出JSON",
        "然後輸出 JSON",
        "輸出JSON",
        "輸出 JSON",
        "根據規則",
        "根據上述規則",
        "用戶希望我分析",
        "用户希望我分析",
        "作為三星門市店員",
        "作為台灣三星門市",
    ]:
        text = text.replace(phrase, "")
    # 去掉開頭的無意義標點
    text = text.lstrip("，,、.．。 ")
    return text.strip()


def extract_natural_monologue(text):
    """Extract the natural-language monologue part from model reasoning text.

    Models often output a structured analysis followed by a line like:
    '獨白：我看到...'. We prefer that sentence over the whole reasoning dump.
    Handles both Traditional and Simplified Chinese outputs.
    """
    if not text:
        return ""
    text = text.strip()

    def _clean_candidate(candidate):
        candidate = candidate.strip()
        for stop in ["\nJSON", "JSON 結構", "JSON结构", "\n{", "view_type:", "view_type：", "```"]:
            stop_idx = candidate.find(stop)
            if stop_idx != -1:
                candidate = candidate[:stop_idx].strip()
        # Keep only the first paragraph/line
        candidate = candidate.split("\n")[0].strip()
        # Remove common draft prefixes
        for prefix in ["草稿：", "草稿:", "草稿", "獨白：", "独白：", "獨白:", "独白:", "獨白", "独白"]:
            if candidate.startswith(prefix):
                candidate = candidate[len(prefix):].strip()
        return candidate

    # 1. Explicit monologue markers (with or without colon)
    for marker in ["獨白：", "独白：", "獨白:", "独白:", "獨白", "独白", "自言自語：", "自言自语：", "自言自語", "自言自语"]:
        idx = text.find(marker)
        if idx != -1:
            candidate = _clean_candidate(text[idx + len(marker):])
            if candidate and len(candidate) >= 10:
                return candidate

    # 2. Draft marker
    for marker in ["草稿：", "草稿:", "草稿"]:
        idx = text.find(marker)
        if idx != -1:
            candidate = _clean_candidate(text[idx + len(marker):])
            if candidate and len(candidate) >= 10:
                return candidate

    # 3. Find the sentence containing "我看到/我看" closest to the JSON block
    json_start = text.find("\n{")
    if json_start == -1:
        json_start = len(text)
    best_line = ""
    best_dist = float("inf")
    for line in text[:json_start].split("\n"):
        line = line.strip()
        if not line or len(line) < 15:
            continue
        # Skip structured / meta lines
        if line.startswith(("-", "*", "1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.", "#", "##", "###", "【", "[", "(", "`")):
            continue
        if any(k in line for k in ["用戶", "用户", "分析", "判斷", "判断", "分類", "分类", "提取", "構建", "构建", "確定", "确定", "最終檢查", "最终检查", "View Type", "Category", "Model:", "Price:", "Screen Status", "Quality Issue"]):
            continue
        if "我看到" in line or "我看" in line or "這台" in line or "这台" in line or "這張" in line or "这张" in line:
            dist = json_start - text.find(line)
            if dist >= 0 and dist < best_dist:
                best_line = line
                best_dist = dist

    if best_line:
        return _clean_candidate(best_line)

    return ""



def normalize_followme_model(raw_model, price=None, context_text="", structured_physical_confirmed=False):
    """Standardize Samsung FollowMe names when the model already detected FollowMe."""
    raw_model_text = str(raw_model or "").upper()
    context_upper = str(context_text or "").upper()
    text = " ".join(str(part or "") for part in [raw_model, context_text]).upper()
    if has_negative_followme_context(context_text) and not structured_physical_confirmed and not has_strong_followme_physical_signature(context_text) and not has_followme_display_fixture_clue(context_text):
        return None
    raw_model_has_followme = "FOLLOWME" in raw_model_text or "FOLLOW ME" in raw_model_text
    context_has_followme = has_positive_followme_word(context_text)
    if not raw_model_has_followme and not context_has_followme:
        return None
    if should_block_followme_due_to_other_brand(text, context_text):
        return None
    if not structured_physical_confirmed and not (
        has_strong_followme_physical_signature(context_text)
        or has_followme_display_fixture_clue(context_text)
    ):
        # FollowMe wording, a promo card, an on-screen advertisement, or a
        # price band cannot establish that the photographed unit is FollowMe.
        return None

    price_int = None
    if price is not None:
        digits = "".join(c for c in str(price) if c.isdigit())
        if digits:
            try:
                price_int = int(digits)
            except ValueError:
                price_int = None

    products = get_followme_products()
    code_name = match_followme_by_code(text, products)

    if code_name:
        return code_name
    pro_label_evidence = bool(
        re.search(
            r"(?:標籤|側標|規格牌|型號|牌面|寫著).{0,32}(?:FOLLOW\s*ME\s*PRO|S43FM|43\s*(?:吋|型|\"))"
            r"|(?:FOLLOW\s*ME\s*PRO|S43FM|43\s*(?:吋|型|\")).{0,32}(?:標籤|側標|規格牌|型號|牌面|寫著)",
            str(context_text or ""),
            re.IGNORECASE,
        )
    )
    if pro_label_evidence:
        return 'FollowMe Pro M7 43"'
    if "M5" in raw_model_text or "S32FM50" in raw_model_text:
        return 'FollowMe M5 32"'
    if "M7" in raw_model_text or "S32FM70" in raw_model_text or "S32DM70" in raw_model_text:
        return 'FollowMe M7 32"'
    if price_int and 12000 <= price_int <= 14000 and any(token in text for token in ["32", "M7", "S32FM70", "S32DM70", "4K"]):
        return 'FollowMe M7 32"'
    if "M5" in context_upper or "S32FM50" in context_upper:
        return 'FollowMe M5 32"'
    if "M7" in context_upper or "S32FM70" in context_upper or "S32DM70" in context_upper or "4K" in context_upper:
        return 'FollowMe M7 32"'
    # Price is not identity evidence.  Store signs, unrelated promotions and
    # OCR hallucinations must never turn a 32-inch FollowMe into Pro 43.
    return 'FollowMe M7 32"'


def infer_followme_from_physical_clues(price=None, context_text=""):
    """Infer FollowMe when the model describes the physical stand/tray but outputs 遠景."""
    raw_text = str(context_text or "")
    text = raw_text.upper()
    display_fixture_clue = has_followme_display_fixture_clue(raw_text)
    positive_followme_evidence = display_fixture_clue
    if should_block_followme_due_to_other_brand(text, context_text):
        return None
    if has_negative_followme_context(text) and not positive_followme_evidence:
        return None
    if re.search(r"(沒有|無|不是|非).{0,24}(白色支架|垂直支架|圓形底座|白色底座|支架|底座)", raw_text) and not positive_followme_evidence:
        return None

    has_followme_word = has_positive_followme_word(raw_text)
    if not has_followme_word or followme_physical_signature_score(raw_text) < 2:
        return None

    digits = "".join(c for c in str(price or "") if c.isdigit())
    price_int = int(digits) if digits else None
    code_name = match_followme_by_code(text, get_followme_products())
    if code_name:
        return code_name
    normalized = normalize_followme_model("FollowMe", price, raw_text)
    if normalized:
        return normalized
    if "M5" in text or "S32FM50" in text:
        return 'FollowMe M5 32"'
    if "M7" in text or "S32FM70" in text or "S32DM70" in text or "4K" in text:
        return 'FollowMe M7 32"'
    if has_followme_word or positive_followme_evidence:
        return normalize_followme_model(None, price, context_text)
    return None


def rescue_followme_32_from_side_label(context_text=""):
    """Rescue FollowMe 32 when a side spec label and M7 price are clearly described."""
    raw_text = str(context_text or "")
    text = raw_text.upper()
    if not has_positive_followme_word(raw_text) or followme_physical_signature_score(raw_text) < 2:
        return None
    has_product = "4K" in text or "移動式智慧聯網組" in raw_text
    has_32 = bool(re.search(r'(?:32\s*(?:吋|型)|32\s*["”]|[(（]\s*32\s*["”]?\s*[)）])', raw_text, re.IGNORECASE))
    has_side_label = any(token in raw_text for token in ["側標", "規格側標", "右側", "黑色規格", "3840x2160", "HDR10", "Type-C", "HDMI"])
    prices = [int(value.replace(",", "")) for value in re.findall(r'(?<!\d)(1[23],?9[09]0)(?!\d)', raw_text)]
    has_m7_price = any(price in {12900, 12990, 13900, 13990} for price in prices)
    if has_product and has_32 and has_side_label and has_m7_price:
        return {"model": 'FollowMe M7 32"', "price": str(next(price for price in prices if price in {12900, 12990, 13900, 13990}))}
    return None


def match_followme_by_price(price_int, products):
    if not price_int:
        return None
    candidates = []
    for product in products:
        price_info = product.get("price", {})
        low, high = price_info.get("range_twd") or price_info.get("expected_range_twd") or [None, None]
        if low is not None and high is not None and low <= price_int <= high:
            candidates.append(product.get("name"))
    return candidates[0] if len(candidates) == 1 else None


def match_followme_by_code(text, products):
    for product in products:
        for code in product.get("model_codes", []):
            compact = code.upper().replace("LS", "").replace("XZW", "")
            if compact and compact in text:
                return product.get("name")
    return None


def has_negative_followme_context(text):
    raw_text = str(text or "")
    normalized = raw_text.upper().replace(" ", "")
    if any(_is_negated_followme_occurrence(raw_text, match) for match in _followme_mentions(raw_text)):
        return True
    if re.search(r"(非|不是|不屬於|不符合|均非)FOLLOWME", normalized):
        return True
    if re.search(r"(沒有|無|不是|非|看不到|未看到)(?:任何)?FOLLOWME.*(支架|底座|特徵|結構)", normalized):
        return True
    if re.search(r"(沒有|無|不是|非|看不到|未看到)FOLLOWME.*(支架|底座|特徵|結構)", normalized):
        return True
    return any(token in normalized for token in [
        "非FOLLOWME",
        "不是FOLLOWME",
        "不適用FOLLOWME",
        "不屬於FOLLOWME",
        "不符合FOLLOWME",
        "不採用FOLLOWME",
        "沒有FOLLOWME特徵",
        "無FOLLOWME特徵",
        "沒有FOLLOWME支架",
        "無FOLLOWME支架",
        "看不到FOLLOWME支架",
        "未看到FOLLOWME支架",
        "沒有FOLLOWME底座",
        "無FOLLOWME底座",
        "看不到FOLLOWME底座",
        "未看到FOLLOWME底座",
    ])


FOLLOWME_PHYSICAL_CLUE_TEXTS = (
    "white vertical stand",
    "vertical stand",
    "white circular base",
    "circular base",
    "mobile smart",
    "mobile stand",
    "白色垂直支架",
    "垂直支架",
    "白色直立支架",
    "直立支架",
    "白色立式",
    "白色外框",
    "白色圓形底座",
    "圓形底座",
    "圓盤底座",
    "白色圓盤",
    "白色底座",
    "落地底座",
    "直桿",
    "長直桿",
    "移動式智慧聯網",
    "移動式立架",
    "托盤",
)


FOLLOWME_DISPLAY_FIXTURE_TERMS = (
    "立式螢幕",
    "展示螢幕",
    "顯示螢幕",
    "直立螢幕",
    "獨立螢幕",
    "展示用",
    "立式展示",
    "直立展示",
    "落地展示",
    "移動式",
    "支架",
    "底座",
    "托盤",
)


FOLLOWME_DISPLAY_LABEL_TERMS = (
    "標籤",
    "標牌",
    "牌面",
    "產品標示",
    "上方",
    "側標",
    "旁邊",
    "寫著",
    "顯示",
)


FOLLOWME_PHYSICAL_NEGATIONS = (
    "沒有",
    "沒看到",
    "未看到",
    "看不到",
    "不是",
    "並非",
    "非",
    "無",
    "no ",
    "not ",
    "without",
)


def _followme_mentions(text):
    return list(re.finditer(r"FOLLOW\s*ME", str(text or ""), flags=re.IGNORECASE))


def _is_negated_followme_occurrence(text, match):
    raw_text = str(text or "")
    before = raw_text[max(0, match.start() - 42):match.start()].upper()
    after = raw_text[match.end():match.end() + 42].upper()
    before_compact = re.sub(r"[\s「」『』()（）,:：、]", "", before)
    after_compact = re.sub(r"[\s「」『』()（）,:：、]", "", after)
    if re.search(r"(?:沒有|並無|無|未見|未看到|沒看到|看不到|找不到|不具備|不是|並非|非).{0,30}$", before_compact):
        return True
    if re.match(r".{0,12}(?:字樣|標誌|標籤|線索|特徵|支架|底座).{0,12}(?:不存在|不足|沒有|未見|看不到|不清楚)", after_compact):
        return True
    return False


def has_positive_followme_word(text):
    """Return True only for a non-negated FollowMe mention."""
    raw_text = str(text or "")
    mentions = _followme_mentions(raw_text)
    return any(not _is_negated_followme_occurrence(raw_text, match) for match in mentions)


def followme_physical_signature_score(text):
    raw_text = str(text or "")
    groups = (
        ("白色垂直支架", "垂直支架", "白色直立支架", "直立支架", "白色立式", "移動式立架", "移動式智慧聯網", "vertical stand", "mobile stand"),
        ("白色圓形底座", "圓形底座", "圓盤底座", "白色圓盤", "白色底座", "落地底座", "circular base", "white circular base"),
        ("托盤", "底部托盤", "置物托盤"),
    )
    return sum(any(term.lower() in raw_text.lower() for term in group) for group in groups)


def has_positive_samsung_subject(text):
    raw_text = str(text or "")
    for match in re.finditer(r"SAMSUNG|三星", raw_text, flags=re.IGNORECASE):
        before = re.sub(
            r"[\s「」『』()（）,:：、]",
            "",
            raw_text[max(0, match.start() - 18):match.start()],
        )
        if not re.search(r"(?:沒有|並無|無|未見|未看到|不是|並非|非).{0,10}$", before):
            return True
    return False


def has_strong_followme_physical_signature(text):
    raw_text = str(text or "")
    has_samsung = has_positive_samsung_subject(raw_text)
    return has_samsung and followme_physical_signature_score(raw_text) >= 2


def has_positive_followme_physical_clue(text):
    """Return True only for a Samsung unit with two physical signature groups."""
    return has_strong_followme_physical_signature(text)


def has_followme_display_fixture_clue(text):
    """Require same-unit physical ownership, not nearby FollowMe wording."""
    raw_text = str(text or "")
    upper = raw_text.upper().replace(" ", "")
    has_followme = has_positive_followme_word(raw_text)
    if not has_followme:
        return False
    has_samsung = has_positive_samsung_subject(raw_text)
    physical_score = followme_physical_signature_score(raw_text)
    ownership_terms = (
        "同一台", "主角螢幕", "主角實機", "連著", "連接", "正下方",
        "屬於主角", "自己的支架", "自己的底座", "實體機",
    )
    has_ownership = any(term in raw_text for term in ownership_terms)
    has_negative_product_context = any(
        term in raw_text
        for term in (
            "只是海報",
            "單純海報",
            "廣告海報",
            "不是商品",
            "不是主角",
            "旁邊廣告",
            "宣傳卡",
            "活動立牌",
            "促銷立牌",
            "螢幕廣告",
            "只有文字",
        )
    )
    return has_samsung and physical_score >= 2 and has_ownership and not has_negative_product_context


def should_block_followme_due_to_other_brand(text, context_text=""):
    """Block LG/other-brand false positives without losing a visible Samsung FollowMe unit."""
    raw_text = str(context_text or "")
    combined = str(text or "")
    upper = combined.upper()
    other_brand_tokens = (
        "它牌(", "ACER", "ASUS", "AOC", "MSI", "LG", "PHILIPS", "BENQ",
        "VIEWSONIC", "DELL", "HP", "LENOVO", "GIGABYTE", "CHIMEI",
        "STANBYME", "MYVIEW", "27ART10", "27LX5", "43SQ700", "32SR83",
    )
    if not any(token in upper for token in other_brand_tokens):
        return False

    has_followme_word = has_positive_followme_word(raw_text)
    has_samsung = has_positive_samsung_subject(raw_text)
    if not has_followme_word or not has_samsung:
        return True

    # A Samsung FollowMe sign plus a standing/display clue is enough to keep the
    # Samsung monitor candidate even when a nearby LG product is also visible.
    standing_display_terms = (
        "立式螢幕",
        "展示螢幕",
        "顯示螢幕",
        "直立螢幕",
        "獨立螢幕",
        "白色立柱",
        "白色支架",
        "垂直支架",
        "圓形底座",
        "白色底座",
        "托盤",
        "移動式",
    )
    return not (
        has_followme_display_fixture_clue(raw_text)
    )


def is_followme_standard_name(model):
    return str(model or "").upper().startswith("FOLLOWME")


def has_strong_single_unit_evidence(text):
    raw_text = str(text or "")
    single_subject_terms = [
        "同一台主角",
        "只有一台",
        "單一主角",
        "主角商品",
        "主角自己的",
        "主角螢幕",
        "主體是",
        "主角是",
        "前景主角螢幕",
        "前景的一台螢幕",
        "中央一台",
        "中間一台",
        "中間的螢幕",
        "主要螢幕",
        "不是遠景",
        "不屬於遠景",
        "不符合遠景",
        "一般單機",
        "單機條件",
    ]
    if has_followme_display_fixture_clue(raw_text) or has_positive_followme_physical_clue(raw_text):
        return True

    # Do not reverse the meaning of sentences such as "沒有單一主角".  The
    # earlier substring-only implementation treated that as positive proof of
    # a single unit and weakened all distant-view checks.
    negations = ("沒有", "未", "無", "看不到", "並非")
    for term in single_subject_terms:
        start = 0
        while True:
            index = raw_text.find(term, start)
            if index < 0:
                break
            if term in {"不是遠景", "不屬於遠景", "不符合遠景"}:
                return True
            # Keep enough left context to preserve negation in phrases such as
            # "無法讀取唯一主角自己的規格".  An eight-character window dropped
            # the leading "無" and repeatedly inverted explicit distant views.
            before = raw_text[max(0, index - 20):index]
            if not any(negation in before for negation in negations):
                return True
            start = index + len(term)

    # A display wall can contain many side labels and price cards.  Those words
    # only prove a single subject when the narration explicitly assigns the
    # label/card to one monitor.  Generic wording such as "整排螢幕下方有實體價牌"
    # must not overturn an otherwise explicit distant-view conclusion.
    owned_label_terms = ("側標", "實體標籤", "實體價牌", "型號標籤")
    owner_terms = (
        "主角", "主體", "同一台", "這台", "該台", "那台", "一台螢幕",
        "單一螢幕", "中央螢幕", "中間螢幕", "前景螢幕", "主要螢幕",
        "可對應", "對應同一台", "自己的",
    )
    for label_term in owned_label_terms:
        start = 0
        while True:
            index = raw_text.find(label_term, start)
            if index < 0:
                break
            context_window = raw_text[max(0, index - 28):min(len(raw_text), index + len(label_term) + 16)]
            if any(owner in context_window for owner in owner_terms):
                return True
            start = index + len(label_term)
    return False


def should_demote_distant_to_single_review(view_type, context_text=""):
    """A distant-view answer with its own single-subject clues is unsafe."""
    raw_text = str(context_text or "")
    if view_type != "遠景":
        return False
    if not has_strong_single_unit_evidence(raw_text):
        return False
    return True


def should_block_rescue_from_distant_view(view_type, context_text=""):
    """When the model explicitly ends as 遠景, do not rescue stray labels from a display wall."""
    raw_text = str(context_text or "")
    if view_type != "遠景":
        return False
    if "整體符合「遠景」條件" not in raw_text and "遠景" not in raw_text:
        return False
    return not has_strong_single_unit_evidence(raw_text)


def has_explicit_distant_layout_evidence(context_text=""):
    """Require an explicit display-wall conclusion before overriding a single-unit result.

    Mentioning a store, shelf, or several labels is not enough.  Those terms occur
    in many valid single-monitor and FollowMe photos, so an uncertain result must
    stay a reviewable single unit instead of becoming a false distant view.
    """
    raw_text = str(context_text or "")
    if has_strong_single_unit_evidence(raw_text):
        return False
    if "整體符合「遠景」條件" not in raw_text:
        return False
    layout_terms = ("多台", "整排", "展示牆", "一整排", "螢幕牆", "超過 3", "超過3", "並排")
    no_unique_subject_terms = (
        "沒有明確的主角",
        "沒有明確主角",
        "沒有單一主角",
        "無法鎖定唯一主角",
        "無法指定唯一主角",
        "無法指定主角",
        "沒有指定主角",
        "無可唯一對應",
    )
    return (
        sum(term in raw_text for term in layout_terms) >= 2
        or any(term in raw_text for term in no_unique_subject_terms)
    )


def has_confirmed_followme_evidence(model, context_text=""):
    """Prevent a successful FollowMe rescue from being overwritten later."""
    if not is_followme_standard_name(model):
        return False
    raw_text = str(context_text or "")
    return has_followme_display_fixture_clue(raw_text) or has_strong_followme_physical_signature(raw_text)


def normalize_followme_price(model, price=None, context_text=""):
    """Never rewrite a photographed price from a presumed product mapping."""
    return None


def build_final_display_thinking(result, original_thinking=""):
    """Return the user-facing final narration after backend corrections."""
    model = str((result or {}).get("model") or "").strip()
    view_type = str((result or {}).get("view_type") or (result or {}).get("category") or "").strip()
    price = str((result or {}).get("price") or "").strip()
    thinking = str(original_thinking or "").strip()
    upper_model = model.upper()
    upper_thinking = thinking.upper()

    final_price = price if price and price.lower() not in {"null", "none"} else "無價格"
    final_model = model if model and model.lower() not in {"null", "none"} else "無型號"

    followme_final = upper_model.startswith("FOLLOWME")
    has_conflicting_followme_text = (
        followme_final
        and (
            has_negative_followme_context(thinking)
            or "整體符合「遠景」條件" in thinking
            or "不是 FOLLOWME" in upper_thinking
            or "非 FOLLOWME" in upper_thinking
        )
    )
    if has_conflicting_followme_text:
        return (
            f"最終校正：這張判定為單機，型號 {final_model}，價格 {final_price}。"
            "畫面中有 Samsung FollowMe 立式展示/產品標示，因此不能因旁邊賣場環境、"
            "其他品牌或背景多台螢幕而判為遠景。"
        )

    if not thinking or thinking == "...":
        return (
            "AI 本輪未回傳完整判讀文字；"
            f"目前僅能確認結構化結果：{view_type or '待複核'}，{final_model}，{final_price}。"
        )

    return thinking


_CLEARANCE_PRICE_KEYWORDS = (
    "手寫",
    "促銷價",
    "出清",
    "展示出清",
    "展示機",
    "福利品",
    "清倉",
    "特賣",
)
_LOW_PRICE_BLOCK_KEYWORDS = (
    "月付",
    "月租",
    "方案",
    "分期",
    "搭配價",
    "電信",
    "配件",
)


def has_clearance_price_context(context_text=""):
    """True when a low 4-digit number is visibly a handwritten/clearance shelf price."""
    text = str(context_text or "")
    if not text:
        return False
    if any(token in text for token in _LOW_PRICE_BLOCK_KEYWORDS):
        return False
    return any(token in text for token in _CLEARANCE_PRICE_KEYWORDS)


def clean_monitor_price(price, min_price=2000, context_text=""):
    """Return a numeric monitor price string, or None for impossible/plan/accessory prices."""
    if price in (None, "", "null", "None"):
        return None
    digits = "".join(c for c in str(price) if c.isdigit())
    if len(digits) not in [4, 5]:
        return None
    try:
        price_int = int(digits)
    except ValueError:
        return None
    if has_clearance_price_context(context_text):
        min_price = min(min_price, 1000)
    if price_int < min_price:
        return None
    return digits


OTHER_BRAND_ALIASES = (
    ("ACER", ("ACER", "宏碁", "PREDATOR", "NITRO")),
    ("ASUS", ("ASUS", "華碩", "ROG", "TUF")),
    ("LG", ("LG", "STANBYME", "閨蜜機", "MYVIEW")),
    ("BENQ", ("BENQ", "明基", "MOBIUZ")),
    ("MSI", ("MSI", "微星", "OPTIX")),
    ("VIEWSONIC", ("VIEWSONIC", "優派")),
    ("AOC", ("AOC",)),
    ("DELL", ("DELL", "ALIENWARE")),
    ("PHILIPS", ("PHILIPS", "飛利浦")),
    ("HP", ("HP", "OMEN")),
    ("LENOVO", ("LENOVO", "聯想")),
    ("GIGABYTE", ("GIGABYTE", "技嘉", "AORUS")),
)


def normalize_other_brand_model(brand):
    raw_brand = str(brand or "").strip()
    raw_upper = raw_brand.upper()
    for canonical, aliases in OTHER_BRAND_ALIASES:
        if any(alias.upper() in raw_upper for alias in aliases):
            return f"它牌({canonical})"
    brand_text = re.sub(r"[^A-Z0-9+_. -]", "", raw_upper).strip()
    if not brand_text:
        return None
    return f"它牌({brand_text})"


def is_other_brand_model(model):
    return bool(re.fullmatch(r"它牌[（(][A-Z0-9+_. -]+[）)]", str(model or "").strip().upper()))


def is_samsung_model_like(model):
    text = str(model or "").strip().upper().replace("-", "")
    if not text or is_other_brand_model(text):
        return False
    if "FOLLOWME" in text or "FOLLOW ME" in text:
        return True
    return bool(re.match(r"^(S|C|U|LC|LS|LU|LF|LH)[A-Z0-9]{6,}$", text))


def infer_other_brand_model(context_text="", raw_model=None):
    """Return normalized '它牌(BRAND)' when the main monitor is clearly not Samsung."""
    context = str(context_text or "")
    # Brand names inside the pixels being displayed are content, not hardware
    # ownership (for example: "螢幕顯示 ASUS Demo 畫面").
    context = re.sub(
        r"(?:螢幕|畫面)?(?:顯示|播放|呈現).{0,40}?(?:DEMO|畫面|內容|介面)",
        " ",
        context,
        flags=re.IGNORECASE,
    )
    raw_text = " ".join(str(part or "") for part in [raw_model, context])
    if not raw_text.strip():
        return None

    existing = re.search(r"它牌[（(]\s*([^）)]+)\s*[）)]", raw_text, re.IGNORECASE)
    if existing:
        normalized_existing = normalize_other_brand_model(existing.group(1))
        if normalized_existing:
            return normalized_existing

    upper_text = raw_text.upper()
    raw_upper = str(raw_model or "").upper()
    # Generic words such as "螢幕顯示 ASUS Demo 畫面" describe screen
    # content, not the physical monitor brand.  Require an explicit subject or
    # brand/logo relationship before replacing a Samsung model with 它牌.
    subject_terms = r"(主角|主體|這台|此台|這個商品|商品|品牌|LOGO)"
    product_terms = r"(螢幕|顯示器|MONITOR|品牌|LOGO|型號)"

    for brand, aliases in OTHER_BRAND_ALIASES:
        alias_re = "|".join(re.escape(alias.upper()) for alias in aliases)
        if raw_upper and re.search(alias_re, raw_upper, re.IGNORECASE):
            return normalize_other_brand_model(brand)
        if re.search(rf"{subject_terms}.{{0,30}}(?:{alias_re})", upper_text, re.IGNORECASE):
            return normalize_other_brand_model(brand)
        content_only_denial = re.search(
            rf"(?:{alias_re}).{{0,60}}(?:螢幕內|遊戲畫面|畫面內容|影像內容).{{0,40}}"
            rf"(?:不是|並非|非).{{0,10}}(?:品牌|品牌標籤|實體品牌)",
            upper_text,
            re.IGNORECASE,
        )
        if content_only_denial:
            continue
        if re.search(rf"(?:{alias_re}).{{0,24}}{product_terms}", upper_text, re.IGNORECASE):
            return normalize_other_brand_model(brand)
        if re.search(rf"(不是|非|不屬於).{{0,10}}(SAMSUNG|三星).{{0,24}}(?:{alias_re})", upper_text, re.IGNORECASE):
            return normalize_other_brand_model(brand)
    return None


def should_clear_non_samsung_price(model, context_text=""):
    """Do not keep a visible price when the model itself says the subject is not Samsung."""
    if model:
        return False
    if infer_other_brand_model(context_text, model):
        return False
    text = str(context_text or "").upper()
    if re.search(r"(不是|非)\s*(LG|ASUS|ROG|BENQ|ACER)", text, re.IGNORECASE):
        return False
    if any(token in text for token in ["非三星產品", "非SAMSUNG產品", "不是三星產品", "不是SAMSUNG產品"]):
        return True
    return bool(re.search(r"(主角|主體|這台|此台|商品).{0,12}(LG|ASUS|ROG|BENQ|ACER)", text, re.IGNORECASE))


def should_block_borrowed_model_rescue(context_text=""):
    raw_text = str(context_text or "")
    if has_followme_display_fixture_clue(raw_text):
        return False
    text = str(context_text or "").upper().replace(" ", "")
    return any(token in text for token in [
        "不可借用",
        "不能借用",
        "不可拿",
        "不能拿",
        "旁邊小牌",
        "旁邊小螢幕",
        "旁邊其他",
        "活動立牌",
        "立牌",
    ])


def extract_main_label_model(context_text=""):
    raw_text = str(context_text or "")
    patterns = [
        r"(?:主角|主體|這台|此台).{0,30}(?:標籤|價牌).{0,20}\b(S\d{2}[A-Z][A-Z0-9]{4,})\b",
        r"(?:標籤|價牌).{0,12}(?:寫|寫著|標示|顯示|型號(?:是|為)?).{0,30}\b(S\d{2}[A-Z][A-Z0-9]{4,})\b",
        r"(?:主角|主體|中間).{0,40}(?:SAMSUNG|三星).{0,18}\b(S\d{2}[A-Z][A-Z0-9]{4,})\b.{0,30}(?:OLED|電競|遊戲|GAMING)",
        r"型號(?:是|為)?\s*\b(S\d{2}[A-Z][A-Z0-9]{4,})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw_text, re.IGNORECASE)
        if not match:
            continue
        prefix = raw_text[max(0, match.start() - 18):match.start()]
        if any(term in prefix for term in ["旁邊", "小牌", "小螢幕", "其他", "活動立牌", "贈品立牌"]):
            continue
        return match.group(1).upper()
    return None


def correct_common_model_price_conflict(model, price, context_text=""):
    """Compatibility hook: never infer or rewrite a model from its price."""
    return model


def ensure_followme_reference_fresh(max_age_hours=24):
    """Refresh the FollowMe reference once at startup when the local copy is stale."""
    if not reference_is_stale(max_age_hours=max_age_hours):
        return "fresh"

    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "update_followme_reference.py")
    if not os.path.exists(script_path):
        return "missing_script"

    try:
        result = subprocess.run(
            [sys.executable, script_path],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )
    except Exception as exc:
        return f"failed: {exc}"

    if result.returncode == 0:
        return "updated"
    detail = (result.stderr or result.stdout or "").strip().splitlines()
    return f"failed: {detail[-1] if detail else 'unknown error'}"


def should_clear_borrowed_odyssey_ark_model(model, context_text=""):
    """Avoid borrowing a nearby small-monitor S model when the visible label is Odyssey Ark."""
    text = str(context_text or "").upper()
    model_text = str(model or "").upper()
    if "ODYSSEY ARK" not in text:
        return False
    return model_text.startswith(("S24", "S27", "S32"))


def has_odyssey_ark_context(context_text=""):
    return "ODYSSEY ARK" in str(context_text or "").upper()


def infer_odyssey_ark_model(context_text=""):
    """Treat the 55-inch Odyssey Ark floor/desk display as the known Ark model."""
    text = str(context_text or "").upper()
    compact = re.sub(r"[^A-Z0-9]", "", text)
    if "S55BG970" in compact or "LS55BG970" in compact:
        return "S55BG970NC"
    ark_subject_evidence = any(
        term in text
        for term in ("主角", "這台", "主體", "55 吋", "55吋", "55 型", "55型", "MINI LED", "大型直立", "大型曲面")
    )
    ark_size_evidence = bool(re.search(r"55\s*(?:吋|型|\")", text)) or "MINI LED" in text
    if "ODYSSEY ARK" in text and ark_subject_evidence and ark_size_evidence:
        return "S55BG970NC"
    return None

# --- Logging Setup (必須在函數定義前) ---
# [v19.8] Avoid cp950 crash when stdout is redirected to a file.
if sys.stdout.isatty():
    console = Console()
else:
    console = Console(file=sys.stdout, force_terminal=False, no_color=True, legacy_windows=False)

# === 鐵律：版本追蹤與快取檢查 ===
def print_version_info():
    """顯示版本資訊，確保使用者知道正在執行的版本"""
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    console.print(f"🔄 [bold green]Samsung OCR System {VERSION}[/bold green]")
    console.print(f"📅 啟動時間: {current_time}")
    console.print(f"🆔 Session ID: {SESSION_ID}")
    console.print(f"🔧 鐵律模式: [bold yellow]強制重載所有模組[/bold yellow]")
    console.print("=" * 50)

# === 快取檢查 ===
CONFIG_FILE = ".last_run_config.json"

def load_last_config():
    """載入上次執行的設定 (目錄、模型、API 端點)"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            console.print(f"[yellow]⚠️ 無法讀取設定檔: {e}[/yellow]")
    return {}

def save_last_config(image_dir, model_name, api_base=None, api_key=None):
    """儲存本次執行的設定"""
    try:
        data = {
            "last_image_dir": image_dir,
            "last_model": model_name,
            "updated_at": datetime.now().isoformat()
        }
        if api_base is not None:
            data["last_api_base"] = api_base
        if api_key is not None:
            data["last_api_key"] = api_key
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        # console.print(f"[dim]💾 設定已儲存: {image_dir}[/dim]")
    except Exception as e:
        console.print(f"[red]❌ 設定儲存失敗: {e}[/red]")
def verify_no_cache():
    """檢查是否還有殘留的快取檔案"""
    cache_dirs = []
    for root, dirs, files in os.walk('.'):
        if '__pycache__' in dirs:
            cache_dirs.append(os.path.join(root, '__pycache__'))

    if cache_dirs:
        console.print(f"⚠️  [bold red]警告：發現 {len(cache_dirs)} 個快取目錄殘留[/bold red]")
        for cache_dir in cache_dirs:
            console.print(f"   🗂️  {cache_dir}")
    else:
        console.print("✅ 快取清理確認：無殘留快取")

# --- 完整 Logging Setup ---
logging.basicConfig(
    level="ERROR",  # v9.96: Only show errors in terminal
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True)]
)
log = logging.getLogger("rich")

# --- Flask App ---
# [v14.8] Serve Dashboard from dist folder
# [v19.6] Robust JSON Serialization for Datetime
from datetime import datetime
class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.strftime('%Y-%m-%d %H:%M:%S')
        return super().default(obj)

flask_app = Flask(__name__, static_folder="dashboard/dist", static_url_path="/")
flask_app.json_encoder = CustomJSONEncoder # For Flask < 2.2
flask_app.json.cls = CustomJSONEncoder # For Flask >= 2.2
CORS(flask_app)
orchestrator: BatchOrchestrator = None
api_client: OpenAI = None
model_name_global = ""
IMAGE_LOOKUP_CACHE = {}
OUTPUT_ROOT = Path(os.environ.get("OCR_OUTPUT_DIR", r"D:\00_商化\00_已OCR照片"))
SOURCE_ROOT = Path(os.environ.get("OCR_SOURCE_ROOT", r"D:\00_商化\00_未整理商化照片"))
DRIVE_MANIFEST_DIR = OUTPUT_ROOT / "_drive_upload"
AUDIT_DIR = OUTPUT_ROOT / "_ocr_audit"
MANUAL_CORRECTIONS_PATH = AUDIT_DIR / "manual_corrections.csv"
MANUAL_RULES_PATH = AUDIT_DIR / "manual_learning_rules.csv"
MANUAL_RULES_CACHE = {"mtime_ns": None, "section": "", "count": 0}
OVERALL_PROGRESS_CACHE = {"mtime": None, "data": None}

def should_save_manual_learning_rule(data: dict) -> bool:
    """Only an explicit checkbox plus a non-empty reusable hint creates a rule."""
    return bool(data.get("learn_rule")) and bool(str(data.get("rule_hint") or "").strip())

REVIEW_REASON_LABELS = {
    "current_year_missing_compare_symbol": "2026+ 缺少 ↑/↓/✓ 比價符號",
    "current_year_missing_price": "2026+ 缺少店內價格",
    "unknown_marker": "檔名仍有？待確認",
    "name_contains_無型號": "檔名為無型號",
    "name_contains_型號未辨識": "型號未辨識",
    "name_contains_無價格": "無價格",
    "name_contains_不合格": "不合格照片",
    "name_contains_照片不清楚": "照片不清楚",
    "name_contains_照不清楚": "照不清楚",
    "name_contains_沒有規格": "沒有規格牌",
    "name_contains_沒有價格": "沒有價格牌",
    "name_contains_黑屏": "黑屏",
    "oversize": "檔案過大",
}


def _append_csv_row(path: Path, fieldnames: list[str], row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow({name: row.get(name, "") for name in fieldnames})


def _safe_int(value, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return default


def _read_csv_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    except Exception as exc:
        log.debug("read csv failed %s: %s", path, exc)
        return []


def load_manual_rule_prompt_section(max_rules: int = 12, max_chars: int = 2400) -> tuple[str, int]:
    """Hot-load human-confirmed reusable rules without pretending to train weights."""
    try:
        mtime_ns = MANUAL_RULES_PATH.stat().st_mtime_ns if MANUAL_RULES_PATH.exists() else 0
    except OSError:
        mtime_ns = 0
    if MANUAL_RULES_CACHE.get("mtime_ns") == mtime_ns:
        return MANUAL_RULES_CACHE.get("section", ""), int(MANUAL_RULES_CACHE.get("count", 0))

    rows = _read_csv_rows(MANUAL_RULES_PATH)
    selected: list[str] = []
    seen: set[str] = set()
    for row in reversed(rows):
        hint = re.sub(r"\s+", " ", str(row.get("rule_hint") or "")).strip()
        # A filename or a bare corrected value is an example, not a reusable rule.
        if len(hint) < 8 or hint.lower() in {"none", "null"}:
            continue
        key = hint.casefold()
        if key in seen:
            continue
        seen.add(key)
        selected.append(hint[:320])
        if len(selected) >= max_rules:
            break
    selected.reverse()

    if selected:
        body = "\n".join(f"- {hint}" for hint in selected)
        section = (
            "\n\n## 人工確認的可重用規則（熱載入）\n"
            "以下只補充通用判斷，不是任何照片的預設答案。每條規則仍須由目前圖片證據支持；"
            "不得把範例檔名、型號或價格借給其他照片。\n"
            f"{body}"
        )[:max_chars]
    else:
        section = ""

    MANUAL_RULES_CACHE.update({"mtime_ns": mtime_ns, "section": section, "count": len(selected)})
    return section, len(selected)
def _folder_key(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return str(Path(text).resolve()).lower()
    except Exception:
        return text.lower()


def _relative_to_source(folder: str) -> str:
    if not folder:
        return ""
    try:
        path = Path(folder).resolve()
        return str(path.relative_to(SOURCE_ROOT.resolve()))
    except Exception:
        return folder


def _latest_mtime(paths: list[Path]):
    mtimes = []
    for path in paths:
        try:
            if path.exists():
                mtimes.append(path.stat().st_mtime)
        except OSError:
            pass
    return max(mtimes) if mtimes else None


def _load_base_overall_progress() -> dict:
    discovery_path = AUDIT_DIR / "folder_discovery.csv"
    summary_path = AUDIT_DIR / "folder_summary.csv"
    rerun_summary_paths = sorted(AUDIT_DIR.glob("missing_result_rerun_summary*.csv"))
    cache_paths = [discovery_path, summary_path, *rerun_summary_paths]
    latest_mtime = _latest_mtime(cache_paths)
    cached = OVERALL_PROGRESS_CACHE.get("data")
    if cached is not None and OVERALL_PROGRESS_CACHE.get("mtime") == latest_mtime:
        return json.loads(json.dumps(cached))

    folders: dict[str, dict] = {}

    discovery_rows = _read_csv_rows(discovery_path)
    summary_rows = _read_csv_rows(summary_path)

    for row in discovery_rows:
        folder = row.get("folder", "")
        key = _folder_key(folder)
        if not key:
            continue
        image_count = _safe_int(row.get("image_count"))
        folders[key] = {
            "folder": folder,
            "relative_folder": _relative_to_source(folder),
            "period": row.get("period", ""),
            "image_count": image_count,
            "processed": 0,
            "ready": 0,
            "status": "pending",
        }

    for row in summary_rows:
        folder = row.get("folder", "")
        key = _folder_key(folder)
        if not key:
            continue
        image_count = _safe_int(row.get("image_count"))
        entry = folders.setdefault(key, {
            "folder": folder,
            "relative_folder": _relative_to_source(folder),
            "period": row.get("period", ""),
            "image_count": image_count,
            "processed": 0,
            "ready": 0,
            "status": "pending",
        })
        if image_count:
            entry["image_count"] = max(_safe_int(entry.get("image_count")), image_count)
        entry["period"] = entry.get("period") or row.get("period", "")
        entry["status"] = row.get("status", entry.get("status", "pending"))
        processed = max(
            _safe_int(row.get("processed")),
            _safe_int(row.get("copied_count")),
            _safe_int(row.get("ready")),
            _safe_int(row.get("success")),
        )
        if row.get("status") in {"copied", "skipped_existing"} and _safe_int(row.get("missing_result")) == 0:
            processed = max(processed, entry["image_count"])
        entry["processed"] = max(_safe_int(entry.get("processed")), processed)
        entry["ready"] = max(_safe_int(entry.get("ready")), _safe_int(row.get("ready")), _safe_int(row.get("copied_count")))

    for path in rerun_summary_paths:
        for row in _read_csv_rows(path):
            folder = row.get("folder", "")
            key = _folder_key(folder)
            if not key:
                continue
            entry = folders.setdefault(key, {
                "folder": folder,
                "relative_folder": _relative_to_source(folder),
                "period": row.get("period", ""),
                "image_count": _safe_int(row.get("records")),
                "processed": 0,
                "ready": 0,
                "status": "rerun",
            })
            records = _safe_int(row.get("records"))
            if records:
                entry["image_count"] = max(_safe_int(entry.get("image_count")), records)
            entry["period"] = entry.get("period") or row.get("period", "")
            processed = max(
                _safe_int(row.get("processed")),
                _safe_int(row.get("copied")),
                _safe_int(row.get("ready")),
                _safe_int(row.get("records")),
            )
            entry["processed"] = max(_safe_int(entry.get("processed")), processed)
            entry["ready"] = max(_safe_int(entry.get("ready")), _safe_int(row.get("ready")), _safe_int(row.get("copied")))
            entry["status"] = "rerun_complete"

    total_images = sum(_safe_int(item.get("image_count")) for item in folders.values())
    total_folders = len(folders)
    base = {
        "source_root": str(SOURCE_ROOT),
        "output_dir": str(OUTPUT_ROOT),
        "audit_dir": str(AUDIT_DIR),
        "total_folders": total_folders,
        "total_images": total_images,
        "folders": list(folders.values()),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    OVERALL_PROGRESS_CACHE["mtime"] = latest_mtime
    OVERALL_PROGRESS_CACHE["data"] = base
    return json.loads(json.dumps(base))


def build_overall_progress(current_folder=None, current_stats=None) -> dict:
    progress = _load_base_overall_progress()
    folders = progress.pop("folders", [])
    current_key = _folder_key(current_folder)
    current_stats = current_stats or {}

    for item in folders:
        if current_key and _folder_key(item.get("folder")) == current_key:
            current_total = _safe_int(current_stats.get("total"))
            current_processed = _safe_int(current_stats.get("processed"))
            if current_total:
                item["image_count"] = max(_safe_int(item.get("image_count")), current_total)
            item["processed"] = max(_safe_int(item.get("processed")), current_processed)
            item["ready"] = max(_safe_int(item.get("ready")), _safe_int(current_stats.get("success")))
            item["status"] = "active" if getattr(orchestrator, "is_running", False) else "active_idle"

    total_images = sum(_safe_int(item.get("image_count")) for item in folders)
    processed_images = sum(
        min(_safe_int(item.get("processed")), _safe_int(item.get("image_count")) or _safe_int(item.get("processed")))
        for item in folders
    )
    ready_images = sum(
        min(_safe_int(item.get("ready")), _safe_int(item.get("image_count")) or _safe_int(item.get("ready")))
        for item in folders
    )
    completed_folders = sum(
        1 for item in folders
        if _safe_int(item.get("image_count")) > 0 and _safe_int(item.get("processed")) >= _safe_int(item.get("image_count"))
    )
    blocked_statuses = {"blocked", "skipped_blocked", "error"}
    next_pending = next(
        (
            item for item in folders
            if item.get("status") not in blocked_statuses
            and _safe_int(item.get("processed")) < _safe_int(item.get("image_count"))
        ),
        None
    )
    next_blocked = next(
        (
            item for item in folders
            if item.get("status") in blocked_statuses
            and _safe_int(item.get("processed")) < _safe_int(item.get("image_count"))
        ),
        None
    )

    current_folder_info = next((item for item in folders if current_key and _folder_key(item.get("folder")) == current_key), None)
    return {
        **progress,
        "total_folders": len(folders),
        "completed_folders": completed_folders,
        "remaining_folders": max(len(folders) - completed_folders, 0),
        "total_images": total_images,
        "processed_images": processed_images,
        "ready_images": ready_images,
        "remaining_images": max(total_images - processed_images, 0),
        "percent": round((processed_images / total_images) * 100, 2) if total_images else 0,
        "current_folder": current_folder_info,
        "next_pending_folder": next_pending,
        "next_blocked_folder": next_blocked,
    }


def _parse_review_filename(file_name: str) -> dict:
    stem = Path(file_name).stem
    parts = stem.split("-")
    period = ""
    view_type = ""
    model = ""
    price = ""
    serial = parts[-1] if parts else ""

    for part in parts:
        if re.fullmatch(r"20\d{4}", part):
            period = part
            break

    for idx, part in enumerate(parts):
        if part in {"單機", "遠景"}:
            view_type = part
            if part == "單機":
                if idx + 1 < len(parts):
                    candidate_model = parts[idx + 1]
                    if candidate_model not in {"無型號", "型號未辨識"}:
                        model = candidate_model
                if idx + 2 < len(parts):
                    price = parts[idx + 2]
            break

    if not price:
        match = re.search(r"([↑↓✓？?]?[\uff04$]\d+|無價格)", stem)
        if match:
            price = match.group(1)

    return {
        "period": period,
        "year": period[:4] if period else "",
        "view_type": view_type,
        "model": model,
        "price": price,
        "serial": serial,
    }


def _review_reason_labels(reasons: str) -> str:
    labels = []
    for reason in str(reasons or "").split(";"):
        reason = reason.strip()
        if not reason:
            continue
        labels.append(REVIEW_REASON_LABELS.get(reason, reason))
    return "；".join(labels)


def _suggest_review_action(row: dict) -> str:
    reasons = str(row.get("reasons") or "")
    parsed = _parse_review_filename(row.get("file_name") or "")
    file_name = row.get("file_name") or ""
    if "型號未辨識" in file_name or "無型號" in file_name:
        return "補型號、改遠景，或按重跑"
    if "無價格" in file_name or "current_year_missing_price" in reasons:
        return "補店內價格，或按重跑"
    if "current_year_missing_compare_symbol" in reasons:
        return "補 ↑/↓/✓，或查價後重建檔名"
    if parsed.get("model"):
        return "確認價格與比價符號"
    return "人工確認"


def _load_review_rows(year: str = "2026", reason: str = "", limit: int = 300) -> tuple[list[dict], dict]:
    review_path = DRIVE_MANIFEST_DIR / "drive_upload_review_required.csv"
    if not review_path.exists():
        return [], {"error": f"找不到待審清單: {review_path}"}

    items: list[dict] = []
    reason_counts: dict[str, int] = {}
    year_counts: dict[str, int] = {}
    with review_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            row_year = (row.get("year") or "")[:4]
            row_reasons = row.get("reasons") or ""
            year_counts[row_year or "_unknown"] = year_counts.get(row_year or "_unknown", 0) + 1
            for one_reason in row_reasons.split(";"):
                one_reason = one_reason.strip()
                if one_reason:
                    reason_counts[one_reason] = reason_counts.get(one_reason, 0) + 1

            if year and row_year != year:
                continue
            if reason and reason not in row_reasons:
                continue

            parsed = _parse_review_filename(row.get("file_name") or "")
            enriched = {
                **row,
                **{k: parsed.get(k, "") for k in ("view_type", "model", "price", "serial")},
                "reason_labels": _review_reason_labels(row_reasons),
                "suggested_action": _suggest_review_action(row),
            }
            items.append(enriched)

    items.sort(
        key=lambda item: (
            -int(item.get("period") or 0) if str(item.get("period") or "").isdigit() else 0,
            item.get("file_name", "").casefold(),
        )
    )
    filtered_count = len(items)
    if limit > 0:
        items = items[:limit]
    return items, {
        "review_path": str(review_path),
        "filtered_count": filtered_count,
        "year_counts": year_counts,
        "reason_counts": dict(sorted(reason_counts.items(), key=lambda pair: pair[1], reverse=True)),
    }

# [v17.09] Structured Output Schema (B-Mode)
SAMSUNG_AUDIT_SCHEMA = {
  "type": "json_schema",
  "json_schema": {
    "name": "samsung_audit",
    "strict": True,
    "schema": {
      "type": "object",
      "additionalProperties": False,
      "properties": {
        # [v17.14 Fix] Strict Pattern to ban English explanations
        # Allowed: Chinese, digits, punctuation, and valid model chars (including FollowMe mixed case)
        "desc": {
            "type": "string",
            "minLength": 60,
            "maxLength": 200,
            "pattern": "^[\\u4e00-\\u9fff0-9A-Za-z\\s，。、「」『』（）()：:；;！!？?\\-\\/,$]*$"
        },
        "data": {
          "type": "object",
          "additionalProperties": False,
          "properties": {
            "view_type": { "type": "string", "enum": ["遠景", "單機"] },
            "screen_status": { "type": "string", "enum": ["顯示畫面", "黑屏", "藍屏", "無"] },
            "quality_issue": { "type": "string", "enum": ["無", "不合格-照不清楚", "不合格-沒有規格牌", "不合格-沒有價格牌", "不合格-沒有規格和價格牌"] },
            "model": { "type": ["string", "null"] },
            "price": { "type": ["string", "null"] }
          },
          "required": ["view_type", "screen_status", "quality_issue", "model", "price"]
        }
      },
      "required": ["desc", "data"]
    }
  }
}

# ... (Original Helper Functions) ...


def clamp_coordinates(x_pct, y_pct, w_pct, h_pct, img_w, img_h):
    """
    Ensure coordinates are within valid bounds and prevent zero-size crops.
    Returns (left, top, right, bottom) in pixels, or None if invalid.
    """
    try:
        # 1. Clamp percentages to 0-100
        x_pct = max(0.0, min(x_pct, 100.0))
        y_pct = max(0.0, min(y_pct, 100.0))
        w_pct = max(0.0, min(w_pct, 100.0 - x_pct)) # Ensure width fits
        h_pct = max(0.0, min(h_pct, 100.0 - y_pct)) # Ensure height fits

        # 2. Convert to pixels
        left = int((x_pct / 100.0) * img_w)
        top = int((y_pct / 100.0) * img_h)
        width = int((w_pct / 100.0) * img_w)
        height = int((h_pct / 100.0) * img_h)

        right = left + width
        bottom = top + height

        # 3. Minimum Check (e.g., 32x32 pixels)
        if width < 32 or height < 32:
            log.warning(f"Crop too small: {width}x{height}")
            return None

        return (left, top, right, bottom)
    except Exception as e:
        log.error(f"Clamp error: {e}")
        return None

def sanitize_json(json_str):
    """
    Aggressively cleans and repairs JSON string:
    1. Removes Markdown code blocks.
    2. Removes lines starting with // (comments).
    3. Removes trailing commas before } or ].
    4. Fixes truncated JSON by closing open brackets/braces.
    5. Handles truncated price field specifically.
    """
    import re
    if not json_str: return ""

    # 1. Strip Markdown
    json_str = json_str.replace("```json", "").replace("```", "").strip()

    # 2. Remove comments (// ...)
    lines = [line for line in json_str.split('\n') if not line.strip().startswith('//')]
    json_str = '\n'.join(lines)

    # 3. Remove trailing commas
    json_str = re.sub(r',\s*}', '}', json_str)
    json_str = re.sub(r',\s*\]', ']', json_str)

    # 4. Fix truncated JSON - specifically handle "price": truncation
    # Pattern: ends with "price": or "price": followed by incomplete value
    if re.search(r'"price"\s*:\s*$', json_str):
        # Price field has no value at all - add empty string
        json_str += '""}'
    elif re.search(r'"price"\s*:\s*"[^"]*$', json_str):
        # Price field has unclosed string - close it
        json_str += '"}'
    elif re.search(r'"price"\s*:\s*\d+$', json_str):
        # Price field has number but no closing brace
        json_str += '}'

    # 5. General bracket/brace closing
    open_braces = json_str.count('{') - json_str.count('}')
    open_brackets = json_str.count('[') - json_str.count(']')

    if open_braces > 0:
        # Check if we're in the middle of a string value
        last_colon = json_str.rfind(':')
        if last_colon > 0:
            after_colon = json_str[last_colon+1:].strip()
            # If value started but not closed
            if after_colon.startswith('"') and after_colon.count('"') % 2 == 1:
                json_str += '"'

        # Close any remaining open braces
        json_str += '}' * open_braces

    if open_brackets > 0:
        json_str += ']' * open_brackets

    return json_str

# --- Helper: OpenCC Global ---
try:
    from opencc import OpenCC
    cc = OpenCC('s2t')
    cc_t2s = OpenCC('t2s')  # 反向轉換,用於偵測殘留簡體
except ImportError:
    cc = None
    cc_t2s = None
    log.warning("OpenCC not installed.")

# 台灣慣用詞替換表(簡體/通用 → 台灣繁體)
TAIWAN_WORD_MAP = [
    ("電器行", "3C賣場"),
    ("电器行", "3C賣場"),
    ("家電賣場", "3C賣場"),
    ("家电卖场", "3C賣場"),
    ("臺", "台"),          # 一臺 -> 一台
    ("裏", "裡"),          # OpenCC s2t 用「裏」,台灣慣用「裡」
    ("屏幕", "螢幕"),
    ("顯示器", "螢幕"),    # 簡體「显示器」經 OpenCC 轉成「顯示器」,再統一為「螢幕」
    ("信息", "資訊"),
    ("图片", "照片"),
    ("圖片", "照片"),
    ("中间", "中央"),
    ("中間", "中央"),
    ("左边", "左側"),
    ("左邊", "左側"),
    ("右边", "右側"),
    ("右邊", "右側"),
    ("视频", "影片"),
    ("視頻", "影片"),
    ("软件", "軟體"),
    ("軟件", "軟體"),
    ("网络", "網路"),
    ("網絡", "網路"),
    ("菜单", "選單"),
    ("菜單", "選單"),
    ("鼠标", "滑鼠"),
    ("打印", "列印"),
    ("默认", "預設"),
    ("数据", "資料"),
    ("數據", "資料"),
    ("质量", "品質"),
    ("用户", "使用者"),
    ("用戶", "使用者"),
    ("点击", "點擊"),
    ("链接", "連結"),
    ("访问", "存取"),
    ("支持", "支援"),
    ("程序", "程式"),
    ("服务器", "伺服器"),
    ("宽带", "寬頻"),
    ("文档", "文件"),
    ("文檔", "文件"),
    ("硬盘", "硬碟"),
    ("光盘", "光碟"),
    ("保存", "儲存"),
    ("发现", "發現"),
    ("发現", "發現"),
    ("型号", "型號"),
    ("价格", "價格"),
    ("标签", "標籤"),
    ("货架", "貨架"),
    ("店里", "店裡"),
    ("里面", "裡面"),
    ("干净", "乾淨"),
    ("仔细", "仔細"),
    ("说明", "說明"),
    ("资料", "資料"),
]

# 高置信度「簡體專用字元」黑名單
# 這些字是簡化字,現代繁體中文幾乎不會使用(繁體有完全不同的對應字)。
# 用於在 OpenCC 轉換後做殘留檢查,若仍出現表示含未轉換的簡體字。
SIMPLIFIED_ONLY_CHARS = set(
    "们这吗么对时过关东问间办动场岁飞风个给将讲结进经开类两马没难气区实数万闻务显写学压亚样业应营优与语远运则种钟专观汉说见车电论读买卖产网练纪维绿编讯设计讨训诉评证识试诚详误谁临为丽举义习乡书乱争于亏仓从仅仆仑仪价众会伟传伤伦伪体余佣侠侦侧侨俭债偿储兑党兴兽册军农冲况冻净凉减"
)

def to_tc(text):
    if not text:
        return text
    if cc:
        text = cc.convert(text)
    # 台灣 3C 用詞偏好
    for src, dst in TAIWAN_WORD_MAP:
        text = text.replace(src, dst)
    return text

def _detect_simplified_residual(text):
    """檢查文字中是否殘留簡體專用字元,回傳違規字元清單。"""
    if not text:
        return []
    return [ch for ch in text if ch in SIMPLIFIED_ONLY_CHARS]

def ensure_traditional_chinese(text, source_label=""):
    """在顯示 LLM 自言自語前,確保文字為台灣繁體中文。

    流程:
    1. 用 OpenCC s2t 轉換簡體 → 繁體。
    2. 套用台灣慣用詞替換。
    3. 檢查殘留簡體專用字元,若仍有違規則記錄警告並標記。

    回傳 (converted_text, violation_chars)。
    violation_chars 為殘留的簡體字元清單(空清單表示通過)。
    """
    if not text:
        return text, []
    original = text
    converted = to_tc(text)
    violations = _detect_simplified_residual(converted)
    if violations:
        # 記錄警告,協助除錯
        unique_violations = sorted(set(violations))
        warning = f"[繁中檢查] {'來源:' + source_label if source_label else ''} 偵測到殘留簡體字元: {' '.join(unique_violations)}"
        try:
            log.warning(warning)
        except Exception:
            pass
        # 嘗試把殘留簡體字逐一用 OpenCC 強制轉換
        if cc:
            for ch in unique_violations:
                converted_ch = cc.convert(ch)
                if converted_ch != ch:
                    converted = converted.replace(ch, converted_ch)
            # 再次檢查
            violations = _detect_simplified_residual(converted)
    return converted, violations

# --- Main Processor Function (Single Stage v6.8) ---

def _detect_repetition(text: str) -> bool:
    """
    [v18.82] 簡易的重複語句偵測器 (Watchdog)
    如果發現長句子連續出現 2 次以上，判定為陷入迴圈。
    """
    if not text:
        return False

    # Structured evidence legitimately repeats keys such as same_subject,
    # cue, and strength once per observed physical cue.  The watchdog exists
    # to catch looping natural language, so inspect only narration (or legacy
    # prose before the JSON object), never the machine-readable structure.
    source_text = str(text)
    watch_text = source_text
    try:
        decoded = json.loads(source_text.strip().replace("```json", "").replace("```", ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        decoded = None
    if isinstance(decoded, dict):
        watch_text = str(decoded.get("narration") or decoded.get("desc") or "")
    elif "{" in source_text:
        watch_text = source_text.split("{", 1)[0].strip()
    if not watch_text:
        return False

    # Catch single-line degeneration such as "1000000:1" repeated hundreds of
    # times. The old line-based watchdog missed this because there were no
    # newlines.
    token_matches = re.findall(r"[A-Za-z0-9]+(?::[A-Za-z0-9]+)?|[\u4e00-\u9fff]{2,}", watch_text)
    if token_matches:
        from collections import Counter
        recent_tokens = [tok for tok in token_matches[-120:] if len(tok) >= 4]
        token_counts = Counter(recent_tokens)
        for token, count in token_counts.items():
            if count >= 12:
                print(f"[Watchdog] repetitive token detected: {token[:24]}... (x{count})")
                return True

    compact_tail = re.sub(r"\s+", "", watch_text)[-1600:]
    for window_size in (4, 5, 6, 8, 10, 12, 16, 20, 24, 32, 40):
        if len(compact_tail) >= window_size * 8:
            chunk = compact_tail[-window_size:]
            if chunk and compact_tail.endswith(chunk * 6):
                print(f"[Watchdog] repetitive tail detected: {chunk[:24]}...")
                return True

    # 1. 簡單的逐行檢查
    lines = [L.strip() for L in watch_text.split('\n') if len(L.strip()) > 15] # 忽略太短的行
    if len(lines) < 2: return False # [Modified] 至少要有2行才可能重複

    # 檢查最後 15 行是否有重複
    recent_lines = lines[-15:]
    from collections import Counter
    counts = Counter(recent_lines)

    # 如果任何一句話出現超過 2 次 [Modified]
    for line, count in counts.items():
        if count >= 2:
            # 進一步確認該句子長度夠長 (避免誤判 "..." 或 "思考中")
            if len(line) > 10:
                print(f"[Watchdog] 偵測到重複語句: {line[:20]}... (x{count})")
                return True

    # 2. 暴力 N-gram 檢查 (針對没換行的長段落)
    # 檢查長度為 30 的 substring 是否重複出現 3 次以上 [Modified]
    if len(watch_text) > 200:
        window_size = 30
        threshold = 3
        # 簡易採樣檢查
        for i in range(0, len(watch_text) - window_size, 50): # 步長 50
            sub = watch_text[i : i+window_size]
            if watch_text.count(sub) >= threshold:
                print(f"[Watchdog] 偵測到段落重複: {sub[:20]}... (x{watch_text.count(sub)})")
                return True

    return False


# === [v19.10] OpenCode Go (Anthropic Messages API) helper ===

def _convert_to_anthropic_messages(messages, max_image_px=2560):
    """Convert OpenAI-format messages to Anthropic Messages format. Optionally resize images."""
    import io as _io
    from PIL import Image as PILImage
    anthropic_msgs = []
    system_text = ""
    for msg in messages:
        role = msg.get("role", "user")
        if role == "system":
            system_text = str(msg.get("content", ""))
            continue
        if role == "user":
            content_blocks = []
            raw_content = msg.get("content", "")
            if isinstance(raw_content, list):
                for item in raw_content:
                    if item.get("type") == "text":
                        content_blocks.append({"type": "text", "text": item.get("text", "")})
                    elif item.get("type") == "image_url":
                        data_uri = item.get("image_url", {}).get("url", "")
                        parts = data_uri.split(",", 1)
                        b64 = parts[1] if len(parts) == 2 else data_uri
                        # Resize image to reduce payload size
                        if max_image_px:
                            try:
                                img_data = base64.b64decode(b64)
                                img = PILImage.open(_io.BytesIO(img_data)).convert('RGB')
                                w, h = img.size
                                if max(w, h) > max_image_px:
                                    ratio = max_image_px / max(w, h)
                                    img = img.resize((int(w*ratio), int(h*ratio)), PILImage.LANCZOS)
                                buf = _io.BytesIO()
                                img.save(buf, format='JPEG', quality=92, optimize=True)
                                b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
                            except Exception:
                                pass  # If resize fails, use original
                        content_blocks.append({
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}
                        })
            else:
                content_blocks.append({"type": "text", "text": str(raw_content)})
            anthropic_msgs.append({"role": "user", "content": content_blocks})
        elif role == "assistant":
            anthropic_msgs.append({"role": "assistant", "content": str(msg.get("content", ""))})
    return anthropic_msgs, system_text


EVIDENCE_KEYS = {"complete_screen_count", "unique_main", "label_ownership", "followme_physical_evidence"}
CORE_JSON_KEYS = {"view_type", "screen_status", "quality_issue", "model", "price", "category"}
PRESENTATION_JSON_KEYS = {"narration", "desc"}


def _json_pairs_no_duplicates(pairs):
    result = {}
    duplicates = []
    for key, value in pairs:
        if key in result:
            duplicates.append(key)
        result[key] = value
    if duplicates:
        raise ValueError("duplicate_json_keys:" + ",".join(sorted(set(duplicates))))
    return result


def _extract_balanced_json_objects(text):
    """Return every valid balanced JSON object in response order."""
    if not text:
        return []
    decoder = json.JSONDecoder(object_pairs_hook=_json_pairs_no_duplicates)
    objects = []
    start = None
    depth = 0
    in_string = False
    escape = False
    for index, ch in enumerate(str(text)):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{" and depth == 0:
            start = index
            depth = 1
        elif ch == "{" and depth:
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                raw = str(text)[start:index + 1]
                try:
                    objects.append({"raw": raw, "value": decoder.decode(raw)})
                except Exception as exc:
                    objects.append({"raw": raw, "value": None, "error": str(exc)[:200]})
                start = None
    return objects


def _merge_v1945_json_objects(text):
    objects = _extract_balanced_json_objects(text)
    valid = [item for item in objects if isinstance(item.get("value"), dict)]
    if not valid:
        return None, objects, "none", "no_valid_json_object"
    if len(valid) == 1:
        value = dict(valid[0]["value"])
        if "evidence" in value:
            nested_evidence = value.pop("evidence")
            if not isinstance(nested_evidence, dict):
                return None, objects, "rejected", "evidence_container_not_object"
            nested_unknown = set(nested_evidence) - EVIDENCE_KEYS
            if nested_unknown:
                return None, objects, "rejected", "evidence_container_unknown_keys:" + ",".join(sorted(nested_unknown))
            duplicate_evidence = set(value) & set(nested_evidence)
            if duplicate_evidence:
                return None, objects, "rejected", "duplicate_nested_evidence_keys:" + ",".join(sorted(duplicate_evidence))
            value.update(nested_evidence)
        unknown = set(value) - CORE_JSON_KEYS - EVIDENCE_KEYS - PRESENTATION_JSON_KEYS
        if unknown:
            return None, objects, "rejected", "unknown_keys:" + ",".join(sorted(unknown))
        return value, objects, "single_object", ""
    core = [item for item in valid if set(item["value"]) & CORE_JSON_KEYS]
    evidence_only = [item for item in valid if set(item["value"]) and not (set(item["value"]) & CORE_JSON_KEYS)]
    if len(core) != 1 or len(evidence_only) > 1 or len(core) + len(evidence_only) != len(valid):
        return None, objects, "rejected", "multiple_core_or_unknown_object"
    merged = dict(core[0]["value"])
    evidence = evidence_only[0]["value"] if evidence_only else {}
    if set(evidence) - EVIDENCE_KEYS - PRESENTATION_JSON_KEYS:
        return None, objects, "rejected", "evidence_object_has_unknown_keys"
    if set(merged) & set(evidence):
        return None, objects, "rejected", "duplicate_core_evidence_keys"
    merged.update(evidence)
    return merged, objects, "disjoint_core_evidence", ""


def _extract_balanced_json(text):
    """Backward-compatible single-object accessor."""
    parsed, objects, _, _ = _merge_v1945_json_objects(text)
    if parsed is None:
        return None
    return json.dumps(parsed, ensure_ascii=False)


def _fallback_extract_fields(text, valid_models=None):
    """[OCG-v2.4] 當模型未輸出 JSON(僅輸出 Markdown/純文字)時,從文字中提取欄位。

    針對 OpenCode Go 模型偶發不遵循「獨白+JSON」格式、改輸出 Markdown 長文的情況,
    用 regex 從文字中搶救型號、價格、視角等資訊,避免 28% 的照片型號價格遺失。

    Args:
        text: 模型回應的純文字(Markdown 或自由格式)。
        valid_models: 型號表清單,用於 fuzzy match 提升型號準確度。

    Returns:
        dict 或 None。若無法提取任何關鍵欄位則回傳 None。
    """
    if not text:
        return None
    result = {
        "view_type": None, "category": None, "model": None,
        "price": None, "screen_status": None, "quality_issue": None,
        "black_screen": False, "thinking": "", "extraction_source": "fallback_regex",
    }
    text_tc = to_tc(text)

    # 1. 視角判斷
    if "遠景" in text_tc:
        result["view_type"] = "遠景"
        result["category"] = "遠景"
    elif "FollowMe" in text_tc or "Followme" in text_tc or "follow me" in text_tc.lower():
        result["view_type"] = "單機"
        result["category"] = "FollowMe"
    elif "單機" in text_tc:
        result["view_type"] = "單機"
        result["category"] = "單機"

    # 2. 型號提取(多種模式)
    model_candidates = []
    # 模式 A:「型號：XXX」「型號:XXX」後的值,容忍 **、空格、引號
    for m in re.finditer(r'型號\s*[：:]\s*\**\s*[*]*([A-Za-z][A-Za-z0-9\-]+)', text_tc):
        model_candidates.append(m.group(1).strip('*'))
    # 模式 B:直接找 Samsung 型號格式 S + 大寫字母數字(至少 8 碼)
    for m in re.finditer(r'\b(S\d[A-Z]{1,3}\d?[A-Z0-9]{5,15})\b', text_tc):
        model_candidates.append(m.group(1))
    # 模式 C:從型號表 fuzzy match
    if valid_models:
        for vm in valid_models:
            if vm and vm in text_tc:
                model_candidates.append(vm)

    # 去重、過濾太短、選最長(最完整)的
    seen = set()
    unique_models = []
    for mc in model_candidates:
        mc_clean = mc.strip().rstrip('()').strip()
        if len(mc_clean) >= 8 and mc_clean not in seen:
            seen.add(mc_clean)
            unique_models.append(mc_clean)
    if unique_models:
        # 偏好與型號表完全吻合的;其次選最長的
        if valid_models:
            exact = [m for m in unique_models if m in valid_models]
            if exact:
                result["model"] = max(exact, key=len)
            else:
                result["model"] = max(unique_models, key=len)
        else:
            result["model"] = max(unique_models, key=len)

    # 3. 價格提取
    price_candidates = []
    clearance_context = has_clearance_price_context(text_tc)
    min_candidate_price = 1000 if clearance_context else 2000
    # 模式 A:「**價格**：**4,990**」「售價:4990」——容忍 Markdown 星號、引號、空白
    for m in re.finditer(r'(?:價格|售價|現金價|特價|標價|促銷價|出清價|展示出清價|手寫價|清倉價)\**\s*[：:]\s*\**\s*[「「"]*([\d,]+)\**\s*[」」"]*', text_tc):
        val = m.group(1).replace(',', '').replace('*', '').strip()
        if val.isdigit():
            price_candidates.append(int(val))
    # 模式 B:帶千分位逗號的 4-6 位數字(如 4,990、17,990),排除月付/分期小額
    for m in re.finditer(r'(?<![\d])(\d{1,3}(?:,\d{3}){1,2})(?![\d])', text_tc):
        val = int(m.group(1).replace(',', ''))
        if val >= min_candidate_price:  # 出清手寫價可低於一般商品售價門檻
            price_candidates.append(val)
    # 模式 C:獨立 4-5 位無逗號數字
    for m in re.finditer(r'(?<![\d\w])(\d{4,5})(?![\d])', text_tc):
        val = int(m.group(1))
        if val >= min_candidate_price:
            price_candidates.append(val)
    if price_candidates:
        # 選出現次數最多或最小的合理價格
        from collections import Counter
        cnt = Counter(price_candidates)
        result["price"] = cnt.most_common(1)[0][0]

    # Free-text fallback cannot prove that a model and a price belong to the
    # same physical label. Preserve scene/quality clues, but force a structured
    # retry instead of manufacturing a cross-label pair.
    if result.get("model") or result.get("price"):
        result["fallback_untrusted_model"] = result.get("model")
        result["fallback_untrusted_price"] = result.get("price")
        result["model"] = None
        result["price"] = None
    result["requires_structured_retry"] = True

    # 4. 螢幕狀態
    if "黑屏" in text_tc or "黑畫面" in text_tc or "沒亮" in text_tc:
        result["screen_status"] = "黑屏"
        result["black_screen"] = True
    elif "正常" in text_tc:
        result["screen_status"] = "正常"

    # 5. 品質問題
    if "沒有規格" in text_tc or "沒有價格牌" in text_tc or "找不到" in text_tc and "標籤" in text_tc:
        result["quality_issue"] = "沒有規格和價格牌"
    elif "拍不清楚" in text_tc or "糊" in text_tc or "反光" in text_tc:
        result["quality_issue"] = "拍不清楚"

    # 判斷是否搶救到任何關鍵欄位
    has_key_info = any([result["model"], result["price"] is not None, result["view_type"]])
    if not has_key_info:
        return None
    return result


# [OCG-v2.5] 保守型號補抓:JSON 成功但 model=null 時,從自言自語低風險搶救型號
# 風險控制:只在三個條件「全部」成立時才補,缺一不可
_RISKY_CONTEXT_WORDS = ["模糊", "不清楚", "看不到", "看不清", "糊掉", "反光", "被遮", "無法判讀", "無法讀", "讀不到"]
# 明確可讀詞必須是「動詞/形容詞」,表示模型確實從標籤讀到字;單獨「型號」不算(太泛)
_CLEAR_CONTEXT_WORDS = ["清楚", "清晰", "印著", "寫著", "標示", "貼紙", "規格牌"]
# 不確定詞:模型用這些詞表示在猜測,不是確實讀到
_HEDGE_WORDS = ["好像", "似乎", "可能", "應該是", "好像是", "估計", "猜", "大概", "也許"]


def _rescue_model_conservative(text, valid_models):
    """JSON 成功但 model=null 時,從模型自言自語保守補抓型號。

    三個嚴格條件全部成立才補,缺一不可:
    1. 型號必須在型號表中完全吻合(防止抓到非三星型號或記憶中的型號)
    2. 型號所在的「同一句」必須含「清楚/清晰/印著/寫著」等明確可讀動詞
    3. 型號所在的「同一句」不能含「模糊/不清楚/看不到/反光」等風險詞
       (用中文標點「，。；！？」分句,避免遠處描述價牌模糊被誤判為型號模糊)

    回傳 (model_or_None, reason_str)。
    """
    if not text or not valid_models:
        return None, "無文字或無型號表"
    text_tc = to_tc(text)

    for vm in valid_models:
        if not vm or len(vm) < 8:
            continue
        idx = text_tc.find(vm)
        if idx < 0:
            continue
        # 以中文標點分句,找出型號所在的句子
        # 往前找最近的標點(初始化為 0,取所有標點中最大的位置)
        sent_start = 0
        for ch in "，。；！？\n":
            p = text_tc.rfind(ch, 0, idx)
            if p != -1 and p + 1 > sent_start:
                sent_start = p + 1
        # 往後找最近的標點(初始化為文末,取所有標點中最小的位置)
        sent_end = len(text_tc)
        for ch in "，。；！？\n":
            p = text_tc.find(ch, idx + len(vm))
            if p != -1 and p < sent_end:
                sent_end = p
        sentence = text_tc[sent_start:sent_end].strip()

        # 條件 2:同一句必須含明確可讀動詞
        clear_found = [w for w in _CLEAR_CONTEXT_WORDS if w in sentence]
        if not clear_found:
            continue

        # 條件 3:同一句不能含風險詞
        risky_found = [w for w in _RISKY_CONTEXT_WORDS if w in sentence]
        if risky_found:
            continue

        # 條件 1 已滿足(vm 在型號表中)
        reason = f"型號 {vm} 在型號表中,所在句子明確可讀({', '.join(clear_found[:3])})且無模糊詞"
        return vm, reason

    return None, "AI 判讀文字中的型號皆不符合保守補抓條件（需同時在型號表、同句明確可讀且無模糊詞）"


def _call_opencode_go_api(api_base, api_key, model, messages, max_tokens=900, timeout=180, max_image_px=2560):
    """Call OpenCode Go Anthropic Messages API. Returns (status_code, response_text, response_json, thinking_text)."""
    import requests as _requests

    anthropic_url = api_base.rstrip('/').replace('/v1', '') + '/v1/messages'
    anthropic_msgs, system_text = _convert_to_anthropic_messages(messages, max_image_px=max_image_px)

    payload = {"model": model, "max_tokens": max_tokens, "messages": anthropic_msgs, "temperature": 0}
    if system_text:
        payload["system"] = system_text
    # [OCG-v2.4] 嘗試設定 thinking 預算,避免 reasoning 無上限導致成本失控。
    # OpenCode Go 的 Anthropic API 會自動啟用 extended thinking,thinking tokens 不計入 max_tokens,
    # 因此 output_tokens 常遠超 max_tokens。這裡設定 budget_tokens 限制 thinking 預算。
    # 若 API 不支援此參數會被忽略,不影響功能。
    thinking_budget = int(os.environ.get("OCR_OCG_THINKING_BUDGET", "2000"))
    payload["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}

    api_key_str = str(api_key) if api_key else ""
    resp = _requests.post(anthropic_url, json=payload, headers={
        "x-api-key": api_key_str,
        "anthropic-version": "2023-06-01"
    }, timeout=timeout)

    if resp.status_code != 200:
        return resp.status_code, f"API error {resp.status_code}: {resp.text[:300]}", None, ""

    resp_data = resp.json()
    content_blocks = resp_data.get("content", [])
    text_blocks = []
    thinking_blocks = []
    for b in content_blocks:
        btype = b.get("type", "")
        if btype == "text":
            text_blocks.append(b.get("text", ""))
        elif btype in ("thinking", "reasoning_content"):
            thinking_blocks.append(b.get("thinking", b.get("text", "")))
    full_text = "".join(text_blocks)
    thinking_text = "".join(thinking_blocks)
    return resp.status_code, full_text, resp_data, thinking_text


def process_single_image(
    fname,
    image_b64,
    prompt_mgr,
    image_processor,
    processed_image=None,
    ocr_attempt=1,
    previous_results=None,
):
    """
    Two-Stage Pipeline with Dual Resolution Strategy (Currently Single Stage):
    1. Low-Res (1024px) for fast & stable locating (Stage 1).
    2. High-Res (4096px) for precise text reading (Stage 2).
    """
    # OpenCC is now global

    # Init Orchestrator reference for logging

    # Init Orchestrator reference for logging
    global api_client, model_name_global, orchestrator

    # RESET STREAM BUFFER FOR NEW IMAGE
    if orchestrator:
        orchestrator.stream_buffer = "" # Reset to empty (remove '...')
        orchestrator.stream_file = fname

    # Load Valid Models for Injection
    # Load Valid Models for Injection
    # [v9.42 Dynamic Loading] Reload model list from file every time to respect User's "maintain text file only" request
    try:
        with open('型號表.txt', 'r', encoding='utf-8') as f:
            raw_models = [line.strip() for line in f if line.strip()]
        # Update matcher dynamically
        if orchestrator.model_matcher:
            orchestrator.model_matcher.valid_models = raw_models
        valid_models_str = "Check against known Samsung models."
    except Exception as e:
        log.error(f"Failed to reload model list: {e}")
        valid_models_str = "Samsung Monitor List (Internal)"

    # Load Prompt
    prompt_bundle = prompt_mgr.get_prompt_bundle()

    start_time = time.time()
    thinking_text = "" # [v17.18 Fix] Initialize early to prevent UnboundLocalError

    # Accuracy-first invariant: legacy fast flags are deliberately ignored.
    # The full scene preserves ownership/layout evidence; supplementary crops
    # help read small model and price labels without replacing that context.
    image_processor.config["max_size"] = None
    image_processor.config["max_dimensions"] = (2560, 1440)

    # [v16.3 DEBUG] Force Print to see if we enter
    print(f"[DEBUG] process_single_image called for: {fname}")

    # [v16.3 Optimization] Use pre-loaded b64 to avoid double reading/path errors
    label_b64 = None
    bottom_label_b64 = None
    bottom_center_b64 = None
    scene_tiles = []
    if processed_image:
        source_path = (processed_image.get("metadata") or {}).get("source_path")
        if int(ocr_attempt or 1) >= 2 and source_path and os.path.isfile(source_path):
            processed_image = image_processor.process(source_path, evidence_attempt=int(ocr_attempt))
        full_image_b64 = processed_image['base64']
        label_b64 = processed_image.get('label_base64')
        bottom_label_b64 = processed_image.get('bottom_label_base64')
        bottom_center_b64 = processed_image.get('bottom_center_base64')
        scene_tiles = list(processed_image.get("scene_tiles") or [])
    elif image_b64:
        full_image_b64 = image_b64
        # print(f"[DEBUG] Using provided image_b64, len={len(image_b64)}")
    else:
        active_file = os.path.join(orchestrator.image_dir, fname)
        result = image_processor.process(active_file)
        if not result:
            print(f"[ERROR] Image processing failed for {fname}")
            return {"error": "Image processing failed"}
        full_image_b64 = result['base64']
        label_b64 = result.get('label_base64') # [v18.25] Dual Vision: Get High-Res Crop
        bottom_label_b64 = result.get('bottom_label_base64')
        bottom_center_b64 = result.get('bottom_center_base64')
        scene_tiles = list(result.get("scene_tiles") or [])
    if orchestrator:
        msg = f"▶️ 正在分析圖片: {fname} (Model: {model_name_global})..."
        if label_b64: msg += " (偵測到價牌，啟用雙重視野放大 🔍)"
        if bottom_label_b64: msg += " (下方整條價牌帶)"
        if bottom_center_b64: msg += " (下方價牌帶放大)"
        orchestrator.log_system(msg)
        console.print(f"[cyan]{msg}[/cyan]")

    # Adopt User's "Samsung Manager" Persona Prompt + IRON RULE (v9.9)
    # Get model list for injection
    # v16.9 Fix: Actually load the list!
    try:
        with open('型號表.txt', 'r', encoding='utf-8') as f:
             valid_models_str = f.read()
    except:
        valid_models_str = "(無法讀取型號表)"

    # [v18.75 FIX] Load System Prompt from PromptManager (Bundle System)
    # 🔴 徹底修復：不再硬編碼 txt 檔案路徑，使用版本化的 Bundle 系統
    # [v18.76 動態讀取] 每張照片都重新讀取 prompt.txt，修改後不需重啟！
    # [v19.11] OpenCode Go 使用專用簡化 prompt + few-shot
    is_opencode_go = False
    try:
        if (api_client and hasattr(api_client, 'base_url')
                and 'opencode.ai' in str(api_client.base_url or '')):
            is_opencode_go = True
    except Exception:
        pass

    try:
        # 🔥 強制每次都從檔案讀取，不使用快取
        # One production prompt is the source of truth for every provider.
        prompt_file = 'samsung_ocr_prompt.txt'
        with open(prompt_file, 'r', encoding='utf-8') as f:
            prompt_template = f.read()
        # 可選：記錄檔案修改時間，方便 debug
        # import os; mtime = os.path.getmtime(prompt_file)
        # print(f"[DEBUG] Prompt loaded, mtime={mtime}")
    except Exception as e:
        # 最終備份
        if orchestrator:
            orchestrator.log_system(f"❌ 讀取 Prompt 失敗: {e}, 使用最小備份")
        prompt_template = "你是三星螢幕管理員。請提取型號與價格。..." # 簡單備份

    # v14.3: Use .replace() instead of .format() to avoid KeyError with JSON braces in prompt
    # [v18.12] Disabled Injection to prevent Hallucination
    # system_prompt = prompt_template.replace("{valid_models_str}", valid_models_str) \
    #                                .replace("{examples_section}", "")
    followme_daily_reference = build_followme_prompt_section()
    manual_rule_section, manual_rule_count = load_manual_rule_prompt_section()
    if manual_rule_section:
        followme_daily_reference = f"{followme_daily_reference}{manual_rule_section}"
        if orchestrator:
            orchestrator.manual_rule_count = manual_rule_count
    system_prompt, used_compact_prompt = build_runtime_system_prompt(
        prompt_template,
        followme_daily_reference,
    )
    if used_compact_prompt:
        prompt_notice = (
            f"提示詞原長度 {len(prompt_template) + len(followme_daily_reference)} 字元，"
            f"已切換為 {len(system_prompt)} 字元的完整規則精簡版，以相容 8K 本機模型。"
        )
        console.print(f"[yellow]⚠️ {prompt_notice}[/yellow]")
        if orchestrator:
            orchestrator.log_system(f"ℹ️ {prompt_notice}")


    # [v17.31 Integrity] Force Echo Filename to prevent crosstalk (849 vs 431 mixup)
    import uuid
    random_salt = str(uuid.uuid4())[:8]

    # [v18.35 Stateless Purge]
    # 1. 強化無狀態提示 (Engineering Strategy)
    # 2. 恢復雙重視野 (Engineering Strategy)
    # 3. 確保每次都建立全新 messages

    ocr_attempt = max(1, int(ocr_attempt or 1))
    previous_results = list(previous_results or [])

    # Construct User Context
    user_images = []

    # Image 1: Context (Full Image)
    user_images.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{full_image_b64}"}})

    # [v19.11] OpenCode Go: disable dual vision to reduce cost/latency; single full image is enough
    if is_opencode_go:
        label_b64 = None
        bottom_label_b64 = None
        bottom_center_b64 = None
        user_prompt = f"「這是一張全新的照片，與之前的任何辨識無關。」\n圖片: {fname}\nRequestID: {random_salt}\n請提取此照片中的資訊。若是 FollowMe，FollowMe 字樣、FM 型號代碼、移動式支架/托盤線索都可作為判定依據；型號與價格盡量確認屬於同一主角商品。"
    else:
        # Image 2: High-Resolution (Crop) if detected.
        # When bottom-label strip is enabled, skip the auto label crop because it can grab a huge
        # unrelated region; full image + deterministic lower strip is more stable for batch OCR.
        if label_b64 and not bottom_label_b64:
            user_images.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{label_b64}"}})
            user_prompt = f"「這是一張全新的照片，與之前的任何辨識無關。請執行『視覺歸屬』檢查。」\n圖片: {fname}\nRequestID: {random_salt}\n[提示]\n圖 1 (全景): 用於確認標籤相對於螢幕底座的位置歸屬。\n圖 2 (特寫): 用於讀取該標籤上的細微文字。\n請結合兩者，確保讀到的文字是來自於『歸屬於該螢幕的同一張標籤』。"
        else:
            user_prompt = f"「這是一張全新的照片，與之前的任何辨識無關。」\n圖片: {fname}\nRequestID: {random_salt}\n請提取此照片中的資訊。若是 FollowMe，FollowMe 字樣、FM 型號代碼、移動式支架/托盤線索都可作為判定依據；型號與價格盡量確認屬於同一主角商品。"

        if bottom_label_b64:
            user_images.append({
                "type": "text",
                "text": "補充圖：這是原圖下方整條商品標籤/價牌區域的自動裁切，請用它輔助尋找不在正中央的價牌。",
            })
            user_images.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{bottom_label_b64}"}})

        if bottom_center_b64:
            user_images.append({
                "type": "text",
                "text": "補充圖：這是原圖下方中間商品價牌區域的自動放大裁切，請優先用它讀主角型號與價格。",
            })
            user_images.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{bottom_center_b64}"}})

        # Retry-only spatial evidence: full-height overlapping tiles preserve
        # object ownership while staying within a deterministic image budget.
        for tile in scene_tiles[:3]:
            user_images.append({"type": "text", "text": f"補充圖：{tile['label']}，原圖全高空間證據，bbox={tile['bbox']}。只能用於定位，不可單獨證明分類。"})
            user_images.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{tile['base64']}"}})

    if ocr_attempt == 2:
        user_prompt += "\n\n" + REVIEW_FOCUS_PROMPTS[2]
    elif ocr_attempt >= 3:
        user_prompt += "\n\n" + REVIEW_FOCUS_PROMPTS[3]


    # Per-photo OCR must remain stateless.  Historical wrong/correct answers
    # belong in offline regression fixtures, never in a live model prompt.

    user_content = [{"type": "text", "text": user_prompt}] + user_images

    messages = build_ocr_messages(system_prompt, user_content, ocr_attempt, previous_results)
    prompt_health_reasons = review_prompt_leak_reasons(
        ocr_attempt,
        messages,
        injected_prior_results=[],
        prior_results_for_leak_check=previous_results,
    )
    if prompt_health_reasons:
        return {
            "error": "runtime health gate stopped contaminated review prompt",
            "runtime_health_stop": True,
            "runtime_health_reasons": prompt_health_reasons,
            "thinking": BLOCKED_NARRATION,
            "view_type": "失敗",
            "category": "失敗",
            "model": None,
            "price": None,
        }

    full_response_text = ""
    # [v17.30 Full Schema] Initialize with all required fields to avoid omission
    result_json = {
        "view_type": "單機",
        "screen_status": "",
        "quality_issue": "",
        "model": None,
        "price": None,
        "category": "單機",
        "black_screen": False,
        "thinking": ""
    }

    # [v9.62 Fix] Allow ANY IP address for Local LLM, not just localhost
    use_local_llm = False
    if api_client:
        use_local_llm = True

    if orchestrator:
        orchestrator.stream_buffer = "" # Clean start
        orchestrator._stream_active = True # [v9.69 Fix] Reset stream latch for new image!
        console.print(f"[bold yellow]☁️  正在將圖片傳送到 LLM (Model: {model_name_global})...[/bold yellow]")
        orchestrator.log_system(f"🚀 本機 AI 引擎已準備就緒 ({model_name_global})")
        # Simplified
        pass

    try:
        # [v19.10] OpenCode Go routing: early return after Anthropic API call
        use_opencode_go = False
        try:
            if (api_client and hasattr(api_client, 'base_url')
                    and 'opencode.ai' in str(api_client.base_url or '')):
                use_opencode_go = True
        except:
            pass

        if use_opencode_go:
            try:
                api_key_raw = api_client.api_key if hasattr(api_client, 'api_key') else ""
                # Provider routing must not silently shrink the prepared image.
                if model_name_global == "qwen3.7-plus":
                    ocg_max_tokens = 900
                    ocg_max_image_px = 2560
                else:
                    ocg_max_tokens = 2000
                    ocg_max_image_px = 2560
                status_code, full_response_text, resp_data, reasoning_text = _call_opencode_go_api(
                    str(api_client.base_url), str(api_key_raw),
                    model_name_global, messages,
                    max_tokens=ocg_max_tokens, timeout=180, max_image_px=ocg_max_image_px
                )
                if orchestrator:
                    # Show natural monologue before JSON as self-talk; reasoning as fallback.
                    # If the model already put the monologue in the content (text before JSON),
                    # use it directly. Otherwise extract from reasoning_text.
                    display_buffer = ""
                    if full_response_text:
                        json_start = full_response_text.find('{')
                        if json_start > 0:
                            # The model followed the "monologue first, then JSON" format
                            display_buffer = to_tc(full_response_text[:json_start].strip())
                        elif reasoning_text:
                            extracted = extract_natural_monologue(reasoning_text)
                            display_buffer = to_tc(extracted) if extracted else ""
                        else:
                            display_buffer = to_tc(full_response_text.strip())
                    elif reasoning_text:
                        extracted = extract_natural_monologue(reasoning_text)
                        display_buffer = to_tc(extracted) if extracted else ""
                    # [繁中檢查] 顯示前確保為台灣繁體,攔截模型誤用簡體
                    display_buffer, zh_violations = ensure_traditional_chinese(
                        display_buffer, source_label=f"{fname} AI 判讀文字"
                    )
                    if zh_violations:
                        orchestrator.log_system(
                            f"⚠️ [繁中檢查] {fname} AI 判讀文字偵測到簡體字元已自動轉換: {' '.join(sorted(set(zh_violations)))}"
                        )
                    simulate_streaming_buffer(display_buffer, orchestrator, char_delay=0.04)

                # Debug log raw response so we can inspect mimo-v2.5 output
                try:
                    debug_log_path = os.path.join(os.getcwd(), "opencode_go_debug.log")
                    usage = resp_data.get("usage", {}) if isinstance(resp_data, dict) else {}
                    input_tokens = usage.get("input_tokens")
                    output_tokens = usage.get("output_tokens")
                    with open(debug_log_path, "a", encoding="utf-8") as dbg:
                        dbg.write(f"\n--- {model_name_global} | {fname} | status={status_code} ---\n")
                        dbg.write(f"TOKENS: input={input_tokens}, output={output_tokens}\n")
                        dbg.write(f"RAW:\n{full_response_text}\n")
                        if reasoning_text:
                            dbg.write(f"REASONING:\n{reasoning_text}\n")
                    # [OCG-v2.3] Track token usage and cost for UI display
                    if orchestrator:
                        orchestrator.last_model_name = model_name_global
                        orchestrator.last_token_usage = {"input": input_tokens, "output": output_tokens}
                        cost = calculate_image_cost(model_name_global, input_tokens, output_tokens)
                        orchestrator.last_image_cost = cost
                        if isinstance(cost, (int, float)):
                            orchestrator.total_image_cost += float(cost)
                            orchestrator.cost_image_count += 1
                except Exception:
                    pass

                # Robust JSON extraction
                parsed, raw_objects, merge_mode, merge_rejected_reason = _merge_v1945_json_objects(full_response_text)
                args_str = raw_objects[0].get("raw") if raw_objects else None
                if parsed is not None:
                    parsed = json.loads(sanitize_json(json.dumps(parsed, ensure_ascii=False)))

                # [OCG-v2.4] JSON 解析失敗時,啟用 fallback 從 Markdown/文字搶救欄位
                if not parsed and not merge_rejected_reason:
                    try:
                        valid_models = []
                        if orchestrator and orchestrator.model_matcher:
                            valid_models = getattr(orchestrator.model_matcher, 'valid_models', []) or []
                        fallback_result = _fallback_extract_fields(full_response_text, valid_models)
                    except Exception:
                        fallback_result = None
                    if fallback_result:
                        parsed = fallback_result
                        if orchestrator:
                            orchestrator.log_system(
                                f"🆘 [Fallback] {fname} 模型未輸出 JSON,已從文字搶救: 型號={parsed.get('model')} 價格={parsed.get('price')} 視角={parsed.get('view_type')}"
                            )
                        console.print(f"[yellow]🆘 [Fallback] {fname}: 從 Markdown 搶救型號/價格[/yellow]")

                if parsed and isinstance(parsed, dict):
                    # [OCG-v2.5] JSON 成功但 model=null 時,保守補抓型號(低風險)
                    parsed_model = parsed.get("model")
                    if (parsed_model is None or parsed_model == "") and parsed.get("view_type") == "單機":
                        try:
                            valid_models = []
                            if orchestrator and orchestrator.model_matcher:
                                valid_models = getattr(orchestrator.model_matcher, 'valid_models', []) or []
                            rescue_source = (reasoning_text or "") + "\n" + (full_response_text or "")
                            rescued_model, rescue_reason = _rescue_model_conservative(rescue_source, valid_models)
                        except Exception:
                            rescued_model, rescue_reason = None, "補抓例外"
                        if rescued_model:
                            parsed["model"] = rescued_model
                            parsed["model_rescued"] = True  # 標記供人工審核
                            if orchestrator:
                                orchestrator.log_system(
                                    f"🔬 [型號補抓] {fname} 模型填 null，從 AI 判讀文字保守補抓: {rescued_model} ({rescue_reason})"
                                )
                            console.print(f"[cyan]🔬 [型號補抓] {fname}: {rescued_model}[/cyan]")
                    # Capture reasoning BEFORE update() overwrites thinking with model's empty string
                    combined_thinking = reasoning_text.strip() if reasoning_text else ""
                    text_prefix = ""
                    if args_str and full_response_text:
                        text_prefix = full_response_text.split(args_str)[0].strip()
                    if not combined_thinking and text_prefix:
                        combined_thinking = text_prefix
                    # [繁中檢查] 存入 thinking 前確保為台灣繁體
                    combined_thinking, _ = ensure_traditional_chinese(
                        combined_thinking, source_label=f"{fname} thinking"
                    )
                    result_json.update(parsed)
                    result_json["raw_objects"] = [str(item.get("raw") or "")[:12000] for item in raw_objects[:3]]
                    result_json["merge_mode"] = merge_mode
                    result_json["merge_rejected_reason"] = merge_rejected_reason
                    # Restore reasoning as thinking/self-talk
                    result_json["thinking"] = combined_thinking
                    result_json["independent_pass"] = True
                    result_json["prior_answer_exposed"] = False
                    result_json["prompt_contamination"] = False
                    result_json = finalize_evidence_contract(result_json, full_response_text)
                    result_json = attach_spatial_evidence_trace(result_json, scene_tiles, ocr_attempt)
                    # Set module-level thinking_text so final persistence (line ~1838) keeps it
                    thinking_text = combined_thinking
                    console.print(f"[green]✅ OpenCode Go: {parsed.get('view_type')}/{parsed.get('model')}/{parsed.get('price')}[/green]")
                    return result_json
                else:
                    console.print(f"[yellow]⚠️ OpenCode Go 無效 JSON (status={status_code})[/yellow]")
                    # Return a recoverable result so the batch can continue
                    fallback_thinking = (reasoning_text.strip() + "\n" + (full_response_text or "")).strip()
                    # [繁中檢查] 失敗分支的 thinking 也要轉繁體
                    fallback_thinking, _ = ensure_traditional_chinese(
                        fallback_thinking, source_label=f"{fname} failed-thinking"
                    )
                    result_json["thinking"] = fallback_thinking
                    result_json["quality_issue"] = "OpenCode Go returned non-JSON"
                    return result_json
            except Exception as e:
                console.print(f"[red]❌ OpenCode Go 失敗: {e}[/red]")
                return {"error": f"OpenCode Go API: {e}"}

        # [v17.16 Fix] Pre-Flight Check for Stop Signal
        if orchestrator and not orchestrator.is_running:
             return {"error": "Stopped by user"}

        # Move prompt loading inside try to catch formatting errors
        # ... actually already done above ...

        # [v18.90] Price Consistency Retry Loop
        # 包裝 LLM 呼叫與解析，當價格/型號矛盾時給予二次機會
        max_retries = max(1, int(os.environ.get("OCR_MAX_RETRIES", "1")))
        final_result = None

        base_messages = build_ocr_messages(system_prompt, user_content, ocr_attempt, [])
        for attempt in range(max_retries + 1):
            # Every transport/parser retry starts from the same pristine image
            # request.  Never append the prior model response to this context.
            messages = list(base_messages)
            if attempt > 0:
                messages.append({
                    "role": "user",
                    "content": "只根據所附原圖產生一個完整 JSON 物件，所有必要欄位都要填入；無法確認的值使用 null。",
                })
            start_llm_t = time.time()
            full_response_text = ""
            args_str = None
            parsed = None
            loop_detected = False
            if attempt > 0:
                console.print(f"[bold yellow]🔄 觸發重試 (Attempt {attempt+1}/{max_retries+1}) - 加入價格警告提示...[/bold yellow]")
                orchestrator.log_system(f"🔄 [Auto-Retry] 觸發價格與型號不一致的重試機制...")

            request_kwargs = {
                "model": model_name_global,
                "messages": messages,
                "stream": True,
                "temperature": 0,
                "stream_options": {"include_usage": True},
            }
            request_kwargs["max_tokens"] = int(os.environ.get("OCR_MAX_TOKENS", "900"))

            stream = api_client.chat.completions.create(
                **request_kwargs
                # top_p=0.8,      # Removed to allow model defaults
                # max_tokens=1024, # Let model decide or use default
                # presence_penalty=1.5, # Removed: harmful for OCR (forces diversity)
            )

            for chunk in stream:
                # ... (Stream handling code remains same, omitted for brevity but conceptually here) ...
                # [Copied from original stream processing to ensure identical behavior]
                if orchestrator and not orchestrator.is_running:
                    console.print("[red]🛑 用戶強制中斷串流[/red]")
                    try: stream.close()
                    except: pass
                    return {"error": "Stopped by user"}

                if not hasattr(chunk, 'choices') or not chunk.choices: continue

                if not full_response_text and attempt == 0: # Only log first char on first try to avoid spam
                     duration_llm_first = time.time() - start_llm_t
                     console.print(f"[bold green]✨ LLM 已回應 (首字耗時: {duration_llm_first:.2f}s)[/bold green]")

                delta = chunk.choices[0].delta
                content_piece = ""
                if hasattr(delta, 'content') and delta.content:
                    content_piece = delta.content
                elif hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                    content_piece = delta.reasoning_content

                if content_piece:
                    content_tc = to_tc(content_piece)
                    full_response_text += content_tc
                    VERSION = "v18.91 (Debug Log)" # Update locally for display

                    # Display Logic
                    if '{' in full_response_text:
                        clean_display = full_response_text.split('{')[0].strip()
                        current_display = clean_display
                    else:
                        current_display = full_response_text

                    current_display = current_display.lstrip('「').rstrip('」').replace('```json', '').replace('```', '').strip()
                    # 獨白欄不需要顯示「思考:」標題前綴
                    import re as _re
                    current_display = _re.sub(r'^思考[:：]\s*', '', current_display).strip()
                    # [v19.15] 即時濾除模型 echo 指令的殘留
                    current_display = clean_stream_display(current_display)
                    if orchestrator: orchestrator.stream_buffer = current_display

                    if len(full_response_text) > 1200 and _detect_repetition(full_response_text):
                        loop_detected = True
                        if orchestrator:
                            orchestrator.stream_buffer = (current_display[:600] + "\n\n(模型輸出重複，正在重試...)").strip()
                            orchestrator.log_system("⚠️ [自動重試] AI 輸出重複，正在結束本次回應並重試。")
                        try:
                            stream.close()
                        except Exception:
                            pass
                        break

            # Anti-Loop Check
            if loop_detected or _detect_repetition(full_response_text):
                if attempt < max_retries:
                    console.print(f"[red]⚠️ 偵測到跳針，重試...[/red]")
                    continue
                else:
                    return {"error": "AI repetitive loop detected"}

            # Parse Result
            args_str = None
            thinking_text = full_response_text

            parsed, raw_objects, merge_mode, merge_rejected_reason = _merge_v1945_json_objects(full_response_text)
            args_str = raw_objects[0].get("raw") if raw_objects else None
            if args_str:
                prefix = full_response_text.split(args_str, 1)[0].strip()
                if prefix:
                    thinking_text = prefix
            if parsed is not None:
                parsed = json.loads(sanitize_json(json.dumps(parsed, ensure_ascii=False)))
                structured_narration = str(
                    parsed.get("narration") or parsed.get("desc") or ""
                ).strip()
                if structured_narration:
                    thinking_text = structured_narration

            # [v18.99 Fix] Retry if JSON is completely missing
            if not parsed and attempt < max_retries:
                console.print(f"[bold red]⚠️ 未偵測到有效 JSON，要求 LLM 補完...[/bold red]")
                orchestrator.log_system("⚠️ [自動重試] AI 未輸出有效結果，正在重試...")
                continue

            # The model response is machine-readable JSON, but the boss-facing
            # UI requires this pass's actual natural-language observation.
            # Retry from the pristine photo when that field is absent; never
            # feed the JSON response back into the next request.
            if parsed and not str(parsed.get("narration") or parsed.get("desc") or "").strip():
                if attempt < max_retries:
                    orchestrator.log_system("⚠️ [自動重試] AI 未輸出可讀判讀獨白，將從原圖獨立重讀。")
                    continue

            # [v18.90] Price Consistency Check
            price_check_passed = True
            if parsed and isinstance(parsed, dict):
                model_check = parsed.get("model")
                price_check = parsed.get("price")

                if should_compare_official_price(fname) and is_samsung_model_like(model_check) and model_check and price_check:
                    # Get Official Price
                    from skills.official_price import get_price_manager
                    pm = get_price_manager()
                    # [v18.93 Hotfix] Corrected method name and return type handling
                    off_price = pm.get_official_price(model_check)

                    if off_price and off_price > 0: # Check for valid price (ignore None or -1)
                        try:
                            # Parse OCR Price
                            ocr_p = int(str(price_check).replace(',', '').replace('$', '').replace('NT', '').strip())

                            # Diff Calculation (Official Price is single int now)
                            diff = abs(ocr_p - off_price)

                            # Threshold: 5000 TWD
                            if diff > 5000:
                                console.print(f"[bold red]⚠️ 價格矛盾警報: 型號 {model_check} 官網價格 ${off_price}, 但 OCR 識別為 ${ocr_p}. 價差 ${int(diff)}[/bold red]")
                                if attempt < max_retries:
                                    # Do not reveal the disputed values to the
                                    # next model call.  Return them as a conflict
                                    # and let the orchestrator schedule a wholly
                                    # independent pass from the original photo.
                                    parsed["price_conflict_detected"] = True
                                    parsed["price_conflict_official_reference"] = off_price
                                    final_result = parsed
                                    price_check_passed = True
                                else:
                                    console.print(f"[yellow]⚠️ 已達重試上限，保留原始結果。[/yellow]")
                        except:
                            pass # Parse error, skip check

            if price_check_passed:
                final_result = parsed
                break # Success!

            # If we are here, we are retrying...

        # End of Retry Loop - Use result from last attempt (parsed)
        # Re-assign parsed to ensure downstream logic works
        if not parsed and args_str: # Try parse one last time if loop finished
             try: parsed = json.loads(args_str)
             except: pass

        # [v17.28] New Prompt puts 'desc' OUTSIDE JSON.
        # But if the model reverted to old schema (desc inside), handle it.
        if parsed and 'desc' in parsed and not thinking_text:
            thinking_text = parsed['desc']

        # [v18 Strict Backend Insurance]
        # 1. Load Model List
        valid_models_list = []
        try:
             with open('型號表.txt', 'r', encoding='utf-8') as f:
                 valid_models_list = [line.strip().upper() for line in f if line.strip()]
        except:
             if orchestrator: orchestrator.log_system("⚠️ 無法讀取型號表，跳過模型嚴格校驗")

        # Handle 'data' nesting if exists
        data_obj = parsed # Default
        if parsed and isinstance(parsed, dict):
             data_obj = parsed.get('data', parsed)

        if not isinstance(data_obj, dict): data_obj = {} # Fallback to empty dict to prevent AttributeError

        # 2. Strict Model Check -> Fuzzy Recovery [v18.04]
        import difflib # Ensure import available (inline is safe)
        raw_model = data_obj.get("model")
        raw_other_brand_model = infer_other_brand_model(thinking_text, raw_model)
        if raw_other_brand_model and is_samsung_model_like(raw_model):
            # Conflicting prose must trigger the accuracy gate; it may never
            # silently overwrite an explicit Samsung SKU.
            data_obj["brand_evidence_conflict"] = True
            data_obj["rejected_other_brand_model"] = raw_other_brand_model
        elif raw_other_brand_model and data_obj.get("view_type") != "遠景":
            console.print(f"[dim]🔎 [Other Brand] '{raw_model}' -> '{raw_other_brand_model}'[/dim]")
            data_obj["model"] = raw_other_brand_model
            raw_model = None

        # [v18.81] 移除 FollowMe 自動偵測邏輯
        # 原因：AI 幻覺說「圓形底座」時會誤觸發，導致錯誤的型號自動填入
        # FollowMe 判定應該只依據 AI 明確輸出的型號，不要從獨白關鍵字推斷

        if raw_model and isinstance(raw_model, str) and raw_model.upper() != "NULL":
            # [v18.28] Enhanced Noise Removal for Raw OCR (Expanded)
            clean_model = raw_model.strip().upper()
            # Added 24" as it appears in 412.jpg
            # [v18.91] Added 'MODEL', '型號' to noise patterns to fix 1278.jpg failure
            noise_patterns = ['24"', '27"', '32"', '34"', '49"', '24INCH', '27INCH', '32INCH', 'SAMSUNG', 'HZ', 'MS', '1000R', '1500R', 'MODEL', '型號', ':', '：']
            for noise in noise_patterns:
                clean_model = clean_model.replace(noise, "")
            clean_model = clean_model.strip()

            # [Debug] Trace Model Validation
            in_list = clean_model in valid_models_list
            console.print(f"[dim]🔍 [比對追蹤] Raw='{raw_model}' -> Clean='{clean_model}' -> InList={in_list}[/dim]")

            # [v18.15] FollowMe Logic (Price-Based Manual Mapping)
            is_followme_bypass = False # [v18.44] Flag to pass strict checking

            # [v18.99] 修復：如果 AI 已識別出有效的 S 型號 (如 S32M703UC)，就不要強制覆蓋為 FollowMe
            # 只有當 clean_model 是空的、無效的、或明確包含 "FOLLOWME" 時才觸發 FollowMe 邏輯
            has_valid_s_model = bool(clean_model and clean_model.startswith('S') and len(clean_model) >= 8)

            # [v18.94] Enhanced FollowMe Detection (Check Raw & Thinking)
            # 即使 clean_model 被洗掉，只要 raw_model 或 thinking_text 有跡象，就啟動救援
            followme_hints = ["FOLLOWME", "FOLLOW ME"]  # [v18.99] 移除 M7/M5/SMART MONITOR，避免誤判
            is_followme_candidate = False
            negative_followme_context = has_negative_followme_context(thinking_text)
            positive_followme_physical_context = (
                has_sufficient_followme_physical_evidence(data_obj)
                or has_followme_display_fixture_clue(thinking_text)
            )
            borrowed_model_context = should_block_borrowed_model_rescue(thinking_text)

            # [v18.99] 只有在以下情況才觸發 FollowMe 邏輯：
            # 1. clean_model 明確包含 "FOLLOWME"
            # 2. 或者 clean_model 為空/無效，且 raw_model/thinking_text 有 FollowMe 關鍵字
            if "FOLLOWME" in clean_model and (not negative_followme_context or positive_followme_physical_context) and not borrowed_model_context:
                is_followme_candidate = True
            elif not has_valid_s_model and (not negative_followme_context or positive_followme_physical_context) and not borrowed_model_context:  # 只有當沒有有效 S 型號時才檢查其他線索
                if raw_model and any(h in raw_model.upper() for h in followme_hints):
                    is_followme_candidate = True
                elif thinking_text and has_positive_followme_word(thinking_text):
                    is_followme_candidate = True
                elif positive_followme_physical_context:
                    is_followme_candidate = True

            if is_followme_candidate:
                 p_val = data_obj.get("price")
                 mapped_model = normalize_followme_model(
                     raw_model or clean_model,
                     p_val,
                     thinking_text,
                     structured_physical_confirmed=has_sufficient_followme_physical_evidence(data_obj),
                 )
                 if mapped_model:
                     clean_model = mapped_model
                     is_followme_bypass = True # [v18.44] Enable Bypass
                     # [v18.63] Silent log, no UI output
                     console.print(f"[dim]⚠️ [FollowMe Logic] '{raw_model}' -> '{clean_model}' (Price: {p_val})[/dim]")

            # [v18.44 Fix] Added bypass flag so FollowMe isn't killed by hallucination check
            if is_placeholder_model(clean_model):
                console.print(f"[yellow]⚠️ [Model Guard] 拒絕提示詞占位／測試型號: {clean_model}[/yellow]")
                data_obj["model"] = None
            elif valid_models_list and clean_model not in valid_models_list and not is_followme_bypass:
                exact_model = strict_known_model(clean_model, valid_models_list)
                if exact_model:
                    data_obj["model"] = exact_model
                elif should_compare_official_price(fname) and is_samsung_model_like(clean_model) and try_discover_model(clean_model):
                    console.print(f"[bold green]✨ [Auto-Discover] 官網驗證成功：{clean_model}[/bold green]")
                    data_obj["model"] = clean_model
                    if valid_models_list is not None and clean_model not in valid_models_list:
                        valid_models_list.append(clean_model)
                else:
                    # A Samsung-looking string is not evidence.  Unknown or
                    # discontinued models must be re-read/reviewed, never
                    # trusted merely because their format looks plausible.
                    console.print(f"[yellow]⚠️ [Model Guard] 型號未經清單或官網驗證，清除並排入複核: {clean_model}[/yellow]")
                    data_obj["model"] = None
            else:
                 data_obj["model"] = clean_model

        # [v18.66] 尺寸描述 vs 型號 交叉驗證
        # 如果思考文字提到「43型」但型號卻是 S32...，表示混搭標籤
        final_model = data_obj.get("model")
        model_rescue_blocked = has_odyssey_ark_context(thinking_text) or should_block_borrowed_model_rescue(thinking_text)
        if final_model and thinking_text:
            import re
            if should_clear_borrowed_odyssey_ark_model(final_model, thinking_text):
                console.print(f"[yellow]⚠️ [Odyssey Ark 保護] 偵測到 Ark 系列名，清除疑似借用的小螢幕型號 {final_model}[/yellow]")
                data_obj["model"] = None
                data_obj["price"] = None
                final_model = None
                model_rescue_blocked = True

            corrected_followme_price = normalize_followme_price(final_model, data_obj.get("price"), thinking_text)
            if corrected_followme_price:
                console.print(f"[yellow]⚠️ [FollowMe Pro 價格校正] {data_obj.get('price')} → {corrected_followme_price}[/yellow]")
                data_obj["price"] = corrected_followme_price

            # 從思考文字中抓取尺寸描述（如「43型」、「32型」、「27型」）
            size_in_desc = re.search(r'(\d{2})型', thinking_text)
            if size_in_desc:
                desc_size = size_in_desc.group(1)
                # 從型號中抓取尺寸（如 S43... 的 43、S32... 的 32）
                model_size_match = re.search(r'S(\d{2})', str(final_model).upper())
                if model_size_match:
                    model_size = model_size_match.group(1)
                    if desc_size != model_size:
                        console.print(f"[yellow]⚠️ [尺寸交叉驗證] 描述={desc_size}型、型號={final_model}，清除後交由下一輪重讀[/yellow]")
                        data_obj["rejected_model"] = final_model
                        data_obj["model"] = None
                        data_obj["model_validation_failed"] = True

        ark_model = infer_odyssey_ark_model(thinking_text)
        if ark_model and not data_obj.get("model"):
            console.print(f"[green]✅ [Odyssey Ark 固定規則] 主角 Ark 55 吋 → {ark_model}[/green]")
            data_obj["model"] = ark_model

        # 3. Strict Price Check (4-5 digits only)
        raw_price = data_obj.get("price")
        if raw_price:
            clean_price = "".join([c for c in str(raw_price) if c.isdigit()])
            if len(clean_price) in [4, 5]:
                # [v18.49] Strict integer check for banned curvature values
                # [v18.97 Fix] Price formatting strict check (Anti-Hallucination)
                # If the raw OCR string didn't have '$' or ',' it might be curvature (1000R).
                # We check raw_price provided by LLM.
                raw_str = str(raw_price)
                has_currency_symbol = '$' in raw_str or ',' in raw_str or 'NT' in raw_str.upper()
                price_context_text = "\n".join([
                    raw_str,
                    str(data_obj.get("thinking") or ""),
                    str(thinking_text or ""),
                    str(full_response_text or ""),
                ])
                clearance_price_context = has_clearance_price_context(price_context_text)

                # Rule: If value is < 2000 AND has no currency symbol, it's likely curvature (1000, 1500, 1800)
                # If value is > 2000 (e.g. 3290), we forgive missing symbol if clearly not curvature.

                try:
                    p_int = int(clean_price)
                    # Price guard: clear obvious plan/accessory prices, but keep low-end monitor sale prices.
                    min_price = 1000 if clearance_price_context else 2000

                    if p_int < min_price:
                         console.print(f"[dim]⚠️ [價格攔截] {raw_price} ({p_int}) < {min_price} -> 過低 (方案/月付/配件價，不是螢幕商品售價)[/dim]")
                         data_obj["price"] = None
                    # [v18.97] Strict Symbol Check for low-ish numbers to be safe
                    elif p_int < 10000 and not has_currency_symbol:
                        # Double check: is it 1000, 1500, 1800? (Common Curvatures)
                        if p_int in [1000, 1500, 1800] and not clearance_price_context:
                             console.print(f"[yellow]⚠️ [價格攔截] {raw_price} 數值像曲率且無貨幣符號 -> 視為幻覺[/yellow]")
                             data_obj["price"] = None
                        else:
                             data_obj["price"] = clean_price
                    else:
                        data_obj["price"] = clean_price
                except ValueError:
                    data_obj["price"] = None
            else:
                # console.print(f"[dim]⚠️ [價格攔截] {raw_price} 長度不符[/dim]")
                data_obj["price"] = None

        # [v18.78] 獨白交叉驗證：從獨白中提取型號和價格，與 JSON 比對
        if thinking_text and thinking_text != "...":
            import re
            # 從獨白提取型號
            desc_model_patterns = [
                r'型號[是為]?\s*([A-Z]\d{2}[A-Z0-9]+)',
                r'寫著\s*([A-Z]\d{2}[A-Z0-9]+)',
                r'\b([A-Z]\d{2}[A-Z][A-Z0-9]{4,})\b',
            ]
            desc_model = None
            for pattern in desc_model_patterns:
                match = re.search(pattern, thinking_text, re.IGNORECASE)
                if match:
                    desc_model = match.group(1).strip().upper()
                    break
            desc_model_is_speculative = bool(re.search(r"(應為|可能|類似|推測).{0,20}" + re.escape(desc_model or ""), thinking_text, re.IGNORECASE)) or bool(re.search(r"沒有.{0,8}完整.{0,6}型號", thinking_text))

            # 從獨白提取價格
            desc_price_patterns = [
                r'(?:手寫|促銷價|出清|展示出清|福利品|展示機|清倉|特賣).{0,12}?(?:寫|標|是|為|:|：)?\s*[「」\"]?(\d{1,2},\d{3})[」\"]?',
                r'(?:手寫|促銷價|出清|展示出清|福利品|展示機|清倉|特賣).{0,12}?(?:寫|標|是|為|:|：)?\s*[「」\"]?(\d{4,5})[」\"]?',
                r'(?:價格|售價|促銷價|建議售價|價牌顯示|標籤寫|寫著)\s*寫\s*[「」\"]?(\d{1,2},\d{3})[」\"]?',
                r'(?:價格|售價|促銷價|建議售價|價牌顯示|標籤寫|寫著)\s*寫\s*[「」\"]?(\d{4,5})[」\"]?',
                r'(?:價格|售價|促銷價|建議售價|價牌顯示|標籤寫|寫著)\s*(?:是|為|:|：)?\s*[「」\"]?(\d{1,2},\d{3})[」\"]?',
                r'(?:價格|售價|促銷價|建議售價|價牌顯示|標籤寫|寫著)\s*(?:是|為|:|：)?\s*[「」\"]?(\d{4,5})[」\"]?',
                r'寫著[「」]?(\d{1,2},\d{3})[」]?',
                r'寫著[「」]?(\d{4,5})[」]?',
                r'\$\s*(\d{1,2},\d{3})',
                r'\$\s*(\d{4,5})',
            ]
            desc_price = None
            for pattern in desc_price_patterns:
                match = re.search(pattern, thinking_text)
                if match:
                    desc_price = match.group(1).strip().replace(",", "")
                    break

            # 驗證型號一致性
            current_model = data_obj.get("model")
            rescue_blocked_by_distant_view = should_block_rescue_from_distant_view(data_obj.get("view_type"), thinking_text)
            if (
                is_followme_standard_name(current_model)
                and has_negative_followme_context(thinking_text)
                and not has_positive_followme_physical_clue(thinking_text)
                and not has_followme_display_fixture_clue(thinking_text)
            ):
                console.print(f"[yellow]⚠️ [FollowMe 排除] 獨白明確說沒有 FollowMe 支架/底座，清除標準化 FollowMe 型號[/yellow]")
                data_obj["model"] = None
                current_model = None
            elif is_followme_standard_name(current_model):
                normalized_followme = normalize_followme_model(current_model, data_obj.get("price"), thinking_text)
                if normalized_followme and normalized_followme != current_model:
                    data_obj["model"] = normalized_followme
                    current_model = normalized_followme
            main_label_model = extract_main_label_model(thinking_text)
            if not current_model and main_label_model and not rescue_blocked_by_distant_view:
                verified_main_label = strict_known_model(main_label_model, valid_models_list)
                if verified_main_label:
                    console.print(f"[green]✅ [主角標籤救援] 從描述補回已驗證型號: {verified_main_label}[/green]")
                    data_obj["model"] = verified_main_label
                    current_model = verified_main_label
                else:
                    data_obj["model_validation_failed"] = True
                    data_obj["rejected_model"] = main_label_model
            if not current_model and desc_model and desc_model in valid_models_list and not desc_model_is_speculative and not model_rescue_blocked and not rescue_blocked_by_distant_view:
                console.print(f"[green]✅ [獨白救援] 從描述補回型號: {desc_model}[/green]")
                data_obj["model"] = desc_model
                current_model = desc_model
            elif current_model and desc_model:
                # 簡單清理
                c_curr = current_model.replace('-', '').strip().upper()
                c_desc = desc_model.replace('-', '').strip().upper()
                if c_curr != c_desc and c_desc in valid_models_list:
                    console.print(f"[yellow]⚠️ [獨白驗證] JSON型號({c_curr}) 與描述型號({c_desc}) 衝突，清除後重讀[/yellow]")
                    data_obj["rejected_model"] = f"{c_curr}|{c_desc}"
                    data_obj["model"] = None
                    data_obj["model_validation_failed"] = True

            # 驗證價格一致性
            current_price = data_obj.get("price")
            if desc_price and not rescue_blocked_by_distant_view:
                rescued_price = clean_monitor_price(desc_price, context_text=thinking_text)
                if rescued_price:
                    current_digits = "".join(c for c in str(current_price or "") if c.isdigit())
                    rescued_digits = "".join(c for c in str(rescued_price or "") if c.isdigit())
                    followme_price_context = "FOLLOWME" in str(data_obj.get("model") or "").upper() or "FOLLOWME" in thinking_text.upper()
                    if not current_price:
                        console.print(f"[green]✅ [獨白救援] 從思考過程中補回價格: {rescued_price}[/green]")
                        data_obj["price"] = rescued_price
                    elif current_digits and rescued_digits and current_digits != rescued_digits and followme_price_context:
                        console.print(f"[yellow]⚠️ [價格衝突] JSON={current_digits}、描述={rescued_digits}，清除後重讀[/yellow]")
                        data_obj["price"] = None
                        data_obj["price_conflict_detected"] = True
                else:
                    console.print(f"[dim]⚠️ [價格攔截] 獨白價格 {desc_price} 2000 元以下或格式不合，未補回[/dim]")

        cleaned_final_price = clean_monitor_price(data_obj.get("price"), context_text=thinking_text)
        if data_obj.get("price") and not cleaned_final_price:
            console.print(f"[dim]⚠️ [價格攔截] 最終價格 {data_obj.get('price')} 2000 元以下或格式不合 -> 清除[/dim]")
            data_obj["price"] = None
        elif cleaned_final_price:
            data_obj["price"] = cleaned_final_price

        if should_block_rescue_from_distant_view(data_obj.get("view_type"), thinking_text):
            if data_obj.get("model") or data_obj.get("price"):
                console.print("[dim]⚠️ [遠景保護] 遠景描述中的零散型號/價格不補入正式答案[/dim]")
            data_obj["model"] = None
            data_obj["price"] = None
        elif should_demote_distant_to_single_review(data_obj.get("view_type"), thinking_text):
            console.print("[yellow]⚠️ [遠景降級] 遠景答案含單一主角/側標/價牌/FollowMe 線索 → 改單機待補，不當遠景放行[/yellow]")
            data_obj["view_type"] = "單機"
            data_obj["category"] = "單機"
            data_obj["screen_status"] = data_obj.get("screen_status") or "正常"

        side_label_followme = rescue_followme_32_from_side_label(thinking_text)
        if side_label_followme and data_obj.get("view_type") == "遠景":
            console.print("[yellow]⚠️ [FollowMe 側標救援] 讀到 FollowMe 4K/32 側標與 12,900-13,990 價牌，覆蓋遠景誤判[/yellow]")
            data_obj["view_type"] = "單機"
            data_obj["category"] = "單機"
            data_obj["screen_status"] = data_obj.get("screen_status") or "正常"
            data_obj["quality_issue"] = ""
            data_obj["model"] = side_label_followme["model"]
            data_obj["price"] = side_label_followme["price"]

        corrected_model = correct_common_model_price_conflict(data_obj.get("model"), data_obj.get("price"), thinking_text)
        if corrected_model != data_obj.get("model"):
            console.print(f"[yellow]⚠️ [型號價格校正] {data_obj.get('model')} + {data_obj.get('price')} 不合理，改為 {corrected_model}[/yellow]")
            data_obj["model"] = corrected_model

        other_brand_model = infer_other_brand_model(thinking_text, data_obj.get("model"))
        if other_brand_model and data_obj.get("view_type") != "遠景" and not is_samsung_model_like(data_obj.get("model")):
            data_obj["model"] = other_brand_model

        if should_clear_non_samsung_price(data_obj.get("model"), thinking_text):
            console.print("[dim]⚠️ [非三星攔截] 主體被描述為非三星且無三星型號，清除價格[/dim]")
            data_obj["price"] = None
            if (
                any(term in thinking_text for term in ["展示區", "多台", "貨架", "遠景"])
                and not has_positive_followme_physical_clue(thinking_text)
                and not has_followme_display_fixture_clue(thinking_text)
            ):
                data_obj["view_type"] = "遠景"

        inferred_followme = None if should_block_borrowed_model_rescue(thinking_text) else infer_followme_from_physical_clues(data_obj.get("price"), thinking_text)
        if inferred_followme and data_obj.get("view_type") == "遠景":
            console.print(f"[yellow]⚠️ [FollowMe 遠景救援] 獨白描述支架/托盤/價牌，遠景改為單機: {inferred_followme}[/yellow]")
            data_obj["view_type"] = "單機"
            data_obj["screen_status"] = data_obj.get("screen_status") or "正常"
            data_obj["model"] = inferred_followme
        elif inferred_followme and not data_obj.get("model"):
            console.print(f"[green]✅ [FollowMe 實體線索救援] 補回型號: {inferred_followme}[/green]")
            data_obj["model"] = inferred_followme

        if data_obj.get("view_type") != "遠景":
            current_model = data_obj.get("model")
            current_price = data_obj.get("price")
            has_model = bool(current_model) and str(current_model).lower() not in ("null", "none", "")
            has_price = bool(current_price) and str(current_price).lower() not in ("null", "none", "")
            dv_keywords = [
                "遠景", "多台", "展示區", "展示牆", "貨架", "海報", "廣告",
                "整排", "一排", "一整排", "牆上", "多支", "多螢幕", "陳列架",
                "非三星", "其他品牌", "多品牌",
            ]
            dv_exclusions = ["同一台", "只有一台", "清晰可讀", "主角", "價牌清晰", "標籤清晰", "型號清晰"]
            has_dv_clue = thinking_text and any(kw in thinking_text for kw in dv_keywords)
            has_single_clue = thinking_text and (
                any(excl in thinking_text for excl in dv_exclusions)
                or has_positive_followme_physical_clue(thinking_text)
                or has_followme_display_fixture_clue(thinking_text)
            )

            # [v19.8] Strong guard: no model + no price + distant-view clue => 遠景
            if (
                not has_model
                and not has_price
                and has_dv_clue
                and not has_single_clue
                and has_explicit_distant_layout_evidence(thinking_text)
            ):
                console.print("[yellow]⚠️ [遠景守衛] 無型號+無價格+獨白含遠景線索 → 改遠景、清 model/price[/yellow]")
                data_obj["view_type"] = "遠景"
                data_obj["category"] = "遠景"
                data_obj["model"] = None
                data_obj["price"] = None
                data_obj["screen_status"] = ""
            elif (
                not has_model
                and has_price
                and has_dv_clue
                and not has_single_clue
                and has_explicit_distant_layout_evidence(thinking_text)
            ):
                console.print("[yellow]⚠️ [遠景守衛] 無型號+有價格+獨白含遠景線索 → 改遠景、清 model/price[/yellow]")
                data_obj["view_type"] = "遠景"
                data_obj["category"] = "遠景"
                data_obj["model"] = None
                data_obj["price"] = None
                data_obj["screen_status"] = ""

        # 4. Auto-Calculate Quality Issue
        p_val = data_obj.get("price")
        m_val = data_obj.get("model")

        if p_val and not m_val:
            data_obj["quality_issue"] = "不合格-沒有規格牌"
        elif m_val and not p_val:
            data_obj["quality_issue"] = "不合格-沒有價格牌"
        elif m_val and p_val:
             data_obj["quality_issue"] = "無"
        else:
             if data_obj.get("view_type") == "遠景":
                 data_obj["quality_issue"] = "無"
             else:
                 # [v18.02] Distinguish 'Missing' vs 'Unclear' based on Description keywords
                 desc_keywords_unclear = ["不清", "模糊", "反光", "遮擋", "無法辨識", "看不到"]
                 is_unclear = any(k in thinking_text for k in desc_keywords_unclear) if thinking_text else False

                 if is_unclear:
                     data_obj["quality_issue"] = "不合格-照不清楚"
                 else:
                     data_obj["quality_issue"] = "不合格-沒有規格和價格牌"

        # Update final result
        if isinstance(result_json, dict):
            for k, v in data_obj.items():
                if k in result_json or k in EVIDENCE_KEYS or k in PRESENTATION_JSON_KEYS or k in {"raw_objects", "merge_mode", "merge_rejected_reason"}:
                    result_json[k] = v
            result_json["raw_objects"] = [str(item.get("raw") or "")[:12000] for item in locals().get("raw_objects", [])[:3]]
            result_json["merge_mode"] = locals().get("merge_mode", "none")
            result_json["merge_rejected_reason"] = locals().get("merge_rejected_reason", "")

            # [Safety] Check for weird keys containing newlines (The specific user error)
            # If we find them, try to fix them by stripping whitespace
            for k in list(result_json.keys()):
                if isinstance(k, str) and ('\n' in k or ' ' in k) and k.strip().strip('"') in ['category', 'model', 'price', 'black_screen']:
                    clean_k = k.strip().strip('"')
                    val = result_json.pop(k)
                    result_json[clean_k] = val
                    if orchestrator: orchestrator.log_system(f"🔧 修復畸形鍵名: {repr(k)} -> {clean_k}")

        # --- Emergency Extraction: Thinking Fallback (REMOVED v9.17) ---
        # 之前的邏輯會因為思考中出現 "非 FollowMe" 而誤抓 "FollowMe"，造成重大誤判。
        # 現在我們只信任模型最終輸出的 JSON。
        # if (not result_json.get("model") ...):
        #    ... (DELETED) ...

        # --- Validation & Cleaning ---
        try:
             # [v17.09] Thinking Text is already extracted from Schema 'desc' field
            if not thinking_text:
                thinking_text = "..." # Fallback

            # Remove any unwanted residue (like [思考] if AI ignores prompt)
            # [v17.18 Safety] Ensure string
            if thinking_text is None: thinking_text = "..."
            thinking_text = str(thinking_text).replace('[思考]', '').replace('觀察內容...', '').replace('Observation:', '').replace('Observation', '').strip()
            # [v18.58] Remove markdown code block markers
            thinking_text = thinking_text.replace('```json', '').replace('```', '').strip()

            # [v17.26 Fix] Aggressively strip "Observation:" prefix
            if thinking_text:
                import re
                thinking_text = re.sub(r'(?i)^observation:\s*', '', thinking_text).strip()

                # [v18.99] 去除重複的「整體符合「遠景」條件」（AI 有時會講兩次）
                distant_phrase = '整體符合「遠景」條件'
                if thinking_text.count(distant_phrase) > 1:
                    # 只保留第一次出現
                    first_idx = thinking_text.find(distant_phrase)
                    end_of_first = first_idx + len(distant_phrase)
                    thinking_text = thinking_text[:end_of_first].strip()

            # Log to Frontend (One time only)
            if thinking_text and orchestrator:
                # [v9.64 Sanitization Logic Preserved]
                clean_think = thinking_text
                replacements = {
                    "model=null": "無型號",
                    "price=null": "無價格",
                    "category=": "類別為",
                    "null": "無",
                    "false": "否",
                    "true": "是"
                }
                for code_term, natural_term in replacements.items():
                    clean_think = re.sub(re.escape(code_term), natural_term, clean_think, flags=re.IGNORECASE)

                # Do not write the raw model narration into the durable UI log yet.
                # Post-processing may rescue false distant-view / FollowMe cases later;
                # the user-facing history must show the final corrected narration.

            # ... (Rest of existing validation logic for model/price/cat) ...
            parsed_model = result_json.get("model")
            if parsed_model:
                parsed_model = parsed_model.strip().upper()

            # ... (Model matching remains same) ...
            valid_models_list = orchestrator.model_matcher.valid_models if orchestrator and orchestrator.model_matcher else []
            if parsed_model and parsed_model not in valid_models_list:
                # Friendly FollowMe names were already normalized from explicit
                # machine-readable physical evidence.  A second generic SKU-list
                # pass must not erase that verified classification.
                if not is_followme_model(parsed_model):
                    exact_model = strict_known_model(parsed_model, valid_models_list)
                    result_json["model"] = exact_model

            # ... (Category & Price logic remains same) ...
            current_cat = result_json.get("category")

            # [v17.23 Fix] Force Quality Issue if Model/Price missing in Single Unit
            if result_json.get("view_type") == "單機" or current_cat == "單機":
                 # If Model is missing and no specific failure reason given, it MUST be "No Spec Card"
                if not result_json.get("model") and not result_json.get("quality_issue"):
                     result_json["quality_issue"] = "不合格-沒有規格牌"

                # If Price is missing and no failure reason, it might be "No Price Card"
                # (But check if Spec Card logic took precedence? Standard says Spec > Price)
                elif not result_json.get("price") and not result_json.get("quality_issue"):
                     result_json["quality_issue"] = "不合格-沒有價格牌"

            # Re-eval current_cat after forcing QI
            qi = result_json.get("quality_issue")
            if qi and qi not in ["無", "無標籤(正常)"]:
                 result_json["category"] = f"不合格-{qi}"

            if current_cat == "單機" and not result_json.get("model"):
                # [v16.33 Refinement] Strictly map requested 4 unqualified types
                qi = result_json.get("quality_issue")
                unqualified_types = ["照不清楚", "沒有規格牌", "沒有價格牌", "沒有規格和價格牌"]

                if qi in unqualified_types:
                    result_json["category"] = f"不合格-{qi}"
                elif qi == "無標籤(正常)":
                    result_json["category"] = "單機"
                else:
                    # Fallback for other issues (like glare/blur) if no model identified
                    result_json["category"] = "不合格-照不清楚"

            # [v17.13 Optimization] Relaxed Price Validation
            # User Feedback: Many valid prices are plain 4-digit numbers (e.g. 4990).
            # [v18.05 Strict Gate] Explicitly BAN spec units
            raw_price = result_json.get("price")
            if raw_price and isinstance(raw_price, str):
                # Check for forbidden spec units AND props
                # "R", "HZ", "MS" -> Specs
                # "SWITCH", "NINTENDO", "PS5" -> Props
                forbidden = ['R', 'HZ', 'MS', 'CM', 'MM', 'X', 'INCH', '”', '"', 'SWITCH', 'NINTENDO', 'PS5', 'SONY']
                if any(u in raw_price.upper() for u in forbidden):
                     if orchestrator:
                         console.print(f"[dim]⚠️ [價格攔截] {raw_price} 含規格關鍵字[/dim]")
                     result_json["price"] = None
                else:
                    # Proceed with digit cleaning
                     pass
            # if raw_price and isinstance(raw_price, str) and ',' not in raw_price and '$' not in raw_price and 'NT' not in raw_price:
            #     result_json["price"] = None

            # [v14.1 Fix] Backward Compatibility: Derive 'category' from new fields if missing
            # [v17.30 Logic] Map view_type/quality_issue to legacy 'category' for UI/Dashboard
            view_type = result_json.get("view_type")
            screen_status = result_json.get("screen_status")
            quality_issue = result_json.get("quality_issue")

            # [v18.99 Backup] 獨白關鍵字備援檢測：若 JSON 沒說遠景，但獨白明確說了，則強制修正
            if thinking_text and '整體符合「遠景」條件' in thinking_text:
                has_single_unit_evidence = has_strong_single_unit_evidence(thinking_text)
                confirmed_followme = has_confirmed_followme_evidence(
                    result_json.get("model"),
                    thinking_text,
                )
                if (
                    view_type != '遠景'
                    and not has_single_unit_evidence
                    and not confirmed_followme
                    and has_explicit_distant_layout_evidence(thinking_text)
                ):
                    console.print(f"[yellow]⚠️ [獨白備援] JSON 寫 {view_type} 但獨白說遠景 → 強制修正為遠景[/yellow]")
                    view_type = '遠景'
                    result_json['view_type'] = '遠景'
                    result_json['model'] = None
                    result_json['price'] = None
                    result_json['quality_issue'] = ''
                elif has_single_unit_evidence or confirmed_followme:
                    console.print("[yellow]⚠️ [獨白備援] 單機或 FollowMe 線索已確認，略過遠景強制修正[/yellow]")

            if view_type == '遠景':
                result_json['category'] = '遠景'
            elif screen_status == '黑屏':
                result_json['category'] = '不合格-黑屏'
                result_json['black_screen'] = True
            elif screen_status == '藍屏':
                result_json['category'] = '不合格-藍屏'
            elif quality_issue and quality_issue not in ['無', '', '正常']:
                # Map new logic to legacy categories if they match common patterns
                if "照不清楚" in quality_issue: result_json['category'] = "不合格-照不清楚"
                elif "沒有規格" in quality_issue: result_json['category'] = "不合格-沒有規格牌"
                elif "沒有價格" in quality_issue: result_json['category'] = "不合格-沒有價格牌"
                else: result_json['category'] = '單機' # Allow price-only or model-only to be 'Single Unit'
            else:
                result_json['category'] = '單機'

            # Final fallback
            if not result_json.get('category'):
                 result_json['category'] = '單機' if result_json.get('price') or result_json.get('model') else '失敗'

            # [v14.6 Fix] Forced Cleaning of labels to prevent UI clutter
            # Remove "Normal" states so they don't trigger the red error box in UI
            if result_json.get("screen_status") in ["顯示畫面", "無", "正常", "None", None, "null"]:
                result_json["screen_status"] = ""
            if result_json.get("quality_issue") in ["無", "正常", "None", None, "null"]:
                result_json["quality_issue"] = ""

            # Trim whitespaces just in case
            if result_json.get("screen_status"): result_json["screen_status"] = result_json["screen_status"].strip()
            if result_json.get("quality_issue"): result_json["quality_issue"] = result_json["quality_issue"].strip()

            # Final fallback if still nothing
            if not result_json.get('category'):
                 result_json['category'] = '失敗' if not result_json.get('model') else '單機'

        except Exception as e:
            log.error(f"Validation Error: {e}")
            if orchestrator: orchestrator.log_system(f"⚠️ 解析異常: {str(e)}")

        # Final fail-closed validation after every rescue/correction path.
        final_guard_model = result_json.get("model")
        if final_guard_model and not is_followme_standard_name(final_guard_model) and not is_other_brand_model(final_guard_model):
            known_final_model = strict_known_model(final_guard_model, valid_models_list)
            if known_final_model:
                result_json["model"] = known_final_model
            elif should_compare_official_price(fname) and is_samsung_model_like(final_guard_model) and try_discover_model(final_guard_model):
                result_json["model"] = final_guard_model
            else:
                result_json["rejected_model"] = str(final_guard_model)
                result_json["model"] = None
                result_json["model_validation_failed"] = True

        # [v18.67] 官方價格驗證
        try:
            model_for_price = result_json.get("model")
            price_for_validate = result_json.get("price")
            compare_official = should_compare_official_price(fname)
            if is_other_brand_model(model_for_price):
                compare_official = False

            # [v18.69] 自動發現新型號
            if compare_official and model_for_price:
                try_discover_model(model_for_price)

            if compare_official and model_for_price and price_for_validate and str(price_for_validate).isdigit():
                price_int = int(price_for_validate)
                price_check = validate_ocr_price(model_for_price, price_int)
                result_json['price_status'] = price_check['status']
                result_json['price_symbol'] = price_check['symbol']
                result_json['official_price'] = price_check['official_price']
                result_json['price_diff_percent'] = price_check['diff_percent']

                # 價格差異 > 20% 時觸發警告
                if price_check['status'] in ['high', 'low'] and price_check['official_price']:
                    diff = price_check['diff_percent']
                    official = price_check['official_price']
                    symbol = price_check['symbol']
                    if orchestrator:
                        orchestrator.log_system(f"⚠️ 價格警告: {model_for_price} OCR=${price_int:,} {symbol} 官方=${official:,} (差異{diff:+.1f}%)")
            elif not compare_official and price_for_validate:
                result_json['price_status'] = 'not_compared'
                result_json['price_symbol'] = ''
                result_json['official_price'] = ''
                result_json['price_diff_percent'] = ''
            elif not compare_official:
                result_json['price_status'] = 'not_compared'
                result_json['price_symbol'] = ''
                result_json['official_price'] = ''
                result_json['price_diff_percent'] = ''
        except Exception as e:
            log.warning(f"Price validation error: {e}")

        # [v9.71 Universal Summary Log]
        # MOVED OUT OF ELSE BLOCK to guarantee execution.
        if orchestrator:
            # [THINK] log is already emitted in the validation block above via [THINK] prefix
            # Do NOT log again here to prevent duplicate in UI

            cat = result_json.get("category") or ""
            mod = result_json.get("model") or "無型號"
            pri = result_json.get("price") or "無價格"
            blk = result_json.get("black_screen")

            # [v9.90 Personalized Summary Log]
            status_emoji = "▶️"
            category_name = "遠景" if "遠景" in cat else "單機"
            summary_line = f"{status_emoji} 判斷是{category_name}： {mod} / {pri}"
            if blk:
                summary_line += " / 黑屏"
            orchestrator.log_system(summary_line)
            # [v14.4 Fix] Keep self-talk visible, but don't clobber a monologue already being streamed.
            # If the buffer is empty, try to extract the natural-language monologue from saved thinking.
            if not orchestrator.stream_buffer:
                if result_json.get('thinking'):
                    mono = extract_natural_monologue(str(result_json.get('thinking', '')))
                    orchestrator.stream_buffer = to_tc(mono[:800]) if mono else ""
                else:
                    orchestrator.stream_buffer = ""

    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        error_str = str(e).lower()

        # [v18.99] 友善錯誤訊息：針對 LM Studio 常見錯誤
        if "failed to process image" in error_str:
            friendly_msg = "❌ AI 回應無法處理圖片，可能是圖片格式不支援或本機模型異常"
            log.error(f"LM Studio 圖片處理失敗: {e}")
            if orchestrator:
                orchestrator.log_system(friendly_msg)
                console.print(f"[red]{friendly_msg}[/red]")
                console.print(f"[dim]💡 建議: 確認 LM Studio 模型是否支援圖片輸入 (需要 Vision 模型如 Qwen-VL)[/dim]")
        else:
            log.error(f"Analysis Failed: {e}")
            log.error(f"詳細錯誤: {error_detail}")
            if orchestrator:
                orchestrator.log_system(f"❌ 系統錯誤: {str(e)}")
                console.print(f"[red]❌ 詳細錯誤追蹤:\n{error_detail}[/red]")

    # [v17.30] Include thinking process for sidecar logging and PERSISTENCE
    # Ensure we use the safest thinking_text version captured earlier
    result_json = finalize_evidence_contract(result_json, locals().get("full_response_text", ""))
    result_json = attach_spatial_evidence_trace(result_json, scene_tiles, ocr_attempt)
    final_think = thinking_text if 'thinking_text' in locals() else ""
    result_json['thinking'] = build_final_display_thinking(result_json, final_think)
    result_json["independent_pass"] = True
    result_json["prior_answer_exposed"] = False
    result_json["prompt_contamination"] = False
    if orchestrator:
        final_display_thinking = str(result_json.get('thinking') or '').strip()
        if final_display_thinking:
            orchestrator.log_system(f"[THINK] {final_display_thinking}")
            if getattr(orchestrator, 'stream_file', None) == fname:
                orchestrator.stream_buffer = to_tc(final_display_thinking[:800])

    return result_json

# --- Flask API Routes ---


@flask_app.route('/api/list_dirs', methods=['GET'])
def list_dirs():
    """列出可用資料夾"""
    try:
        # [v19.8] List actual photo source folders instead of repo root
        source_root = SOURCE_ROOT
        if source_root.exists():
            dirs = sorted([
                str(p.relative_to(source_root))
                for p in source_root.iterdir()
                if p.is_dir() and not p.name.startswith('.')
            ], key=lambda x: ("商化" in x, "照片" in x, x), reverse=True)
            return jsonify(dirs)
        # Fallback to current directory
        entries = os.listdir('.')
        dirs = [e for e in entries if os.path.isdir(e) and not e.startswith('.') and e not in ['dashboard', 'runs', '__pycache__', '.venv', 'node_modules']]
        dirs.sort(key=lambda x: ("照片" in x or "商化" in x), reverse=True)
        return jsonify(dirs)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@flask_app.route('/api/status', methods=['GET'])
def get_status():
    """獲取系統狀態"""
    if not orchestrator:
        return jsonify({"error": "Orchestrator 未初始化"}), 500

    try:
        # 構建狀態對象
        metrics = orchestrator.get_performance_metrics()

        # [🔋 v14.9.1 DEDUPLICATED STATS]
        # Use full record aggregation for accurate, deduplicated counts
        total_success_list = orchestrator.get_all_records()
        total_failed_list = orchestrator.get_all_failed_records()

        total_success = len(total_success_list)
        total_failed = len(total_failed_list)

        review_required = sum(1 for record in total_success_list if _status_needs_review(record))
        verified = sum(
            1 for record in total_success_list
            if _status_flag_true(record.get("auto_verified")) and not _status_needs_review(record)
        )
        verification_unknown = max(0, total_success - verified - review_required)

        stats = {
            "processed": total_success + total_failed,
            # Backward compatibility: success means a non-system-failure OCR
            # record.  The dashboard labels it "已處理", never "品質通過".
            "success": total_success,
            "failed": total_failed,
            "verified": verified,
            "review_required": review_required,
            "verification_unknown": verification_unknown,
            "total": orchestrator.stats.get('total', 0)
        }

        overall_progress = build_overall_progress(
            current_folder=getattr(orchestrator, 'image_dir', None),
            current_stats=stats,
        )
        # A backend restart intentionally starts with an empty in-memory batch
        # total.  Preserve the audited folder denominator while idle so the UI
        # never renders a misleading "1,504 / 0" progress label.
        if not orchestrator.is_running and not stats["total"]:
            current_folder = overall_progress.get("current_folder") or {}
            stats["total"] = int(current_folder.get("image_count") or stats["processed"] or 0)

        # Keep live self-talk tied to the active image. Showing the previous
        # result here makes the dashboard appear one image out of sync.
        current_file = getattr(orchestrator, 'current_file', None)
        stream_file = getattr(orchestrator, 'stream_file', None)
        stream_buffer = str(orchestrator.stream_buffer) if stream_file == current_file else ""

        # [OCG-v2.3] Expose current model and per-image cost info
        current_model = getattr(orchestrator, 'last_model_name', None) or model_name_global or "未知"
        last_token_usage = getattr(orchestrator, 'last_token_usage', None) or {}
        last_image_cost = getattr(orchestrator, 'last_image_cost', None)
        if current_model in {"qwen/qwen3-vl-8b", "qwen3vl8b-ocr"} or "lm-studio" in str(current_model).lower():
            last_image_cost = 0.0

        # A completed staging rerun can leave its last 50 display entries in
        # memory.  They are useful only while a batch is live; returning them
        # while idle makes the dashboard request deleted staging paths.
        presentation_queue = _presentation_payload(orchestrator)
        current_dir_text = str(getattr(orchestrator, 'image_dir', None) or "")
        period_match = re.search(r"(?<!\d)(20\d{4})(?!\d)", current_dir_text)
        current_attempt = int((getattr(orchestrator, "auto_attempts", {}) or {}).get(current_file, 0) or 0)
        review_progress = {
            "mode": "current_year_review" if "_ocr_staging" in current_dir_text else "initial_ocr",
            "period": period_match.group(1) if period_match else "",
            "processed": stats["processed"],
            "success": stats["success"],
            "failed": stats["failed"],
            "verified": stats["verified"],
            "review_required": stats["review_required"],
            "verification_unknown": stats["verification_unknown"],
            "total": stats["total"],
            "current_pass": current_attempt,
            "current_file": current_file,
        }

        status_obj = {
            "version": VERSION,
            "status_contract_version": "compact-v2",
            "frontend_asset_fingerprint": get_frontend_asset_fingerprint(),
            "accuracy_profile": ACCURACY_PROFILE,
            "manual_rule_count": int(getattr(orchestrator, "manual_rule_count", 0) or 0),
            "current_file": current_file or 'None',
            "stream_file": stream_file,
            "latest_result_file": getattr(orchestrator, 'latest_result_file', None),
            "current_model": current_model,
            "last_token_usage": last_token_usage,
            "last_image_cost": last_image_cost,
            "stats": stats,
            "overall_progress": overall_progress,
            "review_progress": review_progress,
            "runtime_health_fuse": read_runtime_health_fuse(AUDIT_DIR),
            "metrics": metrics,
            "stream_buffer": stream_buffer, # 強制轉字串避免類型錯誤
            "presentation_queue": presentation_queue,
            "presentation_sequence": presentation_queue[-1].get("presentation_sequence") if presentation_queue else 0,
            "presentation_sequence_durable": True,
            "presentation_run_id": str(getattr(orchestrator, "current_run_id", "") or ""),
            "lm_logs": list(orchestrator.system_logs)[-200:], # [v11.9 Fix] Limit logs to last 200 to prevent payload bloat
            # Kept for API compatibility, but bounded and stripped of image/raw
            # payloads. The current dashboard uses presentation_queue instead.
            "recent_results": [_compact_status_result(item) for item in list(orchestrator.recent_results)[:10]],
            # "failed_files": getattr(orchestrator, 'failed_files', []), # [v11.9 Fix] REMOVED! Too huge, causes API timeout.
            "is_running": orchestrator.is_running,
            "image_dir": getattr(orchestrator, 'image_dir', None), # [v19.8] Current source folder for dashboard
            "source_root": str(SOURCE_ROOT),
            "current_relative_dir": str(Path(getattr(orchestrator, 'image_dir', '')).resolve().relative_to(SOURCE_ROOT.resolve())) if getattr(orchestrator, 'image_dir', None) and str(Path(getattr(orchestrator, 'image_dir')).resolve()).startswith(str(SOURCE_ROOT.resolve())) else getattr(orchestrator, 'image_dir', None),
            "resources": {
                "cpu": psutil.cpu_percent(interval=0.1),
                "ram": psutil.virtual_memory().percent
            }
        }
        return jsonify(status_obj)
    except Exception as e:
        return jsonify({"error": f"獲取狀態失敗: {str(e)}"}), 500

@flask_app.route('/api/presentation_history/<source_item_id>', methods=['GET'])
def get_presentation_history(source_item_id):
    """按穩定照片身分讀取每輪不可變判讀歷程。"""
    if not orchestrator:
        return jsonify({"error": "Orchestrator 未初始化"}), 500
    if not re.fullmatch(r"[0-9a-f]{64}", str(source_item_id or "")):
        return jsonify({"error": "source_item_id 格式不正確"}), 400
    try:
        limit = int(request.args.get("limit", "12"))
    except (TypeError, ValueError):
        return jsonify({"error": "limit 必須是整數"}), 400
    limit = max(1, min(50, limit))
    try:
        items = orchestrator.get_presentation_history(source_item_id, limit=limit)
        return jsonify({
            "source_item_id": source_item_id,
            "count": len(items),
            "items": items,
        })
    except Exception as exc:
        log.error(f"Presentation history API error: {exc}")
        return jsonify({"error": "判讀歷程暫時無法載入"}), 500

@flask_app.route('/api/presentation_history', methods=['GET'])
def get_recent_presentation_history():
    """Restore the newest completed result cards after a dashboard refresh."""
    if not orchestrator:
        return jsonify({"error": "Orchestrator 未初始化"}), 500
    try:
        limit = int(request.args.get("limit", "200"))
    except (TypeError, ValueError):
        return jsonify({"error": "limit 必須是整數"}), 400
    limit = max(1, min(200, limit))
    scope = str(request.args.get("scope") or "").strip().lower()
    if scope not in {"", "current_batch"}:
        return jsonify({"error": "scope 必須是 current_batch"}), 400
    try:
        if scope == "current_batch":
            source_item_ids = orchestrator.get_current_source_item_ids()
            current_run_id = str(getattr(orchestrator, "current_run_id", "") or "")
            items = orchestrator.get_recent_presentation_history(
                limit=limit,
                source_item_ids=source_item_ids,
                run_id=current_run_id,
                latest_run_only=True,
            )
        else:
            source_item_ids = None
            current_run_id = ""
            items = orchestrator.get_recent_presentation_history(limit=limit)
        return jsonify({
            "count": len(items),
            "items": items,
            "scope": scope or "global",
            "source_item_ids": sorted(source_item_ids) if source_item_ids is not None else [],
            "run_id": current_run_id,
        })
    except Exception as exc:
        log.error(f"Recent presentation history API error: {exc}")
        return jsonify({"error": "最近辨識紀錄暫時無法載入"}), 500

@flask_app.route('/api/logs', methods=['GET'])
def get_logs():
    """獲取系統日誌"""
    if not orchestrator:
        return jsonify({"error": "Orchestrator 未初始化"}), 500

    try:
        # 使用分頁參數
        last = int(request.args.get('last', '0'))
        lines = int(request.args.get('lines', '50'))

        all_logs = list(orchestrator.system_logs)

        # 從last位置開始取logs
        start_idx = max(0, last)
        end_idx = min(len(all_logs), start_idx + lines)

        logs_slice = all_logs[start_idx:end_idx]

        return jsonify({
            "logs": logs_slice,
            "total": len(all_logs),
            "next_id": end_idx if end_idx < len(all_logs) else None
        })
    except Exception as e:
        return jsonify({"error": f"獲取日誌失敗: {str(e)}"}), 500

def _resolve_dashboard_image_path(filename: str) -> str | None:
    """Resolve dashboard image requests to the original photo whenever possible."""
    if not filename:
        return None

    if os.path.isabs(filename) and os.path.exists(filename):
        return filename

    safe_name = os.path.basename(filename)
    if not safe_name:
        return None

    current_dir = ""
    if orchestrator:
        current_dir = orchestrator.config.get("image_dir") or getattr(orchestrator, "image_dir", "") or ""

    if current_dir:
        current_candidate = os.path.join(current_dir, safe_name)
        if os.path.exists(current_candidate):
            return current_candidate

    cached = IMAGE_LOOKUP_CACHE.get(safe_name)
    if cached and os.path.exists(cached):
        return cached

    source_root = Path(r"D:\00_商化\00_未整理商化照片")
    if source_root.exists():
        try:
            for path in source_root.rglob(safe_name):
                if path.is_file():
                    resolved = str(path)
                    IMAGE_LOOKUP_CACHE[safe_name] = resolved
                    return resolved
        except OSError:
            return None

    return None


@flask_app.route('/api/image/<path:filename>')
def get_image(filename):
    if not orchestrator:
        return jsonify({"error": "Orchestrator is not ready"}), 503
    img_path = _resolve_dashboard_image_path(filename)
    if not img_path:
        return jsonify({"error": f"Image not found: {filename}"}), 404
    response = send_file(img_path, mimetype='image/jpeg')
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

    """提供圖片檔案"""
    if not orchestrator:
        return jsonify({"error": "系統未初始化"}), 503

    try:
        # 安全路徑處理: 支援絕對路徑或相對路徑
        if os.path.isabs(filename) and os.path.exists(filename):
            img_path = filename
        else:
            safe_name = os.path.basename(filename)
            img_path = os.path.join(orchestrator.config.get("image_dir", "photos"), safe_name)

        if not os.path.exists(img_path):
            return jsonify({"error": f"圖片不存在: {filename}"}), 404

        return send_file(img_path, mimetype='image/jpeg')
    except Exception as e:
        return jsonify({"error": f"讀取圖片失敗: {str(e)}"}), 500

@flask_app.route('/api/photos/<path:filename>')
def get_photo(filename):
    """提供圖片檔案 (備用路由)"""
    return get_image(filename)

@flask_app.route('/api/success_records')
def get_success_records():
    """提供本次 Session 的成功辨識紀錄 (全量)"""
    if not orchestrator:
        return jsonify({"error": "系統未初始化"}), 500

    # [DEBUG] Visible Proof for User
    print(f"[API] 🟢 /api/success_records called. Path: {orchestrator.image_dir}")

    # Return full run results (in memory)
    # [v11.4] Aggregated History (Current + Legacy + Previous Sessions)
    results = orchestrator.get_all_records()
    print(f"[API] 🟢 Returned {len(results)} records.")
    return jsonify(results)


@flask_app.route('/api/review_queue')
def get_review_queue():
    """Return photos blocked by the Drive upload gate for manual review."""
    try:
        year = request.args.get("year", "2026").strip()
        reason = request.args.get("reason", "").strip()
        try:
            limit = int(request.args.get("limit", "300"))
        except ValueError:
            limit = 300
        limit = max(0, min(limit, 1000))
        items, summary = _load_review_rows(year=year, reason=reason, limit=limit)
        return jsonify({
            "items": items,
            "returned": len(items),
            "total": summary.get("filtered_count", len(items)),
            "summary": summary,
        })
    except Exception as e:
        log.error(f"Review queue API error: {e}")
        return jsonify({"error": str(e)}), 500


@flask_app.route('/api/review_correction', methods=['POST'])
def save_review_correction():
    """Persist a manual review correction and optional reusable learning rule."""
    try:
        data = request.json or {}
        file_name = (data.get("file_name") or "").strip()
        source_path = (data.get("source_path") or "").strip()
        if not file_name:
            return jsonify({"error": "缺少檔名"}), 400

        parsed = _parse_review_filename(file_name)
        timestamp = datetime.now().isoformat(timespec="seconds")
        row = {
            "timestamp": timestamp,
            "file_name": file_name,
            "source_path": source_path,
            "period": data.get("period") or parsed.get("period", ""),
            "year": data.get("year") or parsed.get("year", ""),
            "review_reasons": data.get("reasons", ""),
            "corrected_view_type": data.get("view_type", ""),
            "corrected_model": data.get("model", ""),
            "corrected_price": data.get("price", ""),
            "corrected_price_symbol": data.get("price_symbol", ""),
            "note": data.get("note", ""),
            "action": data.get("action", "manual_correction"),
            "learn_rule": "1" if data.get("learn_rule") else "",
            "rule_hint": data.get("rule_hint", ""),
        }
        correction_fields = [
            "timestamp", "file_name", "source_path", "period", "year", "review_reasons",
            "corrected_view_type", "corrected_model", "corrected_price", "corrected_price_symbol",
            "note", "action", "learn_rule", "rule_hint",
        ]
        _append_csv_row(MANUAL_CORRECTIONS_PATH, correction_fields, row)

        if should_save_manual_learning_rule(data):
            rule_fields = [
                "timestamp", "rule_hint", "match_text", "view_type", "model", "price",
                "note", "example_file", "source_path",
            ]
            _append_csv_row(MANUAL_RULES_PATH, rule_fields, {
                "timestamp": timestamp,
                "rule_hint": data.get("rule_hint", ""),
                "match_text": data.get("match_text", "") or file_name,
                "view_type": data.get("view_type", ""),
                "model": data.get("model", ""),
                "price": data.get("price", ""),
                "note": data.get("note", ""),
                "example_file": file_name,
                "source_path": source_path,
            })

        if orchestrator:
            orchestrator.log_system(f"📝 待審人工校正已記錄: {file_name}")

        return jsonify({
            "status": "success",
            "message": "人工校正已記錄",
            "manual_corrections": str(MANUAL_CORRECTIONS_PATH),
            "manual_rules": str(MANUAL_RULES_PATH),
        })
    except Exception as e:
        log.error(f"Review correction API error: {e}")
        return jsonify({"error": str(e)}), 500

@flask_app.route('/')
def serve_dashboard():
    """服務主控制台"""
    return send_from_directory(flask_app.static_folder, 'index.html')

@flask_app.route('/assets/<path:path>')
def serve_dashboard_assets(path):
    """服務 Vite 靜態資源"""
    return send_from_directory(os.path.join(flask_app.static_folder, 'assets'), path)

@flask_app.route('/dashboard/optimized')
def serve_optimized_dashboard():
    """提供優化後的控制台界面"""
    try:
        import os
        current_dir = os.getcwd()
        dashboard_path = os.path.join(current_dir, 'dashboard_optimized.html')

        if not os.path.exists(dashboard_path):
            return f"Error: dashboard_optimized.html 檔案不存在於 {current_dir}", 404

        with open(dashboard_path, 'r', encoding='utf-8') as f:
            content = f.read()

        resp = Response(content, mimetype='text/html')
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp

    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        return f"Error loading dashboard: {str(e)}\n\n{error_detail}", 500

@flask_app.route('/api/check_existing_results', methods=['POST'])
def check_existing_results():
    """Check if the target image directory already has OCR result JSON files."""
    if not orchestrator:
        return jsonify({"error": "系統未初始化"}), 500
    try:
        req_data = request.json or {}
        target_dir = req_data.get('dir') or orchestrator.image_dir
        if not target_dir or not os.path.isdir(target_dir):
            return jsonify({"exists": False, "files": [], "message": "資料夾不存在"})
        files = [f for f in os.listdir(target_dir) if f.endswith('OCR成功.json')]
        return jsonify({
            "exists": len(files) > 0,
            "files": files[:10],
            "count": len(files),
            "message": f"找到 {len(files)} 個既有結果檔" if files else "沒有既有結果檔"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@flask_app.route('/api/start_batch', methods=['POST'])
def start_batch():
    """啟動批次處理 (可指定資料夾)"""
    if not orchestrator:
        return jsonify({"error": "系統未初始化"}), 500

    try:
        if orchestrator.is_running:
            return jsonify({"error": "批次處理已在執行中"}), 400

        # Get params from request
        req_data = request.json or {}
        target_dir = req_data.get('dir')
        restart = req_data.get('restart', False)
        reprocess_last_n = req_data.get('reprocess_last_n', 0)
        confirmed = req_data.get('confirmed', False)
        runtime_health_trial = req_data.get('runtime_health_trial') is True

        if target_dir and not os.path.exists(target_dir):
            return jsonify({"error": f"資料夾不存在: {target_dir}"}), 404

        selected_dir = target_dir or orchestrator.image_dir
        active_fuse = read_runtime_health_fuse(AUDIT_DIR)
        if active_fuse:
            selected_path = Path(str(selected_dir or "")).resolve()
            smoke_root = (OUTPUT_ROOT / "_ocr_staging").resolve()
            try:
                relative_smoke_path = selected_path.relative_to(smoke_root)
            except (OSError, ValueError):
                relative_smoke_path = None
            smoke_images = []
            if relative_smoke_path is not None and selected_path.is_dir():
                smoke_images = [
                    item for item in selected_path.iterdir()
                    if item.is_file() and item.suffix.lower() in {".jpg", ".jpeg", ".png"}
                ]
            safe_fuse_trial = bool(
                runtime_health_trial
                and relative_smoke_path is not None
                and "runtime_health_smoke" in str(relative_smoke_path).lower()
                and 1 <= len(smoke_images) <= 15
                and (AUDIT_DIR / "model_benchmark.lock").is_file()
                and not any(selected_path.glob("*OCR成功.json"))
                and not any(selected_path.glob("*OCR失敗.json"))
            )
            if not safe_fuse_trial:
                return jsonify({
                    "error": "內容健康門已熔斷；只允許受限隔離試跑，不得續跑正式批次",
                    "runtime_health_fuse": active_fuse,
                }), 423

        if should_compare_official_price(selected_dir):
            # [v18.67] 每次啟動當年度批次時重置價格查詢快取，確保重新從官網抓最新價格
            try:
                from skills.official_price import get_price_manager
                pm = get_price_manager()
                pm.session_fetched.clear()  # 清空「本次已查詢」記錄
                console.print("[dim]🔄 已重置價格查詢狀態，將從官網抓取最新價格[/dim]")
            except Exception as e:
                console.print(f"[yellow]⚠️ 重置價格查詢失敗: {e}[/yellow]")
        else:
            console.print("[dim]⏭️ 歷史年度資料夾：略過官網價格查詢快取重置[/dim]")

        # [v19.15] Interactive Check: If user clicked Continue and there are existing results,
        # always ask for confirmation so they can choose rerun all / test last 5 / continue pending.
        if not confirmed and not restart and reprocess_last_n == 0:
            scan_res = orchestrator.get_pending_files()
            existing_files = []
            if orchestrator.image_dir and os.path.isdir(orchestrator.image_dir):
                existing_files = [
                    f for f in os.listdir(orchestrator.image_dir)
                    if f.endswith(('OCR成功.json', 'OCR失敗.json'))
                ]
            # Show dialog when no pending files OR when there are existing results
            if not scan_res['pending_files'] or existing_files:
                return jsonify({
                    "status": "needs_confirmation",
                    "message": "請選擇處理方式：",
                    "files_count": len(scan_res['all_files']),
                    "pending_files_count": len(scan_res['pending_files']),
                    "existing_results_count": len(existing_files),
                    "existing_files": existing_files[:3]
                })

        # 開始批次處理
        # [v19.1] Save Config on Start
        started = orchestrator.start_batch(
            restart=restart,
            reprocess_last_n=reprocess_last_n,
            image_dir=selected_dir,
        )
        if not started:
            return jsonify({"error": "批次仍在執行或停止中，請稍候再試"}), 409
        save_last_config(orchestrator.image_dir, model_name_global)
        mode_text = "重新啟動" if restart else "繼續執行"
        return jsonify({"status": "started", "message": f"批次處理已{mode_text} (目錄: {orchestrator.image_dir})"})

    except Exception as e:
        return jsonify({"error": f"啟動失敗: {str(e)}"}), 500

@flask_app.route('/api/set_work_dir', methods=['POST'])
def set_work_dir():
    """[v19.0] 允許前端在不啟動批次的情況下切換工作目錄，以便查看歷史紀錄"""
    if not orchestrator:
        return jsonify({"error": "系統未初始化"}), 500

    try:
        data = request.json
        target_dir = data.get('dir')

        if not target_dir:
            return jsonify({"error": "Missing dir parameter"}), 400

        if os.path.exists(target_dir):
            switched, reason = orchestrator.set_work_dir(target_dir)
            if not switched:
                return jsonify({"error": reason}), 409
            # [v19.1] Save Config on Switch
            save_last_config(target_dir, model_name_global)

            # [v19.6 Fix] Refresh stats immediately for correct dashboard counts
            orchestrator.refresh_stats()

            orchestrator.log_system(f"📂 工作目錄已切換至: {target_dir}")
            return jsonify({"status": "success", "message": f"Switched to {target_dir}"})
        else:
            return jsonify({"error": f"Directory not found: {target_dir}"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@flask_app.route('/api/set_llm_config', methods=['POST'])
def set_llm_config():
    """[v19.10] 熱切換 LLM 引擎 (本機 LM Studio / OpenCode Go 等)"""
    global api_client, model_name_global

    if orchestrator and orchestrator.is_running:
        return jsonify({"error": "批次處理執行中，請先停止後再切換引擎"}), 400

    try:
        data = request.json or {}
        new_api_base = data.get('api_base', '').strip()
        new_api_key = data.get('api_key', '').strip()
        new_model = data.get('model', '').strip()

        if not new_api_base or not new_model:
            return jsonify({"error": "缺少 api_base 或 model 參數"}), 400

        if not new_api_key:
            new_api_key = 'lm-studio' if '127.0.0.1' in new_api_base or 'localhost' in new_api_base else ''

        old_model = model_name_global
        try:
            new_client = OpenAI(base_url=new_api_base, api_key=new_api_key, timeout=180.0, max_retries=1)
        except Exception as e:
            return jsonify({"error": f"建立 OpenAI client 失敗: {e}"}), 500

        api_client = new_client
        model_name_global = new_model
        save_last_config(orchestrator.image_dir if orchestrator else '', model_name_global,
                         api_base=new_api_base, api_key=new_api_key)

        orchestrator.log_system(
            f"🔄 AI 引擎已切換: {old_model} → {new_model} (API: {new_api_base})",
            with_timestamp=True
        )
        console.print(f"[bold green]🔄 LLM 引擎已切換: {old_model} → {new_model}[/bold green]")
        console.print(f"[green]   API Base: {new_api_base}[/green]")

        return jsonify({
            "status": "success",
            "message": f"已切換至 {new_model}",
            "model": new_model,
            "api_base": new_api_base
        })
    except Exception as e:
        return jsonify({"error": f"切換失敗: {str(e)}"}), 500


@flask_app.route('/api/llm_config', methods=['GET'])
def get_llm_config():
    """[v19.10] 取得目前 LLM 引擎設定"""
    try:
        last_config = load_last_config()
        return jsonify({
            "model": model_name_global,
            "api_base": last_config.get("last_api_base", "http://127.0.0.1:1234/v1"),
            "api_key_set": bool(last_config.get("last_api_key")),
            "available_engines": [
                {"id": "local_lm_studio", "name": "本機 LM Studio (Qwen3-VL)", "vision": True,
                 "api_base": "http://127.0.0.1:1234/v1", "model": "qwen/qwen3-vl-8b"},
                {"id": "opencode_mimo_omni", "name": "OpenCode Go: mimo-v2-omni (多模態)", "vision": True,
                 "api_base": "https://opencode.ai/zen/go/v1", "model": "mimo-v2-omni"},
                {"id": "opencode_qwen37_max", "name": "OpenCode Go: qwen3.7-max", "vision": True,
                 "api_base": "https://opencode.ai/zen/go/v1", "model": "qwen3.7-max"},
                {"id": "opencode_qwen37_plus", "name": "OpenCode Go: qwen3.7-plus", "vision": True,
                 "api_base": "https://opencode.ai/zen/go/v1", "model": "qwen3.7-plus"},
            ]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@flask_app.route('/api/stop', methods=['POST'])
def stop_batch():
    """停止批次處理"""
    if not orchestrator:
        return jsonify({"error": "系統未初始化"}), 500

    try:
        orchestrator.stop_batch()
        return jsonify({"status": "stopped", "message": "批次處理已停止"})
    except Exception as e:
        return jsonify({"error": f"停止失敗: {str(e)}"}), 500

@flask_app.route('/api/update_record', methods=['POST'])
def update_record():
    """[v11.6] Update a specific record by filename"""
    if not orchestrator:
        return jsonify({"error": "系統未初始化"}), 500

    try:
        data = request.json
        filename = data.get('filename')
        updates = data.get('updates', {})

        if not filename or not updates:
            return jsonify({"error": "Missing filename or updates"}), 400

        success, msg = orchestrator.update_record_by_filename(filename, updates)

        if success:
            return jsonify({"status": "success", "message": msg})
        else:
            return jsonify({"error": msg}), 404

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@flask_app.route('/api/rerun', methods=['POST'])
def rerun_file():
    """
    [v16.12] Force Rerun Endpoint
    Accepts: {"filename": "xxx.jpg"}
    """
    if not orchestrator:
        return jsonify({"error": "Orchestrator 未初始化"}), 500

    try:
        data = request.json
        filename = data.get('filename')
        if not filename:
            return jsonify({"error": "Missing filename"}), 400

        success = orchestrator.force_rerun(filename)

        # [v16.51 Fix] Robust Auto-restart using standard start_batch logic
        if success:
             # Check if thread is alive. If not, start it using standard method.
             if not orchestrator.is_running:
                 print(f"[Run] Auto-starting batch for priority item: {filename}")
                 # Use start_batch to ensure all session variables and stop_events are handled correctly
                 orchestrator.start_batch(limit=None, restart=False)

             return jsonify({"status": "queued", "message": f"{filename} 已加入優先重跑佇列 (立即執行)"})
        else:
           return jsonify({"status": "exists", "message": "此檔案已在佇列中"}), 200
    except Exception as e:
        log.error(f"Rerun API Error: {e}")
        return jsonify({"error": str(e)}), 500

@flask_app.route('/api/failed_records')
def get_failed_records():
    """Returns ALL unique failed records from history + current session."""
    if not orchestrator:
        return jsonify([])

    try:
        # [v12.2 Fix] Return aggregated unique failures
        return jsonify(orchestrator.get_all_failed_records())
    except Exception as e:
        log.error(f"Failed to fetch records: {e}")
        return jsonify([])

@flask_app.route('/api/correct', methods=['POST'])
def correct_result():
    """新的修正API端點，用於整合dashboard (僅存檔，不學習)"""
    if not orchestrator:
        return jsonify({"error": "系統未初始化"}), 503

    try:
        data = request.json
        filename = data.get('filename')

        if not filename:
            return jsonify({"error": "缺少檔名"}), 400

        # 創建修正資料 [v11.5 New Schema]
        correction_data = {
            "view_type": data.get('view_type', '單機'), # Default View
            "screen_status": data.get('screen_status', ''),
            "quality_issue": data.get('quality_issue', ''),
            "note": data.get('note', ''),
            "model": data.get('model', ''),
            "price": data.get('price', '')
        }

        # [v14.7] 僅記錄到紀錄中，不進行動態學習
        orchestrator.update_record_by_filename(filename, correction_data)

        # 記錄到日誌
        orchestrator.log_system(f"✅ 人工修正已儲存: {filename}")

        return jsonify({
            "status": "success",
            "message": "修正已提交並儲存",
            "filename": filename,
            "correction": correction_data
        })

    except Exception as e:
        return jsonify({"error": f"修正失敗: {str(e)}"}), 500

# [v19.55] Global Cache Busting for Frontend
@flask_app.after_request
def add_header(response):
    """
    Force browser to NOT cache index.html so updates are seen immediately.
    """
    if request.path == '/' or request.path.endswith('index.html') or request.path.startswith('/assets/'):
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response

# [v18.99] Serve React App
@flask_app.route('/', defaults={'path': ''})
@flask_app.route('/<path:path>')
def serve(path):
    if path != "" and os.path.exists(os.path.join(flask_app.static_folder, path)):
        return send_from_directory(flask_app.static_folder, path)
    else:
        return send_from_directory(flask_app.static_folder, 'index.html')

# --- Main ---
def main():
    # === 鐵律：首先執行版本檢查 ===
    print_version_info()
    verify_no_cache()

    # Fix for Windows Console Encoding (CP950 vs UTF-8)
    import sys
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass # Python < 3.7

    # [v9.94] Suppress Flask request logging to reduce terminal noise
    # Note: This only affects backend terminal output, NOT frontend stream_buffer display
    werkzeug_log = logging.getLogger('werkzeug')
    werkzeug_log.setLevel(logging.ERROR)
    flask_app.logger.setLevel(logging.WARNING)

    global orchestrator, api_client, model_name_global

    parser = argparse.ArgumentParser()
    # Change default to the actual target directory to avoid CLI encoding issues
    parser.add_argument("--dir", default="商化照片-202601", help="Image directory")
    parser.add_argument("--api_base", default=os.environ.get("LOCAL_LLM_API_BASE", "http://127.0.0.1:1234/v1"), help="LM Studio/OpenAI Base URL")
    parser.add_argument("--api_key", default="lm-studio", help="API Key")
    parser.add_argument("--model", default=os.environ.get("LOCAL_LLM_MODEL", "qwen/qwen3-vl-8b"), help="Model Name")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of files")
    parser.add_argument("--timeout", type=int, default=180, help="API request timeout in seconds")
    parser.add_argument("--host", default=os.environ.get("SAMSUNG_OCR_HOST", "0.0.0.0"), help="Dashboard bind host")
    parser.add_argument("--port", type=int, default=int(os.environ.get("SAMSUNG_OCR_PORT", "5000")), help="Dashboard/API port")
    parser.add_argument("--bottom_label_strip", action="store_true", help="Add an automatic lower full-width price-label strip crop for difficult retail shelves")
    parser.add_argument("--bottom_center_zoom", action="store_true", help="Add an automatic enlarged lower-center price-label crop for reruns")
    parser.add_argument("--no_followme_auto_update", action="store_true", help="Skip the startup refresh for the daily FollowMe reference")
    args = parser.parse_args()

    # [v19.1] Load Last Config (Override defaults if not specified via CLI)
    # Priority: CLI Args > Last Config > Hardcoded Default
    last_config = load_last_config()

    # If user didn't specify --dir (it equals default), try to load from config
    # Note: argparse default is processed before this, so we check if it matches the hardcoded default
    if args.dir == "商化照片-202601" and "last_image_dir" in last_config:
        args.dir = last_config["last_image_dir"]

    # [v19.10] Restore last API base/key if user didn't override via CLI
    # argparse default = 本機 LM Studio, so we check if it matches the default
    if args.api_base == "http://127.0.0.1:1234/v1" and "last_api_base" in last_config:
        args.api_base = last_config["last_api_base"]
    if args.api_key == "lm-studio" and "last_api_key" in last_config:
        args.api_key = last_config["last_api_key"]

    # [v19.2 Fix] Start using absolute path to prevent CWD/Relative path issues in Orchestrator
    if not os.path.isabs(args.dir):
        args.dir = os.path.abspath(args.dir)

    console.print(f"[Init] 📂 Loaded last used directory: [cyan]{args.dir}[/cyan]")
    console.print(f"[Init] 🤖 LLM endpoint: [cyan]{args.api_base}[/cyan] model={args.model}")

    if args.no_followme_auto_update:
        console.print("[Init] FollowMe 每日表自動更新已略過。")
    else:
        refresh_status = ensure_followme_reference_fresh(max_age_hours=24)
        if refresh_status == "fresh":
            console.print("[Init] FollowMe 每日表仍在 24 小時內，直接使用本機快取。")
        elif refresh_status == "updated":
            console.print("[Init] FollowMe 每日表已自動更新。")
        else:
            console.print(f"[Init] ⚠️ FollowMe 每日表更新未完成：{refresh_status}，改用現有本機資料。")

    if args.model in {"qwen/qwen3-vl-8b", "qwen3vl8b-ocr"} and "last_model" in last_config:
         # Optional: override model if desired, but auto-detect usually handles this
         pass

    # [v18.99] Auto-Detect Model from LM Studio
    # 這裡我們嘗試動態獲取當前掛載的模型
    try:
        import requests
        api_base_url = args.api_base
        if not api_base_url.endswith('/v1'):
            chk_url = f"{api_base_url.rstrip('/')}/v1/models"
        else:
            chk_url = f"{api_base_url.rstrip('/')}/models"

        print(f"[Init] Detecting active model from: {chk_url} ...")
        resp = requests.get(chk_url, timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            models = data.get('data', [])
            if models:
                detected_ids = [item.get('id') for item in models if item.get('id')]
                preferred = [
                    args.model,
                    os.environ.get("LOCAL_LLM_MODEL", ""),
                    "qwen/qwen3-vl-8b",
                    "qwen3vl8b-ocr",
                    os.environ.get("LOCAL_LLM_FALLBACK_MODEL", ""),
                    "qwen/qwen3-vl-4b",
                    "qwen3vl4b-ocr",
                ]
                detected_id = next((item for item in preferred if item and item in detected_ids), detected_ids[0])
                if detected_id == args.model:
                    print(f"[Init] 🟢 Requested model is active: {detected_id}")
                else:
                    print(f"[Init] 🟢 Auto-Detected Active Model: {detected_id}")
                    args.model = detected_id # Override command line arg only when requested model is not loaded
            else:
                print(f"[Init] ⚠️ No models found in response. Using default: {args.model}")
        else:
            print(f"[Init] ⚠️ API Check Failed ({resp.status_code}). Using default: {args.model}")
    except Exception as e:
        print(f"[Init] ⚠️ Auto-Detect Failed: {e}. Using default: {args.model}")

    model_name_global = args.model
    # v14.2: Don't force 127.0.0.1 if localhost works better for user
    api_client = OpenAI(base_url=args.api_base, api_key=args.api_key, timeout=float(args.timeout), max_retries=1)

    # Config for Orchestrator
    config = {
        "image_dir": args.dir,
        "output_dir": ".", # Root for csvs
        "audit_dir": str(AUDIT_DIR),
        "evidence_trace_path": str(AUDIT_DIR / "v1945_evidence_trace.jsonl"),
        "source_root": str(SOURCE_ROOT),
        "model_id": args.model,
        "accuracy_profile": ACCURACY_PROFILE,
        "presentation_role": "production",
        "output_file": "final_results_v4.csv", # Legacy
        "assets_dir": "assets",
        "model_list_file": "型號表.txt",
        "max_dimensions": (2560, 1440),
        "bottom_label_strip": args.bottom_label_strip,
        "bottom_center_zoom": args.bottom_center_zoom,
        "clean_config": str(args)
    }

    orchestrator = BatchOrchestrator(config)
    orchestrator.set_processor_function(process_single_image)
    orchestrator.set_result_review_function(immediate_retry_decision)
    orchestrator.log_system(f"[{SESSION_ID}] 系統初始化完成... 後端已連線。", with_timestamp=True) # Immediate feedback

    # [v18.70] 設定價格查詢日誌回調，讓儀錶板也能看到聯網狀態
    def price_log_to_dashboard(msg: str):
        orchestrator.log_system(msg, with_timestamp=False)
    set_price_log_callback(price_log_to_dashboard)

    is_ocg = 'opencode.ai' in str(args.api_base or '')
    prompt_file = 'samsung_ocr_prompt_opencode_go.txt' if is_ocg else 'samsung_ocr_prompt.txt'
    if os.path.exists(prompt_file):
        prompt_mtime = os.path.getmtime(prompt_file)
        prompt_time_str = datetime.fromtimestamp(prompt_mtime).strftime('%Y-%m-%d %H:%M:%S')
        orchestrator.log_system(f"📜 Prompt 版本: {prompt_time_str}", with_timestamp=False)
        console.print(f"[bold green]📜 Prompt 版本 ({prompt_file}): {prompt_time_str}[/bold green]")
    else:
        orchestrator.log_system(f"❌ 找不到 Prompt 檔案: {prompt_file}", with_timestamp=False)
        console.print(f"[bold red]❌ 找不到 Prompt 檔案: {prompt_file}[/bold red]")

    # [v18.67] 啟動時初始化價格管理器；歷史年度資料夾不預抓官網價格
    try:
        if should_compare_official_price(args.dir):
            pm = get_price_manager()
            pm.clear_and_init()
            # [v19.12] 啟動背景預抓型號表內所有型號的官網價格，讓後續辨識時價格比對更即時
            pm.prefetch_all_models()
        else:
            console.print("[dim]⏭️ 啟動資料夾屬歷史年度，略過官網價格預抓[/dim]")
    except Exception as e:
        console.print(f"[yellow]⚠️ 初始化價格管理器失敗: {e}[/yellow]")

    # [v18.75] Display Prompt Version on Startup
    try:
        prompt_bundle = prompt_mgr.get_prompt_bundle()
        prompt_version = prompt_bundle.get("version_id", "unknown")
        prompt_created = prompt_bundle.get("created_at", "N/A")
        console.print(f"[cyan]📝 Prompt Version: {prompt_version}[/cyan]")
        console.print(f"[cyan]   Created: {prompt_created}[/cyan]")
    except Exception as e:
        console.print(f"[yellow]⚠️ 無法取得 Prompt 版本資訊[/yellow]")

    # Replace standard print with console.print/log to avoid CP950 errors on Windows
    title = f"Samsung OCR Batch System {VERSION} [SID: {SESSION_ID}]"
    console.print(f"[bold yellow]>>> SESSION: {SESSION_ID} <<<[/bold yellow]")
    console.print(f"[bold green]{title}[/bold green]")
    console.print(f"Image Dir: {args.dir}")
    console.print(f"Model: {args.model}")
    console.print(f"API Base: {args.api_base}")
    console.print("--------------------------------------------------")

    # [v18.54 Fix] Do NOT auto-start batch processing
    # Wait for user to click "Start" button in dashboard
    console.print("[yellow]⏳ 等待儀表板操作... 請在瀏覽器中選擇資料夾並點擊「繼續執行」[/yellow]")

    # [v17.27] Explicitly enable threading to prevent UI blocking
    # [v19.5] Auto-Open Browser (Moved from batch script to Python for reliability)
    import webbrowser
    from threading import Timer
    def open_browser():
        try:
            webbrowser.open(f"http://localhost:{args.port}")
            console.print("[green]🚀 Browser launch command sent.[/green]")
        except Exception as e:
            console.print(f"[red]⚠️ Browser launch failed: {e}[/red]")

    if os.environ.get("SAMSUNG_OCR_NO_BROWSER") != "1":
        Timer(1.5, open_browser).start()

    flask_app.run(host=args.host, port=args.port, debug=False, use_reloader=False, threaded=True)

if __name__ == "__main__":
    # === 鐵律：最終版本確認 ===
    console.print("\n" + "="*60)
    console.print(f"    [bold green]最終確認：正在執行 {VERSION}[/bold green]")
    console.print(f"    Session ID: {SESSION_ID}")
    console.print(f"[bold green]強制重載模式：已確保所有模組為最新版本[/bold green]")
    console.print("="*60 + "\n")

    main()

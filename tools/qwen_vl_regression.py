import argparse
import base64
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from requests import HTTPError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass

from skills.image_processing import ImageProcessor
from skills.followme_reference import build_followme_prompt_section
from skills.model_catalog_rules import (
    FOLLOWME_UNRESOLVED,
    resolve_followme_model,
)


DEFAULT_CASES = "tools/qwen_vl_regression_cases.json"


def extract_json(text):
    lines = text.strip().splitlines()
    for line in reversed(lines):
        candidate = line.strip()
        if candidate.startswith("{") and candidate.endswith("}"):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

    for candidate in reversed(re.findall(r"(\{[\s\S]*?\})", text)):
        if '"view_type"' in candidate or '"model"' in candidate:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
    return None


def normalize_value(value):
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned if cleaned else None
    return value


def judge(parsed, expected):
    if not parsed:
        return False, ["no_json"]

    failures = []
    for key, expected_value in expected.items():
        actual_value = normalize_value(parsed.get(key))
        expected_value = normalize_value(expected_value)
        if actual_value != expected_value:
            failures.append(f"{key}: expected={expected_value!r}, actual={actual_value!r}")
    return not failures, failures


def normalize_followme_model(raw_model, price=None, context_text=""):
    del price
    text = " ".join(str(part or "") for part in [raw_model, context_text]).upper()
    if has_negative_followme_context(text):
        return None
    if "FOLLOWME" not in text and "FOLLOW ME" not in text:
        return None
    if any(token in text for token in ["LG", "STANBYME", "MYVIEW", "27ART10", "27LX5", "43SQ700", "32SR83"]):
        return None

    family = resolve_followme_model(raw_model, context_text, unresolved=True)
    return family or FOLLOWME_UNRESOLVED


def infer_followme_from_physical_clues(price=None, context_text=""):
    text = str(context_text or "").upper()
    if any(token in text for token in ["LG", "STANBYME", "MYVIEW", "27ART10", "27LX5", "43SQ700", "32SR83"]):
        return None
    if has_negative_followme_context(text):
        return None
    if re.search(r"(沒有|無|不是|非).{0,24}(白色支架|垂直支架|圓形底座|白色底座|支架|底座)", str(context_text or "")):
        return None

    lower_text = str(context_text or "")
    has_followme_word = "FOLLOWME" in text or "FOLLOW ME" in text
    has_stand = any(token in lower_text for token in ["白色支架", "垂直支架", "白色垂直", "圓形底座", "白色底座"])
    has_tray = any(token in lower_text for token in ["托盤", "展示立牌", "底部價牌", "價牌", "價格"])
    if not has_followme_word and not (has_stand and has_tray):
        return None

    del price
    if has_followme_word:
        return normalize_followme_model(None, None, context_text)
    return None


def has_negative_followme_context(text):
    normalized = str(text or "").upper().replace(" ", "")
    if re.search(r"(非|不是|不屬於|不符合|均非)FOLLOWME", normalized):
        return True
    if re.search(r"(沒有|無|不是|非)FOLLOWME.*(支架|底座|特徵|結構)", normalized):
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
        "沒有FOLLOWME底座",
        "無FOLLOWME底座",
    ])


def is_followme_standard_name(model):
    return str(model or "").upper().startswith("FOLLOWME")


def has_strong_single_unit_evidence(text):
    raw_text = str(text or "")
    return any(term in raw_text for term in [
        "同一台主角",
        "只有一台",
        "單一主角",
        "不是遠景",
        "不屬於遠景",
        "不符合遠景",
        "一般單機",
        "單機條件",
    ])


def should_block_rescue_from_distant_view(view_type, context_text=""):
    raw_text = str(context_text or "")
    if view_type != "遠景":
        return False
    if "整體符合「遠景」條件" not in raw_text and "遠景" not in raw_text:
        return False
    return not has_strong_single_unit_evidence(raw_text)


def normalize_followme_price(model, price=None, context_text=""):
    """A photographed price is evidence and must never be rewritten."""
    return None


def clean_monitor_price(price, min_price=2000):
    if price in (None, "", "null", "None"):
        return None
    digits = "".join(c for c in str(price) if c.isdigit())
    if len(digits) not in [4, 5]:
        return None
    try:
        price_int = int(digits)
    except ValueError:
        return None
    if price_int < min_price:
        return None
    return digits


def should_clear_non_samsung_price(model, context_text=""):
    if model:
        return False
    text = str(context_text or "").upper()
    if re.search(r"(不是|非)\s*(LG|ASUS|ROG|BENQ|ACER)", text, re.IGNORECASE):
        return False
    if any(token in text for token in ["非三星產品", "非SAMSUNG產品", "不是三星產品", "不是SAMSUNG產品"]):
        return True
    return bool(re.search(r"(主角|主體|這台|此台|商品).{0,12}(LG|ASUS|ROG|BENQ|ACER)", text, re.IGNORECASE))


def should_block_borrowed_model_rescue(context_text=""):
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


def should_clear_borrowed_odyssey_ark_model(model, context_text=""):
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
    if "ODYSSEY ARK" in text and ("55" in text or "MINI LED" in text or "ARK" in text):
        return "S55BG970NC"
    return None


def correct_common_model_price_conflict(model, price, context_text=""):
    """Match the production backend: price never manufactures a model."""
    return model


def normalize_like_backend(parsed, raw_text):
    if not parsed:
        return parsed
    normalized = dict(parsed)
    model_rescue_blocked = has_odyssey_ark_context(raw_text) or should_block_borrowed_model_rescue(raw_text)
    rescue_blocked_by_distant_view = should_block_rescue_from_distant_view(normalized.get("view_type"), raw_text)
    if is_followme_standard_name(normalized.get("model")) and has_negative_followme_context(raw_text):
        normalized["model"] = None
    current_model = str(normalized.get("model") or "")
    has_valid_s_model = bool(re.match(r"^S[A-Z0-9]{7,14}$", current_model.upper()))
    if not has_valid_s_model and not model_rescue_blocked:
        mapped = normalize_followme_model(normalized.get("model"), normalized.get("price"), raw_text)
        if mapped:
            normalized["model"] = mapped
    corrected_followme_price = normalize_followme_price(normalized.get("model"), normalized.get("price"), raw_text)
    if corrected_followme_price:
        normalized["price"] = corrected_followme_price

    if should_clear_borrowed_odyssey_ark_model(normalized.get("model"), raw_text):
        normalized["model"] = None
        normalized["price"] = None
        model_rescue_blocked = True

    ark_model = infer_odyssey_ark_model(raw_text)
    if ark_model and not normalized.get("model"):
        normalized["model"] = ark_model

    if not normalized.get("model") and not rescue_blocked_by_distant_view:
        main_label_model = extract_main_label_model(raw_text)
        if main_label_model:
            normalized["model"] = main_label_model

    if not normalized.get("price") and not rescue_blocked_by_distant_view:
        price_match = re.search(
            r"(?:價格|售價|促銷價|建議售價|價牌顯示|標籤寫|寫著|NT\$|\$)\s*(?:是|為|寫|:|：)?\s*[「」\"]?(\d{1,2},\d{3}|\d{4,5})",
            raw_text,
        )
        if price_match:
            normalized["price"] = clean_monitor_price(price_match.group(1).replace(",", ""))

    cleaned_price = clean_monitor_price(normalized.get("price"))
    if normalized.get("price") and not cleaned_price:
        normalized["price"] = None
    elif cleaned_price:
        normalized["price"] = cleaned_price

    if rescue_blocked_by_distant_view:
        normalized["model"] = None
        normalized["price"] = None

    corrected_model = correct_common_model_price_conflict(normalized.get("model"), normalized.get("price"), raw_text)
    if corrected_model != normalized.get("model"):
        normalized["model"] = corrected_model

    if should_clear_non_samsung_price(normalized.get("model"), raw_text):
        normalized["price"] = None
        if any(term in raw_text for term in ["展示區", "多台", "貨架", "遠景"]):
            normalized["view_type"] = "遠景"

    inferred_followme = None if model_rescue_blocked else infer_followme_from_physical_clues(normalized.get("price"), raw_text)
    if inferred_followme and normalized.get("view_type") == "遠景":
        normalized["view_type"] = "單機"
        normalized["screen_status"] = normalized.get("screen_status") or "正常"
        normalized["model"] = inferred_followme
    elif inferred_followme and not normalized.get("model"):
        normalized["model"] = inferred_followme

    if normalized.get("price") and not normalized.get("model"):
        normalized["quality_issue"] = "不合格-沒有規格牌"
    elif normalized.get("model") and not normalized.get("price"):
        normalized["quality_issue"] = "不合格-沒有價格牌"
    elif normalized.get("model") and normalized.get("price"):
        normalized["quality_issue"] = "無"

    if normalized.get("view_type") == "遠景":
        if has_strong_single_unit_evidence(raw_text):
            normalized["view_type"] = "單機"
            normalized.setdefault("screen_status", "正常")
    return normalized


def build_user_content(image_path, prompt_text, case, max_side, detect_label_card, bottom_label_strip, bottom_center_zoom):
    processor = ImageProcessor(
        {
            "max_size": max_side,
            "auto_orient": True,
            "detect_label_card": detect_label_card,
            "bottom_label_strip": bottom_label_strip,
            "bottom_center_zoom": bottom_center_zoom,
        }
    )
    processed = processor.process(str(image_path))
    if not processed:
        raise RuntimeError(f"image preprocessing failed: {image_path}")

    text = (
        "這是 prompt 回歸測試。請依照系統提示，只輸出自然描述與下一行 JSON。\n"
        f"圖片檔名: {image_path.name}\n"
        f"測試備註: {case.get('note', '')}"
    )
    content = [{"type": "text", "text": text}]

    content.append(
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{processed['base64']}"},
        }
    )
    if detect_label_card and processed.get("label_base64") and not bottom_label_strip:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{processed['label_base64']}"},
            }
        )
    if detect_label_card and processed.get("bottom_label_base64"):
        content.append(
            {
                "type": "text",
                "text": "補充圖：這是原圖下方商品標籤/價牌區域的自動裁切，請只用它輔助讀型號與價格。",
            }
        )
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{processed['bottom_label_base64']}"},
            }
        )
    if detect_label_card and processed.get("bottom_center_base64"):
        content.append(
            {
                "type": "text",
                "text": "補充圖：這是原圖下方中間商品價牌區域的自動放大裁切，請優先用它讀主角型號與價格。",
            }
        )
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{processed['bottom_center_base64']}"},
            }
        )

    return content


def run_case(api_base, model, prompt_text, root, case, timeout, max_side, detect_label_card, bottom_label_strip, bottom_center_zoom, backend_normalize):
    image_path = root / case["file"]
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt_text},
            {
                "role": "user",
                "content": build_user_content(
                    image_path,
                    prompt_text,
                    case,
                    max_side,
                    detect_label_card,
                    bottom_label_strip,
                    bottom_center_zoom,
                ),
            },
        ],
        "temperature": 0,
        "max_tokens": 1000,
        "stream": False,
    }

    started = time.time()
    response = requests.post(
        f"{api_base.rstrip('/')}/chat/completions",
        json=payload,
        timeout=timeout,
    )
    elapsed = round(time.time() - started, 2)
    try:
        response.raise_for_status()
    except HTTPError as exc:
        detail = response.text.strip()
        if detail:
            raise RuntimeError(f"{exc}; body={detail}") from exc
        raise
    content = response.json()["choices"][0]["message"]["content"]
    parsed = extract_json(content)
    normalized = normalize_like_backend(parsed, content) if backend_normalize else None
    judged = normalized if backend_normalize else parsed
    passed, failures = judge(judged, case.get("expected", {}))
    return {
        "name": case.get("name", case["file"]),
        "file": case["file"],
        "elapsed_sec": elapsed,
        "passed": passed,
        "failures": failures,
        "parsed": parsed,
        "normalized": normalized,
        "raw": content,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", default=os.environ.get("LOCAL_LLM_API_BASE", "http://127.0.0.1:1234/v1"))
    parser.add_argument("--model", default=os.environ.get("LOCAL_LLM_MODEL", "qwen/qwen3-vl-8b"))
    parser.add_argument("--prompt", default="samsung_ocr_prompt.txt")
    parser.add_argument("--cases", default=DEFAULT_CASES)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--max-side", type=int, default=1800)
    parser.add_argument("--no-label-crop", action="store_true")
    parser.add_argument("--bottom-label-strip", action="store_true")
    parser.add_argument("--bottom-center-zoom", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--output", default="")
    parser.add_argument("--normalize-backend", action="store_true")
    args = parser.parse_args()

    root = Path.cwd()
    prompt_text = (root / args.prompt).read_text(encoding="utf-8") + build_followme_prompt_section()
    cases = json.loads((root / args.cases).read_text(encoding="utf-8"))
    if args.limit > 0:
        cases = cases[: args.limit]

    results = []
    for case in cases:
        print(f"===== {case.get('name', case['file'])} =====", flush=True)
        try:
            result = run_case(
                args.api_base,
                args.model,
                prompt_text,
                root,
                case,
                args.timeout,
                args.max_side,
                not args.no_label_crop,
                args.bottom_label_strip,
                args.bottom_center_zoom,
                args.normalize_backend,
            )
        except Exception as exc:
            result = {
                "name": case.get("name", case["file"]),
                "file": case["file"],
                "passed": False,
                "failures": [str(exc)],
                "parsed": None,
                "raw": "",
            }
        results.append(result)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)

    passed_count = sum(1 for item in results if item["passed"])
    print(f"===== SUMMARY {passed_count}/{len(results)} passed =====")
    if args.output:
        output_path = root / args.output
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Report written: {output_path}")


if __name__ == "__main__":
    main()

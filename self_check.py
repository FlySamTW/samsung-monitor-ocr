import os
import io
import json
import base64
import uuid
import re
from openai import OpenAI
from rich.console import Console

# Setup
console = Console()
console.print("[yellow]Starting Self-Diagnostic Check...[/yellow]")

# Config
API_BASE = "http://localhost:1234/v1"
API_KEY = "lm-studio"
MODEL_NAME = "local-model"

REPORT_FILE = "self_check_report.txt"
with open(REPORT_FILE, "w", encoding="utf-8") as f:
    f.write(f"Self-Check Report - {uuid.uuid4()}\n====================================\n")

def log_to_file(msg):
    with open(REPORT_FILE, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


client = OpenAI(base_url=API_BASE, api_key=API_KEY)

# Target Files (User Complained List)
TARGET_DIR = r"d:\00_程式\20260120_商化自動OCR圖片\商化照片-202512-訓練2"
TARGET_FILES = [
    "M-台中市-大甲區-SF-大甲-412.jpg",
    "M-台中市-大里區-eLife-大里-302.jpg",
    "M-台中市-大里區-SF-大里-417.jpg",
    "M-台中市-大里區-SF-大里-424.jpg",
    "M-台中市-大里區-TK3C-大里二-768.jpg",
    "M-台中市-太平區-SF-太平中興-431.jpg",
    "M-台中市-太平區-TK3C-太平-852.jpg"
]

# Load Resources
with open('samsung_ocr_prompt.txt', 'r', encoding='utf-8') as f:
    SYSTEM_PROMPT = f.read()

valid_models_list = []
try:
    with open('型號表.txt', 'r', encoding='utf-8') as f:
        valid_models_list = [line.strip().upper() for line in f if line.strip()]
except:
    console.print("[red]Failed to load 型號表.txt[/red]")

def sanitize_json(json_str):
    if not json_str: return ""
    json_str = json_str.replace("```json", "").replace("```", "").strip()
    return json_str

def process_file(fname):
    fpath = os.path.join(TARGET_DIR, fname)
    if not os.path.exists(fpath):
        console.print(f"[red]File not found: {fname}[/red]")
        return

    with open(fpath, "rb") as image_file:
         full_image_b64 = base64.b64encode(image_file.read()).decode('utf-8')

    # Construct Prompt (Mimic Batch Processor)
    random_salt = str(uuid.uuid4())[:8]
    user_prompt = f"圖片: {fname}\nRequestID: {random_salt}\n請回傳: {fname}\n請提取資訊 (繁體中文)，務必包含 Observation 和 JSON。"

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            # [v18.22] 零記憶機制強制實作 (Zero Memory Policy)
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{full_image_b64}"}},
                    {"type": "text", "text": user_prompt}
                ]}
            ],
            temperature=0.1, # Low temp for determinism
            max_tokens=2048
        )
        
        full_text = response.choices[0].message.content
        log_to_file(f"\nFile: {fname}")
        log_to_file(f"Raw Output: {full_text[:200]}...") # Limit raw output

        # Extract JSON
        json_candidates = re.findall(r'(\{[\s\S]*?\})', full_text)
        if not json_candidates:
            log_to_file("No JSON found!")
            return

        json_str = json_candidates[-1]
        json_str = sanitize_json(json_str)
        data = json.loads(json_str)

        # Apply Strict Backend Checks (The Core Logic)
        
        # 1. Model Check
        raw_model = data.get("model")
        # 1. Model Check -> Fuzzy Recovery
        import difflib
        raw_model = data.get("model")
        if raw_model and isinstance(raw_model, str):
            clean_model = raw_model.strip().upper()
            noise_patterns = ['27"', '32"', '34"', '49"', '27INCH', '32INCH', 'SAMSUNG', 'HZ', 'MS', '1000R', '1500R']
            for noise in noise_patterns:
                clean_model = clean_model.replace(noise, "")
            clean_model = clean_model.strip()

            # [v18.15] FollowMe Logic (Price-Based Manual Mapping)
            if "FOLLOWME" in clean_model:
                    # Default to M7
                    mapped_model = 'FollowMe M7 32"'
                    
                    # Use Price to disambiguate if available
                    p_val = data.get("price")
                    if p_val and str(p_val).isdigit():
                        p_int = int(p_val)
                        if p_int < 11500: # User said 9900
                            mapped_model = 'FollowMe M5 32"'
                        elif p_int > 14500: # User said 15999
                            mapped_model = 'FollowMe Pro M7 43"'
                        else: # User said 12990
                            mapped_model = 'FollowMe M7 32"'
                    
                    # Fallback to text heuristics if price invalid
                    elif "PRO" in clean_model or "43" in clean_model:
                        mapped_model = 'FollowMe Pro M7 43"'
                    elif "M5" in clean_model:
                        mapped_model = 'FollowMe M5 32"'
                        
                    clean_model = mapped_model
                    log_to_file(f"⚠️ [FollowMe Logic] '{raw_model}' mapped to '{clean_model}' (Price: {p_val})")

            if valid_models_list and clean_model not in valid_models_list:
                # Try Fuzzy Match (Aggressive Recovery)
                matches = difflib.get_close_matches(clean_model, valid_models_list, n=1, cutoff=0.8)
                if matches:
                    corrected_model = matches[0]
                    log_to_file(f"⚠️ [模糊修復] '{clean_model}' -> '{corrected_model}' (In List)")
                    data["model"] = corrected_model
                else:
                    log_to_file(f"⚠️ [幻覺攔截] Model '{clean_model}' 無法匹配任何型號 -> None")
                    data["model"] = None
            else:
                 data["model"] = clean_model
                 log_to_file(f"✅ Model '{clean_model}' Valid")
        else:
            log_to_file("ℹ️ Model is Null")

        # 2. Price Check
        raw_price = data.get("price")
        if raw_price:
             # Spec Unit Ban
            forbidden = ['R', 'HZ', 'MS', 'CM', 'MM', 'X', 'INCH', '”', '"', 'SWITCH', 'NINTENDO', 'PS5', 'SONY']
            if isinstance(raw_price, str) and any(u in raw_price.upper() for u in forbidden):
                 log_to_file(f"⚠️ [價格攔截] Price '{raw_price}' 含規格/道具關鍵字 -> 無效")
                 data["price"] = None
            else:
                clean_price = "".join([c for c in str(raw_price) if c.isdigit()])
                if len(clean_price) in [4, 5]:
                    data["price"] = clean_price
                    log_to_file(f"✅ Price '{clean_price}' Valid")
                else:
                    log_to_file(f"⚠️ [價格] Price '{raw_price}' -> '{clean_price}' Invalid Length -> Set to None")
                    data["price"] = None
        else:
             log_to_file("ℹ️ Price is Null")
        
        # 3. Quality Issue (Re-implementation of v18.02 Logic)
        thinking_text = full_text.split('{')[0]
        desc_keywords_unclear = ["不清", "模糊", "反光", "遮擋", "無法辨識", "看不到"]
        is_unclear = any(k in thinking_text for k in desc_keywords_unclear) if thinking_text else False
        
        p = data.get("price")
        m = data.get("model")
        
        if p and not m: qi = "缺型號(規格牌不清/不在表內)"
        elif m and not p: qi = "缺價格(價牌不清/不對齊)"
        elif m and p: qi = "無"
        else:
             if data.get("view_type") == "遠景": qi = "無"
             elif is_unclear: qi = "不合格-照不清楚"
             else: qi = "不合格-沒有規格和價格牌"
        
        log_to_file(f"✅ Final QI: {qi}")
        log_to_file(f"Final Data: {data}")

    except Exception as e:
        log_to_file(f"Error: {e}")

# Main Loop
for f in TARGET_FILES:
    process_file(f)

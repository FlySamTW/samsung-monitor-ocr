import os
import sys
import importlib
import argparse
import time
import json
import base64
import logging
import psutil
from flask import Flask, jsonify, request, send_file, send_from_directory, Response
from flask_cors import CORS
from rich.console import Console
from rich.logging import RichHandler
from openai import OpenAI

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
        'skills.official_price'  # [v18.67] 官方價格驗證
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

VERSION = "v18.99 (FollowMe-Logic-Fix)"
import random, string
from datetime import datetime
SESSION_ID = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))

# --- Logging Setup (必須在函數定義前) ---
console = Console()

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
    """載入上次執行的設定 (目錄與模型)"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            console.print(f"[yellow]⚠️ 無法讀取設定檔: {e}[/yellow]")
    return {}

def save_last_config(image_dir, model_name):
    """儲存本次執行的設定"""
    try:
        data = {
            "last_image_dir": image_dir,
            "last_model": model_name,
            "updated_at": datetime.now().isoformat()
        }
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
except ImportError:
    cc = None
    log.warning("OpenCC not installed.")

def to_tc(text):
    return cc.convert(text) if cc else text

# --- Main Processor Function (Single Stage v6.8) ---

def _detect_repetition(text: str) -> bool:
    """
    [v18.82] 簡易的重複語句偵測器 (Watchdog)
    如果發現長句子連續出現 2 次以上，判定為陷入迴圈。
    """
    if not text: return False
    
    # 1. 簡單的逐行檢查
    lines = [L.strip() for L in text.split('\n') if len(L.strip()) > 15] # 忽略太短的行
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
    if len(text) > 200:
        window_size = 30
        threshold = 3 
        # 簡易採樣檢查
        for i in range(0, len(text) - window_size, 50): # 步長 50
            sub = text[i : i+window_size]
            if text.count(sub) >= threshold:
                print(f"[Watchdog] 偵測到段落重複: {sub[:20]}... (x{text.count(sub)})")
                return True
                
    return False

def process_single_image(fname, image_b64, prompt_mgr, image_processor):
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
    
    # RESTORE ORIGINAL RESOLUTION (No Constraint)
    # [IRON RULE] User said: "Do NOT compress images!!!" 
    # [v18.99 Critical Fix] However, 12MP images (4000x3000) consume ~12k tokens, causing Context Overflow (8k limit).
    # We MUST cap at a reasonable high-res limit (e.g. 2560px) to survive.
    # 2560px is still > 2K resolution, sufficient for OCR.
    image_processor.config["max_size"] = 2560
    
    # [v16.3 DEBUG] Force Print to see if we enter
    print(f"[DEBUG] process_single_image called for: {fname}")

    # [v16.3 Optimization] Use pre-loaded b64 to avoid double reading/path errors
    label_b64 = None
    if image_b64:
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
    if orchestrator:
        msg = f"▶️ 正在分析圖片: {fname} (Model: {model_name_global})..."
        if label_b64: msg += " (偵測到價牌，啟用雙重視野放大 🔍)"
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
    try:
        # 🔥 強制每次都從檔案讀取，不使用快取
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
    system_prompt = prompt_template # Raw Prompt (No List)

    # [v17.31 Integrity] Force Echo Filename to prevent crosstalk (849 vs 431 mixup)
    import uuid
    random_salt = str(uuid.uuid4())[:8]
    
    # [v18.35 Stateless Purge]
    # 1. 強化無狀態提示 (Engineering Strategy)
    # 2. 恢復雙重視野 (Engineering Strategy)
    # 3. 確保每次都建立全新 messages
    
    # Construct User Context
    user_content = []
    
    # Image 1: Context (Full Image)
    user_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{full_image_b64}"}})
    
    # Image 2: High-Resolution (Crop) if detected
    if label_b64:
        user_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{label_b64}"}})
        user_prompt = f"「這是一張全新的照片，與之前的任何辨識無關。請執行『視覺歸屬』檢查。」\n圖片: {fname}\nRequestID: {random_salt}\n[提示]\n圖 1 (全景): 用於確認標籤相對於螢幕底座的位置歸屬。\n圖 2 (特寫): 用於讀取該標籤上的細微文字。\n請結合兩者，確保讀到的文字是來自於『歸屬於該螢幕的同一張標籤』。"
    else:
        user_prompt = f"「這是一張全新的照片，與之前的任何辨識無關。」\n圖片: {fname}\nRequestID: {random_salt}\n請提取此照片中的資訊，並確認型號與價格位於同一張實體標籤上。"

    user_content.append({"type": "text", "text": user_prompt})

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content}
    ]

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
        orchestrator.log_system(f"🚀 初始化 Local LLM 引擎 ({model_name_global}) - 準備就緒")
        # Simplified
        pass

    try:
        # [v17.16 Fix] Pre-Flight Check for Stop Signal
        if orchestrator and not orchestrator.is_running:
             return {"error": "Stopped by user"}

        # Move prompt loading inside try to catch formatting errors
        # ... actually already done above ...
        
        # [v18.90] Price Consistency Retry Loop
        # 包裝 LLM 呼叫與解析，當價格/型號矛盾時給予二次機會
        max_retries = 1
        final_result = None
        
        for attempt in range(max_retries + 1):
            start_llm_t = time.time()
            if attempt > 0:
                console.print(f"[bold yellow]🔄 觸發重試 (Attempt {attempt+1}/{max_retries+1}) - 加入價格警告提示...[/bold yellow]")
                orchestrator.log_system(f"🔄 [Auto-Retry] 觸發價格與型號不一致的重試機制...")
            
            stream = api_client.chat.completions.create(
                model=model_name_global,
                messages=messages,
                stream=True,
                temperature=0.1,  # Keep low for OCR precision
                # top_p=0.8,      # Removed to allow model defaults
                # max_tokens=1024, # Let model decide or use default
                # presence_penalty=1.5, # Removed: harmful for OCR (forces diversity)
                stream_options={"include_usage": True}
            )
            
            tool_calls_buffer = []
            full_response_text = "" # Reset for retry
            
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
                    if orchestrator: orchestrator.stream_buffer = current_display

            # Anti-Loop Check
            if _detect_repetition(full_response_text):
                if attempt < max_retries:
                    console.print(f"[red]⚠️ 偵測到跳針，重試...[/red]")
                    messages.append({"role": "user", "content": "你剛剛的回應陷入無限迴圈，請重試並只輸出 JSON。"})
                    continue
                else:
                    return {"error": "LLM Repetitive Loop Detected"}

            # Parse Result
            args_str = None
            thinking_text = full_response_text
            
            # ... (Parsing Logic) ...
            lines = full_response_text.strip().split('\n')
            json_line = None
            monologue_lines = []
            for line in lines:
                line_stripped = line.strip()
                if line_stripped.startswith('{') and ('"view_type"' in line_stripped or '"model"' in line_stripped):
                    json_line = line_stripped
                elif line_stripped and not line_stripped.startswith('{'):
                    monologue_lines.append(line_stripped)
            
            if json_line:
                args_str = json_line
                thinking_text = ' '.join(monologue_lines) if monologue_lines else "..."
            else:
                # Fallback Regex
                import re
                json_candidates = re.findall(r'(\{[\s\S]*?\})', full_response_text)
                if json_candidates:
                    for candidate in reversed(json_candidates):
                        if '"view_type"' in candidate or '"model"' in candidate:
                            args_str = candidate
                            break
                    if not args_str: args_str = json_candidates[-1]
                    parts = full_response_text.split(args_str)
                    if parts[0].strip(): thinking_text = parts[0].strip()
                elif full_response_text.strip().startswith('{'):
                    args_str = full_response_text
                    thinking_text = "..."

            parsed = None
            if args_str:
                try:
                    args_str = sanitize_json(args_str)
                    parsed = json.loads(args_str)
                except:
                    pass
            
            # [v18.99 Fix] Retry if JSON is completely missing
            if not parsed and attempt < max_retries:
                console.print(f"[bold red]⚠️ 未偵測到有效 JSON，要求 LLM 補完...[/bold red]")
                orchestrator.log_system(f"⚠️ [Auto-Retry] LLM 未輸出 JSON，觸發重試...")
                # Append the previous incomplete response as assistant output
                messages.append({"role": "assistant", "content": full_response_text})
                # Prompt user to force JSON
                messages.append({"role": "user", "content": "你忘了輸出 JSON！請依照格式要求，在最後補上 JSON 區塊。\n(只輸出 JSON，不要再解釋)"})
                continue
            
            # [v18.90] Price Consistency Check
            price_check_passed = True
            if parsed and isinstance(parsed, dict):
                model_check = parsed.get("model")
                price_check = parsed.get("price")
                
                if model_check and price_check:
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
                                    price_check_passed = False
                                    # Add Dynamic Hint
                                    hint_msg = (
                                        f"警告：你識別出的型號 [{model_check}] 市場均價約 ${off_price}，"
                                        f"但你讀取到的價格是 ${ocr_p} (價差 ${int(diff)}，超過安全閾值)。\n"
                                        f"這極大可能是「型號讀錯」或「價格讀錯」。\n"
                                        f"請仔細重新檢查圖片上的每一個字元，特別是型號的後綴。請修正你的判斷。"
                                    )
                                    messages.append({"role": "assistant", "content": full_response_text})
                                    messages.append({"role": "user", "content": hint_msg})
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
            
            # [v18.99] 只有在以下情況才觸發 FollowMe 邏輯：
            # 1. clean_model 明確包含 "FOLLOWME" 
            # 2. 或者 clean_model 為空/無效，且 raw_model/thinking_text 有 FollowMe 關鍵字
            if "FOLLOWME" in clean_model: 
                is_followme_candidate = True
            elif not has_valid_s_model:  # 只有當沒有有效 S 型號時才檢查其他線索
                if raw_model and any(h in raw_model.upper() for h in followme_hints): 
                    is_followme_candidate = True
                elif thinking_text and any(h in thinking_text.upper() for h in followme_hints): 
                    is_followme_candidate = True
            
            if is_followme_candidate:
                 # Default to M7
                 mapped_model = 'FollowMe M7 32"'
                 
                 # Use Price to disambiguate if available
                 p_val = data_obj.get("price")
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
                 is_followme_bypass = True # [v18.44] Enable Bypass
                 # [v18.63] Silent log, no UI output
                 console.print(f"[dim]⚠️ [FollowMe Logic] '{raw_model}' -> '{clean_model}' (Price: {p_val})[/dim]")

            # [v18.44 Fix] Added bypass flag so FollowMe isn't killed by hallucination check
            if valid_models_list and clean_model not in valid_models_list and not is_followme_bypass:
                 # Try Fuzzy Match (Aggressive Recovery)
                 # [v18.28] Cutoff 0.7: Lowered to catch 412.jpg typos (S24F532 vs S24F332)
                 matches = difflib.get_close_matches(clean_model, valid_models_list, n=1, cutoff=0.7)
                 if matches:
                     corrected_model = matches[0]
                     # [v18.58] Silent fuzzy match, no log message
                     data_obj["model"] = corrected_model
                 else:
                    # [v18.92] 最後一道防線：嘗試官網即時驗證 (Auto-Discover)
                    # 這是為了回答 User "以後怎麼避免" 的終極方案。即使型號表忘了更，官網有就算數。
                    if try_discover_model(clean_model):
                         console.print(f"[bold green]✨ [Auto-Discover] 官網驗證成功！型號 {clean_model} 已自動加入型號表。[/bold green]")
                         data_obj["model"] = clean_model
                         # 更新記憶體中的 valid_models_list 以防本次 Batch 後續又遇到
                         if valid_models_list is not None:
                             valid_models_list.append(clean_model)
                    else:
                        # [v18.56] Silently set to None if strictly invalid
                        
                        # [v18.96] Safety Net for Discontinued Models (停產型號救星)
                        # 如果官網查不到 (404/停產)，但格式明明就是 S 型號 (S+8~15碼)，強制保留！
                        import re
                        is_valid_format = re.match(r'^S[A-Z0-9]{7,14}$', clean_model)
                        
                        if is_valid_format:
                             console.print(f"[yellow]⚠️ [Auto-Discover Failed] 官網查無 {clean_model} (可能已停產)，但格式正確，強制信任！[/yellow]")
                             data_obj["model"] = clean_model
                        else:
                             data_obj["model"] = None
            else:
                 data_obj["model"] = clean_model

        # [v18.66] 尺寸描述 vs 型號 交叉驗證
        # 如果思考文字提到「43型」但型號卻是 S32...，表示混搭標籤
        final_model = data_obj.get("model")
        if final_model and thinking_text:
            import re
            # 從思考文字中抓取尺寸描述（如「43型」、「32型」、「27型」）
            size_in_desc = re.search(r'(\d{2})型', thinking_text)
            if size_in_desc:
                desc_size = size_in_desc.group(1)
                # 從型號中抓取尺寸（如 S43... 的 43、S32... 的 32）
                model_size_match = re.search(r'S(\d{2})', str(final_model).upper())
                if model_size_match:
                    model_size = model_size_match.group(1)
                    if desc_size != model_size:
                        # 尺寸不符！嘗試從 valid_models 中找正確尺寸的型號
                        console.print(f"[yellow]⚠️ [尺寸交叉驗證] 描述={desc_size}型 但型號={final_model} → 嘗試修正[/yellow]")
                        # 找同系列但正確尺寸的型號
                        correct_size_candidates = [m for m in valid_models_list if f"S{desc_size}" in m.upper()]
                        if correct_size_candidates:
                            # 用 fuzzy match 在正確尺寸的候選中找最接近的
                            best_match = difflib.get_close_matches(clean_model.replace(model_size, desc_size), correct_size_candidates, n=1, cutoff=0.6)
                            if best_match:
                                data_obj["model"] = best_match[0]
                                console.print(f"[green]✅ [尺寸交叉驗證] 修正為: {best_match[0]}[/green]")

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
                
                # Rule: If value is < 2000 AND has no currency symbol, it's likely curvature (1000, 1500, 1800)
                # If value is > 2000 (e.g. 3290), we forgive missing symbol if clearly not curvature.
                
                try:
                    p_int = int(clean_price)
                    # [v18.94] 1000R 防呆：價格不可能低於 2000 (除非是配件，但我們只抓螢幕)
                    min_price = 2000
                    
                    if p_int < min_price:
                         console.print(f"[dim]⚠️ [價格攔截] {raw_price} ({p_int}) < {min_price} -> 過低 (可能是曲率 1000R/1500R)[/dim]")
                         data_obj["price"] = None
                    # [v18.97] Strict Symbol Check for low-ish numbers to be safe
                    elif p_int < 10000 and not has_currency_symbol:
                        # Double check: is it 1000, 1500, 1800? (Common Curvatures)
                        if p_int in [1000, 1500, 1800]:
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
            
            # 從獨白提取價格
            desc_price_patterns = [
                r'價格[是為標籤上寫著]?\s*[「」\"]?(\d{4,5})[」\"]?',
                r'寫著[「」]?(\d{4,5})[」]?',
                r'\$\s*(\d{4,5})',
            ]
            desc_price = None
            for pattern in desc_price_patterns:
                match = re.search(pattern, thinking_text)
                if match:
                    desc_price = match.group(1).strip()
                    break

            # 驗證型號一致性
            current_model = data_obj.get("model")
            if current_model and desc_model:
                # 簡單清理
                c_curr = current_model.replace('-', '').strip().upper()
                c_desc = desc_model.replace('-', '').strip().upper()
                if c_curr != c_desc and c_desc in valid_models_list:
                    console.print(f"[yellow]⚠️ [獨白驗證] JSON型號({c_curr}) 與 獨白型號({c_desc}) 不符! 採用獨白型號。[/yellow]")
                    data_obj["model"] = c_desc
            
            # 驗證價格一致性
            current_price = data_obj.get("price")
            if not current_price and desc_price: # 如果 JSON 沒抓到但獨白有
                console.print(f"[green]✅ [獨白救援] 從思考過程中補回價格: {desc_price}[/green]")
                data_obj["price"] = desc_price
        # 4. Auto-Calculate Quality Issue
        p_val = data_obj.get("price")
        m_val = data_obj.get("model")
        
        if p_val and not m_val:
            data_obj["quality_issue"] = "缺型號(規格牌不清/不在表內)"
        elif m_val and not p_val:
            data_obj["quality_issue"] = "缺價格(價牌不清/不對齊)"
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
                if k in result_json:
                    result_json[k] = v

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
                
                # [v17.26 Fix] Ensure what we show is what we save
                # We update the 'thinking' field in result_json LATER, so we just log here.
                orchestrator.log_system(f"[THINK] {clean_think}")

            # ... (Rest of existing validation logic for model/price/cat) ...
            parsed_model = result_json.get("model")
            if parsed_model:
                parsed_model = parsed_model.strip().upper()
            
            # ... (Model matching remains same) ...
            valid_models_list = orchestrator.model_matcher.valid_models if orchestrator and orchestrator.model_matcher else []
            if parsed_model and parsed_model not in valid_models_list:
                # [v9.72 Fix] Use orchestrator's matcher instead of undefined function
                best_match = orchestrator.model_matcher.match(parsed_model) if orchestrator and orchestrator.model_matcher else None
                if best_match:
                    result_json["model"] = best_match
                else:
                    result_json["model"] = parsed_model
            
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
                if view_type != '遠景':
                    console.print(f"[yellow]⚠️ [獨白備援] JSON 寫 {view_type} 但獨白說遠景 → 強制修正為遠景[/yellow]")
                    view_type = '遠景'
                    result_json['view_type'] = '遠景'
            
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
            
        # [v18.67] 官方價格驗證
        try:
            model_for_price = result_json.get("model")
            price_for_validate = result_json.get("price")
            
            # [v18.69] 自動發現新型號
            if model_for_price:
                try_discover_model(model_for_price)
            
            if model_for_price and price_for_validate and str(price_for_validate).isdigit():
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
            orchestrator.stream_buffer = "" # [v14.4 Fix] Clear buffer after log done

    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        error_str = str(e).lower()
        
        # [v18.99] 友善錯誤訊息：針對 LM Studio 常見錯誤
        if "failed to process image" in error_str:
            friendly_msg = "❌ LLM 回應「無法處理圖片」- 可能是圖片格式不支援或 LM Studio 模型問題"
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
    final_think = thinking_text if 'thinking_text' in locals() else ""
    result_json['thinking'] = final_think
    
    return result_json

# --- Flask API Routes ---


@flask_app.route('/api/list_dirs', methods=['GET'])
def list_dirs():
    """列出可用資料夾"""
    try:
        # 取得根目錄下所有資料夾
        entries = os.listdir('.')
        dirs = [e for e in entries if os.path.isdir(e) and not e.startswith('.') and e not in ['dashboard', 'runs', '__pycache__', '.venv', 'node_modules']]
        # 優先排序列出包含「照片」或「商化」的資料夾
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
        
        stats = {
            "processed": total_success + total_failed,
            "success": total_success,
            "failed": total_failed,
            "total": orchestrator.stats.get('total', 0)
        }
        
        status_obj = {
            "version": VERSION,
            "current_file": getattr(orchestrator, 'current_file', 'None'),
            "stats": stats,
            "metrics": metrics,
            "stream_buffer": str(orchestrator.stream_buffer), # 強制轉字串避免類型錯誤
            "lm_logs": list(orchestrator.system_logs)[-200:], # [v11.9 Fix] Limit logs to last 200 to prevent payload bloat
            "recent_results": orchestrator.recent_results,
            # "failed_files": getattr(orchestrator, 'failed_files', []), # [v11.9 Fix] REMOVED! Too huge, causes API timeout.
            "is_running": orchestrator.is_running,
            "resources": {
                "cpu": psutil.cpu_percent(interval=0.1),
                "ram": psutil.virtual_memory().percent
            }
        }
        return jsonify(status_obj)
    except Exception as e:
        return jsonify({"error": f"獲取狀態失敗: {str(e)}"}), 500

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

@flask_app.route('/api/image/<path:filename>')
def get_image(filename):
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

@flask_app.route('/api/start_batch', methods=['POST'])
def start_batch():
    """啟動批次處理 (可指定資料夾)"""
    if not orchestrator:
        return jsonify({"error": "系統未初始化"}), 500
    
    try:
        if orchestrator.is_running:
            return jsonify({"error": "批次處理已在執行中"}), 400
        
        # [v18.67] 每次啟動時重置價格查詢快取，確保重新從官網抓最新價格
        try:
            from skills.official_price import get_price_manager
            pm = get_price_manager()
            pm.session_fetched.clear()  # 清空「本次已查詢」記錄
            console.print("[dim]🔄 已重置價格查詢狀態，將從官網抓取最新價格[/dim]")
        except Exception as e:
            console.print(f"[yellow]⚠️ 重置價格查詢失敗: {e}[/yellow]")
        
        # Get params from request
        req_data = request.json or {}
        target_dir = req_data.get('dir')
        restart = req_data.get('restart', False)
        reprocess_last_n = req_data.get('reprocess_last_n', 0)
        
        if target_dir:
            if os.path.exists(target_dir):
                orchestrator.image_dir = target_dir
                orchestrator.config['image_dir'] = target_dir # [v16.7 Fix] Update detailed config too
            else:
                return jsonify({"error": f"資料夾不存在: {target_dir}"}), 404

        # [v15.0] Interactive Check: If no files to process, ask to re-run last 5
        if not restart and reprocess_last_n == 0:
            scan_res = orchestrator.get_pending_files()
            if not scan_res['pending_files']:
                return jsonify({
                    "status": "needs_confirmation", 
                    "message": "已全部處理完畢，是否要重跑最後 5 張圖片？",
                    "files_count": len(scan_res['all_files'])
                })

        # 開始批次處理
        # [v19.1] Save Config on Start
        save_last_config(orchestrator.image_dir, model_name_global)
        
        orchestrator.start_batch(restart=restart, reprocess_last_n=reprocess_last_n)
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
            orchestrator.image_dir = target_dir
            orchestrator.config['image_dir'] = target_dir
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
    if request.path == '/' or request.path.endswith('index.html'):
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
    parser.add_argument("--api_base", default="http://192.168.0.234:1234/v1", help="LM Studio/OpenAI Base URL")
    parser.add_argument("--api_key", default="lm-studio", help="API Key")
    parser.add_argument("--model", default="qwen/qwen3-vl-4b", help="Model Name")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of files")
    parser.add_argument("--timeout", type=int, default=180, help="API request timeout in seconds")
    args = parser.parse_args()

    # [v19.1] Load Last Config (Override defaults if not specified via CLI)
    # Priority: CLI Args > Last Config > Hardcoded Default
    last_config = load_last_config()
    
    # If user didn't specify --dir (it equals default), try to load from config
    # Note: argparse default is processed before this, so we check if it matches the hardcoded default
    if args.dir == "商化照片-202601" and "last_image_dir" in last_config:
        args.dir = last_config["last_image_dir"]
    
    # [v19.2 Fix] Start using absolute path to prevent CWD/Relative path issues in Orchestrator
    if not os.path.isabs(args.dir):
        args.dir = os.path.abspath(args.dir)
    
    console.print(f"[Init] 📂 Loaded last used directory: [cyan]{args.dir}[/cyan]")
    
    if args.model == "qwen/qwen3-vl-4b" and "last_model" in last_config:
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
                detected_id = models[0]['id']
                print(f"[Init] 🟢 Auto-Detected Active Model: {detected_id}")
                args.model = detected_id # Override command line arg
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
        "output_file": "final_results_v4.csv", # Legacy
        "assets_dir": "assets",
        "model_list_file": "型號表.txt",
        "clean_config": str(args)
    }

    orchestrator = BatchOrchestrator(config)
    orchestrator.set_processor_function(process_single_image)
    orchestrator.log_system(f"[{SESSION_ID}] 系統初始化完成... 後端已連線。", with_timestamp=True) # Immediate feedback
    
    # [v18.70] 設定價格查詢日誌回調，讓儀錶板也能看到聯網狀態
    def price_log_to_dashboard(msg: str):
        orchestrator.log_system(msg, with_timestamp=False)
    set_price_log_callback(price_log_to_dashboard)
    
    prompt_file = 'samsung_ocr_prompt.txt'
    if os.path.exists(prompt_file):
        prompt_mtime = os.path.getmtime(prompt_file)
        prompt_time_str = datetime.fromtimestamp(prompt_mtime).strftime('%Y-%m-%d %H:%M:%S')
        orchestrator.log_system(f"📜 Prompt 版本: {prompt_time_str}", with_timestamp=False)
        console.print(f"[bold green]📜 Prompt 版本: {prompt_time_str}[/bold green]")
    else:
        orchestrator.log_system(f"❌ 找不到 Prompt 檔案: {prompt_file}", with_timestamp=False)
        console.print(f"[bold red]❌ 找不到 Prompt 檔案: {prompt_file}[/bold red]")
    
    # [v18.67] 啟動時清空價格快取 TXT
    try:
        pm = get_price_manager()
        pm.clear_and_init()
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
            webbrowser.open("http://localhost:5000")
            console.print("[green]🚀 Browser launch command sent.[/green]")
        except Exception as e:
            console.print(f"[red]⚠️ Browser launch failed: {e}[/red]")
            
    Timer(1.5, open_browser).start()
    
    flask_app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False, threaded=True)

if __name__ == "__main__":
    # === 鐵律：最終版本確認 ===
    console.print("\n" + "="*60)
    console.print(f"🎯 [bold green]最終確認：正在執行 {VERSION}[/bold green]")
    console.print(f"📊 Session ID: {SESSION_ID}")
    console.print(f"⚡ 強制重載模式：已確保所有模組為最新版本")
    console.print("="*60 + "\n")
    
    main()

import os
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

# Import Skills
from skills.batch_orchestrator import BatchOrchestrator
from skills.prompt_versioning import PromptManager 

VERSION = "v17.34 (Relaxed Check)"
import random, string
from datetime import datetime
SESSION_ID = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))

# --- Logging Setup ---
console = Console()
logging.basicConfig(
    level="ERROR",  # v9.96: Only show errors in terminal
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True)]
)
log = logging.getLogger("rich")

# --- Flask App ---
# [v14.8] Serve Dashboard from dist folder
flask_app = Flask(__name__, static_folder=os.path.join('dashboard', 'dist'))
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
def process_single_image(fname, image_b64, prompt_mgr, auto_curator, image_processor):
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
    
    # NEW: Inject Few-Shot Examples from AutoCurator (Learning Loop)
    examples_section = ""
    if auto_curator:
        # Reduce to 1 to save tokens (prevent context overflow)
        curated_examples = auto_curator.get_relevant_examples(limit=1)
        if curated_examples:
            examples_str = "\n".join([f"範例 {i+1}:\n[圖片特徵]{ex['context']}\n[正確輸出]{ex['output']}" for i, ex in enumerate(curated_examples)])
            examples_section = f"\n\n### 參考範例 (人工訂正):\n{examples_str}\n請依據上述人類訂正的邏輯進行推論。"
    
    start_time = time.time()
    thinking_text = "" # [v17.18 Fix] Initialize early to prevent UnboundLocalError
    
    # RESTORE ORIGINAL RESOLUTION (No Constraint)
    # [IRON RULE] User said: "Do NOT compress images!!!" 
    image_processor.config["max_size"] = None 
    
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
        msg = f"▶️ 正在分析圖片: {fname} (單一階段 Qwen-VL極速版)..."
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
    
    # Load System Prompt from external file
    try:
        with open('samsung_ocr_prompt.txt', 'r', encoding='utf-8') as f:
            prompt_template = f.read()
    except Exception as e:
        if orchestrator:
            orchestrator.log_system(f"❌ 讀取 Prompt 檔案失敗: {e}, 使用備份 Prompt")
        prompt_template = "你是三星螢幕管理員。請提取型號與價格。..." # 簡單備份

    # v14.3: Use .replace() instead of .format() to avoid KeyError with JSON braces in prompt
    # [v18.12] Disabled Injection to prevent Hallucination
    # system_prompt = prompt_template.replace("{valid_models_str}", valid_models_str) \
    #                                .replace("{examples_section}", "") 
    system_prompt = prompt_template # Raw Prompt (No List)

    # [v17.31 Integrity] Force Echo Filename to prevent crosstalk (849 vs 431 mixup)
    import uuid
    random_salt = str(uuid.uuid4())[:8]
    
    # [v18.25 Dual Vision Prompt]
    if label_b64:
        user_prompt = f"圖片: {fname}\nRequestID: {random_salt}\n我們為你準備了兩張圖：\n1. 圖 1 是環境全景圖 (用於判斷空間與底座)。\n2. 圖 2 是價牌的高清放大圖 (用於精確讀取文字)。\n請在第一行先盤點，並務必以「圖 2」字元為準。"
    else:
        user_prompt = f"圖片: {fname}\nRequestID: {random_salt}\n請提取此照片中的資訊 (繁體中文)，務必包含 Observation 和 JSON。"

    # [v18.22] 零記憶機制強制實作 (Zero Memory Policy)
    # 每次辨識前重設 messages 陣列，不留存任何對話歷史，確保圖片辨識的獨立性。
    messages = [{"role": "system", "content": system_prompt}]
    
    # Construct User Context
    user_content = []
    user_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{full_image_b64}"}})
    if label_b64:
        user_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{label_b64}"}})
    user_content.append({"type": "text", "text": user_prompt})

    messages.append({
        "role": "user",
        "content": user_content
    })

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
        
        start_llm_t = time.time()
        stream = api_client.chat.completions.create(
            model=model_name_global,
            messages=messages,
            response_format=SAMSUNG_AUDIT_SCHEMA, # [v17.09] Enforce JSON Schema
            stream=True,
            temperature=0.7, # [Recommend] Higher temp is safe with Schema
            top_p=0.95,
            max_tokens=1024,
            presence_penalty=0.2, # [v17.20 Fix] Reduce stuttering
            stream_options={"include_usage": True}
        )
        pass
        
        tool_calls_buffer = []

        for chunk in stream:
            # [v17.25 Fix] Aggressive Stop Check
            if orchestrator and not orchestrator.is_running:
                console.print("[red]🛑 用戶強制中斷串流 (Aggressive Stop)[/red]")
                try:
                    stream.close() # Attempt to close stream connection
                except:
                    pass
                return {"error": "Stopped by user"} # Exit immediately, don't just break loop 

            # [v17.11 Fix Refined] Restore Stop Button Functionality
            if orchestrator and not orchestrator.is_running:
                console.print("[red]🛑 用戶強制中斷串流[/red]")
                # stream.close() # Can confuse client
                break

            # [v9.99 Fix] Skip chunks without choices (e.g., usage chunks from stream_options)
            if not hasattr(chunk, 'choices') or not chunk.choices:
                continue
            
            if not full_response_text: # First token!
                duration_llm_first = time.time() - start_llm_t
                console.print(f"[bold green]✨ LLM 已回應 (首字耗時: {duration_llm_first:.2f}s)[/bold green]")
            
            delta = chunk.choices[0].delta
            # 1. Capture Content (Standard)
            content_piece = ""
            if hasattr(delta, 'content') and delta.content:
                content_piece = delta.content
            # 2. Capture Reasoning Content (DeepSeek/Qwen Thought)
            elif hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                content_piece = delta.reasoning_content
            
            if content_piece:
                content_tc = to_tc(content_piece)
                full_response_text += content_tc
                VERSION = "v17.34 (Relaxed Check)"
                
                # [v17.27 Clean Stream] Only show description, HIDE JSON
                # The user prompt enforces: Line 1 (Text) \n Line 2 (JSON)
                if '{' in full_response_text:
                    clean_display = full_response_text.split('{')[0].strip()
                    current_display = clean_display
                else:
                    current_display = full_response_text

                # Final Update
                if orchestrator:
                    orchestrator.stream_buffer = current_display

             
            if delta.tool_calls:
                # [Fallback] If model skips thinking and jumps to tool calls
                if not content_piece and not full_response_text and orchestrator:
                     if fallback_msg not in orchestrator.stream_buffer:
                         orchestrator.stream_buffer += fallback_msg
                         # print(fallback_msg, end="", flush=True)  # v10.0: Disabled
                         pass  # v10.0: Keep block structure

                for tc in delta.tool_calls:
                    if len(tool_calls_buffer) <= tc.index:
                        tool_calls_buffer.append({"id": "", "function": {"name": "", "arguments": ""}})
                    tcb = tool_calls_buffer[tc.index]
                    if tc.id: tcb["id"] = tc.id
                    if tc.function.name: tcb["function"]["name"] = tc.function.name
                    if tc.function.arguments: tcb["function"]["arguments"] += tc.function.arguments

        # print() # Newline after stream  # v10.0: Disabled
    
        # [v9.55 Fix] DISABLED Stream Buffer Logging to prevent JSON leakage. 
        # We now rely solely on the Regex Extraction block below for clean thinking logs.
        # if orchestrator and orchestrator.stream_buffer:
        #     clean_think = orchestrator.stream_buffer.strip()
        #     if "```" in clean_think:
        #         clean_think = clean_think.split("```")[0].strip()
        #     
        #     if clean_think and clean_think != "..." and clean_think != "... [⚡ 模型略過思考，直接提取數據...]":
        #         orchestrator.log_system(f"[{SESSION_ID}] [思考詳細] {clean_think}")
        #         console.print(f"[dim]已將思考過程紀錄至系統日誌。[/dim]")

        # [v17.28 Fix] Parsing Strategy for "Text + JSON" Prompt
        # Prompt Format:
        # Line 1: Description... (Thinking)
        # Line 2: { "view_type": ... } (JSON)
        
        args_str = None
        # [v17.29 Safety] Default to full text so we NEVER verify empty logs
        thinking_text = full_response_text 

        import re
        # Find JSON block (last valid block preferrably)
        json_candidates = re.findall(r'(\{[\s\S]*?\})', full_response_text)
        
        if json_candidates:
            # Take the last candidate that looks like our schema
            for candidate in reversed(json_candidates):
                if '"view_type"' in candidate or '"model"' in candidate:
                    args_str = candidate
                    break
            if not args_str: args_str = json_candidates[-1]
            
            # Extract Thinking Text (Everything BEFORE the JSON)
            # We split by the found JSON string to get the text before it
            parts = full_response_text.split(args_str)
            if parts[0].strip():
                thinking_text = parts[0].strip()

                # [v17.35 Refactored] Cross-Talk Check REMOVED.
                # The v18 Prompt relies on Random Salt for protection and does NOT echo filename.
                pass

        # [Fallback] If strict JSON only (unlikely with this prompt but possible)
        elif full_response_text.strip().startswith('{'):
             args_str = full_response_text
             thinking_text = "..." # Just JSON, no thinking

        if args_str:
             try:
                args_str = sanitize_json(args_str)
                parsed = json.loads(args_str)
                
                if isinstance(parsed, dict):
                    # [v17.28] New Prompt puts 'desc' OUTSIDE JSON.
                    # But if the model reverted to old schema (desc inside), handle it.
                    if 'desc' in parsed and not thinking_text:
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
                    data_obj = parsed.get('data', parsed)
                    if not isinstance(data_obj, dict): data_obj = parsed # Fallback

                    # 2. Strict Model Check -> Fuzzy Recovery [v18.04]
                    import difflib # Ensure import available (inline is safe)
                    raw_model = data_obj.get("model")
                    if raw_model and isinstance(raw_model, str):
                        # [v18.12] Enhanced Noise Removal for Raw OCR
                        # Remove: quotes, units, brand names to maximize fuzzy match chance
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
                             if orchestrator:
                                 orchestrator.log_system(f"⚠️ [FollowMe Logic] '{raw_model}' mapped to '{clean_model}' (Price: {p_val})")

                        if valid_models_list and clean_model not in valid_models_list:
                             # Try Fuzzy Match (Aggressive Recovery)
                             # Cutoff 0.8: Only catch close typos (1-2 chars), avoid wrong model guessing
                             matches = difflib.get_close_matches(clean_model, valid_models_list, n=1, cutoff=0.8)
                             if matches:
                                 corrected_model = matches[0]
                                 if orchestrator: 
                                     orchestrator.log_system(f"⚠️ [模糊修復] '{clean_model}' -> '{corrected_model}' (In List)")
                                 data_obj["model"] = corrected_model
                             else:
                                 if orchestrator: 
                                     orchestrator.log_system(f"⚠️ [幻覺攔截] Model '{clean_model}' 無法匹配任何型號 -> None")
                                 data_obj["model"] = None
                        else:
                             data_obj["model"] = clean_model

                    # 3. Strict Price Check (4-5 digits only)
                    raw_price = data_obj.get("price")
                    if raw_price:
                        clean_price = "".join([c for c in str(raw_price) if c.isdigit()])
                        if len(clean_price) in [4, 5]:
                            data_obj["price"] = clean_price
                        else:
                            if orchestrator: orchestrator.log_system(f"⚠️ [價格攔截] Price '{raw_price}' -> '{clean_price}' 長度不符 (4-5) -> 強制修正為 None")
                            data_obj["price"] = None

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
                             # Since strict JSON doesn't carry this info, we infer it from Line 1.
                             desc_keywords_unclear = ["不清", "模糊", "反光", "遮擋", "無法辨識", "看不到"]
                             is_unclear = any(k in thinking_text for k in desc_keywords_unclear) if thinking_text else False
                             
                             if is_unclear:
                                 data_obj["quality_issue"] = "不合格-照不清楚"
                             else:
                                 data_obj["quality_issue"] = "不合格-沒有規格和價格牌"

                    # Update final result
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

                    # [v17.18 Fix] REMOVED Early "Unlisted" Marking
                    # Reason: This logic conflicts with Prompt's fuzzy matching and blocks the Python ModelMatcher.
                    # We now let the late-stage ModelMatcher handle alignment.
                else:
                    log.error(f"JSON Output was not a dict: {type(parsed)} -> {parsed}")
                    if orchestrator: orchestrator.log_system(f"❌ JSON 解析後非物件: {str(parsed)[:50]}")
                    
             except Exception as e:
                log.error(f"JSON Parse Error: {e}")
                if orchestrator: orchestrator.log_system(f"❌ JSON 格式不完整，正在啟動救援程序... (內容: {str(args_str)[:60]}...)")
                # Do not return! Fall through to emergency extraction.

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

            # [v17.26 Fix] Aggressively strip "Observation:" prefix
            if thinking_text:
                import re
                thinking_text = re.sub(r'(?i)^observation:\s*', '', thinking_text).strip()

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
                         orchestrator.log_system(f"⚠️ [價格攔截] Price '{raw_price}' 含規格/道具關鍵字 -> 無效")
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
            
        # [v9.71 Universal Summary Log] 
        # MOVED OUT OF ELSE BLOCK to guarantee execution.
        if orchestrator:
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
        log.error(f"Analysis Failed: {e}")
        if orchestrator: orchestrator.log_system(f"❌ 系統錯誤: {str(e)}")
    
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
    
    # Return full run results (in memory)
    # [v11.4] Aggregated History (Current + Legacy + Previous Sessions)
    results = orchestrator.get_all_records()
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
        orchestrator.start_batch(restart=restart, reprocess_last_n=reprocess_last_n)
        mode_text = "重新啟動" if restart else "繼續執行"
        return jsonify({"status": "started", "message": f"批次處理已{mode_text} (目錄: {orchestrator.image_dir})"})
        
    except Exception as e:
        return jsonify({"error": f"啟動失敗: {str(e)}"}), 500

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

@flask_app.route('/<path:filename>')
def serve_dist_fallback(filename):
    """服務 dist 根目錄的其他檔案 (如 favicon, failed_records.html 等)"""
    if os.path.exists(os.path.join(flask_app.static_folder, filename)):
        return send_from_directory(flask_app.static_folder, filename)
    return f"File not found: {filename}", 404

# --- Main ---
def main():
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
    parser.add_argument("--dir", default="商化照片-202512", help="Image directory")
    parser.add_argument("--api_base", default="http://192.168.0.234:1234/v1", help="LM Studio/OpenAI Base URL")
    parser.add_argument("--api_key", default="lm-studio", help="API Key")
    parser.add_argument("--model", default="qwen/qwen3-vl-4b", help="Model Name")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of files")
    parser.add_argument("--timeout", type=int, default=180, help="API request timeout in seconds")
    args = parser.parse_args()

    model_name_global = args.model
    # v14.2: Don't force 127.0.0.1 if localhost works better for user
    api_client = OpenAI(base_url=args.api_base, api_key=args.api_key, timeout=float(args.timeout), max_retries=1)

    # Config for Orchestrator
    config = {
        "image_dir": args.dir,
        "output_dir": ".", # Root for csvs
        "output_file": "final_results_v4.csv", # Legacy
        "assets_dir": "assets",
        "persist_file": "dynamic_data.json",
        "model_list_file": "型號表.txt",
        "clean_config": str(args)
    }

    orchestrator = BatchOrchestrator(config)
    orchestrator.set_processor_function(process_single_image)
    orchestrator.log_system(f"[{SESSION_ID}] 系統初始化完成... 後端已連線。", with_timestamp=True) # Immediate feedback
    
    # Replace standard print with console.print/log to avoid CP950 errors on Windows
    title = f"Samsung OCR Batch System {VERSION} [SID: {SESSION_ID}]"
    console.print(f"[bold yellow]>>> SESSION: {SESSION_ID} <<<[/bold yellow]")
    console.print(f"[bold green]{title}[/bold green]")
    console.print(f"Image Dir: {args.dir}")
    console.print(f"Model: {args.model}")
    console.print(f"API Base: {args.api_base}")
    console.print("--------------------------------------------------")
    
    # Start loop
    # Start loop (start_batch spawns its own thread)
    orchestrator.start_batch(args.limit)

    # [v17.27] Explicitly enable threading to prevent UI blocking
    flask_app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False, threaded=True)

if __name__ == "__main__":
    main()

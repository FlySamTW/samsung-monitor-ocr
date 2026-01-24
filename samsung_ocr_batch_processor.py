import os
import argparse
import time
import json
import base64
import logging
import psutil
from flask import Flask, jsonify, request, send_file, send_from_directory, send_from_directory, Response
from flask_cors import CORS
from rich.console import Console
from rich.logging import RichHandler
from openai import OpenAI

# Import Skills
from skills.batch_orchestrator import BatchOrchestrator
from skills.prompt_versioning import PromptManager 

VERSION = "v9.60 (Final Polish)"
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
flask_app = Flask(__name__)
CORS(flask_app)
orchestrator: BatchOrchestrator = None
api_client: OpenAI = None
model_name_global = ""

# --- Helper: Coordinate Clamping ---
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
        orchestrator.stream_buffer = "..." # Reset to indicator

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
    
    # RESTORE ORIGINAL RESOLUTION (No Constraint)
    # [IRON RULE] User said: "Do NOT compress images!!!" 
    image_processor.config["max_size"] = None 
    active_file = os.path.join(orchestrator.image_dir, fname)
    # if orchestrator: orchestrator.log_system(f"▶️ 載入圖片: {fname} (原始畫質/無壓縮)")
    result = image_processor.process(active_file)
    
    if not result:
        return {"error": "Image processing failed"}

    # Use Full Image directly (Single Stage)
    full_image_b64 = result['base64'] 

    if orchestrator:
        # orchestrator.log_system(f"▶️ 正在分析圖片: {fname} (單一階段 Qwen-VL極速版)...")
        console.print(f"[cyan]正在分析... {fname}[/cyan]")
    
    # Adopt User's "Samsung Manager" Persona Prompt + IRON RULE (v9.9)
    # Get model list for injection
    # v9.38: Optimization: Don't inject huge string
    valid_models_str = "Samsung Monitor List (Internal)"
    
    # Load System Prompt from external file
    try:
        with open('samsung_ocr_prompt.txt', 'r', encoding='utf-8') as f:
            prompt_template = f.read()
    except Exception as e:
        if orchestrator:
            orchestrator.log_system(f"❌ 讀取 Prompt 檔案失敗: {e}, 使用備份 Prompt")
        prompt_template = "你是三星螢幕管理員。請提取型號與價格。..." # 簡單備份

    system_prompt = prompt_template.format(
        valid_models_str=valid_models_str,
        examples_section=examples_section
    )

    user_prompt = f"圖片: {fname}\n請提取資訊 (繁體中文)，務必包含 Observation 和 JSON。"
    
    # messages usage check - No content change, just ensuring I check.
    if orchestrator:
        # orchestrator.log_system(f"▶️ [全圖分析] 讀取資訊與思考中...")
        console.print(f"[cyan][全圖分析] 詳細資訊提取中...[/cyan]")

    messages = [{"role": "system", "content": system_prompt}]
    messages.append({
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{full_image_b64}"}},
            {"type": "text", "text": user_prompt}
        ]
    })

    full_response_text = ""
    result_json = {"category": "失敗", "model": None, "price": None, "black_screen": False}

    # Reset Stream Buffer for strict real-time sync
    if orchestrator:
        orchestrator.stream_buffer = "" # Clean start
        orchestrator._stream_active = True # [v9.69 Fix] Reset stream latch for new image!
        # orchestrator.log_system(f"▶️ 正在分析圖片: {fname} (單一階段 Qwen-VL極速版)...") # Removed duplicate log
        # console.print(f"[cyan]▶️ 正在分析圖片: {fname}[/cyan]")  # v10.0: Disabled for performance

    try:
        stream = api_client.chat.completions.create(
            model=model_name_global,
            messages=messages,
            # tools=[tool_def], # REMOVED: Force Text Output
            stream=True,
            temperature=0.1,
            top_p=0.8,  # Qwen official recommendation
            max_tokens=150,  # v10.1: ~100 chars, let LLM flow naturally without word counting
            stream_options={"include_usage": True}  # Qwen streaming optimization
        )
        
        tool_calls_buffer = []

        for chunk in stream:
            # [v9.99 Fix] Skip chunks without choices (e.g., usage chunks from stream_options)
            if not hasattr(chunk, 'choices') or not chunk.choices:
                continue
                
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
                
                # --- [Stream Logic v9.13] ---
                # 1. No Artificial Delay (User Request: "Faster!")
                # 2. Stop updating visual buffer if JSON block starts (User Request: "No Code!")
                
                if orchestrator:
                    # Check if we are entering code block
                    if "```" in content_tc or "json" in content_tc.lower():
                        # Simple check: if buffer doesn't have it yet, maybe text is transitioning
                        # stricter: check full response or lookahead. 
                        # reliable: checks if full_response_text has '```json'
                        pass
                    
                    # Logic: Only add to stream_buffer if we haven't seen the start of JSON yet
                    # identifying marker: "```json" or "Step 2" if strict
                    is_json_part = False
                    if "```json" in full_response_text:
                         is_json_part = True
                    
                    # [v9.62 Real-time Sanitization]
                    # Attempt to clean partial chunks of jargon.
                    # Note: Split tokens might still leak, but this covers most cases.
                    stream_replacements = {
                        "model=null": "無型號",
                        "price=null": "無價格",
                        "null": "無",
                        "false": "否",
                        "true": "是",
                        "[思考]": "" # [v9.66 Hide Thinking Tag]
                    }
                    temp_tc = content_tc
                    for k, v in stream_replacements.items():
                        if k in temp_tc:
                            temp_tc = temp_tc.replace(k, v)
                    
                    # [v9.68 Strict Split-and-Stop]
                    # If we aren't streaming, just print to console and skip buffer update
                    # Note: We rely on 'is_json_part' as our persistent latch (defined outside loop? No, need to be careful)
                    # Actually, 'is_json_part' is reset every loop in previous code! This was the BUG!
                    # It should be a persistent state variable across chunks.
                    
                    # Correction: creating a local latch.
                    # We assume 'is_now_json' tracks if we have EVER hit json in this session.
                    if getattr(orchestrator, '_stream_active', True):
                        if '{' in temp_tc:
                            # CRITICAL: Split at the exact point of JSON start
                            safe_part = temp_tc.split('{')[0]
                            
                            # Clean up trailing junk like "here is the json"
                            if "json" in safe_part.lower():
                                safe_part = safe_part.replace("json", "").replace("JSON", "")
                            
                            orchestrator.stream_buffer += safe_part
                            orchestrator._stream_active = False # TRIP THE BREAKER
                            # print(safe_part, end="", flush=True)  # v10.0: Disabled
                        else:
                            # Check for other "code leakage" signals at end of stream
                            if "```" in temp_tc:
                                safe_part = temp_tc.split("```")[0]
                                orchestrator.stream_buffer += safe_part
                                orchestrator._stream_active = False
                                # print(safe_part, end="", flush=True)  # v10.0: Disabled
                            else:
                                orchestrator.stream_buffer += temp_tc
                                # print(temp_tc, end="", flush=True)  # v10.0: Disabled 
                    else:
                         # Stream is dead, long live the logs
                         # print(content_tc, end="", flush=True)  # v10.0: Disabled
                         pass  # v10.0: Keep block structure

            if delta.tool_calls:
                # [Fallback] If model skips thinking and jumps to tool calls
                if not content_piece and not full_response_text and orchestrator:
                     fallback_msg = " [⚡ 模型略過思考，直接提取數據...]"
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

        # --- Parse Result (Strategy: Tool Call -> Regex Fallback) ---
        args_str = None
        
        # Strategy A: Tool Calls
        if tool_calls_buffer:
             args_str = tool_calls_buffer[0]["function"]["arguments"]
        
        # Strategy B: Regex Fallback (if no tool calls)
        if not args_str:
            import re
            # [v9.50 Fix] Improved extraction for "[思考] ... {JSON}" format
            # 1. Try to find the last valid JSON object block
            json_candidates = re.findall(r'(\{[\s\S]*?\})', full_response_text)
            
            if json_candidates:
                # Iterate backwards to find the one that looks most like our result
                for candidate in reversed(json_candidates):
                    if '"category"' in candidate and '"model"' in candidate:
                        args_str = candidate
                        break
                
                # If still none, take the last one
                if not args_str and json_candidates:
                    args_str = json_candidates[-1]
        
        if args_str:
             try:
                # [v9.50 Fix] Pre-cleaning to avoid "str object" or KeyError issues
                if isinstance(args_str, str):
                    args_str = args_str.strip()
                    # Remove any leading non-json junk if mixed in (though regex usually handles this)
                
                args_str = sanitize_json(args_str)
                # [DEBUG LOG REMOVED]
                # if orchestrator: 
                #     orchestrator.log_system(f"🔍 [DEBUG] Pre-Parse JSON: {repr(args_str)[:100]}...")

                parsed = json.loads(args_str)
                
                # [CRITICAL CHECK] Ensure it is a DICT
                if isinstance(parsed, dict):
                    # [v9.52 Fix] Use UPDATE instead of REPLACE to preserve defaults (category='失敗')
                    # This prevents KeyError if parsed dict has weird keys or missing keys
                    result_json.update(parsed)
                    
                    # [Safety] Check for weird keys containing newlines (The specific user error)
                    # If we find them, try to fix them by stripping whitespace
                    for k in list(result_json.keys()):
                        if isinstance(k, str) and ('\n' in k or ' ' in k) and k.strip().strip('"') in ['category', 'model', 'price', 'black_screen']:
                            clean_k = k.strip().strip('"')
                            val = result_json.pop(k)
                            result_json[clean_k] = val
                            if orchestrator: orchestrator.log_system(f"🔧 修復畸形鍵名: {repr(k)} -> {clean_k}")
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
            # [v9.71 Regex Reasoning Extraction]
            # Capture everything before the first '{' as reasoning.
            thinking_text = ""
            if '{' in full_response_text:
                thinking_text = full_response_text.split('{')[0].strip()
            else:
                thinking_text = full_response_text.strip()
            
            # Remove any unwanted residue (like [思考] if AI ignores prompt)
            thinking_text = thinking_text.replace('[思考]', '').replace('觀察內容...', '').strip()

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
                
                orchestrator.log_system(f"[思考詳細] {clean_think}")

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
            if current_cat == "單機" and not result_json.get("model"):
                result_json["category"] = "不合格-單機但看不清楚價格或型號"

            # [v9.56 Strict Price Rule]
            raw_price = result_json.get("price")
            if raw_price and isinstance(raw_price, str) and ',' not in raw_price and '$' not in raw_price and 'NT' not in raw_price:
                result_json["price"] = None

        except Exception as e:
            log.error(f"Validation Error: {e}")
            if orchestrator: orchestrator.log_system(f"⚠️ 解析異常: {str(e)}")
            
        # [v9.71 Universal Summary Log] 
        # MOVED OUT OF ELSE BLOCK to guarantee execution.
        if orchestrator:
            cat = result_json.get("category", "")
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

    except Exception as e:
        log.error(f"Analysis Failed: {e}")
        if orchestrator: orchestrator.log_system(f"❌ 系統錯誤: {str(e)}")
    
    return result_json

# --- Flask API Routes ---

@flask_app.route('/')
def index():
    # Defensive path check to prevent 500
    dist_path = os.path.join('dashboard', 'dist', 'index.html')
    if not os.path.exists(dist_path):
        return "Dashboard Build in progress... Please wait and refresh.", 503
    response = send_file(dist_path)
    # [v9.81] Force no-cache to ensure latest UI is always served
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@flask_app.route('/assets/<path:path>')
def serve_assets(path):
    assets_dir = os.path.join('dashboard', 'dist', 'assets')
    if not os.path.exists(assets_dir):
        return "Assets not found", 404
    response = send_from_directory(assets_dir, path)
    # [v9.81] Force no-cache for JS/CSS assets
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@flask_app.route('/api/status', methods=['GET'])
def get_status():
    """獲取系統狀態"""
    if not orchestrator:
        return jsonify({"error": "Orchestrator 未初始化"}), 500
    
    try:
        # 構建狀態對象
        metrics = orchestrator.get_performance_metrics()
        
        # [🔋 FORCE SYNC] 直接抓取全域變量，確保不被線程鎖定
        status_obj = {
            "version": VERSION,
            "current_file": getattr(orchestrator, 'current_file', 'None'),
            "stats": orchestrator.stats,
            "metrics": metrics,
            "stream_buffer": str(orchestrator.stream_buffer), # 強制轉字串避免類型錯誤
            "lm_logs": list(orchestrator.system_logs),       # 傳送完整日誌確保前端過濾
            "recent_results": orchestrator.recent_results,
            "is_running": orchestrator.is_running,
            "resources": {
                "cpu": psutil.cpu_percent(interval=None),
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
        
        # Get directory and restart flag from request
        req_data = request.json or {}
        target_dir = req_data.get('dir')
        restart = req_data.get('restart', False) # Default to false (Continue)
        
        if target_dir:
            if os.path.exists(target_dir):
                orchestrator.image_dir = target_dir
            else:
                return jsonify({"error": f"資料夾不存在: {target_dir}"}), 404

        # 開始批次處理
        orchestrator.start_batch(restart=restart)
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

@flask_app.route('/api/feedback', methods=['POST'])
def feedback():
    if not orchestrator: return jsonify({"error": "Init"}), 503
    data = request.json
    fname = data.get('file_name')
    if data.get('is_correct'): return jsonify({"status":"ok"})
    
    correct_data = data.get('correct_data')
    if correct_data:
        orchestrator.auto_curator.add_correction(fname, correct_data)
        # Re-queue
        if fname not in orchestrator.retry_queue:
            orchestrator.retry_queue.append(fname)
        
        return jsonify({"status": "ok", "msg": "Learned & Re-queued"})
    return jsonify({"error": "Missing data"}), 400

@flask_app.route('/api/correct', methods=['POST'])
def correct_result():
    """新的修正API端點，用於整合dashboard"""
    if not orchestrator: 
        return jsonify({"error": "系統未初始化"}), 503
    
    try:
        data = request.json
        filename = data.get('filename')
        
        if not filename:
            return jsonify({"error": "缺少檔名"}), 400
        
        # 創建修正資料
        correction_data = {
            "category": data.get('category', '單機'),
            "model": data.get('model', ''),
            "price": data.get('price', ''),
            "black_screen": data.get('black_screen', False)
        }
        
        # 添加到修正學習系統
        orchestrator.auto_curator.add_correction(filename, correction_data)
        
        # 記錄到日誌
        orchestrator.log_system(f"✅ 人工修正: {filename} -> {correction_data}")
        
        # 重新加入處理佇列
        if filename not in orchestrator.retry_queue:
            orchestrator.retry_queue.append(filename)
            orchestrator.log_system(f"🔄 重新排程: {filename}")
        
        return jsonify({
            "status": "success", 
            "message": "修正已提交並加入學習",
            "filename": filename,
            "correction": correction_data
        })
        
    except Exception as e:
        return jsonify({"error": f"修正失敗: {str(e)}"}), 500

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
    parser.add_argument("--api_base", default="http://localhost:1234/v1", help="LM Studio/OpenAI Base URL")
    parser.add_argument("--api_key", default="lm-studio", help="API Key")
    parser.add_argument("--model", default="qwen/qwen3-vl-4b", help="Model Name")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of files")
    parser.add_argument("--timeout", type=int, default=180, help="API request timeout in seconds")
    args = parser.parse_args()

    model_name_global = args.model
    # Force 127.0.0.1 and add timeout to prevent hangs
    base_url_fixed = args.api_base.replace("localhost", "127.0.0.1")
    api_client = OpenAI(base_url=base_url_fixed, api_key=args.api_key, timeout=float(args.timeout), max_retries=1)

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

    flask_app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)

if __name__ == "__main__":
    main()

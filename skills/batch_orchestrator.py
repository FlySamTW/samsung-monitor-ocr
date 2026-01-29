import os
import time
import json
import csv
import logging
from datetime import datetime
from threading import Thread, Event
from typing import List, Optional, Callable

# Import Skills
from skills.prompt_versioning import PromptManager
from skills.image_processing import ImageProcessor
from skills.model_matching import ModelMatcher
from skills.field_extraction import FieldNormalizer
from skills.evaluation import Evaluator

log = logging.getLogger("rich")

class BatchOrchestrator:
    """
    Main Controller.
    - Manages the processing loop (files -> process -> save).
    - Handles Retries and Circuit Breaking.
    - Maintains runtime state (metrics, logs) for the API.
    """
    def __init__(self, config: dict):
        self.config = config
        self.image_dir = config['image_dir']
        self.output_dir = config['output_dir']
        os.makedirs(self.output_dir, exist_ok=True) # Ensure output dir exists

        # Initializes Skills
        self.prompt_mgr = PromptManager(config['assets_dir'])
        self.img_proc = ImageProcessor({"max_size": None})  # [v18.57] 不壓縮原圖
        self.model_matcher = ModelMatcher(config['model_list_file'])
        self.field_norm = FieldNormalizer()
        self.evaluator = Evaluator()

        # Runtime State
        # Runtime State - Init logs FIRST
        self.stats = {"total": 0, "processed": 0, "success": 0, "failed": 0, "streak": 0, "max_streak": 0, "is_running": False}
        self.system_logs = []
        self.stream_buffer = "" # Real-time streaming buffer
        self.recent_results = []
        self.retry_queue = []
        self.base_success_count = 0 # [v11.7] Cache for performance

        self.is_running = False
        self.stop_event = Event() # Retained for functionality
        self.current_file = None # [v9.92] Initialize to prevent AttributeError
        self.log_system("批次處理已停止。") # Added as per instruction
        self.save_data_file = None 
        self.recent_results = []
        self.retry_queue = []
        self.failed_files = [] 
        self.failed_files = [] 
        self.stream_buffer = "" 
        self.unknown_models = set() # [v16.10] Track unique unknown models 
        
        # Dynamic Session Paths [v11.2]
        self.current_success_file = None # [v16.5] No default to prevent root pollution
        self.current_failed_file = None  # [v16.5] No default to prevent root pollution
        
        # Processor Function (Dependency Injection)
        self.processor_fn = None 
        
        # [v16.12] Force Rerun Queue
        self.priority_queue = [] 

    def force_rerun(self, filename: str):
        """
        [v16.12] Manually trigger re-processing of a specific file.
        1. Remove from 'processed' / 'failed' cache if exists.
        2. Delete the specific result JSON file to ensure 'overwrite' logic triggers.
        3. Add to priority queue.
        """
        self.log_system(f"🔄 收到強制重跑請求: {filename}")
        
        # 1. Clean Memory State (Simple check, exact cleanup happens in loop)
        # We don't need to surgically remove from self.recent_results or stats immediately
        # because processing loop handles checking.
        
        # 2. Clean Disk State (Force Clean)
        json_path = os.path.join(self.image_dir, f"{os.path.splitext(filename)[0]}_ocr_result.json")
        if os.path.exists(json_path):
            try:
                os.remove(json_path)
                self.log_system(f"   🗑️ 已刪除舊結果檔案: {os.path.basename(json_path)}")
            except Exception as e:
                self.log_system(f"   ⚠️ 刪除舊檔失敗: {e}")
        
        # 3. Inject into Priority Queue
        if filename not in self.priority_queue:
            self.priority_queue.append(filename)
            self.log_system(f"   ✅ 已加入優先佇列 (目前 {len(self.priority_queue)} 筆排隊中)")
            return True
        return False

    def set_processor_function(self, fn: Callable):
        """Sets the function that performs the actual LLM call."""
        self.processor_fn = fn

    def get_all_records(self):
        """
        [v14.9] Scoped to current folder. Aggregates records for files that actually EXIST.
        """
        all_records_map = {}
        # Get actual files in current dir
        try:
            actual_files = set(f for f in os.listdir(self.image_dir) if f.lower().endswith(('.jpg', '.jpeg', '.png')))
        except:
            actual_files = set()

        # 1. Load Legacy Global File
        legacy_files = [os.path.join(self.image_dir, "project-output.json"), "project-output.json"]
        for lf in legacy_files:
            if os.path.exists(lf):
                self._load_json_to_map(lf, all_records_map)
        
        # 2. Load Session Files
        if os.path.exists(self.image_dir):
            for fname in os.listdir(self.image_dir):
                if fname.endswith("OCR成功.json"):
                    self._load_json_to_map(os.path.join(self.image_dir, fname), all_records_map)

        # 3. Filter by existing files and prioritize Memory
        final_map = {}
        for f in actual_files:
            if f in all_records_map:
                final_map[f] = all_records_map[f]
        
        # Overlay Memory
        for res in self.run_results:
            if res['file_name'] in actual_files:
                final_map[res['file_name']] = res
            
        return list(final_map.values())

    def get_all_failed_records(self):
        """
        [v14.9] Scoped to current folder. Aggregates failures for files that exist and haven't succeeded.
        """
        all_failed_map = {}
        try:
            actual_files = set(f for f in os.listdir(self.image_dir) if f.lower().endswith(('.jpg', '.jpeg', '.png')))
        except: 
            actual_files = set()

        if os.path.exists(self.image_dir):
            for fname in os.listdir(self.image_dir):
                if fname.endswith("OCR失敗.json"):
                    full_path = os.path.join(self.image_dir, fname)
                    try:
                        with open(full_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                            for item in data:
                                fname_ref = item.get('filename')
                                if fname_ref in actual_files:
                                    all_failed_map[fname_ref] = item
                    except: pass
        
        for item in self.failed_files:
            if item.get('filename') in actual_files:
                all_failed_map[item['filename']] = item

        # Filter out files that eventually succeeded in this folder
        success_filenames = set(r['file_name'] for r in self.get_all_records())
        return [record for fname, record in all_failed_map.items() if fname not in success_filenames]

    def _load_json_to_map(self, filepath, record_map):
        """
        Helper to parse Label Studio JSON back to internal flat format.
        [v11.5] Schema Migration:
        - Splits old 'category' string into: view_type, screen_status, quality_issue
        """
        if not os.path.exists(filepath): return
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for item in data:
                    # Parse LS JSON
                    try:
                        img_path = item.get('data', {}).get('image', '')
                        filename = os.path.basename(img_path)
                        if not filename: continue
                        
                        # Initial default values
                        res = {
                            "file_name": filename,
                            "view_type": "單機",        # Default View
                            "screen_status": "",      # Default Empty
                            "quality_issue": "",      # Default Empty
                            "note": "",               # Default Empty
                            "model": "",
                            "price": "",
                            "timestamp": item.get('annotations', [{}])[0].get('created_at', ''),
                            "thumb_b64": None
                        }
                        
                        annotations = item.get('annotations', [])
                        if annotations:
                            result_list = annotations[0].get('result', [])
                            for field in result_list:
                                from_name = field.get('from_name')
                                val_obj = field.get('value', {})
                                
                                if from_name == 'category':
                                    # [MIGRATION LOGIC] Parse old complex string
                                    raw_cat = val_obj.get('choices', [''])[0]
                                    
                                    # 1. View Type
                                    if "遠景" in raw_cat:
                                        res['view_type'] = "遠景"
                                    else:
                                        res['view_type'] = "單機"
                                        
                                    # 2. Screen Status (Infer from old string or separate logic)
                                    if "黑屏" in raw_cat: res['screen_status'] = "黑屏"
                                    elif "藍屏" in raw_cat: res['screen_status'] = "藍屏"
                                    
                                    # 3. Quality Issue
                                    if "看不清楚" in raw_cat: res['quality_issue'] = "照不清楚"
                                    elif "沒有規格" in raw_cat: res['quality_issue'] = "沒有規格價格牌"
                                    
                                elif from_name == 'model':
                                    res['model'] = val_obj.get('text', [''])[0]
                                elif from_name == 'price':
                                    res['price'] = val_obj.get('text', [''])[0]
                                    
                                    res['price'] = val_obj.get('text', [''])[0]
                                    
                            # [v12.0] Migration Logic for new fields
                            # If we don't have view_type yet (loaded from old JSON), infer it
                            if 'view_type' not in res:
                                res['view_type'] = "單機" # Default

                            # [v11.5] If black_screen was a separate boolean before, merge it
                            # Some older JSONs might have had 'black_screen' key at root level if I saved it that way?
                            # No, LS format stores in result_list. 
                            # However, checking if my previous 'get_all_records' injected 'black_screen' from somewhere else?
                            # It was parsing 'black_screen' from result_json in 'evaluation.py'.
                            # Let's check 'result_list' for explicit black_screen tag if it existed?
                            # Assuming purely string parsing for now as per prior logic.

                        # Populate map
                        record_map[filename] = res
                    except Exception as e:
                        continue
        except Exception as e:
            log.error(f"Failed to load {filepath}: {e}")

    def update_record_by_filename(self, filename: str, updates: dict):
        """
        [v11.6] Inline Edit Support
        Locates the record for 'filename' in the active session file or history,
        updates it, and saves it back to disk.
        """
        target_file = None
        target_index = -1
        current_data = []

        # 1. Search in Current Session File
        if os.path.exists(self.current_success_file):
            try:
                with open(self.current_success_file, 'r', encoding='utf-8') as f:
                    current_data = json.load(f)
                    for i, item in enumerate(current_data):
                        img_path = item.get('data', {}).get('image', '')
                        if os.path.basename(img_path) == filename:
                            target_file = self.current_success_file
                            target_index = i
                            break
            except Exception as e:
                log.error(f"Error reading current session for update: {e}")

        # 2. If not found, search in History Files (Newest first)
        if not target_file:
            try:
                for fname in sorted(os.listdir(self.image_dir), reverse=True):
                    if fname.endswith("OCR成功.json"):
                        full_path = os.path.join(self.image_dir, fname)
                        try:
                            with open(full_path, 'r', encoding='utf-8') as f:
                                data = json.load(f)
                                for i, item in enumerate(data):
                                    img_path = item.get('data', {}).get('image', '')
                                    if os.path.basename(img_path) == filename:
                                        target_file = full_path
                                        target_index = i
                                        current_data = data
                                        break
                        except: pass
                        if target_file: break
            except: pass

        if target_file and target_index >= 0:
            # Apply Updates to Label Studio Format
            res_list = current_data[target_index]['annotations'][0]['result']
            
            # Helper to update or append specific field
            def update_ls_field(field_name, text_value):
                found = False
                for field in res_list:
                    if field.get('from_name') == field_name:
                        # Update existing
                        if field_name == 'category': # Old field, now unused but maybe keep for compat?
                             pass
                        elif 'text' in field.get('value', {}):
                            field['value']['text'] = [text_value]
                        elif 'choices' in field.get('value', {}):
                            field['value']['choices'] = [text_value]
                        found = True
                        break
                
                # If not found, create new (Simplified LS structure)
                if not found:
                    new_field = {
                        "from_name": field_name,
                        "to_name": "image",
                        "type": "textarea" if field_name in ['model', 'price'] else "choices",
                        "value": {
                            "text" if field_name in ['model', 'price'] else "choices": [text_value]
                        }
                    }
                    res_list.append(new_field)

            # Map flat updates to LS fields
            # Note: We are migrating away from 'category' choices to discrete fields in LS?
            # For now, let's map back to the flat structure the frontend expects.
            # But wait, 'project-output.json' IS LS FORMAT.
            # We need to decide: Do we update the old 'category' choices string or separate fields?
            # Propose: Update the specific fields (model, price) is easy.
            # Updating 'view_type', 'screen_status' etc into the old 'category' field is hard.
            # Let's just update 'model' and 'price' for now as those are the most critical edits.
            
            if 'model' in updates: update_ls_field('model', updates['model'])
            if 'price' in updates: update_ls_field('price', updates['price'])
            
            # Simple metadata update
            if 'view_type' in updates: 
                 # Hack: append to note or ignore? 
                 # Let's support saving these new fields into LS format if we can.
                 # For now, assuming user edits model/price mostly.
                 pass

            # Save back to file
            try:
                with open(target_file, 'w', encoding='utf-8') as f:
                    json.dump(current_data, f, ensure_ascii=False, indent=2)
                return True, "Updated successfully"
            except Exception as e:
                return False, f"Failed to save file: {e}"
        
        return False, "Record not found"

    def _delete_records_from_disk(self, filenames: List[str]):
        """
        [v16.2] Truly delete records for specific files from ALL JSON logs on disk.
        This ensures 're-run' starts fresh without duplicate or legacy data.
        """
        if not filenames: return
        
        target_files = set(filenames)
        deleted_count = 0
        
        # [v16.4 Fix] Target both ImageDir logs AND Root Legacy logs
        # We construct a list of FULL PATHS to process
        files_to_scan = []
        
        # 1. Image Directory Logs
        for f in os.listdir(self.image_dir):
            if f.endswith(('OCR成功.json', 'OCR失敗.json')) or f == "project-output.json":
                files_to_scan.append(os.path.join(self.image_dir, f))
                
        # 2. Root Legacy Log (The Ghost Record Source)
        root_legacy = "project-output.json" # Relative to CWD
        if os.path.exists(root_legacy):
            files_to_scan.append(os.path.abspath(root_legacy))
            
        files_to_scan = list(set(files_to_scan))

        for full_path in files_to_scan:
            if not os.path.exists(full_path): continue
            
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                original_len = len(data)
                
                # Filter out targets
                # Handle both LS format (data.image) and simple format (filename)
                new_data = []
                for item in data:
                    fname = ""
                    # Try LS format
                    if 'data' in item and 'image' in item['data']:
                        fname = os.path.basename(item['data']['image'])
                    # Try simple format
                    elif 'filename' in item:
                        fname = item['filename']
                    # Try flat format
                    elif 'file_name' in item:
                        fname = item['file_name']
                        
                    if fname and fname in target_files:
                        continue # Skip (Delete)
                    new_data.append(item)
                
                if len(new_data) < original_len:
                    # Save back
                    with open(full_path, 'w', encoding='utf-8') as f:
                        json.dump(new_data, f, ensure_ascii=False, indent=2)
                    deleted_count += (original_len - len(new_data))
                    
            except Exception as e:
                log.error(f"Error deleting from {full_path}: {e}")
        
        if deleted_count > 0:
            self.log_system(f"🗑️ 已從 {len(files_to_scan)} 個紀錄檔中移除 {deleted_count} 筆舊資料 (重跑準備)。")
        
    def _purge_records_for_restart(self):
        """
        [v17.08] Physically remove old results to prevent shadowing.
        This ensures that if we 'Restart' then 'Stop' then 'Continue', 
        the 'Continue' step won't see legacy records from BEFORE the restart.
        """
        self.log_system("🧹 正在清理舊有的辨識紀錄 (已備份至 runs 目錄)...")
        
        count = 0
        for f in os.listdir(self.image_dir):
            file_path = os.path.join(self.image_dir, f)
            # 1. Target Session JSONs
            is_session = f.endswith(('OCR成功.json', 'OCR失敗.json')) or f == "project-output.json"
            # 2. Target Individual Results
            is_individual = f.endswith(('_ocr_result.json', '_ocr_result.txt'))
            
            if is_session or is_individual:
                try:
                    os.remove(file_path)
                    count += 1
                except Exception as e:
                    log.warning(f"Failed to delete {f}: {e}")
        
        if count > 0:
            self.log_system(f"✅ 已清理 {count} 個舊紀錄檔案，準備全新開始。")
        else:
            self.log_system(f"⚠️ 警告: 找不到可刪除的舊紀錄，可能檔名不匹配？")

    def start_batch(self, limit: int = None, restart: bool = False, reprocess_last_n: int = 0):
        if self.is_running:
            log.warning("Batch already running.")
            return
        
        self.stop_event.clear()
        self.is_running = True
        self.stats['is_running'] = True
        
        # [v11.2] Initialize Session Files
        # Generate Session ID: yyyymmdd-hhmm
        session_id = datetime.now().strftime("%Y%m%d-%H%M")
        
        # Output paths inside the image directory
        # e.g., d:/.../商化照片-202512/20260124-1900-OCR成功.json
        self.current_success_file = os.path.join(self.image_dir, f"{session_id}-OCR成功.json")
        self.current_failed_file = os.path.join(self.image_dir, f"{session_id}-OCR失敗.json")
        
        # Reset Session Data (User requested "New Folder = New Files")
        # Unless 'restart' is True? User said "開啟新資料匣，都要重新產生檔案". 
        # If restarting the SAME batch, maybe we should append? 
        # But 'restart' arg usually means "Start Over".
        # Let's assume for now: Every 'start_batch' call is a new session if it wasn't running.
        # However, to be safe, if we are just "Continuing", we might want to append?
        # The user's prompt implies they want isolation. Let's start fresh.
        # [v11.9 Fix] Always reset session stats on new batch start
        # Previous logic was inverted/confused. New Batch = New Stats.
        self.failed_files = []
        self.run_results = []
        self.stats['success'] = 0
        self.stats['failed'] = 0
        self.stats['processed'] = 0 # [v11.91 Fix] Reset to 0, thread will update base count
        
        self.log_system(f"🆕 建立新場次: {session_id}")
        self.log_system(f"📁 成功紀錄: {os.path.basename(self.current_success_file)}")
        self.log_system(f"📁 失敗紀錄: {os.path.basename(self.current_failed_file)}")

        # Run in separate thread
        t = Thread(target=self._safe_run_loop, args=(limit, restart, reprocess_last_n))
        t.daemon = True
        t.start()

    def stop_batch(self):
        self.stop_event.set()
        self.is_running = False
        self.stats['is_running'] = False
        self.log_system("🛑 收到停止指令，正在中斷處理...")
        log.info("Batch stopped by user.")

    def get_status(self):
        """Returns current runtime status and metrics."""
        return {
            "is_running": self.is_running,
            "stats": self.stats,
            "lm_logs": self.system_logs[-100:],
            "stream_buffer": self.stream_buffer,
            "current_file": self.current_file,
            "failed_files": self.failed_files,  # [v11.0] Include failed files
            "recent_results": self.recent_results, # [v16.9 Fix] Add missing sync
            "unknown_models": sorted(list(self.unknown_models)) # [v16.10]
        }
    
    def _save_failed_files(self):
        """[v11.0] Save failed files to JSON for persistence."""
        try:
            # [v11.2] Use dynamic path
            target_file = self.current_failed_file
            with open(target_file, 'w', encoding='utf-8') as f:
                json.dump(self.failed_files, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.error(f"Failed to save failed files: {e}")

    def _safe_run_loop(self, limit: int, restart: bool, reprocess_last_n: int = 0):
        """Wrapper to catch thread crashes."""
        print(f"DEBUG: _safe_run_loop started. Limit={limit}, Restart={restart}, ReprocessLast={reprocess_last_n}")
        try:
            self._run_loop(limit, restart, reprocess_last_n)
        except Exception as e:
            import traceback
            traceback.print_exc()
            log.error(f"Batch Thread Halted: {e}")
            self.log_system(f"FATAL: Batch Thread Halted: {e}")
            self.is_running = False
            self.stats['is_running'] = False

    def get_pending_files(self):
        """[v15.0] Discovers all files and filters by history to find pending ones."""
        all_files = sorted([
            f for f in os.listdir(self.image_dir) 
            if f.lower().endswith(('.jpg', '.jpeg', '.png'))
        ])
        
        processed_success = set()
        processed_failed = set()
        
        # Scan for all *OCR成功.json files
        for existing_log in os.listdir(self.image_dir):
            if existing_log.endswith("OCR成功.json"):
                try:
                    full_log_path = os.path.join(self.image_dir, existing_log)
                    with open(full_log_path, 'r', encoding='utf-8') as f:
                        data_json = json.load(f)
                        for item in data_json:
                            img_path = item.get('data', {}).get('image', '')
                            fname = os.path.basename(img_path)
                            if fname in all_files:
                                processed_success.add(fname)
                except: pass
        
        # Scan for *OCR失敗.json
        for existing_log in os.listdir(self.image_dir):
            if existing_log.endswith("OCR失敗.json"):
                try:
                    full_log_path = os.path.join(self.image_dir, existing_log)
                    with open(full_log_path, 'r', encoding='utf-8') as f:
                        data_json = json.load(f)
                        for item in data_json:
                            fname = item.get('filename')
                            if fname in all_files and fname not in processed_success:
                                processed_failed.add(fname)
                except: pass

        # Legacy Support
        for lp in [os.path.join(self.image_dir, "project-output.json"), "project-output.json"]:
            if os.path.exists(lp):
                try:
                    with open(lp, 'r', encoding='utf-8') as f:
                        data_json = json.load(f)
                        for item in data_json:
                            img_path = item.get('data', {}).get('image', '')
                            fname = os.path.basename(img_path)
                            if fname in all_files:
                                processed_success.add(fname)
                except: pass
        
        # [v16.2 Deduplication Fix]
        # Ensure 'processed_failed' ONLY contains files that have failed and NEVER succeeded.
        # This resolves the 1317 + 44 > 1356 math impossibility.
        processed_failed = processed_failed - processed_success 
        
        # [v16.1 Revert] Count failed files as 'processed' to SKIP them.
        # This ensures we reach "All Done" state so the "Re-run Last 5" prompt can trigger.
        processed_all = processed_success | processed_failed 
        pending = [f for f in all_files if f not in processed_all]
        
        # [v9.64 DEBUG LOG - FORCE CONSOLE OUTPUT]
        try:
            # [v9.64 Log Fix] 
            # Dashboard 'Active Failures' = Unique files that failed and NEVER succeeded.
            # 'processed_failed' set here calculates exactly that (filtered by not in success).
            # If there's a mismatch (44 vs 39), it might be due to 5 files being corrupted/ignored or phantom files.
            # Let's print it clearly.
            print(f"[DEBUG] 檔案掃描報告:")
            print(f"   - 總檔案數 (Total): {len(all_files)}")
            print(f"   - 已成功 (Success): {len(processed_success)}")
            print(f"   - 尚有失敗 (Active Failed): {len(processed_failed)} (這是目前還沒成功的檔案數)")
            print(f"   - 可略過 (Processed): {len(processed_all)}")
            print(f"   - 待處理 (Pending): {len(pending)}")
            if len(pending) == 0:
                print("   => ✅ 佇列清空，將觸發「全部完成」與「重跑詢問」")
        except: pass
        
        return {
            "all_files": all_files,
            "pending_files": pending,
            "processed_success": processed_success,
            "processed_failed": processed_failed,
            "processed_all": processed_all
        }

    def _run_loop(self, limit: int, restart: bool, reprocess_last_n: int = 0):
        # Sanitize path for console logging to prevent cp950 errors
        safe_dir_name = self.image_dir.encode('ascii', 'replace').decode('ascii')
        mode_str = "RESTART" if restart else "CONTINUE"
        log.info(f"Starting batch ({mode_str}) in {safe_dir_name}")
        self.log_system(f"Batch thread started ({mode_str}). Dir: {self.image_dir}") 
        
        # [v17.08] Physical Cleanup on Restart
        # This MUST happen before get_pending_files() so the scan is clean.
        if restart:
            self._purge_records_for_restart()

        # 1. Discover & Filter
        scan_res = self.get_pending_files()
        all_files = scan_res['all_files']
        pending_files = scan_res['pending_files']
        processed_success = scan_res['processed_success']
        processed_failed = scan_res['processed_failed']
        processed_all = scan_res['processed_all']
        
        # [v11.8 UX Fix] Update Total
        self.stats['total'] = len(all_files)
        
        # [v14.9] Scoped Statistics Initialization
        self.base_success_count = len(processed_success)
        self.base_failed_count = len(processed_failed)
        self.stats['processed'] = len(processed_all)
        # [v16.21 Fix] Initialize with strict counts from disk scan to prevent drift
        self.stats['success'] = len(processed_success)
        self.stats['failed'] = len(processed_failed)  
        
        initial_processed_count = len(processed_all)

        # Apply limit if any
        if limit and limit > 0: 
            pending_files = pending_files[:limit]

        # [v15.0] Force Reprocess Last N (Interactive Mode)
        if reprocess_last_n > 0:
            try:
                # Sort by modification time to get the "last" ones
                all_files_sorted = sorted(
                    all_files, 
                    key=lambda x: os.path.getmtime(os.path.join(self.image_dir, x))
                )
                targets = all_files_sorted[-reprocess_last_n:] if len(all_files_sorted) >= reprocess_last_n else all_files_sorted
                
                if targets:
                    self.log_system(f"🔄 強制重跑最後 {len(targets)} 張圖片 (覆蓋模式)...")
                    
                    # [v16.3 Fix] Actually delete from disk so they don't reappear on restart
                    self._delete_records_from_disk(targets)
                    
                    # Deduplicate: if target is already in pending, don't duplicate
                    for t in targets:
                        if t not in pending_files:
                            pending_files.insert(0, t) # Prioritize them
                        
                        # [Critical] Adjust base counts to avoid double counting
                        if t in processed_success:
                            self.base_success_count -= 1
                            processed_all.discard(t)
                        elif t in processed_failed:
                            self.base_failed_count -= 1
                            processed_all.discard(t)
                            
                    self.stats['processed'] = len(processed_all)
                    initial_processed_count = len(processed_all)
                    
                    # [v16.2] PHYSICAL DELETION


            except Exception as e:
                log.error(f"Reprocess error: {e}")

        if len(processed_all) > 0:
            self.log_system(f"ℹ️ 已略過 {len(processed_all)} 個已處理檔案 (成功:{self.base_success_count}, 失敗:{self.base_failed_count})。")
        
        if not pending_files and not self.priority_queue:
            self.log_system("🎉 全部檔案皆已處理完畢！")
            self.is_running = False
            self.stats['is_running'] = False
            return # Exit early if nothing to do
        
        consecutive_failures = 0
        MAX_FAILURES = 5

        # 3. Create Run Manifest (Start)
        # ... (unchanged)
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = os.path.join("runs", run_id)
        os.makedirs(run_dir, exist_ok=True)
        
        manifest = {
            "run_id": run_id,
            "mode": mode_str,
            "start_time": datetime.now().isoformat(),
            "config": self.config.get('clean_config', {}), 
            "prompt_version": self.prompt_mgr.current_bundle_id
        }
        
        # [v11.2] We start clean for THIS file output, but we keep idempotency.
        self.run_results = [] 
        
        # --- Loop ---
        work_queue = list(pending_files)
        
        while (work_queue or self.retry_queue or self.priority_queue) and not self.stop_event.is_set():
            # [v11.8] Yield thread to allow Flask API to breathe during tight loops (e.g., fast failures)
            time.sleep(0.05) 
            
            # Check Circuit Breaker
            if consecutive_failures >= MAX_FAILURES:
                self.log_system(f"Meltdown: {MAX_FAILURES} consecutive failures. Stopping.")
                break
            
            # Pick File
            if self.priority_queue:
                fname = self.priority_queue.pop(0)
                is_retry = True # Treat as retry to avoid skipping stats logic issues
                self.log_system(f"⚡ [Priority] 優先插隊: {fname}")
            elif self.retry_queue:
                fname = self.retry_queue.pop(0)
                is_retry = True
            elif work_queue:
                fname = work_queue.pop(0)
                is_retry = False
            else:
                break
                
            self.current_file = fname
            self.stream_buffer = "" # Reset buffer for new file
            self.log_system("━━━━━━━━━━━━━━━")
            self.log_system(f"▶️ 載入圖片: {fname}")
            
            try:
                # A. Preprocess
                # [v16.7 Fix] Removed invalid self.VERSION check
                img_path = os.path.join(self.image_dir, fname)
                proc_res = self.img_proc.process(img_path)
                if not proc_res:
                    raise Exception("Image preprocessing failed")
                
                # B. LLM Call (Delegated)
                if not self.processor_fn:
                    raise Exception("No processor function set")
                
                start_t = time.time()
                raw_result = self.processor_fn(
                    fname=fname,
                    image_b64=proc_res['base64'], 
                    prompt_mgr=self.prompt_mgr,
                    auto_curator=None, # [v16.7 Fix] Removed undefined attribute access
                    image_processor=self.img_proc 
                )
                duration = time.time() - start_t
                
                # [v18.53 Fix] Check stop signal after LLM call completes
                if self.stop_event.is_set():
                    self.log_system("🛑 處理已中斷 (LLM 呼叫後)")
                    break
                
                # C. Post-Process (Validation & Matching)
                norm_result = self.field_norm.normalize(raw_result)
                
                # Model Match (Strict Mode)
                if norm_result.get('view_type') == '單機':
                    raw_model = norm_result.get('model', '')
                    if raw_model:
                        matched = self.model_matcher.match(raw_model)
                        if matched:
                             norm_result['model'] = matched
                        else:
                             self.log_system(f"⚠️ 型號未在標準表中匹配，保留原始值: '{raw_model}'")
                             # Keep original raw_model, don't clear!
                             norm_result['model'] = raw_model                             
                # Add Metadata

                # Add Metadata
                norm_result['file_name'] = fname
                norm_result['timestamp'] = datetime.now().isoformat()
                norm_result['duration'] = round(duration, 2)
                norm_result['run_id'] = run_id
                
                # Generate thumbnail for frontend display
                norm_result['thumb_b64'] = self.img_proc.create_thumbnail(img_path, max_size=400)

                # D. Update Stats (v16.24 Robust)
                is_success = norm_result.get('view_type') != '失敗' and norm_result.get('category') != '失敗'
                
                if is_success:
                    # If it was a failure before, remove from failure list
                    if fname in processed_failed:
                        processed_failed.discard(fname)
                        self.stats['failed'] = len(processed_failed)
                    
                    # Add to success list if new
                    if fname not in processed_success:
                        processed_success.add(fname)
                        self.stats['success'] = len(processed_success)
                    
                    consecutive_failures = 0
                else:
                    # If it's a failure and not already marked as success elsewhere 
                    # (though rerun is usually failure -> success, not success -> failure)
                    if fname not in processed_success:
                        processed_failed.add(fname)
                        self.stats['failed'] = len(processed_failed)
                    
                    consecutive_failures += 1
                
                # Total processed is strictly the sum of unique outcomes
                self.stats['processed'] = len(processed_success) + len(processed_failed)
                
                # E. Save Result
                # [v16.10] Track Unknown Models
                if '(未建檔)' in str(norm_result.get('model', '')):
                     self.unknown_models.add(norm_result['model'])

                # [v16.9 Fix] Deduplicate recent results (Remove old entry if exists)
                self.recent_results = [r for r in self.recent_results if r['file_name'] != norm_result['file_name']]
                
                self.recent_results.insert(0, norm_result)
                if len(self.recent_results) > 50: self.recent_results.pop()
                
                # Append to Run CSV for backup
                self.evaluator.generate_csv_report(
                    [norm_result], 
                    os.path.join(run_dir, "results.csv")
                )

                # [v11.2] Save to DYNAMIC Session File
                self.run_results.append(norm_result)
                self.evaluator.export_to_label_studio_json(self.run_results, self.current_success_file)

                # [v17.15 Fix] Save Thinking Process to Single Session TXT file
                if 'thinking' in norm_result and norm_result['thinking']:
                    try:
                        # Derive TXT path from the main JSON path (project-output.json -> project-output.txt)
                        session_txt_path = os.path.splitext(self.current_success_file)[0] + ".txt"
                        
                        # Append Mode
                        with open(session_txt_path, 'a', encoding='utf-8') as f:
                            f.write(f"{'='*30}\n")
                            f.write(f"File: {fname}\n")
                            f.write(f"Time: {datetime.now().strftime('%H:%M:%S')}\n")
                            f.write(f"思考：{norm_result['thinking']}\n")
                            
                            # [v17.17 UX] Simple One-Line Result (Strict Format)
                            res_line = f"結果：{norm_result.get('view_type')}/{norm_result.get('screen_status')}/{norm_result.get('quality_issue')}/{norm_result.get('model')}/{norm_result.get('price')}"
                            f.write(res_line)
                            f.write(f"\n{'='*30}\n\n")
                            
                        # self.log_system(f"📝 思考日誌已追加至: {os.path.basename(session_txt_path)}")
                    except Exception as e:
                        self.log_system(f"⚠️ 儲存思考日誌失敗: {e}")


            except Exception as e:
                import traceback
                error_msg = str(e)
                
                # [v10.9] Distinguish permanent failures from retryable errors
                is_permanent_failure = "Image preprocessing failed" in error_msg or "cannot identify image" in error_msg
                
                # [v11.0] Record failed file
                failed_record = {
                    "filename": fname,
                    "reason": "",
                    "timestamp": datetime.now().isoformat(),
                    "error_type": ""
                }
                
                if is_permanent_failure:
                    # [v11.95 Performance Logic] 
                    # Corrupted image - SILENTLY FAIL in terminal to prevent IO blocking
                    # Do NOT use log.error() here, it kills the console!
                    
                    self.log_system(f"❌ 圖片損壞，永久跳過: {fname}") 
                    self.log_system(f"   原因: 無法識別圖片格式或文件損壞")
                    log.error(f"Image Corrupted: {fname}")
                    
                    failed_record["reason"] = "圖片損壞 - 無法識別圖片格式"
                    failed_record["error_type"] = "corrupted_image"
                    consecutive_failures = 0
                else:
                    # System error - might be retryable, so we log this
                    # traceback.print_exc() # Disable traceback to keep terminal clean
                    log.warning(f"System Error on {fname}: {error_msg}")
                    # self.log_system(f"❌ 系統錯誤: {error_msg}") # [v12.0 Silence]
                    failed_record["reason"] = f"系統錯誤: {error_msg[:100]}"
                    failed_record["error_type"] = "system_error"
                    consecutive_failures += 1
                
                self.failed_files.append(failed_record)
                self._save_failed_files()  # Save immediately
                
                self.stats['failed'] += 1
                
                # [v11.8] Slow down on errors to prevent log flooding / CPU lockup
                time.sleep(0.2) # 0.2s is enough if we don't print to terminal
        
        self.is_running = False
        self.stats['is_running'] = False
        manifest["end_time"] = datetime.now().isoformat()
        manifest["stats"] = self.stats
        
        with open(os.path.join(run_dir, "manifest.json"), 'w') as f:
            json.dump(manifest, f, indent=2)
        
        # [v18.53 Fix] Show appropriate message based on stop reason
        if self.stop_event.is_set():
            self.log_system("🛑 批次處理已被用戶中斷。")
        else:
            self.log_system("✅ 批次處理已完成。")
        self.stream_buffer = "" # [v14.5 Fix] Clear on completion

    def log_system(self, msg: str, with_timestamp: bool = False):
        """Log system message. Only add timestamp if with_timestamp=True"""
        if with_timestamp:
            ts = datetime.now().strftime("%H:%M:%S")
            entry = f"[{ts}] {msg}"
        else:
            entry = msg
        self.system_logs.append(entry)
        if len(self.system_logs) > 1000: self.system_logs.pop(0)

    def get_performance_metrics(self):
        """Calculate real-time metrics for dashboard"""
        results = [r for r in self.recent_results if 'duration' in r]
        
        # 1. Avg Duration
        if results:
            avg_duration = sum(r['duration'] for r in results) / len(results)
        else:
            avg_duration = 0.0
            
        # 2. Last Duration
        last_duration = results[0]['duration'] if results else 0.0
        
        return {
            "avg_duration": round(avg_duration, 2),
            "last_duration": round(last_duration, 2),
            "total_processed": self.stats['processed']
        }

    def _append_to_global_csv(self, result: dict):
        # Legacy support
        try:
            exists = os.path.exists(self.config['output_file'])
            with open(self.config['output_file'], 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                if not exists: writer.writerow(['timestamp', 'file_name', 'category', 'model', 'price', 'black_screen', 'raw_response'])
                writer.writerow([
                    result.get('timestamp',''), result.get('file_name',''), result.get('category',''),
                    result.get('model',''), result.get('price',''), result.get('black_screen',''),
                    result.get('raw_response','').replace('\n', ' ')[:1000]
                ])
        except: pass

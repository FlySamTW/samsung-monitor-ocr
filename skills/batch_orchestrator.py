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
from skills.auto_curation import AutoCurator

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
        self.img_proc = ImageProcessor()
        self.model_matcher = ModelMatcher(config['model_list_file'])
        self.field_norm = FieldNormalizer()
        self.evaluator = Evaluator()
        self.auto_curator = AutoCurator(config['persist_file'])

        # Runtime State
        # Runtime State - Init logs FIRST
        self.stats = {"total": 0, "processed": 0, "success": 0, "failed": 0, "streak": 0, "max_streak": 0, "is_running": False}
        self.system_logs = []
        self.stream_buffer = "" # Real-time streaming buffer
        self.recent_results = []
        self.retry_queue = []

        self.is_running = False
        self.stop_event = Event() # Retained for functionality
        self.log_system("批次處理已停止。") # Added as per instruction
        self.save_data_file = None # Corrected syntax from self.save_data()_file
        self.recent_results = []
        self.retry_queue = []
        self.stream_buffer = "" # Real-time streaming buffer
        
        # Processor Function (Dependency Injection)
        self.processor_fn = None 

    def set_processor_function(self, fn: Callable):
        """Sets the function that performs the actual LLM call."""
        self.processor_fn = fn

    def start_batch(self, limit: int = None):
        if self.is_running:
            log.warning("Batch already running.")
            return
        
        self.stop_event.clear()
        self.is_running = True
        self.stats['is_running'] = True
        
        # Run in separate thread
        t = Thread(target=self._safe_run_loop, args=(limit,))
        t.daemon = True
        t.start()

    def stop_batch(self):
        self.stop_event.set()
        self.is_running = False
        self.stats['is_running'] = False
        log.info("Batch stopped by user.")

    def _safe_run_loop(self, limit: int):
        """Wrapper to catch thread crashes."""
        print(f"DEBUG: _safe_run_loop started. Limit={limit}")
        try:
            self._run_loop(limit)
        except Exception as e:
            import traceback
            traceback.print_exc()
            log.error(f"Batch Thread Halted: {e}")
            self.log_system(f"FATAL: Batch Thread Halted: {e}")
            self.is_running = False
            self.stats['is_running'] = False

    def _run_loop(self, limit: int):
        # Sanitize path for console logging to prevent cp950 errors
        safe_dir_name = self.image_dir.encode('ascii', 'replace').decode('ascii')
        log.info(f"Starting batch in {safe_dir_name}")
        self.log_system(f"Batch thread started. Dir: {self.image_dir}") # In-memory log supports unicode
        
        # 1. Discover Files (ascending order by filename)
        all_files = sorted([
            f for f in os.listdir(self.image_dir) 
            if f.lower().endswith(('.jpg', '.jpeg', '.png'))
        ])  # Default sorted() is ascending
        
        # 2. Filter Processed (Idempotency)
        # TODO: Load Run Manifest to skip fully processed files if needed
        # For now, simplistic check against current results list could be added
        
        pending_files = all_files
        if limit: pending_files = pending_files[:limit]
        
        self.stats['total'] = len(pending_files)
        # Reset stats for new run
        self.stats['processed'] = 0
        self.stats['success'] = 0
        self.stats['failed'] = 0

        consecutive_failures = 0
        MAX_FAILURES = 5

        # 3. Create Run Manifest (Start)
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = os.path.join("runs", run_id)
        os.makedirs(run_dir, exist_ok=True)
        
        manifest = {
            "run_id": run_id,
            "start_time": datetime.now().isoformat(),
            "config": self.config.get('clean_config', {}), # Mask secrets
            "prompt_version": self.prompt_mgr.current_bundle_id
        }
        
        self.run_results = [] # Track all results for this batch run (for JSON export)
        
        # --- Loop ---
        work_queue = list(pending_files)
        
        while (work_queue or self.retry_queue) and not self.stop_event.is_set():
            # Check Circuit Breaker
            if consecutive_failures >= MAX_FAILURES:
                self.log_system(f"Meltdown: {MAX_FAILURES} consecutive failures. Stopping.")
                break
            
            # Pick File
            if self.retry_queue:
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
                    auto_curator=self.auto_curator,
                    image_processor=self.img_proc 
                )
                duration = time.time() - start_t
                
                # C. Post-Process (Validation & Matching)
                norm_result = self.field_norm.normalize(raw_result)
                
                # Model Match (Strict Mode)
                if norm_result['category'] == '單機':
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

                # D. Update Stats
                if norm_result['category'] != '失敗':
                    self.stats['success'] += 1
                    consecutive_failures = 0
                else:
                    self.stats['failed'] += 1
                    consecutive_failures += 1
                
                # Streak Logic REMOVED (User Request v9.70)
                
                self.stats['processed'] += 1
                
                # E. Save Result
                self.recent_results.insert(0, norm_result)
                if len(self.recent_results) > 50: self.recent_results.pop()
                
                # Append to Run CSV
                self.evaluator.generate_csv_report(
                    [norm_result], # Append mode handled by file opening? 
                                   # Actually Evaluator needs 'append' support or we manage it here.
                                   # Let's just append to a dedicated csv for this run.
                    os.path.join(run_dir, "results.csv")
                )
                # Also append to global results for dashboard legacy support if needed
                self.evaluator.generate_csv_report([norm_result], "results.csv.tmp") # Hacky append
                self._append_to_global_csv(norm_result)

                # Export to Label Studio JSON (Requirement)
                # We export the entire 'recent_results' or accumulate?
                # Ideally we want the entire batch run results.
                # Since we don't hold ALL results in memory forever (memory risk), 
                # but 'run_dir' has results.csv. We can rely on that or just dump 'system_logs' style?
                # Actually, let's keep it simple: dump 'recent_results' to a 'latest_output.json' 
                # OR dump the current batch run to specific file.
                
                # For compliance with "project-1...json", let's overwrite a global json file "output.json"
                # using the accumulated results of THIS run.
                # However, 'self.recent_results' is capped at 50. 
                # We need a dedicated list for this run if we want full JSON export.
                # Let's add 'self.run_results' list in __init__ and _run_loop.
                self.run_results.append(norm_result)
                self.evaluator.export_to_label_studio_json(self.run_results, "project-output.json")


            except Exception as e:
                import traceback
                traceback.print_exc()
                self.log_system(f"Error processing {fname}: {e}")
                consecutive_failures += 1
                self.stats['failed'] += 1
        
        self.is_running = False
        self.stats['is_running'] = False
        manifest["end_time"] = datetime.now().isoformat()
        manifest["stats"] = self.stats
        
        with open(os.path.join(run_dir, "manifest.json"), 'w') as f:
            json.dump(manifest, f, indent=2)
            
        self.log_system("Batch run completed.")

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

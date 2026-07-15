import os
import time
import json
import csv
import logging
import hashlib
import gzip
from datetime import datetime
from pathlib import Path
from threading import Thread, Event, RLock, current_thread
from typing import List, Optional, Callable

# Import Skills
from skills.prompt_versioning import PromptManager
from skills.image_processing import ImageProcessor
from skills.model_matching import ModelMatcher
from skills.field_extraction import FieldNormalizer
from skills.evaluation import Evaluator
from skills.audit_fields import enrich_result_for_review
from skills.model_validation import is_placeholder_model, strict_known_model

log = logging.getLogger("rich")


def _append_v1945_trace(output_dir, result, review_decision, retry_reasons):
    """Append one idempotent, bounded pass trace without image payloads."""
    source = str(result.get("original_source_path") or result.get("source_path") or result.get("file_name") or "")
    source_item_id = str(result.get("source_item_id") or hashlib.sha256(source.casefold().encode("utf-8")).hexdigest())
    key = hashlib.sha256(f"{source_item_id}|{result.get('run_id','')}|{result.get('ocr_attempt','')}".encode("utf-8")).hexdigest()
    destination = Path(output_dir)
    path = destination if destination.suffix.lower() == ".jsonl" else destination / "v1945_evidence_trace.jsonl"
    existing = set()
    if path.is_file():
        try:
            for line in path.read_text(encoding="utf-8").splitlines()[-5000:]:
                if line.strip():
                    existing.add(json.loads(line).get("trace_id"))
        except Exception:
            existing = set()
    if key in existing:
        return
    parsed = {k: v for k, v in result.items() if k not in {"thumb_b64", "image_b64", "base64"}}
    entry = {
        "trace_id": key,
        "trace_version": "v19.45",
        "timestamp": datetime.now().isoformat(),
        "source_identity": source_item_id,
        "source_item_id": source_item_id,
        "source_path": str(result.get("source_path") or source),
        "original_source_path": source,
        "period": str(result.get("period") or ""),
        "audit_folder": str(result.get("audit_folder") or ""),
        "file_name": result.get("file_name"),
        "attempt": result.get("ocr_attempt"),
        "run_id": result.get("run_id"),
        "raw_output": str(result.get("raw_model_output") or "")[:12000],
        "raw_objects": [str(x)[:12000] for x in (result.get("raw_objects") or [])[:3]],
        "merge_mode": result.get("merge_mode"),
        "merge_rejected_reason": result.get("merge_rejected_reason"),
        "parsed_output": parsed,
        "normalized_evidence": result.get("normalized_evidence"),
        "guard_decision": {k: review_decision.get(k) for k in ("retry", "unresolved", "verified")},
        "retry_reason": list(retry_reasons)[:20],
        "accepted_reason": "evidence_contract_and_guard_verified" if review_decision.get("verified") else "",
        "rejected_reason": list(retry_reasons)[:20] if not review_decision.get("verified") else [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")

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
        self.img_proc = ImageProcessor({
            "max_size": None,
            "max_dimensions": config.get("max_dimensions", (2560, 1440)),
            "bottom_label_strip": config.get("bottom_label_strip", False),
            "bottom_center_zoom": config.get("bottom_center_zoom", False),
        })
        self.model_matcher = ModelMatcher(config['model_list_file'])
        self.field_norm = FieldNormalizer()
        self.evaluator = Evaluator()

        # Runtime State
        # Runtime State - Init logs FIRST
        self.stats = {"total": 0, "processed": 0, "success": 0, "failed": 0, "streak": 0, "max_streak": 0, "is_running": False}
        self.system_logs = []
        self.stream_buffer = "" # Real-time streaming buffer
        self.recent_results = []
        self.session_results = []
        self.retry_queue = []
        self.priority_queue = [] # [v16.12] Priority Queue
        self.session_processed = set() # [v19.1] Session-level deduplication to prevent "Infinite Loop" ghosts
        self.base_success_count = 0 # [v11.7] Cache for performance

        self.is_running = False
        self.stop_event = Event() # Retained for functionality
        self._state_lock = RLock()
        self._worker_thread = None
        self.active_image_dir = None
        self.current_file = None # [v9.92] Initialize to prevent AttributeError
        self.stream_file = None
        self.latest_result_file = None
        self.display_queue = [] # [v19.8 UX] Completed results waiting to be displayed
        # Keep the operator's cumulative pass counter monotonic across safe
        # backend restarts. The durable audit is authoritative; an in-memory
        # reset would make "cumulative interpretations" silently lie.
        self.presentation_sequence = self._load_presentation_sequence()
        self.log_system("批次處理已停止。") # Added as per instruction
        self.save_data_file = None 
        self.recent_results = []
        self.session_results = []
        self.retry_queue = []
        self.failed_files = [] 
        self.failed_files = [] 
        self.stream_buffer = "" 
        self.unknown_models = set() # [v16.10] Track unique unknown models 
        
        # [OCG-v2.3] Cumulative cost tracking for average cost per image
        self.total_image_cost = 0.0
        self.cost_image_count = 0
        
        # Dynamic Session Paths [v11.2]
        self.current_success_file = None # [v16.5] No default to prevent root pollution
        self.current_failed_file = None  # [v16.5] No default to prevent root pollution
        
        # Processor Function (Dependency Injection)
        self.processor_fn = None 
        self.result_review_fn = None
        self.max_auto_attempts = max(1, int(config.get("max_auto_attempts", 3)))
        self.auto_attempts = {}
        self.auto_result_history = {}
        self.source_metadata_map = {}
        
        # [v16.12] Force Rerun Queue
        self.priority_queue = [] 

    @staticmethod
    def _standardize_followme_model(model: object) -> object:
        """Keep FollowMe names consistent before saving JSON/CSV/UI state."""
        if model is None:
            return model
        text = str(model).strip()
        if not text or text.lower() == "null":
            return model

        compact = text.upper().replace("FOLLOW ME", "FOLLOWME")
        if not compact.startswith("FOLLOWME"):
            return model

        if "PRO" in compact or "43" in compact or "S43FM" in compact:
            return 'FollowMe Pro M7 43"'
        if "M5" in compact or "S32FM50" in compact or "FM501" in compact:
            return 'FollowMe M5 32"'
        return 'FollowMe M7 32"'

    def _standardize_followme_result(self, result: dict) -> dict:
        standard_model = self._standardize_followme_model(result.get("model"))
        if standard_model != result.get("model"):
            result["model"] = standard_model
        return result

    def force_rerun(self, filename: str):
        """
        [v16.12] Manually trigger re-processing of a specific file.
        1. Remove from 'processed' / 'failed' cache if exists.
        2. Delete the specific result JSON file to ensure 'overwrite' logic triggers.
        3. Add to priority queue.
        """
        self.log_system(f"🔄 收到強制重跑請求: {filename}")
        
        with self._state_lock:
            image_dir = self.active_image_dir or self.image_dir
            image_path = os.path.join(image_dir, filename)
            if not os.path.isfile(image_path):
                self.log_system(f"   ⚠️ 重跑略過：目前來源資料夾找不到照片 {filename}")
                return False

        # 1. Clean Memory State (Simple check, exact cleanup happens in loop)
        # We don't need to surgically remove from self.recent_results or stats immediately
        # because processing loop handles checking.
        
        # 2. Invalidate every historical session record for this file.  Merely
        # deleting a legacy per-image JSON allows an older session success to
        # reappear after a failed rerun.
        self._delete_records_from_disk([filename])
        
        # 3. Inject into Priority Queue
        with self._state_lock:
            self.auto_attempts.pop(filename, None)
            self.auto_result_history.pop(filename, None)
            if filename not in self.priority_queue:
                self.priority_queue.append(filename)
                self._persist_retry_state()
                self.log_system(f"   ✅ 已加入優先佇列 (目前 {len(self.priority_queue)} 筆排隊中)")
                return True
        return False

    def set_work_dir(self, target_dir: str):
        """Switch folders only when no batch thread can still read the old one."""
        target_dir = str(Path(target_dir).resolve())
        with self._state_lock:
            worker_alive = bool(self._worker_thread and self._worker_thread.is_alive())
            if self.is_running or worker_alive:
                return False, "批次仍在執行或停止中，不能切換來源資料夾"

            previous_dir = str(Path(self.image_dir).resolve()) if self.image_dir else ""
            if previous_dir != target_dir:
                # Filename-only queue entries are valid only inside their source folder.
                self.priority_queue = []
                self.retry_queue = []
                self.session_processed = set()

            self.image_dir = target_dir
            self.config['image_dir'] = target_dir
            return True, ""

    def refresh_stats(self):
        """
        [v19.6] Force refresh of statistics based on current directory.
        Called when directory changes or on-demand.
        """
        try:
            # [v19.6 Fix] Purge memory to prevent ghost records from previous folder
            self.recent_results = []
            self.session_results = []
            self.failed_files = []
            
            scan_res = self.get_pending_files()
            self.stats['total'] = len(scan_res['all_files'])
            self.stats['success'] = len(scan_res['processed_success'])
            self.stats['failed'] = len(scan_res['processed_failed'])
            self.stats['processed'] = len(scan_res['processed_all'])
            
            # Update base counts specific to this run context
            self.base_success_count = len(scan_res['processed_success'])
            self.base_failed_count = len(scan_res['processed_failed'])
            
            return self.stats
        except Exception as e:
            self.log_system(f"⚠️ Stats refresh failed: {e}")
            return self.stats


    def set_processor_function(self, fn: Callable):
        """Sets the function that performs the actual LLM call."""
        self.processor_fn = fn

    def set_result_review_function(self, fn: Callable):
        """Set the accuracy gate evaluated before any result becomes visible."""
        self.result_review_fn = fn

    def _retry_state_path(self) -> Path:
        return Path(self.image_dir) / ".ocr_retry_queue.json"

    def _persist_retry_state(self) -> None:
        if not self.image_dir or not os.path.isdir(self.image_dir):
            return
        path = self._retry_state_path()
        payload = {
            "image_dir": str(Path(self.image_dir).resolve()),
            "priority_queue": list(dict.fromkeys(self.priority_queue)),
            "retry_queue": list(dict.fromkeys(self.retry_queue)),
            "auto_attempts": self.auto_attempts,
            "auto_result_history": self.auto_result_history,
            "updated_at": datetime.now().isoformat(),
        }
        temp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp_path, path)
        except OSError as exc:
            self.log_system(f"⚠️ 儲存複核佇列失敗: {exc}")

    def _restore_retry_state(self) -> None:
        path = self._retry_state_path()
        if not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            expected = str(Path(self.image_dir).resolve())
            if str(payload.get("image_dir") or "") != expected:
                return
            existing = lambda name: os.path.isfile(os.path.join(self.image_dir, str(name)))
            self.priority_queue = [str(x) for x in payload.get("priority_queue", []) if existing(x)]
            self.retry_queue = [str(x) for x in payload.get("retry_queue", []) if existing(x)]
            self.auto_attempts = {
                str(k): int(v) for k, v in (payload.get("auto_attempts") or {}).items() if existing(k)
            }
            self.auto_result_history = {
                str(k): list(v) for k, v in (payload.get("auto_result_history") or {}).items() if existing(k)
            }
            if self.priority_queue or self.retry_queue:
                self.log_system(
                    f"♻️ 已恢復未完成複核佇列：人工 {len(self.priority_queue)}、自動 {len(self.retry_queue)}"
                )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.log_system(f"⚠️ 複核佇列無法恢復，保留檔案供檢查: {exc}")

    @staticmethod
    def _history_snapshot(result: dict, reasons: list[str]) -> dict:
        return {
            "view_type": result.get("view_type"),
            "category": result.get("category"),
            "model": result.get("model"),
            "price": result.get("price"),
            "screen_status": result.get("screen_status"),
            "quality_issue": result.get("quality_issue"),
            "complete_screen_count": result.get("complete_screen_count"),
            "unique_main": result.get("unique_main"),
            "label_ownership": result.get("label_ownership"),
            "followme_physical_evidence": result.get("followme_physical_evidence") or [],
            "thinking": str(result.get("thinking") or "")[:1200],
            "reasons": list(reasons),
        }

    @staticmethod
    def _source_item_id(source_path: object) -> str:
        """Stable identity for every pass of one source image in this batch."""
        try:
            source = str(Path(str(source_path or "")).resolve()).casefold()
        except (OSError, ValueError):
            source = str(source_path or "").casefold()
        return hashlib.sha256(source.encode("utf-8", errors="replace")).hexdigest()

    @staticmethod
    def _infer_period(*values: object) -> str:
        import re
        text = " ".join(str(value or "") for value in values)
        match = re.search(r"(?<!\d)(20\d{2}(?:0[1-9]|1[0-2]))(?!\d)", text)
        return match.group(1) if match else ""

    def _load_source_metadata_map(self, image_dir: str) -> dict[str, dict]:
        path = Path(image_dir) / ".ocr_source_map.json"
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload.get("items", payload) if isinstance(payload, dict) else {}
            if not isinstance(rows, dict):
                return {}
            return {
                str(name): dict(metadata)
                for name, metadata in rows.items()
                if isinstance(metadata, dict)
            }
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            self.log_system(f"⚠️ 來源身分對照表讀取失敗: {exc}")
            return {}

    def _source_metadata(self, filename: str, processing_path: object) -> dict:
        mapped = dict(self.source_metadata_map.get(str(filename), {}) or {})
        processing = str(Path(str(processing_path)).resolve())
        original = str(mapped.get("original_source_path") or mapped.get("source_path") or processing)
        source_item_id = str(mapped.get("source_item_id") or self._source_item_id(original))
        return {
            "source_item_id": source_item_id,
            "original_source_path": original,
            "period": str(mapped.get("period") or self._infer_period(original, processing, filename)),
            "audit_folder": str(mapped.get("audit_folder") or ""),
        }

    def _pass_metadata(self, attempt: int) -> tuple[int, str]:
        role = str(self.config.get("presentation_role") or "auto").strip().lower()
        model_id = str(self.config.get("model_id") or getattr(self, "last_model_name", "") or "")
        slow_models = ("qwen3.5", "qwen2.5", "gemma", "internvl", "minicpm", "paddleocr")
        if role == "slow_model" or (role == "auto" and any(token in model_id.lower() for token in slow_models)):
            return 4, "慢模型仲裁"
        attempt = max(1, int(attempt or 1))
        return attempt, {
            1: "初次辨識",
            2: "第二輪複核",
            3: "第三輪獨立判讀",
        }.get(attempt, f"第 {attempt} 輪複核")

    @staticmethod
    def _previous_result_summary(previous_results: list[dict]) -> dict:
        if not previous_results:
            return {}
        previous = dict(previous_results[-1] or {})
        return {
            key: previous.get(key)
            for key in (
                "view_type", "category", "model", "price", "screen_status",
                "quality_issue", "complete_screen_count", "unique_main",
                "label_ownership", "reasons",
            )
            if previous.get(key) not in (None, "", [])
        }

    def _presentation_audit_dir(self) -> Path:
        root = Path(str(self.config.get("audit_dir") or self.output_dir)).resolve()
        return root / "presentation_history"

    def _load_presentation_sequence(self) -> int:
        """Recover a monotonic durable pass count without loading images.

        A service restart can leave a later segment whose sequence restarted at
        one.  Summing the maximum of each chronological segment preserves those
        passes instead of letting the visible cumulative count move backwards.
        """
        audit_dir = self._presentation_audit_dir()
        if not audit_dir.is_dir():
            return 0
        completed_segments = 0
        segment_highest = 0
        previous_sequence = 0
        paths = sorted(
            audit_dir.glob("presentation_*.jsonl*"),
            key=lambda path: path.stat().st_mtime,
        )
        for path in paths:
            try:
                opener = gzip.open if path.suffix.lower() == ".gz" else open
                with opener(path, "rt", encoding="utf-8") as handle:
                    for line in handle:
                        if '"presentation_sequence"' not in line:
                            continue
                        try:
                            item = json.loads(line)
                            sequence = int(item.get("presentation_sequence") or 0)
                            if sequence <= 0:
                                continue
                            if previous_sequence and sequence < previous_sequence:
                                completed_segments += segment_highest
                                segment_highest = 0
                            segment_highest = max(segment_highest, sequence)
                            previous_sequence = sequence
                        except (TypeError, ValueError, json.JSONDecodeError):
                            continue
            except (OSError, UnicodeError):
                continue
        return completed_segments + segment_highest

    def _rotate_presentation_audit(self, path: Path) -> None:
        max_bytes = int(self.config.get("presentation_audit_max_bytes", 64 * 1024 * 1024))
        try:
            if not path.is_file() or path.stat().st_size < max_bytes:
                return
            stamp = datetime.now().strftime("%H%M%S")
            rotated = path.with_name(f"{path.stem}_{stamp}.jsonl.gz")
            with path.open("rb") as source, gzip.open(rotated, "wb", compresslevel=6) as target:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    target.write(chunk)
            path.unlink()
        except OSError as exc:
            self.log_system(f"⚠️ AI 判讀稽核輪替失敗: {exc}")

    def _append_presentation_audit(self, event: dict) -> None:
        audit_dir = self._presentation_audit_dir()
        audit_dir.mkdir(parents=True, exist_ok=True)
        path = audit_dir / f"presentation_{datetime.now().strftime('%Y%m%d')}.jsonl"
        self._rotate_presentation_audit(path)
        durable = {
            key: value for key, value in event.items()
            if key not in {"thumb_b64", "image_b64", "base64"}
        }
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(durable, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()

    def queue_presentation_event(
        self,
        *,
        result: dict,
        attempt: int,
        started_at: str,
        completed_at: str,
        previous_results: list[dict],
        retry_reasons: list[str],
        decision: str,
        narration: str,
        thumbnail: str = "",
    ) -> dict:
        """Queue and persist exactly one immutable event for every OCR pass."""
        generic_prefix = "這張已完成辨識："
        raw_output = str(result.get("raw_model_output") or "").strip()
        raw_narration = raw_output.split("\n\n{", 1)[0].strip() if raw_output else ""
        narration_candidates = (
            str(result.get("thinking") or "").strip(),
            str(narration or "").strip(),
            raw_narration,
        )
        detailed_narration = next(
            (text for text in narration_candidates if text and not text.startswith(generic_prefix)),
            next((text for text in narration_candidates if text), ""),
        )
        structured_keys = (
            "view_type", "category", "screen_status", "quality_issue", "model", "price",
            "price_status", "price_symbol", "official_price", "price_diff_percent",
            "complete_screen_count", "unique_main", "label_ownership",
            "followme_physical_evidence", "normalized_evidence",
            "evidence_contract_version", "evidence_contract_valid", "evidence_contract_errors",
            "auto_verified", "auto_review_required", "review_status",
        )
        structured = {key: result.get(key) for key in structured_keys if key in result}
        processing_source_path = str(result.get("source_path") or "")
        source_path = str(result.get("original_source_path") or processing_source_path)
        source_item_id = str(
            result.get("source_item_id")
            or self._source_item_id(source_path or result.get("file_name"))
        )
        pass_index, pass_label = self._pass_metadata(attempt)
        with self._state_lock:
            self.presentation_sequence += 1
            sequence = self.presentation_sequence
            identity_seed = f"{source_item_id}|{result.get('run_id', '')}|{attempt}|{started_at}|{sequence}"
            presentation_id = "p-" + hashlib.sha256(identity_seed.encode("utf-8")).hexdigest()[:24]
            event = {
                "presentation_id": presentation_id,
                "presentation_sequence": sequence,
                "source_item_id": source_item_id,
                "file_name": result.get("file_name", ""),
                "source_path": source_path,
                "thumb_b64": thumbnail or result.get("thumb_b64", ""),
                "pass_index": pass_index,
                "pass_label": pass_label,
                "ocr_attempt": max(1, int(attempt or 1)),
                "retry_reason": list(dict.fromkeys(str(x) for x in retry_reasons if str(x).strip())),
                "model_id": str(self.config.get("model_id") or getattr(self, "last_model_name", "") or ""),
                "accuracy_profile": str(self.config.get("accuracy_profile") or "strict"),
                "evidence_contract_version": str(result.get("evidence_contract_version") or ""),
                "started_at": started_at,
                "completed_at": completed_at,
                "previous_result_summary": self._previous_result_summary(previous_results),
                "full_ai_narration": detailed_narration,
                "narration": detailed_narration,
                "stream_buffer": detailed_narration,
                "structured_result": structured,
                "decision": str(decision or ""),
                "result": {
                    key: result.get(key)
                    for key in (
                        "view_type", "screen_status", "quality_issue", "model", "price",
                        "category", "price_symbol", "price_status", "official_price",
                        "price_diff_percent", "auto_review_required", "review_status",
                    )
                },
            }
            self.display_queue.append(event)
            if len(self.display_queue) > 200:
                self.display_queue.pop(0)
            self._append_presentation_audit(event)
        return event

    @staticmethod
    def _public_presentation_event(item: dict) -> dict:
        safe = {
            key: value for key, value in dict(item or {}).items()
            if key not in {"thumb_b64", "image_b64", "base64", "raw_model_output", "raw_objects"}
        }
        safe["structured_result"] = {
            key: value for key, value in dict(safe.get("structured_result") or {}).items()
            if key not in {"thumb_b64", "image_b64", "base64", "raw_model_output", "raw_objects"}
        }
        return safe

    def get_presentation_history(self, source_item_id: str, limit: int = 12) -> list[dict]:
        """Read one photo's pass history on demand without retaining all jobs in RAM."""
        wanted = str(source_item_id or "").strip()
        if not wanted:
            return []
        limit = max(1, min(50, int(limit or 12)))
        found: dict[str, dict] = {}
        with self._state_lock:
            live_items = list(self.display_queue)
        for item in reversed(live_items):
            if item.get("source_item_id") == wanted:
                found[str(item.get("presentation_id"))] = self._public_presentation_event(item)
        audit_dir = self._presentation_audit_dir()
        if audit_dir.is_dir() and len(found) < limit:
            paths = sorted(audit_dir.glob("presentation_*.jsonl*"), key=lambda p: p.stat().st_mtime, reverse=True)
            for path in paths:
                try:
                    opener = gzip.open if path.suffix.lower() == ".gz" else open
                    with opener(path, "rt", encoding="utf-8") as handle:
                        for line in handle:
                            if wanted not in line:
                                continue
                            item = json.loads(line)
                            if item.get("source_item_id") == wanted:
                                found[str(item.get("presentation_id"))] = self._public_presentation_event(item)
                except (OSError, UnicodeError, json.JSONDecodeError):
                    continue
                if len(found) >= limit:
                    break
        return sorted(
            found.values(),
            key=lambda item: (int(item.get("presentation_sequence") or 0), str(item.get("started_at") or "")),
        )[-limit:]

    def get_recent_presentation_history(self, limit: int = 200) -> list[dict]:
        """Return newest durable presentation events for result-rail recovery."""
        limit = max(1, min(200, int(limit or 200)))
        found: dict[str, dict] = {}
        with self._state_lock:
            live_items = list(self.display_queue)
        for item in reversed(live_items):
            presentation_id = str(item.get("presentation_id") or "")
            if presentation_id:
                found[presentation_id] = self._public_presentation_event(item)

        audit_dir = self._presentation_audit_dir()
        if audit_dir.is_dir() and len(found) < limit:
            paths = sorted(audit_dir.glob("presentation_*.jsonl*"), key=lambda p: p.stat().st_mtime, reverse=True)
            for path in paths:
                try:
                    if path.suffix.lower() == ".gz":
                        with gzip.open(path, "rt", encoding="utf-8") as handle:
                            lines = list(handle)[-(limit * 4):]
                    else:
                        with path.open("rb") as handle:
                            size = handle.seek(0, os.SEEK_END)
                            handle.seek(max(0, size - (4 * 1024 * 1024)))
                            if handle.tell() > 0:
                                handle.readline()
                            lines = handle.read().decode("utf-8", errors="ignore").splitlines()
                    for line in reversed(lines):
                        try:
                            item = json.loads(line)
                        except (TypeError, json.JSONDecodeError):
                            continue
                        presentation_id = str(item.get("presentation_id") or "")
                        if presentation_id and presentation_id not in found:
                            found[presentation_id] = self._public_presentation_event(item)
                        if len(found) >= limit:
                            break
                except (OSError, UnicodeError):
                    continue
                if len(found) >= limit:
                    break

        return sorted(
            found.values(),
            key=lambda item: (
                str(item.get("completed_at") or item.get("started_at") or ""),
                str(item.get("presentation_id") or ""),
            ),
            reverse=True,
        )[:limit]

    def get_all_records(self):
        """
        [v14.9] Scoped to current folder. Aggregates records for files that actually EXIST.
        """
        all_records_map = {}
        # Get actual files in current dir
        try:
            actual_files = set(f for f in os.listdir(self.image_dir) if f.lower().endswith(('.jpg', '.jpeg', '.png')))
            # self.log_system(f"Found {len(actual_files)} actual image files in {self.image_dir}")
        except Exception as e:
            self.log_system(f"Failed to list actual files: {e}")
            actual_files = set()

        # 1. Load Legacy Global File (Scoped to current dir ONLY)
        legacy_files = [os.path.join(self.image_dir, "project-output.json")]
        for lf in legacy_files:
            if os.path.exists(lf):
                self._load_json_to_map(lf, all_records_map)
        
        # 2. Load Session Files
        if os.path.exists(self.image_dir):
            # [v12.0 Fix] Scan ALL existing JSONs to rebuild memory
            # [v19.1 Fix] Sort by modification time (Oldest -> Newest) to ensure latest updates win
            try:
                # [v19.4 Logic] Robust sorting with error handling for race conditions
                all_files = []
                for f in os.listdir(self.image_dir):
                    try:
                        # 嘗試獲取 mtime，如果失敗 (如檔案剛被刪除) 則忽略
                        mtime = os.path.getmtime(os.path.join(self.image_dir, f))
                        all_files.append((f, mtime))
                    except (FileNotFoundError, PermissionError):
                        continue
                
                # Sort by time
                all_files.sort(key=lambda x: x[1])
                
                for fname, _ in all_files:
                    if fname.endswith("OCR成功.json"):
                        fpath = os.path.join(self.image_dir, fname)
                        before_count = len(all_records_map)
                        self._load_json_to_map(fpath, all_records_map)
                        after_count = len(all_records_map)
                        diff = after_count - before_count
                        if diff != 0:
                            pass
                            # self.log_system(f"Loaded {fname}: {diff} records (Total: {after_count})")
            except Exception as e:
                pass
                # self.log_system(f"⚠️ Failed to sort/load historical files: {e}")

        # 3. Filter by existing files and prioritize Memory
        record_map = {}
        for f in actual_files:
            if f in all_records_map:
                record_map[f] = all_records_map[f]
        
        # Overlay Memory
        for res in self.recent_results:
            if res['file_name'] in actual_files:
                record_map[res['file_name']] = res
        for res in self.session_results:
            if res['file_name'] in actual_files:
                record_map[res['file_name']] = res
        
        # 3. Filter by actual files in current dir
        # Only return records for files that actually exist in the image_dir
        final_records = []
        actual_files_lower = set()
        try:
            # [v19.7 Fix] Case-Insensitive Matching for Windows
            # Store lower-case filenames for existence check
            actual_files_lower = set(f.lower() for f in os.listdir(self.image_dir) if f.lower().endswith(('.jpg', '.jpeg', '.png')))
            
            with open("debug_orchestrator.log", "a", encoding="utf-8") as debug_log:
                debug_log.write(f"[{datetime.now()}] Found {len(actual_files_lower)} actual image files (case-insensitive) in {self.image_dir}\n")
        except Exception as e:
            self.log_system(f"Failed to list actual files: {e}")
            with open("debug_orchestrator.log", "a", encoding="utf-8") as debug_log:
                debug_log.write(f"[{datetime.now()}] Failed to list actual files: {e}\n")

        for filename, record in record_map.items():
            # Check if filename.lower() exists in actual_files_lower
            if filename.lower() in actual_files_lower:
                final_records.append(record)
            else:
                # [DEBUG] Log missing files (first 5)
                if len(final_records) == 0 and len(record_map) > 0:
                     with open("debug_orchestrator.log", "a", encoding="utf-8") as debug_log:
                        debug_log.write(f"[{datetime.now()}] Skipped missing file: {filename}\n")
        
        with open("debug_orchestrator.log", "a", encoding="utf-8") as debug_log:
            debug_log.write(f"[{datetime.now()}] Final records count after filtering: {len(final_records)}\n")

        return final_records

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
                # [v18.99 Fix] Handle empty or corrupted files
                content = f.read().strip()
                if not content: 
                    self.log_system(f"⚠️ Warning: {filepath} is empty.")
                    return
                try:
                    data = json.loads(content)
                except json.JSONDecodeError as je:
                    self.log_system(f"❌ JSON Error in {filepath}: {je.msg} at line {je.lineno} col {je.colno}")
                    return
                
                for item in data:
                    # Parse LS JSON
                    try:
                        img_path = item.get('data', {}).get('image', '')
                        filename = os.path.basename(img_path)
                        if not filename: continue
                        
                        # Initial default values
                        meta = item.get('data', {}).get('ocr_meta', {}) or item.get('ocr_meta', {}) or {}
                        res = {
                            "file_name": filename,
                            "view_type": meta.get("view_type") or "單機",        # Default View
                            "screen_status": meta.get("screen_status") or "",      # Default Empty
                            "quality_issue": meta.get("quality_issue") or "",      # Default Empty
                            "note": "",               # Default Empty
                            "model": "",
                            "price": "",
                            "price_status": meta.get("price_status") or "",
                            "price_symbol": meta.get("price_symbol") or "",
                            "official_price": meta.get("official_price") or "",
                            "price_diff_percent": meta.get("price_diff_percent") or "",
                            "auto_verified": meta.get("auto_verified", False),
                            "auto_review_required": meta.get("auto_review_required", False),
                            "review_status": meta.get("review_status") or "",
                            "evidence_contract_version": meta.get("evidence_contract_version") or "",
                            "evidence_contract_valid": meta.get("evidence_contract_valid", False),
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
            
            # Extract old model for mistake tracking
            old_model = ""
            for field in res_list:
                if field.get('from_name') == 'model':
                    val = field.get('value', {}).get('text', [])
                    if val: old_model = val[0]
                    break
            
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
            
            # [Phase 2] Save to Mistake Book if model was corrected
            if 'model' in updates and old_model and updates['model'] and str(old_model).upper() != str(updates['model']).upper():
                mistake_file = os.path.join(self.image_dir, "mistake_book.json")
                mistakes = []
                if os.path.exists(mistake_file):
                    try:
                        with open(mistake_file, 'r', encoding='utf-8') as mf:
                            mistakes = json.load(mf)
                    except: pass
                
                new_mistake = {"wrong": str(old_model), "correct": str(updates['model'])}
                if new_mistake not in mistakes:
                    mistakes.append(new_mistake)
                    try:
                        with open(mistake_file, 'w', encoding='utf-8') as mf:
                            json.dump(mistakes, mf, ensure_ascii=False, indent=2)
                        self.log_system(f"🧠 已將糾錯經驗學習至錯題本: {old_model} -> {updates['model']}")
                    except Exception as e:
                        log.error(f"Failed to save mistake book: {e}")
            
            # [Stage 2] Generate SFT Dataset JSONL (ShareGPT Format)
            try:
                dataset_file = os.path.join(self.image_dir, "human_verified_dataset.jsonl")
                img_path_for_training = os.path.abspath(os.path.join(self.image_dir, filename))
                
                # Reconstruct full corrected JSON
                # Attempt to get existing metadata to preserve fields not in updates
                current_view = updates.get('view_type', '單機')
                current_status = updates.get('screen_status', '正常')
                current_quality = updates.get('quality_issue', '無')
                
                for field in res_list:
                    if field.get('from_name') == 'category':
                        raw_cat = field.get('value', {}).get('choices', [''])[0]
                        if "遠景" in raw_cat: current_view = "遠景"
                        if "黑屏" in raw_cat: current_status = "黑屏"
                        if "看不清楚" in raw_cat: current_quality = "照不清楚"
                
                correct_json = {
                    "view_type": updates.get('view_type', current_view),
                    "screen_status": updates.get('screen_status', current_status),
                    "quality_issue": updates.get('quality_issue', current_quality),
                    "model": updates.get('model', ''),
                    "price": updates.get('price', '')
                }
                
                sft_record = {
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "image", "image": img_path_for_training.replace('\\', '/')},
                                {"type": "text", "text": f"「這是一張全新的照片，與之前的任何辨識無關。」\n圖片: {filename}\n請提取此照片中的資訊，並確認型號與價格位於同一張實體標籤上。"}
                            ]
                        },
                        {
                            "role": "assistant",
                            "content": json.dumps(correct_json, ensure_ascii=False)
                        }
                    ]
                }
                
                with open(dataset_file, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(sft_record, ensure_ascii=False) + '\n')
                
                self.log_system(f"📈 [Stage 2] 已產生 ShareGPT 訓練數據，累積至: human_verified_dataset.jsonl")
            except Exception as e:
                log.error(f"Failed to generate SFT dataset: {e}")
                
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

    def start_batch(
        self,
        limit: int = None,
        restart: bool = False,
        reprocess_last_n: int = 0,
        image_dir: str = None,
    ):
        with self._state_lock:
            worker_alive = bool(self._worker_thread and self._worker_thread.is_alive())
            if self.is_running or worker_alive:
                log.warning("Batch already running or stopping.")
                return False

            if image_dir:
                target_dir = str(Path(image_dir).resolve())
                previous_dir = str(Path(self.image_dir).resolve()) if self.image_dir else ""
                if previous_dir != target_dir:
                    self.priority_queue = []
                    self.retry_queue = []
                    self.auto_attempts = {}
                    self.auto_result_history = {}
                    self.session_processed = set()
                self.image_dir = target_dir
                self.config['image_dir'] = target_dir

            batch_image_dir = str(Path(self.image_dir).resolve())
            self.active_image_dir = batch_image_dir
            self.source_metadata_map = self._load_source_metadata_map(batch_image_dir)
            self.stop_event.clear()
            self.is_running = True
            self.stats['is_running'] = True
            self.session_processed = set()
            self._restore_retry_state()

        # Presentation data belongs to one batch only.  Keeping rows from a
        # staging rerun makes the dashboard replay deleted files in the next
        # folder and is the root cause of photo/AI/card desynchronization.
        self.display_queue = []
        self.stream_buffer = ""
        self.stream_file = None
        self.latest_result_file = None
        
        # [v11.2] Initialize Session Files
        # Generate Session ID: yyyymmdd-hhmm
        session_id = datetime.now().strftime("%Y%m%d-%H%M")
        
        # Output paths inside the image directory
        # e.g., d:/.../商化照片-202512/20260124-1900-OCR成功.json
        self.current_success_file = os.path.join(batch_image_dir, f"{session_id}-OCR成功.json")
        self.current_failed_file = os.path.join(batch_image_dir, f"{session_id}-OCR失敗.json")
        
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
        self.recent_results = []
        self.session_results = []
        self.stats['success'] = 0
        self.stats['failed'] = 0
        self.stats['processed'] = 0 # [v11.91 Fix] Reset to 0, thread will update base count
        
        self.log_system(f"🆕 建立新場次: {session_id}")
        self.log_system(f"📁 成功紀錄: {os.path.basename(self.current_success_file)}")
        self.log_system(f"📁 失敗紀錄: {os.path.basename(self.current_failed_file)}")

        # Run in separate thread
        t = Thread(target=self._safe_run_loop, args=(limit, restart, reprocess_last_n, batch_image_dir))
        t.daemon = True
        with self._state_lock:
            self._worker_thread = t
        t.start()
        return True

    def stop_batch(self):
        with self._state_lock:
            worker_alive = bool(self._worker_thread and self._worker_thread.is_alive())
            self.stop_event.set()
            if not worker_alive and not self.is_running:
                self.is_running = False
                self.stats['is_running'] = False
                self.active_image_dir = None
            self.stream_buffer = ""
            self.stream_file = None
            self.log_system("🛑 收到停止指令，正在中斷處理...")
            log.info("Batch stop requested.")

    def get_status(self):
        """Returns current runtime status and metrics."""
        return {
            "is_running": self.is_running,
            "stats": self.stats,
            "lm_logs": self.system_logs[-100:],
            "stream_buffer": self.stream_buffer,
            "current_file": self.current_file,
            "stream_file": self.stream_file,
            "latest_result_file": self.latest_result_file,
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

    def _safe_run_loop(self, limit: int, restart: bool, reprocess_last_n: int = 0, batch_image_dir: str = None):
        """Wrapper to catch thread crashes."""
        print(f"DEBUG: _safe_run_loop started. Limit={limit}, Restart={restart}, ReprocessLast={reprocess_last_n}")
        try:
            self._run_loop(limit, restart, reprocess_last_n, batch_image_dir=batch_image_dir)
        except Exception as e:
            import traceback
            traceback.print_exc()
            log.error(f"Batch Thread Halted: {e}")
            self.log_system(f"FATAL: Batch Thread Halted: {e}")
        finally:
            with self._state_lock:
                self.is_running = False
                self.stats['is_running'] = False
                self.active_image_dir = None
                if self._worker_thread is current_thread():
                    self._worker_thread = None

    def get_pending_files(self, image_dir: str = None):
        """[v15.0] Discovers all files and filters by history to find pending ones."""
        image_dir = image_dir or self.image_dir
        all_files = sorted([
            f for f in os.listdir(image_dir)
            if f.lower().endswith(('.jpg', '.jpeg', '.png'))
        ])
        
        processed_success = set()
        processed_failed = set()
        
        # Scan for all *OCR成功.json files
        for existing_log in os.listdir(image_dir):
            if existing_log.endswith("OCR成功.json"):
                try:
                    full_log_path = os.path.join(image_dir, existing_log)
                    with open(full_log_path, 'r', encoding='utf-8') as f:
                        data_json = json.load(f)
                        for item in data_json:
                            img_path = item.get('data', {}).get('image', '')
                            fname = os.path.basename(img_path)
                            if fname in all_files:
                                processed_success.add(fname)
                except: pass
        
        # Scan for *OCR失敗.json
        for existing_log in os.listdir(image_dir):
            if existing_log.endswith("OCR失敗.json"):
                try:
                    full_log_path = os.path.join(image_dir, existing_log)
                    with open(full_log_path, 'r', encoding='utf-8') as f:
                        data_json = json.load(f)
                        for item in data_json:
                            fname = item.get('filename')
                            if fname in all_files and fname not in processed_success:
                                processed_failed.add(fname)
                except: pass

        # Legacy Support (Scoped to current dir ONLY)
        for lp in [os.path.join(image_dir, "project-output.json")]:
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

    def _run_loop(self, limit: int, restart: bool, reprocess_last_n: int = 0, batch_image_dir: str = None):
        batch_image_dir = batch_image_dir or str(Path(self.image_dir).resolve())
        # Sanitize path for console logging to prevent cp950 errors
        safe_dir_name = batch_image_dir.encode('ascii', 'replace').decode('ascii')
        mode_str = "RESTART" if restart else "CONTINUE"
        log.info(f"Starting batch ({mode_str}) in {safe_dir_name}")
        self.log_system(f"Batch thread started ({mode_str}). Dir: {batch_image_dir}")
        
        # [v17.08] Physical Cleanup on Restart
        # This MUST happen before get_pending_files() so the scan is clean.
        if restart:
            self._purge_records_for_restart()

        # 1. Discover & Filter
        scan_res = self.get_pending_files(batch_image_dir)
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
                    key=lambda x: os.path.getmtime(os.path.join(batch_image_dir, x))
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
        self.recent_results = [] 
        self.session_results = []
        
        # --- Loop ---
        work_queue = list(pending_files)
        
        while (work_queue or self.retry_queue or self.priority_queue) and not self.stop_event.is_set():
            # [v11.8] Yield thread to allow Flask API to breathe during tight loops (e.g., fast failures)
            time.sleep(0.05) 
            
            # Check Circuit Breaker
            if consecutive_failures >= MAX_FAILURES:
                self.log_system(f"Meltdown: {MAX_FAILURES} consecutive failures. Stopping.")
                break
            
            # [v19.1 Fix] Session-level Deduplication (The Ultimate Ghost Buster)
            # If this file was already processed IN THIS SESSION (and we are not forcing a specific retry logic that clears this),
            # skip it. This prevents the "Triple Trigger" where:
            # 1. Priority Queue adds it
            # 2. File system scan sees it as "pending" (because JSON was deleted) and adds to Work Queue
            # 3. Work Queue processes it AGAIN
            
            # Pick File
            fname = None
            is_priority = False
            
            if self.priority_queue:
                fname = self.priority_queue.pop(0)
                is_retry = True 
                is_priority = True # Mark as priority
                self.log_system(f"⚡ [Priority] 優先插隊: {fname}")
                
            elif self.retry_queue:
                fname = self.retry_queue.pop(0)
                is_retry = True
            elif work_queue:
                fname = work_queue.pop(0)
                is_retry = False
            else:
                break
                
            # [v19.1] Global Deduplication Check
            if fname in self.session_processed and not is_priority:
                 # If it's NOT a priority item (user requested), but it IS in session_processed,
                 # it implies a Work Queue duplicate. SKIP IT.
                 # Priority items are allowed to re-run (that's the point).
                 self.log_system(f"⏩ [Dedup] 已在本次會話處理過，略過重複執行: {fname}")
                 continue

            # [v19.01 Fix] Deduplicate: Remove from work_queue if it's there
            if is_priority and fname in work_queue:
                try:
                    work_queue.remove(fname)
                except ValueError:
                    pass

            attempt_number = int(self.auto_attempts.get(fname, 0)) + 1
            self.auto_attempts[fname] = attempt_number
            previous_results = list(self.auto_result_history.get(fname, []))

            self.current_file = fname
            self.stream_buffer = "" # Reset buffer for new file
            self.stream_file = fname
            self.log_system("━━━━━━━━━━━━━━━")
            self.log_system(f"▶️ 載入圖片: {fname}")
            pass_started_at = datetime.now().isoformat()
            img_path = os.path.join(batch_image_dir, fname)

            try:
                # A. Preprocess
                # [v16.7 Fix] Removed invalid self.VERSION check
                if not os.path.isfile(img_path):
                    raise FileNotFoundError(f"Source image disappeared during batch: {img_path}")
                # Accuracy-first invariant: production OCR never downscales to
                # the legacy 1280px profile or disables label assistance.
                self.img_proc.config["max_size"] = None
                self.img_proc.config["max_dimensions"] = self.config.get("max_dimensions", (2560, 1440))
                self.img_proc.config["detect_label_card"] = True
                self.img_proc.config["bottom_label_strip"] = bool(
                    self.config.get("bottom_label_strip", False) or attempt_number >= 2
                )
                self.img_proc.config["bottom_center_zoom"] = bool(
                    self.config.get("bottom_center_zoom", False) or attempt_number >= 3
                )
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
                    image_processor=self.img_proc,
                    processed_image=proc_res,
                    ocr_attempt=attempt_number,
                    previous_results=previous_results,
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
                    if raw_model and not str(raw_model).upper().startswith("FOLLOWME"):
                        matched = strict_known_model(raw_model, self.model_matcher.valid_models)
                        if matched:
                            norm_result['model'] = matched
                        else:
                            self.log_system(f"⚠️ 型號未通過標準表精確驗證，已清除並列入複核: '{raw_model}'")
                            norm_result['model'] = None
                            norm_result['model_validation_failed'] = True
                            norm_result['rejected_model'] = str(raw_model)
                norm_result = self._standardize_followme_result(norm_result)
                # Add Metadata

                # Add Metadata
                norm_result['file_name'] = fname
                norm_result['source_path'] = str(Path(img_path).resolve())
                norm_result.update(self._source_metadata(fname, img_path))
                norm_result['timestamp'] = datetime.now().isoformat()
                norm_result['duration'] = round(duration, 2)
                norm_result['run_id'] = run_id
                norm_result['model_id'] = str(self.config.get("model_id") or getattr(self, "last_model_name", "") or "")
                norm_result['started_at'] = pass_started_at
                norm_result = enrich_result_for_review(norm_result)
                norm_result['ocr_attempt'] = attempt_number

                review_decision = {"retry": False, "reasons": [], "unresolved": False}
                if self.result_review_fn:
                    review_decision = self.result_review_fn(
                        norm_result,
                        attempt_number,
                        previous_results,
                        self.max_auto_attempts,
                    ) or review_decision
                retry_reasons = [str(x) for x in review_decision.get("reasons", []) if str(x).strip()]
                _append_v1945_trace(
                    self.config.get("evidence_trace_path") or self.output_dir,
                    norm_result,
                    review_decision,
                    retry_reasons,
                )
                norm_result['auto_retry_reasons'] = "；".join(dict.fromkeys(retry_reasons))
                norm_result['auto_verified'] = bool(review_decision.get("verified"))
                pass_completed_at = datetime.now().isoformat()
                norm_result['completed_at'] = pass_completed_at
                norm_result['thumb_b64'] = self.img_proc.create_thumbnail(img_path, max_size=400)
                thinking_text = str(norm_result.get("thinking") or "")
                display_text = thinking_text if thinking_text else str(self.stream_buffer or "")
                if not display_text.strip():
                    display_text = (
                        f"這張已完成辨識：{norm_result.get('view_type') or '單機'}，"
                        f"{norm_result.get('model') or '無型號'}，"
                        f"{norm_result.get('price') or '無價格'}。"
                    )

                if review_decision.get("retry") and attempt_number < self.max_auto_attempts:
                    self.queue_presentation_event(
                        result=norm_result,
                        attempt=attempt_number,
                        started_at=pass_started_at,
                        completed_at=pass_completed_at,
                        previous_results=previous_results,
                        retry_reasons=retry_reasons,
                        decision="retry_scheduled",
                        narration=display_text,
                        thumbnail=norm_result.get("thumb_b64", ""),
                    )
                    history = self.auto_result_history.setdefault(fname, [])
                    history.append(self._history_snapshot(norm_result, retry_reasons))
                    if fname not in self.retry_queue:
                        # The questionable photo occupies the very next queue
                        # slot; ordinary later photos cannot overtake it.
                        self.retry_queue.insert(0, fname)
                    self._persist_retry_state()
                    self.stream_buffer = (
                        f"第 {attempt_number} 輪仍有疑慮，已立即進入第 {attempt_number + 1} 輪獨立複核。"
                    )
                    self.log_system(
                        f"🔁 [Accuracy Gate] {fname} 立即插入下一格做第 {attempt_number + 1} 輪："
                        f"{'；'.join(retry_reasons) or '結果需再次確認'}"
                    )
                    # Intermediate guesses must never enter statistics, disk
                    # success files, the UI result cards, or upload manifests.
                    continue

                if review_decision.get("unresolved"):
                    norm_result['auto_review_required'] = True
                    norm_result['review_status'] = "需慢模型或人工校正"
                    norm_result['rerun_priority'] = "P1"
                    norm_result['rerun_reason'] = norm_result['auto_retry_reasons'] or "三輪後仍有疑慮"
                else:
                    norm_result['auto_review_required'] = False

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

                self.session_processed.add(fname)
                
                # Total processed is strictly the sum of unique outcomes
                self.stats['processed'] = len(processed_success) + len(processed_failed)
                
                # E. Save Result
                # [v16.10] Track Unknown Models
                if '(未建檔)' in str(norm_result.get('model', '')):
                     self.unknown_models.add(norm_result['model'])

                # [v16.9 Fix] Deduplicate recent results (Remove old entry if exists)
                self.recent_results = [r for r in self.recent_results if r['file_name'] != norm_result['file_name']]
                self.session_results = [r for r in self.session_results if r['file_name'] != norm_result['file_name']]
                
                self.recent_results.insert(0, norm_result)
                self.latest_result_file = norm_result['file_name']
                if len(self.recent_results) > 50: self.recent_results.pop()
                self.session_results.insert(0, norm_result)
                
                # Append to Run CSV for backup
                self.evaluator.generate_csv_report(
                    [norm_result], 
                    os.path.join(run_dir, "results.csv"),
                    append=True
                )

                # [v11.2] Save to DYNAMIC Session File
                # UI recent_results is capped; session_results preserves the full batch for rename/export.
                self.evaluator.export_to_label_studio_json(self.session_results, self.current_success_file)

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

                try:
                    final_decision = "review_required" if review_decision.get("unresolved") else "accepted"
                    self.queue_presentation_event(
                        result=norm_result,
                        attempt=attempt_number,
                        started_at=pass_started_at,
                        completed_at=pass_completed_at,
                        previous_results=previous_results,
                        retry_reasons=retry_reasons,
                        decision=final_decision,
                        narration=display_text,
                        thumbnail=norm_result.get("thumb_b64", ""),
                    )
                    self.auto_attempts.pop(fname, None)
                    self.auto_result_history.pop(fname, None)
                    self._persist_retry_state()
                except Exception as e:
                    self.log_system(f"⚠️ 佇列完成結果失敗: {e}")


            except Exception as e:
                import traceback
                error_msg = str(e)
                
                # [v10.9] Distinguish permanent failures from retryable errors
                is_missing_runtime = isinstance(e, FileNotFoundError)
                is_permanent_failure = not is_missing_runtime and (
                    "Image preprocessing failed" in error_msg or "cannot identify image" in error_msg
                )

                if not is_permanent_failure and not is_missing_runtime and attempt_number < self.max_auto_attempts:
                    error_completed_at = datetime.now().isoformat()
                    error_result = {
                        "file_name": fname,
                        "source_path": str(Path(img_path).resolve()),
                        "run_id": run_id,
                        "model_id": str(self.config.get("model_id") or getattr(self, "last_model_name", "") or ""),
                        "ocr_attempt": attempt_number,
                        "view_type": "失敗",
                        "category": "失敗",
                        "model": None,
                        "price": None,
                        "quality_issue": "AI 呼叫或解析暫時失敗",
                        "thinking": error_msg[:12000],
                        "started_at": pass_started_at,
                        "completed_at": error_completed_at,
                    }
                    error_result.update(self._source_metadata(fname, img_path))
                    error_thumb = ""
                    if os.path.isfile(img_path):
                        try:
                            error_thumb = self.img_proc.create_thumbnail(img_path, max_size=400)
                        except Exception:
                            error_thumb = ""
                    self.queue_presentation_event(
                        result=error_result,
                        attempt=attempt_number,
                        started_at=pass_started_at,
                        completed_at=error_completed_at,
                        previous_results=previous_results,
                        retry_reasons=["AI 呼叫或解析暫時失敗"],
                        decision="retry_scheduled",
                        narration=error_msg[:12000],
                        thumbnail=error_thumb,
                    )
                    history = self.auto_result_history.setdefault(fname, [])
                    history.append({
                        "view_type": "失敗",
                        "category": "失敗",
                        "model": None,
                        "price": None,
                        "quality_issue": "系統或模型暫時錯誤",
                        "thinking": error_msg[:500],
                        "reasons": ["系統或模型暫時錯誤"],
                    })
                    if fname not in self.retry_queue:
                        self.retry_queue.insert(0, fname)
                    self._persist_retry_state()
                    self.log_system(
                        f"🔁 [Accuracy Gate] {fname} 發生暫時錯誤，立即插入下一格做第 {attempt_number + 1} 輪"
                    )
                    time.sleep(0.2)
                    continue

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
                elif is_missing_runtime:
                    self.log_system(f"❌ 來源照片在批次執行中消失，停止本批次: {fname}")
                    failed_record["reason"] = error_msg[:300]
                    failed_record["error_type"] = "source_missing_runtime"
                    consecutive_failures = MAX_FAILURES
                else:
                    # System error - might be retryable, so we log this
                    # traceback.print_exc() # Disable traceback to keep terminal clean
                    log.warning(f"System Error on {fname}: {error_msg}")
                    # self.log_system(f"❌ 系統錯誤: {error_msg}") # [v12.0 Silence]
                    failed_record["reason"] = f"系統錯誤: {error_msg[:100]}"
                    failed_record["error_type"] = "system_error"
                    consecutive_failures += 1
                
                self.failed_files.append(failed_record)
                failure_completed_at = datetime.now().isoformat()
                failure_result = {
                    "file_name": fname,
                    "source_path": str(Path(img_path).resolve()),
                    "run_id": run_id,
                    "model_id": str(self.config.get("model_id") or getattr(self, "last_model_name", "") or ""),
                    "ocr_attempt": attempt_number,
                    "view_type": "失敗",
                    "category": "失敗",
                    "model": None,
                    "price": None,
                    "quality_issue": failed_record.get("reason") or error_msg[:300],
                    "thinking": error_msg[:12000],
                    "started_at": pass_started_at,
                    "completed_at": failure_completed_at,
                }
                failure_thumb = ""
                if os.path.isfile(img_path):
                    try:
                        failure_thumb = self.img_proc.create_thumbnail(img_path, max_size=400)
                    except Exception:
                        failure_thumb = ""
                self.queue_presentation_event(
                    result=failure_result,
                    attempt=attempt_number,
                    started_at=pass_started_at,
                    completed_at=failure_completed_at,
                    previous_results=previous_results,
                    retry_reasons=[failed_record.get("reason") or "辨識失敗"],
                    decision="failed",
                    narration=error_msg[:12000],
                    thumbnail=failure_thumb,
                )
                self.session_processed.add(fname)
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
        self.stream_buffer = "" # [v14.5 Fix] Clear buffer after completion
        self.stream_file = None

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

        # [OCG-v2.3] Average cost per image across all tracked inferences
        if self.cost_image_count > 0:
            avg_cost = self.total_image_cost / self.cost_image_count
        else:
            avg_cost = None
        
        return {
            "avg_duration": round(avg_duration, 2),
            "last_duration": round(last_duration, 2),
            "avg_cost": round(avg_cost, 6) if avg_cost is not None else None,
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

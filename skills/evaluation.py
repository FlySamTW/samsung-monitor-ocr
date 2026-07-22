import csv
import logging
from collections import Counter
from typing import List, Dict, Any

log = logging.getLogger("rich")

class Evaluator:
    """
    Calculates metrics for a batch run.
    Generates summary report + error distribution.
    """
    def __init__(self):
        pass

    def calculate_metrics(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Computes aggregate statistics from a list of results.
        """
        total = len(results)
        if total == 0:
            return {"total": 0, "success_rate": 0}

        # Categories
        cat_counts = Counter(r.get('category', 'Unknown') for r in results)
        
        # Valid Extractions (Single Device with Model + Price)
        valid_extractions = 0
        failed_count = 0
        
        for r in results:
            cat = r.get('category')
            if cat == '失敗':
                failed_count += 1
            elif cat == '單機':
                if r.get('model') and r.get('price'):
                    valid_extractions += 1
        
        success_rate = ((total - failed_count) / total) * 100
        extraction_rate = (valid_extractions / total) * 100

        metrics = {
            "total": total,
            "processed": total, # For compatibility
            "success": total - failed_count,
            "failed": failed_count,
            "success_rate": round(success_rate, 2),
            "extraction_rate": round(extraction_rate, 2),
            "categories": dict(cat_counts)
        }
        return metrics

    def generate_csv_report(self, results: List[Dict[str, Any]], filepath: str, append: bool = False):
        """Writes the standard results CSV."""
        try:
            headers = [
                'timestamp',
                'file_name',
                'category',
                'view_type',
                'screen_status',
                'quality_issue',
                'model',
                'price',
                'price_status',
                'price_symbol',
                'official_price',
                'price_diff_percent',
                'black_screen',
                'duration',
                'run_id',
                'evidence_contract_version',
                'evidence_guard_revision',
                'evidence_contract_valid',
                'evidence_contract_errors',
                'review_status',
                'human_is_correct',
                'human_category',
                'human_model',
                'human_price',
                'human_notes',
                'rerun_priority',
                'rerun_reason',
                'rerun_recommended_model',
                'ocr_attempt',
                'auto_retry_reasons',
                'auto_verified',
                'auto_review_required',
                'model_validation_failed',
                'rejected_model',
                'price_conflict_detected',
                'raw_response',
                'thinking',
            ]
            mode = 'a' if append else 'w'
            should_write_header = True
            if append:
                try:
                    import os
                    should_write_header = (not os.path.exists(filepath)) or os.path.getsize(filepath) == 0
                except OSError:
                    should_write_header = True

            with open(filepath, mode, newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                if should_write_header:
                    writer.writerow(headers)
                for r in results:
                    writer.writerow([
                        r.get('timestamp',''), 
                        r.get('file_name',''), 
                        r.get('category',''),
                        r.get('view_type',''),
                        r.get('screen_status',''),
                        r.get('quality_issue',''),
                        r.get('model','') or 'null', 
                        r.get('price','') or 'null', 
                        r.get('price_status',''),
                        r.get('price_symbol',''),
                        r.get('official_price',''),
                        r.get('price_diff_percent',''),
                        r.get('black_screen',''),
                        r.get('duration',''),
                        r.get('run_id', ''),
                        r.get('evidence_contract_version', ''),
                        r.get('evidence_guard_revision', ''),
                        r.get('evidence_contract_valid', ''),
                        r.get('evidence_contract_errors', ''),
                        r.get('review_status', ''),
                        r.get('human_is_correct', ''),
                        r.get('human_category', ''),
                        r.get('human_model', ''),
                        r.get('human_price', ''),
                        r.get('human_notes', ''),
                        r.get('rerun_priority', ''),
                        r.get('rerun_reason', ''),
                        r.get('rerun_recommended_model', ''),
                        r.get('ocr_attempt', ''),
                        r.get('auto_retry_reasons', ''),
                        r.get('auto_verified', ''),
                        r.get('auto_review_required', ''),
                        r.get('model_validation_failed', ''),
                        r.get('rejected_model', ''),
                        r.get('price_conflict_detected', ''),
                        r.get('raw_response','').replace('\n', ' ')[:5000],
                        r.get('thinking','').replace('\n', ' ')[:5000],
                    ])
        except Exception as e:
            log.error(f"Failed to write CSV report: {e}")

    def export_to_label_studio_json(self, results: List[Dict[str, Any]], filepath: str):
        """Exports results in Label Studio JSON format."""
        import json
        import os
        
        ls_tasks = []
        for i, r in enumerate(results):
            # Construct Label Studio Task Structure
            file_upload = r.get('file_name', f"image_{i}.jpg")
            
            # Map Category
            cat = r.get('category', '單機')
            model = r.get('model', '')
            price = r.get('price', '')
            
            # Annotations
            result_items = []
            
            # 1. Category
            result_items.append({
                "from_name": "category",
                "to_name": "image",
                "type": "choices",
                "origin": "prediction",
                "value": {"choices": [cat]}
            })
            
            # 2. Model (Always output)
            result_items.append({
                "from_name": "model",
                "to_name": "image",
                "type": "textarea",
                "origin": "prediction",
                "value": {"text": [str(model) if model else "null"]}
            })

            # 3. Price (Always output)
            result_items.append({
                "from_name": "price",
                "to_name": "image",
                "type": "textarea",
                "origin": "prediction",
                "value": {"text": [str(price) if price else "null"]}
            })
                
            task = {
                "id": i + 1,
                "data": {
                    "image": f"/data/upload/1/{file_upload}",
                    "ocr_meta": {
                        "view_type": r.get('view_type', ''),
                        "screen_status": r.get('screen_status', ''),
                        "quality_issue": r.get('quality_issue', ''),
                        "price_status": r.get('price_status', ''),
                        "price_symbol": r.get('price_symbol', ''),
                        "official_price": r.get('official_price', ''),
                        "price_diff_percent": r.get('price_diff_percent', ''),
                        "ocr_attempt": r.get('ocr_attempt', ''),
                        "auto_retry_reasons": r.get('auto_retry_reasons', ''),
                        "auto_verified": r.get('auto_verified', False),
                        "auto_review_required": r.get('auto_review_required', False),
                        "review_status": r.get('review_status', ''),
                        "evidence_contract_version": r.get('evidence_contract_version', ''),
                        "evidence_guard_revision": r.get('evidence_guard_revision', ''),
                        "evidence_contract_valid": r.get('evidence_contract_valid', False),
                        "model_validation_failed": r.get('model_validation_failed', False),
                        "rejected_model": r.get('rejected_model', ''),
                        "price_conflict_detected": r.get('price_conflict_detected', False),
                        "adjudication_rule": r.get('adjudication_rule', ''),
                        "three_pass_adjudicated": r.get('three_pass_adjudicated', False),
                    }
                },
                "annotations": [{
                    "id": i + 1,
                    "created_at": r.get('timestamp'),
                    "result": result_items,
                    "was_cancelled": False,
                    "ground_truth": False
                }]
            }
            ls_tasks.append(task)
            
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(ls_tasks, f, indent=2, ensure_ascii=False)
            log.info(f"Exported JSON to {filepath}")
        except Exception as e:
            log.error(f"Failed to export JSON: {e}")

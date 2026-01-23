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

    def generate_csv_report(self, results: List[Dict[str, Any]], filepath: str):
        """Writes the standard results CSV."""
        try:
            headers = ['timestamp', 'file_name', 'category', 'model', 'price', 'black_screen', 'raw_response', 'run_id']
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                for r in results:
                    writer.writerow([
                        r.get('timestamp',''), 
                        r.get('file_name',''), 
                        r.get('category',''),
                        r.get('model','') or 'null', 
                        r.get('price','') or 'null', 
                        r.get('black_screen',''),
                        r.get('raw_response','').replace('\n', ' ')[:5000],
                        r.get('run_id', '')
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
            
            # 2. Model
            if model:
                result_items.append({
                    "from_name": "model",
                    "to_name": "image",
                    "type": "textarea",
                    "origin": "prediction",
                    "value": {"text": [model]}
                })

            # 3. Price
            if price:
                result_items.append({
                    "from_name": "price",
                    "to_name": "image",
                    "type": "textarea",
                    "origin": "prediction",
                    "value": {"text": [str(price)]}
                })
                
            task = {
                "id": i + 1,
                "data": {
                    "image": f"/data/upload/1/{file_upload}" 
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

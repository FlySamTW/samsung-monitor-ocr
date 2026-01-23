import json
import logging
import os
from typing import List, Dict, Tuple

log = logging.getLogger("rich")

class AutoCurator:
    """
    The 'Brain' of the closed loop.
    1. Receives human feedback (corrections).
    2. Updates dynamic few-shot examples.
    3. Updates feedback rules (if applicable).
    """
    def __init__(self, persistence_file: str = "dynamic_data.json"):
        self.persistence_file = persistence_file
        self.dynamic_examples: List[Tuple[str, Dict]] = []
        self.feedback_rules: List[str] = []
        self._load_data()

    def _load_data(self):
        if os.path.exists(self.persistence_file):
            try:
                with open(self.persistence_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Convert list of lists back to list of tuples
                    self.dynamic_examples = [tuple(x) for x in data.get('dynamic_examples', [])]
                    self.feedback_rules = data.get('feedback_rules', [])
                log.info(f"AutoCurator loaded {len(self.dynamic_examples)} examples, {len(self.feedback_rules)} rules.")
            except Exception as e:
                log.error(f"AutoCurator load failed: {e}")

    def save_data(self):
        try:
            data = {
                "dynamic_examples": self.dynamic_examples,
                "feedback_rules": self.feedback_rules
            }
            with open(self.persistence_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            log.info("AutoCurator data saved.")
        except Exception as e:
            log.error(f"AutoCurator save failed: {e}")

    def add_correction(self, file_name: str, corrected_result: Dict, rule_note: str = None):
        """
        Ingest a human correction.
        """
        # 1. Add to dynamic examples (Limit 20 recent)
        # Check if already exists, remove old
        self.dynamic_examples = [x for x in self.dynamic_examples if x[0] != file_name]
        self.dynamic_examples.append((file_name, corrected_result))
        
        if len(self.dynamic_examples) > 20:
            self.dynamic_examples.pop(0)

        # 2. Add rule if provided
        if rule_note:
            new_rule = f"User Note ({file_name}): {rule_note}"
            if new_rule not in self.feedback_rules:
                self.feedback_rules.append(new_rule)
        
        self.save_data()

    def get_dynamic_examples(self, k=3) -> List[Tuple[str, Dict]]:
        """Returns the k most recent dynamic examples."""
        return self.dynamic_examples[-k:]

    def get_feedback_rules(self) -> List[str]:
        return self.feedback_rules

    def get_relevant_examples(self, limit: int = 3) -> List[Dict]:
        """
        Adapter to format examples for prompt injection.
        """
        raw_examples = self.get_dynamic_examples(k=limit)
        formatted = []
        for fname, correction in raw_examples:
            formatted.append({
                "context": f"檔名: {fname}",
                "output": json.dumps(correction, ensure_ascii=False)
            })
        return formatted

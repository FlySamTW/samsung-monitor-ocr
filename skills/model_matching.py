import os
import difflib
import logging
from typing import List, Optional

log = logging.getLogger("rich")

class ModelMatcher:
    """
    Matches OCR extracted model names against a known valid model list.
    Supports exact match, substring match, and fuzzy match.
    """
    def __init__(self, model_list_path: str):
        self.model_list_path = model_list_path
        self.valid_models = self._load_model_list()

    def _load_model_list(self) -> List[str]:
        valid_models = []
        if os.path.exists(self.model_list_path):
            try:
                with open(self.model_list_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                    valid_models = [line.strip() for line in lines if line.strip()]
                log.info(f"已載入標準型號表: {len(valid_models)} 筆")
            except Exception as e:
                log.error(f"讀取型號表失敗: {e}")
        else:
            log.warning(f"找不道型號表檔案: {self.model_list_path}")
        return valid_models

    def match(self, raw_model: str, cutoff: float = 0.6) -> Optional[str]:
        """
        Attempts to find the closest matching model from the valid list.
        Returns the matched model name, or None if no match found.
        """
        if not raw_model or not self.valid_models:
            return None

        # Normalize raw input
        upper_raw = raw_model.upper().replace("-", "").replace(" ", "")

        # 1. Exact Match (Normalized)
        for m in self.valid_models:
            m_norm = m.upper().replace("-", "").replace(" ", "")
            if m_norm == upper_raw:
                return m

        # 2. Substring Match with Keyword Weighting
        # Priority Keywords (Case Insensitive)
        priority_keywords = ["M7", "M5", "M8", "G5", "G7", "G8", "G9", "T55", "R500"]
        
        # Check for keyword matches first
        for key in priority_keywords:
            if key in upper_raw:
                # Find all models containing this keyword
                potential_matches = [m for m in self.valid_models if key in m.upper()]
                if potential_matches:
                    # Return the longest matching model or best fit
                    return potential_matches[0]

        if len(upper_raw) >= 3:
            for m in self.valid_models:
                m_norm = m.upper().replace("-", "").replace(" ", "")
                if upper_raw in m_norm or m_norm in upper_raw:
                    return m

        # 3. Fuzzy Match
        matches = difflib.get_close_matches(raw_model.upper(), self.valid_models, n=1, cutoff=0.3)
        if matches:
            return matches[0]

        return None

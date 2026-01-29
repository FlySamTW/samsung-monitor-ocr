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
        [v16.80] Added Size Gatekeeper to prevent cross-size hallucinations.
        [v18.41] Added Greedy Regex Extraction to handle noisy inputs (e.g., '24SAMSUNG S24F532EAC').
        """
        if not raw_model or not self.valid_models:
            return None

        import re

        # [v18.41] Greedy Regex Extraction Strategy
        # Try to extract potential model patterns from the raw string first
        # This solves cases like "24SAMSUNG S24F532EAC 100" where the correct model is buried
        greedy_pattern = r'([SFC][0-9]{2}[A-Z0-9]+)'
        greedy_matches = re.findall(greedy_pattern, raw_model.upper())
        
        candidates_to_check = [raw_model] # Always check the full raw string too
        if greedy_matches:
            # Filter matches that are too short to be valid models (e.g., S24)
            valid_greedy = [m for m in greedy_matches if len(m) >= 6]
            candidates_to_check.extend(valid_greedy)
            log.info(f"[ModelMatcher] Greedy Extraction Found: {valid_greedy}")

        # Iterate through candidates (Greedy matches first, then raw)
        # We prioritize the extracted clean signals
        best_match = None
        
        for candidate_raw in candidates_to_check:
            # Normalize raw input
            # [v18.39] Remove quotes and size units to handle 'FollowMe M7 32"' vs 'FollowMe M7 32'
            upper_raw = candidate_raw.upper().replace("-", "").replace(" ", "").replace('"', "").replace("'", "").replace("INCH", "").replace("吋", "")
            
            # ... internal logic ...
            match_result = self._internal_match(upper_raw, cutoff)
            if match_result:
                return match_result
        
        return None

    def _internal_match(self, upper_raw: str, cutoff: float) -> Optional[str]:
        """Internal matching logic separated for reuse."""
        
        # Helper: Extract size from string (e.g., S32... -> 32)
        def get_size_prefix(s: str) -> Optional[str]:
            import re
            m = re.search(r'(24|27|32|34|37|40|43|49|55|57)', s)
            return m.group(1) if m else None

        detected_input_size = get_size_prefix(upper_raw)

        # [v18.42 Fix] Define upper_raw_no_l BEFORE usage
        upper_raw_no_l = upper_raw
        if upper_raw.startswith("L") and len(upper_raw) > 3:
            upper_raw_no_l = upper_raw[1:]

        # 1. Exact Match (Normalized)
        for m in self.valid_models:
            # [v18.39] Also normalize the valid model string (remove quotes)
            m_norm = m.upper().replace("-", "").replace(" ", "").replace('"', "").replace("'", "")
            if m_norm == upper_raw:
                return m
            if m_norm == upper_raw_no_l: 
                return m

        # (Moved above)
            
        # Re-check Exact Match with no_l
        for m in self.valid_models:
             m_norm = m.upper().replace("-", "").replace(" ", "").replace('"', "").replace("'", "")
             if m_norm == upper_raw_no_l:
                 return m

        def check_substring(model_val: str, input_val: str) -> bool:
            """Check if model is in input or input is in model."""
            return model_val in input_val or input_val in model_val

        def is_size_matching(candidate_model: str) -> bool:
            """[v16.80] Gatekeeper: Mismatched sizes are strictly forbidden."""
            if not detected_input_size: return True # Cannot verify
            candidate_size = get_size_prefix(candidate_model)
            if candidate_size and candidate_size != detected_input_size:
                return False
            return True

        # 2. Substring Match with Keyword Weighting
        # [v18.39] Added FollowMe to priority keywords
        priority_keywords = ["FollowMe", "M7", "M5", "M8", "G5", "G7", "G8", "G9", "T55", "R500"]
        matched_candidates = []
        
        for key in priority_keywords:
            if key.upper() in upper_raw:
                candidates = [m for m in self.valid_models if key.upper() in m.upper()]
                for m in candidates:
                    # [v18.39] Normalize candidate
                    m_norm = m.upper().replace("-", "").replace(" ", "").replace('"', "").replace("'", "")
                    if check_substring(m_norm, upper_raw) or check_substring(m_norm, upper_raw_no_l):
                        # [v16.80] Apply Size Gatekeeper
                        if is_size_matching(m):
                            matched_candidates.append(m)
        
        if matched_candidates:
            matched_candidates = list(set(matched_candidates))
            matched_candidates.sort(key=lambda x: len(x), reverse=True)
            return matched_candidates[0]

        # 3. General Substring Match
        if len(upper_raw) >= 3:
            for m in self.valid_models:
                # [v18.39] Normalize candidate
                m_norm = m.upper().replace("-", "").replace(" ", "").replace('"', "").replace("'", "")
                if check_substring(m_norm, upper_raw) or check_substring(m_norm, upper_raw_no_l):
                    # [v16.80] Apply Size Gatekeeper
                    if is_size_matching(m):
                        return m

        # 4. [v18.61] Precise Single-Digit Tolerance Matching
        # Check for exactly ONE digit difference, prioritize spec list models
        if detected_input_size:
            potential_models = [m for m in self.valid_models if detected_input_size in m]
        else:
            potential_models = self.valid_models
        
        # [v18.61] Precise single-digit difference detection
        for model in potential_models:
            model_norm = model.upper().replace("-", "").replace(" ", "").replace('"', "").replace("'", "")
            
            # Must be same length to compare character by character
            if len(model_norm) == len(upper_raw):
                different_positions = []
                digit_differences = []
                
                # Check each character position
                for i, (spec_char, ocr_char) in enumerate(zip(model_norm, upper_raw)):
                    if spec_char != ocr_char:
                        different_positions.append(i)
                        # Check if both are digits (this is the key difference)
                        if spec_char.isdigit() and ocr_char.isdigit():
                            digit_differences.append((i, spec_char, ocr_char))
                
                # Allow exactly ONE digit difference, no other differences
                if len(different_positions) == 1 and len(digit_differences) == 1:
                    pos, spec_digit, ocr_digit = digit_differences[0]
                    log.info(f"[ModelMatcher] Single digit tolerance: {upper_raw} -> {model} (position {pos}: OCR={ocr_digit} -> Spec={spec_digit})")
                    return model
        
        # 5. Standard fuzzy match as fallback (lowered cutoff)
        matches = difflib.get_close_matches(upper_raw, potential_models, n=1, cutoff=0.5)
        if matches:
            return matches[0]
            
        matches_no_l = difflib.get_close_matches(upper_raw_no_l, potential_models, n=1, cutoff=0.5)
        if matches_no_l:
            return matches_no_l[0]

        return None

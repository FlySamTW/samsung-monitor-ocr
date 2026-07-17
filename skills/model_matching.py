"""Catalog-bound model matching for OCR output."""

from __future__ import annotations

import logging
import os
import re
from typing import List, Optional

from skills.model_catalog_rules import extract_samsung_models
from skills.model_validation import (
    known_model_suggestions,
    safe_known_model_correction,
    strict_known_model,
)


log = logging.getLogger("rich")


class ModelMatcher:
    """Match OCR text only to a known Samsung model.

    Exact official/retailer aliases are accepted.  A unique short suffix or a
    bounded same-size OCR typo may be corrected.  Broad substring and low-score
    fuzzy matching are intentionally excluded because they can manufacture a
    model that was never present in the photo.
    """

    def __init__(self, model_list_path: str):
        self.model_list_path = model_list_path
        self.valid_models = self._load_model_list()

    def _load_model_list(self) -> List[str]:
        valid_models: list[str] = []
        if os.path.exists(self.model_list_path):
            try:
                with open(self.model_list_path, "r", encoding="utf-8") as handle:
                    valid_models = [line.strip() for line in handle if line.strip()]
                log.info(f"已載入標準型號表: {len(valid_models)} 筆")
            except Exception as exc:
                log.error(f"讀取型號表失敗: {exc}")
        else:
            log.warning(f"找不到型號表檔案: {self.model_list_path}")
        return valid_models

    def match(self, raw_model: str, cutoff: float = 0.6) -> Optional[str]:
        """Return a catalog model or ``None``.

        ``cutoff`` remains in the public signature for old callers but no
        longer weakens the safety threshold.
        """
        del cutoff
        if not raw_model or not self.valid_models:
            return None

        candidates = extract_samsung_models(raw_model)
        if re.fullmatch(r"\s*L?[A-Z]\d{2}[A-Z0-9-]{5,}(?:XZW)?\s*", str(raw_model), re.IGNORECASE):
            candidates.insert(0, raw_model)
        candidates.append(raw_model)

        checked: set[str] = set()
        for candidate in candidates:
            key = str(candidate).strip().upper()
            if not key or key in checked:
                continue
            checked.add(key)
            exact = strict_known_model(candidate, self.valid_models)
            if exact:
                return exact
            corrected = safe_known_model_correction(candidate, self.valid_models)
            if corrected:
                log.info(f"[ModelMatcher] 安全校正: {candidate} -> {corrected}")
                return corrected
        return None

    def suggestions(self, raw_model: str, limit: int = 4) -> list[str]:
        """List possible catalog models for human review without auto-selecting."""
        return known_model_suggestions(raw_model, self.valid_models, limit=limit)

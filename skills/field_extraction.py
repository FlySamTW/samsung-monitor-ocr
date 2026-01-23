import re
import logging
from typing import Dict, Any

log = logging.getLogger("rich")

class FieldNormalizer:
    """
    Normalizes and validates extracted OCR fields.
    - Category: Ensures valid enum values.
    - Model: Strips excess whitespace, handles nulls.
    - Price: Convers '2,990' -> '2990', handles currency symbols.
    """
    def __init__(self):
        pass

    def normalize(self, raw_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Input: Raw JSON from LLM
        Output: Cleaned JSON with normalized fields
        """
        normalized = raw_result.copy()
        
        # 1. Category Normalization
        valid_cats = ["單機", "遠景", "不合格-照片不清楚", "不合格-單機但看不清楚價格或型號"]
        cat = normalized.get("category", "")
        if cat not in valid_cats:
            # Fallback based on keywords
            if "遠景" in cat: normalized["category"] = "遠景"
            elif "不清" in cat: normalized["category"] = "不合格-照片不清楚"
            else: normalized["category"] = "單機"  # Default aggressive fallback
            
        # 2. Model Normalization
        model = normalized.get("model")
        if model:
            # Remove whitespace
            model = str(model).strip()
            # Normalize 'null', 'None' strings to actual None
            if model.lower() in ['null', 'none', 'n/a']:
                model = None
            else:
                # Remove common prefixes users might hallucinate
                model = re.sub(r'^(ANS|MODEL|TYPE)[:\s]*', '', model, flags=re.IGNORECASE)
        normalized["model"] = model

        # 3. Price Normalization
        price = normalized.get("price")
        if price:
            price_str = str(price).strip()
            if price_str.lower() in ['null', 'none', 'n/a']:
                normalized["price"] = None
            else:
                # Remove currency symbols ($, NT$, NT, etc.)
                # Keep only digits and decimal points
                clean_price = re.sub(r'[^\d.]', '', price_str)
                try:
                    # Convert to int if integer, else float
                    if '.' in clean_price:
                        normalized["price"] = str(int(float(clean_price))) # Cast to int string '2990'
                    else:
                        normalized["price"] = clean_price
                except ValueError:
                    normalized["price"] = None # Failed to parse digit
        else:
             normalized["price"] = None
             
        # 4. Black Screen Boolean
        bs = normalized.get("black_screen")
        if isinstance(bs, str):
            normalized["black_screen"] = bs.lower() == "true"
            
        return normalized

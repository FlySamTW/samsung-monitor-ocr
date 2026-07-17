#!/usr/bin/env python3
"""Test validate_ocr_price for FollowMe models."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from skills.model_catalog_rules import FOLLOWME_MODELS
from skills.official_price import validate_ocr_price


def main():
    sample_prices = {
        'FollowMe M5 27"': 5990,
        'FollowMe M5 32"': 10990,
        'FollowMe M7 32"': 12990,
        'FollowMe Pro M7 32"': 12990,
        'FollowMe M7 43"': 13990,
        'FollowMe Pro M7 43"': 17990,
    }
    test_cases = [(model, sample_prices[model]) for model in FOLLOWME_MODELS]

    print("Testing validate_ocr_price for FollowMe models:")
    for model, price in test_cases:
        result = validate_ocr_price(model, price)
        print(f"  {model} @ {price}:")
        print(f"    status: {result.get('status')}")
        print(f"    symbol: {result.get('symbol')}")
        print(f"    official_price: {result.get('official_price')}")

if __name__ == "__main__":
    main()

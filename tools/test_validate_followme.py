#!/usr/bin/env python3
"""Test validate_ocr_price for FollowMe models."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from skills.official_price import validate_ocr_price

def main():
    test_cases = [
        ('FOLLOWME PRO M7 43"', 17990),
        ('FOLLOWME M7 32"', 12990),
        ('FOLLOWME M5 32"', 10990),
    ]

    print("Testing validate_ocr_price for FollowMe models:")
    for model, price in test_cases:
        result = validate_ocr_price(model, price)
        print(f"  {model} @ {price}:")
        print(f"    status: {result.get('status')}")
        print(f"    symbol: {result.get('symbol')}")
        print(f"    official_price: {result.get('official_price')}")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Test PChome fallback for unknown models."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from skills.official_price import get_price_manager

def main():
    manager = get_price_manager()
    test_models = [
        'S24DG302EC',
        'S27D392GAC',
        'S27B610EQC',
        'S32DM703UC',
        'S27DG502EC',
        'S32BG700EC',
        'FOLLOWME PRO M7 43"',
        'FOLLOWME M7 32"',
        'FOLLOWME M5 32"',
    ]

    print("Testing PChome fallback for unknown models...")
    for m in test_models:
        price = manager.get_official_price(m)
        status = f"NT${price:,}" if price else "NOT FOUND"
        print(f"  {m}: {status}")

if __name__ == "__main__":
    main()

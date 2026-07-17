#!/usr/bin/env python3
"""Test cache lookup for FollowMe models."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from skills.model_catalog_rules import FOLLOWME_MODELS
from skills.official_price import get_price_manager


def main():
    manager = get_price_manager()
    manager._load_from_txt()

    print("Testing cache lookup for FollowMe models:")
    for model in FOLLOWME_MODELS:
        m = model.upper()
        in_cache = m in manager.price_cache
        price = manager.price_cache.get(m, "NOT IN CACHE")
        print(f"  {m}: in_cache={in_cache}, price={price}")

if __name__ == "__main__":
    main()

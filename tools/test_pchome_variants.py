#!/usr/bin/env python3
"""Test various PChome search queries for Samsung models."""
import csv
import requests
import re
import sys
from pathlib import Path

PCHOME_SEARCH_API = "https://ecshweb.pchome.com.tw/search/v3.3/all/results"


def search_pchome(query):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json,text/plain,*/*',
        'Referer': 'https://24h.pchome.com.tw/',
    }
    try:
        response = requests.get(
            PCHOME_SEARCH_API,
            params={"q": query, "page": 1, "sort": "sale/dc"},
            headers=headers,
            timeout=15,
        )
        data = response.json()
        return data.get("prods", []) or []
    except Exception as e:
        print(f"Error for {query}: {e}")
        return []


def main():
    models = ['S24DG302EC', 'S27D392GAC', 'S27B610EQC', 'S32DM703UC']
    samsung_keywords = {'SAMSUNG', '三星', 'MONITOR', '螢幕', '顯示器'}

    for model in models:
        print(f"\n=== {model} ===")
        queries = [
            model,
            f"Samsung {model}",
            f"三星 {model}",
            model[:-2],  # without last 2 chars
            model[:-1],  # without last char
        ]

        for query in queries:
            prods = search_pchome(query)
            print(f"\nQuery: {query} -> {len(prods)} results")

            for prod in prods[:5]:
                name = str(prod.get("name") or "")
                product_id = str(prod.get("Id") or prod.get("Idno") or "")
                price = prod.get("price") or prod.get("originPrice")
                name_upper = name.upper()
                has_samsung = any(kw in name_upper for kw in samsung_keywords)
                compact_query = re.sub(r"[^A-Z0-9]", "", query.upper())
                haystack = re.sub(r"[^A-Z0-9]", "", f"{name_upper} {product_id}")
                matched = compact_query in haystack
                print(f"  {name[:50]} | ID:{product_id} | ${price} | Samsung:{has_samsung} | Matched:{matched}")

if __name__ == "__main__":
    main()

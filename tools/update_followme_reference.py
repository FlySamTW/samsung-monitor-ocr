import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from skills.model_catalog_rules import followme_catalog_models, normalize_samsung_model


DATA_PATH = PROJECT_ROOT / "data" / "followme_reference.json"
MODEL_LIST_PATH = PROJECT_ROOT / "型號表.txt"


PRODUCTS = [
    {
        "name": 'FollowMe M5 32"',
        "aliases": ["FollowMe M5 32", "M5", "M50F", "FollowMe FHD 32"],
        "model_codes": ["S32FM501EC", "S32FM500EC", "LS32FM501ECXZW"],
        "specs": {
            "size_inch": 32,
            "resolution_label": "FHD",
            "resolution": "1920 x 1080",
            "panel": "VA",
            "refresh_rate": "Max 60Hz",
            "response_time": "4ms(GtG)",
            "brightness": "200-250 cd/m2",
            "stand": "M50F + 移動式立架",
        },
        "price": {
            "expected_range_twd": [9999, 10990],
            "seed_twd": [9999, 10990],
            "observed_twd": [],
        },
        "sources": [
            {
                "name": "Samsung 支援台灣 S32FM501EC",
                "url": "https://www.samsung.com/tw/support/model/LS32FM501ECXZW/",
                "type": "official_support",
            },
            {
                "name": "EcLife S32FM501EC M5",
                "url": "https://www.eclife.com.tw/pc_nb/moreinfo_208286.htm",
                "type": "retail",
            },
            {
                "name": "Costco S32FM501EC-FOLLOWME",
                "url": "https://www.costco.com.tw/Digital-Mobile/Laptops-Computers/Monitors/Samsung-32-inch-FHD-FollowMe-Smart-Display-S32FM501EC-FOLLOWME/p/154938",
                "type": "retail",
            },
            {
                "name": "PChome S32FM501EC FollowMe",
                "url": "https://24h.pchome.com.tw/prod/DSABVI-1900JF8XS",
                "type": "retail",
            },
        ],
    },
    {
        "name": 'FollowMe M7 32"',
        "aliases": ["FollowMe M7 32", "M7", "M70F", "FollowMe 4K 32"],
        "model_codes": ["S32FM703UC", "S32FM702UC", "S32DM703UC", "LS32FM703UCXZW"],
        "specs": {
            "size_inch": 32,
            "resolution_label": "4K UHD",
            "resolution": "3840 x 2160",
            "panel": "VA",
            "refresh_rate": "Max 60Hz",
            "response_time": "4ms(GtG)",
            "brightness": "300 cd/m2",
            "stand": "M70F + 移動式立架",
        },
        "price": {
            "expected_range_twd": [12990, 15990],
            "seed_twd": [12990, 13990, 15990],
            "observed_twd": [],
        },
        "sources": [
            {
                "name": "Samsung 台灣 S32FM703UC",
                "url": "https://www.samsung.com/tw/monitors/smart/smart-monitor-m7-32-inch-smart-tv-apps-4k-uhd-ls32fm703ucxzw/",
                "type": "official_product",
            },
            {
                "name": "myfone S32FM702UC/S32FM703UC",
                "url": "https://www.myfone.com.tw/buy/prod/P0000203737941-SAMSUNG%20%E4%B8%89%E6%98%9F-FollowMe",
                "type": "retail",
            },
            {
                "name": "EcLife Rakuten S32FM703UC",
                "url": "https://www.rakuten.com.tw/shop/eclife3c01/product/f4163324/",
                "type": "retail",
            },
            {
                "name": "Yahoo S32FM703UC+立架",
                "url": "https://tw.buy.yahoo.com/gdsale/SAMSUNG-%E4%B8%89%E6%98%9F-FollowMe-32%E5%90%8B-4K-%E7%A7%BB%E5%8B%95%E5%BC%8F%E6%99%BA%E6%85%A7%E8%81%AF%E7%B6%B2%E8%9E%A2%E5%B9%95%E7%B5%84-S32FM703UC-%E7%AB%8B-11674840.html",
                "type": "retail",
            },
        ],
    },
    {
        "name": 'FollowMe Pro M7 43"',
        "aliases": ["FollowMe Pro", "FollowMe Pro 43", "FM7", "43FM7", "M7 43"],
        "model_codes": ["S43FM703UC", "S43FM702UC", "LS43FM703UCXZW"],
        "specs": {
            "size_inch": 43,
            "resolution_label": "4K UHD",
            "resolution": "3840 x 2160",
            "panel": "VA",
            "refresh_rate": "Max 60Hz",
            "response_time": "4ms(GtG)",
            "brightness": "300 cd/m2",
            "stand": "FollowMe Pro 移動式立架",
        },
        "price": {
            "expected_range_twd": [16888, 17990],
            "seed_twd": [16888, 17900, 17990],
            "observed_twd": [],
        },
        "sources": [
            {
                "name": "Samsung 台灣 S43FM703UC",
                "url": "https://www.samsung.com/tw/monitors/smart/smart-monitor-m7-43-inch-smart-tv-apps-4k-uhd-ls43fm703ucxzw/",
                "type": "official_product",
            },
            {
                "name": "myfone S43FM703UC FollowMe Pro",
                "url": "https://www.myfone.com.tw/mbuy/prod/P0000203736509-SAMSUNG%20%E4%B8%89%E6%98%9F-S43FM703UC-FOLLOWME%20PRO",
                "type": "retail",
            },
            {
                "name": "PChome S43FM703UC FollowMe Pro",
                "url": "https://24h.pchome.com.tw/prod/DSABT5-A900JMN5I",
                "type": "retail",
            },
            {
                "name": "EcLife S43FM703UC FollowMe Pro",
                "url": "https://www.eclife.com.tw/pc_nb/moreinfo_211591.htm",
                "type": "retail",
            },
        ],
    },
]


def fetch_text(url: str, timeout: int = 20) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 SamsungOCR/1.0",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.5",
        },
    )
    with urlopen(req, timeout=timeout) as response:
        raw = response.read()
    for encoding in ("utf-8", "cp950", "big5"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def strip_html(text: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", text)


def extract_prices(text: str) -> list[int]:
    plain = strip_html(text)
    prices = set()
    for match in re.finditer(r"(?:NT\$|NT|TWD|\$|＄)\s*([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,6})", plain, flags=re.I):
        value = int(match.group(1).replace(",", ""))
        if 5000 <= value <= 30000:
            prices.add(value)
    for match in re.finditer(r"(?:特價|售價|限定價|推薦價|市價|價格|原價)[^0-9]{0,20}([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,6})", plain):
        value = int(match.group(1).replace(",", ""))
        if 5000 <= value <= 30000:
            prices.add(value)
    return sorted(prices)


def expected_prices_for(product: dict) -> list[int]:
    low, high = product["price"]["expected_range_twd"]
    observed = set(product["price"].get("observed_twd", []))
    observed.update(product["price"].get("seed_twd", []))
    return sorted(p for p in observed if low <= p <= high)


def update_model_list(products: list[dict]) -> None:
    existing = []
    if MODEL_LIST_PATH.exists():
        existing = [line.strip() for line in MODEL_LIST_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    seen = {item.upper() for item in existing}
    for candidate in followme_catalog_models():
        if candidate and candidate.upper() not in seen:
            existing.append(candidate)
            seen.add(candidate.upper())
    for product in products:
        for code in product.get("model_codes", []):
            short = normalize_samsung_model(code)
            for candidate in [short, code]:
                if candidate and candidate.upper() not in seen:
                    existing.append(candidate)
                    seen.add(candidate.upper())
        name = product.get("name")
        if name and name.upper() not in seen:
            existing.append(name)
            seen.add(name.upper())
    MODEL_LIST_PATH.write_text("\n".join(existing) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Update daily Samsung FollowMe specs and price reference.")
    parser.add_argument("--offline", action="store_true", help="Write seeded reference without fetching websites.")
    parser.add_argument("--no-model-list", action="store_true", help="Do not update 型號表.txt")
    args = parser.parse_args()

    products = json.loads(json.dumps(PRODUCTS, ensure_ascii=False))
    failures = []
    for product in products:
        raw_observed = set()
        source_results = []
        for source in product.get("sources", []):
            result = {"name": source["name"], "url": source["url"], "type": source["type"], "prices_twd": [], "status": "seeded"}
            if not args.offline:
                try:
                    text = fetch_text(source["url"])
                    prices = extract_prices(text)
                    result["prices_twd"] = prices
                    result["status"] = "ok"
                    raw_observed.update(prices)
                except (URLError, TimeoutError, OSError) as exc:
                    result["status"] = f"fetch_failed: {exc.__class__.__name__}"
                    failures.append(f"{product['name']} / {source['name']}: {exc}")
            source_results.append(result)
        product["source_results"] = source_results
        product["price"]["raw_observed_twd"] = sorted(raw_observed)
        low, high = product["price"]["expected_range_twd"]
        observed = set(product["price"].get("seed_twd", []))
        observed.update(p for p in raw_observed if low <= p <= high)
        product["price"]["observed_twd"] = sorted(observed)
        in_range = expected_prices_for(product)
        product["price"]["range_twd"] = [min(in_range), max(in_range)] if in_range else product["price"]["expected_range_twd"]

    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "updated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "note": "Generated by tools/update_followme_reference.py. Prices are plausibility references only and never identify a FollowMe family.",
        "products": products,
        "failures": failures,
    }
    DATA_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.no_model_list:
        update_model_list(products)

    print(f"updated={DATA_PATH}")
    print(f"products={len(products)}")
    print(f"failures={len(failures)}")
    for product in products:
        print(f"{product['name']}: observed={product['price']['observed_twd']} range={product['price']['range_twd']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

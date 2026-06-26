import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REFERENCE_PATH = PROJECT_ROOT / "data" / "followme_reference.json"


def load_followme_reference(path: Path = DEFAULT_REFERENCE_PATH) -> Dict[str, Any]:
    if not path.exists():
        return {"updated_at": "", "products": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"updated_at": "", "products": []}


def get_followme_products() -> List[Dict[str, Any]]:
    data = load_followme_reference()
    products = data.get("products", [])
    return products if isinstance(products, list) else []


def build_followme_prompt_section(path: Path = DEFAULT_REFERENCE_PATH) -> str:
    data = load_followme_reference(path)
    products = data.get("products", [])
    if not products:
        return ""

    updated_at = data.get("updated_at") or "未知"
    lines = [
        "",
        "---",
        "## 每日更新 FollowMe 型號規格價格表",
        f"資料更新時間：{updated_at}",
        "用途：只用於 FollowMe 分支的型號輔助判斷；照片價牌仍是最高優先，禁止用此表覆蓋清楚可見的標籤價格。",
    ]
    for item in products:
        name = item.get("name", "")
        aliases = " / ".join(item.get("aliases", []))
        model_codes = ", ".join(item.get("model_codes", []))
        specs = item.get("specs", {})
        price = item.get("price", {})
        price_range = price.get("range_twd", [])
        observed = ", ".join(str(p) for p in price.get("observed_twd", []))
        spec_text = (
            f"{specs.get('size_inch', '')}吋、"
            f"{specs.get('resolution_label', '')} {specs.get('resolution', '')}、"
            f"{specs.get('panel', '')}、"
            f"{specs.get('refresh_rate', '')}、"
            f"{specs.get('response_time', '')}"
        )
        lines.append(f"- {name}：{aliases}；型號碼 {model_codes}；規格 {spec_text}；常見售價 {price_range}；觀測價 {observed}。")
    lines.extend([
        "FollowMe 判斷提醒：",
        "- 32吋 FHD / M5 / S32FM50x / 價格約 9,999-10,990，多半是 FollowMe M5 32\"。",
        "- 32吋 4K / M7 / M70F / S32FM70x / 價格約 12,990-15,990，多半是 FollowMe M7 32\"。",
        "- 43吋 4K / FollowMe Pro / S43FM70x / 價格約 16,888-17,990，多半是 FollowMe Pro M7 43\"。",
        "---",
    ])
    return "\n".join(lines)


def reference_is_stale(path: Path = DEFAULT_REFERENCE_PATH, max_age_hours: int = 24) -> bool:
    data = load_followme_reference(path)
    updated_at = data.get("updated_at")
    if not updated_at:
        return True
    try:
        updated = datetime.fromisoformat(updated_at)
    except ValueError:
        return True
    return (datetime.now(updated.tzinfo) - updated).total_seconds() > max_age_hours * 3600

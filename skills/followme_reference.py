import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from skills.model_catalog_rules import FOLLOWME_BUNDLES, FOLLOWME_MODELS


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
    updated_at = data.get("updated_at") or "未知"
    lines = [
        "",
        "---",
        "## FollowMe 套裝型號與每日價格參考",
        f"資料更新時間：{updated_at}",
        "身分鐵律：價格只能檢查讀值合理性，絕對不能用價格決定 M5、M7、尺寸或 Pro。",
        "必須先確認同一台實機的 FollowMe 字樣或移動式長直架／圓形落地底座／附著托盤；旁邊文宣、螢幕廣告與裸面板型號都不能單獨成立 FollowMe。",
        "所有 FollowMe 都是 Smart 系列，不需要用 OSD 畫面證明。",
        "Pro 必須在同一台實機或其附著牌面清楚看到 Pro；同一面板 SKU 可能同時用於一般與 Pro 套裝，不得只靠 SKU 升級成 Pro。",
        "若已確認 FollowMe 實機但 M5／M7／尺寸／Pro 仍無法確認，輸出 FollowMe 型號未細分，不得猜最常見款。",
        "可用標準名稱：" + "、".join(FOLLOWME_MODELS) + "。",
        "面板對照（只在 FollowMe 實機身分先成立後使用）：",
    ]
    grouped: dict[str, list[str]] = {}
    for bundle in FOLLOWME_BUNDLES:
        grouped.setdefault(bundle.family_model, [])
        if bundle.panel_model not in grouped[bundle.family_model]:
            grouped[bundle.family_model].append(bundle.panel_model)
    for family, panel_models in grouped.items():
        lines.append(f"- {family}：{', '.join(panel_models)}。")
    lines.append(
        f"後端價格參考更新時間：{updated_at}；價格資料不放進型號判定提示，避免模型用常見價猜系列。"
    )
    lines.extend([
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

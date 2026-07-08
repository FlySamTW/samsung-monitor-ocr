import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from photo_rename_planner import make_plan, price_segment


def assert_equal(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def sample_row():
    return {
        "category": "單機",
        "model": "S27CG552EC",
        "price": "4990",
        "price_status": "high",
        "price_symbol": "↑",
    }


def test_price_symbol_by_period():
    row = sample_row()
    assert_equal(
        price_segment(row, "＄", period="202512", current_year=2026),
        "＄4990",
        "歷史年度不得保留比價符號",
    )
    assert_equal(
        price_segment(row, "＄", period="202605", current_year=2026),
        "↑＄4990",
        "當年度要保留比價符號",
    )


def test_make_plan_uses_period_for_price_symbol():
    with tempfile.TemporaryDirectory() as tmp:
        image_dir = Path(tmp)
        image_path = image_dir / "M-台北市-萬華區-TK3C-萬大-1005.jpg"
        image_path.write_bytes(b"fake")
        plan = make_plan(
            image_dir,
            {image_path.name: sample_row()},
            "202512",
            "＄",
            current_year=2026,
        )
    assert_equal(len(plan), 1, "應只產生一筆改名計畫")
    assert_equal(plan[0]["price"], "＄4990", "歷史年度計畫價格")
    if "-↑＄4990-" in plan[0]["target_name"]:
        raise AssertionError(f"歷史年度目標檔名不應含比價符號: {plan[0]['target_name']}")
    if "-＄4990-" not in plan[0]["target_name"]:
        raise AssertionError(f"歷史年度目標檔名應保留店內價格: {plan[0]['target_name']}")


def test_discontinued_legacy_symbol_becomes_unknown():
    row = sample_row()
    row["price_status"] = "discontinued"
    row["price_symbol"] = "-"
    assert_equal(
        price_segment(row, "＄", period="202605", current_year=2026),
        "？＄4990",
        "停產 legacy symbol must become unknown",
    )


def test_distant_view_filename_omits_model_and_price():
    with tempfile.TemporaryDirectory() as tmp:
        image_dir = Path(tmp)
        image_path = image_dir / "M-台中市-大甲區-SF-大甲-184.jpg"
        image_path.write_bytes(b"fake")
        plan = make_plan(
            image_dir,
            {
                image_path.name: {
                    "category": "遠景",
                    "view_type": "遠景",
                    "model": "",
                    "price": "",
                }
            },
            "202605",
            "＄",
            current_year=2026,
        )
    assert_equal(
        plan[0]["target_name"],
        "M-202605-台中市-大甲區-SF-大甲-遠景-184.jpg",
        "遠景 filename",
    )


def test_other_brand_model_is_kept_in_filename():
    with tempfile.TemporaryDirectory() as tmp:
        image_dir = Path(tmp)
        image_path = image_dir / "M-台中市-西區-TK3C-公益-88.jpg"
        image_path.write_bytes(b"fake")
        plan = make_plan(
            image_dir,
            {
                image_path.name: {
                    "category": "單機",
                    "view_type": "單機",
                    "model": "它牌(ACER)",
                    "price": "6990",
                }
            },
            "202605",
            "＄",
            current_year=2026,
        )
    assert_equal(
        plan[0]["target_name"],
        "M-202605-台中市-西區-TK3C-公益-單機-它牌(ACER)-＄6990-88.jpg",
        "它牌 filename",
    )


if __name__ == "__main__":
    test_price_symbol_by_period()
    test_make_plan_uses_period_for_price_symbol()
    test_discontinued_legacy_symbol_becomes_unknown()
    test_distant_view_filename_omits_model_and_price()
    test_other_brand_model_is_kept_in_filename()
    print("photo_rename_planner historical price-symbol tests passed")

from __future__ import annotations

import unittest

from skills.audit_fields import (
    followme_models_equivalent,
    followme_variant_evidence_reasons,
)
from skills.followme_reference import build_followme_prompt_section
from skills.model_catalog_rules import (
    FOLLOWME_MODELS,
    FOLLOWME_UNRESOLVED,
    extract_samsung_models,
    normalize_confirmed_followme_model,
    normalize_followme_family,
    normalize_samsung_model,
    resolve_followme_model,
)
from skills.model_validation import (
    safe_known_model_correction,
    strict_known_model,
    unique_known_model_completion,
)
from skills.official_price import OfficialPriceManager
from tools.photo_rename_planner import canonical_followme_model


class ModelCatalogRulesTest(unittest.TestCase):
    def test_prose_model_and_price_do_not_merge_into_a_fake_model(self):
        narration = (
            "中央主角正下方價牌清楚標示型號 S24D362GAC，"
            "會員售價 3,490 元。"
        )
        self.assertEqual(extract_samsung_models(narration), ["S24D362GAC"])
        self.assertEqual(extract_samsung_models("S24D362GAC"), ["S24D362GAC"])

    def test_official_code_normalization_preserves_model_suffix(self):
        self.assertEqual(normalize_samsung_model("LS27HG806EFXZW"), "S27HG806EF")
        self.assertEqual(normalize_samsung_model("LS32FM703UCXZW"), "S32FM703UC")
        self.assertEqual(normalize_samsung_model("LC34G55TWWCXZW"), "C34G55TWWC")

        manager = OfficialPriceManager.__new__(OfficialPriceManager)
        self.assertEqual(manager._extract_best_short_model("LS27HG806EFXZW"), "S27HG806EF")

    def test_catalog_matching_is_exact_or_uniquely_bounded(self):
        models = ["S27HG806EF", "S27HG807EF", "S32FM703UC"]
        self.assertEqual(strict_known_model("LS27HG806EFXZW", models), "S27HG806EF")
        self.assertEqual(unique_known_model_completion("S27HG806", models), "S27HG806EF")
        self.assertEqual(safe_known_model_correction("S27HG806E8", models), "S27HG806EF")
        self.assertIsNone(safe_known_model_correction("S27HG80", models))
        self.assertIsNone(safe_known_model_correction("S27HG806DF", ["S27HG806EF", "S27HG806CF"]))

    def test_all_followme_families_and_shared_panel_rules(self):
        self.assertEqual(len(FOLLOWME_MODELS), 6)
        self.assertEqual(normalize_followme_family('FollowMe Pro M7 32"'), 'FollowMe Pro M7 32"')
        self.assertEqual(normalize_confirmed_followme_model("S32FM703UC"), 'FollowMe M7 32"')
        self.assertEqual(normalize_confirmed_followme_model("S43FM703UC"), 'FollowMe M7 43"')
        self.assertFalse(followme_models_equivalent('FollowMe Pro M7 32"', "S32FM703UC"))
        self.assertFalse(followme_models_equivalent('FollowMe Pro M7 43"', "S43FM703UC"))
        self.assertEqual(
            resolve_followme_model("同一台規格牌寫 FollowMe Pro M7 43 吋，面板 S43FM703UC"),
            'FollowMe Pro M7 43"',
        )
        self.assertEqual(canonical_followme_model("FollowMe 4K"), FOLLOWME_UNRESOLVED)
        self.assertEqual(canonical_followme_model('FollowMe M7 43"'), 'FollowMe M7 43"')
        self.assertEqual(canonical_followme_model('FollowMe Pro M7 32"'), 'FollowMe Pro M7 32"')

    def test_price_and_size_do_not_prove_pro(self):
        record = {
            "model": 'FollowMe Pro M7 43"',
            "price": "17990",
            "thinking": "同一台 FollowMe 43 吋螢幕，面板型號 S43FM703UC，價牌 17,990。",
        }
        self.assertEqual(
            followme_variant_evidence_reasons(record),
            ["followme_pro_identity_evidence_missing"],
        )
        record["thinking"] = "同一台實機附著規格牌清楚寫 FollowMe Pro M7 43 吋。"
        self.assertEqual(followme_variant_evidence_reasons(record), [])
        record["thinking"] = "主角是一般 FollowMe；旁邊活動文宣寫 FollowMe Pro M7 43 吋。"
        self.assertEqual(
            followme_variant_evidence_reasons(record),
            ["followme_pro_identity_evidence_missing"],
        )

    def test_prompt_states_smart_and_no_price_identity(self):
        prompt = build_followme_prompt_section()
        self.assertIn("所有 FollowMe 都是 Smart 系列，不需要用 OSD 畫面證明", prompt)
        self.assertIn("價格只能檢查讀值合理性", prompt)
        self.assertIn("價格資料不放進型號判定提示", prompt)
        self.assertNotIn("常見售價", prompt)
        self.assertIn('FollowMe M5 27"', prompt)
        self.assertIn('FollowMe Pro M7 32"', prompt)


if __name__ == "__main__":
    unittest.main()

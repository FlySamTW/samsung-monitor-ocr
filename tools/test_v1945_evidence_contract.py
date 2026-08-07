import csv
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import samsung_ocr_batch_processor as batch

from skills.audit_fields import (
    EVIDENCE_GUARD_REVISION,
    FOLLOWME_TW_EARLIEST_YEAR,
    KNOWN_SOURCE_EXPECTATIONS,
    apply_human_audited_pixel_authority,
    evidence_contract_decision,
    finalize_three_pass_outcome,
    followme_variant_evidence_reasons,
    immediate_retry_decision,
    narrated_followme_physical_cues,
    narration_evidence_consistency_reasons,
    narration_has_followme_subject_counterevidence,
    narration_has_positive_followme_identity,
    narration_has_unmistakable_followme_fixture,
    _label_ownership_conflicts_with_narration,
    _historical_same_card_raw_recovery,
    _narration_supports_only_one_complete_monitor,
    validate_evidence_contract,
)
from tools.prepare_drive_upload_manifest import (
    classify_file,
    load_complete_auto_verified_names,
    load_v1945_trace_names,
)
from tools.rerun_questionable_records import is_complete_auto_verified
from tools.finalize_existing_three_pass_reviews import (
    _recover_clean_single_tail_after_restart,
    _recover_known_authority_after_restart,
)
from skills.batch_orchestrator import BatchOrchestrator, _append_v1945_trace, cross_photo_duplicate_core
from skills.runtime_health_gate import review_prompt_leak_reasons
from skills.model_validation import (
    has_photo_label_model_evidence,
    recover_pipeline_unlisted_model_candidate,
    resolve_photo_label_model_candidate,
    unique_embedded_known_model,
    unique_known_first_letter_alternative,
    unique_known_model_completion,
)
from samsung_ocr_batch_processor import (
    _merge_v1945_json_objects,
    _stream_narration_preview,
    build_v1945_response_format,
    new_request_id,
    request_binding_tail,
    validate_request_binding,
    validate_v1945_response_shape,
)


def evidence(count, unique, ownership="not_visible", physical=None):
    return {
        "complete_screen_count": count,
        "unique_main": unique,
        "label_ownership": ownership,
        "followme_physical_evidence": physical or [],
    }


class EvidenceContractTests(unittest.TestCase):
    RISK_650_SHA = "9e182f053a3c893a5c6a791d0abfb52e97eb52b945b0beeb962178d49025e549"
    FRAME_940_SHA = "d69c226c34a43da94bf624b5d1640f6552f0eec22dc2d1e37a6c62a777c6828f"
    FRAME_942_SHA = "d96292fc2c3050e9830247bc23c614072e63658c4acc1f11ba853d334d8256d2"
    FRAME_943_SHA = "c0dab61862e5b61bee09baa479b470876f38e4c7bfd742bcbf003a131e22490c"
    WIDE_1528_SHA = "50b7524736f05c39b2180b3c8240e18fab5a2f737929e73e7dee3b447ee6943f"
    FRAME_649_SHA = "9bf9e2e855f785d5e091b76c98ac087063413c1bf4bf403ed104b2c393f78ba5"
    FRAME_668_SHA = "17a98b95ebaebf4b7203d4e3fee4721650b5da9a248b77733f77d9594a9db871"
    FRAME_673_SHA = "76e461cddc915c2e3b92bdc942e2c94cf27d013fe0ca9021c95f3c52094d0016"
    FRAME_674_SHA = "c9bbac284fec04529de8991134f14020cd74edebd597405a9a0612670173caf0"
    FRAME_1257_SHA = "d48231cb464540aa0ea5816fe9e6b238547a6292254c6513606d786f101fc4a7"
    WIDE_FOLLOWME_1467_SHA = "46dca52b5b33bf720300723703bac2bcab2120ee1b850803ad28b56b2464bab0"
    YOUCHANG_1148_SHA = "688233fe7652e64469feab5e8d4a97dbeae224fd25df778e3221fce6da51c844"
    TAINAN_438_SHA = "67e134c5a48752627a8445fa0933bc203a2e5c3aa2ef4f10639eeccea4de27c4"
    TAINAN_1063_SHA = "1066f8575b45442395537bc609adf39b5756c0b3326ba44b34e96fd0f2c9019c"
    TAINAN_231_SHA = "0886bdb903c560cfd548830f4b89e81c16798e6fc42334e686022a74838d888b"
    CHIAYI_199_SHA = "3880911cd0d8c55abf2de05dcd007f31315917c91f29a793a5c3966ef4771333"
    CHANGHUA_234_SHA = "f8a38f32e21f0e01c3047e64f70c4b37008d382beacd3e1ecf188a0415423e8d"
    SMS_348_SHA = "31a0244a9f6186e483158f5ae80cbdd7f501383ae8eb222fde3a0262a801a85c"
    SMS_356_SHA = "9eae0b812784f4f72ac57d8ac2043b28e57de3e1a0abde3fc82ffc69fabc40a9"
    LALAPORT_301_SHA = "46efc7264cfde6dd35e82caef9c2c8182613d1acd231a8ada092efd3b585dc66"
    LIANGXING_765_SHA = "7c2abf080d2e4232895c169a5067c77cf01490bc4c017bdc79ed0cf5bbf295fd"
    AUDITED_202606_EXPECTATIONS = {
        "729f470ae5cd2f1d147904959fa777f42f45910cfe352c345477f320a9757230":
            ("單機", 1, None, None, True),
        "8be32ccfe71d8bb7096276248057e42f95a933fad4228c8f8cdde642cf51d06b":
            ("遠景", 3, None, None, False),
        "9943022d069a3c556a2da2106cf9600d93776c87ec73ec3ff04107bdcefe97c4":
            ("單機", 1, "S27FG532EC", 5790, False),
        "3d977798d9d7a275e97ebe4c8b9099a7cf71877fe6ef514e60b08bd96c50771a":
            ("單機", 1, "S32DM803UC", 19900, False),
        "c65f64217ba5181f429df00b21a473ef6bb78e444c18b6197dfe11e9bb01be87":
            ("遠景", 4, None, None, False),
        "e5d7157216f3700895160913bf6a1104959b0e02d55d751c90714029a5c6dae8":
            ("遠景", 4, None, None, False),
        "7ebacc47f8782b02702e6dabccf1215c8032c8f10dbede8e4b1bb03c685df8c5":
            ("遠景", 8, None, None, False),
        "1eba26f5209605f30559627f02fdf9e4a3dd3d35707dceb29a7c5741744e7185":
            ("遠景", 3, None, None, False),
        "74d17bdea3b9d6b5908b42ebce7ca1c461020473276ef4f1a35f96daa3e9a024":
            ("單機", 2, None, None, False),
    }

    def test_staging_timestamp_never_replaces_the_source_period(self):
        staging = (
            r"D:\00_商化\00_已OCR照片\_ocr_staging\20260720_200139"
            r"\202601_商化照片-202601_6403a632"
        )
        self.assertEqual(batch.infer_period_from_text(staging), "202601")
        self.assertEqual(batch.infer_period_from_text(r"商化照片-202606"), "202606")
        self.assertEqual(batch.infer_period_from_text(r"run\20260720_200139"), "")

    def test_catalog_loading_is_independent_of_process_working_directory(self):
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                os.chdir(temp_dir)
                catalog = batch.load_model_catalog()
            finally:
                os.chdir(original_cwd)
        self.assertIn("S32DG702EC", catalog)

    def test_empty_catalog_never_erases_photo_model(self):
        model, available = batch.revalidate_model_without_empty_catalog_erasure(
            "S32DG702EC", []
        )
        self.assertEqual(model, "S32DG702EC")
        self.assertFalse(available)

    def test_resolution_fragment_cannot_become_retail_price(self):
        self.assertTrue(
            batch.price_looks_like_display_spec(
                "35424",
                "下方價牌寫「35,424 2160」，螢幕播放 Samsung Follow Me 4K。",
            )
        )
        self.assertFalse(
            batch.price_looks_like_display_spec(
                "12990",
                "同一實機價牌清楚寫 32吋 4K，售價 12,990 元。",
            )
        )

    def test_generic_followme_4k_does_not_prove_m5_32_variant(self):
        row = {
            "period": "202601",
            "view_type": "單機",
            "category": "單機",
            "model": 'FollowMe M5 32"',
            "price": "12990",
            "quality_issue": "無",
            "thinking": (
                "同一實機貼紙只寫 Samsung Follow Me 4K；白色直立支架、"
                "圓形底座與附著託盤清楚可見，價牌寫 12,990 元。"
            ),
            **evidence(
                1,
                True,
                "matched",
                [
                    {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
                    {"cue": "round_base", "same_subject": True, "strength": "strong"},
                    {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
                ],
            ),
        }
        self.assertIn(
            "followme_specific_identity_evidence_missing",
            followme_variant_evidence_reasons(row),
        )
        decision = immediate_retry_decision(row, 1, [], 3)
        self.assertFalse(decision["retry"])
        self.assertTrue(decision["verified"])
        self.assertTrue(row["ordered_followme_early_exit"])
        self.assertTrue(row["followme_family_confirmed"])
        self.assertEqual(row["model"], "FollowMe 型號未細分")
        self.assertEqual(row["price"], "12990")

    def test_ordered_followme_family_survives_denied_price_without_extra_round(self):
        narration = (
            "中央偏左同一台螢幕有白色直立支架、完整圓形落地底座與附著托盤，"
            "螢幕上方同主體標籤寫 Samsung Follow Me 4K；未見完整 S 開頭型號與可讀價格牌。"
        )
        physical = [
            {"cue": "direct_followme_branding_on_unit", "same_subject": True, "strength": "direct"},
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
            {"cue": "round_base", "same_subject": True, "strength": "strong"},
            {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
        ]
        row = {
            "period": "202601",
            "view_type": "單機",
            "category": "單機",
            "model": "FollowMe 型號未細分",
            "price": None,
            "quality_issue": "沒有價格牌",
            "price_conflict_detected": True,
            "thinking": narration,
            **evidence(1, True, "matched", physical),
        }
        decision = immediate_retry_decision(row, 1, [], 3)
        self.assertTrue(decision["verified"])
        self.assertFalse(decision["retry"])
        self.assertTrue(row["ordered_followme_early_exit"])
        self.assertTrue(row["ordered_followme_price_withdrawn"])
        self.assertEqual(row["model"], "FollowMe 型號未細分")
        self.assertIsNone(row["price"])

    def test_generic_m7_tabletop_card_cannot_terminally_lock_followme_on_first_pass(self):
        narration = (
            "我看到本輪結論：單機，無型號，9,990元。判讀依據：中央偏左的"
            "螢幕下方有白色直立支架與完整圓形底座，連著附著托盤與 "
            "Samsung Smart Monitor M7 商品卡，卡上可逐字讀取 32 與 G83。"
            "其他螢幕為背景，無足夠實體 FollowMe 證據。"
        )
        row = {
            "file_name": "M-台中市-大里區-SF-大里-194.jpg",
            "period": "202605",
            "view_type": "單機",
            "category": "單機",
            "model": None,
            "price": "9990",
            "quality_issue": "不合格-沒有規格牌",
            "thinking": narration,
            "narration": narration,
            **evidence(2, True, "matched", [
                {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
                {"cue": "round_base", "same_subject": True, "strength": "strong"},
                {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
            ]),
        }
        decision = immediate_retry_decision(row, 1, [], 3)
        self.assertTrue(decision["retry"])
        self.assertFalse(decision["verified"])
        self.assertFalse(row["ordered_followme_early_exit"])
        self.assertFalse(row.get("followme_family_confirmed", False))
        self.assertTrue(
            row["generic_smart_monitor_requires_independent_followme_confirmation"]
        )

    def test_generic_m7_raw_output_cannot_lock_followme_on_lifetime_attempt_two(self):
        raw = {
            "narration": (
                "型號為 Samsung Smart Monitor M7，價格 9,990；"
                "未見同機 FollowMe 直接品牌。"
            ),
            "model": "Samsung Smart Monitor M7",
            "price": "9990",
        }
        row = {
            "file_name": "M-台中市-大里區-SF-大里-194.jpg",
            "period": "202605",
            "view_type": "單機",
            "category": "單機",
            "model": None,
            "price": "9990",
            "quality_issue": "不合格-沒有規格牌",
            "thinking": (
                "中央螢幕下方被判成白色直立支架、圓形底座與附著託盤；"
                "其他螢幕為背景。"
            ),
            "raw_model_output": json.dumps(raw, ensure_ascii=False),
            "raw_objects": [json.dumps(raw, ensure_ascii=False)],
            **evidence(2, True, "matched", [
                {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
                {"cue": "round_base", "same_subject": True, "strength": "strong"},
                {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
            ]),
        }
        decision = immediate_retry_decision(row, 2, [], 3)
        self.assertTrue(decision["retry"])
        self.assertFalse(decision["verified"])
        self.assertFalse(row["ordered_followme_early_exit"])
        self.assertFalse(row.get("followme_family_confirmed", False))
        self.assertTrue(
            row["generic_smart_monitor_requires_independent_followme_confirmation"]
        )

    def test_runtime_user_prompt_rejects_fm_and_m7_as_standalone_followme_proof(self):
        source = Path(batch.__file__).read_text(encoding="utf-8")
        self.assertIn("FM／S32FM／M7／M5 名稱本身不成立", source)
        self.assertNotIn("FollowMe 字樣、FM 型號代碼", source)

    def test_youchang_1651_black_stand_counterevidence_repairs_same_pass_json(self):
        physical = [
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
            {"cue": "round_base", "same_subject": True, "strength": "strong"},
            {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
        ]
        narration = (
            "我看到本輪結論：單機，型號 S27D300GAC，價格 3,290。"
            "中央偏左的螢幕有黑色直立支架與完整圓形底座，下方價牌明確寫 "
            "SAMSUNG 27型 S3平面螢幕 S27D300GAC 會員價 3,290，與實體支架連接。"
            "左側與右側螢幕僅局部可見，未完整入鏡。無 FollowMe 字樣或移動式支架線索。"
        )
        row = {
            "file_name": "M-高雄市-楠梓區-TK3C-右昌-1651.jpg",
            "period": "202602",
            "view_type": "單機",
            "category": "單機",
            "model": "S27D300GAC",
            "price": "3290",
            "thinking": narration,
            "narration": narration,
            **evidence(3, True, "matched", physical),
        }
        decision = immediate_retry_decision(row, 1, [], 3)
        self.assertTrue(narration_has_followme_subject_counterevidence(narration))
        self.assertFalse(decision["retry"])
        self.assertTrue(decision["verified"])
        self.assertFalse(row["ordered_followme_early_exit"])
        self.assertNotIn("ordered_followme_family_lock", row)
        self.assertEqual(row["model"], "S27D300GAC")
        self.assertEqual(row["complete_screen_count"], 1)
        self.assertEqual(row["followme_physical_evidence"], [])
        self.assertTrue(row["same_pass_owned_single_reconciled"])
        self.assertEqual(row["reconciled_complete_screen_count_from"], 3)

    def test_explicit_fixture_denials_cannot_verify_structured_fixture_claims(self):
        narration = (
            "主角是一般桌上型螢幕，沒有白色直立支架、沒有圓形底座，"
            "也沒有附著托盤；未見正面 FollowMe 標牌。"
        )
        row = {
            "period": "202602",
            "view_type": "單機",
            "category": "單機",
            "model": "S27D300GAC",
            "price": "3290",
            "thinking": narration,
            "narration": narration,
            **evidence(1, True, "matched", [
                {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
                {"cue": "round_base", "same_subject": True, "strength": "strong"},
                {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
            ]),
        }
        decision = immediate_retry_decision(row, 1, [], 3)
        self.assertTrue(decision["retry"])
        self.assertFalse(decision["verified"])
        self.assertFalse(row["ordered_followme_early_exit"])
        self.assertIn("narration_denies_structured_followme_fixture", decision["reasons"])
    def test_youchang_1050_background_negative_does_not_veto_positive_fixture(self):
        physical = [
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
            {"cue": "round_base", "same_subject": True, "strength": "strong"},
            {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
        ]
        narration = (
            "中央偏左的螢幕下方有白色直立支架與完整圓形底座，支架上附著託盤，"
            "託盤上有可逐字讀取的型號與價格，且與螢幕正下方空間對齊。"
            "其他螢幕雖完整入鏡，但無實體 FollowMe 候選，故不影響商業主角判斷。"
        )
        row = {
            "file_name": "M-高雄市-楠梓區-SF-右昌-1050.jpg",
            "period": "202602",
            "view_type": "單機",
            "category": "單機",
            "model": "S27CG552EC",
            "price": "4990",
            "thinking": narration,
            "narration": narration,
            **evidence(3, True, "matched", physical),
        }
        decision = immediate_retry_decision(row, 1, [], 3)
        self.assertFalse(narration_has_followme_subject_counterevidence(narration))
        self.assertFalse(decision["retry"])
        self.assertTrue(decision["verified"])
        self.assertTrue(row["ordered_followme_early_exit"])
        self.assertEqual(row["model"], "FollowMe 型號未細分")
    def test_explicit_same_pass_followme_variant_remains_supported(self):
        row = {
            "model": 'FollowMe M7 32"',
            "thinking": (
                "同一實機規格牌清楚寫 FollowMe M7 32吋，"
                "白色直立支架與圓形底座屬於同一台。"
            ),
        }
        self.assertEqual(followme_variant_evidence_reasons(row), [])

    def test_wujia_1267_ordered_family_lock_preserves_owned_ordinary_sku_and_price(self):
        narration = (
            "五甲 1267 的同一台主角有白色直立支架、完整圓形底座與附著托盤，"
            "附著商品卡清楚寫 Samsung S32FM803UC，售價 14,990 元。"
        )
        row = {
            "file_name": "M-高雄市-鳳山區-SF-五甲-1267.jpg",
            "period": "202602",
            "view_type": "單機",
            "category": "單機",
            "model": "S32FM803UC",
            "price": "14990",
            "quality_issue": "無",
            "thinking": narration,
            **evidence(1, True, "matched", [
                {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
                {"cue": "round_base", "same_subject": True, "strength": "strong"},
                {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
            ]),
        }
        decision = immediate_retry_decision(row, 1, [], 3)
        self.assertTrue(decision["verified"])
        self.assertTrue(row["ordered_followme_early_exit"])
        self.assertTrue(row["followme_family_confirmed"])
        self.assertEqual(row["model"], "S32FM803UC")
        self.assertEqual(row["price"], "14990")

    def test_wujia_1267_frozen_generic_family_recovers_bound_raw_sku(self):
        narration = (
            "中央偏左的同一台螢幕有白色直立支架、完整圓形底座與附著托盤，"
            "附著商品卡清楚寫 Samsung S32FM803UC，售價 14,990 元。"
        )
        raw_value = {
                "view_type": "單機",
                "model": "S32FM803UC",
                "price": "14990",
            }
        raw = json.dumps(
            raw_value,
            ensure_ascii=False,
        )
        row = {
            "file_name": "M-高雄市-鳳山區-TK3C-五甲-1267.jpg",
            "period": "202602",
            "view_type": "單機",
            "category": "單機",
            "model": "FollowMe 型號未細分",
            "price": "14990",
            "quality_issue": "無",
            "thinking": narration,
            "narration": narration,
            "raw_objects": [{"raw": raw, "value": raw_value}],
            **evidence(1, True, "matched", [
                {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
                {"cue": "round_base", "same_subject": True, "strength": "strong"},
                {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
            ]),
        }
        decision = immediate_retry_decision(row, 1, [], 3)
        self.assertTrue(decision["verified"])
        self.assertTrue(row["ordered_followme_early_exit"])
        self.assertTrue(row["ordered_followme_raw_sku_recovered"])
        self.assertEqual(row["model"], "S32FM803UC")
        self.assertEqual(row["price"], "14990")

    def test_followme_fixture_conclusion_only_cannot_rescue_raw_sku(self):
        narration = (
            "我看到本輪結論：單機，型號 S32FM803UC，價格 14,990。"
            "主體有白色直立支架、圓形底座與托盤，但看不清任何標籤。"
        )
        raw_value = {
            "view_type": "單機",
            "model": "S32FM803UC",
            "price": "14990",
        }
        row = {
            "period": "202602",
            "view_type": "單機",
            "category": "單機",
            "model": "FollowMe 型號未細分",
            "price": "14990",
            "quality_issue": "無",
            "thinking": narration,
            "narration": narration,
            "raw_objects": [{"value": raw_value}],
            **evidence(1, True, "matched", [
                {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
                {"cue": "round_base", "same_subject": True, "strength": "strong"},
                {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
            ]),
        }
        decision = immediate_retry_decision(row, 1, [], 3)
        self.assertTrue(decision["verified"])
        self.assertNotIn("ordered_followme_raw_sku_recovered", row)
        self.assertEqual(row["model"], "FollowMe 型號未細分")

    def test_sf_fengshan_1064_fixture_only_conclusion_cannot_invent_variant(self):
        narration = (
            "本輪結論：FollowMe M7 32吋，售價 12,990 元。"
            "同一台主角可見白色直立支架、完整圓形底座與附著托盤；"
            "沒有任何價牌、商品卡、標籤或側標清楚印出確切 M5、M7、Pro 或面板型號。"
        )
        row = {
            "file_name": "M-高雄市-鳳山區-SF-鳳山-1064.jpg",
            "period": "202602",
            "view_type": "單機",
            "category": "單機",
            "model": 'FollowMe M7 32"',
            "price": "12990",
            "quality_issue": "無",
            "thinking": narration,
            **evidence(1, True, "matched", [
                {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
                {"cue": "round_base", "same_subject": True, "strength": "strong"},
                {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
            ]),
        }
        decision = immediate_retry_decision(row, 1, [], 3)
        self.assertTrue(decision["verified"])
        self.assertTrue(row["followme_family_confirmed"])
        self.assertEqual(row["model"], "FollowMe 型號未細分")

    def _multiscreen_single(self, **updates):
        row = {
            "period": "202601", "view_type": "單機", "category": "單機",
            "model": "S27D300GAC", "price": "3090",
            "thinking": "中央有一台主角螢幕，規格與價格牌都屬於它自己。",
            "independent_pass": True, "prior_answer_exposed": False,
            "prompt_contamination": False, "runtime_health": {"healthy": True},
            **evidence(3, True, "matched", []),
        }
        row.update(updates)
        return row

    def test_bounded_202606_visual_audits_override_all_nine_exhausted_rows(self):
        for image_hash, expected in self.AUDITED_202606_EXPECTATIONS.items():
            view_type, count, model, price, followme = expected
            with self.subTest(image_hash=image_hash):
                authority = KNOWN_SOURCE_EXPECTATIONS[image_hash]
                self.assertEqual(authority["view_type"], view_type)
                self.assertEqual(authority["complete_screen_count"], count)
                self.assertEqual(authority.get("model"), model)
                self.assertEqual(authority.get("price"), price)

                passes = []
                for attempt in (1, 2, 3):
                    passes.append({
                        "period": "202606",
                        "ocr_attempt": attempt,
                        "input_image_sha256": image_hash,
                        "request_id_verified": True,
                        "independent_pass": True,
                        "prior_answer_exposed": False,
                        "prompt_contamination": False,
                        "view_type": "單機" if view_type == "遠景" else "遠景",
                        "category": "單機" if view_type == "遠景" else "遠景",
                        "model": "WRONG",
                        "price": "999999",
                        **evidence(3, view_type != "遠景", "ambiguous", []),
                    })

                self.assertTrue(
                    apply_human_audited_pixel_authority(passes[2], passes[:2], 3)
                )
                self.assertEqual(passes[2]["view_type"], view_type)
                self.assertEqual(passes[2]["complete_screen_count"], count)
                self.assertEqual(passes[2]["model"], model)
                self.assertEqual(passes[2]["price"], price)
                self.assertEqual(passes[2]["followme_family_confirmed"], followme)

    def test_current_wide_single_uses_one_followme_check_not_ritual_three_passes(self):
        row = self._multiscreen_single(
            model=None,
            price=None,
            unique_main=False,
            label_ownership="not_visible",
            thinking=(
                "我看到三台以上完整螢幕，沒有可歸屬的型號或價格，"
                "也沒有同主體 FollowMe 實體。"
            ),
        )
        first = immediate_retry_decision(dict(row), 1, [], 3)
        self.assertTrue(first["retry"])
        self.assertFalse(first["verified"])
        self.assertIn("寬景單機候選需第二輪確認是否為 FollowMe", first["reasons"])

        second_row = dict(row)
        second = immediate_retry_decision(second_row, 2, [dict(row)], 3)
        self.assertTrue(second["verified"])
        self.assertFalse(second["retry"])
        self.assertFalse(second["unresolved"])
        self.assertEqual(second_row["view_type"], "遠景")
        self.assertIsNone(second_row["model"])
        self.assertIsNone(second_row["price"])
        self.assertTrue(second_row["wide_scene_resolved_without_ritual_third_pass"])

    def test_tk3c_nanzi_1585_owned_side_label_beats_wide_count_and_matches_price_card(self):
        narration = (
            "我看到中央主角右上側直接附著的規格側標有完整 S27CG552EC；"
            "下方雖有多張不同型號價牌，其中與主角空間對齊的一張也印有"
            "完全相同的 S27CG552EC，實售價為 4,990 元。"
        )
        row = self._multiscreen_single(
            period="202602",
            file_name="M-高雄市-楠梓區-TK3C-楠梓-1585.jpg",
            model="S27CG552EC",
            price="4990",
            complete_screen_count=3,
            unique_main=True,
            label_ownership="matched",
            thinking=narration,
            narration=narration,
        )

        decision = immediate_retry_decision(row, 1, [], 3)

        self.assertTrue(decision["verified"])
        self.assertFalse(decision["retry"])
        self.assertFalse(decision["unresolved"])
        self.assertEqual(row["view_type"], "單機")
        self.assertEqual(row["model"], "S27CG552EC")
        self.assertEqual(row["price"], "4990")
        self.assertNotIn("wide_scene_resolved_without_ritual_third_pass", row)
        self.assertNotIn(
            "沒有 FollowMe 實體證據的三台以上完整螢幕應定案遠景",
            decision["reasons"],
        )

        prompt = (
            Path(__file__).resolve().parents[1]
            .joinpath("samsung_ocr_prompt.txt")
            .read_text(encoding="utf-8")
        )
        self.assertIn("必須逐張找印有與側標完全相同 SKU 的卡", prompt)
        self.assertIn("保留側標鎖定的 model，但 price 必須填 null", prompt)

    def test_elife_281_followme_uncertainty_cannot_cross_sentence_into_owned_card(self):
        narration = (
            "我沒有看到白色落地支架，因此無法確認 FollowMe。"
            "主螢幕正下方有實體規格價牌，清楚標示 S27FG502SC 與 13,900 元，"
            "並且與主螢幕空間對齊。"
        )
        self.assertFalse(_label_ownership_conflicts_with_narration(narration))

    def test_side_label_model_survives_but_unmatched_multi_card_price_is_null(self):
        narration = (
            "我看到中央主角右上側附著的規格側標清楚寫 S27CG552EC。"
            "下方有多張價牌，但可讀的 6,990 元只印在另一型號的卡上，"
            "沒有任何同型號價牌可供主角使用。"
        )
        row = self._multiscreen_single(
            period="202602",
            model="S27CG552EC",
            price="6990",
            complete_screen_count=3,
            unique_main=True,
            label_ownership="matched",
            thinking=narration,
            narration=narration,
        )

        decision = immediate_retry_decision(row, 1, [], 3)

        self.assertTrue(decision["retry"])
        self.assertFalse(decision["verified"])
        self.assertEqual(row["view_type"], "單機")
        self.assertEqual(row["model"], "S27CG552EC")
        self.assertIsNone(row["price"])
        self.assertEqual(row["rejected_price_without_exact_model_card"], "6990")
        self.assertIn("2026 單機缺價格", decision["reasons"])

    def test_1032_partial_edge_neighbors_finish_on_first_owned_pass(self):
        narration = (
            "中央一台完整螢幕，型號 S27D300GAC，價格 3,090 元；"
            "左側與右側螢幕僅局部可見，外框未完整入鏡，不計為完整螢幕。"
        )
        first_row = self._multiscreen_single(thinking=narration)

        self.assertTrue(_narration_supports_only_one_complete_monitor(first_row))
        decision = immediate_retry_decision(first_row, 1, [], 3)

        self.assertFalse(decision["retry"])
        self.assertTrue(decision["verified"])
        self.assertFalse(decision["unresolved"])
        self.assertEqual(first_row["view_type"], "單機")
        self.assertEqual(first_row["complete_screen_count"], 1)
        self.assertNotIn("wide_scene_resolved_without_ritual_third_pass", first_row)

    def test_complete_owned_single_with_partial_neighbor_can_finish_at_count_two(self):
        row = self._multiscreen_single(
            complete_screen_count=2,
            thinking=(
                "我看到前景中央一台完整螢幕，左側螢幕外框被原圖左邊界截斷，"
                "右側螢幕外框也被原圖右邊界截斷，兩側鄰機都不完整。"
            ),
        )

        decision = immediate_retry_decision(row, 1, [], 3)

        self.assertFalse(decision["retry"])
        self.assertTrue(decision["verified"])
        self.assertEqual(decision["normalized_evidence"]["complete_screen_count"], 1)
        self.assertNotIn("敘述明確只有一台完整螢幕，結構完整台數必須為1", decision["reasons"])

    def test_partial_neighbor_count_two_still_retries_without_complete_single_identity(self):
        narration = (
            "我看到前景中央一台完整螢幕，右側另一台螢幕只有部分露出，"
            "其外框被原圖右邊界截斷，不完整。"
        )
        unsafe_updates = (
            {"model": ""},
            {"price": ""},
            {"label_ownership": "ambiguous"},
            {"unique_main": False},
            {"view_type": "遠景", "category": "遠景"},
        )

        for updates in unsafe_updates:
            with self.subTest(updates=updates):
                row = self._multiscreen_single(
                    complete_screen_count=2,
                    thinking=narration,
                    **updates,
                )
                decision = immediate_retry_decision(row, 1, [], 3)
                self.assertTrue(decision["retry"])
                self.assertFalse(decision["verified"])
                self.assertIn(
                    "敘述明確只有一台完整螢幕，結構完整台數必須為1",
                    decision["reasons"],
                )

    def test_partial_neighbor_count_three_is_reconciled_when_identity_is_owned(self):
        row = self._multiscreen_single(
            complete_screen_count=3,
            thinking=(
                "我看到前景中央一台完整螢幕，左右兩側鄰機都被原圖邊界裁切，"
                "其外框不完整。"
            ),
        )

        decision = immediate_retry_decision(row, 1, [], 3)

        self.assertFalse(decision["retry"])
        self.assertTrue(decision["verified"])
        self.assertEqual(row["complete_screen_count"], 1)
        self.assertTrue(row["same_pass_owned_single_reconciled"])

    def test_incompatible_background_marketing_family_retries(self):
        row = self._multiscreen_single(
            complete_screen_count=1,
            model="S27D392GAC",
            price="4290",
            thinking=(
                "主角價牌是 S27D392GAC 與 4,290 元；旁邊廣告有 Odyssey OLED G8，"
                "所以這是 Odyssey G8。"
            ),
        )

        decision = immediate_retry_decision(row, 1, [], 3)

        self.assertTrue(decision["retry"])
        self.assertIn("敘述借用了與主角型號不相容的背景產品系列", decision["reasons"])

    def test_null_identity_pixel_authority_finishes_truthfully(self):
        def authority_pass(attempt):
            return {
                "period": "202601",
                "ocr_attempt": attempt,
                "input_image_sha256": self.SMS_348_SHA,
                "request_id_verified": True,
                "independent_pass": True,
                "prior_answer_exposed": False,
                "prompt_contamination": False,
                "view_type": "遠景",
                "category": "遠景",
                "model": "S32DM803UC",
                "price": "39900",
                **evidence(3, False, "ambiguous", []),
            }

        history = [authority_pass(1), authority_pass(2)]
        current = authority_pass(3)
        current["followme_family_confirmed"] = True

        self.assertTrue(apply_human_audited_pixel_authority(current, history, 3))
        self.assertEqual(current["view_type"], "單機")
        self.assertEqual(current["complete_screen_count"], 1)
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])
        self.assertFalse(current["followme_family_confirmed"])
        self.assertEqual(current["quality_issue"], "不合格-沒有規格和價格牌")
        self.assertNotIn("None", current["thinking"])
        self.assertTrue(current["thinking"].startswith("我看到本輪結論：單機"))
        self.assertNotIn("所以……", current["thinking"])

    def test_non_followme_pixel_authority_clears_false_fixture_family(self):
        def authority_pass(attempt):
            return {
                "period": "202601",
                "ocr_attempt": attempt,
                "input_image_sha256": self.SMS_356_SHA,
                "request_id_verified": True,
                "independent_pass": True,
                "prior_answer_exposed": False,
                "prompt_contamination": False,
                "view_type": "單機",
                "category": "單機",
                "model": None,
                "price": "14900",
                "followme_family_confirmed": True,
                **evidence(
                    2,
                    True,
                    "matched",
                    [
                        {"cue": "round_base", "same_subject": True, "strength": "strong"},
                        {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
                    ],
                ),
            }

        history = [authority_pass(1), authority_pass(2)]
        current = authority_pass(3)
        current.update(
            price_status="high",
            price_symbol="↑",
            official_price=4990,
            price_diff_percent=198.6,
        )

        with patch(
            "skills.official_price.validate_ocr_price",
            return_value={
                "status": "high",
                "symbol": "↑",
                "official_price": 10900,
                "ocr_price": 14900,
                "diff_percent": 36.7,
            },
        ):
            self.assertTrue(apply_human_audited_pixel_authority(current, history, 3))
        self.assertEqual(current["complete_screen_count"], 1)
        self.assertEqual(current["model"], "S32DM803UC")
        self.assertEqual(current["price"], 14900)
        self.assertEqual(current["official_price"], 10900)
        self.assertEqual(current["price_diff_percent"], 36.7)
        self.assertFalse(current["followme_family_confirmed"])
        self.assertEqual(current["followme_physical_evidence"], [])

    def test_liangxing_765_three_calls_finalize_as_distant_without_fourth_call(self):
        def authority_pass(attempt, view_type, count, unique, ownership):
            return {
                "ocr_attempt": attempt,
                "input_image_sha256": self.LIANGXING_765_SHA,
                "request_id_verified": True,
                "independent_pass": True,
                "prior_answer_exposed": False,
                "prompt_contamination": False,
                "runtime_health": {"healthy": True, "reasons": []},
                "view_type": view_type,
                "category": view_type,
                "model": "S27CG552EC" if view_type == "單機" else None,
                "price": None,
                **evidence(count, unique, ownership, []),
            }

        first = authority_pass(1, "單機", 7, True, "matched")
        second = authority_pass(2, "單機", 1, True, "matched")
        third = authority_pass(3, "遠景", 3, False, "not_visible")

        self.assertTrue(apply_human_audited_pixel_authority(third, [first, second], 3))
        self.assertEqual(third["view_type"], "遠景")
        self.assertEqual(third["complete_screen_count"], 3)
        self.assertIsNone(third["model"])
        self.assertIsNone(third["price"])
        self.assertFalse(third["unique_main"])
        self.assertEqual(third["ocr_attempt"], 3)
        self.assertTrue(third["human_pixel_authority_applied"])
        self.assertEqual(
            third["adjudication_rule"],
            "three_pass_human_audited_pixel_authority",
        )

    def test_lalaport_followme_authority_drops_unsupported_variant_and_price(self):
        def authority_pass(attempt):
            return {
                "period": "202606",
                "ocr_attempt": attempt,
                "input_image_sha256": self.LALAPORT_301_SHA,
                "request_id_verified": True,
                "independent_pass": True,
                "prior_answer_exposed": False,
                "prompt_contamination": False,
                "view_type": "單機",
                "category": "單機",
                "model": "FollowMe Pro M7 43\"",
                "price": "12990",
                **evidence(1, True, "matched", []),
            }

        history = [authority_pass(1), authority_pass(2)]
        current = authority_pass(3)

        self.assertTrue(apply_human_audited_pixel_authority(current, history, 3))
        self.assertEqual(current["view_type"], "單機")
        self.assertEqual(current["complete_screen_count"], 1)
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])
        self.assertTrue(current["followme_family_confirmed"])
        self.assertEqual(
            {item["cue"] for item in current["followme_physical_evidence"]},
            {
                "direct_followme_branding_on_unit",
                "white_vertical_stand",
                "round_base",
                "attached_price_tray",
            },
        )

    def test_wide_owned_identity_disagreement_retries_without_erasing_current_evidence(self):
        row = self._multiscreen_single()
        conflicting = self._multiscreen_single(model="S27CG552EC", price="4990")
        current = dict(row)
        decision = immediate_retry_decision(current, 2, [conflicting], 3)
        self.assertFalse(decision["verified"])
        self.assertTrue(decision["retry"])
        self.assertFalse(decision["unresolved"])
        self.assertEqual(current["view_type"], "單機")
        self.assertEqual(current["model"], "S27D300GAC")
        self.assertEqual(current["price"], "3090")
        self.assertIn(
            "寬景唯一主角型號跨輪不一致，需完成有界複核",
            decision["reasons"],
        )

    def test_pre_followme_market_wide_scene_closes_as_distant_on_first_pass(self):
        row = self._multiscreen_single(
            period="201901",
            model=None,
            price=None,
            unique_main=False,
            label_ownership="not_visible",
            thinking=(
                "我看到三台以上完整螢幕，沒有唯一商業主角，"
                "也沒有可歸屬的型號或價格。"
            ),
        )
        first_row = dict(row)
        first = immediate_retry_decision(first_row, 1, [], 3)
        self.assertFalse(first["retry"])
        self.assertTrue(first["verified"])
        self.assertEqual(first_row["view_type"], "遠景")
        self.assertIsNone(first_row["model"])
        self.assertIsNone(first_row["price"])
        self.assertEqual(FOLLOWME_TW_EARLIEST_YEAR, 2024)

    def test_edge_cut_narration_cannot_claim_three_complete_monitors(self):
        row = self._multiscreen_single(
            thinking=(
                "我看到中央一台螢幕，左右各有一台螢幕，但左右鄰機都被照片邊界裁切，"
                "其他區域沒有額外完整螢幕，所以……這是一般單機。"
            )
        )
        decision = immediate_retry_decision(dict(row), 1, [], 3)
        self.assertFalse(decision["retry"])
        self.assertTrue(decision["verified"])
        self.assertEqual(decision["normalized_evidence"]["complete_screen_count"], 1)

    def test_edge_cut_two_sides_wording_cannot_claim_three_complete_monitors(self):
        row = self._multiscreen_single(
            thinking=(
                "我看到中央一台螢幕正下方有價牌，左右兩側螢幕被照片邊界裁切，"
                "全圖沒有其他完整螢幕，所以……這是一般單機。"
            )
        )
        decision = immediate_retry_decision(dict(row), 1, [], 3)
        self.assertFalse(decision["retry"])
        self.assertTrue(decision["verified"])
        self.assertEqual(decision["normalized_evidence"]["complete_screen_count"], 1)

    def test_wide_multiscreen_without_owned_identity_closes_as_distant_after_check(self):
        row = self._multiscreen_single(
            model=None,
            price=None,
            label_ownership="ambiguous",
            complete_screen_count=8,
            thinking="我看到一整排螢幕陳列，上方與下方均有完整螢幕，所以……這是一般單機。",
        )
        current = dict(row)
        decision = immediate_retry_decision(current, 2, [dict(row)], 3)
        self.assertTrue(decision["verified"])
        self.assertFalse(decision["unresolved"])
        self.assertEqual(current["view_type"], "遠景")

    def test_three_bound_wide_scene_calls_finish_as_distant_without_fourth_call(self):
        common = {
            "period": "202601", "model": None, "price": None,
            "input_image_sha256": "a" * 64, "request_id_verified": True,
            "request_binding_enforced": True,
            "independent_pass": True, "prior_answer_exposed": False,
            "prompt_contamination": False, "runtime_health": {"healthy": True},
        }
        first = {
            **common, "view_type": "單機", "category": "單機",
            "thinking": "一整排螢幕陳列，上下都有完整螢幕，無法鎖定唯一主角。",
            **evidence(7, False, "not_visible", []),
        }
        second = {
            **common, "view_type": "單機", "category": "單機",
            "thinking": "貨架上有多台螢幕陳列，沒有可歸屬的型號或價格。",
            **evidence(5, True, "matched", []),
        }
        third = {
            **common, "view_type": "遠景", "category": "遠景",
            "thinking": "一整排至少三台完整螢幕，無法鎖定唯一主角與其價格。",
            **evidence(3, False, "ambiguous", []),
        }
        outcome = finalize_three_pass_outcome(
            third, [first, second],
            {"attempt": 3, "unresolved": True, "verified": False, "reasons": ["core_evidence_disagreement"]},
            3,
        )
        self.assertTrue(outcome["verified"])
        self.assertFalse(outcome["unresolved"])
        self.assertEqual(third["view_type"], "遠景")
        self.assertEqual(outcome["adjudication_rule"], "distant_structural_veto_over_two_weak_wide_single_votes")

    def test_human_audited_940_pixels_can_never_auto_verify_as_distant(self):
        row = {
            "period": "202601", "view_type": "遠景", "category": "遠景",
            "model": None, "price": None, "input_image_sha256": self.FRAME_940_SHA,
            "thinking": "三台螢幕完整入鏡，無法鎖定唯一主角與價格，所以……整體符合遠景條件。",
            **evidence(3, False, "not_visible", []),
        }
        decision = immediate_retry_decision(dict(row), 3, [dict(row), dict(row)], 3)
        self.assertFalse(decision["verified"])
        self.assertTrue(decision["unresolved"])
        self.assertTrue(any("人工確認高風險原圖與模型" in reason for reason in decision["reasons"]))

    def test_edge_cut_distant_json_is_blocked_by_its_own_narration(self):
        row = {
            "period": "202601", "view_type": "遠景", "category": "遠景",
            "model": None, "price": None,
            "thinking": (
                "我看到中央一台螢幕正下方有 Samsung S32FM803UC 與 12,900 價牌，"
                "背景左右兩側各有一台螢幕，但都被照片邊界裁切，所以……整體符合遠景條件。"
            ),
            **evidence(3, False, "not_visible", []),
        }
        decision = immediate_retry_decision(dict(row), 1, [], 3)
        self.assertTrue(decision["retry"])
        self.assertFalse(decision["verified"])
        self.assertIn("敘述明確只有一台完整螢幕，結構完整台數必須為1", decision["reasons"])

    def test_human_audited_1528_pixels_can_never_auto_verify_as_single(self):
        row = self._multiscreen_single(input_image_sha256=self.WIDE_1528_SHA)
        decision = immediate_retry_decision(dict(row), 3, [dict(row), dict(row)], 3)
        self.assertFalse(decision["verified"])
        self.assertTrue(decision["unresolved"])
        self.assertTrue(any("人工確認高風險原圖與模型" in reason for reason in decision["reasons"]))

    def test_explicit_one_complete_narration_conflicts_with_structured_two(self):
        row = self._multiscreen_single(
            complete_screen_count=2,
            thinking="我看到前景一台主角，背景其他螢幕均未完整入鏡，所以……這是單機。",
        )
        decision = immediate_retry_decision(dict(row), 1, [], 3)
        self.assertFalse(decision["retry"])
        self.assertTrue(decision["verified"])
        self.assertEqual(decision["normalized_evidence"]["complete_screen_count"], 1)

    def test_known_650_pixels_can_never_auto_verify_as_single(self):
        row = self._multiscreen_single(input_image_sha256=self.RISK_650_SHA)
        first = immediate_retry_decision(dict(row), 1, [], 3)
        self.assertFalse(first["verified"])
        self.assertTrue(first["retry"])
        third = immediate_retry_decision(dict(row), 3, [dict(row), dict(row)], 3)
        self.assertFalse(third["verified"])
        self.assertTrue(third["unresolved"])
        self.assertTrue(any("人工確認高風險原圖與模型" in reason for reason in third["reasons"]))

    def test_human_audited_pixel_sources_require_exact_evidence(self):
        cases = (
            (self.FRAME_940_SHA, "S32FM803UC", "12900"),
            (self.FRAME_942_SHA, "S32CG552EC", "6990"),
            (self.FRAME_943_SHA, "S27F612EAC", "4990"),
            (self.FRAME_649_SHA, "S27CG552EC", "4990"),
            (self.FRAME_668_SHA, "S32FM703UC", "9990"),
            (self.FRAME_673_SHA, "S27FG532EC", "4990"),
            (self.FRAME_674_SHA, "S27D300GAC", "3090"),
            (self.FRAME_1257_SHA, "C34G55TWWC", "9900"),
        )
        for image_hash, model, price in cases:
            correct = {
                "period": "202601", "view_type": "單機", "category": "單機",
                "model": model, "price": price, "input_image_sha256": image_hash,
                "thinking": "中央唯一完整主角與其正下方價牌歸屬一致。",
                "independent_pass": True, "prior_answer_exposed": False,
                "prompt_contamination": False, "runtime_health": {"healthy": True},
                **evidence(1, True, "matched", []),
            }
            with self.subTest(image_hash=image_hash):
                first = immediate_retry_decision(dict(correct), 1, [], 3)
                self.assertTrue(first["retry"])
                final = immediate_retry_decision(dict(correct), 3, [dict(correct), dict(correct)], 3)
                self.assertTrue(final["verified"])
                wrong = dict(correct, complete_screen_count=3)
                blocked = immediate_retry_decision(dict(wrong), 3, [dict(wrong), dict(wrong)], 3)
                self.assertFalse(blocked["verified"])
                self.assertTrue(blocked["unresolved"])

    def test_human_audited_668_rejects_hallucinated_followme_fixture(self):
        row = {
            "period": "202601", "view_type": "單機", "category": "單機",
            "model": "S32FM703UC", "price": "9990", "input_image_sha256": self.FRAME_668_SHA,
            "thinking": "只看到白色直桿與貨架價牌條。",
            "independent_pass": True, "prior_answer_exposed": False,
            "prompt_contamination": False, "runtime_health": {"healthy": True},
            **evidence(1, True, "matched", [
                {"cue": "round_base", "same_subject": True, "strength": "strong"},
                {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
            ]),
        }
        decision = immediate_retry_decision(dict(row), 3, [dict(row), dict(row)], 3)
        self.assertFalse(decision["verified"])
        self.assertTrue(decision["unresolved"])

    def test_human_pixel_authority_finalizes_only_after_three_bound_stateless_calls(self):
        wrong = {
            "period": "202601", "view_type": "遠景", "category": "遠景",
            "model": None, "price": None, "input_image_sha256": self.FRAME_649_SHA,
            "thinking": "三台螢幕完整入鏡。", "ocr_attempt": 1,
            "request_id_verified": True, "independent_pass": True,
            "prior_answer_exposed": False, "prompt_contamination": False,
            **evidence(3, False, "not_visible", []),
        }
        first, second, third = dict(wrong), dict(wrong, ocr_attempt=2), dict(wrong, ocr_attempt=3)
        self.assertFalse(apply_human_audited_pixel_authority(first, [], 3))
        self.assertTrue(apply_human_audited_pixel_authority(third, [first, second], 3))
        self.assertEqual(third["view_type"], "單機")
        self.assertEqual(third["complete_screen_count"], 1)
        self.assertEqual(third["model"], "S27CG552EC")
        self.assertEqual(third["price"], 4990)
        self.assertTrue(third["human_pixel_authority_applied"])
        self.assertTrue(third["evidence_contract_valid"])
        self.assertEqual(third["evidence_contract_errors"], [])
        decision = immediate_retry_decision(third, 3, [first, second], 3)
        self.assertTrue(decision["verified"])

    def test_known_pixel_authority_recovers_missing_first_trace_without_fourth_call(self):
        calls = [
            {
                "ocr_attempt": attempt,
                "input_image_sha256": self.FRAME_1257_SHA,
                "request_id_verified": True,
                "independent_pass": True,
                "prior_answer_exposed": False,
                "prompt_contamination": False,
            }
            for attempt in (2, 3)
        ]
        current = dict(calls[-1])
        recovered = _recover_known_authority_after_restart(
            current,
            calls,
            {"auto_retry_reasons": "known_source_expectation_conflict；three_call_hard_limit_reached"},
        )
        self.assertTrue(recovered)
        self.assertEqual(current["model"], "C34G55TWWC")
        self.assertEqual(current["price"], 9900)
        self.assertEqual(
            current["adjudication_rule"],
            "three_call_known_pixel_authority_restart_recovery",
        )

    def test_restart_recovery_preserves_audited_followme_physical_evidence(self):
        followme_sha = "4b069632c9af4da183fa5ff7e1ec616331f59ede149b7d9ea27b571be19213c5"
        calls = [
            {
                "period": "202601",
                "view_type": "單機",
                "category": "單機",
                "ocr_attempt": attempt,
                "input_image_sha256": followme_sha,
                "request_id_verified": True,
                "request_binding_enforced": True,
                "independent_pass": True,
                "prior_answer_exposed": False,
                "prompt_contamination": False,
                "runtime_health": {"healthy": True, "reasons": []},
                **evidence(1, True, "matched", []),
            }
            for attempt in (1, 3)
        ]
        current = dict(calls[-1])

        recovered = _recover_known_authority_after_restart(
            current,
            calls,
            {"auto_retry_reasons": "three_call_hard_limit_reached"},
        )

        self.assertTrue(recovered)
        self.assertTrue(current["followme_family_confirmed"])
        self.assertTrue(current["followme_physical_evidence"])
        self.assertEqual(current["model"], "FollowMe Pro M7 43\"")
        self.assertIn("實體 FollowMe 主角", current["thinking"])

    def test_two_clean_single_tail_calls_keep_only_repeated_fields(self):
        calls = []
        for attempt, model, count in ((2, "S32DM803UC", 1), (3, None, 2)):
            calls.append({
                "ocr_attempt": attempt,
                "input_image_sha256": "4" * 64,
                "request_id_verified": True,
                "independent_pass": True,
                "prior_answer_exposed": False,
                "prompt_contamination": False,
                "runtime_health": {"healthy": True, "reasons": []},
                "view_type": "單機",
                "category": "單機",
                "model": model,
                "price": "19900",
                **evidence(count, True, "matched", []),
            })
        current = dict(calls[-1])
        recovered = _recover_clean_single_tail_after_restart(
            current,
            calls,
            {"auto_retry_reasons": "core_evidence_disagreement；three_call_hard_limit_reached"},
        )
        self.assertTrue(recovered)
        self.assertIsNone(current["model"])
        self.assertEqual(current["price"], "19900")
        self.assertEqual(current["complete_screen_count"], 1)

    def test_tainan_yude_183_raw_tail_zero_model_finalizes_ordered_followme(self):
        name = "M-台南市-北　區-Q哥-台南育德-183.jpg"
        image_hash = "08fa67ad38423bb455c34afb9c961ab916a1ad0f86924f7b567740b025d8b4c2"
        source_id = "033790fe16cfb3a193afb16be5eee383999d67756116a75ba9573a5dae4e4997"
        original = rf"D:\source\商化照片-202602\{name}"
        payloads = [
            {
                "request_id": "1" * 32,
                "narration": (
                    "我看到中央偏左一台螢幕，同一主體有 Samsung Follow Me 4K 標籤、"
                    "白色直立支架、完整圓形落地底座與附著託盤，價格牌標示 13,290。"
                ),
                "view_type": "單機",
                "screen_status": "正常",
                "quality_issue": "無",
                "model": "FollowMe 型號未細分",
                "price": "13290",
                "complete_screen_count": 1,
                "unique_main": True,
                "label_ownership": "matched",
                "followme_physical_evidence": [
                    {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
                    {"cue": "round_base", "same_subject": True, "strength": "strong"},
                    {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
                ],
            },
            {
                "request_id": "2" * 32,
                "narration": (
                    "我看到中央偏右一台螢幕，同一主體有 Samsung Follow Me 4K 產品卡、"
                    "白色直立支架與完整圓形落地底座，價格牌明確標示 13,290。"
                ),
                "view_type": "單機",
                "screen_status": "正常",
                "quality_issue": "無",
                "model": "Samsung Follow Me 4K",
                "price": "13290",
                "complete_screen_count": 1,
                "unique_main": True,
                "label_ownership": "matched",
                "followme_physical_evidence": [
                    {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
                    {"cue": "round_base", "same_subject": True, "strength": "strong"},
                    {"cue": "attached_followme_product_card", "same_subject": True, "strength": "strong"},
                ],
            },
        ]
        calls = []
        for attempt, payload in zip((2, 3), payloads):
            calls.append({
                "ocr_attempt": attempt,
                "period": "202602",
                "file_name": name,
                "source_path": rf"D:\staging\{name}",
                "source_item_id": source_id,
                "original_source_path": original,
                "input_image_sha256": image_hash,
                "request_id_verified": True,
                "independent_pass": True,
                "prior_answer_exposed": False,
                "prompt_contamination": False,
                # Reproduce the legacy pass-3 parse: the immutable raw JSON is
                # sound, but the old guard blocked its parsed model field.
                "runtime_health": {
                    "healthy": attempt == 2,
                    "reasons": [] if attempt == 2 else ["structured_authority_material_conflict:model"],
                },
                "view_type": "單機",
                "category": "單機",
                "model": payload["model"] if attempt == 2 else None,
                "price": "13290",
                "raw_objects": [json.dumps(payload, ensure_ascii=False)],
                **evidence(1, True, "matched", payload["followme_physical_evidence"]),
            })
        current = dict(calls[-1])
        recovered = _recover_clean_single_tail_after_restart(
            current,
            calls,
            {
                "auto_retry_reasons": (
                    "three_pass_current_integrity_invalid；"
                    "structured_authority_material_conflict:model；"
                    "three_call_hard_limit_reached"
                )
            },
        )
        self.assertTrue(recovered)
        self.assertEqual(current["model"], "FollowMe 型號未細分")
        self.assertEqual(current["price"], "13290")
        self.assertTrue(current["followme_family_confirmed"])
        self.assertTrue(current["ordered_followme_early_exit"])
        self.assertTrue(current["followme_physical_evidence"])
        self.assertEqual(
            current["adjudication_rule"],
            "two_current_ordered_followme_tail_calls_after_persisted_attempt_one",
        )

    def test_known_650_pixels_require_three_clean_distant_passes(self):
        distant = {
            "period": "202601", "view_type": "遠景", "category": "遠景",
            "model": None, "price": None, "input_image_sha256": self.RISK_650_SHA,
            "thinking": "整排三台以上螢幕完整入鏡，無法鎖定唯一主角及其自己的規格與價格。",
            "independent_pass": True, "prior_answer_exposed": False,
            "prompt_contamination": False, "runtime_health": {"healthy": True},
            **evidence(3, False, "not_visible", []),
        }
        first = immediate_retry_decision(dict(distant), 1, [], 3)
        self.assertTrue(first["retry"])
        decision = immediate_retry_decision(dict(distant), 3, [dict(distant), dict(distant)], 3)
        self.assertTrue(decision["verified"])
        self.assertFalse(decision["unresolved"])

    def test_negated_followme_with_black_short_stand_and_tray_is_not_a_fixture_conflict(self):
        row = {
            "view_type": "單機", "category": "單機", "model": "S32DM703UC", "price": None,
            "thinking": (
                "我看到一台直立螢幕，正下方有黑色短支架與託盤，"
                "所以這是一台 Samsung Smart Monitor M7，非 FollowMe。"
            ),
            **evidence(1, True, "matched", []),
        }
        self.assertEqual(narrated_followme_physical_cues(row), {"portrait_display", "attached_price_tray"})
        self.assertFalse(narration_has_positive_followme_identity(row["thinking"]))
        self.assertFalse(narration_has_unmistakable_followme_fixture(row["thinking"]))
        self.assertEqual(narration_evidence_consistency_reasons(row), [])

    def test_white_round_base_with_attached_tray_is_an_unmistakable_fixture(self):
        narration = "中央直立螢幕下方有白色圓形底座與託盤，但未見白色垂直支架。"
        self.assertTrue(narration_has_unmistakable_followme_fixture(narration))

    def test_followme_friendly_names_equal_only_their_established_physical_sku_family(self):
        self.assertTrue(batch.followme_models_equivalent('FollowMe M7 32"', "S32FM703UC"))
        self.assertTrue(batch.followme_models_equivalent('FollowMe M5 32"', "LS32FM501ECXZW"))
        self.assertTrue(batch.followme_models_equivalent('FollowMe M7 43"', "S43FM703UC"))
        self.assertFalse(batch.followme_models_equivalent('FollowMe Pro M7 43"', "S43FM703UC"))
        self.assertFalse(batch.followme_models_equivalent('FollowMe M7 32"', "S43FM703UC"))
        self.assertFalse(batch.followme_models_equivalent('FollowMe M5 32"', "S32FM703UC"))
        self.assertFalse(batch.followme_models_equivalent('FollowMe M7 32"', "S32FM803UC"))
        self.assertFalse(batch.is_followme_model("S32FM703UC"))
        self.assertTrue(batch.is_followme_model('FollowMe M7 32"'))

    def test_explicit_followme_model_with_same_pass_fixtures_survives_generic_signage_word(self):
        record = {
            "model": 'FollowMe M7 32"',
            "price": "12990",
            "thinking": (
                "我看到前景一台直立的 Samsung 螢幕，正下方有白色圓形落地底座與託盤，"
                "螢幕右側貼有 Samsung Smart Monitor M7 標籤，下方價牌清楚寫 12,990，"
                "上方藍色立牌寫 Samsung Follow Me 4K，這些證據都屬於同一主體。"
            ),
            **evidence(1, True, "matched", [
                {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
                {"cue": "round_base", "same_subject": True, "strength": "strong"},
                {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
                {"cue": "attached_followme_product_card", "same_subject": True, "strength": "strong"},
            ]),
        }
        self.assertFalse(batch.should_block_borrowed_model_rescue(record["thinking"]))
        self.assertTrue(
            batch.explicit_followme_model_has_same_pass_physical_evidence(record["model"], record)
        )
        self.assertEqual(
            batch.normalize_followme_model(
                record["model"], record["price"], record["thinking"],
                structured_physical_confirmed=True,
            ),
            'FollowMe M7 32"',
        )

        signage_only = dict(record)
        signage_only["followme_physical_evidence"] = []
        signage_only["thinking"] = "旁邊活動立牌寫 Samsung Follow Me 4K，但主角實機沒有可歸屬的支架證據。"
        self.assertTrue(batch.should_block_borrowed_model_rescue(signage_only["thinking"]))
        self.assertFalse(
            batch.explicit_followme_model_has_same_pass_physical_evidence(
                signage_only["model"], signage_only
            )
        )

    def test_followme_pro_43_requires_observed_variant_evidence(self):
        generic_m7 = {
            "view_type": "單機", "category": "單機",
            "model": 'FollowMe Pro M7 43"', "price": "12990",
            "thinking": (
                "我看到同一台白色移動式螢幕，上方只有 Samsung Follow Me 4K，"
                "右側是 Smart Monitor M7，價牌為 12,990。"
            ),
            **evidence(1, True, "matched", [
                {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
                {"cue": "round_base", "same_subject": True, "strength": "strong"},
            ]),
        }
        self.assertEqual(
            followme_variant_evidence_reasons(generic_m7),
            ["followme_pro_identity_evidence_missing"],
        )
        decision = evidence_contract_decision(generic_m7)
        self.assertFalse(decision["valid"])
        self.assertIn("followme_pro_identity_evidence_missing", decision["reasons"])

        price_only_pro = dict(generic_m7)
        price_only_pro["price"] = "17990"
        self.assertEqual(
            followme_variant_evidence_reasons(price_only_pro),
            ["followme_pro_identity_evidence_missing"],
        )

        explicit_pro = dict(generic_m7)
        explicit_pro["price"] = "17990"
        explicit_pro["thinking"] = "同一實機規格牌清楚寫 FollowMe Pro 43吋，價牌 17,990。"
        self.assertEqual(followme_variant_evidence_reasons(explicit_pro), [])
        self.assertTrue(evidence_contract_decision(explicit_pro)["valid"])

    def test_request_id_binds_response_to_current_photo(self):
        raw = json.dumps({
            "request_id": "a1b2c3d4",
            "narration": "我看到當前照片的一台螢幕，所以……",
            "view_type": "單機", "screen_status": "正常", "quality_issue": "無",
            "model": "S27D300GAC", "price": "3090", "category": "單機",
            **evidence(1, True, "matched"),
        }, ensure_ascii=False)
        parsed, _, mode, reason = _merge_v1945_json_objects(raw)
        self.assertEqual(mode, "single_object")
        self.assertEqual(reason, "")
        self.assertEqual(validate_request_binding(parsed, "a1b2c3d4"), "")
        self.assertEqual(validate_request_binding(parsed, "deadbeef"), "request_id_mismatch")
        parsed.pop("request_id")
        self.assertEqual(validate_request_binding(parsed, "a1b2c3d4"), "request_id_missing")

    def test_strict_response_schema_is_bound_and_used_by_production_request(self):
        request_id = "0123456789abcdef0123456789abcdef"
        response_format = build_v1945_response_format(request_id)
        self.assertEqual(response_format["type"], "json_schema")
        definition = response_format["json_schema"]
        self.assertTrue(definition["strict"])
        schema = definition["schema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            next(iter(schema["properties"])),
            "narration",
        )
        self.assertEqual(
            schema["properties"]["request_id"]["enum"],
            [request_id],
        )
        self.assertEqual(
            set(schema["required"]),
            set(batch.V1945_RESPONSE_REQUIRED_KEYS),
        )
        self.assertFalse(
            schema["properties"]["followme_physical_evidence"]["items"][
                "additionalProperties"
            ]
        )
        production_source = __import__("inspect").getsource(
            batch.process_single_image
        )
        self.assertIn(
            '"response_format": build_v1945_response_format(random_salt)',
            production_source,
        )

    def test_strict_response_shape_fails_closed(self):
        request_id = "0123456789abcdef0123456789abcdef"
        payload = {
            "narration": "我看到本輪結論：單機，型號 S27D300GAC，價格 3,090 元。",
            "request_id": request_id,
            "view_type": "單機",
            "screen_status": "正常",
            "quality_issue": "無",
            "model": "S27D300GAC",
            "price": "3090",
            "category": "單機",
            **evidence(1, True, "matched"),
        }
        self.assertEqual(
            validate_v1945_response_shape(payload, request_id),
            "",
        )
        missing = dict(payload)
        missing.pop("narration")
        self.assertEqual(
            validate_v1945_response_shape(missing, request_id),
            "structured_response_missing:narration",
        )
        extra = {**payload, "unexpected": True}
        self.assertEqual(
            validate_v1945_response_shape(extra, request_id),
            "structured_response_extra:unexpected",
        )
        wrong_id = {**payload, "request_id": "f" * 32}
        self.assertEqual(
            validate_v1945_response_shape(wrong_id, request_id),
            "request_id_mismatch",
        )
        bad_narration = {**payload, "narration": "這是一台螢幕。"}
        self.assertEqual(
            validate_v1945_response_shape(bad_narration, request_id),
            "structured_narration_invalid",
        )

    def test_model_narration_prefix_is_repaired_before_shape_validation(self):
        from samsung_ocr_batch_processor import normalize_model_narration_prefix

        request_id = "0123456789abcdef0123456789abcdef"
        payload = {
            "narration": "遠景，無型號，無價格。判讀依據：三台完整螢幕。",
            "request_id": request_id,
            "view_type": "遠景",
            "screen_status": "正常",
            "quality_issue": "無",
            "model": None,
            "price": None,
            "category": "遠景",
            **evidence(3, False, "not_visible"),
        }
        repaired = normalize_model_narration_prefix(payload)
        self.assertTrue(repaired["narration"].startswith("我看到本輪結論："))
        self.assertEqual(validate_v1945_response_shape(repaired, request_id), "")

    def test_stream_preview_extracts_partial_json_narration(self):
        partial = (
            '{"narration":"我看到本輪結論：單機，型號 S27D300GAC，'
            '價格 3,090 元。判讀依據：'
        )
        self.assertEqual(
            _stream_narration_preview(partial),
            "我看到本輪結論：單機，型號 S27D300GAC，價格 3,090 元。判讀依據：",
        )
        self.assertEqual(
            _stream_narration_preview(
                "我看到本輪結論：遠景，無型號，無價格。"
                '{"view_type":"遠景"}'
            ),
            "我看到本輪結論：遠景，無型號，無價格。",
        )

    def test_request_id_uses_full_128_bit_space(self):
        values = {new_request_id() for _ in range(1000)}
        self.assertEqual(len(values), 1000)
        self.assertTrue(all(len(value) == 32 for value in values))
        self.assertTrue(all(all(ch in "0123456789abcdef" for ch in value) for value in values))

    def test_request_binding_tail_keeps_full_current_token_at_prompt_end(self):
        request_id = "0123456789abcdef0123456789abcdef"
        tail = request_binding_tail(request_id)
        self.assertTrue(tail.endswith(request_id))
        self.assertEqual(tail.count(request_id), 1)
        with self.assertRaises(ValueError):
            request_binding_tail("too-short")

    def test_adjacent_duplicate_core_forces_only_first_pass_retry(self):
        previous = {
            "auto_verified": True, "source_item_id": "source-a", "label_ownership": "matched",
            "model": "S27D300GAC", "price": "3090",
        }
        current = {
            "source_item_id": "source-b", "label_ownership": "matched",
            "model": "S27D300GAC", "price": "3,090",
        }
        self.assertTrue(cross_photo_duplicate_core(previous, current))
        previous["auto_verified"] = False
        previous["label_ownership"] = "ambiguous"
        current["label_ownership"] = "not_visible"
        self.assertTrue(cross_photo_duplicate_core(previous, current))
        row = {
            "period": "202601", "view_type": "單機", "category": "單機",
            "model": "S27D300GAC", "price": "3090",
            "thinking": "我看到主角價牌標示型號與價格，所以……",
            "cross_photo_duplicate_core_suspected": True,
            "independent_pass": True, "prior_answer_exposed": False, "prompt_contamination": False,
            "runtime_health": {"healthy": True},
            **evidence(1, True, "matched"),
        }
        decision = immediate_retry_decision(row, 1, [], 3)
        self.assertTrue(decision["retry"])
        self.assertIn("跨照片", "".join(decision["reasons"]))
        history_row = dict(row)
        second_row = dict(row)
        second_row.pop("cross_photo_duplicate_core_suspected")
        second = immediate_retry_decision(second_row, 2, [history_row], 3)
        self.assertTrue(second["retry"])
        self.assertIn("不得以兩輪相同洗白", "".join(second["reasons"]))
        third = immediate_retry_decision(dict(second_row), 3, [history_row, dict(second_row)], 3)
        self.assertTrue(third["unresolved"])
        self.assertFalse(third["verified"])

    def test_pipeline_owned_model_markers_survive_postprocess_merge(self):
        target = {
            "view_type": "單機",
            "category": "單機",
            "model": None,
            "price": None,
        }
        batch.merge_postprocessed_result_fields(
            target,
            {
                "model": "S27CG552EC",
                "price": "4990",
                "model_prefix_completed": True,
                "model_prefix_completion_from": "S27CG552",
                "unlisted_model_candidate": True,
                "official_model_unverified": True,
                "model_supplied_unknown_key": "must not leak",
            },
        )
        self.assertEqual(target["model"], "S27CG552EC")
        self.assertTrue(target["model_prefix_completed"])
        self.assertEqual(target["model_prefix_completion_from"], "S27CG552")
        self.assertTrue(target["unlisted_model_candidate"])
        self.assertTrue(target["official_model_unverified"])
        self.assertNotIn("model_supplied_unknown_key", target)

    def test_unique_trailing_model_completion_is_bounded(self):
        self.assertEqual(
            unique_known_model_completion("S27CG552", ["S27CG552EC", "S32CG552EC"]),
            "S27CG552EC",
        )
        self.assertIsNone(
            unique_known_model_completion("S27CG552", ["S27CG552EC", "S27CG552EUC"])
        )
        self.assertIsNone(unique_known_model_completion("S27CG552", ["S32CG552EC"]))

    def test_flagged_unique_prefix_completion_is_not_a_structured_identity_conflict(self):
        postprocessed = {
            "view_type": "單機",
            "category": "單機",
            "model": "S27CG552EC",
            "price": "4990",
            "model_prefix_completed": True,
            "model_prefix_completion_from": "S27CG552",
        }
        blocked = batch.enforce_explicit_structured_authority(
            postprocessed,
            {"view_type": "單機", "category": "單機", "model": "S27CG552", "price": "4990"},
        )
        self.assertEqual(postprocessed["model"], "S27CG552EC")
        self.assertNotIn("model", blocked)

    def test_unique_physical_label_family_letter_correction_is_not_a_structured_conflict(self):
        postprocessed = {
            "view_type": "\u55ae\u6a5f",
            "category": "\u55ae\u6a5f",
            "model": "F24T350FHC",
            "price": "3990",
            "unlisted_model_candidate": True,
            "photo_label_model_correction_from": "S24T350FHC",
            "photo_label_model_correction_to": "F24T350FHC",
        }
        blocked = batch.enforce_explicit_structured_authority(
            postprocessed,
            {
                "view_type": "\u55ae\u6a5f",
                "category": "\u55ae\u6a5f",
                "model": "S24T350FHC",
                "price": "3990",
            },
        )
        self.assertEqual(postprocessed["model"], "F24T350FHC")
        self.assertNotIn("model", blocked)

    def test_unique_embedded_catalog_sku_is_safe_structured_normalization(self):
        raw_model = "SAMSUNG 24型IPS液晶顯示器 F24T350FHC"
        valid_models = ["F24T350FHC", "C24F390FHE"]
        recovered = unique_embedded_known_model(raw_model, valid_models)
        self.assertEqual(recovered, "F24T350FHC")

        postprocessed = {
            "view_type": "單機",
            "category": "單機",
            "model": recovered,
            "price": None,
            "embedded_catalog_model_recovered": True,
            "embedded_catalog_model_from": raw_model,
            "embedded_catalog_model_to": recovered,
        }
        blocked = batch.enforce_explicit_structured_authority(
            postprocessed,
            {"view_type": "單機", "category": "單機", "model": raw_model},
        )
        self.assertEqual(postprocessed["model"], "F24T350FHC")
        self.assertNotIn("model", blocked)

        self.assertIsNone(
            unique_embedded_known_model(
                "F24T350FHC 與 C24F390FHE",
                valid_models,
            )
        )

    def test_unmarked_family_letter_change_remains_a_structured_conflict(self):
        postprocessed = {
            "view_type": "\u55ae\u6a5f",
            "category": "\u55ae\u6a5f",
            "model": "F24T350FHC",
            "price": "3990",
        }
        blocked = batch.enforce_explicit_structured_authority(
            postprocessed,
            {"model": "S24T350FHC", "price": "3990"},
        )
        self.assertIsNone(postprocessed["model"])
        self.assertIn("model", blocked)

    def test_prefix_completion_needs_two_independent_matching_passes(self):
        base = {
            "period": "202601",
            "view_type": "單機",
            "category": "單機",
            "model": "S27CG552EC",
            "price": "4990",
            "thinking": "主角自己的價牌清楚標示 S27CG552，售價 4,990 元。",
            "model_prefix_completed": True,
            "model_prefix_completion_from": "S27CG552",
            "independent_pass": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
            "runtime_health": {"healthy": True},
            **evidence(1, True, "matched"),
        }
        first = dict(base)
        self.assertTrue(immediate_retry_decision(first, 1, [], 3)["retry"])
        second = dict(base)
        decision = immediate_retry_decision(second, 2, [first], 3)
        self.assertTrue(decision["verified"])
        self.assertFalse(decision["retry"])

    def test_one_aligned_card_and_neighbour_cards_close_on_matching_second_pass(self):
        first = {
            "period": "202601",
            "view_type": "單機",
            "category": "單機",
            "model": "S27CG552EC",
            "price": "5790",
            "price_status": "high",
            "thinking": (
                "中央價牌與主角螢幕對齊，清楚標示 S27CG552 與 5,790 元；"
                "其他兩張價牌屬於鄰機。"
            ),
            "model_prefix_completed": True,
            "model_prefix_completion_from": "S27CG552",
            "independent_pass": True,
            "request_id_verified": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
            "runtime_health": {"healthy": True},
            **evidence(1, True, "matched"),
        }
        second = {
            **first,
            "thinking": (
                "螢幕下方有三張實體價牌，其中一張與螢幕對齊，寫著"
                "「27吋 SAMSUNG S27CG552」與價格 5,790 元，"
                "其他兩張價牌則屬於旁邊螢幕。這不是 FollowMe，"
                "也無法確認型號與價格是否屬於同一主角商品，所以……"
            ),
        }
        decision = immediate_retry_decision(second, 2, [first], 3)
        self.assertTrue(decision["verified"])
        self.assertFalse(decision["retry"])
        self.assertNotIn("標籤歸屬與敘述衝突", decision["reasons"])

    def test_unaligned_card_still_conflicts_with_matched_structure(self):
        row = {
            "period": "202601",
            "view_type": "單機",
            "category": "單機",
            "model": "S27CG552EC",
            "price": "5790",
            "thinking": "螢幕下方有價牌，但無法確認該價牌是否與主角螢幕空間對齊。",
            "independent_pass": True,
            "request_id_verified": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
            "runtime_health": {"healthy": True},
            **evidence(1, True, "matched"),
        }
        decision = immediate_retry_decision(row, 1, [], 3)
        self.assertTrue(decision["retry"])
        self.assertIn("標籤歸屬與敘述衝突", decision["reasons"])

    def test_unlisted_model_candidate_requires_same_photo_label_evidence(self):
        narration = (
            "中間主角螢幕正下方有實體價牌，清楚標示型號 S24D300GAC，"
            "售價 2,990 元，價牌歸屬明確。"
        )
        record = {
            "view_type": "單機",
            "unique_main": True,
            "label_ownership": "matched",
        }
        self.assertTrue(has_photo_label_model_evidence("S24D300GAC", record, narration))
        self.assertFalse(
            has_photo_label_model_evidence(
                "S24D300GAC",
                record,
                "價牌模糊，可能是 S24D300GAC，但無法確認。",
            )
        )
        self.assertFalse(
            has_photo_label_model_evidence(
                "S24D300GAC",
                {**record, "view_type": "遠景"},
                narration,
            )
        )

    def test_unlisted_legacy_model_uses_unique_visible_card_token(self):
        record = {
            "view_type": "單機",
            "unique_main": True,
            "label_ownership": "matched",
        }
        narration = (
            '我看到中央主角自己的實體價牌清楚寫著 27" SAMSUNG '
            "C27F390FHE 曲面螢幕與售價。"
        )
        self.assertEqual(
            resolve_photo_label_model_candidate("S27F390FHE", record, narration),
            "C27F390FHE",
        )
        self.assertEqual(
            resolve_photo_label_model_candidate(
                "S27R500FHC",
                record,
                "我看到本輪結論：單機，S27R500FHC，5,691 元。"
                "主角正下方實體價牌清楚寫著 C27R500FHC 與 5,691 元，歸屬明確。",
            ),
            "C27R500FHC",
        )
        self.assertIsNone(
            resolve_photo_label_model_candidate(
                "S27F390FHE",
                record,
                narration + "旁邊另一張牌寫 S27F390FHE。",
            )
        )
        self.assertIsNone(
            resolve_photo_label_model_candidate(
                "S27F390FHE",
                {**record, "label_ownership": "ambiguous", "unique_main": False},
                "價牌模糊，可能是 C27F390FHE，但無法確認。",
            )
        )

    def test_decorative_family_prefix_preserves_one_owned_exact_sku(self):
        record = {
            "view_type": "單機",
            "unique_main": True,
            "label_ownership": "matched",
        }
        narration = (
            "中央主角自己的實體側標清楚寫著 ViewFinity S9 S27C900PAC，"
            "同一台螢幕正下方價牌標示 45,900 元，歸屬明確。"
        )
        self.assertEqual(
            resolve_photo_label_model_candidate(
                "ViewFinity S9 S27C900PAC",
                record,
                narration,
            ),
            "S27C900PAC",
        )
        self.assertIsNone(
            resolve_photo_label_model_candidate(
                "ViewFinity S9 S27C900PAC S32DG802SC",
                record,
                narration + "旁邊另一張牌寫 S32DG802SC。",
            )
        )

    def test_unlisted_model_three_independent_pass_consensus_can_verify(self):
        base = {
            "period": "202601",
            "view_type": "單機",
            "category": "單機",
            "model": "S24D300GAC",
            "price": "2990",
            "thinking": "主角自己的實體價牌清楚標示 S24D300GAC 與 2,990 元。",
            "unlisted_model_candidate": True,
            "independent_pass": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
            "runtime_health": {"healthy": True},
            **evidence(1, True, "matched"),
        }
        first = dict(base)
        second = dict(base)
        third = dict(base)
        self.assertTrue(immediate_retry_decision(first, 1, [], 3)["retry"])
        self.assertTrue(immediate_retry_decision(second, 2, [first], 3)["retry"])
        final = immediate_retry_decision(third, 3, [first, second], 3)
        self.assertTrue(final["verified"])
        self.assertFalse(final["unresolved"])
        self.assertTrue(third["unlisted_model_photo_consensus"])

    def test_bound_same_card_unlisted_sku_can_close_on_first_pass(self):
        narration = (
            "我看到本輪結論：單機，型號 S24D362GAC，價格 3,490 元。"
            "判讀依據：中央唯一主角自己的實體價牌同一張清楚標示 "
            "S24D362GAC 與 3,490 元，價牌歸屬明確。"
        )
        row = {
            "period": "202601",
            "view_type": "單機",
            "category": "單機",
            "model": "S24D362GAC",
            "price": "3490",
            "thinking": narration,
            "narration": narration,
            "unlisted_model_candidate": True,
            "official_model_unverified": True,
            "independent_pass": True,
            "request_binding_enforced": True,
            "request_id_verified": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
            "input_image_sha256": "a" * 64,
            "runtime_health": {"healthy": True, "reasons": []},
            "screen_status": "正常",
            "quality_issue": "無",
            **evidence(1, True, "matched"),
        }
        historical = dict(row)
        historical["period"] = "202208"
        decision = immediate_retry_decision(row, 1, [], 3)
        self.assertTrue(decision["verified"])
        self.assertFalse(decision["retry"])
        self.assertFalse(decision["unresolved"])
        self.assertTrue(row["unlisted_model_first_pass_evidence_lock"])
        self.assertTrue(row["unlisted_model_photo_consensus"])

        historical_decision = immediate_retry_decision(historical, 1, [], 3)
        self.assertTrue(historical_decision["verified"])
        self.assertFalse(historical_decision["retry"])
        self.assertTrue(historical["unlisted_model_first_pass_evidence_lock"])

    def test_unlisted_first_pass_lock_rejects_unbound_or_mismatched_card(self):
        narration = (
            "我看到本輪結論：單機，型號 S24D362GAC，價格 3,490 元。"
            "主角側標寫 S24D362GAC；另一台螢幕的價牌寫 "
            "S27D300GAC 與 3,490 元。"
        )
        base = {
            "period": "202601",
            "view_type": "單機",
            "category": "單機",
            "model": "S24D362GAC",
            "price": "3490",
            "thinking": narration,
            "narration": narration,
            "unlisted_model_candidate": True,
            "independent_pass": True,
            "request_binding_enforced": True,
            "request_id_verified": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
            "input_image_sha256": "b" * 64,
            "runtime_health": {"healthy": True, "reasons": []},
            "screen_status": "正常",
            "quality_issue": "無",
            **evidence(1, True, "matched"),
        }
        mismatch = dict(base)
        mismatch_decision = immediate_retry_decision(mismatch, 1, [], 3)
        self.assertTrue(mismatch_decision["retry"])
        self.assertFalse(mismatch["unlisted_model_first_pass_evidence_lock"])

        unbound = dict(base)
        unbound["thinking"] = unbound["narration"] = (
            "我看到本輪結論：單機，型號 S24D362GAC，價格 3,490 元。"
            "中央唯一主角自己的實體價牌同一張清楚標示 "
            "S24D362GAC 與 3,490 元。"
        )
        unbound["request_id_verified"] = False
        unbound_decision = immediate_retry_decision(unbound, 1, [], 3)
        self.assertTrue(unbound_decision["retry"])
        self.assertFalse(unbound["unlisted_model_first_pass_evidence_lock"])

    def test_pipeline_marker_recovers_unlisted_model_erased_after_validation(self):
        narration = (
            "中央主角螢幕正下方有實體價牌，清楚標示型號 "
            "S24D362GAC 與會員售價 3,490 元，價牌歸屬明確。"
        )
        record = {
            "period": "202601",
            "view_type": "單機",
            "category": "單機",
            "model": None,
            "price": "3490",
            "thinking": narration,
            "narration": narration,
            "unlisted_model_candidate": True,
            "unique_main": True,
            "label_ownership": "matched",
            "raw_objects": [
                json.dumps(
                    {
                        "view_type": "單機",
                        "model": "S24D362GAC",
                        "price": "3490",
                    },
                    ensure_ascii=False,
                )
            ],
        }
        self.assertEqual(
            recover_pipeline_unlisted_model_candidate(record),
            "S24D362GAC",
        )
        self.assertEqual(record["model"], "S24D362GAC")
        self.assertTrue(record["official_model_unverified"])

    def test_catalog_only_suppression_does_not_erase_historical_photo_label(self):
        narration = (
            "中央唯一主角正下方的實體價牌清楚標示 "
            "C24F390FHE 與 3,990 元，價牌歸屬明確。"
        )
        record = {
            "period": "202201",
            "view_type": "單機",
            "category": "單機",
            "model": None,
            "price": "3990",
            "thinking": narration,
            "narration": narration,
            "unlisted_model_candidate": True,
            "unique_main": True,
            "label_ownership": "matched",
            "model_validation_failed": True,
            "field_suppression_reasons": [
                "model:suppressed_after_structured_parse"
            ],
            "raw_objects": [
                json.dumps(
                    {"model": "C24F390FHE", "price": "3990"},
                    ensure_ascii=False,
                )
            ],
        }
        self.assertEqual(
            recover_pipeline_unlisted_model_candidate(record),
            "C24F390FHE",
        )
        self.assertFalse(record["model_validation_failed"])
        self.assertTrue(record["catalog_only_model_erasure_recovered"])

    def test_first_letter_catalog_alternative_blocks_unsafe_first_pass_lock(self):
        self.assertEqual(
            unique_known_first_letter_alternative(
                "S24T350FHC", ["F24T350FHC", "S27D300GAC"]
            ),
            "F24T350FHC",
        )
        narration = (
            "我看到本輪結論：單機，型號 S24T350FHC，價格 3,990 元。"
            "中央唯一主角正下方的實體價牌同一張清楚標示 "
            "S24T350FHC 與 3,990 元。"
        )
        row = {
            "period": "202201",
            "view_type": "單機",
            "category": "單機",
            "model": "S24T350FHC",
            "price": "3990",
            "thinking": narration,
            "narration": narration,
            "unlisted_model_candidate": True,
            "catalog_confusable_first_letter_candidate": True,
            "catalog_confusable_first_letter_alternative": "F24T350FHC",
            "independent_pass": True,
            "request_binding_enforced": True,
            "request_id_verified": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
            "input_image_sha256": "c" * 64,
            "runtime_health": {"healthy": True, "reasons": []},
            "screen_status": "正常",
            "quality_issue": "無",
            **evidence(1, True, "matched"),
        }
        decision = immediate_retry_decision(row, 1, [], 3)
        self.assertTrue(decision["retry"])
        self.assertFalse(row["unlisted_model_first_pass_evidence_lock"])

    def test_historical_same_card_raw_evidence_recovers_real_three_trace_cases(self):
        image_hash = "d" * 64

        def one_pass(attempt, model, narration, ownership="matched", failed=False):
            return {
                "period": "202201",
                "ocr_attempt": attempt,
                "view_type": "單機",
                "category": "單機",
                "model": model,
                "price": None,
                "thinking": narration,
                "narration": narration,
                "input_image_sha256": image_hash,
                "request_binding_enforced": True,
                "request_id_verified": True,
                "independent_pass": True,
                "prior_answer_exposed": False,
                "prompt_contamination": False,
                "runtime_health": {"healthy": True, "reasons": []},
                "model_validation_failed": failed,
                "price_conflict_detected": False,
                "brand_evidence_conflict": False,
                "normalized_evidence": evidence(1, True, ownership),
                **evidence(1, True, ownership),
            }

        exact_passes = [
            one_pass(
                1,
                None,
                "中央主螢幕正下方的實體價牌上清楚寫著 C24F390FHE 與 4,290。",
                failed=True,
            ),
            one_pass(
                2,
                None,
                "螢幕下方的黃色價牌上可讀取 C24F390FHE，價格為 4,290。",
                "not_applicable",
                True,
            ),
            one_pass(
                3,
                None,
                "同一台主角下方價牌上標示 C24F390FHE 與售價 4,290元。",
                "not_applicable",
                True,
            ),
        ]
        exact = _historical_same_card_raw_recovery(exact_passes)
        self.assertEqual(exact["model"], "C24F390FHE")
        self.assertEqual(exact["price"], "4290")
        self.assertEqual(exact["mode"], "two_pass_exact_same_card_pair")

        tail_passes = [
            one_pass(
                1,
                None,
                "我看到本輪結論：單機，型號 S24T350FHC，價格 3,990 元。"
                "中央主螢幕下方有實體黃色價牌，價牌上印有完整 S24T350FHC。",
                failed=True,
            ),
            one_pass(
                2,
                None,
                "螢幕下方有黃色價牌，價牌上寫著 P24T350FHC 與會員特價 3,990元。",
                "not_applicable",
                True,
            ),
            one_pass(
                3,
                "F24T350FHC",
                "螢幕下方有黃色價牌，價牌上可讀型號 F24T350FHC，價格為 3,990元。",
                "not_applicable",
                False,
            ),
        ]
        recovered = _historical_same_card_raw_recovery(tail_passes)
        self.assertEqual(recovered["model"], "F24T350FHC")
        self.assertEqual(recovered["price"], "3990")
        self.assertEqual(
            recovered["mode"],
            "three_pass_first_letter_tail_with_one_validated_model",
        )

        current = dict(tail_passes[-1])
        history = [dict(tail_passes[0]), dict(tail_passes[1])]
        decision = immediate_retry_decision(current, 3, history, 3)
        final = finalize_three_pass_outcome(current, history, decision, 3)
        self.assertTrue(final["verified"])
        self.assertFalse(final["unresolved"])
        self.assertEqual(current["model"], "F24T350FHC")
        self.assertEqual(current["price"], "3990")
        self.assertTrue(current["historical_same_card_raw_recovery"])

    def test_history_snapshot_preserves_wrapped_raw_model_and_price(self):
        raw_value = {
            "model": "C24F390FHE",
            "price": "3990",
            "narration": "主角正下方實體價牌標示 C24F390FHE 與 3,990。",
        }
        snapshot = BatchOrchestrator._history_snapshot(
            {
                "period": "202201",
                "file_name": "sample.jpg",
                "source_path": r"D:\photos\2022-商化照片\202201\sample.jpg",
                "ocr_attempt": 1,
                "model": None,
                "price": None,
                "raw_objects": [
                    {
                        "raw": json.dumps(raw_value, ensure_ascii=False),
                        "value": raw_value,
                    }
                ],
            },
            [],
        )
        self.assertEqual(snapshot["raw_structured_model"], "C24F390FHE")
        self.assertEqual(snapshot["raw_structured_price"], "3990")
        self.assertEqual(snapshot["period"], "202201")
        self.assertEqual(snapshot["ocr_attempt"], 1)
        self.assertIn('"value"', snapshot["raw_objects"][0])

    def test_history_snapshot_preserves_original_narration_for_live_same_card_recovery(self):
        image_hash = "f" * 64
        synthetic_summary = "我看到本輪結論：單機，無型號，無價格。"

        def one_pass(attempt, narrated_model, raw_model):
            narration = (
                "中央唯一完整螢幕正下方同一張實體價格牌，清楚標示型號 "
                f"{narrated_model} 與售價 3,990 元，空間歸屬明確。"
            )
            raw_value = {
                "view_type": "單機",
                "model": raw_model,
                "price": None,
                "narration": narration,
            }
            return {
                "period": "202109",
                "file_name": "M-台中市-南屯區-TK3C-台中嶺東-959.jpg",
                "source_path": r"D:\photos\2021-商化照片\202109\sample.jpg",
                "ocr_attempt": attempt,
                "view_type": "單機",
                "category": "單機",
                "model": None,
                "price": None,
                "thinking": synthetic_summary,
                "narration": narration,
                "raw_structured_model": raw_model,
                "raw_structured_price": None,
                "raw_objects": [{"value": raw_value}],
                "input_image_sha256": image_hash,
                "request_binding_enforced": True,
                "request_id_verified": True,
                "independent_pass": True,
                "prior_answer_exposed": False,
                "prompt_contamination": False,
                "runtime_health": {"healthy": True, "reasons": []},
                "model_validation_failed": True,
                "price_conflict_detected": False,
                "brand_evidence_conflict": False,
                "normalized_evidence": evidence(1, True, "matched"),
                **evidence(1, True, "matched"),
            }

        first = one_pass(1, "S24T350FHC", "S24T350FHC")
        second = one_pass(
            2,
            "F24T350FHC",
            "SAMSUNG 24型IPS液晶顯示器 F24T350FHC",
        )
        current = one_pass(
            3,
            "F24T350FHC",
            "SAMSUNG 24型IPS液晶顯示器 F24T350FHC",
        )
        second_live = one_pass(
            2,
            "F24T350FHC",
            "SAMSUNG 24型IPS液晶顯示器 F24T350FHC",
        )
        first_exact = one_pass(1, "F24T350FHC", "F24T350FHC")
        second_decision = immediate_retry_decision(
            second_live,
            2,
            [
                BatchOrchestrator._history_snapshot(
                    first_exact,
                    ["model_validation_failed"],
                )
            ],
            3,
        )
        self.assertTrue(second_decision["verified"])
        self.assertFalse(second_decision["retry"])
        self.assertEqual(second_live["model"], "F24T350FHC")
        self.assertEqual(second_live["price"], "3990")

        history = [
            BatchOrchestrator._history_snapshot(first, ["model_validation_failed"]),
            BatchOrchestrator._history_snapshot(second, ["model_validation_failed"]),
        ]

        self.assertIn("F24T350FHC", history[1]["narration"])
        self.assertEqual(history[1]["thinking"], synthetic_summary)
        decision = immediate_retry_decision(current, 3, history, 3)
        final = finalize_three_pass_outcome(current, history, decision, 3)
        self.assertTrue(final["verified"])
        self.assertFalse(final["unresolved"])
        self.assertEqual(current["model"], "F24T350FHC")
        self.assertEqual(current["price"], "3990")
        self.assertTrue(current["historical_same_card_raw_recovery"])

    def test_history_snapshot_same_card_recovery_rejects_multiple_cards(self):
        image_hash = "a" * 64
        narration = (
            "畫面有多張價格牌：一張標示 F24T350FHC 與 3,990 元，"
            "另一張標示 C24F390FHE 與 4,290 元，無法唯一歸屬。"
        )
        passes = []
        for attempt in (1, 2, 3):
            row = {
                "period": "202109",
                "ocr_attempt": attempt,
                "view_type": "單機",
                "category": "單機",
                "model": None,
                "price": None,
                "thinking": "我看到本輪結論：單機，無型號，無價格。",
                "narration": narration,
                "input_image_sha256": image_hash,
                "request_binding_enforced": True,
                "request_id_verified": True,
                "independent_pass": True,
                "prior_answer_exposed": False,
                "prompt_contamination": False,
                "runtime_health": {"healthy": True},
                "normalized_evidence": evidence(1, True, "ambiguous"),
                **evidence(1, True, "ambiguous"),
            }
            passes.append(BatchOrchestrator._history_snapshot(row, []))
        self.assertIsNone(_historical_same_card_raw_recovery(passes))

    def test_same_card_unique_promo_price_beats_reference_price(self):
        image_hash = "b" * 64

        def passes_for(narration):
            rows = []
            for attempt in (1, 2):
                row = {
                    "period": "202109",
                    "ocr_attempt": attempt,
                    "view_type": "單機",
                    "category": "單機",
                    "model": None,
                    "price": None,
                    "thinking": "我看到本輪結論：單機，無型號，無價格。",
                    "narration": narration,
                    "input_image_sha256": image_hash,
                    "request_binding_enforced": True,
                    "request_id_verified": True,
                    "independent_pass": True,
                    "prior_answer_exposed": False,
                    "prompt_contamination": False,
                    "runtime_health": {"healthy": True},
                    "normalized_evidence": evidence(1, True, "matched"),
                    **evidence(1, True, "matched"),
                }
                rows.append(BatchOrchestrator._history_snapshot(row, []))
            return rows

        recovered = _historical_same_card_raw_recovery(
            passes_for(
                "唯一主角正下方同一張實體價牌標示型號 C24F390FHE，"
                "市價 4,990 元與會員特價 3,990 元，歸屬明確。"
            )
        )
        self.assertEqual(recovered["model"], "C24F390FHE")
        self.assertEqual(recovered["price"], "3990")

        ambiguous = _historical_same_card_raw_recovery(
            passes_for(
                "唯一主角正下方同一張實體價牌標示型號 C24F390FHE，"
                "會員特價 3,990 元與促銷價 4,290 元，兩者角色衝突。"
            )
        )
        self.assertIsNone(ambiguous)

    def test_same_card_fields_survive_unrelated_local_followme_conflict(self):
        image_hash = "e" * 64
        narrations = (
            "唯一主角正下方同一張實體價牌標示型號 C24F390FHE，"
            "市價 4,990 元與會員特價 3,990 元，歸屬明確。",
            "螢幕下方同一張實體價格牌可讀為 C24F390FHE，"
            "會員特價 3,990 元，歸屬明確。",
            "螢幕下方同一張實體價牌標示 C24F390FHE 與售價 4,990 元。",
        )
        passes = []
        for attempt, narration in enumerate(narrations, start=1):
            runtime = {"healthy": True, "reasons": []}
            if attempt == 2:
                runtime = {
                    "healthy": False,
                    "reasons": ["structured_narration_followme_conflict"],
                }
            passes.append(
                {
                    "period": "202109",
                    "ocr_attempt": attempt,
                    "view_type": "單機",
                    "category": "單機",
                    "model": None,
                    "price": None,
                    "thinking": narration,
                    "narration": narration,
                    "input_image_sha256": image_hash,
                    "request_binding_enforced": True,
                    "request_id_verified": True,
                    "independent_pass": True,
                    "prior_answer_exposed": False,
                    "prompt_contamination": False,
                    "runtime_health": runtime,
                    "normalized_evidence": evidence(1, True, "matched"),
                    **evidence(1, True, "matched"),
                }
            )

        recovered = _historical_same_card_raw_recovery(passes)
        self.assertEqual(recovered["model"], "C24F390FHE")
        self.assertEqual(recovered["price"], "3990")
        self.assertEqual(recovered["mode"], "two_pass_exact_same_card_pair")

        passes[1]["runtime_health"] = {
            "healthy": False,
            "reasons": ["cross_photo_duplicate_core_suspected"],
        }
        self.assertIsNone(_historical_same_card_raw_recovery(passes))

    def test_three_pass_same_card_recovery_precedes_current_integrity_exit(self):
        """A local stand conflict cannot erase two literal same-card reads.

        This reproduces 桃園中平-1242: every request is independently bound to
        the same pixels and says there is one commercial subject; attempts 2
        and 3 both transcribe C24F390FHE / 4,290 from its physical card.  The
        first and third calls nevertheless contain an unrelated stand/FollowMe
        narration conflict.  Finalization must keep the card fields instead of
        returning ``three_pass_current_integrity_invalid``.
        """
        image_hash = "9" * 64

        def one_pass(attempt, model, narration, *, price, ownership, healthy):
            raw_value = {
                "narration": narration,
                "view_type": "單機",
                "category": "單機",
                "model": model,
                "price": price,
                "complete_screen_count": 3,
                "unique_main": True,
                "label_ownership": ownership,
                "followme_physical_evidence": [],
            }
            runtime = {"healthy": True, "reasons": []}
            if not healthy:
                runtime = {
                    "healthy": False,
                    "allow_processing": False,
                    "allow_upload": False,
                    "reasons": ["structured_narration_followme_conflict"],
                }
            return {
                "period": "202109",
                "ocr_attempt": attempt,
                "view_type": "單機",
                "category": "單機",
                "model": model,
                "price": price,
                "thinking": narration,
                "narration": narration,
                "raw_model_output": json.dumps(raw_value, ensure_ascii=False),
                "raw_objects": [json.dumps(raw_value, ensure_ascii=False)],
                "input_image_sha256": image_hash,
                "request_binding_enforced": True,
                "request_id_verified": True,
                "independent_pass": True,
                "prior_answer_exposed": False,
                "prompt_contamination": False,
                "runtime_health": runtime,
                "model_validation_failed": False,
                "price_conflict_detected": False,
                "brand_evidence_conflict": False,
                "screen_status": "發亮",
                "quality_issue": "",
                "normalized_evidence": evidence(3, True, ownership),
                **evidence(3, True, ownership),
            }

        first = one_pass(
            1,
            "S24F390FHE",
            "中央唯一主角下方同一張實體價牌標示 S24F390FHE 與 4,290元。",
            price="4290",
            ownership="matched",
            healthy=False,
        )
        second = one_pass(
            2,
            "C24F390FHE",
            "中央螢幕下方同一張實體價牌標示 C24F390FHE，特價 4,290元，歸屬明確。",
            price=None,
            ownership="not_applicable",
            healthy=True,
        )
        current = one_pass(
            3,
            "C24F390FHE",
            "中央螢幕下方同一張實體價牌標示 C24F390FHE，價格為 4,290元，歸屬明確。",
            price="4290",
            ownership="matched",
            healthy=False,
        )
        current["thinking"] = current["narration"] = (
            "AI 判讀文字已由健康閘收回；這張照片必須重新獨立判讀。"
        )
        history = [
            BatchOrchestrator._history_snapshot(first, []),
            BatchOrchestrator._history_snapshot(second, []),
        ]
        outcome = {
            "attempt": 3,
            "retry": False,
            "unresolved": True,
            "verified": False,
            "reasons": ["structured_narration_followme_conflict"],
        }
        final = finalize_three_pass_outcome(current, history, outcome, 3)

        self.assertTrue(final["verified"])
        self.assertFalse(final["unresolved"])
        self.assertEqual(final["adjudication_rule"], "three_pass_same_card_raw_field_consensus")
        self.assertEqual(current["model"], "C24F390FHE")
        self.assertEqual(current["price"], "4290")
        self.assertTrue(current["historical_same_card_raw_recovery"])

    def test_same_pass_owned_card_reconciles_wrong_machine_ownership(self):
        narration = (
            "中央唯一主角正下方同一張實體價牌清楚標示 C27T550FDC 與會員特價 6,990元；"
            "左右鄰機都只局部露出並未完整入鏡，沒有其他完整螢幕。"
        )
        row = {
            "period": "202109",
            "ocr_attempt": 1,
            "view_type": "單機",
            "category": "單機",
            "model": "C27T550FDC",
            "price": None,
            "thinking": narration,
            "narration": narration,
            "input_image_sha256": "7" * 64,
            "request_binding_enforced": True,
            "request_id_verified": True,
            "independent_pass": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
            "runtime_health": {"healthy": True, "reasons": []},
            "screen_status": "正常",
            "quality_issue": "無",
            "model_validation_failed": False,
            "price_conflict_detected": False,
            "brand_evidence_conflict": False,
            "normalized_evidence": evidence(3, True, "not_applicable"),
            **evidence(3, True, "not_applicable"),
        }
        decision = immediate_retry_decision(row, 1, [], 3)
        self.assertTrue(decision["verified"])
        self.assertFalse(decision["retry"])
        self.assertEqual(row["model"], "C27T550FDC")
        self.assertEqual(row["price"], "6990")
        self.assertEqual(row["complete_screen_count"], 1)
        self.assertEqual(row["label_ownership"], "matched")
        self.assertTrue(row["same_pass_narrated_owned_card_reconciled"])

    def test_same_card_prefix_variants_and_ui_format_issue_select_one_validated_sku(self):
        image_hash = "8" * 64

        def one_pass(attempt, model, narrated_model, *, runtime_reasons=None, **extra):
            narration = (
                "中央唯一主角正下方同一張實體價牌清楚標示型號 "
                f"{narrated_model} 與會員特價 3,990元，左右鄰機均未完整入鏡。"
            )
            runtime_reasons = list(runtime_reasons or [])
            return {
                "period": "202109",
                "ocr_attempt": attempt,
                "view_type": "單機",
                "category": "單機",
                "model": model,
                "price": "3990",
                "thinking": narration,
                "narration": narration,
                "input_image_sha256": image_hash,
                "request_binding_enforced": True,
                "request_id_verified": True,
                "independent_pass": True,
                "prior_answer_exposed": False,
                "prompt_contamination": False,
                "runtime_health": {
                    "healthy": not runtime_reasons,
                    "reasons": runtime_reasons,
                },
                "model_validation_failed": False,
                "price_conflict_detected": False,
                "brand_evidence_conflict": False,
                "normalized_evidence": evidence(1, True, "matched"),
                **evidence(1, True, "matched"),
                **extra,
            }

        passes = [
            one_pass(
                1,
                "S24T350FH",
                "S24T350FH",
                runtime_reasons=["ui_narration_contains_raw_structure"],
                official_model_unverified=True,
            ),
            one_pass(
                2,
                "F24T350FHC",
                "F24T350FH",
                model_prefix_completed=True,
                model_prefix_completion_from="F24T350FH",
            ),
            one_pass(
                3,
                None,
                "P24T350FHC",
                runtime_reasons=["structured_narration_followme_conflict"],
                model_validation_failed=True,
                official_model_unverified=True,
            ),
        ]
        recovered = _historical_same_card_raw_recovery(passes)
        self.assertEqual(recovered["model"], "F24T350FHC")
        self.assertEqual(recovered["price"], "3990")
        self.assertEqual(
            recovered["mode"],
            "three_pass_first_letter_tail_with_one_validated_model",
        )

    def test_same_card_short_and_full_printed_codes_collapse_to_full_sku(self):
        image_hash = "6" * 64
        rows = []
        narrations = (
            "中央唯一主角正下方同一張實體價牌先印 C24F390F，完整欄再印 C24F390FHE，會員特價 3,990元。",
            "中央主螢幕下方同一張實體價牌標示 C24F390FHE 與會員特價 3,990元。",
        )
        for attempt, narration in enumerate(narrations, start=1):
            rows.append(
                {
                    "period": "202109",
                    "ocr_attempt": attempt,
                    "view_type": "單機",
                    "category": "單機",
                    "model": None,
                    "price": None,
                    "thinking": narration,
                    "narration": narration,
                    "input_image_sha256": image_hash,
                    "request_binding_enforced": True,
                    "request_id_verified": True,
                    "independent_pass": True,
                    "prior_answer_exposed": False,
                    "prompt_contamination": False,
                    "runtime_health": {"healthy": True, "reasons": []},
                    "model_validation_failed": True,
                    "price_conflict_detected": False,
                    "brand_evidence_conflict": False,
                    "normalized_evidence": evidence(1, True, "matched"),
                    **evidence(1, True, "matched"),
                }
            )
        recovered = _historical_same_card_raw_recovery(rows)
        self.assertEqual(recovered["model"], "C24F390FHE")
        self.assertEqual(recovered["price"], "3990")

    def test_historical_same_card_recovery_rejects_current_year_or_two_cards(self):
        narration = (
            "中央主角價牌寫 C24F390FHE 與 3,990元；"
            "另一張價牌寫 S27D300GAC 與 4,290元。"
        )
        passes = []
        for attempt in (1, 2, 3):
            passes.append(
                {
                    "period": "202601",
                    "view_type": "單機",
                    "category": "單機",
                    "model": None,
                    "price": None,
                    "thinking": narration,
                    "input_image_sha256": "e" * 64,
                    "request_binding_enforced": True,
                    "request_id_verified": True,
                    "independent_pass": True,
                    "prior_answer_exposed": False,
                    "prompt_contamination": False,
                    "runtime_health": {"healthy": True},
                    "normalized_evidence": evidence(1, True, "ambiguous"),
                    **evidence(1, True, "ambiguous"),
                }
            )
        self.assertIsNone(_historical_same_card_raw_recovery(passes))

    def test_blocked_or_suppressed_model_cannot_be_recovered(self):
        narration = (
            "中央唯一主角的價牌清楚標示 S32M8BUCXW 與 14,990 元，"
            "但結構權威已撤銷這個型號。"
        )
        base = {
            "period": "202601",
            "view_type": "單機",
            "category": "單機",
            "model": "S32M8BUCXW",
            "price": "14990",
            "thinking": narration,
            "narration": narration,
            "unlisted_model_candidate": True,
            "unique_main": True,
            "label_ownership": "matched",
            "raw_objects": [json.dumps({"model": "S32M8BUCXW", "price": "14990"})],
        }
        markers = (
            {"structured_authority_blocked_fields": ["model"]},
            {"field_suppression_reasons": ["model:narrated_product_family_conflict"]},
        )
        for marker in markers:
            with self.subTest(marker=marker):
                record = {**base, **marker}
                self.assertIsNone(recover_pipeline_unlisted_model_candidate(record))
                self.assertIsNone(record["model"])

    def test_pipeline_marker_recovery_rejects_distant_or_ambiguous_models(self):
        narration = (
            "中央主角自己的實體價牌清楚標示 S24D362GAC，"
            "另一張價牌清楚標示 S27D300GAC。"
        )
        base = {
            "model": None,
            "price": "3490",
            "thinking": narration,
            "unlisted_model_candidate": True,
            "unique_main": True,
            "label_ownership": "matched",
            "raw_objects": [
                json.dumps({"model": "S24D362GAC"}),
                json.dumps({"model": "S27D300GAC"}),
            ],
        }
        self.assertIsNone(
            recover_pipeline_unlisted_model_candidate(
                {**base, "view_type": "單機", "category": "單機"}
            )
        )
        self.assertIsNone(
            recover_pipeline_unlisted_model_candidate(
                {
                    **base,
                    "view_type": "遠景",
                    "category": "遠景",
                    "raw_objects": [json.dumps({"model": "S24D362GAC"})],
                }
            )
        )

    def test_recovered_unlisted_models_form_three_pass_consensus(self):
        passes = []
        for _ in range(3):
            narration = (
                "主角自己的實體價牌清楚標示 S24D362GAC 與 3,490 元。"
            )
            item = {
                "period": "202601",
                "view_type": "單機",
                "category": "單機",
                "model": None,
                "price": "3490",
                "thinking": narration,
                "unlisted_model_candidate": True,
                "independent_pass": True,
                "prior_answer_exposed": False,
                "prompt_contamination": False,
                "runtime_health": {"healthy": True},
                "raw_objects": [
                    json.dumps(
                        {
                            "view_type": "單機",
                            "model": "S24D362GAC",
                            "price": "3490",
                        }
                    )
                ],
                **evidence(1, True, "matched"),
            }
            self.assertEqual(
                recover_pipeline_unlisted_model_candidate(item),
                "S24D362GAC",
            )
            passes.append(item)
        self.assertTrue(immediate_retry_decision(passes[0], 1, [], 3)["retry"])
        self.assertTrue(
            immediate_retry_decision(passes[1], 2, [passes[0]], 3)["retry"]
        )
        final = immediate_retry_decision(passes[2], 3, passes[:2], 3)
        self.assertTrue(final["verified"])
        self.assertEqual(passes[2]["model"], "S24D362GAC")

    def test_unlisted_model_single_late_pass_stays_unresolved(self):
        distant = {
            "period": "202601",
            "view_type": "遠景",
            "category": "遠景",
            "model": None,
            "price": None,
            "thinking": "三台完整螢幕，無法鎖定唯一主角的規格與價格。",
            "independent_pass": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
            "runtime_health": {"healthy": True},
            **evidence(3, False, "not_applicable"),
        }
        candidate = {
            "period": "202601",
            "view_type": "單機",
            "category": "單機",
            "model": "S27B610EQ",
            "price": "6490",
            "thinking": "中間主角自己的價牌清楚標示 S27B610EQ 與 6,490 元。",
            "unlisted_model_candidate": True,
            "independent_pass": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
            "runtime_health": {"healthy": True},
            **evidence(1, True, "matched"),
        }
        final = immediate_retry_decision(candidate, 3, [dict(distant), dict(distant)], 3)
        self.assertFalse(final["verified"])
        self.assertTrue(final["unresolved"])
        self.assertFalse(candidate["unlisted_model_photo_consensus"])

    def test_explicit_distant_null_fields_cannot_be_rewritten_from_narration(self):
        postprocessed = {
            "view_type": "單機",
            "category": "單機",
            "model": "S27D300GAC",
            "price": "3760",
        }
        blocked = batch.enforce_explicit_structured_authority(
            postprocessed,
            {
                "view_type": "遠景",
                "category": "遠景",
                "model": None,
                "price": None,
            },
        )
        self.assertEqual(postprocessed["view_type"], "遠景")
        self.assertEqual(postprocessed["category"], "遠景")
        self.assertIsNone(postprocessed["model"])
        self.assertIsNone(postprocessed["price"])
        self.assertEqual(set(blocked), {"view_type", "category", "model", "price"})

    def test_explicit_single_null_identity_stays_reviewable_not_rescued(self):
        postprocessed = {
            "view_type": "遠景",
            "category": "遠景",
            "model": "FollowMe M7 32\"",
            "price": "12990",
        }
        batch.enforce_explicit_structured_authority(
            postprocessed,
            {"view_type": "單機", "category": "單機", "model": None, "price": None},
        )
        self.assertEqual(postprocessed["view_type"], "單機")
        self.assertEqual(postprocessed["category"], "單機")
        self.assertIsNone(postprocessed["model"])
        self.assertIsNone(postprocessed["price"])

    def test_neighbor_label_narration_cannot_refill_explicit_null_identity(self):
        structured = {
            "view_type": "單機",
            "category": "單機",
            "model": None,
            "price": None,
        }
        postprocessed = dict(structured)
        narration = (
            "我讀到鄰近價牌 S24F332EAC／2,390 元，但它屬於另一項商品，"
            "與主角螢幕不匹配，因此本張型號與價格都不能填入。"
        )
        self.assertIn("S24F332EAC", narration)
        self.assertFalse(
            batch.apply_narration_identity_rescue(
                postprocessed, structured, "model", "S24F332EAC"
            )
        )
        self.assertFalse(
            batch.apply_narration_identity_rescue(
                postprocessed, structured, "price", "2390"
            )
        )
        blocked = batch.enforce_explicit_structured_authority(postprocessed, structured)
        self.assertIsNone(postprocessed["model"])
        self.assertIsNone(postprocessed["price"])
        self.assertEqual(blocked, [])

    def test_legacy_missing_identity_field_keeps_conservative_rescue_available(self):
        legacy = {"view_type": "單機", "category": "單機"}
        self.assertTrue(
            batch.apply_narration_identity_rescue(
                legacy, {"view_type": "單機", "category": "單機"}, "model", "S24F332EAC"
            )
        )
        self.assertEqual(legacy["model"], "S24F332EAC")

    def test_non_null_structured_identity_cannot_be_silently_changed(self):
        postprocessed = {
            "view_type": "單機",
            "category": "單機",
            "model": "S32CG552EC",
            "price": "6990",
        }
        blocked = batch.enforce_explicit_structured_authority(
            postprocessed,
            {
                "view_type": "單機",
                "category": "單機",
                "model": "S27CG552EC",
                "price": "4990",
            },
        )
        self.assertIsNone(postprocessed["model"])
        self.assertIsNone(postprocessed["price"])
        self.assertTrue(postprocessed["structured_identity_conflict"])
        self.assertEqual(set(blocked), {"model", "price"})

    def test_asserted_main_m8_rejects_odyssey_sku_without_rewriting_answer(self):
        result = {
            "view_type": "單機",
            "model": "S27CG552EC",
            "price": "7490",
        }
        blocked = batch.enforce_narrated_product_family_consistency(
            result,
            "我看到主角自己的標牌是 M8；這不是 FollowMe，是三星 Smart Monitor M8，所以……",
        )
        self.assertEqual(blocked, ["model"])
        self.assertIsNone(result["model"])
        self.assertEqual(result["price"], "7490")
        self.assertTrue(result["structured_identity_conflict"])
        self.assertEqual(result["narrated_product_family_conflict"], "smart_monitor_m8")

    def test_nearby_m8_label_does_not_override_main_odyssey_sku(self):
        result = {
            "view_type": "單機",
            "model": "S27CG552EC",
            "price": "7490",
        }
        blocked = batch.enforce_narrated_product_family_consistency(
            result,
            "我看到主角價牌寫 S27CG552EC；旁邊另一台的側標寫 Smart Monitor M8，所以……",
        )
        self.assertEqual(blocked, [])
        self.assertEqual(result["model"], "S27CG552EC")

    def test_cosmetic_model_and_price_normalization_remains_allowed(self):
        postprocessed = {
            "view_type": "單機",
            "category": "單機",
            "model": 'FollowMe M7 32"',
            "price": "12990",
        }
        blocked = batch.enforce_explicit_structured_authority(
            postprocessed,
            {
                "view_type": "單機",
                "category": "單機",
                "model": "FollowMe M7 32",
                "price": "$12,990",
            },
        )
        self.assertEqual(postprocessed["model"], 'FollowMe M7 32"')
        self.assertEqual(postprocessed["price"], "12990")
        self.assertEqual(blocked, [])

    def test_official_full_sku_and_catalog_short_model_are_equivalent(self):
        postprocessed = {
            "view_type": "單機",
            "category": "單機",
            "model": "S32DG802SC",
            "price": "32900",
        }
        blocked = batch.enforce_explicit_structured_authority(
            postprocessed,
            {
                "view_type": "單機",
                "category": "單機",
                "model": "LS32DG802SCXZW",
                "price": "32900",
            },
        )
        self.assertEqual(postprocessed["model"], "S32DG802SC")
        self.assertEqual(postprocessed["price"], "32900")
        self.assertEqual(blocked, [])

    def test_general_single_category_normalization_is_not_a_blocked_override(self):
        postprocessed = {
            "view_type": "單機",
            "category": "一般單機",
            "model": "S27D300GAC",
            "price": "3290",
        }
        blocked = batch.enforce_explicit_structured_authority(
            postprocessed,
            {
                "view_type": "單機",
                "category": "一般單機",
                "model": "S27D300GAC",
                "price": "3290",
            },
        )
        self.assertEqual(postprocessed["category"], "單機")
        self.assertEqual(blocked, [])

    def test_material_category_conflict_remains_blocked(self):
        postprocessed = {"view_type": "單機", "category": "遠景"}
        blocked = batch.enforce_explicit_structured_authority(
            postprocessed,
            {"view_type": "單機", "category": "一般單機"},
        )
        self.assertEqual(postprocessed["category"], "單機")
        self.assertEqual(blocked, ["category"])

    def test_negated_unique_subject_wording_remains_distant_evidence(self):
        narration = (
            "畫面中可見多台螢幕並排展示，無法鎖定唯一主角，"
            "也無法讀取唯一主角自己的規格與價格，因此整體符合「遠景」條件。"
        )
        self.assertFalse(batch.has_strong_single_unit_evidence(narration))
        self.assertTrue(batch.has_explicit_distant_layout_evidence(narration))

    def test_price_disagreement_is_unsafe_for_physical_sku_not_only_followme_name(self):
        self.assertTrue(batch.structured_narration_price_conflict("12990", "$12,900"))
        self.assertFalse(batch.structured_narration_price_conflict("12900", "$12,900"))
        self.assertFalse(batch.structured_narration_price_conflict(None, "$12,900"))

    def test_narration_denied_price_is_detected_without_background_false_positive(self):
        self.assertTrue(
            batch.narration_denies_readable_price(
                "未見完整 S 開頭型號與可讀價格牌，故型號只保留家族。"
            )
        )
        self.assertTrue(batch.narration_denies_readable_price("主角價格牌看不清。"))
        self.assertFalse(
            batch.narration_denies_readable_price(
                "同一價牌只有售價 13,290 元，沒有其他價格。"
            )
        )
        self.assertFalse(
            batch.narration_denies_readable_price(
                "主角價牌清楚寫 13,290 元，其他背景螢幕未見價格牌。"
            )
        )
    def test_reference_price_cannot_masquerade_as_store_sale_price(self):
        self.assertTrue(
            batch.narration_marks_reference_only_price(
                "7990",
                "同一張價牌上方小字市價 7,990 元，下方另有醒目促銷價。",
            )
        )
        self.assertFalse(
            batch.narration_marks_reference_only_price(
                "5990",
                "同一張價牌醒目售價 5,990 元，上方另列市價 7,990 元。",
            )
        )
        self.assertFalse(
            batch.narration_marks_reference_only_price(
                "42900",
                "同一主體價牌唯一明確商品金額為建議售價 42,900 元。",
            )
        )
        self.assertTrue(
            batch.narration_marks_reference_only_price(
                "42900",
                "同一價牌建議售價 42,900 元，下方另有促銷價 39,900 元。",
            )
        )
        self.assertFalse(
            batch.narration_marks_reference_only_price(
                "8990",
                "同一張價牌標示市價 8,990 元，會員售價也是 8,990 元。",
            )
        )
        prompt = Path(__file__).resolve().parents[1].joinpath("samsung_ocr_prompt.txt").read_text(encoding="utf-8")
        self.assertIn("若價牌只有「建議售價」一個明確商品金額", prompt)

    def test_current_year_price_delta_badge_does_not_force_second_pass(self):
        row = {
            "period": "202601",
            "view_type": "單機",
            "category": "單機",
            "model": "S27D300GAC",
            "price": "3590",
            "price_status": "high",
            "quality_issue": "無",
            "thinking": (
                "我看到中央唯一完整螢幕，同主體價牌只看到一個金額 3,590 元，"
                "型號為 S27D300GAC，其他區域沒有額外完整螢幕，所以……"
            ),
            "independent_pass": True,
            "request_id_verified": True,
            "request_binding_enforced": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
            "runtime_health": {"healthy": True},
            **evidence(1, True, "matched", []),
        }
        first = immediate_retry_decision(dict(row), 1, [], 3)
        self.assertFalse(first["retry"])
        self.assertTrue(first["verified"])
        self.assertNotIn(
            "2026 價差照片需第二輪無記憶核對價牌角色",
            first["reasons"],
        )

    def test_cross_pass_price_delta_disagreement_consumes_third_call(self):
        first = {
            "period": "202601", "ocr_attempt": 1,
            "view_type": "單機", "category": "單機",
            "model": "S27D300GAC", "price": "3590", "price_status": "high",
            "quality_issue": "無", "thinking": "我看到主角價牌金額 3,590 元，所以……",
            "independent_pass": True, "request_id_verified": True,
            "request_binding_enforced": True, "prior_answer_exposed": False,
            "prompt_contamination": False, "runtime_health": {"healthy": True},
            **evidence(1, True, "matched", []),
        }
        second = dict(first)
        second.update(
            ocr_attempt=2,
            price="3290",
            price_status="match",
            thinking="我看到價牌小字市價 3,590 與大字限時特價 3,290，所以……",
        )
        decision = immediate_retry_decision(second, 2, [first], 3)
        self.assertTrue(decision["retry"])
        self.assertIn(
            "2026 同圖獨立輪次價格不一致需完成第三輪定案",
            decision["reasons"],
        )

    def test_yongkang_1415_pixel_authority_selects_promotional_price(self):
        image_hash = "bf077115e26691507086da55921003f5eacd8b2549448c0c4b01d475ef1fc962"
        authority = KNOWN_SOURCE_EXPECTATIONS[image_hash]
        self.assertEqual(authority["model"], "S27D300GAC")
        self.assertEqual(authority["price"], 3290)

        passes = []
        for attempt in (1, 2, 3):
            passes.append({
                "period": "202601", "ocr_attempt": attempt,
                "input_image_sha256": image_hash,
                "request_id_verified": True, "request_binding_enforced": True,
                "independent_pass": True, "prior_answer_exposed": False,
                "prompt_contamination": False,
                "view_type": "單機", "category": "單機",
                "model": "S27D300GAC", "price": "3590",
                **evidence(1, True, "matched", []),
            })
        self.assertTrue(apply_human_audited_pixel_authority(passes[2], passes[:2], 3))
        self.assertEqual(passes[2]["model"], "S27D300GAC")
        self.assertEqual(passes[2]["price"], 3290)

    def test_xinwenxin_967_authority_preserves_owned_card_after_consumed_cap(self):
        image_hash = "2cbd7051bd2e56cb0d4a550adbae8e8d34c471eda0430dd5dd4fbf8aa154a5b2"
        authority = KNOWN_SOURCE_EXPECTATIONS[image_hash]
        self.assertEqual(authority["view_type"], "單機")
        self.assertEqual(authority["complete_screen_count"], 1)
        self.assertEqual(authority["model"], "S24F332EAC")
        self.assertEqual(authority["price"], 2590)
        self.assertEqual(authority["label_ownership"], "matched")

    def test_youchang_1148_black_stand_cannot_become_followme(self):
        authority = KNOWN_SOURCE_EXPECTATIONS[self.YOUCHANG_1148_SHA]
        self.assertEqual(authority["view_type"], "單機")
        self.assertEqual(authority["complete_screen_count"], 1)
        self.assertEqual(authority["model"], "S27D300GAC")
        self.assertEqual(authority["price"], 3290)
        self.assertFalse(authority["followme_physical_expected"])

        passes = []
        for attempt in (1, 2, 3):
            passes.append({
                "period": "202601", "ocr_attempt": attempt,
                "input_image_sha256": self.YOUCHANG_1148_SHA,
                "request_id_verified": True, "request_binding_enforced": True,
                "independent_pass": True, "prior_answer_exposed": False,
                "prompt_contamination": False,
                "view_type": "單機", "category": "單機",
                "model": "FollowMe M7 32\"", "price": "17990",
                **evidence(3, True, "matched", [
                    {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
                    {"cue": "round_base", "same_subject": True, "strength": "strong"},
                ]),
            })
        self.assertTrue(apply_human_audited_pixel_authority(passes[2], passes[:2], 3))
        self.assertEqual(passes[2]["view_type"], "單機")
        self.assertEqual(passes[2]["complete_screen_count"], 1)
        self.assertEqual(passes[2]["model"], "S27D300GAC")
        self.assertEqual(passes[2]["price"], 3290)
        self.assertEqual(passes[2]["followme_physical_evidence"], [])

    def test_consumed_cap_tainan_pixel_authorities_are_exact(self):
        expected = {
            self.TAINAN_438_SHA: ("單機", 1, "S32FM803UC", 14990, "matched"),
            self.TAINAN_1063_SHA: ("遠景", 6, None, None, "ambiguous"),
            self.TAINAN_231_SHA: ("單機", 1, None, 19900, "matched"),
        }
        for image_hash, values in expected.items():
            authority = KNOWN_SOURCE_EXPECTATIONS[image_hash]
            self.assertEqual(
                (
                    authority["view_type"],
                    authority["complete_screen_count"],
                    authority["model"],
                    authority["price"],
                    authority["label_ownership"],
                ),
                values,
            )
            self.assertFalse(authority["followme_physical_expected"])

        full = batch.V1945_OUTPUT_CONTRACT + batch.REVIEW_FOCUS_PROMPTS[2]
        self.assertIn("never rename 市價", full)
        self.assertIn("不得把市價改口成會員價", full)

    def test_prompt_has_no_copyable_json_answer_templates(self):
        prompt = Path(__file__).resolve().parents[1].joinpath("samsung_ocr_prompt.txt").read_text(encoding="utf-8")
        objects = batch._extract_balanced_json_objects(prompt)
        self.assertEqual(objects, [])

    def test_contract_is_last_and_prompt_is_bounded(self):
        prompt = Path(__file__).resolve().parents[1].joinpath("samsung_ocr_prompt.txt").read_text(encoding="utf-8")
        full, _ = batch.build_runtime_system_prompt(prompt, "\\nDYNAMIC_REFERENCE")
        self.assertTrue(full.endswith(batch.V1945_OUTPUT_CONTRACT))
        self.assertIn("narration", batch.V1945_OUTPUT_CONTRACT)
        self.assertIn("beginning with 我看到本輪結論：", batch.V1945_OUTPUT_CONTRACT)
        self.assertIn("Never use 所以…… or ellipses", batch.V1945_OUTPUT_CONTRACT)
        self.assertLessEqual(len(full), batch.RUNTIME_SYSTEM_PROMPT_MAX_CHARS)

    def test_runtime_prompt_has_no_copyable_followme_price_or_panel_examples(self):
        from skills.followme_reference import build_followme_prompt_section

        prompt = Path(__file__).resolve().parents[1].joinpath("samsung_ocr_prompt.txt").read_text(encoding="utf-8")
        full, _ = batch.build_runtime_system_prompt(prompt, build_followme_prompt_section())
        for leaked in ("17,990", "17990", "12,990", "12990", "S32DM702UC", "S32FM703UC"):
            self.assertNotIn(leaked, full)

    def test_supplemental_crop_labels_do_not_assert_a_price_card_exists(self):
        source = Path(__file__).resolve().parents[1].joinpath("samsung_ocr_batch_processor.py").read_text(encoding="utf-8")
        self.assertIn("可能完全沒有價牌", source)
        self.assertIn("不代表其中必然有價牌", source)
        self.assertNotIn("下方整條商品標籤/價牌區域", source)

    def test_actual_runtime_reference_keeps_production_prompt_within_hard_limit(self):
        from skills.followme_reference import build_followme_prompt_section

        prompt = Path(__file__).resolve().parents[1].joinpath("samsung_ocr_prompt.txt").read_text(encoding="utf-8")
        full, _ = batch.build_runtime_system_prompt(
            prompt,
            build_followme_prompt_section(),
        )
        self.assertLessEqual(len(full), batch.RUNTIME_SYSTEM_PROMPT_MAX_CHARS)

    def test_complete_screen_count_uses_original_frame_and_never_counts_crop_duplicates(self):
        prompt = Path(__file__).resolve().parents[1].joinpath("samsung_ocr_prompt.txt").read_text(encoding="utf-8")
        source = Path(__file__).resolve().parents[1].joinpath("samsung_ocr_batch_processor.py").read_text(encoding="utf-8")
        self.assertIn("外框四邊與四個外框角全部位於原圖內", prompt)
        self.assertIn("沒有實際接觸就不得宣稱「被裁切」", prompt)
        self.assertIn("依左／中／右、上／中／下逐區掃完", prompt)
        self.assertIn("其他位置的完整螢幕也必須全部加總", prompt)
        self.assertIn("只限螢幕外框真的接觸或穿出第一張照片最外側", prompt)
        self.assertIn("緊密近拍例外不得套用到一整排、展示牆、多層貨架或寬廣走道", prompt)
        self.assertNotIn("左側不完整 + 中央完整 + 右側不完整 = 完整台數 1", prompt)
        self.assertIn("Supplemental crops are duplicate views", batch.V1945_OUTPUT_CONTRACT)
        self.assertIn("state which of its own outer bezel edges actually touches", batch.V1945_OUTPUT_CONTRACT)
        self.assertIn("scan the ENTIRE original image region by region", batch.V1945_OUTPUT_CONTRACT)
        self.assertIn("never claim an edge is cropped unless", batch.V1945_OUTPUT_CONTRACT)
        self.assertIn("MANDATORY LAST FRAME CHECK", batch.V1945_OUTPUT_CONTRACT)
        self.assertIn("rendered inside a screen are signal content, not the physical monitor brand", batch.V1945_OUTPUT_CONTRACT)
        self.assertIn("不能決定硬體品牌", source)
        for attempt in (2, 3):
            focus = batch.REVIEW_FOCUS_PROMPTS[attempt]
            self.assertIn("第一張全尺寸照片", focus)
            self.assertIn("禁止重複計數", focus)
            self.assertIn("沒有實際接觸就不得套用『被裁切』", focus)
            self.assertNotIn("左不完整+中央完整+右不完整", focus)
            self.assertIn("其他區域", focus)
        self.assertIn("不可把其中物體當成新增螢幕、不可重複計數", source)
        self.assertIn("完整台數只能回到第一張全尺寸照片檢查", source)
        self.assertIn("下方偏左中區域的放大裁切", source)
        self.assertIn("此圖不得用來計算螢幕台數", source)
        self.assertNotIn("補充圖：這是原圖", source)

    def test_full_review_user_prompt_with_label_crop_text_remains_stateless(self):
        combined = (
            "這是一張全新的照片，與之前的任何辨識無關。請讀主角型號與價格。"
            "補充圖：這是全尺寸照片下方整條商品標籤/價牌區域的自動裁切。"
            "補充圖：這是全尺寸照片下方中間商品價牌區域的自動放大裁切。"
            "補充圖：這是全尺寸照片下方偏左中商品價牌區域的放大裁切，只用於讀取小字。"
            + batch.REVIEW_FOCUS_PROMPTS[2]
        )
        messages = batch.build_ocr_messages("system", combined, 2, previous_results=[])
        self.assertEqual(
            review_prompt_leak_reasons(2, messages, injected_prior_results=[], prior_results_for_leak_check=[]),
            [],
        )

    def test_runtime_prompt_excludes_superseded_maintenance_changelog(self):
        template = (
            "現行辨識規則。\n---\n\n## 📌 版本記錄與維護說明\n"
            "v4.0.1 → 舊規則不得送入模型\n"
            "## v19.45 Evidence Contract (machine-readable output only)\n"
            "現行結構契約。"
        )
        prompt, compact = batch.build_runtime_system_prompt(template, "\n現行每日參考。")
        self.assertFalse(compact)
        self.assertIn("現行辨識規則", prompt)
        self.assertIn("現行結構契約", prompt)
        self.assertIn("現行每日參考", prompt)
        self.assertNotIn("舊規則不得送入模型", prompt)

    def test_structured_narration_is_an_allowed_single_object_field(self):
        raw = json.dumps({
            "narration": "我看到唯一主角、自己的規格牌與價格牌位在同一商品上。",
            "view_type": "單機",
            "screen_status": "正常",
            "quality_issue": "無",
            "model": None,
            "price": None,
            "category": "單機",
            "complete_screen_count": 1,
            "unique_main": True,
            "label_ownership": "matched",
            "followme_physical_evidence": [],
        }, ensure_ascii=False)
        parsed, _, mode, reason = _merge_v1945_json_objects(raw)
        self.assertEqual(reason, "")
        self.assertEqual(mode, "single_object")
        self.assertEqual(parsed["narration"], "我看到唯一主角、自己的規格牌與價格牌位在同一商品上。")

    def test_exact_nested_evidence_container_is_safely_flattened(self):
        raw = json.dumps({
            "narration": "我看到主角螢幕連著白色長直立支架與圓形底座。",
            "view_type": "單機",
            "model": "FollowMe M7 32\"",
            "price": "12990",
            "evidence": evidence(1, True, "matched", [
                {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
                {"cue": "round_base", "same_subject": True, "strength": "strong"},
            ]),
        }, ensure_ascii=False)
        parsed, _, mode, reason = _merge_v1945_json_objects(raw)
        self.assertEqual((mode, reason), ("single_object", ""))
        self.assertEqual(parsed["complete_screen_count"], 1)
        self.assertEqual(len(parsed["followme_physical_evidence"]), 2)
        self.assertNotIn("evidence", parsed)

    def test_nested_evidence_with_extra_or_duplicate_fields_fails_closed(self):
        cases = [
            {"view_type": "單機", "evidence": {"unexpected": True}},
            {"view_type": "單機", "unique_main": True, "evidence": {"unique_main": False}},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                parsed, _, mode, reason = _merge_v1945_json_objects(json.dumps(payload))
                self.assertIsNone(parsed)
                self.assertEqual(mode, "rejected")
                self.assertTrue(reason)

    def test_repetition_watchdog_ignores_repeated_structural_evidence_keys(self):
        raw = json.dumps({
            "narration": "我看到主角螢幕連著白色長直立支架、圓形底座、托盤與自己的商品卡。",
            "view_type": "單機",
            "followme_physical_evidence": [
                {"cue": cue, "same_subject": True, "strength": "strong"}
                for cue in ("white_vertical_stand", "round_base", "attached_price_tray", "attached_followme_product_card")
            ],
        }, ensure_ascii=False, indent=2)
        self.assertFalse(batch._detect_repetition(raw))

    def test_repetition_watchdog_still_rejects_looping_structured_narration(self):
        repeated = "我看到同一段敘述不停重複。" * 20
        raw = json.dumps({"narration": repeated, "view_type": "單機"}, ensure_ascii=False)
        self.assertTrue(batch._detect_repetition(raw))

    def test_prompt_has_no_legacy_two_part_output_contract(self):
        prompt = Path(__file__).resolve().parents[1].joinpath("samsung_ocr_prompt.txt").read_text(encoding="utf-8")
        self.assertNotIn("第 1 行：自然語言描述", prompt)
        self.assertNotIn("第 2 行：純 JSON", prompt)
        self.assertNotIn("自然語言 + JSON", prompt)
        self.assertIn("只輸出一個完整 JSON 物件", prompt)

    def test_second_pass_is_independent_of_prior_evidence(self):
        previous = [{"view_type": "遠景", "complete_screen_count": 3, "unique_main": False,
                     "label_ownership": "not_visible", "followme_physical_evidence": []}]
        messages = batch.build_ocr_messages("system", "user", 2, previous)
        self.assertEqual(messages, [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ])
        self.assertNotIn("complete_screen_count", json.dumps(messages, ensure_ascii=False))
        self.assertNotIn("遠景", json.dumps(messages, ensure_ascii=False))

    def test_third_pass_messages_are_independent_of_prior_answers(self):
        previous = [
            {"view_type": "單機", "model": "WRONG-FIRST", "price": "9999"},
            {"view_type": "遠景", "model": None, "price": None},
        ]
        messages = batch.build_ocr_messages("system", "third-pass-user", 3, previous)
        self.assertEqual(messages, [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "third-pass-user"},
        ])
        self.assertNotIn("WRONG-FIRST", json.dumps(messages, ensure_ascii=False))

    def test_production_review_focus_prompts_are_neutral_and_stateless(self):
        prior = [{"view_type": "單機", "model": "WRONG-FIRST", "price": "9999"}]
        for attempt in (2, 3):
            messages = batch.build_ocr_messages(
                "system",
                batch.REVIEW_FOCUS_PROMPTS[attempt],
                attempt,
                prior,
            )
            self.assertEqual(
                review_prompt_leak_reasons(
                    attempt,
                    messages,
                    injected_prior_results=[],
                    prior_results_for_leak_check=prior,
                ),
                [],
            )
            self.assertNotIn("WRONG-FIRST", json.dumps(messages, ensure_ascii=False))

    def test_explicit_verified_state_overrides_legacy_pending_placeholder(self):
        verified = {
            "auto_verified": True,
            "auto_review_required": False,
            "review_status": "待審核",
        }
        unresolved = {
            "auto_verified": False,
            "auto_review_required": True,
            "review_status": "需慢模型或人工校正",
        }
        self.assertFalse(batch._status_needs_review(verified))
        self.assertTrue(batch._status_needs_review(unresolved))

    def test_captured_nine_response_shapes(self):
        core = '{"view_type":"遠景","screen_status":null,"quality_issue":null,"model":null,"price":null}'
        evidence = '{"complete_screen_count":3,"unique_main":false,"label_ownership":"not_visible","followme_physical_evidence":[]}'
        patterns = [core + evidence, core, '{"view_type":"單機","model":null,"price":"3790"}' + '{"complete_screen_count":4,"unique_main":true,"label_ownership":"matched","followme_physical_evidence":[]}', core + evidence, core, '{"view_type":"遠景","complete_screen_count":0,"unique_main":false,"label_ownership":"not_applicable","followme_physical_evidence":[]}', core + evidence, core, core + '{"complete_screen_count":4,"unique_main":false,"label_ownership":"mismatched","followme_physical_evidence":[]}']
        for raw in patterns:
            parsed, objects, mode, reason = _merge_v1945_json_objects(raw)
            self.assertGreaterEqual(len(objects), 1)
            self.assertIsNotNone(parsed)
            self.assertIn(mode, {"single_object", "disjoint_core_evidence"})

    def test_merge_rejects_duplicate_core_and_unknown_object(self):
        parsed, _, mode, reason = _merge_v1945_json_objects('{"view_type":"遠景"}{"view_type":"單機"}')
        self.assertIsNone(parsed)
        self.assertEqual(mode, "rejected")
        self.assertIn("multiple_core", reason)
        parsed, _, mode, reason = _merge_v1945_json_objects('{"view_type":"遠景"}{"unexpected":true}')
        self.assertIsNone(parsed)
        self.assertEqual(mode, "rejected")

    def test_single_object_evidence_survives_normalization_contract(self):
        raw = '{"view_type":"遠景","complete_screen_count":3,"unique_main":false,"label_ownership":"not_visible","followme_physical_evidence":[]}'
        parsed, _, mode, _ = _merge_v1945_json_objects(raw)
        self.assertEqual(mode, "single_object")
        self.assertTrue(validate_evidence_contract(parsed)[0])
    def test_confirmed_cases_fail_closed_without_structured_evidence(self):
        for name, view in (("汐止-1609", "單機"), ("林口館-193", "單機"), ("中埔-1180", "單機")):
            row = {"file_name": name, "view_type": view, "model": None, "price": None}
            decision = immediate_retry_decision(row, 3, [], 3)
            self.assertTrue(decision["unresolved"])
            self.assertTrue(any(reason.endswith("_missing") for reason in decision["reasons"]))

    def test_three_complete_no_main_is_valid_distant(self):
        row = {"view_type": "遠景", "model": None, "price": None, **evidence(3, False, "not_visible")}
        self.assertTrue(validate_evidence_contract(row)[0])

    def test_distant_counts_and_unowned_label_states_compare_by_gate_meaning(self):
        previous = [{
            "view_type": "遠景", "category": "遠景", "model": None, "price": None,
            **evidence(3, False, "not_visible"),
        }]
        current = {
            "view_type": "遠景", "category": "遠景", "model": None, "price": None,
            **evidence(12, False, "ambiguous"),
        }
        decision = evidence_contract_decision(current, previous)
        self.assertTrue(decision["valid"])
        self.assertNotIn("core_evidence_disagreement", decision["reasons"])

    def test_distant_semantic_comparison_still_rejects_material_changes(self):
        previous = [{
            "view_type": "遠景", "category": "遠景", "model": None, "price": None,
            **evidence(3, False, "not_visible"),
        }]
        for current in (
            {"view_type": "遠景", "category": "遠景", "model": None, "price": None, **evidence(2, False, "not_visible")},
            {"view_type": "遠景", "category": "遠景", "model": None, "price": None, **evidence(5, True, "not_visible")},
            {"view_type": "遠景", "category": "遠景", "model": None, "price": None, **evidence(5, False, "matched")},
        ):
            decision = evidence_contract_decision(current, previous)
            self.assertFalse(decision["valid"])
            self.assertTrue(decision["reasons"])

    def test_distant_prompt_requires_complete_machine_readable_evidence(self):
        prompt = Path(__file__).resolve().parents[1].joinpath("samsung_ocr_prompt.txt").read_text(encoding="utf-8")
        self.assertIn("`unique_main` 固定填 `false`", prompt)
        self.assertIn("`complete_screen_count` 填成實際數到的整數且至少為 3", prompt)
        self.assertIn("禁止填 `matched`", prompt)
        self.assertIn("0、1、2 台完整入鏡時絕對不是遠景", prompt)
        self.assertIn("中央有一台明顯較完整、較大或構圖居中的主螢幕", prompt)

    def test_retry_prompts_repeat_subthree_and_dominant_single_invariant(self):
        for attempt in (2, 3):
            focus = batch.REVIEW_FOCUS_PROMPTS[attempt]
            self.assertIn("完整台數只有 0、1、2 時絕對不可判遠景", focus)
            self.assertIn("中央主螢幕與其正下方可讀價牌對齊", focus)
            self.assertIn("白色直立支架＋完整圓形落地底座", focus)
            self.assertIn("followme_physical_evidence", focus)
            self.assertIn("只有完全沒有實體 FollowMe 候選時", focus)
            self.assertIn("screen_content_only、same_subject=false", focus)
        self.assertIn("complete_screen_count 0, 1, or 2 can never be", batch.V1945_OUTPUT_CONTRACT)
        self.assertIn("PROJECT TARGET PRIORITY", batch.V1945_OUTPUT_CONTRACT)
        self.assertIn("regardless of surrounding screens", batch.V1945_OUTPUT_CONTRACT)
        self.assertIn("outside the illuminated screen rectangle", batch.V1945_OUTPUT_CONTRACT)
        self.assertIn("Every physical fixture cue stated in narration", batch.V1945_OUTPUT_CONTRACT)
        self.assertIn("MANDATORY FINAL SELF-CHECK", batch.V1945_OUTPUT_CONTRACT)
        self.assertIn("can NEVER negate", batch.V1945_OUTPUT_CONTRACT)
        self.assertIn("include portrait_display", batch.V1945_OUTPUT_CONTRACT)
        self.assertIn("include attached_followme_product_card", batch.V1945_OUTPUT_CONTRACT)

    def test_screen_promotion_cannot_negate_followme_hardware(self):
        prompt = Path(__file__).resolve().parents[1].joinpath("samsung_ocr_prompt.txt").read_text(encoding="utf-8")
        self.assertIn("播放宣傳內容也絕不能反過來否定已看見的實體結構", prompt)
        self.assertIn("不得抵銷白色直立支架、圓形底座、托盤等強實體線索", prompt)
        for attempt in (2, 3):
            self.assertIn("絕不能反向否定", batch.REVIEW_FOCUS_PROMPTS[attempt])
            self.assertIn("禁止說它不是實機", batch.REVIEW_FOCUS_PROMPTS[attempt])
            self.assertIn("portrait_display", batch.REVIEW_FOCUS_PROMPTS[attempt])
            self.assertIn("attached_followme_product_card", batch.REVIEW_FOCUS_PROMPTS[attempt])

    def test_backend_never_rewrites_conflicting_narration_as_final_correction(self):
        original = "我看到前景 FollowMe 實體，但最後卻說是遠景。"
        result = {"view_type": "單機", "model": 'FollowMe Pro M7 43"', "price": "17990"}
        displayed = batch.build_final_display_thinking(result, original)
        self.assertIn("前景 FollowMe 實體，但最後卻說是遠景", displayed)
        self.assertTrue(displayed.startswith('我看到本輪結論：單機，FollowMe Pro M7 43"，17,990元。'))
        self.assertNotIn("所以……", displayed)
        self.assertNotIn("最終校正", displayed)
        source = Path(batch.__file__).read_text(encoding="utf-8")
        self.assertIn("先掃描全張原圖，逐區搜尋實體 FollowMe，再計算所有完整螢幕", source)
        self.assertNotIn("最終校正：這張判定為單機，型號 {final_model}", source)

    def test_narrated_followme_fixture_cannot_be_omitted_from_structure(self):
        row = {
            "file_name": "M-202601-台中超越-913.jpg",
            "view_type": "遠景",
            "category": "遠景",
            "model": None,
            "price": None,
            "quality_issue": "",
            "thinking": (
                "中央有一台直立螢幕，下方有白色圓形底座與託盤，但未見白色垂直支架。"
                "背景雖有多台電視，仍判斷為遠景。"
            ),
            **evidence(3, False, "not_visible", []),
        }
        decision = evidence_contract_decision(row)
        self.assertFalse(decision["valid"])
        self.assertIn("narration_followme_physical_evidence_omitted", decision["reasons"])

    def test_matching_narrated_followme_fixture_is_machine_readable(self):
        physical = [
            {"cue": "portrait_display", "same_subject": True, "strength": "strong"},
            {"cue": "round_base", "same_subject": True, "strength": "strong"},
            {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
        ]
        row = {
            "file_name": "M-202601-followme.jpg",
            "view_type": "單機",
            "category": "單機",
            "model": "FollowMe M7 43\"",
            "price": "17990",
            "thinking": "唯一主角是直立螢幕，下方連著白色圓形底座與託盤。",
            **evidence(1, True, "matched", physical),
        }
        decision = evidence_contract_decision(row)
        self.assertTrue(decision["valid"])
        self.assertNotIn("narration_followme_physical_evidence_omitted", decision["reasons"])

    def test_prior_narration_structure_conflict_cannot_be_washed_by_later_passes(self):
        unsafe = {
            "file_name": "M-202601-unsafe.jpg",
            "view_type": "遠景",
            "category": "遠景",
            "model": None,
            "price": None,
            "thinking": "中央直立螢幕下方有白色圓形底座與託盤，但判斷為遠景。",
            **evidence(3, False, "not_visible", []),
        }
        clean = {
            "file_name": "M-202601-unsafe.jpg",
            "view_type": "遠景",
            "category": "遠景",
            "model": None,
            "price": None,
            "thinking": "整排三台以上螢幕完整入鏡，無法鎖定唯一主角及其自己的規格與價格。",
            **evidence(3, False, "not_visible", []),
        }
        decision = immediate_retry_decision(dict(clean), 3, [unsafe, dict(clean)], 3)
        self.assertTrue(decision["unresolved"])
        self.assertFalse(decision["verified"])
        self.assertIn("prior_narration_evidence_conflict", decision["reasons"])

    def test_material_structured_authority_conflict_cannot_be_accepted(self):
        row = {
            "file_name": "M-202601-structured-conflict.jpg",
            "view_type": "遠景",
            "category": "遠景",
            "model": None,
            "price": None,
            "thinking": "整排三台螢幕完整入鏡，無法鎖定唯一主角及其自己的規格與價格。",
            "structured_authority_blocked_fields": ["view_type"],
            **evidence(3, False, "not_visible", []),
        }
        decision = immediate_retry_decision(dict(row), 3, [dict(row), dict(row)], 3)
        self.assertTrue(decision["unresolved"])
        self.assertFalse(decision["verified"])
        self.assertIn("structured_authority_conflict:view_type", decision["reasons"])

    def test_single_prompt_requires_all_machine_readable_evidence_every_pass(self):
        prompt = Path(__file__).resolve().parents[1].joinpath("samsung_ocr_prompt.txt").read_text(encoding="utf-8")
        self.assertIn("一般單機的四個機器證據欄位每一輪都必須出現", prompt)
        self.assertIn("一般單機必須為 true", prompt)
        self.assertIn("任何分支、任何輪次都不得省略四個機器證據欄位", prompt)
        schema = prompt.split("### JSON Schema", 1)[1].split("---", 1)[0]
        for field in (
            "complete_screen_count",
            "unique_main",
            "label_ownership",
            "followme_physical_evidence",
        ):
            self.assertIn(field, schema)

    def test_strong_followme_inside_three_screen_wall_becomes_business_subject(self):
        physical = [
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
            {"cue": "round_base", "same_subject": True, "strength": "strong"},
        ]
        row = {
            "file_name": "M-202605-distant-followme.jpg", "view_type": "遠景", "category": "遠景",
            "model": None, "price": None, "quality_issue": "",
            "thinking": "三台完整入鏡，沒有唯一主角；其中可見 S32FM703UC 的白色垂直支架與圓形底座。",
            "ocr_attempt": 3,
            "input_image_sha256": "c" * 64,
            "request_binding_enforced": True,
            "request_id_verified": True,
            "independent_pass": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
            "runtime_health": {
                "healthy": False,
                "allow_processing": True,
                "allow_upload": False,
                "reasons": ["distant_followme_strong_evidence_conflict"],
            },
            **evidence(3, False, "not_visible", physical),
        }
        valid, errors, _ = validate_evidence_contract(row)
        self.assertFalse(valid)
        self.assertIn("distant_followme_physical_conflict", errors)
        history = [dict(row), dict(row)]
        decision = immediate_retry_decision(row, 3, history, 3)
        self.assertTrue(decision["unresolved"])
        decision = finalize_three_pass_outcome(row, history, decision, 3)
        self.assertTrue(decision["verified"])
        self.assertFalse(decision["unresolved"])
        self.assertEqual(row["view_type"], "單機")
        self.assertTrue(row["followme_family_confirmed"])
        self.assertEqual(row["model"], "FollowMe 型號未細分")
        self.assertIsNone(row["price"])

    def test_234_pixel_authority_counts_cropped_monitor_as_zero(self):
        wrong = {
            "period": "202601", "view_type": "單機", "category": "單機",
            "model": None, "price": None, "input_image_sha256": self.CHANGHUA_234_SHA,
            "thinking": "右下方一台螢幕完整入鏡。", "ocr_attempt": 1,
            "request_id_verified": True, "request_binding_enforced": True,
            "independent_pass": True, "prior_answer_exposed": False,
            "prompt_contamination": False, "runtime_health": {"healthy": True, "reasons": []},
            **evidence(1, True, "not_visible", []),
        }
        first, second, third = dict(wrong), dict(wrong, ocr_attempt=2), dict(wrong, ocr_attempt=3)
        self.assertTrue(apply_human_audited_pixel_authority(third, [first, second], 3))
        self.assertEqual(third["view_type"], "遠景")
        self.assertEqual(third["complete_screen_count"], 0)
        self.assertFalse(third["unique_main"])
        self.assertIsNone(third["model"])
        self.assertIsNone(third["price"])
        self.assertTrue(third["three_pass_adjudicated"])
        self.assertIn("完整螢幕數為 0", third["thinking"])
        decision = immediate_retry_decision(third, 3, [first, second], 3)
        decision = finalize_three_pass_outcome(third, [first, second], decision, 3)
        self.assertTrue(decision["verified"])

    def test_199_pixel_authority_clears_adjacent_price_and_model(self):
        wrong = {
            "period": "202601", "view_type": "單機", "category": "單機",
            "model": "S32DM700UC", "price": "19900",
            "input_image_sha256": self.CHIAYI_199_SHA,
            "thinking": "鄰近展示牌寫著 19,900。", "ocr_attempt": 1,
            "request_id_verified": True, "request_binding_enforced": True,
            "independent_pass": True, "prior_answer_exposed": False,
            "prompt_contamination": False, "runtime_health": {"healthy": True, "reasons": []},
            **evidence(1, True, "matched", []),
        }
        first, second, third = dict(wrong), dict(wrong, ocr_attempt=2), dict(wrong, ocr_attempt=3)
        self.assertTrue(apply_human_audited_pixel_authority(third, [first, second], 3))
        self.assertEqual(third["view_type"], "單機")
        self.assertEqual(third["complete_screen_count"], 1)
        self.assertIsNone(third["model"])
        self.assertIsNone(third["price"])
        decision = immediate_retry_decision(third, 3, [first, second], 3)
        decision = finalize_three_pass_outcome(third, [first, second], decision, 3)
        self.assertTrue(decision["verified"])

    def test_human_audited_1467_pixels_preserve_wide_scene_followme_subject(self):
        wrong = {
            "period": "202601", "view_type": "遠景", "category": "遠景",
            "model": None, "price": None,
            "input_image_sha256": self.WIDE_FOLLOWME_1467_SHA,
            "thinking": "寬廣賣場中三台完整螢幕，沒有唯一主角。",
            "request_id_verified": True, "independent_pass": True,
            "prior_answer_exposed": False, "prompt_contamination": False,
            **evidence(3, False, "not_visible", []),
        }
        first = dict(wrong, ocr_attempt=1)
        second = dict(wrong, ocr_attempt=2)
        third = dict(wrong, ocr_attempt=3)

        self.assertTrue(
            apply_human_audited_pixel_authority(third, [first, second], 3)
        )
        self.assertEqual(third["view_type"], "單機")
        self.assertEqual(third["complete_screen_count"], 3)
        self.assertIsNone(third["model"])
        self.assertIsNone(third["price"])
        self.assertTrue(third["followme_family_confirmed"])
        self.assertTrue(third["wide_scene_followme_present"])
        self.assertEqual(
            {item["cue"] for item in third["followme_physical_evidence"]},
            {"white_vertical_stand", "round_base", "portrait_display"},
        )

    def test_distant_explicitly_negated_followme_word_is_not_a_positive_cue(self):
        row = {
            "file_name": "M-202605-distant-no-followme.jpg", "view_type": "遠景", "category": "遠景",
            "model": None, "price": None, "quality_issue": "",
            "thinking": "整排螢幕完整入鏡，無法鎖定唯一主角及其自己的規格與價格；畫面中無 FollowMe 白色支架或圓形底座，也沒有看到 FollowMe 實機。",
            **evidence(7, False, "not_visible", []),
        }
        decision = immediate_retry_decision(row, 3, [dict(row), dict(row)], 3)
        self.assertTrue(decision["verified"])
        self.assertFalse(decision["unresolved"])
        self.assertNotIn("遠景仍含未排除的 FollowMe 線索", decision["reasons"])

    def test_minor_narrated_cue_omission_does_not_fuse_sufficient_single_evidence(self):
        current = {
            "file_name": "M-202601-followme-minor-omission.jpg",
            "view_type": "單機",
            "category": "單機",
            "model": 'FollowMe M7 43"',
            "price": "17990",
            "thinking": "我看到直立螢幕連著白色直桿、圓形底座與托盤。",
            "complete_screen_count": 4,
            "unique_main": True,
            "label_ownership": "matched",
            "followme_physical_evidence": [
                {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
                {"cue": "round_base", "same_subject": True, "strength": "strong"},
                {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
            ],
        }
        decision = evidence_contract_decision(current)
        self.assertTrue(decision["valid"])
        self.assertNotIn("narration_followme_physical_evidence_omitted", decision["reasons"])

    def test_valid_single_is_auto_verified_without_forcing_extra_passes(self):
        row = {
            "file_name": "M-202605-test.jpg", "view_type": "單機", "category": "單機",
            "model": "S24F332EAC", "price": "2390", "quality_issue": "",
            "thinking": "唯一主角自己的規格牌與價格牌清楚可讀。",
            **evidence(1, True, "matched"),
        }
        decision = immediate_retry_decision(row, 1, [], 3)
        self.assertFalse(decision["retry"])
        self.assertTrue(decision["verified"])

    def test_single_structure_cannot_ignore_explicit_distant_narration(self):
        row = {
            "file_name": "M-202605-conflict.jpg", "view_type": "單機", "category": "單機",
            "model": "S24F332EAC", "price": "2390", "quality_issue": "",
            "thinking": "檢視整體陳列後，這張符合遠景條件。",
            **evidence(1, True, "matched"),
        }
        decision = immediate_retry_decision(row, 1, [], 3)
        self.assertTrue(decision["retry"])
        self.assertFalse(decision["verified"])
        self.assertIn("結構為單機但敘述明確判為遠景", decision["reasons"])

    def test_retry_dialogue_contamination_is_fail_closed(self):
        row = {
            "file_name": "M-202601-contaminated.jpg", "view_type": "單機", "category": "單機",
            "model": "S27CG552EC", "price": "17990", "quality_issue": "",
            "thinking": "您指正得非常正確，我先前的型號判斷錯誤，現在修正答案。",
            **evidence(1, True, "matched"),
        }
        decision = immediate_retry_decision(row, 2, [], 3)
        self.assertTrue(decision["retry"])
        self.assertFalse(decision["verified"])
        self.assertIn("本輪出現承接前輪答案的污染語句", decision["reasons"])

    def test_view_type_and_category_conflict_fails_closed(self):
        row = {
            "file_name": "M-202605-category-conflict.jpg", "view_type": "單機", "category": "遠景",
            "model": "S24F332EAC", "price": "2390", "quality_issue": "",
            "thinking": "唯一主角自己的規格牌與價格牌清楚可讀。",
            **evidence(1, True, "matched"),
        }
        valid, errors, _ = validate_evidence_contract(row)
        self.assertFalse(valid)
        self.assertIn("view_category_conflict", errors)
        self.assertTrue(immediate_retry_decision(row, 1, [], 3)["retry"])

    def test_matched_label_cannot_contradict_narration_ownership(self):
        row = {
            "file_name": "M-202605-owner-conflict.jpg", "view_type": "單機", "category": "單機",
            "model": "S24F332EAC", "price": "2390", "quality_issue": "",
            "thinking": "規格牌與價格牌屬於旁邊商品，不能歸屬唯一主角。",
            **evidence(1, True, "matched"),
        }
        decision = immediate_retry_decision(row, 1, [], 3)
        self.assertTrue(decision["retry"])
        self.assertIn("標籤歸屬與敘述衝突", decision["reasons"])

    def test_three_pass_adjudication_drops_adjacent_price_when_narration_denies_alignment(self):
        common = {
            "period": "202601",
            "file_name": "M-嘉義市-西　區-集雅社-嘉義新光-199.jpg",
            "view_type": "單機",
            "category": "單機",
            "model": None,
            "quality_issue": "不合格-沒有規格牌",
            "input_image_sha256": "b" * 64,
            "request_id_verified": True,
            "request_binding_enforced": True,
            "independent_pass": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
            "runtime_health": {"healthy": True, "reasons": []},
            **evidence(1, True, "matched", []),
        }
        first = {
            **common,
            "price": "19900",
            "thinking": "中央主角正下方有實體價牌，結構回傳為 matched。",
        }
        second = {
            **common,
            "price": None,
            "label_ownership": "ambiguous",
            "thinking": "螢幕下方有實體價牌，但無法確認是否屬於這台螢幕。",
        }
        third = {
            **common,
            "price": "19900",
            "thinking": "螢幕下方有實體價牌，但無法確認是否與主角螢幕空間對齊。",
        }

        conflict = immediate_retry_decision(dict(third), 3, [dict(first), dict(second)], 3)
        self.assertIn("標籤歸屬與敘述衝突", conflict["reasons"])

        outcome = finalize_three_pass_outcome(
            third,
            [first, second],
            {"attempt": 3, "unresolved": True, "verified": False, "reasons": conflict["reasons"]},
            3,
        )
        self.assertTrue(outcome["verified"])
        self.assertFalse(outcome["unresolved"])
        self.assertEqual(third["view_type"], "單機")
        self.assertIsNone(third["model"])
        self.assertIsNone(third["price"])
        self.assertEqual(third["label_ownership"], "ambiguous")

    def test_screen_content_brand_does_not_override_samsung_sku(self):
        narration = "這台是 Samsung S27D392GAC，螢幕顯示 ASUS Demo 畫面，價牌為 4,290 元。"
        self.assertIsNone(batch.infer_other_brand_model(narration, "S27D392GAC"))
        self.assertEqual(batch.infer_other_brand_model("主角是 ASUS 螢幕。", None), "它牌(ASUS)")

    def test_negated_screen_brand_and_raw_samsung_sku_cannot_become_other_brand(self):
        narration = (
            "這台螢幕是三星 Odyssey G5，型號 S27FG532EC。雖然畫面有 LG 字樣，"
            "但那是螢幕內的遊戲畫面，不是品牌標籤，主角是三星商品。"
        )
        self.assertIsNone(batch.infer_other_brand_model(narration, "S27FG532EC"))

        row = {
            "file_name": "M-台南市-永康區-TK3C-中華-1064.jpg",
            "view_type": "單機", "category": "單機", "model": "它牌(LG)", "price": "4990",
            "quality_issue": "", "thinking": narration,
            "raw_objects": [json.dumps({
                "view_type": "單機", "model": "S27FG532EC", "price": "4990",
                "complete_screen_count": 1, "unique_main": True,
                "label_ownership": "matched", "followme_physical_evidence": [],
            }, ensure_ascii=False)],
            **evidence(1, True, "matched"),
        }
        decision = immediate_retry_decision(row, 1, [], 3)
        self.assertTrue(decision["retry"])
        self.assertFalse(decision["verified"])
        self.assertIn("最終它牌結果與原始 Samsung SKU 衝突", decision["reasons"])
        self.assertEqual(decision["evidence_guard_revision"], EVIDENCE_GUARD_REVISION)

    def test_large_official_price_difference_keeps_badge_without_forced_retry(self):
        row = {
            "file_name": "M-202605-price-diff.jpg", "view_type": "單機", "category": "單機",
            "model": "S27D392GAC", "price": "4290", "quality_issue": "",
            "price_status": "high", "price_diff_percent": 36.7,
            "thinking": "唯一主角自己的實體規格牌與價格牌清楚可讀。",
            **evidence(1, True, "matched"),
        }
        first = immediate_retry_decision(dict(row), 1, [], 3)
        self.assertFalse(first["retry"])
        self.assertTrue(first["verified"])
        self.assertNotIn("2026 價差照片需第二輪無記憶核對價牌角色", first["reasons"])
        self.assertEqual(row["price_status"], "high")
        self.assertEqual(row["price_diff_percent"], 36.7)

    def test_complete_current_year_followme_uses_one_pass_fast_path(self):
        physical = [
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
            {"cue": "round_base", "same_subject": True, "strength": "strong"},
            {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
            {"cue": "attached_followme_product_card", "same_subject": True, "strength": "direct"},
        ]
        row = {
            "file_name": "M-202605-followme.jpg", "view_type": "單機", "category": "單機",
            "model": 'FollowMe M7 32"', "price": "12900", "quality_issue": "",
            "thinking": (
                "唯一主角同一台實機有白色垂直支架、完整圓形底座、附著價牌托盤，"
                "附著產品卡清楚寫 FollowMe M7 32 吋與 12,900 元。"
            ),
            **evidence(1, True, "matched", physical),
        }
        first = immediate_retry_decision(dict(row), 1, [], 3)
        self.assertFalse(first["retry"])
        self.assertTrue(first["verified"])
        self.assertNotIn("2026 FollowMe 身分與價牌需第二輪無記憶獨立確認", first["reasons"])
    def test_current_year_followme_identity_disagreement_never_becomes_majority_success(self):
        physical = [
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
            {"cue": "round_base", "same_subject": True, "strength": "strong"},
        ]
        first = {
            "file_name": "M-202601-followme-714.jpg", "view_type": "單機", "category": "單機",
            "model": 'FollowMe M7 32"', "price": "12990", "quality_issue": "",
            "thinking": "同一台實機有白色垂直支架與圓形底座，Smart Monitor M7 價牌 12,990。",
            **evidence(1, True, "matched", physical),
        }
        conflicting_second = {
            **first,
            "model": 'FollowMe Pro M7 43"', "price": "17990",
            "thinking": "同一台實機規格牌清楚寫 FollowMe Pro 43吋，價牌 17,990。",
        }
        second = immediate_retry_decision(dict(conflicting_second), 2, [dict(first)], 3)
        self.assertTrue(second["retry"])
        self.assertFalse(second["verified"])
        self.assertIn("2026 FollowMe 各輪型號與價格不一致，不得自動驗證", second["reasons"])

        # A two-to-one majority cannot wash the independently observed conflict.
        third = immediate_retry_decision(dict(first), 3, [dict(first), dict(conflicting_second)], 3)
        self.assertTrue(third["unresolved"])
        self.assertFalse(third["verified"])
        self.assertIn("2026 FollowMe 各輪型號與價格不一致，不得自動驗證", third["reasons"])

    def test_followme_uses_structured_physical_evidence_not_narration_keywords(self):
        physical = [
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
            {"cue": "round_base", "same_subject": True, "strength": "strong"},
        ]
        row = {
            "file_name": "M-202601-followme-structured.jpg",
            "view_type": "單機", "category": "單機",
            "model": 'FollowMe M7 32"', "price": "12990", "quality_issue": "",
            "thinking": '{"result":"machine-readable evidence is authoritative"}',
            **evidence(1, True, "matched", physical),
        }
        first = immediate_retry_decision(dict(row), 1, [], 3)
        self.assertFalse(first["retry"])
        self.assertTrue(first["verified"])
        self.assertNotIn("2026 FollowMe 身分與價牌需第二輪無記憶獨立確認", first["reasons"])
        self.assertNotIn("FollowMe 缺少同一實機的物理支架證據", first["reasons"])

    def test_structured_evidence_allows_friendly_followme_normalization(self):
        narration_without_physical_keywords = "主角規格牌可讀。"
        self.assertEqual(
            batch.normalize_followme_model(
                'FollowMe M7 32"',
                "12990",
                narration_without_physical_keywords,
                structured_physical_confirmed=True,
            ),
            'FollowMe M7 32"',
        )
        self.assertEqual(
            batch.normalize_followme_model(
                'FollowMe Pro M7 43"',
                "17990",
                "中央直立式螢幕下方有白色圓形底座與託盤。",
                structured_physical_confirmed=True,
            ),
            'FollowMe Pro M7 43"',
        )

    def test_followme_positive_sentence_is_not_negated_by_later_no_unique_main_wording(self):
        narration = (
            "中央直立式螢幕下方有白色圓形底座與託盤，符合 FollowMe 實機。"
            "背景牆有多台電視但無法鎖定背景唯一主角；因此前景符合 FollowMe 判定條件。"
        )
        self.assertFalse(batch.has_negative_followme_context(narration))
        physical = [
            {"cue": "round_base", "same_subject": True, "strength": "strong"},
            {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
            {"cue": "direct_followme_branding_on_unit", "same_subject": True, "strength": "direct"},
        ]
        row = {
            "view_type": "單機", "category": "單機", "model": 'FollowMe M7 43"',
            "price": "17990", "thinking": narration,
            **evidence(3, True, "matched", physical),
        }
        decision = evidence_contract_decision(row)
        self.assertTrue(decision["valid"])
        self.assertNotIn("narration_followme_physical_evidence_omitted", decision["reasons"])

    def test_smart_monitor_sku_needs_fixture_evidence_to_become_followme(self):
        missing = {
            "file_name": "M-202605-followme-sku.jpg", "view_type": "單機", "category": "單機",
            "model": "S32FM703UC", "price": "12990", "quality_issue": "",
            "thinking": "唯一主角自己的規格與價格清楚可讀。",
            **evidence(1, True, "matched"),
        }
        first_missing = immediate_retry_decision(dict(missing), 1, [], 3)
        self.assertFalse(first_missing["retry"])
        self.assertTrue(first_missing["verified"])
        self.assertNotIn("followme_physical_evidence_insufficient", first_missing["reasons"])

        physical = [
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
            {"cue": "round_base", "same_subject": True, "strength": "strong"},
        ]
        valid = dict(missing)
        valid.update({
            "thinking": "同一台實機有白色垂直支架與圓形底座，規格與價格屬於唯一主角。",
            "followme_physical_evidence": physical,
        })
        first = immediate_retry_decision(dict(valid), 1, [], 3)
        self.assertFalse(first["retry"])
        self.assertTrue(first["verified"])
        self.assertNotIn("2026 FollowMe 身分與價牌需第二輪無記憶獨立確認", first["reasons"])

    def test_distant_structured_evidence_still_requires_supporting_narration(self):
        row = {
            "file_name": "M-202605-distant.jpg", "view_type": "遠景", "category": "遠景",
            "model": None, "price": None, "quality_issue": "", "thinking": "整體符合遠景條件。",
            **evidence(3, False, "not_visible"),
        }
        decision = immediate_retry_decision(row, 3, [dict(row), dict(row)], 3)
        self.assertTrue(decision["unresolved"])
        self.assertIn("evidence_thinking_conflict", decision["reasons"])

    def test_distant_narration_can_support_structured_count_without_repeating_integer(self):
        def distant_pass(count):
            return {
                "file_name": "M-202605-distant-many.jpg", "view_type": "遠景", "category": "遠景",
                "model": None, "price": None, "quality_issue": "",
                "thinking": "畫面中可見多台螢幕整齊排列，沒有唯一主角，也沒有可歸屬到主角自己的規格或價格。",
                **evidence(count, False, "ambiguous"),
            }

        first = distant_pass(5)
        second = distant_pass(6)
        third = distant_pass(5)
        decision = immediate_retry_decision(third, 3, [first, second], 3)
        self.assertTrue(decision["verified"])
        self.assertFalse(decision["unresolved"])
        self.assertNotIn("evidence_thinking_conflict", decision["reasons"])

    def test_distant_narration_sub_three_statement_still_conflicts_with_structured_count(self):
        row = {
            "file_name": "M-202605-distant-contradiction.jpg", "view_type": "遠景", "category": "遠景",
            "model": None, "price": None, "quality_issue": "",
            "thinking": "只有一台完整入鏡，沒有唯一主角，也沒有可歸屬的規格或價格。",
            **evidence(5, False, "ambiguous"),
        }
        decision = immediate_retry_decision(row, 3, [dict(row), dict(row)], 3)
        self.assertTrue(decision["unresolved"])
        self.assertIn("evidence_thinking_conflict", decision["reasons"])

    def test_current_year_clean_distant_uses_one_pass_fast_path(self):
        row = {
            "file_name": "M-202605-distant.jpg", "view_type": "遠景", "category": "遠景",
            "model": None, "price": None, "quality_issue": "",
            "thinking": "三台完整入鏡，沒有唯一主角，也無法對應主角自己的規格與價格。",
            **evidence(3, False, "not_visible"),
        }
        first = immediate_retry_decision(dict(row), 1, [], 3)
        second = immediate_retry_decision(dict(row), 2, [dict(row)], 3)
        third = immediate_retry_decision(dict(row), 3, [dict(row), dict(row)], 3)
        self.assertFalse(first["retry"])
        self.assertTrue(first["verified"])
        self.assertFalse(second["retry"])
        self.assertTrue(second["verified"])
        self.assertFalse(third["retry"])
        self.assertFalse(third["unresolved"])
        self.assertTrue(third["verified"])

    def test_current_year_distant_accepts_semantically_equal_three_plus_passes(self):
        def distant_pass(count, ownership):
            return {
                "file_name": "M-202605-distant-many.jpg", "view_type": "遠景", "category": "遠景",
                "model": None, "price": None, "quality_issue": "",
                "thinking": f"我數到 {count} 台，屬於 3 台以上完整入鏡；沒有唯一主角，也無法對應主角自己的規格與價格。",
                **evidence(count, False, ownership),
            }

        first_row = distant_pass(3, "not_visible")
        second_row = distant_pass(10, "ambiguous")
        third_row = distant_pass(12, "mismatched")
        first = immediate_retry_decision(first_row, 1, [], 3)
        second = immediate_retry_decision(second_row, 2, [first_row], 3)
        third = immediate_retry_decision(third_row, 3, [first_row, second_row], 3)
        self.assertFalse(first["retry"])
        self.assertTrue(first["verified"])
        self.assertFalse(second["retry"])
        self.assertTrue(second["verified"])
        self.assertFalse(third["retry"])
        self.assertFalse(third["unresolved"])
        self.assertTrue(third["verified"])

    def test_current_year_distant_cannot_wash_unsafe_prior_passes(self):
        clean = {
            "file_name": "M-202605-distant-clean.jpg", "view_type": "遠景", "category": "遠景",
            "model": None, "price": None, "quality_issue": "",
            "thinking": "我數到 8 台，屬於 3 台以上完整入鏡；沒有唯一主角，也無法對應主角自己的規格與價格。",
            **evidence(8, False, "not_visible"),
        }
        unsafe_rows = []
        for count, unique, ownership in ((None, False, "not_visible"), (2, False, "not_visible"), (5, None, "not_visible"), (5, True, "not_visible"), (5, False, "matched")):
            unsafe_rows.append({**clean, **evidence(count, unique, ownership)})
        unsafe_rows.append({
            **clean,
            "followme_physical_evidence": [
                {"cue": "white_vertical_stand", "same_subject": True, "strength": "invalid"},
            ],
        })
        for unsafe in unsafe_rows:
            decision = immediate_retry_decision(dict(clean), 3, [unsafe, dict(clean)], 3)
            self.assertTrue(decision["unresolved"])
            self.assertFalse(decision["verified"])
            self.assertIn("prior_evidence_contract_invalid", decision["reasons"])

    def test_current_year_distant_allows_weak_prior_followme_signage(self):
        def distant_row(count, ownership, physical):
            return {
                "file_name": "M-202605-distant-signage.jpg", "view_type": "遠景", "category": "遠景",
                "model": None, "price": None, "quality_issue": "",
                "thinking": f"我數到 {count} 台，屬於 3 台以上完整入鏡；沒有唯一主角，也無法對應主角自己的規格與價格。",
                **evidence(count, False, ownership, physical),
            }
        weak = [{"cue": "nearby_signage_only", "same_subject": False, "strength": "weak"}]
        first = distant_row(3, "not_visible", weak)
        second = distant_row(10, "ambiguous", [])
        third = distant_row(8, "not_applicable", weak)
        decision = immediate_retry_decision(third, 3, [first, second], 3)
        self.assertTrue(decision["verified"])

    def test_clean_wide_display_narration_does_not_force_ritual_retries(self):
        row = {
            "period": "202602",
            "view_type": "遠景",
            "category": "遠景",
            "model": None,
            "price": None,
            "quality_issue": "無",
            "thinking": (
                "全圖有三台以上完整螢幕，沒有同主體 FollowMe 實體。"
                "背景螢幕與價牌均屬展示，故判為遠景。"
            ),
            **evidence(3, False, "not_visible", []),
        }
        decision = immediate_retry_decision(row, 1, [], 3)
        self.assertTrue(decision["verified"])
        self.assertFalse(decision["retry"])
        self.assertFalse(decision["unresolved"])

    def test_third_pass_core_disagreement_is_unresolved(self):
        distant = {
            "view_type": "遠景", "category": "遠景", "model": None, "price": None,
            "quality_issue": "",
            "thinking": "三台完整入鏡，沒有唯一主角，也無法對應主角自己的規格與價格。",
            **evidence(3, False, "not_visible"),
        }
        prior_single = {
            "view_type": "單機", "category": "單機", "model": "S24F332EAC", "price": "2390",
            **evidence(1, True, "matched"),
        }
        decision = immediate_retry_decision(distant, 3, [prior_single, dict(distant)], 3)
        self.assertTrue(decision["unresolved"])
        self.assertFalse(decision["verified"])
        self.assertIn("core_evidence_disagreement", decision["reasons"])

    def test_three_visible_one_complete_is_single_only_with_matched_label(self):
        row = {"view_type": "單機", "model": "S32ABC123", "price": "12900", **evidence(3, True, "matched")}
        self.assertTrue(validate_evidence_contract(row)[0])
        row["unique_main"] = False
        self.assertFalse(validate_evidence_contract(row)[0])

    def test_label_ownership_mismatch_is_unresolved(self):
        row = {"view_type": "單機", "model": "S32ABC123", "price": "12900", **evidence(1, True, "mismatched")}
        decision = immediate_retry_decision(row, 3, [], 3)
        self.assertTrue(decision["unresolved"])
        self.assertIn("label_ownership_required_for_fields", decision["reasons"])

    def test_followme_requires_same_subject_physical_evidence(self):
        row = {"view_type": "單機", "model": 'FollowMe M7 32"', "price": "12900", **evidence(1, True, "matched", [{"cue": "screen_content_only", "same_subject": True, "strength": "strong"}])}
        self.assertFalse(validate_evidence_contract(row)[0])
        row["followme_physical_evidence"] = [
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
            {"cue": "round_base", "same_subject": True, "strength": "strong"},
        ]
        self.assertTrue(validate_evidence_contract(row)[0])

    def test_followme_cue_codes_are_atomic_and_weak_cues_cannot_establish_model(self):
        weak = {"cue": "screen_content_only", "same_subject": True, "strength": "strong"}
        row = {"view_type": "單機", "model": 'FollowMe M7 32"', "price": None, **evidence(1, True, "not_visible", [weak])}
        valid, errors, normalized = validate_evidence_contract(row)
        self.assertFalse(valid)
        self.assertIn("followme_physical_evidence_insufficient", errors)
        self.assertEqual(normalized["followme_physical_evidence"][0]["strength"], "weak")
        duplicate = [
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
        ]
        _, errors, _ = validate_evidence_contract({"view_type": "單機", "model": 'FollowMe M7 32"', **evidence(1, True, "not_visible", duplicate)})
        self.assertIn("followme_physical_evidence_duplicate_cue", errors)

    def test_trace_persistence_shape_and_upload_exclusion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit = root / "_ocr_audit" / "sample"
            audit.mkdir(parents=True)
            trace = audit / "v1945_evidence_trace.jsonl"
            target_name = "M-202601-test-單機-S24F332EAC-✓＄2390-1.jpg"
            with (audit / "copied.csv").open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["original_name", "target_name"])
                writer.writeheader()
                writer.writerow({"original_name": "source-test.jpg", "target_name": target_name})
            trace.write_text(json.dumps({
                "trace_version": "v19.45", "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
                "file_name": "source-test.jpg", "period": "202601",
                "guard_decision": {"verified": True},
            }) + "\n", encoding="utf-8")
            self.assertEqual(load_v1945_trace_names(root), {target_name})
            row = {"auto_verified": "true", "auto_review_required": "false", "ocr_attempt": "1", "evidence_contract_version": "v19.45", "evidence_guard_revision": EVIDENCE_GUARD_REVISION, "evidence_contract_valid": "true", "file_name": "source-test.jpg", "period": "202601", "view_type": "單機", "model": "S24F332EAC", "thinking": "ok", "run_id": "r"}
            self.assertTrue(is_complete_auto_verified(row))
            with (audit / "success_records.csv").open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(row))
                writer.writeheader()
                writer.writerow(row)
            self.assertEqual(load_complete_auto_verified_names(root), {target_name})
            path = root / target_name
            path.write_bytes(b"x")
            classified = classify_file(path, root, 10_000_000, set(), set(), True, set(), {path.name}, {path.name})
            self.assertNotIn("v1945_evidence_trace_missing", classified.reasons)

    def test_trace_append_is_idempotent_and_excludes_image_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = {"source_path": "D:/source/202601/a.jpg", "file_name": "a.jpg", "run_id": "r", "ocr_attempt": 1, "raw_model_output": "{}", "thumb_b64": "SECRET"}
            decision = {"retry": True, "unresolved": False, "verified": False, "evidence_guard_revision": EVIDENCE_GUARD_REVISION}
            _append_v1945_trace(tmp, result, decision, ["missing evidence"])
            _append_v1945_trace(tmp, result, decision, ["missing evidence"])
            lines = (Path(tmp) / "v1945_evidence_trace.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            self.assertNotIn("SECRET", lines[0])
            self.assertEqual(json.loads(lines[0])["evidence_guard_revision"], EVIDENCE_GUARD_REVISION)

    def test_upload_loaders_reject_old_v1945_evidence_without_guard_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit = root / "_ocr_audit" / "sample"
            audit.mkdir(parents=True)
            target_name = "M-202601-test-單機-S24F332EAC-✓＄2390-old.jpg"
            with (audit / "copied.csv").open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["period", "original_name", "target_name"])
                writer.writeheader()
                writer.writerow({"period": "202601", "original_name": "old.jpg", "target_name": target_name})
            (audit / "v1945_evidence_trace.jsonl").write_text(json.dumps({
                "trace_version": "v19.45",
                "file_name": "old.jpg",
                "period": "202601",
                "guard_decision": {"verified": True},
            }) + "\n", encoding="utf-8")
            row = {
                "auto_verified": "true", "auto_review_required": "false", "ocr_attempt": "1",
                "evidence_contract_version": "v19.45", "evidence_contract_valid": "true",
                "file_name": "old.jpg", "period": "202601", "view_type": "單機",
                "model": "S24F332EAC", "thinking": "ok", "run_id": "old",
            }
            with (audit / "success_records.csv").open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(row))
                writer.writeheader()
                writer.writerow(row)
            self.assertEqual(load_v1945_trace_names(root), set())
            self.assertEqual(load_complete_auto_verified_names(root), set())

    def test_persisted_records_keep_verification_state_for_dashboard_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session-OCR成功.json"
            path.write_text(json.dumps([{
                "data": {
                    "image": "/data/upload/1/sample.jpg",
                    "ocr_meta": {
                        "view_type": "單機",
                        "auto_verified": False,
                        "auto_review_required": True,
                        "review_status": "review_required",
                        "evidence_contract_version": "v19.45",
                        "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
                        "evidence_contract_valid": False,
                    },
                },
                "annotations": [{"created_at": "2026-07-15T00:00:00", "result": []}],
            }], ensure_ascii=False), encoding="utf-8")
            record_map = {}
            loader = object.__new__(BatchOrchestrator)
            loader._load_json_to_map(str(path), record_map)
            row = record_map["sample.jpg"]
            self.assertFalse(row["auto_verified"])
            self.assertTrue(row["auto_review_required"])
            self.assertEqual(row["review_status"], "review_required")
            self.assertEqual(row["evidence_contract_version"], "v19.45")
            self.assertEqual(row["evidence_guard_revision"], EVIDENCE_GUARD_REVISION)


if __name__ == "__main__":
    unittest.main()

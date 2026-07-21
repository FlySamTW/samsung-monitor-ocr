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
    KNOWN_SOURCE_EXPECTATIONS,
    apply_human_audited_pixel_authority,
    evidence_contract_decision,
    finalize_three_pass_outcome,
    followme_variant_evidence_reasons,
    immediate_retry_decision,
    narrated_followme_physical_cues,
    narration_evidence_consistency_reasons,
    narration_has_positive_followme_identity,
    narration_has_unmistakable_followme_fixture,
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
    unique_known_model_completion,
)
from samsung_ocr_batch_processor import (
    _merge_v1945_json_objects,
    new_request_id,
    request_binding_tail,
    validate_request_binding,
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
        self.assertTrue(decision["retry"])
        self.assertFalse(decision["verified"])

    def test_explicit_same_pass_followme_variant_remains_supported(self):
        row = {
            "model": 'FollowMe M7 32"',
            "thinking": (
                "同一實機規格牌清楚寫 FollowMe M7 32吋，"
                "白色直立支架與圓形底座屬於同一台。"
            ),
        }
        self.assertEqual(followme_variant_evidence_reasons(row), [])

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

    def test_three_plus_screen_single_never_verifies_on_first_pass(self):
        row = self._multiscreen_single()
        first = immediate_retry_decision(dict(row), 1, [], 3)
        self.assertTrue(first["retry"])
        self.assertFalse(first["verified"])
        self.assertIn("三台以上入鏡的單機候選必須完成三輪獨立複核", first["reasons"])

        third = immediate_retry_decision(dict(row), 3, [dict(row), dict(row)], 3)
        self.assertFalse(third["verified"])
        self.assertTrue(third["unresolved"])
        self.assertIn(
            "沒有 FollowMe 實體證據的三台以上完整螢幕必須依全圖幾何定案遠景",
            third["reasons"],
        )

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
        self.assertEqual(decision["normalized_evidence"]["complete_screen_count"], 2)
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

    def test_partial_neighbor_count_three_still_requires_three_pass_review(self):
        row = self._multiscreen_single(
            complete_screen_count=3,
            thinking=(
                "我看到前景中央一台完整螢幕，左右兩側鄰機都被原圖邊界裁切，"
                "其外框不完整。"
            ),
        )

        decision = immediate_retry_decision(row, 1, [], 3)

        self.assertTrue(decision["retry"])
        self.assertFalse(decision["verified"])
        self.assertIn("敘述明確只有一台完整螢幕，結構完整台數必須為1", decision["reasons"])

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
        self.assertTrue(current["thinking"].endswith("所以……"))

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

    def test_three_plus_screen_single_disagreement_stays_unresolved(self):
        row = self._multiscreen_single()
        conflicting = self._multiscreen_single(model="S27CG552EC", price="4990")
        decision = immediate_retry_decision(dict(row), 3, [dict(row), conflicting], 3)
        self.assertFalse(decision["verified"])
        self.assertTrue(decision["unresolved"])
        self.assertIn(
            "沒有 FollowMe 實體證據的三台以上完整螢幕必須依全圖幾何定案遠景",
            decision["reasons"],
        )

    def test_three_plus_screen_single_guard_applies_to_older_years_too(self):
        row = self._multiscreen_single(period="201901")
        first = immediate_retry_decision(dict(row), 1, [], 3)
        self.assertTrue(first["retry"])
        self.assertFalse(first["verified"])
        self.assertIn("三台以上入鏡的單機候選必須完成三輪獨立複核", first["reasons"])

    def test_edge_cut_narration_cannot_claim_three_complete_monitors(self):
        row = self._multiscreen_single(
            thinking=(
                "我看到中央一台螢幕，左右各有一台螢幕，但左右鄰機都被照片邊界裁切，"
                "其他區域沒有額外完整螢幕，所以……這是一般單機。"
            )
        )
        decision = immediate_retry_decision(dict(row), 1, [], 3)
        self.assertTrue(decision["retry"])
        self.assertIn(
            "敘述指出中央一台且左右鄰機被邊界裁切，完整台數不得填三台以上",
            decision["reasons"],
        )

    def test_edge_cut_two_sides_wording_cannot_claim_three_complete_monitors(self):
        row = self._multiscreen_single(
            thinking=(
                "我看到中央一台螢幕正下方有價牌，左右兩側螢幕被照片邊界裁切，"
                "全圖沒有其他完整螢幕，所以……這是一般單機。"
            )
        )
        decision = immediate_retry_decision(dict(row), 1, [], 3)
        self.assertTrue(decision["retry"])
        self.assertIn("敘述明確只有一台完整螢幕，結構完整台數必須為1", decision["reasons"])

    def test_wide_multiscreen_single_without_bound_identity_never_verifies(self):
        row = self._multiscreen_single(
            model=None,
            price=None,
            label_ownership="ambiguous",
            complete_screen_count=8,
            thinking="我看到一整排螢幕陳列，上方與下方均有完整螢幕，所以……這是一般單機。",
        )
        decision = immediate_retry_decision(dict(row), 3, [dict(row), dict(row)], 3)
        self.assertFalse(decision["verified"])
        self.assertTrue(decision["unresolved"])
        self.assertIn("寬廣多螢幕陳列缺少可歸屬的單機身分證據", decision["reasons"])

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
        self.assertTrue(decision["retry"])
        self.assertIn("敘述明確只有一台完整螢幕，結構完整台數必須為1", decision["reasons"])

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
        prompt = Path(__file__).resolve().parents[1].joinpath("samsung_ocr_prompt.txt").read_text(encoding="utf-8")
        self.assertIn("若價牌只有「建議售價」一個明確商品金額", prompt)

    def test_current_year_price_delta_gets_one_stateless_role_confirmation(self):
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
        self.assertTrue(first["retry"])
        self.assertIn(
            "2026 價差照片需第二輪無記憶核對價牌角色",
            first["reasons"],
        )

        repeated = dict(row)
        repeated["ocr_attempt"] = 2
        second = immediate_retry_decision(repeated, 2, [dict(row)], 3)
        self.assertFalse(second["retry"])
        self.assertTrue(second["verified"])

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
        self.assertIn("Traditional Chinese first-person observation", batch.V1945_OUTPUT_CONTRACT)
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
        self.assertTrue(displayed.startswith("我看到"))
        self.assertTrue(displayed.endswith("所以……"))
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
        self.assertIsNone(row["model"])
        self.assertIsNone(row["price"])

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

    def test_large_official_price_difference_keeps_badge_and_gets_one_confirmation(self):
        row = {
            "file_name": "M-202605-price-diff.jpg", "view_type": "單機", "category": "單機",
            "model": "S27D392GAC", "price": "4290", "quality_issue": "",
            "price_status": "high", "price_diff_percent": 36.7,
            "thinking": "唯一主角自己的實體規格牌與價格牌清楚可讀。",
            **evidence(1, True, "matched"),
        }
        first = immediate_retry_decision(dict(row), 1, [], 3)
        self.assertTrue(first["retry"])
        self.assertFalse(first["verified"])
        self.assertIn("2026 價差照片需第二輪無記憶核對價牌角色", first["reasons"])
        repeated = dict(row)
        repeated.update(
            independent_pass=True,
            request_id_verified=True,
            prior_answer_exposed=False,
            prompt_contamination=False,
            runtime_health={"healthy": True},
        )
        second = immediate_retry_decision(dict(repeated), 2, [dict(repeated)], 3)
        self.assertFalse(second["retry"])
        self.assertTrue(second["verified"])
        self.assertEqual(row["price_status"], "high")
        self.assertEqual(row["price_diff_percent"], 36.7)

    def test_complete_current_year_followme_requires_one_stateless_confirmation(self):
        physical = [
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
            {"cue": "round_base", "same_subject": True, "strength": "strong"},
        ]
        row = {
            "file_name": "M-202605-followme.jpg", "view_type": "單機", "category": "單機",
            "model": 'FollowMe M7 32"', "price": "12900", "quality_issue": "",
            "thinking": "同一台實機有白色垂直支架與圓形底座，規格與價格屬於唯一主角。",
            **evidence(1, True, "matched", physical),
        }
        first = immediate_retry_decision(dict(row), 1, [], 3)
        self.assertTrue(first["retry"])
        self.assertFalse(first["verified"])
        self.assertIn("2026 FollowMe 身分與價牌需第二輪無記憶獨立確認", first["reasons"])

        second = immediate_retry_decision(dict(row), 2, [dict(row)], 3)
        self.assertFalse(second["retry"])
        self.assertTrue(second["verified"])
        self.assertEqual(second["reasons"], [])

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
        self.assertTrue(first["retry"])
        self.assertFalse(first["verified"])
        self.assertIn("2026 FollowMe 身分與價牌需第二輪無記憶獨立確認", first["reasons"])
        self.assertNotIn("FollowMe 缺少同一實機的物理支架證據", first["reasons"])

        second = immediate_retry_decision(dict(row), 2, [dict(row)], 3)
        self.assertTrue(second["verified"])
        self.assertEqual(second["reasons"], [])

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
        self.assertTrue(first["retry"])
        self.assertFalse(first["verified"])
        self.assertIn("2026 FollowMe 身分與價牌需第二輪無記憶獨立確認", first["reasons"])

        second = immediate_retry_decision(dict(valid), 2, [dict(valid)], 3)
        self.assertTrue(second["verified"])
        self.assertEqual(second["reasons"], [])

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

    def test_current_year_distant_requires_three_consistent_passes(self):
        row = {
            "file_name": "M-202605-distant.jpg", "view_type": "遠景", "category": "遠景",
            "model": None, "price": None, "quality_issue": "",
            "thinking": "三台完整入鏡，沒有唯一主角，也無法對應主角自己的規格與價格。",
            **evidence(3, False, "not_visible"),
        }
        first = immediate_retry_decision(dict(row), 1, [], 3)
        second = immediate_retry_decision(dict(row), 2, [dict(row)], 3)
        third = immediate_retry_decision(dict(row), 3, [dict(row), dict(row)], 3)
        self.assertTrue(first["retry"])
        self.assertTrue(second["retry"])
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
        self.assertTrue(first["retry"])
        self.assertTrue(second["retry"])
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

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from skills.audit_fields import immediate_retry_decision, validate_evidence_contract
from skills.batch_orchestrator import BatchOrchestrator
from skills.review_pass_contract import (
    FIXED_RESPONSE_FIELD_CONTRACT,
    RESPONSE_FIELD_NAMES,
    REVIEW_PASS_CONTRACT_VERSION,
    apply_trusted_source_view_lock,
    build_pass_instruction,
    build_review_system_prompt,
    trusted_source_view_metadata,
)
from skills.runtime_health_gate import review_prompt_leak_reasons
from tools.rerun_staged_candidates import stage_images


def source_hint(view: str) -> dict:
    return {
        "source_view_hint": view,
        "source_view_hint_locked": True,
        "source_view_hint_source": "capture-workflow",
        "source_view_hint_version": "2026.08",
    }


class ReviewPassContractTests(unittest.TestCase):
    def test_hint_requires_lock_and_provenance(self):
        self.assertEqual(
            trusted_source_view_metadata({"source_view_hint": "遠景"})[
                "source_view_hint"
            ],
            "",
        )
        accepted = trusted_source_view_metadata(source_hint("closeup"))
        self.assertEqual(accepted["source_view_hint"], "近景")
        self.assertTrue(accepted["source_view_hint_locked"])

    def test_all_passes_share_exact_response_field_contract(self):
        prompts = [build_pass_instruction(index) for index in (1, 2, 3)]
        for field in RESPONSE_FIELD_NAMES:
            self.assertTrue(all(field in prompt for prompt in prompts), field)
        self.assertTrue(all(FIXED_RESPONSE_FIELD_CONTRACT in prompt for prompt in prompts))
        self.assertNotEqual(prompts[0], prompts[1])
        self.assertNotEqual(prompts[1], prompts[2])

    def test_fixed_review_instruction_does_not_trip_its_own_memory_guard(self):
        for index in (2, 3):
            instruction = build_pass_instruction(index)
            reasons = review_prompt_leak_reasons(
                index,
                [{"role": "user", "content": instruction}],
                injected_prior_results=[],
                prior_results_for_leak_check=[],
            )
            self.assertEqual(reasons, [], (index, reasons))

    def test_review_system_is_independent_from_first_pass_prompt(self):
        prompt = build_review_system_prompt()
        self.assertIn(REVIEW_PASS_CONTRACT_VERSION, prompt)
        self.assertIn("你看不到、也不得推測任何前輪答案", prompt)
        self.assertNotIn("samsung_ocr_prompt.txt", prompt)
        self.assertNotIn("SAMPLE-FIRST-PASS-VALUE", prompt)

    def test_trusted_distant_hint_does_not_require_three_screen_reclassification(self):
        record = {
            **source_hint("遠景"),
            "period": "202608",
            "view_type": "遠景",
            "category": "遠景",
            "complete_screen_count": 1,
            "unique_main": False,
            "label_ownership": "not_applicable",
            "followme_physical_evidence": [],
            "model": None,
            "price": None,
            "quality_issue": "無",
            "thinking": "我看到本輪結論：遠景，無型號，無價格。來源已指定遠景。",
        }
        valid, reasons, _normalized = validate_evidence_contract(record)
        self.assertTrue(valid, reasons)
        decision = immediate_retry_decision(record, 1, [])
        self.assertTrue(decision["verified"], decision["reasons"])
        self.assertFalse(decision["retry"])

    def test_trusted_close_hint_is_not_converted_by_wide_scene_heuristic(self):
        record = {
            **source_hint("近景"),
            "period": "202608",
            "view_type": "單機",
            "category": "單機",
            "complete_screen_count": 3,
            "unique_main": True,
            "label_ownership": "matched",
            "followme_physical_evidence": [],
            "model": "S27CG552EC",
            "price": "4990",
            "quality_issue": "無",
            "thinking": (
                "我看到本輪結論：單機，S27CG552EC，4,990元。"
                "右上側標與同一主角價牌清楚可讀，型號與價格同屬一台。"
            ),
        }
        decision = immediate_retry_decision(record, 1, [])
        self.assertEqual(record["view_type"], "單機")
        self.assertNotIn("沒有 FollowMe 實體證據的三台以上完整螢幕應定案遠景", decision["reasons"])
        self.assertNotIn("寬景單機候選需第二輪確認是否為 FollowMe", decision["reasons"])

    def test_model_output_conflicting_with_locked_hint_retries_only_missing_fields(self):
        record = {
            **source_hint("近景"),
            "period": "202608",
            "view_type": "遠景",
            "category": "遠景",
            "complete_screen_count": 3,
            "unique_main": False,
            "label_ownership": "not_applicable",
            "followme_physical_evidence": [],
            "model": None,
            "price": None,
            "quality_issue": "無",
            "thinking": "我看到本輪結論：遠景，無型號，無價格。",
        }
        decision = immediate_retry_decision(record, 1, [])
        self.assertTrue(decision["retry"])
        self.assertNotIn("來源遠近景標示與本輪結構衝突", decision["reasons"])
        self.assertIn("2026 單機缺型號", decision["reasons"])
        self.assertIn("2026 單機缺價格", decision["reasons"])
        self.assertEqual(record["view_type"], "單機")
        self.assertTrue(record["source_view_hint_conflict"])
        self.assertTrue(record["source_view_hint_override_applied"])

    def test_locked_distant_can_close_without_model_screen_count(self):
        record = {
            **source_hint("遠景"),
            "period": "202608",
            "view_type": "單機",
            "category": "單機",
            "complete_screen_count": None,
            "unique_main": True,
            "label_ownership": "matched",
            "followme_physical_evidence": [],
            "model": "S27CG552EC",
            "price": "4990",
            "quality_issue": "不合格-沒有價格牌",
            "thinking": "來源擷取系統指定為遠景。",
        }
        apply_trusted_source_view_lock(record)
        valid, reasons, _normalized = validate_evidence_contract(record)
        self.assertTrue(valid, reasons)
        decision = immediate_retry_decision(record, 1, [])
        self.assertTrue(decision["verified"], decision["reasons"])
        self.assertFalse(decision["retry"])
        self.assertEqual(record["view_type"], "遠景")
        self.assertIsNone(record["model"])
        self.assertIsNone(record["price"])

    def test_source_lock_is_idempotent_and_preserves_original_observation(self):
        record = {
            **source_hint("遠景"),
            "view_type": "單機",
            "category": "單機",
            "model": "S27CG552EC",
            "price": "4990",
            "complete_screen_count": 1,
            "unique_main": True,
            "label_ownership": "matched",
            "followme_physical_evidence": [],
        }
        apply_trusted_source_view_lock(record)
        apply_trusted_source_view_lock(record)
        self.assertEqual(record["source_view_observed_view"], "單機")
        self.assertEqual(record["source_view_observed_model"], "S27CG552EC")
        self.assertEqual(record["source_view_observed_price"], "4990")
        self.assertTrue(record["source_view_hint_conflict"])
        self.assertTrue(record["source_view_hint_override_applied"])

    def test_staging_preserves_explicit_source_hint_without_filename_inference(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.jpg"
            source.write_bytes(b"not-an-image-needed-for-copy-test")
            staging = root / "staging"
            row = {
                "source_path": str(source),
                "period": "202608",
                "audit_folder": str(root / "audit"),
                **source_hint("遠景"),
            }
            self.assertEqual(stage_images([row], staging), 1)
            payload = json.loads(
                (staging / ".ocr_source_map.json").read_text(encoding="utf-8")
            )
            item = payload["items"][source.name]
            self.assertEqual(item["source_view_hint"], "遠景")
            self.assertTrue(item["source_view_hint_locked"])
            self.assertEqual(item["source_view_hint_source"], "capture-workflow")

    def test_orchestrator_passes_trusted_hint_to_result_metadata(self):
        orchestrator = BatchOrchestrator.__new__(BatchOrchestrator)
        orchestrator.source_metadata_map = {
            "source.jpg": {
                "original_source_path": "D:/capture/source.jpg",
                **source_hint("近景"),
            }
        }
        metadata = orchestrator._source_metadata("source.jpg", "D:/stage/source.jpg")
        self.assertEqual(metadata["source_view_hint"], "近景")
        self.assertTrue(metadata["source_view_hint_locked"])
        self.assertEqual(
            metadata["review_pass_contract_version"], REVIEW_PASS_CONTRACT_VERSION
        )


if __name__ == "__main__":
    unittest.main()

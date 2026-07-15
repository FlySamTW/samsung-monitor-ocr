from __future__ import annotations

import unittest
from pathlib import Path

from skills.runtime_health_gate import BLOCKED_NARRATION, evaluate_runtime_health


def record(**updates):
    value = {
        "view_type": "單機",
        "model": "S24F332EAC",
        "price": "2390",
        "followme_physical_evidence": [],
    }
    value.update(updates)
    return value


class RuntimeHealthGateTests(unittest.TestCase):
    def test_health_gate_is_wired_into_prompt_and_batch_loop(self):
        root = Path(__file__).resolve().parents[1]
        processor = (root / "samsung_ocr_batch_processor.py").read_text(encoding="utf-8")
        orchestrator = (root / "skills" / "batch_orchestrator.py").read_text(encoding="utf-8")
        self.assertIn("prompt_health_reasons = review_prompt_leak_reasons(", processor)
        self.assertIn('"runtime_health_stop": True', processor)
        self.assertIn("runtime_health = evaluate_runtime_health(", orchestrator)
        self.assertIn("if not runtime_health.allow_processing:", orchestrator)
        self.assertIn("self.stop_event.set()", orchestrator)

    def test_plain_narration_is_safe_but_upload_still_needs_separate_authority(self):
        decision = evaluate_runtime_health(record(), "唯一主角與價牌歸屬一致。")
        self.assertTrue(decision.healthy)
        self.assertTrue(decision.allow_processing)
        self.assertFalse(decision.allow_upload)
        authorized = evaluate_runtime_health(
            record(), "唯一主角與價牌歸屬一致。", upstream_upload_authorized=True
        )
        self.assertTrue(authorized.allow_upload)

    def test_naked_json_is_never_returned_as_display_narration(self):
        raw = '判讀完成 {"view_type":"單機","model":"S24F332EAC","price":"2390"}'
        decision = evaluate_runtime_health(record(), raw, upstream_upload_authorized=True)
        self.assertFalse(decision.healthy)
        self.assertFalse(decision.allow_processing)
        self.assertFalse(decision.allow_upload)
        self.assertIn("ui_narration_contains_raw_structure", decision.reasons)
        self.assertEqual(decision.display_narration, BLOCKED_NARRATION)
        self.assertNotIn("view_type", decision.display_narration)

    def test_raw_field_assignment_and_json_fence_are_blocked(self):
        for narration in ('model=S24F332EAC price=2390', '```json\n{"price":2390}\n```'):
            with self.subTest(narration=narration):
                decision = evaluate_runtime_health(record(), narration)
                self.assertIn("ui_narration_contains_raw_structure", decision.reasons)

    def test_second_pass_accepts_only_one_fresh_user_turn(self):
        messages = [
            {"role": "system", "content": "使用固定規則獨立判讀照片。"},
            {"role": "user", "content": [{"type": "text", "text": "這是一張全新照片，請獨立觀察。"}]},
        ]
        decision = evaluate_runtime_health(record(), "唯一主角清楚。", attempt=2, messages=messages)
        self.assertTrue(decision.healthy)

    def test_second_or_third_pass_rejects_injected_prior_result(self):
        messages = [{"role": "user", "content": "請獨立判讀這張照片。"}]
        for attempt in (2, 3):
            with self.subTest(attempt=attempt):
                decision = evaluate_runtime_health(
                    record(),
                    "唯一主角清楚。",
                    attempt=attempt,
                    messages=messages,
                    injected_prior_results=[{"view_type": "遠景", "model": None, "price": None}],
                    upstream_upload_authorized=True,
                )
                self.assertIn("review_prior_result_injected", decision.reasons)
                self.assertFalse(decision.allow_processing)
                self.assertFalse(decision.allow_upload)

    def test_review_rejects_conversation_history_and_correction_language(self):
        cases = [
            [
                {"role": "assistant", "content": "上次判斷為遠景。"},
                {"role": "user", "content": "請重新看照片。"},
            ],
            [{"role": "user", "content": "第一次答案只是待推翻假設，請修正上一輪型號與價格。"}],
        ]
        for messages in cases:
            with self.subTest(messages=messages):
                decision = evaluate_runtime_health(record(), "獨立判讀完成。", attempt=3, messages=messages)
                self.assertFalse(decision.healthy)
                self.assertTrue(any(reason.startswith("review_") for reason in decision.reasons))

    def test_review_detects_unlabelled_prior_values_and_reason(self):
        prior = {
            "view_type": "遠景",
            "model": "S32FM703UC",
            "price": "12990",
            "reasons": ["價牌歸屬不明"],
        }
        cases = (
            "請重點確認 S32FM703UC 與 12990。",
            "請注意價牌歸屬不明。",
        )
        for prompt in cases:
            with self.subTest(prompt=prompt):
                decision = evaluate_runtime_health(
                    record(),
                    "獨立判讀完成。",
                    attempt=2,
                    messages=[{"role": "user", "content": prompt}],
                    prior_results_for_leak_check=[prior],
                )
                self.assertFalse(decision.healthy)
                self.assertTrue(
                    {"review_prior_value_present", "review_prior_reason_present"} & set(decision.reasons)
                )

    def test_fresh_review_prompt_stays_clean_when_prior_values_are_only_gate_reference(self):
        decision = evaluate_runtime_health(
            record(),
            "獨立判讀完成。",
            attempt=3,
            messages=[{"role": "user", "content": "這是全新照片，請只根據當前影像獨立判讀。"}],
            prior_results_for_leak_check=[
                {"view_type": "遠景", "model": "S32FM703UC", "price": "12990", "reasons": ["價牌歸屬不明"]}
            ],
        )
        self.assertTrue(decision.healthy)

    def test_generic_prior_class_word_does_not_make_neutral_prompt_contaminated(self):
        decision = evaluate_runtime_health(
            record(),
            "獨立判讀完成。",
            attempt=2,
            messages=[{"role": "user", "content": "請獨立判斷單機或遠景。"}],
            prior_results_for_leak_check=[{"view_type": "遠景", "category": "遠景"}],
        )
        self.assertTrue(decision.healthy)

    def test_missing_review_prompt_fails_closed(self):
        decision = evaluate_runtime_health(record(), "獨立判讀完成。", attempt=2, messages=[])
        self.assertIn("review_prompt_missing", decision.reasons)
        self.assertFalse(decision.allow_processing)

    def test_absurd_prices_fail_closed(self):
        for price in ("60", "999999", "165HZ", "12900/13900", -2390, True):
            with self.subTest(price=price):
                decision = evaluate_runtime_health(record(price=price), "唯一主角清楚。", upstream_upload_authorized=True)
                self.assertTrue(any(reason.startswith("price_") for reason in decision.reasons))
                self.assertFalse(decision.allow_processing)
                self.assertFalse(decision.allow_upload)

    def test_distant_with_strong_followme_evidence_fails_closed(self):
        physical = [
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
            {"cue": "round_base", "same_subject": True, "strength": "strong"},
        ]
        decision = evaluate_runtime_health(
            record(view_type="遠景", model=None, price=None, followme_physical_evidence=physical),
            "三台完整入鏡，沒有唯一主角。",
            upstream_upload_authorized=True,
        )
        self.assertIn("distant_followme_strong_evidence_conflict", decision.reasons)
        self.assertFalse(decision.allow_processing)
        self.assertFalse(decision.allow_upload)

    def test_distant_with_followme_sku_also_fails_closed(self):
        decision = evaluate_runtime_health(
            record(view_type="遠景", model="S32FM703UC", price=None),
            "三台完整入鏡，沒有唯一主角。",
        )
        self.assertIn("distant_followme_strong_evidence_conflict", decision.reasons)

    def test_weak_followme_promotion_alone_does_not_create_physical_conflict(self):
        weak = [{"cue": "nearby_signage_only", "same_subject": False, "strength": "weak"}]
        decision = evaluate_runtime_health(
            record(view_type="遠景", model=None, price=None, followme_physical_evidence=weak),
            "三台完整入鏡，沒有唯一主角。",
        )
        self.assertTrue(decision.healthy)


if __name__ == "__main__":
    unittest.main()

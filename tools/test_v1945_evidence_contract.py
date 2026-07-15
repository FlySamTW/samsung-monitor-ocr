import csv
import json
import tempfile
import unittest
from pathlib import Path

import samsung_ocr_batch_processor as batch

from skills.audit_fields import (
    EVIDENCE_GUARD_REVISION,
    evidence_contract_decision,
    immediate_retry_decision,
    validate_evidence_contract,
)
from tools.prepare_drive_upload_manifest import (
    classify_file,
    load_complete_auto_verified_names,
    load_v1945_trace_names,
)
from tools.rerun_questionable_records import is_complete_auto_verified
from skills.batch_orchestrator import BatchOrchestrator, _append_v1945_trace
from skills.runtime_health_gate import review_prompt_leak_reasons
from skills.model_validation import has_photo_label_model_evidence, unique_known_model_completion
from samsung_ocr_batch_processor import _merge_v1945_json_objects


def evidence(count, unique, ownership="not_visible", physical=None):
    return {
        "complete_screen_count": count,
        "unique_main": unique,
        "label_ownership": ownership,
        "followme_physical_evidence": physical or [],
    }


class EvidenceContractTests(unittest.TestCase):
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
        self.assertIn("complete_screen_count 0, 1, or 2 can never be", batch.V1945_OUTPUT_CONTRACT)

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

    def test_distant_cannot_carry_unresolved_followme_physical_evidence_or_sku(self):
        physical = [
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
            {"cue": "round_base", "same_subject": True, "strength": "strong"},
        ]
        row = {
            "file_name": "M-202605-distant-followme.jpg", "view_type": "遠景", "category": "遠景",
            "model": None, "price": None, "quality_issue": "",
            "thinking": "三台完整入鏡，沒有唯一主角；其中可見 S32FM703UC 的白色垂直支架與圓形底座。",
            **evidence(3, False, "not_visible", physical),
        }
        valid, errors, _ = validate_evidence_contract(row)
        self.assertFalse(valid)
        self.assertIn("distant_followme_physical_conflict", errors)
        decision = immediate_retry_decision(row, 3, [dict(row), dict(row)], 3)
        self.assertTrue(decision["unresolved"])
        self.assertIn("遠景仍含未排除的 FollowMe 線索", decision["reasons"])

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

    def test_large_official_price_difference_requires_independent_confirmation(self):
        row = {
            "file_name": "M-202605-price-diff.jpg", "view_type": "單機", "category": "單機",
            "model": "S27D392GAC", "price": "4290", "quality_issue": "",
            "price_status": "high", "price_diff_percent": 36.7,
            "thinking": "唯一主角自己的實體規格牌與價格牌清楚可讀。",
            **evidence(1, True, "matched"),
        }
        first = immediate_retry_decision(dict(row), 1, [], 3)
        self.assertTrue(first["retry"])
        self.assertIn("照片價格與官方參考價差異過大，需獨立重讀", first["reasons"])
        second = immediate_retry_decision(dict(row), 2, [dict(row)], 3)
        self.assertFalse(second["retry"])
        self.assertTrue(second["verified"])

    def test_current_year_followme_requires_second_consistent_pass(self):
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
        second = immediate_retry_decision(dict(row), 2, [dict(row)], 3)
        self.assertFalse(second["retry"])
        self.assertTrue(second["verified"])

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
        self.assertEqual(first["reasons"], ["2026 FollowMe 必須完成第二輪實體證據複核"])
        second = immediate_retry_decision(dict(row), 2, [dict(row)], 3)
        self.assertTrue(second["verified"])
        self.assertNotIn("FollowMe 缺少同一實機的物理支架證據", second["reasons"])

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

    def test_followme_sku_requires_physical_evidence_and_second_pass(self):
        missing = {
            "file_name": "M-202605-followme-sku.jpg", "view_type": "單機", "category": "單機",
            "model": "S32FM703UC", "price": "12990", "quality_issue": "",
            "thinking": "唯一主角自己的規格與價格清楚可讀。",
            **evidence(1, True, "matched"),
        }
        first_missing = immediate_retry_decision(dict(missing), 1, [], 3)
        self.assertTrue(first_missing["retry"])
        self.assertIn("followme_physical_evidence_insufficient", first_missing["reasons"])

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
        self.assertIn("2026 FollowMe 必須完成第二輪實體證據複核", first["reasons"])
        second = immediate_retry_decision(dict(valid), 2, [dict(valid)], 3)
        self.assertFalse(second["retry"])
        self.assertTrue(second["verified"])

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
                {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
                {"cue": "round_base", "same_subject": True, "strength": "strong"},
            ],
        })
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

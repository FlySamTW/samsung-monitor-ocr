import copy
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.audit_fields import EVIDENCE_GUARD_REVISION, finalize_three_pass_outcome, validate_evidence_contract


IMAGE_HASH = "a" * 64


def make_pass(
    view="單機",
    model=None,
    price=None,
    count=1,
    unique=True,
    ownership="matched",
    physical=None,
    *,
    healthy=True,
    image_hash=IMAGE_HASH,
    **extra,
):
    row = {
        "view_type": view,
        "category": view,
        "model": model,
        "price": price,
        "complete_screen_count": count,
        "unique_main": unique,
        "label_ownership": ownership,
        "followme_physical_evidence": list(physical or []),
        "independent_pass": True,
        "request_binding_enforced": True,
        "request_id_verified": True,
        "prior_answer_exposed": False,
        "prompt_contamination": False,
        "input_image_sha256": image_hash,
        "runtime_health": {"healthy": healthy},
        "thinking": f"模型原始判讀：{view}。",
    }
    row.update(extra)
    return row


def unresolved():
    return {
        "attempt": 3,
        "retry": False,
        "unresolved": True,
        "verified": False,
        "reasons": ["core_evidence_disagreement"],
    }


class ThreePassFinalizationTests(unittest.TestCase):
    def test_final_zoom_price_wins_over_one_extra_digit_outlier(self):
        history = [
            make_pass("單機", "S27CG552EC", "74990", 1, True, "matched"),
            make_pass("單機", "S27CG552EC", "74990", 3, True, "matched"),
        ]
        current = make_pass(
            "單機",
            "S27CG552EC",
            "7490",
            1,
            True,
            "matched",
            official_price=4990,
            price_status="high",
            thinking="中央價牌清楚顯示價格 7,490 元。",
        )

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(current["model"], "S27CG552EC")
        self.assertEqual(current["price"], "7490")

    def test_one_strong_wide_vote_settles_two_identity_free_weak_single_votes(self):
        history = [
            make_pass(
                "單機", None, None, 5, False, "not_visible",
                thinking="一整排多台完整螢幕，沒有唯一主角，也沒有可歸屬價牌。",
            ),
            make_pass(
                "單機", None, None, 1, True, "not_visible",
                thinking="一排螢幕陳列，無法鎖定唯一主角的規格與價格。",
            ),
        ]
        current = make_pass(
            "單機", None, None, 1, True, "matched",
            thinking="一整排多台螢幕陳列，沒有自己的型號或價格。",
        )

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(
            result["adjudication_rule"],
            "three_pass_wide_scene_structural_consensus",
        )
        self.assertEqual(current["view_type"], "遠景")
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])

    def test_restart_recovery_joins_exact_bound_calls_one_and_three(self):
        from tools.finalize_existing_three_pass_reviews import _load_three_call_groups

        with TemporaryDirectory() as temp:
            trace = Path(temp) / "trace.jsonl"
            rows = []
            for attempt, run_id, timestamp in (
                (1, "run-a", "2026-07-17T03:35:00"),
                (3, "run-b", "2026-07-17T04:00:00"),
            ):
                parsed = make_pass("單機", "S24F332EAC", "2390", 2, True, "matched")
                parsed.update({
                    "file_name": "sample-636.jpg",
                    "source_item_id": "same-source",
                    "period": "202601",
                    "ocr_attempt": attempt,
                    "timestamp": timestamp,
                    "run_id": run_id,
                })
                rows.append({
                    "file_name": "sample-636.jpg",
                    "source_item_id": "same-source",
                    "run_id": run_id,
                    "timestamp": timestamp,
                    "parsed_output": parsed,
                })
            trace.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                encoding="utf-8",
            )

            groups = _load_three_call_groups(trace)

        self.assertEqual(
            [item["ocr_attempt"] for item in groups["sample-636.jpg"]],
            [1, 3],
        )

    def test_full_same_run_three_call_group_is_not_replaced_by_source_tail(self):
        from tools.finalize_existing_three_pass_reviews import _load_three_call_groups

        with TemporaryDirectory() as temp:
            trace = Path(temp) / "trace.jsonl"
            rows = []
            for attempt in (1, 2, 3):
                parsed = make_pass("單機", None, None, attempt + 2, attempt == 1, "not_visible")
                parsed.update({
                    "file_name": "wide-1099.jpg",
                    "source_item_id": "same-source",
                    "period": "202601",
                    "ocr_attempt": attempt,
                    "timestamp": f"2026-07-17T05:00:0{attempt}",
                    "run_id": "formal-run",
                })
                rows.append({
                    "file_name": "wide-1099.jpg",
                    "source_item_id": "same-source",
                    "run_id": "formal-run",
                    "timestamp": parsed["timestamp"],
                    "parsed_output": parsed,
                })
            trace.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                encoding="utf-8",
            )

            groups = _load_three_call_groups(trace)

        self.assertEqual(
            [item["ocr_attempt"] for item in groups["wide-1099.jpg"]],
            [1, 2, 3],
        )

    def test_two_no_complete_screen_scenes_finalize_truthful_distant(self):
        history = [
            make_pass("遠景", None, None, 0, False, "not_visible"),
            make_pass("單機", None, None, 1, True, "ambiguous"),
        ]
        current = make_pass("遠景", None, None, 0, False, "not_visible")

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(result["adjudication_rule"], "two_pass_no_complete_screen_scene_consensus")
        self.assertEqual(current["complete_screen_count"], 0)
        self.assertEqual(current["view_type"], "遠景")
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])

    def test_one_or_two_complete_screens_still_cannot_claim_distant(self):
        for count in (1, 2):
            valid, errors, _normalized = validate_evidence_contract(
                make_pass("遠景", None, None, count, False, "not_visible")
            )
            self.assertFalse(valid)
            self.assertIn("distant_evidence_inconsistent", errors)

    def test_three_bound_subthree_distant_claims_finish_as_conservative_single(self):
        history = [
            make_pass("遠景", None, None, 2, False, "not_visible"),
            make_pass("遠景", None, None, 2, False, "ambiguous"),
        ]
        current = make_pass("遠景", None, None, 2, False, "not_visible")

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertFalse(result["unresolved"])
        self.assertEqual(current["view_type"], "單機")
        self.assertEqual(current["complete_screen_count"], 2)
        self.assertTrue(current["unique_main"])
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])
        self.assertEqual(
            result["adjudication_rule"],
            "three_pass_subthree_distant_conflict_conservative_single",
        )

    def test_one_valid_single_one_invalid_distant_one_valid_distant_finishes_single(self):
        history = [
            make_pass("單機", "S32CG552EC", "6990", 2, True, "matched"),
            make_pass("遠景", None, None, 2, False, "not_visible"),
        ]
        current = make_pass("遠景", None, None, 3, False, "not_visible")

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(current["view_type"], "單機")
        self.assertEqual(current["complete_screen_count"], 2)
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])

    def test_subthree_fallback_never_accepts_contaminated_pass(self):
        history = [
            make_pass("遠景", None, None, 2, False, "not_visible"),
            make_pass(
                "遠景", None, None, 2, False, "not_visible",
                prior_answer_exposed=True,
            ),
        ]
        current = make_pass("遠景", None, None, 2, False, "not_visible")

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertFalse(result["verified"])
        self.assertTrue(result["technical_retry_required"])

    def test_single_then_two_distant_finalizes_truthful_distant(self):
        history = [
            make_pass("單機", "S27F612EAC", "5990", 3, True, "matched"),
            make_pass("遠景", None, None, 3, False, "ambiguous"),
        ]
        current = make_pass("遠景", None, None, 3, False, "not_visible")

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertFalse(result["unresolved"])
        self.assertEqual(current["view_type"], "遠景")
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])
        self.assertEqual(result["adjudication_rule"], "two_pass_distant_structural_consensus")
        self.assertEqual(current["evidence_guard_revision"], EVIDENCE_GUARD_REVISION)

    def test_two_distant_results_survive_one_local_runtime_narration_conflict(self):
        history = [
            make_pass("遠景", None, None, 3, False, "ambiguous"),
            make_pass("單機", "S27F612EAC", "5990", 1, True, "matched"),
        ]
        current = make_pass(
            "遠景", None, None, 4, False, "not_visible", healthy=False
        )
        current["runtime_health"]["reasons"] = [
            "structured_narration_followme_conflict"
        ]

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertFalse(result["unresolved"])
        self.assertEqual(current["view_type"], "遠景")
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])

    def test_cross_photo_or_prior_answer_distant_pass_never_supplies_majority(self):
        unsafe_rows = [
            make_pass(
                "遠景", None, None, 3, False, "ambiguous",
                cross_photo_duplicate_core_suspected=True,
            ),
            make_pass(
                "遠景", None, None, 3, False, "ambiguous",
                prior_answer_exposed=True,
            ),
        ]
        for unsafe in unsafe_rows:
            with self.subTest(unsafe=unsafe):
                history = [
                    unsafe,
                    make_pass("單機", "S27F612EAC", "5990", 1, True, "matched"),
                ]
                current = make_pass("遠景", None, None, 3, False, "not_visible")

                result = finalize_three_pass_outcome(current, history, unresolved())

                self.assertFalse(result["verified"])
                self.assertTrue(result["technical_retry_required"])

    def test_single_consensus_keeps_supported_model_price_pair(self):
        history = [
            make_pass(model="S27CG552EC", price="4990"),
            make_pass(model="S27CG552EC", price="4990"),
        ]
        current = make_pass(model="S27CG552EC", price="6990")

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(current["model"], "S27CG552EC")
        self.assertEqual(current["price"], "4990")

    def test_no_field_consensus_finishes_single_with_null_fields(self):
        history = [
            make_pass(model="S24A", price="3990"),
            make_pass(model="S25B", price="4990"),
        ]
        current = make_pass(model="S26C", price="5990")

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(current["view_type"], "單機")
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])

    def test_two_edge_cut_narrations_correct_structured_three_to_one(self):
        edge_text = (
            "我看到中央一台螢幕，左右各有一台螢幕，但左右鄰機都被照片邊界裁切，"
            "整張照片其他區域沒有額外完整螢幕，所以……這是一般單機。"
        )
        history = [
            make_pass("單機", None, None, 3, True, "not_visible", thinking=edge_text),
            make_pass("遠景", None, None, 3, False, "not_visible"),
        ]
        current = make_pass("單機", None, None, 3, True, "ambiguous", thinking=edge_text)

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(result["adjudication_rule"], "two_pass_edge_cut_frame_consensus")
        self.assertEqual(current["view_type"], "單機")
        self.assertEqual(current["complete_screen_count"], 1)
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])

    def test_edge_cut_rule_does_not_hide_other_complete_display_rows(self):
        text = (
            "我看到中央一台螢幕，左右各有一台螢幕且被照片邊界裁切，"
            "但上方另一排還有三台四邊四角完整螢幕，所以……這是遠景。"
        )
        history = [
            make_pass("單機", None, None, 3, True, "ambiguous", thinking=text),
            make_pass("遠景", None, None, 3, False, "not_visible"),
        ]
        current = make_pass("遠景", None, None, 4, False, "not_visible")

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(result["adjudication_rule"], "two_pass_distant_structural_consensus")
        self.assertEqual(current["view_type"], "遠景")

    def test_one_structural_distant_pass_vetoes_two_weak_wide_single_votes(self):
        weak_one = make_pass(
            "單機", None, None, 1, True, "ambiguous",
            thinking=(
                "我看到一整排螢幕陳列，中央一台完整，左右螢幕被照片邊界裁切，"
                "上方與遠處也有螢幕，但沒有可歸屬的型號或價格，所以……這是一般單機。"
            ),
        )
        structural_distant = make_pass(
            "遠景", None, None, 8, False, "not_visible",
            thinking=(
                "我看到上方三台、中間三台、下方兩台四邊四角完整的螢幕，"
                "無法鎖定唯一主角及其自己的規格與價格，整體符合「遠景」條件。"
            ),
        )
        current = make_pass(
            "單機", None, None, 1, True, "ambiguous",
            thinking=(
                "我看到一排螢幕展示架，上方與左右邊緣也有其他螢幕，"
                "但無型號無價格，所以……這是一般單機。"
            ),
        )

        result = finalize_three_pass_outcome(
            current, [weak_one, structural_distant], unresolved()
        )

        self.assertTrue(result["verified"])
        self.assertEqual(
            result["adjudication_rule"],
            "distant_structural_veto_over_two_weak_wide_single_votes",
        )
        self.assertEqual(current["view_type"], "遠景")
        self.assertGreaterEqual(current["complete_screen_count"], 3)
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])

    def test_three_weak_wide_single_labels_with_three_plus_structure_finish_distant(self):
        weak = make_pass(
            "單機", None, None, 8, True, "ambiguous",
            thinking="我看到一整排螢幕陳列，上方與下方都有完整螢幕，但無型號無價格，所以……這是一般單機。",
        )
        current = copy.deepcopy(weak)
        result = finalize_three_pass_outcome(current, [copy.deepcopy(weak), copy.deepcopy(weak)], unresolved())
        self.assertTrue(result["verified"])
        self.assertEqual(result["adjudication_rule"], "three_pass_wide_scene_structural_consensus")

    def test_local_model_omissions_still_vote_as_one_wide_scene(self):
        def local_model_omission():
            item = make_pass(
                "單機", None, None, 3, True, "matched", healthy=False
            )
            item["thinking"] = "我看到一整排螢幕陳列，但沒有可安全歸屬的型號與價格。"
            item["runtime_health"]["reasons"] = [
                "structured_authority_material_conflict:model"
            ]
            return item

        history = [local_model_omission(), local_model_omission()]
        current = local_model_omission()

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertFalse(result["unresolved"])
        self.assertEqual(current["view_type"], "遠景")
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])
        self.assertEqual(result["adjudication_rule"], "three_pass_wide_scene_structural_consensus")
        self.assertEqual(current["view_type"], "遠景")
        self.assertGreaterEqual(current["complete_screen_count"], 3)

    def test_structural_distant_veto_does_not_override_bound_single_identity(self):
        history = [
            make_pass("單機", "S32FM803UC", "12900", 1, True, "matched"),
            make_pass("遠景", None, None, 3, False, "not_visible"),
        ]
        current = make_pass("單機", "S32FM803UC", "12900", 1, True, "matched")

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(result["adjudication_rule"], "two_pass_single_view_consensus")
        self.assertEqual(current["view_type"], "單機")
        self.assertEqual(current["model"], "S32FM803UC")
        self.assertEqual(current["price"], "12900")

    def test_two_non_followme_identity_reads_override_generic_stand_hallucination(self):
        generic_fixture = [
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
            {"cue": "round_base", "same_subject": True, "strength": "strong"},
            {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
        ]
        first = make_pass(
            model="S32FM803UC",
            price="14990",
            count=1,
            physical=[],
            runtime_health={
                "healthy": False,
                "reasons": ["structured_narration_followme_conflict"],
            },
            thinking="中央一台完整螢幕，左右都被照片邊界裁切。",
        )
        second = make_pass(
            model=None,
            price="14990",
            count=1,
            physical=generic_fixture,
            thinking="中央一台完整螢幕，左右都被照片邊界裁切。",
        )
        current = make_pass(
            model="S32FM803UC",
            price="14990",
            count=2,
            physical=generic_fixture,
            thinking="中央一台完整螢幕，另一台被照片邊界裁切。",
        )

        result = finalize_three_pass_outcome(current, [first, second], unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(
            result["adjudication_rule"],
            "two_pass_non_followme_identity_consensus",
        )
        self.assertEqual(current["view_type"], "單機")
        self.assertEqual(current["complete_screen_count"], 1)
        self.assertEqual(current["model"], "S32FM803UC")
        self.assertEqual(current["price"], "14990")
        self.assertEqual(current["followme_physical_evidence"], [])
        self.assertFalse(current["followme_family_confirmed"])

    def test_three_single_subject_passes_finish_without_inventing_missing_model(self):
        first = make_pass(
            model=None,
            price="3300",
            count=2,
            thinking="中央只有一台完整螢幕，左右都被裁切。",
        )
        second = make_pass(
            model=None,
            price="3300",
            count=1,
            runtime_health={
                "healthy": False,
                "reasons": ["structured_narration_followme_conflict"],
            },
            thinking="中央只有一台完整螢幕，左右都被裁切。",
        )
        current = make_pass(
            model=None,
            price="3300",
            count=1,
            thinking="中央只有一台完整螢幕，左右都被裁切。",
        )

        result = finalize_three_pass_outcome(current, [first, second], unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(result["adjudication_rule"], "three_pass_single_subject_consensus")
        self.assertEqual(current["view_type"], "單機")
        self.assertEqual(current["complete_screen_count"], 1)
        self.assertIsNone(current["model"])
        self.assertEqual(current["price"], "3300")

    def test_one_structural_distant_and_two_wide_scene_votes_finish_distant(self):
        weak_single = make_pass(
            view="單機", model=None, price=None, count=5,
            unique=False, ownership="ambiguous",
            thinking="我看到一整排螢幕陳列，上下仍有完整螢幕，無法確認唯一主角。",
        )
        structural = make_pass(
            view="遠景", model=None, price=None, count=3,
            unique=False, ownership="not_visible",
            thinking="一排多台螢幕陳列，無唯一主角。",
        )
        current = make_pass(
            view="遠景", model=None, price=None, count=1,
            unique=False, ownership="ambiguous",
            thinking="一排多台螢幕陳列，無唯一主角。",
        )

        result = finalize_three_pass_outcome(
            current, [weak_single, structural], unresolved()
        )

        self.assertTrue(result["verified"])
        self.assertEqual(
            result["adjudication_rule"],
            "three_pass_mixed_wide_distant_consensus",
        )
        self.assertEqual(current["view_type"], "遠景")
        self.assertGreaterEqual(current["complete_screen_count"], 3)
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])

    def test_three_mislabeled_wide_single_calls_finish_as_structural_distant(self):
        history = [
            make_pass(
                view="單機", model=None, price=None, count=5,
                unique=False, ownership="ambiguous",
                thinking="我看到一整排、多層貨架螢幕陳列，無法鎖定唯一主角。",
            ),
            make_pass(
                view="單機", model=None, price=None, count=2,
                unique=True, ownership="matched",
                thinking="我看到一排螢幕陳列，中央兩台完整，左右被裁切。",
            ),
        ]
        current = make_pass(
            view="單機", model=None, price=None, count=4,
            unique=True, ownership="matched",
            thinking="我看到一排螢幕陳列，上方與下方另有完整螢幕，沒有 FollowMe 實體。",
        )

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(result["adjudication_rule"], "three_pass_wide_scene_structural_consensus")
        self.assertEqual(current["view_type"], "遠景")
        self.assertGreaterEqual(current["complete_screen_count"], 3)
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])

    def test_model_and_price_majorities_cannot_form_unsupported_chimera(self):
        history = [
            make_pass(model="S24A", price="100"),
            make_pass(model="S24A", price="200"),
        ]
        current = make_pass(model="S27B", price="200")

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])

    def test_followme_pair_disagreement_finalizes_family_without_guessing_variant(self):
        fixture = [
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
            {"cue": "round_base", "same_subject": True, "strength": "strong"},
        ]
        history = [
            make_pass(model='FollowMe M7 32"', price="12990", physical=fixture),
            make_pass(model='FollowMe M5 32"', price="11990", physical=fixture),
        ]
        current = make_pass(model='FollowMe M7 32"', price="12990", physical=fixture)

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(current["view_type"], "單機")
        self.assertTrue(current["followme_family_confirmed"])
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])

    def test_followme_local_narration_conflict_finishes_after_three_bound_calls(self):
        fixture = [
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
            {"cue": "round_base", "same_subject": True, "strength": "strong"},
            {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
        ]
        history = [
            make_pass(model='FollowMe Pro M7 43"', price="17990", physical=fixture),
            make_pass(model='FollowMe Pro M7 43"', price="15990", count=2, physical=fixture),
        ]
        current = make_pass(
            model='FollowMe Pro M7 43"', price="15990", count=2, physical=fixture,
            runtime_health={
                "healthy": False,
                "reasons": ["structured_narration_followme_conflict"],
            },
        )

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(result["adjudication_rule"], "two_pass_followme_physical_consensus")
        self.assertTrue(current["followme_family_confirmed"])
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])

    def test_followme_fixture_consensus_survives_variant_disagreement(self):
        fixture = [
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
            {"cue": "round_base", "same_subject": True, "strength": "strong"},
            {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
        ]
        history = [
            make_pass(model='FollowMe M7 32"', price="12990", physical=fixture),
            make_pass(model='FollowMe M5 32"', price="11990", physical=fixture),
        ]
        current = make_pass(model='FollowMe Pro M7 43"', price="17990", physical=fixture)

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(current["view_type"], "單機")
        self.assertTrue(current["followme_family_confirmed"])
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])
        self.assertEqual(result["adjudication_rule"], "two_pass_followme_physical_consensus")

    def test_two_followme_narrations_with_incomplete_background_set_count_one(self):
        fixture = [
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
            {"cue": "round_base", "same_subject": True, "strength": "strong"},
        ]
        history = [
            make_pass(
                model='FollowMe Pro M7 43"', price="17990", count=3,
                physical=fixture,
                thinking="前景一台 FollowMe，背景其他螢幕均未完整入鏡，所以……",
            ),
            make_pass(
                model='FollowMe Pro M7 43"', price="17990", count=4,
                physical=fixture,
                thinking="前景一台 FollowMe，背景另有三台完整螢幕，所以……",
            ),
        ]
        current = make_pass(
            model='FollowMe Pro M7 43"', price="17990", count=2,
            physical=fixture,
            thinking="全圖掃描確認無其他完整螢幕，所以……",
        )

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(result["adjudication_rule"], "two_pass_followme_physical_consensus")
        self.assertEqual(current["complete_screen_count"], 1)

    def test_any_technical_failure_requires_another_healthy_pass(self):
        history = [
            make_pass(healthy=False),
            make_pass(model="S24A", price="3990"),
        ]
        current = make_pass(model="S24A", price="3990")
        original = copy.deepcopy(current)

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["technical_retry_required"])
        self.assertTrue(result["unresolved"])
        self.assertFalse(result["verified"])
        self.assertEqual(current, original)

    def test_cross_photo_contamination_flag_cannot_be_voted_away(self):
        history = [
            make_pass(cross_photo_duplicate_core_suspected=True),
            make_pass(),
        ]
        current = make_pass()

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["technical_retry_required"])
        self.assertFalse(result["verified"])

    def test_mixed_noncanonical_view_cannot_vote(self):
        history = [
            make_pass("單機", None, None, 1, True, "matched"),
            make_pass("單機遠景", None, None, 3, False, "ambiguous"),
        ]
        current = make_pass("遠景", None, None, 3, False, "ambiguous")

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["technical_retry_required"])
        self.assertFalse(result["verified"])

    def test_different_image_hashes_never_vote_together(self):
        history = [make_pass(image_hash="a" * 64), make_pass(image_hash="b" * 64)]
        current = make_pass(image_hash="a" * 64)

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["technical_retry_required"])
        self.assertFalse(result["verified"])

    def test_pass_four_or_six_can_never_be_adjudicated(self):
        history = [
            make_pass("遠景", None, None, 3, False, "ambiguous"),
            make_pass("遠景", None, None, 4, False, "not_visible"),
        ]
        for attempt in (4, 6):
            with self.subTest(attempt=attempt):
                decision = unresolved()
                decision["attempt"] = attempt
                current = make_pass("遠景", None, None, 3, False, "ambiguous")

                result = finalize_three_pass_outcome(current, history, decision)

                self.assertFalse(result["verified"])
                self.assertTrue(result["technical_retry_required"])
                self.assertIn("three_call_hard_limit_reached", result["reasons"])

    def test_original_model_self_talk_is_preserved_for_audit(self):
        history = [
            make_pass("遠景", None, None, 3, False, "ambiguous"),
            make_pass("遠景", None, None, 4, False, "not_visible"),
        ]
        current = make_pass("單機", "S27X", "5990", 1, True, "matched")
        original_thinking = current["thinking"]

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(current["thinking"], original_thinking)
        self.assertIn("系統", "系統" + current["adjudication_summary"])
        self.assertIn("遠景", current["adjudication_summary"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

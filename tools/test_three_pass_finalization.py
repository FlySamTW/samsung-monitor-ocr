import copy
import sys
import unittest
from pathlib import Path

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

    def test_two_strong_followme_passes_finalize_single_without_guessing_variant(self):
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
        self.assertEqual(current["model"], 'FollowMe M7 32"')
        self.assertEqual(current["price"], "12990")

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

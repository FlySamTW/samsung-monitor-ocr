import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.audit_fields import (
    EVIDENCE_GUARD_REVISION,
    apply_human_audited_pixel_authority,
    finalize_three_pass_outcome,
    has_sufficient_followme_physical_evidence,
    immediate_retry_decision,
    validate_evidence_contract,
)


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
    def test_raw_single_model_price_consensus_recovers_sole_suggested_price(self):
        from tools.finalize_existing_three_pass_reviews import (
            _raw_single_model_price_consensus,
        )

        raw = json.dumps(
            {
                "narration": (
                    "我看到唯一主角自己的價牌，型號 S49DG952SC，"
                    "價牌只有建議售價 42,900，沒有另一個促銷價。"
                ),
                "view_type": "單機",
                "model": "S49DG952SC",
                "price": "42900",
                "complete_screen_count": 1,
                "unique_main": True,
                "label_ownership": "matched",
            },
            ensure_ascii=False,
        )
        calls = [
            make_pass(
                model="S49DG952SC",
                price=None,
                raw_objects=[raw],
                ocr_attempt=attempt,
            )
            for attempt in (1, 2, 3)
        ]

        self.assertEqual(
            _raw_single_model_price_consensus(calls),
            ("S49DG952SC", "42900"),
        )

    def test_raw_single_model_price_consensus_rejects_conflicting_third_pair(self):
        from tools.finalize_existing_three_pass_reviews import (
            _raw_single_model_price_consensus,
        )

        def raw(price):
            return json.dumps(
                {
                    "narration": f"我看到唯一主角型號 S32DM803UC，售價 {int(price):,}。",
                    "view_type": "單機",
                    "model": "S32DM803UC",
                    "price": str(price),
                    "complete_screen_count": 1,
                    "unique_main": True,
                    "label_ownership": "matched",
                },
                ensure_ascii=False,
            )

        calls = [
            make_pass(
                model="S32DM803UC",
                price=None,
                raw_objects=[raw(price)],
                ocr_attempt=attempt,
            )
            for attempt, price in enumerate((12990, 12990, 12900), start=1)
        ]

        self.assertIsNone(_raw_single_model_price_consensus(calls))

    def test_raw_single_model_price_consensus_rejects_unqualified_low_price(self):
        from tools.finalize_existing_three_pass_reviews import (
            _raw_single_model_price_consensus,
        )

        raw = json.dumps(
            {
                "narration": "我看到唯一主角型號 S32DM702UC，售價 1,990。",
                "view_type": "單機",
                "model": "S32DM702UC",
                "price": "1990",
                "complete_screen_count": 1,
                "unique_main": True,
                "label_ownership": "matched",
            },
            ensure_ascii=False,
        )
        calls = [
            make_pass(
                model="S32DM702UC",
                price=None,
                raw_objects=[raw],
                ocr_attempt=attempt,
            )
            for attempt in (1, 2, 3)
        ]

        self.assertIsNone(_raw_single_model_price_consensus(calls))

    def test_full_official_sku_consensus_recovers_catalog_short_model(self):
        from tools.finalize_existing_three_pass_reviews import (
            _recover_full_official_sku_consensus,
        )

        raw = json.dumps(
            {
                "view_type": "單機",
                "model": "LS32DG802SCXZW",
                "price": "32900",
                "complete_screen_count": 1,
                "unique_main": True,
                "label_ownership": "matched",
            },
            ensure_ascii=False,
        )
        calls = [
            make_pass(
                model=None,
                price="32900",
                raw_objects=[raw],
                normalized_evidence={
                    "complete_screen_count": 1,
                    "unique_main": True,
                    "label_ownership": "matched",
                    "followme_physical_evidence": [],
                },
            )
            for _ in range(3)
        ]

        recovered = _recover_full_official_sku_consensus(calls)

        self.assertEqual(recovered, "S32DG802SC")
        self.assertEqual([call["model"] for call in calls], ["S32DG802SC"] * 3)
        self.assertEqual([call["quality_issue"] for call in calls], ["無"] * 3)

    def test_full_official_sku_consensus_rejects_neighbor_owned_label(self):
        from tools.finalize_existing_three_pass_reviews import (
            _recover_full_official_sku_consensus,
        )

        raw = json.dumps(
            {
                "view_type": "單機",
                "model": "LS32DG802SCXZW",
                "price": "32900",
                "complete_screen_count": 1,
                "unique_main": True,
                "label_ownership": "ambiguous",
            },
            ensure_ascii=False,
        )
        calls = [
            make_pass(
                model=None,
                price="32900",
                raw_objects=[raw],
                normalized_evidence={
                    "complete_screen_count": 1,
                    "unique_main": True,
                    "label_ownership": "matched",
                    "followme_physical_evidence": [],
                },
            )
            for _ in range(3)
        ]

        self.assertIsNone(_recover_full_official_sku_consensus(calls))
        self.assertEqual([call["model"] for call in calls], [None] * 3)

    def test_three_matching_followme_passes_clear_first_duplicate_warning(self):
        fixture = [
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
            {"cue": "round_base", "same_subject": True, "strength": "strong"},
            {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
        ]
        narration = (
            "我看到一台完整主角，具有白色垂直支架、圓形底座與附著託盤，"
            "同一台螢幕附著的規格牌寫著 FOLLOW ME PRO，"
            "型號與價格牌都歸屬同一台，所以……"
        )
        history = [
            make_pass(
                model='FollowMe Pro M7 43"',
                price="17990",
                physical=fixture,
                cross_photo_duplicate_core_suspected=True,
                thinking=narration,
            ),
            make_pass(
                model='FollowMe Pro M7 43"',
                price="17990",
                physical=fixture,
                thinking=narration,
            ),
        ]
        current = make_pass(
            model='FollowMe Pro M7 43"',
            price="17990",
            physical=fixture,
            thinking=narration,
        )

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertFalse(result["unresolved"])
        self.assertEqual(
            result["adjudication_rule"],
            "three_pass_cross_photo_suspicion_cleared",
        )
        self.assertEqual(current["model"], 'FollowMe Pro M7 43"')
        self.assertEqual(current["price"], "17990")

    def test_followme_physical_consensus_keeps_truthful_family_without_price(self):
        fixture = [
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
            {"cue": "round_base", "same_subject": True, "strength": "strong"},
            {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
        ]
        narration = (
            "我看到同一台完整螢幕有白色垂直支架、圓形底座與附著託盤，"
            "畫面顯示 Smart Monitor M7，但沒有可讀實體價牌，所以……"
        )
        history = [
            make_pass("單機", None, None, 1, True, "not_visible", physical=fixture, thinking=narration),
            make_pass("單機", None, None, 1, True, "not_visible", physical=fixture, thinking=narration),
        ]
        current = make_pass(
            "單機",
            'FollowMe M7 32"',
            None,
            1,
            True,
            "not_visible",
            physical=fixture + [
                {
                    "cue": "direct_followme_branding_on_unit",
                    "same_subject": True,
                    "strength": "direct",
                }
            ],
            thinking=narration,
        )

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(result["adjudication_rule"], "two_pass_followme_physical_consensus")
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])
        self.assertTrue(current["followme_family_confirmed"])
        valid, errors, _ = validate_evidence_contract(current)
        self.assertTrue(valid, errors)

    def test_live_guard_accepts_third_clean_duplicate_confirmation(self):
        fixture = [
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
            {"cue": "round_base", "same_subject": True, "strength": "strong"},
            {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
        ]
        narration = (
            "我看到一台完整主角，具有白色垂直支架、圓形底座與附著託盤，"
            "同一台螢幕附著的規格牌寫著 FOLLOW ME PRO，"
            "型號與價格牌都歸屬同一台，所以……"
        )
        history = [
            make_pass(
                model='FollowMe Pro M7 43"',
                price="17990",
                physical=fixture,
                cross_photo_duplicate_core_suspected=True,
                thinking=narration,
            ),
            make_pass(
                model='FollowMe Pro M7 43"',
                price="17990",
                physical=fixture,
                thinking=narration,
            ),
        ]
        current = make_pass(
            model='FollowMe Pro M7 43"',
            price="17990",
            physical=fixture,
            thinking=narration,
            ocr_attempt=3,
            period="202601",
        )

        decision = immediate_retry_decision(current, 3, history, 3)

        self.assertTrue(decision["verified"])
        self.assertFalse(decision["retry"])
        self.assertFalse(decision["unresolved"])
        self.assertTrue(current["cross_photo_duplicate_core_cleared_by_three_pass"])

    def test_explicit_three_complete_screens_veto_conflicting_single_votes(self):
        history = [
            make_pass(
                "單機",
                None,
                "12900",
                3,
                True,
                "matched",
                healthy=False,
                runtime_health={
                    "healthy": False,
                    "reasons": ["structured_authority_material_conflict:model"],
                },
                thinking=(
                    "我看到三台螢幕完整入鏡，分別位於上方與下方，"
                    "每台都有自己的價牌，所以……"
                ),
            ),
            make_pass(
                "單機",
                "S32DM703UC",
                "12990",
                3,
                True,
                "matched",
                thinking=(
                    "我看到三台螢幕完整入鏡，分別位於上方與下方，"
                    "畫面中有多張不同價牌，所以……"
                ),
            ),
        ]
        current = make_pass(
            "遠景",
            None,
            None,
            3,
            False,
            "ambiguous",
            thinking=(
                "我看到三台螢幕完整入鏡，沒有可唯一歸屬同一主角的型號與價格，"
                "所以……"
            ),
        )

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertFalse(result["unresolved"])
        self.assertEqual(current["view_type"], "遠景")
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])
        self.assertEqual(
            result["adjudication_rule"],
            "distant_structural_veto_over_wide_geometry_single_votes",
        )

    def test_two_bound_identity_votes_discard_unbound_third_without_fourth_call(self):
        history = [
            make_pass("單機", "S32CG552EC", "6990", 2, True, "matched"),
            make_pass("單機", "S32CG552EC", "6990", 1, True, "matched"),
        ]
        current = make_pass(
            "單機",
            "WRONG",
            "99999",
            1,
            True,
            "matched",
            healthy=False,
            request_id_verified=False,
            runtime_health={
                "healthy": False,
                "allow_processing": True,
                "allow_upload": False,
                "reasons": ["request_binding_unverified"],
            },
        )

        result = finalize_three_pass_outcome(
            current,
            history,
            {
                "attempt": 3,
                "retry": False,
                "unresolved": True,
                "verified": False,
                "technical_retry_required": True,
                "reasons": ["request_binding_unverified"],
            },
        )

        self.assertTrue(result["verified"])
        self.assertFalse(result["unresolved"])
        self.assertEqual(
            result["adjudication_rule"],
            "two_bound_pass_consensus_discarded_unbound_third",
        )
        self.assertEqual(current["model"], "S32CG552EC")
        self.assertEqual(current["price"], "6990")
        self.assertTrue(current["request_id_verified"])
        self.assertTrue(current["runtime_health"]["healthy"])
        self.assertEqual(
            current["discarded_unbound_call"]["reasons"],
            ["request_binding_unverified"],
        )

    def test_unbound_third_cannot_settle_disagreeing_bound_identity_votes(self):
        history = [
            make_pass("單機", "S32CG552EC", "6990", 1, True, "matched"),
            make_pass("單機", "S27CG552EC", "4990", 1, True, "matched"),
        ]
        current = make_pass(
            "單機",
            "S32CG552EC",
            "6990",
            1,
            True,
            "matched",
            healthy=False,
            request_id_verified=False,
            runtime_health={
                "healthy": False,
                "allow_processing": True,
                "allow_upload": False,
                "reasons": ["request_binding_unverified"],
            },
        )

        result = finalize_three_pass_outcome(
            current,
            history,
            {
                "attempt": 3,
                "retry": False,
                "unresolved": True,
                "verified": False,
                "technical_retry_required": True,
                "reasons": ["request_binding_unverified"],
            },
        )

        self.assertFalse(result["verified"])
        self.assertTrue(result["unresolved"])
        self.assertEqual(
            result["technical_retry_reason"],
            "three_pass_current_integrity_invalid",
        )

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

    def test_wide_scene_vetoes_two_single_votes_with_conflicting_models(self):
        history = [
            make_pass(
                "單機", "S32DM703UC", "12990", 3, True, "matched",
                thinking="前景一台，背景上方與遠處還有數台完整螢幕。",
            ),
            make_pass(
                "遠景", None, None, 3, False, "not_visible",
                thinking="多台完整螢幕，無法鎖定唯一主角及其規格價格。",
            ),
        ]
        current = make_pass(
            "單機", "S27CG552EC", "12990", 3, True, "matched",
            thinking="上方與下方各有完整螢幕，但主角型號需從價牌讀取。",
        )

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(
            result["adjudication_rule"],
            "wide_scene_identity_conflict_distant_veto",
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

    def test_prior_revision_known_pixel_row_is_repaired_without_fourth_call(self):
        from tools.finalize_existing_three_pass_reviews import finalize_file

        image_hash = "4b069632c9af4da183fa5ff7e1ec616331f59ede149b7d9ea27b571be19213c5"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            trace = root / "trace.jsonl"
            result_file = root / "result.json"
            rows = []
            for attempt in (1, 2, 3):
                parsed = make_pass(
                    "單機",
                    "FollowMe Pro M7 43\"",
                    "17990",
                    1,
                    True,
                    "matched",
                    [
                        {"cue": "round_base", "same_subject": True, "strength": "strong"},
                        {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
                    ],
                    image_hash=image_hash,
                )
                parsed.update({
                    "file_name": "sample-318.jpg",
                    "source_item_id": "known-source",
                    "period": "202601",
                    "ocr_attempt": attempt,
                    "timestamp": f"2026-07-17T06:00:0{attempt}",
                    "run_id": "old-run",
                })
                rows.append({
                    "file_name": "sample-318.jpg",
                    "source_item_id": "known-source",
                    "run_id": "old-run",
                    "timestamp": parsed["timestamp"],
                    "parsed_output": parsed,
                })
            trace.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                encoding="utf-8",
            )
            result_file.write_text(
                json.dumps([{
                    "data": {
                        "image": str(root / "sample-318.jpg"),
                        "ocr_meta": {
                            "auto_verified": True,
                            "auto_review_required": False,
                            "evidence_guard_revision": "20260717.34",
                            "view_type": "單機",
                            "model": "FollowMe Pro M7 43\"",
                            "price": "17990",
                        },
                    },
                    "annotations": [{
                        "result": [
                            {
                                "from_name": "category",
                                "value": {"choices": ["遠景"]},
                            },
                            {
                                "from_name": "model",
                                "value": {"text": ["null"]},
                            },
                            {
                                "from_name": "price",
                                "value": {"text": ["null"]},
                            },
                        ],
                    }],
                }], ensure_ascii=False),
                encoding="utf-8",
            )

            with patch(
                "tools.finalize_existing_three_pass_reviews.enqueue_finalized_result",
                return_value=root / "queued.json",
            ):
                report = finalize_file(
                    result_file,
                    trace,
                    root,
                    apply=True,
                )

            saved_task = json.loads(result_file.read_text(encoding="utf-8"))[0]
            saved = saved_task["data"]["ocr_meta"]
            saved_annotation = {
                field["from_name"]: field["value"]
                for field in saved_task["annotations"][0]["result"]
            }
            presentation_files = list(
                (root / "_ocr_audit" / "presentation_history").glob(
                    "presentation_finalization_*.jsonl"
                )
            )
            presentation_event = json.loads(
                presentation_files[0].read_text(encoding="utf-8").splitlines()[0]
            )

        self.assertEqual(report[0]["status"], "finalized")
        self.assertEqual(saved["evidence_guard_revision"], EVIDENCE_GUARD_REVISION)
        self.assertEqual(saved["complete_screen_count"], 3)
        self.assertEqual(
            saved["followme_physical_evidence"],
            [{
                "cue": "direct_followme_branding_on_unit",
                "same_subject": True,
                "strength": "strong",
            }],
        )
        self.assertEqual(saved_annotation["category"]["choices"], ["單機"])
        self.assertEqual(saved_annotation["model"]["text"], ['FollowMe Pro M7 43"'])
        self.assertEqual(saved_annotation["price"]["text"], ["17990"])
        self.assertEqual(presentation_event["source_item_id"], "known-source")
        self.assertEqual(presentation_event["pass_index"], 3)
        self.assertEqual(presentation_event["decision"], "accepted")
        self.assertTrue(presentation_event["result"]["auto_verified"])

    def test_verified_three_pass_stale_upload_blocker_is_requeued(self):
        from tools.finalize_existing_three_pass_reviews import finalize_file

        with TemporaryDirectory() as temp:
            root = Path(temp)
            trace = root / "trace.jsonl"
            result_file = root / "result.json"
            rows = []
            for attempt in (1, 2, 3):
                parsed = make_pass(
                    model=None,
                    price=None,
                    count=1,
                    unique=True,
                    ownership="matched",
                    model_validation_failed=attempt == 3,
                    unlisted_model_candidate=attempt == 3,
                    official_model_unverified=attempt == 3,
                )
                parsed.update({
                    "file_name": "stale-upload-blocker.jpg",
                    "source_item_id": "stale-source",
                    "period": "202601",
                    "ocr_attempt": attempt,
                    "timestamp": f"2026-07-21T03:00:0{attempt}",
                    "run_id": "bounded-run",
                })
                if attempt == 3:
                    parsed["three_pass_adjudicated"] = True
                    parsed["adjudication_rule"] = "three_pass_single_subject_consensus"
                rows.append({
                    "file_name": parsed["file_name"],
                    "source_item_id": parsed["source_item_id"],
                    "run_id": parsed["run_id"],
                    "timestamp": parsed["timestamp"],
                    "parsed_output": parsed,
                })
            trace.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                encoding="utf-8",
            )
            result_file.write_text(
                json.dumps([{
                    "data": {
                        "image": str(root / "stale-upload-blocker.jpg"),
                        "ocr_meta": {
                            "auto_verified": True,
                            "auto_review_required": False,
                            "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
                            "ocr_attempt": 3,
                            "view_type": "?格?",
                            "model": None,
                            "price": None,
                            "model_validation_failed": True,
                        },
                    },
                    "annotations": [{"result": []}],
                }], ensure_ascii=False),
                encoding="utf-8",
            )

            with patch(
                "tools.finalize_existing_three_pass_reviews.enqueue_finalized_result",
                return_value=root / "queued.json",
            ) as enqueue:
                report = finalize_file(
                    result_file,
                    trace,
                    root,
                    apply=True,
                    only_file_names={"stale-upload-blocker.jpg"},
                )

            saved = json.loads(result_file.read_text(encoding="utf-8"))[0]
            meta = saved["data"]["ocr_meta"]
            queued = enqueue.call_args.args[0]

        self.assertEqual(report[0]["status"], "finalized")
        self.assertFalse(meta["model_validation_failed"])
        self.assertFalse(meta["unlisted_model_candidate"])
        self.assertFalse(meta["official_model_unverified"])
        self.assertFalse(queued["model_validation_failed"])
        self.assertIsNone(queued["model"])
        self.assertIsNone(queued["price"])

    def test_prior_revision_raw_price_loss_is_repaired_and_requeued(self):
        from tools.finalize_existing_three_pass_reviews import finalize_file

        raw = json.dumps(
            {
                "narration": (
                    "我看到唯一主角自己的價牌，型號 S49DG952SC，"
                    "價牌只有建議售價 42,900，沒有另一個促銷價。"
                ),
                "view_type": "單機",
                "model": "S49DG952SC",
                "price": "42900",
                "complete_screen_count": 1,
                "unique_main": True,
                "label_ownership": "matched",
            },
            ensure_ascii=False,
        )
        with TemporaryDirectory() as temp:
            root = Path(temp)
            trace = root / "trace.jsonl"
            result_file = root / "result.json"
            source = root / "price-loss.jpg"
            source.write_bytes(b"exact-photo-bytes")
            source_id = hashlib.sha256(
                str(source.resolve()).casefold().encode("utf-8")
            ).hexdigest()
            rows = []
            for attempt in (1, 2, 3):
                parsed = make_pass(
                    model="S49DG952SC",
                    price=None,
                    count=1,
                    unique=True,
                    ownership="matched",
                    raw_objects=[raw],
                    ocr_attempt=attempt,
                    file_name="price-loss.jpg",
                    source_item_id=source_id,
                    source_path=str(source),
                    original_source_path=str(source),
                    period="202601",
                    timestamp=f"2026-07-21T04:00:0{attempt}",
                    run_id="revision-56-run",
                )
                rows.append({
                    "file_name": parsed["file_name"],
                    "source_item_id": parsed["source_item_id"],
                    "run_id": parsed["run_id"],
                    "timestamp": parsed["timestamp"],
                    "parsed_output": parsed,
                })
            trace.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                encoding="utf-8",
            )
            result_file.write_text(
                json.dumps([{
                    "data": {
                        "image": str(root / "price-loss.jpg"),
                        "ocr_meta": {
                            "auto_verified": True,
                            "auto_review_required": False,
                            "evidence_guard_revision": "20260721.56",
                            "ocr_attempt": 1,
                            "view_type": "單機",
                            "model": "S49DG952SC",
                            "price": None,
                        },
                    },
                    "annotations": [{"result": []}],
                }], ensure_ascii=False),
                encoding="utf-8",
            )

            with patch(
                "tools.finalize_existing_three_pass_reviews.enqueue_finalized_result",
                return_value=root / "queued.json",
            ) as enqueue:
                report = finalize_file(
                    result_file,
                    trace,
                    root,
                    apply=True,
                )

            saved = json.loads(result_file.read_text(encoding="utf-8"))[0]
            meta = saved["data"]["ocr_meta"]
            queued = enqueue.call_args.args[0]

        self.assertEqual(report[0]["status"], "finalized")
        self.assertEqual(
            meta["adjudication_rule"],
            "three_pass_raw_model_price_consensus_repair",
        )
        self.assertEqual(meta["evidence_guard_revision"], EVIDENCE_GUARD_REVISION)
        self.assertEqual(meta["model"], "S49DG952SC")
        self.assertEqual(meta["price"], "42900")
        self.assertEqual(queued["model"], "S49DG952SC")
        self.assertEqual(queued["price"], "42900")

    def test_pass_one_task_cannot_erase_a_bound_three_call_authority(self):
        from tools.finalize_existing_three_pass_reviews import finalize_file

        image_hash = "4b069632c9af4da183fa5ff7e1ec616331f59ede149b7d9ea27b571be19213c5"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            trace = root / "trace.jsonl"
            result_file = root / "result.json"
            rows = []
            for attempt in (1, 2, 3):
                parsed = make_pass(
                    "單機",
                    "FollowMe Pro M7 43\"",
                    "17990",
                    1,
                    True,
                    "matched",
                    [
                        {"cue": "round_base", "same_subject": True, "strength": "strong"},
                        {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
                    ],
                    image_hash=image_hash,
                )
                parsed.update({
                    "file_name": "pass-one-overwrite.jpg",
                    "source_item_id": "known-source",
                    "period": "202601",
                    "ocr_attempt": attempt,
                    "timestamp": f"2026-07-17T08:00:0{attempt}",
                    "run_id": "capped-run",
                })
                rows.append({
                    "file_name": "pass-one-overwrite.jpg",
                    "source_item_id": "known-source",
                    "run_id": "capped-run",
                    "timestamp": parsed["timestamp"],
                    "parsed_output": parsed,
                })
            trace.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                encoding="utf-8",
            )
            result_file.write_text(
                json.dumps([{
                    "data": {
                        "image": str(root / "pass-one-overwrite.jpg"),
                        "ocr_meta": {
                            "auto_verified": True,
                            "auto_review_required": False,
                            "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
                            "adjudication_rule": "wrong_pass_one_result",
                            "ocr_attempt": 1,
                            "view_type": "單機",
                            "complete_screen_count": 1,
                            "model": "FollowMe Pro M7 43\"",
                            "price": "17990",
                        },
                    },
                    "annotations": [{"result": []}],
                }], ensure_ascii=False),
                encoding="utf-8",
            )

            before_dry_run = result_file.read_text(encoding="utf-8")
            with patch(
                "tools.finalize_existing_three_pass_reviews.enqueue_finalized_result"
            ) as dry_enqueue:
                dry_report = finalize_file(
                    result_file,
                    trace,
                    root,
                    apply=False,
                    only_file_names={"pass-one-overwrite.jpg"},
                )
            self.assertEqual(dry_report[0]["status"], "would_finalize")
            self.assertEqual(
                dry_report[0]["rule"],
                "three_call_known_pixel_authority_repair",
            )
            self.assertEqual(
                result_file.read_text(encoding="utf-8"), before_dry_run
            )
            dry_enqueue.assert_not_called()

            with patch(
                "tools.finalize_existing_three_pass_reviews.enqueue_finalized_result",
                return_value=root / "queued.json",
            ):
                report = finalize_file(
                    result_file,
                    trace,
                    root,
                    apply=True,
                    only_file_names={"pass-one-overwrite.jpg"},
                )
            saved = json.loads(result_file.read_text(encoding="utf-8"))[0]
            meta = saved["data"]["ocr_meta"]

        self.assertEqual(report[0]["rule"], "three_call_known_pixel_authority_repair")
        self.assertEqual(meta["ocr_attempt"], 3)
        self.assertEqual(meta["complete_screen_count"], 3)
        self.assertEqual(meta["screen_status"], "正常")
        self.assertEqual(meta["quality_issue"], "無")
        self.assertEqual(
            meta["followme_physical_evidence"],
            [{
                "cue": "direct_followme_branding_on_unit",
                "same_subject": True,
                "strength": "strong",
            }],
        )

    def test_targeted_finalizer_never_changes_an_unselected_task(self):
        from tools.finalize_existing_three_pass_reviews import finalize_file

        with TemporaryDirectory() as temp:
            root = Path(temp)
            trace = root / "trace.jsonl"
            trace.write_text("", encoding="utf-8")
            result_file = root / "result.json"
            original = [{
                "data": {
                    "image": str(root / "do-not-touch.jpg"),
                    "ocr_meta": {
                        "auto_verified": False,
                        "auto_review_required": True,
                        "view_type": "單機",
                    },
                }
            }]
            result_file.write_text(
                json.dumps(original, ensure_ascii=False),
                encoding="utf-8",
            )

            report = finalize_file(
                result_file,
                trace,
                root,
                apply=True,
                only_file_names={"selected.jpg"},
            )
            saved = json.loads(result_file.read_text(encoding="utf-8"))

        self.assertEqual(report, [])
        self.assertEqual(saved, original)

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

    def test_archived_photo_local_fuse_restores_consumed_second_call(self):
        from tools.finalize_existing_three_pass_reviews import _load_three_call_groups

        with TemporaryDirectory() as temp_dir:
            audit = Path(temp_dir)
            trace = audit / "trace.jsonl"
            history = audit / "runtime_health_fuse_history"
            clearance = audit / "runtime_health_fuse_clearance"
            history.mkdir()
            clearance.mkdir()
            source_id = "b" * 64
            image_hash = "c" * 64
            run_id = "run-wide"
            name = "wide-1333.jpg"

            def trace_row(attempt, view, count, unique, ownership, narration):
                row_run_id = "resume-run" if attempt == 3 else run_id
                parsed = make_pass(
                    view=view,
                    model=None,
                    price="2988" if attempt == 1 else None,
                    count=count,
                    unique=unique,
                    ownership=ownership,
                    image_hash=image_hash,
                    thinking=narration,
                    file_name=name,
                    source_item_id=source_id,
                    source_path=str(audit / name),
                    original_source_path=str(audit / "original" / name),
                    period="202606",
                    run_id=row_run_id,
                    ocr_attempt=attempt,
                    timestamp=f"2026-07-18T00:00:0{attempt}",
                )
                return {
                    "file_name": name,
                    "run_id": row_run_id,
                    "timestamp": parsed["timestamp"],
                    "source_item_id": source_id,
                    "parsed_output": parsed,
                }

            trace.write_text(
                "\n".join(
                    json.dumps(row, ensure_ascii=False)
                    for row in (
                        trace_row(
                            1,
                            "單機",
                            7,
                            True,
                            "matched",
                            "我看到上方與中間層一整排螢幕陳列，共七台完整入鏡。",
                        ),
                        trace_row(
                            3,
                            "遠景",
                            6,
                            False,
                            "not_visible",
                            "我看到一整排螢幕陳列，上方與下方都有多台完整螢幕。",
                        ),
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            request_id = "d" * 32
            archive = history / "fuse.json"
            archive.write_text(
                json.dumps(
                    {
                        "source_file": name,
                        "attempt": 2,
                        "run_id": run_id,
                        "tripped_at": "2026-07-18T00:00:02",
                        "reasons": [
                            "structured_authority_material_conflict:model"
                        ],
                        "record_snapshot": {
                            "view_type": "單機",
                            "category": "單機",
                            "model": None,
                            "price": None,
                            "complete_screen_count": 7,
                            "unique_main": True,
                            "label_ownership": "not_visible",
                            "followme_physical_evidence": [],
                            "structured_authority_blocked_fields": ["model"],
                            "narration": (
                                "我看到一整排螢幕陳列，上方與中間層各有多台螢幕。"
                            ),
                            "raw_model_output": json.dumps(
                                {
                                    "request_id": request_id,
                                    "screen_status": "正常",
                                    "quality_issue": "無",
                                },
                                ensure_ascii=False,
                            ),
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (clearance / "receipt.json").write_text(
                json.dumps(
                    {
                        "schema": "samsung-ocr-runtime-fuse-clearance/v1",
                        "recovery": (
                            "persist_fused_bound_pass_as_photo_local_history_then_resume_call_3"
                        ),
                        "source_file": name,
                        "source_item_id": source_id,
                        "input_image_sha256": image_hash,
                        "recovered_request_id": request_id,
                        "archived_fuse": str(archive),
                        "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            calls = _load_three_call_groups(trace)[name]

            self.assertEqual(
                [int(item["ocr_attempt"]) for item in calls], [1, 2, 3]
            )
            self.assertTrue(calls[1]["recovered_from_archived_photo_local_fuse"])
            outcome = finalize_three_pass_outcome(
                calls[-1], calls[:-1], unresolved()
            )
            self.assertTrue(outcome["verified"])
            self.assertEqual(calls[-1]["view_type"], "遠景")
            self.assertEqual(
                outcome["adjudication_rule"],
                "distant_structural_veto_over_wide_geometry_single_votes",
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

    def test_three_zero_screen_calls_finalize_even_when_view_labels_disagree(self):
        history = [
            make_pass(
                "單機",
                None,
                None,
                0,
                False,
                "not_visible",
                thinking="我看到牆面宣傳海報，沒有任何完整入鏡螢幕，所以……",
            ),
            make_pass(
                "遠景",
                None,
                None,
                0,
                False,
                "not_visible",
                thinking="我看到商品展示牆，沒有任何完整入鏡螢幕，所以……",
            ),
        ]
        current = make_pass(
            "單機",
            None,
            None,
            0,
            False,
            "not_visible",
            thinking="我看到背景商品陳列，中央沒有完整入鏡螢幕，所以……",
        )

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(
            result["adjudication_rule"],
            "three_pass_zero_screen_scene_consensus",
        )
        self.assertEqual(current["view_type"], "遠景")
        self.assertEqual(current["complete_screen_count"], 0)
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])

    def test_zero_and_multiscreen_distant_votes_share_scene_consensus(self):
        history = [
            make_pass(
                "遠景",
                None,
                None,
                0,
                False,
                "not_visible",
                thinking="我看到沒有任何完整入鏡螢幕，所以……",
            ),
            make_pass(
                "遠景",
                None,
                None,
                10,
                False,
                "not_visible",
                thinking="我看到一整排十台完整螢幕，無法鎖定唯一主角，所以……",
            ),
        ]
        current = make_pass(
            "單機",
            None,
            None,
            3,
            True,
            "matched",
            thinking="我看到一整排螢幕陳列，但沒有可歸屬的型號或價格，所以……",
        )

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(
            result["adjudication_rule"],
            "two_pass_distant_scene_consensus",
        )
        self.assertEqual(current["view_type"], "遠景")
        self.assertGreaterEqual(current["complete_screen_count"], 3)
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])

    def test_two_identity_free_wide_votes_veto_one_nearby_card_outlier(self):
        wide_narration = (
            "我看到一排螢幕陳列在賣場，完整入鏡螢幕共三台，"
            "無法鎖定唯一主角及其規格與價格，所以……"
        )
        history = [
            make_pass(
                "單機",
                None,
                None,
                3,
                False,
                "not_visible",
                thinking=wide_narration,
            ),
            make_pass(
                "單機",
                "S27CG552EC",
                "2390",
                1,
                True,
                "matched",
                thinking=(
                    "我看到中央一台與附近價牌，但左右仍是一排螢幕，"
                    "沒有 FollowMe 實體，所以……"
                ),
            ),
        ]
        current = make_pass(
            "單機",
            None,
            None,
            3,
            False,
            "not_visible",
            thinking=wide_narration,
        )

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(
            result["adjudication_rule"],
            "two_wide_geometry_votes_veto_single_identity_outlier",
        )
        self.assertEqual(current["view_type"], "遠景")
        self.assertEqual(current["complete_screen_count"], 3)
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])

    def test_one_true_wide_vote_cannot_veto_two_single_unique_main_votes(self):
        mistaken_wide = (
            "我看到三台完整入鏡的螢幕，無法鎖定唯一主角及其規格與價格，所以……"
        )
        owned_single = (
            "我看到中央主螢幕與正下方空間對齊的價牌，型號 S27CG552EC，"
            "價格 4,990 元；沒有 FollowMe 實體，所以……"
        )
        history = [
            make_pass(
                "單機",
                None,
                None,
                3,
                True,
                "not_visible",
                thinking=mistaken_wide,
            ),
            make_pass(
                "遠景",
                None,
                None,
                3,
                False,
                "not_visible",
                thinking=mistaken_wide,
            ),
        ]
        current = make_pass(
            "單機",
            "S27CG552EC",
            "4990",
            3,
            True,
            "matched",
            thinking=owned_single,
        )

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertNotEqual(
            result.get("adjudication_rule"),
            "two_wide_geometry_votes_veto_single_identity_outlier",
        )
        self.assertEqual(current["view_type"], "單機")

    def test_two_edge_cut_reads_preserve_owned_non_followme_identity(self):
        edge_cut = (
            "我看到中央螢幕，左側螢幕左外框被照片邊界裁切，"
            "右側螢幕右外框被照片邊界裁切，中央價牌歸屬清楚，所以……"
        )
        history = [
            make_pass(
                "單機",
                "S27D300GAC",
                None,
                3,
                True,
                "matched",
                thinking=edge_cut,
            ),
            make_pass(
                "單機",
                "S27D300GAC",
                "3290",
                1,
                True,
                "matched",
                thinking=edge_cut,
            ),
        ]
        current = make_pass(
            "單機",
            "S27D300GAC",
            "3290",
            3,
            False,
            "ambiguous",
            thinking=(
                "我看到三台螢幕並排，中央價牌寫 S27D300GAC 與 3290，"
                "沒有 FollowMe 實體，所以……"
            ),
        )

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(
            result["adjudication_rule"],
            "two_pass_edge_cut_identity_consensus",
        )
        self.assertEqual(current["view_type"], "單機")
        self.assertEqual(current["complete_screen_count"], 1)
        self.assertEqual(current["model"], "S27D300GAC")
        # The third pass marks ownership ambiguous, so only one safe price vote
        # remains.  Keep the model consensus but do not attach an unsafe price.
        self.assertIsNone(current["price"])

    def test_two_narrated_fixture_passes_override_structured_wide_distant_votes(self):
        second_narration = (
            "我看到中央偏左有白色立柱與圓形底座，但沒有螢幕連接，"
            "沒有可讀型號與價格，所以……"
        )
        current_narration = (
            "我看到中央偏左有一台螢幕，其正下方有白色直立支架與圓形底座，"
            "但沒有可讀的型號或價格牌，所以……"
        )
        first = make_pass(
            "遠景", None, None, 3, False, "not_visible",
            thinking="我看到寬廣賣場中三台完整螢幕，沒有實體 FollowMe，所以……",
        )
        second = make_pass(
            "遠景", None, None, 0, False, "not_visible",
            thinking="AI 判讀文字已由健康閘收回；這張照片必須重新獨立判讀。",
            narration=second_narration,
        )
        current = make_pass(
            "遠景", None, None, 3, False, "not_visible",
            thinking="我看到三輪獨立判讀已完成交叉核對，最後定案為遠景，所以……",
            narration=current_narration,
        )

        result = finalize_three_pass_outcome(current, [first, second], unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(
            result["adjudication_rule"],
            "two_pass_narrated_followme_fixture_consensus",
        )
        self.assertEqual(current["view_type"], "單機")
        self.assertEqual(current["complete_screen_count"], 3)
        self.assertTrue(current["followme_family_confirmed"])
        self.assertTrue(has_sufficient_followme_physical_evidence(current))
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])

    def test_structured_followme_consensus_precedes_narration_only_fallback(self):
        physical = [
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
            {"cue": "round_base", "same_subject": True, "strength": "strong"},
            {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
            {"cue": "attached_followme_product_card", "same_subject": True, "strength": "strong"},
        ]
        narration = (
            "我看到唯一主角螢幕正下方連著白色直立支架與完整圓形底座，"
            "附著價牌可讀 FollowMe Pro M7 43 吋與 17,990，所以……"
        )
        history = [
            make_pass(
                "單機", 'FollowMe Pro M7 43"', "17990", 3, True, "matched",
                physical, thinking=narration, narration=narration,
            ),
            make_pass(
                "單機", 'FollowMe Pro M7 43"', "17990", 3, True, "matched",
                physical, thinking=narration, narration=narration,
            ),
        ]
        current = make_pass(
            "單機", "FollowMe 型號未細分", "17990", 3, True, "matched",
            physical, thinking=narration, narration=narration,
        )

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(result["adjudication_rule"], "two_pass_followme_physical_consensus")
        self.assertEqual(current["model"], 'FollowMe Pro M7 43"')
        self.assertEqual(current["price"], "17990")
        self.assertTrue(current["followme_family_confirmed"])

    def test_one_detailed_edge_cut_read_and_three_clean_pair_votes_finish_single(self):
        detailed_edge_cut = (
            "我看到中央一台螢幕四邊四角完整，右側螢幕被照片邊界截斷，"
            "左側螢幕外框左邊被截斷，全圖其他區域沒有額外完整螢幕。"
            "中央自有價牌為 S27F612EAC 與 4,990，所以……這是一般單機。"
        )
        history = [
            make_pass(
                "單機", "S27F612EAC", "4990", 3, True, "matched",
                thinking="我看到中央單機與自有價牌 S27F612EAC、4,990，所以……",
            ),
            make_pass(
                "單機", "S27F612EAC", "4990", 3, True, "matched",
                thinking=detailed_edge_cut,
            ),
        ]
        current = make_pass(
            "單機", "S27F612EAC", "4990", 3, True, "matched",
            thinking="我看到中央單機與自有價牌 S27F612EAC、4,990，所以……",
        )

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(
            result["adjudication_rule"],
            "two_pass_edge_cut_identity_consensus",
        )
        self.assertEqual(current["view_type"], "單機")
        self.assertEqual(current["complete_screen_count"], 1)
        self.assertEqual(current["model"], "S27F612EAC")
        self.assertEqual(current["price"], "4990")

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

    def test_separate_left_and_right_edge_clauses_cannot_claim_three_complete(self):
        edge_text = (
            "我看到三台螢幕並排展示，中央螢幕四邊四角完整在原圖內。"
            "左側螢幕左外框被照片左邊界截斷，右側螢幕右外框被照片右邊界截斷，"
            "上方與下方無其他完整螢幕，所以……這是一般單機。"
        )
        history = [
            make_pass("單機", "S24F332EAC", "2590", 3, True, "matched", thinking=edge_text),
            make_pass("單機", "S24F332EAC", "2590", 1, True, "matched"),
        ]
        current = make_pass(
            "單機", "S24F332EAC", "2590", 3, True, "matched", thinking=edge_text
        )

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(current["complete_screen_count"], 1)

    def test_followme_inside_wide_wall_finalizes_as_followme_subject(self):
        physical = [
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
            {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
        ]
        history = [
            make_pass(
                "單機", "FollowMe M7 32\"", "14990", 3, True, "matched", physical,
                thinking="前景是 FollowMe，背景上方展示牆有三台以上完整入鏡的螢幕。",
            ),
            make_pass(
                "單機", "FollowMe M7 32\"", "14990", 1, True, "matched", physical,
                thinking="前景是 FollowMe，背景展示牆上有數台完整螢幕。",
            ),
        ]
        current = make_pass(
            "單機", "FollowMe M7 32\"", "14990", 1, True, "matched", physical,
            thinking="前景是 FollowMe 唯一商品主角。",
        )

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(
            result["adjudication_rule"],
            "two_pass_followme_physical_consensus",
        )
        self.assertEqual(current["view_type"], "單機")
        self.assertGreaterEqual(current["complete_screen_count"], 3)
        self.assertEqual(current["model"], 'FollowMe M7 32"')
        self.assertEqual(current["price"], "14990")
        self.assertTrue(current["followme_family_confirmed"])
        self.assertTrue(current["followme_physical_evidence"])

    def test_audited_followme_pixel_authority_replaces_out_of_frame_cues(self):
        image_hash = "4b069632c9af4da183fa5ff7e1ec616331f59ede149b7d9ea27b571be19213c5"
        passes = [
            make_pass(
                "單機",
                "FollowMe Pro M7 43\"",
                "17990",
                1,
                True,
                "matched",
                [
                    {"cue": "round_base", "same_subject": True, "strength": "strong"},
                    {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
                ],
                image_hash=image_hash,
            )
            for _ in range(3)
        ]
        passes[-1]["ocr_attempt"] = 3

        applied = apply_human_audited_pixel_authority(
            passes[-1], passes[:-1], max_attempts=3
        )

        self.assertTrue(applied)
        self.assertEqual(passes[-1]["complete_screen_count"], 3)
        self.assertEqual(
            passes[-1]["followme_physical_evidence"],
            [
                {
                    "cue": "direct_followme_branding_on_unit",
                    "same_subject": True,
                    "strength": "strong",
                }
            ],
        )

    def test_audited_distant_pixel_authority_marks_live_adjudication(self):
        image_hash = "3a3a69db3de4e5c5fd614e4f11921ae4c9d8cd21fdde682078fb01910e5dc317"
        passes = [
            make_pass(
                "遠景", None, None, 3, False, "ambiguous", [],
                image_hash=image_hash,
            )
            for _ in range(3)
        ]
        passes[-1]["ocr_attempt"] = 3

        applied = apply_human_audited_pixel_authority(
            passes[-1], passes[:-1], max_attempts=3
        )

        self.assertTrue(applied)
        self.assertTrue(passes[-1]["human_pixel_authority_applied"])
        self.assertEqual(
            passes[-1]["adjudication_rule"],
            "three_pass_human_audited_pixel_authority",
        )
        result = finalize_three_pass_outcome(
            passes[-1], passes[:-1], unresolved(), max_attempts=3
        )
        self.assertTrue(result["verified"])
        self.assertFalse(result["unresolved"])
        self.assertFalse(result["retry"])

    def test_xinzhuang_1458_pixel_authority_overrides_false_single_majority(self):
        image_hash = "66901c0a7a233affd6654e53a9a273a6e0b52803a219e869b6d430852fccf116"
        passes = [
            make_pass(
                "單機",
                "S32FM703UC",
                "12900",
                3,
                True,
                "matched",
                [],
                image_hash=image_hash,
            ),
            make_pass(
                "單機",
                "S32FM703UC",
                "12990",
                4,
                True,
                "matched",
                [],
                image_hash=image_hash,
            ),
            make_pass(
                "遠景",
                None,
                None,
                3,
                False,
                "not_visible",
                [],
                image_hash=image_hash,
            ),
        ]
        passes[-1]["ocr_attempt"] = 3

        applied = apply_human_audited_pixel_authority(
            passes[-1], passes[:-1], max_attempts=3
        )
        result = finalize_three_pass_outcome(
            passes[-1], passes[:-1], unresolved(), max_attempts=3
        )

        self.assertTrue(applied)
        self.assertTrue(result["verified"])
        self.assertEqual(passes[-1]["view_type"], "遠景")
        self.assertEqual(passes[-1]["complete_screen_count"], 3)
        self.assertFalse(passes[-1]["unique_main"])
        self.assertEqual(passes[-1]["label_ownership"], "not_visible")
        self.assertIsNone(passes[-1]["model"])
        self.assertIsNone(passes[-1]["price"])

    def test_audited_g8_and_crowded_label_pixels_override_fixture_and_label_drift(self):
        cases = [
            (
                "06d40425c784320d3acb7a3751da09f472cd9b727f1c63a06f3aae566fbc0f76",
                "S32DG802SC",
                27900,
            ),
            (
                "df57693c2161bac813e332484833addeb4b04d57e877fa4c742c3f31762be845",
                "S27F612EAC",
                4480,
            ),
            (
                "aaeb56c3d6e8739ae0027cbeb8275c124ba421319c4627ae9e65c5ee98675a23",
                "S32FM703UC",
                9990,
            ),
            (
                "ac0bdb9a1273eefe5d7f7e34908dfa49f2b6de2246a837b7e7330e29a078bd99",
                "S32DG802SC",
                36900,
            ),
        ]
        misleading_physical = [
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
            {"cue": "round_base", "same_subject": True, "strength": "strong"},
        ]
        for image_hash, expected_model, expected_price in cases:
            with self.subTest(image_hash=image_hash):
                passes = [
                    make_pass(
                        "單機", None, str(expected_price), 3, True, "matched",
                        misleading_physical,
                        image_hash=image_hash,
                    )
                    for _ in range(3)
                ]
                passes[-1]["ocr_attempt"] = 3

                applied = apply_human_audited_pixel_authority(
                    passes[-1], passes[:-1], max_attempts=3
                )
                result = finalize_three_pass_outcome(
                    passes[-1], passes[:-1], unresolved(), max_attempts=3
                )

                self.assertTrue(applied)
                self.assertTrue(result["verified"])
                self.assertEqual(passes[-1]["complete_screen_count"], 1)
                self.assertEqual(passes[-1]["model"], expected_model)
                self.assertEqual(passes[-1]["price"], expected_price)
                self.assertEqual(passes[-1]["followme_physical_evidence"], [])
                self.assertIn(expected_model, passes[-1]["thinking"])
                self.assertNotIn("健康閘收回", passes[-1]["thinking"])
                self.assertTrue(passes[-1]["thinking"].startswith("我看到"))
                self.assertTrue(passes[-1]["thinking"].endswith("所以……"))

    def test_audited_wide_scene_followme_pixels_override_false_distant_calls(self):
        for image_hash in (
            "87117a30dd8546152994366d43da2bfb20fe9825b1d1dae5c510d403c992113b",
            "48027d9a9f229514b85895ffa6fdf7e44681bbd4209fd41e831501d37ae1398b",
            "dfba3f110111a1804cd663c1828ad701d0f73e5cd6506f5d2f0d16f1aac60b98",
        ):
            with self.subTest(image_hash=image_hash):
                passes = [
                    make_pass(
                        "遠景", None, None, 5, False, "not_visible", [],
                        image_hash=image_hash,
                    )
                    for _ in range(3)
                ]
                passes[-1]["ocr_attempt"] = 3

                applied = apply_human_audited_pixel_authority(
                    passes[-1], passes[:-1], max_attempts=3
                )
                result = finalize_three_pass_outcome(
                    passes[-1], passes[:-1], unresolved(), max_attempts=3
                )

                self.assertTrue(applied)
                self.assertTrue(result["verified"])
                self.assertEqual(passes[-1]["view_type"], "單機")
                self.assertTrue(passes[-1]["followme_family_confirmed"])
                self.assertTrue(passes[-1]["followme_physical_evidence"])
                self.assertIsNone(passes[-1]["model"])
                self.assertIsNone(passes[-1]["price"])

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
        self.assertTrue(current["adjudication_narration_synthesized"])
        self.assertTrue(current["thinking"].startswith("我看到"))
        self.assertTrue(current["thinking"].endswith("所以……"))
        self.assertIn("維持無型號", current["thinking"])
        self.assertIn("3,300元", current["thinking"])
        self.assertNotIn("健康閘收回", current["thinking"])

    def test_adjudicated_narration_uses_final_count_not_superseded_raw_claim(self):
        first = make_pass(
            model="S24F332EAC",
            price="2390",
            count=3,
            thinking="我看到三台螢幕並排展示，中央螢幕四邊四角都在照片內，左側螢幕左外框被照片左邊界截斷，右側螢幕右外框被照片右邊界截斷。",
        )
        second = make_pass(
            model="S24F332EAC",
            price="2390",
            count=1,
            thinking="我看到中央一台完整，左右鄰機都被照片邊界裁切。",
        )
        current = make_pass(
            model="S24F332EAC",
            price="2390",
            count=3,
            thinking="我看到三台螢幕並排展示，中央一台螢幕完整入鏡，左右兩側的螢幕都被照片邊界裁切；所以這是一般單機，完整台數為三台。",
        )
        current["normalized_evidence"] = {
            "complete_screen_count": 3,
            "unique_main": True,
            "label_ownership": "matched",
            "followme_physical_evidence": [],
        }

        result = finalize_three_pass_outcome(current, [first, second], unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(current["complete_screen_count"], 1)
        self.assertIn("被裁切的鄰機不列入完整台數", current["thinking"])
        self.assertIn("S24F332EAC", current["thinking"])
        self.assertIn("2,390元", current["thinking"])
        self.assertNotIn("三台完整入鏡", current["thinking"])
        self.assertNotIn("共有3台", current["thinking"])
        self.assertNotIn("健康閘收回", current["thinking"])

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

    def test_wide_wall_stray_price_and_local_model_conflict_finish_distant(self):
        first = make_pass(
            view="單機",
            model=None,
            price="2988",
            count=7,
            unique=True,
            ownership="matched",
            thinking="我看到前景與上方一整排螢幕陳列，所有七台螢幕都完整入鏡。",
        )
        second = make_pass(
            view="單機",
            model=None,
            price=None,
            count=7,
            unique=True,
            ownership="not_visible",
            healthy=False,
            thinking="我看到一整排螢幕陳列，上方與中間層各有多台螢幕。",
            runtime_health={
                "healthy": False,
                "allow_processing": True,
                "allow_upload": False,
                "reasons": ["structured_authority_material_conflict:model"],
                "contained_for_stateless_retry": True,
            },
        )
        current = make_pass(
            view="單機",
            model=None,
            price=None,
            count=7,
            unique=True,
            ownership="ambiguous",
            thinking="我看到展示牆上方與下方多台螢幕完整陳列，沒有 FollowMe 實體。",
        )

        result = finalize_three_pass_outcome(current, [first, second], unresolved())

        self.assertTrue(result["verified"])
        self.assertFalse(result["unresolved"])
        self.assertEqual(
            result["adjudication_rule"],
            "three_pass_wide_scene_structural_consensus",
        )
        self.assertEqual(current["view_type"], "遠景")
        self.assertEqual(current["complete_screen_count"], 7)
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])

    def test_one_distant_call_vetoes_two_seven_screen_single_calls(self):
        first = make_pass(
            view="單機",
            model=None,
            price="2988",
            count=7,
            unique=True,
            ownership="matched",
            thinking="我看到前景、上方與中間層一整排螢幕陳列，所有七台完整入鏡。",
        )
        second = make_pass(
            view="單機",
            model=None,
            price=None,
            count=7,
            unique=True,
            ownership="not_visible",
            healthy=False,
            thinking="我看到一整排螢幕陳列，上方與中間層各有多台螢幕。",
            runtime_health={
                "healthy": False,
                "allow_processing": True,
                "allow_upload": False,
                "reasons": ["structured_authority_material_conflict:model"],
                "contained_for_stateless_retry": True,
            },
        )
        current = make_pass(
            view="遠景",
            model=None,
            price=None,
            count=6,
            unique=False,
            ownership="not_visible",
            thinking="我看到一整排螢幕陳列，上方與下方都有多台完整螢幕，無法鎖定唯一主角。",
        )

        result = finalize_three_pass_outcome(current, [first, second], unresolved())

        self.assertTrue(result["verified"])
        self.assertFalse(result["unresolved"])
        self.assertEqual(
            result["adjudication_rule"],
            "distant_structural_veto_over_wide_geometry_single_votes",
        )
        self.assertEqual(current["view_type"], "遠景")
        self.assertEqual(current["complete_screen_count"], 6)
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

    def test_first_distant_then_two_followme_fixture_votes_finalize_without_fourth_call(self):
        fixture = [
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
            {"cue": "round_base", "same_subject": True, "strength": "strong"},
            {"cue": "attached_followme_product_card", "same_subject": True, "strength": "strong"},
        ]
        history = [
            make_pass(
                "遠景", None, None, 3, False, "not_visible",
                runtime_health={
                    "healthy": False,
                    "reasons": ["structured_narration_followme_conflict"],
                },
                thinking="前景看見白色垂直支架與圓形底座，但結構輸出漏列。",
            ),
            make_pass(
                "單機", None, None, 4, True, "not_visible", fixture,
                thinking="前景同一主體有白色支架、圓形底座與產品卡。",
            ),
        ]
        current = make_pass(
            "單機", None, None, 1, True, "not_visible", fixture,
            thinking="同一主體再次確認白色支架、圓形底座與 FollowMe 產品卡。",
        )

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertFalse(result["unresolved"])
        self.assertFalse(result.get("technical_retry_required", False))
        self.assertEqual(result["adjudication_rule"], "two_pass_followme_physical_consensus")
        self.assertEqual(current["view_type"], "單機")
        self.assertTrue(current["followme_family_confirmed"])
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])

    def test_one_structural_distant_does_not_override_two_identity_free_single_votes(self):
        structural = make_pass(
            view="遠景",
            model=None,
            price=None,
            count=3,
            unique=False,
            ownership="not_visible",
            thinking=(
                "我看到前景左、中、右三台完整入鏡的螢幕，四邊四角都在原圖內，"
                "背景另有展示螢幕，無法鎖定唯一主角。"
            ),
        )
        weak_single = make_pass(
            view="單機",
            model=None,
            price=None,
            count=1,
            unique=True,
            ownership="not_visible",
            thinking="我看到展示櫃上三台螢幕，但猜測只有中央一台完整。",
        )
        current = make_pass(
            view="單機",
            model=None,
            price=None,
            count=1,
            unique=True,
            ownership="ambiguous",
            thinking="我看到寬廣展示區，但猜測中央螢幕是唯一主角。",
        )

        result = finalize_three_pass_outcome(
            current, [structural, weak_single], unresolved()
        )

        self.assertTrue(result["verified"])
        self.assertEqual(
            result["adjudication_rule"],
            "two_pass_identity_free_single_consensus",
        )
        self.assertEqual(current["view_type"], "單機")
        self.assertEqual(current["complete_screen_count"], 1)
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])

    def test_two_matched_identity_single_votes_survive_one_bad_distant_count(self):
        structural = make_pass(
            view="遠景",
            model=None,
            price=None,
            count=3,
            unique=False,
            ownership="not_visible",
            thinking="我看到三台完整螢幕並排，因此判遠景。",
        )
        second = make_pass(
            view="單機",
            model="S24F332EAC",
            price="2390",
            count=1,
            unique=True,
            ownership="matched",
            thinking="我看到唯一完整主螢幕與其正下方同位價牌 S24F332EAC、2,390 元。",
        )
        current = make_pass(
            view="單機",
            model="S24F332EAC",
            price="2390",
            count=1,
            unique=True,
            ownership="matched",
            thinking="我看到唯一完整主螢幕與其正下方同位價牌 S24F332EAC、2,390 元。",
        )

        result = finalize_three_pass_outcome(
            current, [structural, second], unresolved()
        )

        self.assertTrue(result["verified"])
        self.assertEqual(current["view_type"], "單機")
        self.assertEqual(current["model"], "S24F332EAC")
        self.assertEqual(current["price"], "2390")

    def test_followme_inside_five_monitor_wall_finishes_as_followme_subject(self):
        fixture = [
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
            {"cue": "round_base", "same_subject": True, "strength": "strong"},
            {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
        ]
        history = [
            make_pass(
                "遠景",
                None,
                None,
                5,
                False,
                "not_visible",
                thinking="我看到展示架至少五台完整螢幕，中央雖有 FollowMe，仍無唯一主角。",
                runtime_health={
                    "healthy": False,
                    "allow_processing": True,
                    "allow_upload": False,
                    "reasons": ["structured_narration_followme_conflict"],
                },
            ),
            make_pass(
                "單機",
                'FollowMe M7 32"',
                "12618",
                5,
                True,
                "matched",
                fixture,
                thinking="中央是 FollowMe M7 32，但上方與右側另有多台完整螢幕。",
            ),
        ]
        current = make_pass(
            "單機",
            'FollowMe M7 32"',
            "12618",
            3,
            True,
            "matched",
            fixture,
            thinking="中央 FollowMe 可辨識，背景與旁邊至少三台完整螢幕仍完整入鏡。",
        )

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertFalse(result["unresolved"])
        self.assertEqual(
            result["adjudication_rule"],
            "two_pass_followme_physical_consensus",
        )
        self.assertEqual(current["view_type"], "單機")
        self.assertTrue(current.get("followme_family_confirmed", False))
        self.assertEqual(current["model"], 'FollowMe M7 32"')
        self.assertEqual(current["price"], "12618")
        self.assertGreaterEqual(current["complete_screen_count"], 3)

    def test_distant_with_strong_followme_hardware_is_contract_conflict(self):
        fixture = [
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
            {"cue": "round_base", "same_subject": True, "strength": "strong"},
        ]
        record = make_pass(
            "遠景", None, None, 5, False, "not_visible", fixture,
            thinking="我看到右側實機在螢幕像素外連著白色直立支架與完整圓形落地底座。",
        )

        valid, errors, _normalized = validate_evidence_contract(record)

        self.assertFalse(valid)
        self.assertIn("distant_followme_physical_conflict", errors)

    def test_followme_poster_in_five_monitor_wall_does_not_create_single_subject(self):
        history = [
            make_pass(
                "單機",
                None,
                None,
                5,
                True,
                "matched",
                thinking="展示牆共有五台完整螢幕，右側 FollowMe 只是宣傳展示牌。",
            ),
            make_pass(
                "單機",
                None,
                None,
                5,
                True,
                "ambiguous",
                thinking="上層與下層可見五台完整螢幕，沒有唯一可歸屬價牌的主角。",
            ),
        ]
        current = make_pass(
            "遠景",
            None,
            None,
            3,
            False,
            "not_visible",
            thinking="展示架至少三台完整螢幕，FollowMe 字樣不能歸屬特定橫向螢幕。",
        )

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(current["view_type"], "遠景")
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

    def test_followme_variant_disagreement_keeps_independent_price_consensus(self):
        fixture = [
            {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
            {"cue": "round_base", "same_subject": True, "strength": "strong"},
            {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
        ]
        history = [
            make_pass(
                model='FollowMe Pro M7 32"',
                price="17990",
                count=3,
                physical=fixture,
                thinking='我看到同一主體規格牌寫著 FollowMe Pro M7 32"，價牌為 17,990 元，所以……',
            ),
            make_pass(
                model='FollowMe M7 32"',
                price="17990",
                count=1,
                physical=fixture,
                thinking='我看到同一主體 FollowMe M7 32"，附著價牌為 17,990 元，所以……',
            ),
        ]
        current = make_pass(
            model=None,
            price="17990",
            count=3,
            physical=fixture,
            thinking="我看到同一台實體 FollowMe 與附著價牌 17,990 元，但型號未能細分，所以……",
        )

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(result["adjudication_rule"], "two_pass_followme_physical_consensus")
        self.assertTrue(current["followme_family_confirmed"])
        self.assertIsNone(current["model"])
        self.assertEqual(current["price"], "17990")

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

    def test_original_model_self_talk_is_preserved_in_adjudication_audit(self):
        history = [
            make_pass("遠景", None, None, 3, False, "ambiguous"),
            make_pass("遠景", None, None, 4, False, "not_visible"),
        ]
        current = make_pass("單機", "S27X", "5990", 1, True, "matched")
        original_thinking = current["thinking"]

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(
            current["adjudication_original_current"]["thinking"], original_thinking
        )
        self.assertTrue(current["thinking"].startswith("我看到"))
        self.assertTrue(current["thinking"].endswith("所以……"))
        self.assertIn("系統", "系統" + current["adjudication_summary"])
        self.assertIn("遠景", current["adjudication_summary"])

    def test_identity_free_single_majority_finishes_without_fourth_call(self):
        history = [
            make_pass(
                "遠景", None, None, 3, False, "not_visible",
                thinking="賣場有螢幕陳列，但本輪沒有提供可核對的幾何證據。",
            ),
            make_pass(
                "單機", None, None, 1, True, "not_visible",
                thinking="中央只有一台完整主角，規格與價格看不清楚。",
            ),
        ]
        current = make_pass(
            "單機", None, None, 1, True, "not_visible",
            quality_issue="不合格-沒有規格和價格牌",
            thinking="中央只有一台完整主角，沒有可讀型號或價格。",
        )

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertFalse(result["unresolved"])
        self.assertEqual(
            result["adjudication_rule"],
            "two_pass_identity_free_single_consensus",
        )
        self.assertEqual(current["view_type"], "單機")
        self.assertEqual(current["complete_screen_count"], 1)
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])
        self.assertTrue(current["thinking"].endswith("所以……"))

    def test_identity_free_terminal_result_clears_superseded_upload_blockers(self):
        history = [
            make_pass(
                model=None,
                price=None,
                count=1,
                unique=True,
                ownership="matched",
                model_validation_failed=True,
                unlisted_model_candidate=True,
                official_model_unverified=True,
            ),
            make_pass(
                model="S34D300GAC",
                price=None,
                count=1,
                unique=True,
                ownership="matched",
                model_validation_failed=True,
                unlisted_model_candidate=True,
                official_model_unverified=True,
            ),
        ]
        current = make_pass(
            model=None,
            price=None,
            count=1,
            unique=True,
            ownership="matched",
            model_validation_failed=True,
            unlisted_model_candidate=True,
            official_model_unverified=True,
        )

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(
            result["adjudication_rule"],
            "two_pass_identity_free_single_consensus",
        )
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])
        self.assertFalse(current["model_validation_failed"])
        self.assertEqual(current["rejected_model"], "")
        self.assertFalse(current["price_conflict_detected"])
        self.assertFalse(current["requires_structured_retry"])
        self.assertEqual(current["structured_authority_blocked_fields"], [])
        self.assertFalse(current["unlisted_model_candidate"])
        self.assertFalse(current["official_model_unverified"])
        self.assertFalse(current["unlisted_model_photo_consensus"])

    def test_identity_free_fallback_never_clears_cross_photo_integrity_flag(self):
        history = [
            make_pass("單機", None, None, 1, True, "not_visible"),
            make_pass(
                "單機", None, None, 1, True, "not_visible",
                cross_photo_duplicate_core_suspected=True,
            ),
        ]
        current = make_pass("單機", None, None, 1, True, "not_visible")

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertFalse(result["verified"])
        self.assertTrue(result["technical_retry_required"])

    def test_screen_below_branding_is_not_mistaken_for_wide_background(self):
        history = [
            make_pass(
                "單機", None, None, 1, True, "not_visible",
                thinking=(
                    "我看到一台直立螢幕，螢幕下方有 Samsung 品牌標誌，"
                    "價牌看不清楚，沒有其他完整螢幕。"
                ),
            ),
            make_pass(
                "單機", None, None, 1, True, "not_visible",
                thinking=(
                    "我看到一台完整主角，螢幕正下方有品牌貼紙，"
                    "沒有可讀型號或價格。"
                ),
            ),
        ]
        current = make_pass(
            "單機", None, None, 1, True, "not_visible",
            thinking="我看到一台完整螢幕，下方標籤無法讀取，沒有其他完整螢幕。",
        )

        result = finalize_three_pass_outcome(current, history, unresolved())

        self.assertTrue(result["verified"])
        self.assertEqual(
            result["adjudication_rule"],
            "two_pass_identity_free_single_consensus",
        )

    def test_followme_scene_conflict_finishes_after_three_calls_without_price(self):
        physical = [
            {
                "cue": "white_vertical_stand",
                "same_subject": True,
                "strength": "strong",
            },
            {
                "cue": "round_base",
                "same_subject": True,
                "strength": "strong",
            },
        ]
        first = make_pass(
            "單機", None, None, 1, True, "not_visible", physical
        )
        second = make_pass(
            "遠景",
            None,
            None,
            3,
            False,
            "not_visible",
            physical,
            healthy=False,
            runtime_health={
                "healthy": False,
                "allow_processing": True,
                "allow_upload": False,
                "reasons": [
                    "distant_followme_strong_evidence_conflict",
                    "structured_narration_followme_conflict",
                ],
                "contained_for_stateless_retry": True,
            },
        )
        current = make_pass(
            "單機",
            'FollowMe Pro M7 43"',
            None,
            1,
            True,
            "matched",
            physical,
        )

        result = finalize_three_pass_outcome(
            current,
            [first, second],
            unresolved(),
        )

        self.assertTrue(result["verified"])
        self.assertFalse(result["unresolved"])
        self.assertEqual(current["view_type"], "單機")
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])
        self.assertTrue(current["followme_family_confirmed"])
        self.assertEqual(
            result["adjudication_rule"],
            "two_pass_followme_physical_consensus",
        )

    def test_content_fuse_receipt_restores_followme_scene_second_call(self):
        from tools.finalize_existing_three_pass_reviews import _load_three_call_groups

        with TemporaryDirectory() as temp_dir:
            audit = Path(temp_dir)
            trace = audit / "trace.jsonl"
            recovery_dir = audit / "content_fuse_recovery"
            history_dir = audit / "runtime_health_fuse_history"
            recovery_dir.mkdir()
            history_dir.mkdir()
            source_id = "9" * 64
            image_hash = "8" * 64
            name = "followme-753.jpg"
            physical = [
                {
                    "cue": "white_vertical_stand",
                    "same_subject": True,
                    "strength": "strong",
                },
                {
                    "cue": "round_base",
                    "same_subject": True,
                    "strength": "strong",
                },
            ]

            def trace_row(attempt, model):
                parsed = make_pass(
                    "單機",
                    model,
                    None,
                    1,
                    True,
                    "matched" if model else "not_visible",
                    physical,
                    image_hash=image_hash,
                    file_name=name,
                    source_item_id=source_id,
                    source_path=str(audit / name),
                    original_source_path=str(audit / "original" / name),
                    period="202606",
                    run_id=f"run-{attempt}",
                    ocr_attempt=attempt,
                    timestamp=f"2026-07-18T00:00:0{attempt}",
                )
                return {
                    "file_name": name,
                    "source_item_id": source_id,
                    "run_id": parsed["run_id"],
                    "timestamp": parsed["timestamp"],
                    "parsed_output": parsed,
                }

            trace.write_text(
                "\n".join(
                    json.dumps(row, ensure_ascii=False)
                    for row in (
                        trace_row(1, None),
                        trace_row(3, 'FollowMe Pro M7 43"'),
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            receipt = recovery_dir / "receipt.json"
            archive = history_dir / f"content_test_{source_id[:12]}.json"
            archive.write_text(
                json.dumps(
                    {
                        "source_file": name,
                        "attempt": 2,
                        "run_id": "run-2",
                        "tripped_at": "2026-07-18T00:00:02",
                        "reasons": [
                            "distant_followme_strong_evidence_conflict",
                            "structured_narration_followme_conflict",
                        ],
                        "clearance": (
                            "same_photo_followme_scene_conflict_preserve_call_and_retry"
                        ),
                        "recovery_receipt": str(receipt),
                        "record_snapshot": {
                            "view_type": "遠景",
                            "category": "遠景",
                            "model": None,
                            "price": None,
                            "complete_screen_count": 3,
                            "unique_main": False,
                            "label_ownership": "not_visible",
                            "followme_physical_evidence": physical,
                            "narration": "前景主體具有 FollowMe 支架，但結構欄位判為遠景。",
                            "raw_model_output": json.dumps(
                                {
                                    "request_id": "7" * 32,
                                    "screen_status": "正常",
                                    "quality_issue": "無",
                                },
                                ensure_ascii=False,
                            ),
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            receipt.write_text(
                json.dumps(
                    {
                        "status": "recovered",
                        "file_name": name,
                        "source_item_id": source_id,
                        "consumed_calls": 2,
                        "remaining_calls": 1,
                        "recovery_rule": (
                            "same_photo_followme_scene_conflict_preserve_call_and_retry"
                        ),
                        "fuse_history": str(archive),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            calls = _load_three_call_groups(trace)[name]
            current = calls[-1]
            outcome = finalize_three_pass_outcome(
                current,
                calls[:-1],
                unresolved(),
            )

        self.assertEqual([item["ocr_attempt"] for item in calls], [1, 2, 3])
        self.assertTrue(calls[1]["recovered_from_contained_followme_scene_fuse"])
        self.assertTrue(outcome["verified"])
        self.assertTrue(current["followme_family_confirmed"])
        self.assertIsNone(current["model"])
        self.assertIsNone(current["price"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

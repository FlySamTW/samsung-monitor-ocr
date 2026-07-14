import csv
import json
import tempfile
import unittest
from pathlib import Path

import samsung_ocr_batch_processor as batch

from skills.audit_fields import evidence_contract_decision, immediate_retry_decision, validate_evidence_contract
from tools.prepare_drive_upload_manifest import (
    classify_file,
    load_complete_auto_verified_names,
    load_v1945_trace_names,
)
from tools.rerun_questionable_records import is_complete_auto_verified
from skills.batch_orchestrator import _append_v1945_trace
from samsung_ocr_batch_processor import _merge_v1945_json_objects


def evidence(count, unique, ownership="not_visible", physical=None):
    return {
        "complete_screen_count": count,
        "unique_main": unique,
        "label_ownership": ownership,
        "followme_physical_evidence": physical or [],
    }


class EvidenceContractTests(unittest.TestCase):
    def test_prompt_examples_all_have_core_and_evidence(self):
        prompt = Path(__file__).resolve().parents[1].joinpath("samsung_ocr_prompt.txt").read_text(encoding="utf-8")
        objects = batch._extract_balanced_json_objects(prompt)
        required = {"view_type", "screen_status", "quality_issue", "model", "price", "complete_screen_count", "unique_main", "label_ownership", "followme_physical_evidence"}
        self.assertEqual(len(objects), 14)
        for raw in objects:
            value = raw.get("value", raw) if isinstance(raw, dict) else json.loads(raw)
            self.assertTrue(required.issubset(value), value)

    def test_contract_is_last_and_prompt_is_bounded(self):
        prompt = Path(__file__).resolve().parents[1].joinpath("samsung_ocr_prompt.txt").read_text(encoding="utf-8")
        full, _ = batch.build_runtime_system_prompt(prompt, "\\nDYNAMIC_REFERENCE")
        self.assertTrue(full.endswith(batch.V1945_OUTPUT_CONTRACT))
        self.assertLessEqual(len(full), batch.RUNTIME_SYSTEM_PROMPT_MAX_CHARS)

    def test_retry_contract_and_prior_evidence_are_present(self):
        previous = [{"view_type": "遠景", "complete_screen_count": 3, "unique_main": False,
                     "label_ownership": "not_visible", "followme_physical_evidence": []}]
        messages = batch.build_ocr_messages("system", "user", 2, previous)
        self.assertIn(batch.V1945_OUTPUT_CONTRACT, messages[-1]["content"])
        self.assertIn("complete_screen_count", messages[-2]["content"])
        self.assertIn("followme_physical_evidence", messages[-2]["content"])
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

    def test_distant_structured_evidence_still_requires_supporting_narration(self):
        row = {
            "file_name": "M-202605-distant.jpg", "view_type": "遠景", "category": "遠景",
            "model": None, "price": None, "quality_issue": "", "thinking": "整體符合遠景條件。",
            **evidence(3, False, "not_visible"),
        }
        decision = immediate_retry_decision(row, 3, [dict(row), dict(row)], 3)
        self.assertTrue(decision["unresolved"])
        self.assertIn("evidence_thinking_conflict", decision["reasons"])

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
                "trace_version": "v19.45", "file_name": "source-test.jpg", "period": "202601",
                "guard_decision": {"verified": True},
            }) + "\n", encoding="utf-8")
            self.assertEqual(load_v1945_trace_names(root), {target_name})
            row = {"auto_verified": "true", "auto_review_required": "false", "ocr_attempt": "1", "evidence_contract_version": "v19.45", "evidence_contract_valid": "true", "file_name": "source-test.jpg", "period": "202601", "view_type": "單機", "model": "S24F332EAC", "thinking": "ok", "run_id": "r"}
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
            decision = {"retry": True, "unresolved": False, "verified": False}
            _append_v1945_trace(tmp, result, decision, ["missing evidence"])
            _append_v1945_trace(tmp, result, decision, ["missing evidence"])
            lines = (Path(tmp) / "v1945_evidence_trace.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            self.assertNotIn("SECRET", lines[0])


if __name__ == "__main__":
    unittest.main()

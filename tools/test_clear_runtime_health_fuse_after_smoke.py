import json
import tempfile
import unittest
from pathlib import Path

from tools.clear_runtime_health_fuse_after_smoke import verify_and_clear


class RuntimeFuseSmokeClearanceTests(unittest.TestCase):
    def test_bound_smoke_archives_fuse_without_touching_formal_retry_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audit = root / "_ocr_audit"
            smoke = root / "_ocr_staging" / "runtime_health_smoke_case"
            audit.mkdir(parents=True)
            smoke.mkdir(parents=True)
            (audit / "model_benchmark.lock").write_text("locked", encoding="utf-8")
            (audit / "runtime_health_fuse.json").write_text(
                json.dumps({"active": True, "tripped_at": "2026-07-21T01:00:00+08:00", "source_file": "747.jpg", "reasons": ["request_id_mismatch"]}),
                encoding="utf-8",
            )
            (smoke / ".ocr_presentation_run.json").write_text(
                json.dumps({"run_id": "smoke-run", "started_at": "2026-07-21T02:00:00+08:00"}),
                encoding="utf-8",
            )
            task = [{
                "data": {"image": "/data/upload/1/199.jpg", "ocr_meta": {"auto_verified": True, "evidence_guard_revision": "rev"}},
                "annotations": [{"result": [
                    {"from_name": "category", "value": {"choices": ["單機"]}},
                    {"from_name": "model", "value": {"text": ["null"]}},
                    {"from_name": "price", "value": {"text": ["null"]}},
                ]}],
            }]
            (smoke / "run-OCR成功.json").write_text(json.dumps(task), encoding="utf-8")
            trace = {
                "run_id": "smoke-run", "file_name": "199.jpg", "attempt": 1,
                "evidence_guard_revision": "rev",
                "parsed_output": {
                    "request_id_verified": True, "request_binding_enforced": True,
                    "independent_pass": True, "prior_answer_exposed": False,
                    "prompt_contamination": False, "input_image_sha256": "a" * 64,
                    "runtime_health": {"healthy": True, "reasons": []},
                },
            }
            (audit / "v1945_evidence_trace.jsonl").write_text(json.dumps(trace) + "\n", encoding="utf-8")

            dry = verify_and_clear(audit_dir=audit, smoke_dir=smoke, expected_revision="rev", audited_file="199.jpg", apply=False)
            self.assertEqual(dry["status"], "would_clear")
            self.assertTrue((audit / "runtime_health_fuse.json").is_file())

            applied = verify_and_clear(audit_dir=audit, smoke_dir=smoke, expected_revision="rev", audited_file="199.jpg", apply=True)
            self.assertEqual(applied["status"], "cleared")
            self.assertFalse((audit / "runtime_health_fuse.json").exists())
            self.assertTrue(Path(applied["receipt"]).is_file())
            self.assertTrue(Path(applied["fuse_history"]).is_file())


if __name__ == "__main__":
    unittest.main()

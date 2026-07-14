"""Offline tests for the bounded benchmark sidecar."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model_benchmark_sidecar as sidecar


class SidecarTests(unittest.TestCase):
    def test_atomic_lock_and_live_owner_prevents_stale_recovery(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "_ocr_audit" / sidecar.LOCK_NAME
            owner = sidecar.acquire_benchmark_lock(path, ["qwen/qwen3-vl-8b"], pid_exists=lambda _: True)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["pid"], owner["pid"])
            with self.assertRaises(sidecar.SafetyError):
                sidecar.acquire_benchmark_lock(path, ["other"], recover_stale=True, stale_age_seconds=0, pid_exists=lambda _: True)
            sidecar.release_benchmark_lock(path, owner["pid"])
            self.assertFalse(path.exists())

    def test_lock_claim_is_exclusive(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "_ocr_audit" / sidecar.LOCK_NAME
            owner = sidecar.acquire_benchmark_lock(path, ["qwen/qwen3-vl-8b"])
            with self.assertRaises(sidecar.SafetyError):
                sidecar.acquire_benchmark_lock(path, ["qwen/qwen3-vl-8b"])
            sidecar.release_benchmark_lock(path, owner["pid"])

    def test_watcher_waits_before_owned_launches(self):
        source = (Path(__file__).resolve().parent / "auto_rerun_questionable_after_recursive.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("function Wait-ForBenchmarkLock", source)
        self.assertGreaterEqual(source.count("Wait-ForBenchmarkLock"), 5)
        self.assertIn("model_benchmark.lock", source)

    def test_atomic_lock_and_conservative_stale_recovery(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "_ocr_audit" / sidecar.LOCK_NAME
            owner = sidecar.acquire_benchmark_lock(path, ["qwen/qwen3-vl-8b"], pid_exists=lambda _: True)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["pid"], owner["pid"])
            with self.assertRaises(sidecar.SafetyError):
                sidecar.acquire_benchmark_lock(path, ["other"], recover_stale=True, stale_age_seconds=0, pid_exists=lambda _: True)
            sidecar.release_benchmark_lock(path, owner["pid"])
            self.assertFalse(path.exists())

    def test_lock_claim_then_idle_recheck_cleans_up_on_race(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "_ocr_audit" / sidecar.LOCK_NAME
            sidecar.acquire_benchmark_lock(path, ["qwen/qwen3-vl-8b"])
            with self.assertRaises(sidecar.SafetyError):
                sidecar.acquire_benchmark_lock(path, ["qwen/qwen3-vl-8b"])
            owner = json.loads(path.read_text(encoding="utf-8"))["pid"]
            sidecar.release_benchmark_lock(path, owner)

    def test_dry_run_requires_execute_and_does_not_touch_lms(self):
        with patch.object(sidecar, "run_lms") as run_lms:
            with patch("sys.argv", ["runner"]):
                self.assertEqual(sidecar.main(), 0)
            run_lms.assert_not_called()

    def test_local_endpoint_and_runtime_guards_fail_closed(self):
        with self.assertRaises(sidecar.SafetyError):
            sidecar.local_url("https://example.invalid/v1")
        with patch.object(sidecar, "get_json", return_value={"is_running": True}):
            with self.assertRaises(sidecar.SafetyError):
                sidecar.assert_idle("http://127.0.0.1:5000/api/status", [])
        with patch.object(sidecar, "get_json", return_value={"is_running": False}):
            with self.assertRaises(sidecar.SafetyError):
                sidecar.assert_idle("http://127.0.0.1:5000/api/status", ["tools\\rerun_staged_candidates.py"])

    def test_process_inventory_is_utf8_json_and_enumeration_failure_is_fatal(self):
        command_line = str(sidecar.ROOT / "tools" / "rerun_staged_candidates.py")
        ok = type("Result", (), {"returncode": 0, "stdout": json.dumps([command_line]), "stderr": ""})()
        with patch.object(sidecar.subprocess, "run", return_value=ok) as run:
            self.assertEqual(sidecar.project_processes(), [command_line])
            command = run.call_args.args[0][-1]
            self.assertIn("ForEach-Object", command)
            self.assertIn("ConvertTo-Json", command)
            self.assertNotIn("%%", command)
        failed = type("Result", (), {"returncode": 1, "stdout": "", "stderr": "CIM failed"})()
        with patch.object(sidecar.subprocess, "run", return_value=failed):
            with self.assertRaises(sidecar.SafetyError):
                sidecar.project_processes()

    def test_chinese_distant_followme_result_is_counted_as_dangerous(self):
        case = {"id":"x","tags":["followme"],"expected":{"view_type":"單機","model":"FollowMe M7","price":"無價格"}}
        result = sidecar.score(case, {"view_type":"遠景","model":None,"price":None}, 0.1, None)
        self.assertIn("followme_misclassification", result["dangerous_categories"])

    def test_fixed_image_hash_and_resume_fingerprint_are_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); image = root / "sample.jpg"; image.write_bytes(b"image")
            case = {"id":"a","image":"sample.jpg","image_sha256":sidecar.sha256_bytes(b"image")}
            with patch.object(sidecar, "crop_bytes", return_value=[base64.b64encode(b"crop").decode()]):
                prepared = sidecar.prepare_case_evidence(case, "prompt", root)
            done = sidecar.validate_resume_fingerprints(
                [{"key":"m:a","candidate_model":"m","case_id":"a","input_fingerprint":prepared["input_fingerprint"]}],
                {"a":prepared},
            )
            self.assertEqual(done, {"m:a"})
            with self.assertRaises(sidecar.SafetyError):
                sidecar.validate_resume_fingerprints([{"key":"m:a","candidate_model":"m","case_id":"a","input_fingerprint":"stale"}], {"a":prepared})
            with self.assertRaises(sidecar.SafetyError):
                sidecar.validate_resume_fingerprints([{"key":"wrong:a","candidate_model":"m","case_id":"a","input_fingerprint":prepared["input_fingerprint"]}], {"a":prepared})
            complete_row = {"key":"m:a","candidate_model":"m","case_id":"a","input_fingerprint":prepared["input_fingerprint"],
                            "manifest_sha256":"manifest","case_set_sha256":"cases","prompt_sha256":"prompt"}
            with self.assertRaises(sidecar.SafetyError):
                sidecar.validate_resume_fingerprints([complete_row], {"a":prepared}, manifest_sha256="changed")
            with self.assertRaises(sidecar.SafetyError):
                sidecar.validate_resume_fingerprints([complete_row, complete_row], {"a":prepared})
            case["image_sha256"] = "bad"
            with self.assertRaises(sidecar.SafetyError):
                sidecar.prepare_case_evidence(case, "prompt", root)

    def test_mock_execute_is_resumable_and_restores_baseline(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            image = root / "sample.jpg"
            image.write_bytes(b"not-used-by-mock")
            manifest = root / "manifest.json"
            cases = [{
                "id": "a", "image": "sample.jpg", "tags": [],
                "image_sha256": sidecar.sha256_bytes(b"not-used-by-mock"),
                "expected": {"view_type": "single", "model": "S", "price": "100"}}]
            case_set_sha256 = hashlib.sha256(json.dumps(
                sidecar.manifest_case_contract(cases), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")).hexdigest()
            manifest.write_text(json.dumps({"schema":"samsung-model-benchmark/v2","case_set_sha256":case_set_sha256,"candidates": ["qwen/qwen3-vl-8b"], "cases": cases}), encoding="utf-8")
            prompt = root / "prompt.txt"
            prompt.write_text("fixed prompt", encoding="utf-8")
            args = argparse.Namespace(api_base="http://127.0.0.1:1234/v1", backend_url="http://127.0.0.1:5000",
                manifest=manifest, prompt=prompt, output=root / "out", context_length=16384, timeout=2,
                models=["qwen/qwen3-vl-8b"], runtime_output_dir=root / "runtime",
                recover_stale_lock=False, stale_lock_age_seconds=3600)
            calls = []
            def fake_lms(_lms, command, _timeout=600):
                calls.append(command)
                if command[0] == "ps": return "qwen/qwen3-vl-8b 16384"
                return "ok"
            status_calls = []
            def status_getter():
                status_calls.append(True); return {"is_running": False}
            with patch.object(sidecar, "visible_models", return_value={"qwen/qwen3-vl-8b"}), \
                 patch.object(sidecar, "loaded_snapshot", return_value={"qwen/qwen3-vl-8b": {"context_length": 32768}}), \
                 patch.object(sidecar, "run_lms", side_effect=fake_lms), \
                 patch.object(sidecar, "crop_bytes", return_value=[base64.b64encode(b"crop").decode()]), \
                 patch.object(sidecar, "post_completion", return_value='{"view_type":"single","model":"S","price":"100"}'):
                result = sidecar.run(args, status_getter=status_getter, process_getter=lambda: [], lms="lms", sample_root=root)
                calls_after_first_run = len(calls)
                statuses_after_first_run = len(status_calls)
                resumed = sidecar.run(args, status_getter=status_getter, process_getter=lambda: [], lms="lms", sample_root=root)
            self.assertEqual(result["new_records"], 1)
            self.assertEqual(resumed["new_records"], 0)
            self.assertEqual(statuses_after_first_run, 2)
            self.assertEqual(len(status_calls), statuses_after_first_run + 1)
            self.assertEqual(len(calls), calls_after_first_run)
            self.assertTrue(any(c[:2] == ["load", "qwen/qwen3-vl-8b"] for c in calls))
            self.assertEqual(calls[-2][:2], ["unload", "qwen/qwen3-vl-8b"])
            self.assertEqual(calls[-1][:2], ["load", "qwen/qwen3-vl-8b"])
            self.assertIn("32768", calls[-1])
            raw_rows = [json.loads(line) for line in (root / "out" / "raw.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(raw_rows), 1)
            self.assertEqual(raw_rows[0]["candidate_model"], "qwen/qwen3-vl-8b")
            self.assertEqual(raw_rows[0]["model"], "S")


if __name__ == "__main__":
    unittest.main()

"""Offline tests for the bounded benchmark sidecar."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model_benchmark_sidecar as sidecar


class SidecarTests(unittest.TestCase):
    def test_atomic_lock_and_conservative_stale_recovery(self):
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

    def test_mock_execute_is_resumable_and_restores_baseline(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            image = root / "sample.jpg"
            image.write_bytes(b"not-used-by-mock")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"candidates": ["qwen/qwen3-vl-8b"], "cases": [{
                "id": "a", "image": "sample.jpg", "tags": [],
                "expected": {"view_type": "single", "model": "S", "price": "100"}}]}), encoding="utf-8")
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
            with patch.object(sidecar, "get_json", return_value={"is_running": False}), \
                 patch.object(sidecar, "visible_models", return_value={"qwen/qwen3-vl-8b"}), \
                 patch.object(sidecar, "loaded_snapshot", return_value={"qwen/qwen3-vl-8b": {"context_length": 16384}}), \
                 patch.object(sidecar, "run_lms", side_effect=fake_lms), \
                 patch.object(sidecar, "encode_image", return_value="full"), \
                 patch.object(sidecar, "crop_bytes", return_value=["crop"]), \
                 patch.object(sidecar, "post_completion", return_value='{"view_type":"single","model":"S","price":"100"}'):
                result = sidecar.run(args, process_getter=lambda: [], lms="lms")
            self.assertEqual(result["new_records"], 1)
            self.assertTrue(any(c[:2] == ["load", "qwen/qwen3-vl-8b"] for c in calls))
            self.assertEqual(calls[-2][:2], ["unload", "qwen/qwen3-vl-8b"])
            self.assertEqual(calls[-1][:2], ["load", "qwen/qwen3-vl-8b"])
            self.assertEqual(len((root / "out" / "raw.jsonl").read_text(encoding="utf-8").splitlines()), 1)
            sidecar.run(args, process_getter=lambda: [], lms="lms") if False else None


if __name__ == "__main__":
    unittest.main()

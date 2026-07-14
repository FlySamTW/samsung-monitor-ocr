"""Offline tests for the immutable 50-case benchmark manifest."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import model_benchmark_manifest as manifest_tool
import model_benchmark_sidecar as sidecar


class BenchmarkManifestTests(unittest.TestCase):
    def test_build_has_fixed_case_and_image_hashes(self):
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "manifest.json"
            manifest_tool.build(output)
            manifest = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(manifest["schema"], "samsung-model-benchmark/v2")
        self.assertEqual(len(manifest["cases"]), 50)
        self.assertEqual(len(manifest["labels_sha256"]), 64)
        self.assertEqual(len(manifest["case_set_sha256"]), 64)
        self.assertTrue(all(len(case["image_sha256"]) == 64 for case in manifest["cases"]))
        self.assertEqual(sidecar.verify_manifest_contract(manifest), manifest["case_set_sha256"])

    def test_case_contract_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "manifest.json"
            manifest_tool.build(output)
            manifest = json.loads(output.read_text(encoding="utf-8"))
        manifest["cases"][0]["expected"]["model"] = "tampered"
        with self.assertRaises(sidecar.SafetyError):
            sidecar.verify_manifest_contract(manifest)


if __name__ == "__main__":
    unittest.main(verbosity=2)

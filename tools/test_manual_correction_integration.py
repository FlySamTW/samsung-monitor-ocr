import csv
import tempfile
import time
import unittest
from pathlib import Path

from samsung_ocr_batch_processor import (
    load_manual_rule_prompt_section,
    should_save_manual_learning_rule,
)

class ManualCorrectionIntegrationTests(unittest.TestCase):
    def test_rule_requires_checkbox_and_hint(self):
        self.assertFalse(should_save_manual_learning_rule({"learn_rule": False, "rule_hint": "usable rule"}))
        self.assertFalse(should_save_manual_learning_rule({"learn_rule": True, "rule_hint": ""}))
        self.assertTrue(should_save_manual_learning_rule({"learn_rule": True, "rule_hint": "usable rule"}))

    def test_manual_rule_loader_reloads_after_mtime_change(self):
        import samsung_ocr_batch_processor as backend
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp) / "manual_learning_rules.csv"
            path.write_text("rule_hint\nfirst reusable rule\n", encoding="utf-8")
            old_path, old_cache = backend.MANUAL_RULES_PATH, dict(backend.MANUAL_RULES_CACHE)
            try:
                backend.MANUAL_RULES_PATH=path; backend.MANUAL_RULES_CACHE={"mtime_ns":None,"section":"","count":0}
                first,count=load_manual_rule_prompt_section(); self.assertEqual(count,1); self.assertIn("first reusable rule",first)
                time.sleep(0.002)
                path.write_text("rule_hint\nsecond reusable rule\n", encoding="utf-8")
                path.touch()
                second,count=load_manual_rule_prompt_section(); self.assertEqual(count,1); self.assertIn("second reusable rule",second); self.assertNotIn("first reusable rule",second)
            finally:
                backend.MANUAL_RULES_PATH, backend.MANUAL_RULES_CACHE = old_path, old_cache

    def test_manual_rule_loader_excludes_per_photo_answers(self):
        import samsung_ocr_batch_processor as backend
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manual_learning_rules.csv"
            path.write_text(
                "rule_hint\n"
                "遠景必須掃描全張照片所有區域\n"
                "這張型號 S32DM702UC 價格 17990\n"
                "M-門市-329.jpg 改成單機\n",
                encoding="utf-8",
            )
            old_path, old_cache = backend.MANUAL_RULES_PATH, dict(backend.MANUAL_RULES_CACHE)
            try:
                backend.MANUAL_RULES_PATH = path
                backend.MANUAL_RULES_CACHE = {"mtime_ns": None, "section": "", "count": 0}
                section, count = load_manual_rule_prompt_section()
                self.assertEqual(count, 1)
                self.assertIn("遠景必須掃描全張照片所有區域", section)
                self.assertNotIn("S32DM702UC", section)
                self.assertNotIn("17990", section)
                self.assertNotIn("329.jpg", section)
            finally:
                backend.MANUAL_RULES_PATH, backend.MANUAL_RULES_CACHE = old_path, old_cache

if __name__ == "__main__": unittest.main()

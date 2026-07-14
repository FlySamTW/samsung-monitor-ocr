import unittest
from pathlib import Path
import sys; sys.path.insert(0, str(Path(__file__).parent))
from build_manual_learning_dataset import build_rows, split_rows
from optimize_prompt_from_corrections import promotion_allowed, validate_endpoint

class PipelineTests(unittest.TestCase):
    def test_real_manual_schema_structured_target_and_rule_hint(self):
        rows=build_rows([{"source_path":"M-Taipei-Xinyi-TK3C-Shop-0001.jpg","corrected_view_type":"遠景","corrected_model":"S27","corrected_price":"999","corrected_price_symbol":"$","note":"ok"}], [{"rule_hint":"keep distant view"}], Path("."), False)
        self.assertEqual(rows[0]["target"]["model"], "S27"); self.assertEqual(rows[0]["target"]["price"], "999"); self.assertEqual(rows[0]["rule_context"], ["keep distant view"])
        self.assertEqual(rows[0]["store_group"], "m-taipei-xinyi-tk3c-shop")
    def test_dedup_and_group_safe_split(self):
        rows=build_rows([{"source_path":"a.jpg","corrected_model":"A"},{"source_path":"a.jpg","corrected_model":"A"},{"source_path":"b.jpg","corrected_model":"B"}],[],Path("."),False)
        out=split_rows(rows); self.assertEqual(len(out),2); self.assertEqual(len({r["split"] for r in out}),1)
    def test_gate_requires_improvement_and_all_guards(self):
        base={"exact_match":.5,"distant_view":.1,"followme":.1,"model_hallucination":.1}; good={**base,"exact_match":.6}; bad={**good,"followme":.2}
        self.assertTrue(promotion_allowed(base,good)); self.assertFalse(promotion_allowed(base,bad)); self.assertFalse(promotion_allowed(base,base))
    def test_external_endpoint_is_allowed_when_explicit(self):
        self.assertEqual(validate_endpoint("https://example.com/v1"), "https://example.com/v1")
        self.assertEqual(validate_endpoint("http://127.0.0.1:1234/v1"), "http://127.0.0.1:1234/v1")
if __name__ == "__main__": unittest.main()

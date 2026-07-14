import unittest

from model_benchmark_score import score_manifest


MANIFEST = {"cases": [
    {"id":"a","tags":["distant_view","hallucination_guard"],"expected":{"view_type":"遠景","model":None,"price":None}},
    {"id":"b","tags":["followme"],"expected":{"view_type":"單機","model":"FollowMe M7","price":"無價格"}},
]}


class BenchmarkScoreTests(unittest.TestCase):
    def test_complete_candidate_is_scored_without_mixing_other_model(self):
        predictions = [
            {"id":"a","candidate_model":"vlm-a","view_type":"遠景","model":None,"price":None,"latency_ms":10},
            {"id":"b","candidate_model":"vlm-a","view_type":"單機","model":"FollowMe M7","price":"無價格","latency_ms":20},
            {"id":"a","candidate_model":"vlm-b","view_type":"單機","model":"fake","price":"1"},
        ]
        result = score_manifest(MANIFEST, predictions, "vlm-a")
        self.assertTrue(result["benchmark_gate_pass"])
        self.assertEqual(result["fully_correct_rate"], 1.0)
        self.assertEqual(result["prediction_records_selected"], 2)

    def test_missing_duplicate_and_unknown_ids_fail_protocol_and_denominator(self):
        predictions = [
            {"id":"a","candidate_model":"vlm-a","view_type":"遠景","model":None,"price":None},
            {"id":"a","candidate_model":"vlm-a","view_type":"遠景","model":None,"price":None},
            {"id":"unknown","candidate_model":"vlm-a"},
        ]
        result = score_manifest(MANIFEST, predictions, "vlm-a")
        self.assertFalse(result["benchmark_gate_pass"])
        self.assertEqual(result["complete_prediction_rate"], 0.0)
        self.assertEqual(result["failure_reasons"]["duplicate_prediction"], 1)
        self.assertEqual(result["failure_reasons"]["missing_prediction"], 1)
        self.assertIn("unknown", result["unexpected_prediction_ids"])

    def test_danger_categories_cover_chinese_followme_and_model_hallucination(self):
        predictions = [
            {"id":"a","candidate_model":"vlm-a","view_type":"單機","model":"fake","price":None},
            {"id":"b","candidate_model":"vlm-a","view_type":"遠景","model":None,"price":None},
        ]
        result = score_manifest(MANIFEST, predictions, "vlm-a")
        categories = {item for row in result["rows"] for item in row["dangerous_categories"]}
        self.assertIn("distant_view_misclassification", categories)
        self.assertIn("model_hallucination", categories)
        self.assertIn("followme_misclassification", categories)


if __name__ == "__main__":
    unittest.main()

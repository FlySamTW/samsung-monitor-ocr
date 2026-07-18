import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DashboardReviewLabelTests(unittest.TestCase):
    def test_retry_card_never_claims_third_round_before_pass_three(self):
        source = (ROOT / "dashboard" / "src" / "App.jsx").read_text(
            encoding="utf-8"
        )
        self.assertIn("const getUnresolvedCardStatus = (item) =>", source)
        self.assertIn('decision === "retry_scheduled"', source)
        self.assertIn("passIndex > 0 && passIndex < 3", source)
        self.assertIn(
            "第 ${passIndex} 輪有疑點／已排入第 ${passIndex + 1} 輪",
            source,
        )
        self.assertIn("最多三輪，完成後自動結案上傳", source)
        self.assertIn("{getUnresolvedCardStatus(res).label}", source)
        self.assertIn("{getUnresolvedCardStatus(res).detail}", source)


if __name__ == "__main__":
    unittest.main()

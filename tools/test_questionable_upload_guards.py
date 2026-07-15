import csv, tempfile, unittest
from pathlib import Path
from tools.rerun_questionable_records import is_complete_auto_verified, reason_for
from tools.prepare_drive_upload_manifest import classify_file, write_stale_uploaded_review_csv

class QuestionableUploadGuardTests(unittest.TestCase):
    def _file(self, root, name):
        path = root / name
        path.write_bytes(b"jpg")
        return path

    def test_stale_current_year_finalization_blocks_every_current_year_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ordinary = self._file(root, "M-202601-Store-\u2191$100-x.jpg")
            distant = self._file(root, "M-202601-Store-\u9060\u666f-\u2191$100-y.jpg")
            ordinary_row = classify_file(ordinary, root, 100000, current_year_risk_fresh=False)
            distant_row = classify_file(distant, root, 100000, current_year_risk_fresh=False,
                                        auto_verified_names={distant.name})
            self.assertEqual(ordinary_row.status, "review")
            self.assertIn("current_year_finalization_proof_missing_or_stale", ordinary_row.reasons)
            self.assertNotIn("current_year_risk_audit_missing_or_stale", ordinary_row.reasons)
            self.assertEqual(distant_row.status, "review")
            self.assertIn("current_year_finalization_proof_missing_or_stale", distant_row.reasons)
            self.assertIn("current_year_risk_audit_missing_or_stale", distant_row.reasons)

    def test_historical_ready_and_uploaded_rows_ignore_current_year_staleness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self._file(root, "M-202512-Store-$100-old.jpg")
            row = classify_file(old, root, 100000, current_year_risk_fresh=False)
            self.assertEqual(row.status, "ready")
            self.assertEqual(write_stale_uploaded_review_csv(root / "stale.csv", [row], {old.name}), 0)

    def test_current_year_fresh_and_missing_trace_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fresh = self._file(root, "M-202601-Store-\u2191$100-fresh.jpg")
            missing = self._file(root, "M-202601-Store-\u2191$100-missing.jpg")
            fresh_row = classify_file(fresh, root, 100000, current_year_risk_fresh=True,
                                      auto_verified_names={fresh.name}, v1945_trace_names={fresh.name})
            missing_row = classify_file(missing, root, 100000, current_year_risk_fresh=True)
            self.assertEqual(fresh_row.status, "ready")
            self.assertIn("auto_verified_evidence_missing", missing_row.reasons)

    def test_v1945_verified_complete_is_not_rerun(self):
        row={"auto_verified":"true","auto_review_required":"false","ocr_attempt":"3","thinking":"three complete screens; no unique main subject","run_id":"v19.44" ,"view_type":"遠景"}
        row.update({"period":"202601", "evidence_contract_version":"v19.45", "evidence_contract_valid":"true"})
        self.assertTrue(is_complete_auto_verified(row)); self.assertEqual(reason_for(row), [])

    def test_auto_review_or_incomplete_remains_candidate(self):
        base={"auto_verified":"true","auto_review_required":"true","ocr_attempt":"3","thinking":"three complete screens; no unique main subject","run_id":"x","view_type":"遠景"}
        self.assertTrue(reason_for(base))
        self.assertTrue(reason_for({"view_type":"遠景","thinking":"three visible, only one complete"}))

    def test_promo_followme_card_is_not_physical_followme(self):
        row={"model":"FollowMe","thinking":"promotional card, not FollowMe, no physical stand or base","price":"10990"}
        self.assertIn("promotional", " ".join(reason_for(row)).lower())

    def test_true_distant_three_of_three_consensus_can_be_verified(self):
        row={"auto_verified":"true","auto_review_required":"false","ocr_attempt":"3","thinking":"3 complete screens; no unique main subject and no unique price/spec evidence","run_id":"v19.44","view_type":"遠景"}
        row.update({"period":"202601", "evidence_contract_version":"v19.45", "evidence_contract_valid":"true"})
        self.assertTrue(is_complete_auto_verified(row)); self.assertEqual(reason_for(row), [])

    def test_manifest_fails_closed_and_marks_uploaded_risk_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); path=root/"M-202601-Taipei-Shop-遠景-1.jpg"; path.write_bytes(b"jpg")
            row=classify_file(path,root,100000,risk_names={path.name},current_year_risk_fresh=True,auto_verified_names=set())
            self.assertEqual(row.status,"review"); self.assertIn("auto_verified_evidence_missing",row.reasons)
            stale=root/"stale.csv"; self.assertEqual(write_stale_uploaded_review_csv(stale,[row],{path.name}),1)
            self.assertIn("uploaded_but_now_review_required", stale.read_text(encoding="utf-8-sig"))

if __name__ == "__main__": unittest.main()

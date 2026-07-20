import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.maintain_active_three_pass_repairs import (
    _collect_stale_status_repairs,
    _merge_repaired_tasks,
    _repair_key,
)
from skills.batch_orchestrator import _select_durable_or_memory_record


def task(name, *, verified, review, rule=""):
    return {
        "data": {
            "image": f"/api/photos/{name}",
            "ocr_meta": {
                "file_name": name,
                "auto_verified": verified,
                "auto_review_required": review,
                "adjudication_rule": rule,
            },
        },
        "annotations": [{"result": []}],
    }


class ActiveThreePassRepairTests(unittest.TestCase):
    def test_verified_durable_adjudication_outweighs_stale_memory_failure(self):
        durable = {
            "file_name": "done.jpg",
            "auto_verified": True,
            "auto_review_required": False,
            "three_pass_adjudicated": True,
            "adjudication_rule": "three_pass_zero_screen_scene_consensus",
            "review_status": "已完成",
            "view_type": "遠景",
        }
        stale_memory = {
            "file_name": "done.jpg",
            "auto_verified": False,
            "auto_review_required": True,
            "technical_retry_exhausted": True,
            "review_status": "技術錯誤／已停止該張上傳",
            "view_type": "單機",
        }

        selected = _select_durable_or_memory_record(
            durable,
            stale_memory,
        )

        self.assertEqual(selected, durable)

    def test_normal_new_memory_result_still_outweighs_nonadjudicated_disk_row(self):
        durable = {
            "file_name": "live.jpg",
            "auto_verified": True,
            "auto_review_required": False,
            "view_type": "遠景",
        }
        live_memory = {
            "file_name": "live.jpg",
            "auto_verified": True,
            "auto_review_required": False,
            "view_type": "單機",
            "model": "S27D300GAC",
            "price": "3290",
        }

        selected = _select_durable_or_memory_record(
            durable,
            live_memory,
        )

        self.assertEqual(selected, live_memory)

    def test_merge_repairs_only_named_old_row_and_preserves_new_backend_row(self):
        result_path = Path("D:/staging/run-OCR成功.json")
        old = task("old.jpg", verified=False, review=True)
        new = task("new.jpg", verified=True, review=False)
        repaired = task(
            "old.jpg",
            verified=True,
            review=False,
            rule="three_pass_zero_screen_scene_consensus",
        )
        repairs = {
            _repair_key(result_path, "old.jpg"): copy.deepcopy(repaired),
        }

        merged, applied = _merge_repaired_tasks(
            [new, old],
            result_path=result_path,
            repairs=repairs,
        )

        self.assertEqual(applied, 1)
        self.assertEqual(
            merged[0]["data"]["ocr_meta"]["file_name"],
            "new.jpg",
        )
        self.assertTrue(merged[1]["data"]["ocr_meta"]["auto_verified"])
        self.assertEqual(
            merged[1]["data"]["ocr_meta"]["adjudication_rule"],
            "three_pass_zero_screen_scene_consensus",
        )

    def test_merge_is_idempotent(self):
        result_path = Path("D:/staging/run-OCR成功.json")
        repaired = task(
            "old.jpg",
            verified=True,
            review=False,
            rule="two_wide_geometry_votes_veto_single_identity_outlier",
        )
        repairs = {
            _repair_key(result_path, "old.jpg"): copy.deepcopy(repaired),
        }

        merged, applied = _merge_repaired_tasks(
            [copy.deepcopy(repaired)],
            result_path=result_path,
            repairs=repairs,
        )

        self.assertEqual(applied, 0)
        self.assertEqual(merged[0], repaired)

    def test_collects_stale_review_text_from_verified_row(self):
        from tempfile import TemporaryDirectory
        import json

        with TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "run-OCR成功.json"
            row = task("done.jpg", verified=True, review=False)
            row["data"]["ocr_meta"]["review_status"] = "review_required"
            result_path.write_text(
                json.dumps([row], ensure_ascii=False),
                encoding="utf-8",
            )
            repairs = {}

            added = _collect_stale_status_repairs(result_path, repairs)

            self.assertEqual(added, 1)
            repaired = repairs[_repair_key(result_path, "done.jpg")]
            self.assertEqual(
                repaired["data"]["ocr_meta"]["review_status"],
                "已完成",
            )

            merged, applied = _merge_repaired_tasks(
                [row],
                result_path=result_path,
                repairs=repairs,
            )
            self.assertEqual(applied, 1)
            self.assertEqual(
                merged[0]["data"]["ocr_meta"]["review_status"],
                "已完成",
            )


if __name__ == "__main__":
    unittest.main()

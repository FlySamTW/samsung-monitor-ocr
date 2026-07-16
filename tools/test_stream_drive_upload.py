import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.audit_fields import EVIDENCE_GUARD_REVISION
from tools.photo_rename_planner import copy_planned_image_idempotent, plan_single_image
from tools.rclone_drive_upload import md5_file, read_csv
from tools.stream_drive_upload import (
    _append_uploaded_atomic,
    enqueue_finalized_result,
    process_one_job,
    read_stream_status,
)


def make_image(path: Path, color=(20, 40, 60)) -> None:
    Image.new("RGB", (40, 30), color).save(path, format="JPEG", quality=92)


def verified_result(source: Path, **overrides):
    row = {
        "source_item_id": "a" * 64,
        "original_source_path": str(source),
        "source_path": str(source),
        "file_name": source.name,
        "period": "202601",
        "view_type": "單機",
        "category": "單機",
        "model": "S27CG552EC",
        "price": "4990",
        "price_status": "match",
        "price_symbol": "✓",
        "screen_status": "正常",
        "quality_issue": "無",
        "complete_screen_count": 1,
        "unique_main": True,
        "label_ownership": "matched",
        "followme_physical_evidence": [],
        "auto_verified": True,
        "auto_review_required": False,
        "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
        "independent_pass": True,
        "request_binding_enforced": True,
        "request_id_verified": True,
        "prior_answer_exposed": False,
        "prompt_contamination": False,
        "runtime_health": {"healthy": True},
        "input_image_sha256": "b" * 64,
        "run_id": "run-test",
        "ocr_attempt": 3,
    }
    row.update(overrides)
    return row


class FakeRclone:
    def __init__(self):
        self.remote = None
        self.copy_calls = 0
        self.copy_command = None

    def __call__(self, command, **_kwargs):
        action = command[1]
        if action == "copyto":
            local = Path(command[2])
            self.copy_calls += 1
            self.copy_command = list(command)
            self.remote = {
                "Name": Path(command[3]).name,
                "Size": local.stat().st_size,
                "Hashes": {"MD5": md5_file(local)},
                "ID": "drive-test-id",
            }
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if action == "lsjson":
            if self.remote is None:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="object not found")
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(self.remote), stderr="")
        raise AssertionError(command)


class StreamDriveUploadTests(unittest.TestCase):
    def test_fresh_receipt_refreshes_stale_matching_legacy_ledger_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "drive_upload_uploaded.csv"
            stale = {
                "index": "7",
                "source_path": "same-source.jpg",
                "file_name": "same-target.jpg",
                "drive_file_id": "",
                "uploaded_at": "2026-07-04T15:46:47",
            }
            fresh = {
                **stale,
                "drive_file_id": "fresh-drive-id",
                "uploaded_at": "2026-07-16T18:00:00",
            }

            _append_uploaded_atomic(ledger, stale)
            count = _append_uploaded_atomic(ledger, fresh)

            self.assertEqual(count, 1)
            rows = read_csv(ledger)
            self.assertEqual(rows[0]["index"], "1")
            self.assertEqual(rows[0]["drive_file_id"], "fresh-drive-id")
            self.assertEqual(rows[0]["uploaded_at"], "2026-07-16T18:00:00")

    def test_only_verified_bound_result_enters_outbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "M-台中市-南區-TK3C-台中旗艦-943.jpg"
            make_image(source)
            output = root / "output"

            job = enqueue_finalized_result(verified_result(source), output_dir=output)
            rejected = enqueue_finalized_result(
                verified_result(source, source_item_id="c" * 64, auto_review_required=True),
                output_dir=output,
            )

            self.assertIsNotNone(job)
            self.assertTrue(job.is_file())
            self.assertIsNone(rejected)
            self.assertEqual(read_stream_status(output)["pending"], 1)

    def test_distant_is_a_valid_final_upload_job_without_model_or_price(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "M-台中市-南區-TK3C-台中旗艦-942.jpg"
            make_image(source)
            output = root / "output"
            row = verified_result(
                source,
                view_type="遠景",
                category="遠景",
                model=None,
                price=None,
                price_status="not_compared",
                price_symbol="",
                complete_screen_count=3,
                unique_main=False,
                label_ownership="ambiguous",
            )

            job = enqueue_finalized_result(row, output_dir=output)
            payload = json.loads(job.read_text(encoding="utf-8"))

            self.assertIn("-遠景-", payload["target_name"])
            self.assertNotIn("型號未辨識", payload["target_name"])
            self.assertNotIn("無價格", payload["target_name"])

    def test_confirmed_followme_family_never_falls_back_to_distant_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "M-台中市-大里區-SF-大里-632.jpg"
            make_image(source)
            output = root / "output"
            physical = [
                {"cue": "white_vertical_stand", "same_subject": True, "strength": "strong"},
                {"cue": "round_base", "same_subject": True, "strength": "strong"},
                {"cue": "attached_price_tray", "same_subject": True, "strength": "strong"},
            ]
            row = verified_result(
                source,
                model=None,
                price=None,
                price_status="not_compared",
                price_symbol="",
                followme_family_confirmed=True,
                followme_physical_evidence=physical,
            )

            job = enqueue_finalized_result(row, output_dir=output)
            payload = json.loads(job.read_text(encoding="utf-8"))

            self.assertIn("-單機-FollowMe_型號未細分-", payload["target_name"])
            self.assertNotIn("-遠景-", payload["target_name"])

    def test_price_comparison_symbol_is_preserved_in_target_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "M-台中市-南區-TK3C-台中旗艦-941.jpg"
            make_image(source)
            for index, symbol in enumerate(("↑", "↓", "✓")):
                row = verified_result(source, source_item_id=f"{index + 1:064x}", price_symbol=symbol)
                job = enqueue_finalized_result(row, output_dir=root / "output")
                self.assertIn(f"-{symbol}＄4990-", json.loads(job.read_text(encoding="utf-8"))["target_name"])

    def test_publish_is_idempotent_and_never_creates_suffix_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "M-台中市-南區-TK3C-台中旗艦-940.jpg"
            make_image(source)
            output = root / "output"
            plan = plan_single_image(source, verified_result(source), "202601", "＄", current_year=2026)

            first = copy_planned_image_idempotent(plan, output)
            second = copy_planned_image_idempotent(plan, output)

            self.assertEqual(first["target_path"], second["target_path"])
            self.assertEqual(second["status"], "existing_same_bytes")
            self.assertEqual(len(list(output.glob("*.jpg"))), 1)
            self.assertFalse(any("_2" in path.stem for path in output.glob("*.jpg")))

    def test_upload_requires_exact_readback_before_receipt_and_legacy_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "M-台中市-南區-TK3C-台中旗艦-939.jpg"
            make_image(source)
            output = root / "output"
            job = enqueue_finalized_result(verified_result(source), output_dir=output)
            fake = FakeRclone()

            receipt = process_one_job(
                job,
                output_dir=output,
                rclone=Path("rclone.exe"),
                runner=fake,
            )

            self.assertEqual(fake.copy_calls, 1)
            self.assertIn("--local-encoding", fake.copy_command)
            self.assertEqual(fake.copy_command[fake.copy_command.index("--local-encoding") + 1], "None")
            self.assertEqual(receipt["drive_file_id"], "drive-test-id")
            self.assertTrue((output / "_drive_upload_stream" / "receipts" / f"{'a' * 64}.json").is_file())
            ledger = (output / "_drive_upload" / "drive_upload_uploaded.csv").read_text(encoding="utf-8-sig")
            self.assertIn(receipt["file_name"], ledger)

    def test_existing_confirmed_remote_is_idempotent_without_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "M-台中市-南區-TK3C-台中旗艦-938.jpg"
            make_image(source)
            output = root / "output"
            job = enqueue_finalized_result(verified_result(source), output_dir=output)
            payload = json.loads(job.read_text(encoding="utf-8"))
            published = copy_planned_image_idempotent(payload["plan"], output)
            local = Path(published["target_path"])
            fake = FakeRclone()
            fake.remote = {
                "Name": local.name,
                "Size": local.stat().st_size,
                "Hashes": {"MD5": md5_file(local)},
                "ID": "already-there",
            }

            receipt = process_one_job(
                job,
                output_dir=output,
                rclone=Path("rclone.exe"),
                runner=fake,
            )

            self.assertEqual(fake.copy_calls, 0)
            self.assertEqual(receipt["drive_file_id"], "already-there")

    def test_wrong_remote_hash_is_replaced_then_receipted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "M-台中市-南區-TK3C-台中旗艦-937.jpg"
            make_image(source)
            output = root / "output"
            job = enqueue_finalized_result(verified_result(source), output_dir=output)
            fake = FakeRclone()
            fake.remote = {
                "Name": "wrong.jpg",
                "Size": 1,
                "Hashes": {"MD5": "0" * 32},
                "ID": "wrong-object",
            }

            receipt = process_one_job(
                job,
                output_dir=output,
                rclone=Path("rclone.exe"),
                runner=fake,
            )

            self.assertEqual(fake.copy_calls, 1)
            self.assertEqual(receipt["drive_file_id"], "drive-test-id")
            self.assertTrue((output / "_drive_upload_stream" / "receipts" / f"{'a' * 64}.json").exists())

    def test_failed_post_replace_readback_never_writes_any_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "M-bad-readback-936.jpg"
            make_image(source)
            output = root / "output"
            job = enqueue_finalized_result(verified_result(source), output_dir=output)

            class NonUpdatingRclone(FakeRclone):
                def __call__(self, command, **kwargs):
                    if command[1] == "copyto":
                        self.copy_calls += 1
                        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
                    return super().__call__(command, **kwargs)

            fake = NonUpdatingRclone()
            fake.remote = {
                "Name": "wrong.jpg",
                "Size": 1,
                "Hashes": {"MD5": "0" * 32},
                "ID": "wrong-object",
            }

            with self.assertRaisesRegex(RuntimeError, "size and MD5"):
                process_one_job(
                    job,
                    output_dir=output,
                    rclone=Path("rclone.exe"),
                    readback_attempts=1,
                    runner=fake,
                )

            self.assertFalse((output / "_drive_upload_stream" / "receipts" / f"{'a' * 64}.json").exists())
            self.assertFalse((output / "_drive_upload" / "drive_upload_uploaded.csv").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)

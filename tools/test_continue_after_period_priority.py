import tempfile
import unittest
import csv
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from tools.continue_after_period_priority import ContinuationMonitor, MonitorConfig


class FakeProcess:
    def __init__(self, pid, polls=None, returncode=0):
        self.pid = pid
        self._polls = list(polls or [])
        self.returncode = returncode

    def poll(self):
        if self._polls:
            value = self._polls.pop(0)
            if value is not None:
                self.returncode = value
            return value
        return self.returncode


class ContinuationMonitorTests(unittest.TestCase):
    def config(self, root: Path) -> MonitorConfig:
        repo = root / "repo"
        output = root / "output"
        priority = output / "_ocr_staging" / "priority" / "202606_商化照片-202606_deadbeef"
        staging = output / "_ocr_staging" / "backfill"
        source = root / "source"
        source202601 = source / "商化照片-202601"
        digest = hashlib.sha1(str(source202601.resolve()).encode("utf-8")).hexdigest()[:8]
        target = staging / f"202601_商化照片-202601_{digest}"
        for path in (repo, output, priority, staging, target, source202601):
            path.mkdir(parents=True, exist_ok=True)
        (target / "one.jpg").write_bytes(b"x")
        input_csv = root / "input.csv"
        with input_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["period", "source_folder"])
            writer.writeheader()
            writer.writerow({"period": "202601", "source_folder": str(source202601)})
        return MonitorConfig(
            repo_root=repo,
            source_root=source.resolve(),
            output_dir=output,
            backend_url="http://127.0.0.1:5002",
            priority_dir=priority.resolve(),
            target_dir=target.resolve(),
            staging_root=staging.resolve(),
            input_csv=input_csv,
            output_csv=root / "output.csv",
            run_summary_csv=root / "summary.csv",
            log_path=root / "monitor.jsonl",
            runner_stdout=root / "runner.out",
            runner_stderr=root / "runner.err",
            receipt_path=root / "receipt.json",
            poll_seconds=10,
            timeout_minutes=10080,
            monitor_timeout_minutes=2880,
            no_progress_minutes=90,
        )

    @staticmethod
    def status(config, current, running, processed, total, pending=0):
        return {
            "version": "v19.45 (accuracy-first evidence contract)",
            "status_contract_version": "compact-v2",
            "accuracy_profile": "strict",
            "evidence_guard_revision": "20260717.42",
            "runtime_health_fuse": None,
            "current_relative_dir": str(current),
            "is_running": running,
            "stats": {
                "processed": processed,
                "success": processed,
                "verified": processed,
                "failed": 0,
                "review_required": 0,
                "verification_unknown": 0,
                "total": total,
            },
            "stream_upload": {
                "pending": pending,
                "working": 0,
                "worker_state": "running",
                "worker_pid": 999,
                "last_uploaded_at": "2026-07-17T12:00:00",
                "canonical_uploaded": 1,
            },
        }

    def test_running_priority_is_observed_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            calls = []

            def requester(_url, endpoint, payload=None, timeout=0):
                calls.append((endpoint, payload))
                return self.status(config, config.priority_dir, True, 10, 100)

            monitor = ContinuationMonitor(config, requester=requester, sleeper=lambda _n: None)
            with self.assertRaises(TimeoutError):
                monitor.run(max_polls=1)
            self.assertEqual(calls, [("/api/status", None)])

    def test_idle_incomplete_priority_resumes_without_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            calls = []
            started = False

            def requester(_url, endpoint, payload=None, timeout=0):
                nonlocal started
                calls.append((endpoint, payload))
                if endpoint == "/api/status":
                    return self.status(config, config.priority_dir, started, 10, 100)
                started = True
                return {"status": "started"}

            monitor = ContinuationMonitor(config, requester=requester, sleeper=lambda _n: None)
            with self.assertRaises(TimeoutError):
                monitor.run(max_polls=1)
            self.assertEqual(calls[1][0], "/api/start_batch")
            self.assertFalse(calls[1][1]["restart"])
            self.assertEqual(Path(calls[1][1]["dir"]), config.priority_dir)

    def test_complete_priority_waits_for_stream_upload_drain(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            calls = []

            def requester(_url, endpoint, payload=None, timeout=0):
                calls.append((endpoint, payload))
                return self.status(config, config.priority_dir, False, 100, 100, pending=2)

            monitor = ContinuationMonitor(config, requester=requester, sleeper=lambda _n: None)
            with self.assertRaises(TimeoutError):
                monitor.run(max_polls=1)
            self.assertEqual(calls, [("/api/status", None)])

    @patch("tools.continue_after_period_priority.psutil.pid_exists", return_value=True)
    def test_complete_priority_switches_and_launches_existing_runner(self, _pid_exists):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            states = [
                self.status(config, config.priority_dir, False, 1, 1),
                self.status(config, config.target_dir, True, 14, 1500),
                self.status(config, config.target_dir, True, 14, 1500),
            ]
            calls = []
            source_item_id = "a" * 64
            original = config.source_root / "商化照片-202606" / "one.jpg"
            original.parent.mkdir()
            original.write_bytes(b"source")
            source_sha = hashlib.sha256(original.read_bytes()).hexdigest()
            staged = config.priority_dir / "one.jpg"
            staged.write_bytes(b"staged-image")
            staged_sha = hashlib.sha256(staged.read_bytes()).hexdigest()
            published = config.output_dir / "M-202606-one.jpg"
            published.write_bytes(b"published")
            published_sha = hashlib.sha256(published.read_bytes()).hexdigest()
            receipt_dir = config.output_dir / "_drive_upload_stream" / "receipts"
            receipt_dir.mkdir(parents=True)
            (receipt_dir / f"{source_item_id}.json").write_text(
                json.dumps(
                    {
                        "schema": "samsung-ocr-stream-receipt-v1",
                        "source_item_id": source_item_id,
                        "original_source_path": str(original),
                        "published_path": str(published),
                        "source_sha256": source_sha,
                        "published_sha256": published_sha,
                        "period": "202606",
                        "evidence_guard_revision": "20260717.42",
                        "run_id": "run-one",
                        "drive_file_id": "drive-one",
                        "remote_path": "remote:2026/M-202606-one.jpg",
                    }
                ),
                encoding="utf-8",
            )

            def requester(_url, endpoint, payload=None, timeout=0):
                calls.append((endpoint, payload))
                if endpoint == "/api/status":
                    return states.pop(0)
                if endpoint == "/api/success_records":
                    return [
                        {
                            "file_name": "one.jpg",
                            "source_item_id": source_item_id,
                            "source_path": str(staged),
                            "original_source_path": str(original),
                            "input_image_sha256": staged_sha,
                            "evidence_guard_revision": "20260717.42",
                            "run_id": "run-one",
                            "auto_verified": True,
                            "auto_review_required": False,
                            "stream_upload_queued": True,
                        }
                    ]
                if endpoint == "/api/set_work_dir":
                    return {"status": "success"}
                if endpoint == "/api/start_batch":
                    return {"status": "started"}
                raise AssertionError(endpoint)

            def launcher(_config):
                config.output_csv.write_text("period\n202601\n", encoding="utf-8")
                with config.run_summary_csv.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(
                        handle,
                        fieldnames=[
                            "period",
                            "aborted",
                            "folder",
                            "staging_dir",
                            "queued",
                            "staged",
                            "processed",
                        ],
                    )
                    writer.writeheader()
                    writer.writerow(
                        {
                            "period": "202601",
                            "aborted": "0",
                            "folder": str(config.source_root / "商化照片-202601"),
                            "staging_dir": str(config.target_dir),
                            "queued": "1",
                            "staged": "1",
                            "processed": "1",
                        }
                    )
                return FakeProcess(4321, polls=[None, 0], returncode=0)

            monitor = ContinuationMonitor(
                config,
                requester=requester,
                sleeper=lambda _n: None,
                launcher=launcher,
                runner_finder=lambda _repo: [],
                wall_clock=lambda: 0,
            )
            self.assertEqual(monitor.run(max_polls=2), 4321)
            self.assertEqual(
                [item[0] for item in calls],
                [
                    "/api/status",
                    "/api/success_records",
                    "/api/set_work_dir",
                    "/api/start_batch",
                    "/api/status",
                    "/api/status",
                ],
            )
            self.assertTrue(config.receipt_path.is_file())

    @patch("tools.continue_after_period_priority.psutil.pid_exists", return_value=True)
    def test_receipt_must_hash_the_original_not_the_staging_copy(self, _pid_exists):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            source_item_id = "d" * 64
            original = config.source_root / "period-202606" / "one.jpg"
            original.parent.mkdir()
            original.write_bytes(b"original")
            staged = config.priority_dir / "one.jpg"
            staged.write_bytes(b"staging-copy")
            staged_sha = hashlib.sha256(staged.read_bytes()).hexdigest()
            published = config.output_dir / "M-202606-one.jpg"
            published.write_bytes(b"published")
            receipt_dir = config.output_dir / "_drive_upload_stream" / "receipts"
            receipt_dir.mkdir(parents=True)
            (receipt_dir / f"{source_item_id}.json").write_text(
                json.dumps(
                    {
                        "schema": "samsung-ocr-stream-receipt-v1",
                        "source_item_id": source_item_id,
                        "original_source_path": str(original),
                        "published_path": str(published),
                        # This deliberately uses the staging hash. It must fail.
                        "source_sha256": staged_sha,
                        "published_sha256": hashlib.sha256(
                            published.read_bytes()
                        ).hexdigest(),
                        "period": "202606",
                        "evidence_guard_revision": "20260717.42",
                        "run_id": "run-one",
                        "drive_file_id": "drive-one",
                        "remote_path": "remote:2026/M-202606-one.jpg",
                    }
                ),
                encoding="utf-8",
            )

            def requester(_url, endpoint, payload=None, timeout=0):
                if endpoint == "/api/status":
                    return self.status(config, config.priority_dir, False, 1, 1)
                if endpoint == "/api/success_records":
                    return [
                        {
                            "file_name": "one.jpg",
                            "source_item_id": source_item_id,
                            "source_path": str(staged),
                            "original_source_path": str(original),
                            "input_image_sha256": staged_sha,
                            "evidence_guard_revision": "20260717.42",
                            "run_id": "run-one",
                            "auto_verified": True,
                            "auto_review_required": False,
                            "stream_upload_queued": True,
                        }
                    ]
                raise AssertionError(endpoint)

            monitor = ContinuationMonitor(
                config,
                requester=requester,
                sleeper=lambda _n: None,
            )
            with self.assertRaisesRegex(RuntimeError, "Drive readback receipts"):
                monitor.run(max_polls=1)

    def test_unexpected_folder_fails_closed_without_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            unexpected = Path(tmp) / "other"
            unexpected.mkdir()

            def requester(_url, _endpoint, payload=None, timeout=0):
                return self.status(config, unexpected, False, 1, 1)

            monitor = ContinuationMonitor(config, requester=requester, sleeper=lambda _n: None)
            with self.assertRaisesRegex(RuntimeError, "unexpected backend work directory"):
                monitor.run(max_polls=1)

    def test_runtime_fuse_blocks_all_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            status = self.status(config, config.priority_dir, False, 100, 100)
            status["runtime_health_fuse"] = {"reason": "test"}

            monitor = ContinuationMonitor(
                config,
                requester=lambda *_args, **_kwargs: status,
                sleeper=lambda _n: None,
            )
            with self.assertRaisesRegex(RuntimeError, "runtime health fuse"):
                monitor.run(max_polls=1)

    def test_nonfinal_terminal_outcomes_block_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            status = self.status(config, config.priority_dir, False, 100, 100)
            status["stats"]["review_required"] = 1
            status["stats"]["verified"] = 99

            monitor = ContinuationMonitor(
                config,
                requester=lambda *_args, **_kwargs: status,
                sleeper=lambda _n: None,
            )
            with self.assertRaisesRegex(RuntimeError, "non-final terminal outcomes"):
                monitor.run(max_polls=1)

    @patch("tools.continue_after_period_priority.psutil.pid_exists", return_value=True)
    def test_missing_drive_receipt_blocks_switch(self, _pid_exists):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            source_item_id = "b" * 64
            status = self.status(config, config.priority_dir, False, 1, 1)

            def requester(_url, endpoint, payload=None, timeout=0):
                if endpoint == "/api/status":
                    return status
                if endpoint == "/api/success_records":
                    return [
                        {
                            "file_name": "one.jpg",
                            "source_item_id": source_item_id,
                            "original_source_path": "missing.jpg",
                            "input_image_sha256": "c" * 64,
                            "evidence_guard_revision": "20260717.42",
                            "run_id": "run-one",
                            "auto_verified": True,
                            "auto_review_required": False,
                            "stream_upload_queued": True,
                        }
                    ]
                raise AssertionError(endpoint)

            monitor = ContinuationMonitor(
                config,
                requester=requester,
                sleeper=lambda _n: None,
            )
            with self.assertRaisesRegex(RuntimeError, "Drive readback receipts"):
                monitor.run(max_polls=1)

    def test_stale_summary_path_blocks_new_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            config.run_summary_csv.write_text("period\n202601\n", encoding="utf-8")
            monitor = ContinuationMonitor(
                config,
                requester=lambda *_args, **_kwargs: self.status(
                    config, config.target_dir, True, 1, 100
                ),
                sleeper=lambda _n: None,
                runner_finder=lambda _repo: [],
            )
            with self.assertRaisesRegex(RuntimeError, "stale proof"):
                monitor.run(max_polls=1)

    def test_any_other_staged_runner_blocks_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            process = FakeProcess(123)
            process.cmdline = lambda: [
                str(config.repo_root / ".venv/Scripts/python.exe"),
                str(config.repo_root / "tools/rerun_staged_candidates.py"),
                "--attach-existing",
            ]
            monitor = ContinuationMonitor(
                config,
                requester=lambda *_args, **_kwargs: self.status(
                    config, config.target_dir, True, 1, 100
                ),
                sleeper=lambda _n: None,
                runner_finder=lambda _repo: [process],
            )
            with self.assertRaisesRegex(RuntimeError, "already active"):
                monitor.run(max_polls=1)


if __name__ == "__main__":
    unittest.main()

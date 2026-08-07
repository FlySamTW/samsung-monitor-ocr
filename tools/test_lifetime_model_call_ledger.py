import ast
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image

from skills.batch_orchestrator import BatchOrchestrator
from skills.model_call_ledger import (
    LifetimeModelCallBindingError,
    LifetimeModelCallCapReached,
    LifetimeModelCallLedger,
    build_source_image_binding,
)


def _source_id(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).casefold().encode("utf-8")).hexdigest()


def _write_image(path: Path, color: str) -> None:
    Image.new("RGB", (32, 24), color).save(path, quality=95)


def _binding(source_id: str, path: Path):
    raw_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    return build_source_image_binding(
        source_item_id=source_id,
        original_source_path=path,
        input_image_sha256=raw_hash,
    )


def _trace_row(
    *,
    source_id: str,
    source_path: Path,
    input_hash: str,
    run_id: str,
    attempt: int,
    lifetime_model_call_count: int | None = None,
) -> dict:
    row = {
        "trace_id": hashlib.sha256(
            f"{source_id}|{run_id}|{attempt}".encode("utf-8")
        ).hexdigest(),
        "source_item_id": source_id,
        "source_path": str(source_path.resolve()),
        "original_source_path": str(source_path.resolve()),
        "run_id": run_id,
        "attempt": attempt,
        "parsed_output": {
            "input_image_sha256": input_hash,
            "request_id_verified": True,
            "independent_pass": True,
        },
    }
    if lifetime_model_call_count is not None:
        row["parsed_output"]["lifetime_model_call_count"] = int(
            lifetime_model_call_count
        )
        row["parsed_output"]["model_call_reservation_id"] = (
            f"reservation-{lifetime_model_call_count}"
        )
    return row


class LifetimeModelCallLedgerTests(unittest.TestCase):
    def test_retry_checkpoint_without_attempt_map_honors_durable_cap_before_processor(self):
        """Regression: 高雄大遠百-1296 must not reappear as fake pass one."""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            staging = root / "staging"
            audit = root / "audit"
            assets = root / "assets"
            output = root / "output"
            for directory in (staging, audit, assets, output):
                directory.mkdir()
            original = root / "original.jpg"
            processing = staging / "M-高雄大遠百-1296.jpg"
            _write_image(original, "white")
            processing.write_bytes(original.read_bytes())
            source_id = _source_id(original)
            (staging / ".ocr_source_map.json").write_text(
                json.dumps(
                    {
                        "items": {
                            processing.name: {
                                "source_item_id": source_id,
                                "original_source_path": str(original.resolve()),
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            model_list = root / "models.txt"
            model_list.write_text("S32DG802SC\n", encoding="utf-8")

            ledger = LifetimeModelCallLedger(audit_dir=audit)
            binding = _binding(source_id, original)
            for attempt in (1, 2, 3):
                ledger.reserve(
                    binding=binding,
                    run_id="before-revalidation",
                    requested_attempt=attempt,
                    checkpoint_attempt=attempt - 1,
                    task_attempt=attempt - 1,
                    file_name=processing.name,
                )

            # Offline revalidation retained the retry filename but cleared the
            # staging-local attempt map.  This exact shape previously entered
            # the processor as a fake first pass and only failed at reserve().
            (staging / ".ocr_retry_queue.json").write_text(
                json.dumps(
                    {
                        "image_dir": str(staging.resolve()),
                        "priority_queue": [],
                        "retry_queue": [processing.name],
                        "auto_attempts": {},
                        "auto_result_history": {processing.name: []},
                        "runtime_health_incident_sources": {},
                        "request_binding_incident_events": [],
                    }
                ),
                encoding="utf-8",
            )

            processor_calls = []

            def processor(**kwargs):
                processor_calls.append(kwargs)
                raise AssertionError("processor must not run after lifetime call 3")

            orchestrator = BatchOrchestrator(
                {
                    "image_dir": str(staging),
                    "output_dir": str(output),
                    "audit_dir": str(audit),
                    "assets_dir": str(assets),
                    "model_list_file": str(model_list),
                }
            )
            orchestrator.set_processor_function(processor)
            self.assertTrue(orchestrator.start_batch())
            deadline = time.time() + 10
            while orchestrator.is_running and time.time() < deadline:
                time.sleep(0.05)

            self.assertFalse(orchestrator.is_running)
            self.assertEqual(processor_calls, [])
            self.assertEqual(orchestrator.retry_queue, [])
            checkpoint = json.loads(
                (staging / ".ocr_retry_queue.json").read_text(encoding="utf-8")
            )
            self.assertNotIn(processing.name, checkpoint["retry_queue"])
            self.assertEqual(
                checkpoint["auto_attempts"][processing.name],
                3,
            )

    def test_two_calls_then_restart_third_reservation_blocks_fourth(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audit = root / "audit"
            audit.mkdir()
            image = root / "photo.jpg"
            _write_image(image, "white")
            source_id = _source_id(image)
            binding = _binding(source_id, image)
            trace = audit / "v1945_evidence_trace.jsonl"
            trace.write_text(
                "\n".join(
                    json.dumps(
                        _trace_row(
                            source_id=source_id,
                            source_path=image,
                            input_hash=binding.input_image_sha256,
                            run_id="run-before-restart",
                            attempt=attempt,
                        )
                    )
                    for attempt in (1, 2)
                )
                + "\n",
                encoding="utf-8",
            )

            first_process = LifetimeModelCallLedger(
                audit_dir=audit,
                evidence_trace_path=trace,
            )
            third = first_process.reserve(
                binding=binding,
                run_id="run-after-restart",
                requested_attempt=1,
                checkpoint_attempt=0,
                task_attempt=0,
                file_name=image.name,
            )
            self.assertEqual(third["call_number"], 3)
            self.assertEqual(third["remaining_calls"], 0)

            restarted_process = LifetimeModelCallLedger(
                audit_dir=audit,
                evidence_trace_path=trace,
            )
            with self.assertRaises(LifetimeModelCallCapReached) as caught:
                restarted_process.reserve(
                    binding=binding,
                    run_id="run-second-restart",
                    requested_attempt=1,
                    file_name=image.name,
                )
            self.assertEqual(caught.exception.consumed_calls, 3)
            entry = json.loads(Path(third["ledger_path"]).read_text(encoding="utf-8"))
            self.assertEqual(entry["reserved_calls"], 3)
            self.assertEqual(len(entry["reservations"]), 1)

    def test_different_sources_do_not_share_call_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audit = root / "audit"
            first = root / "first.jpg"
            second = root / "second.jpg"
            _write_image(first, "red")
            _write_image(second, "blue")
            ledger = LifetimeModelCallLedger(audit_dir=audit)

            first_binding = _binding(_source_id(first), first)
            second_binding = _binding(_source_id(second), second)
            first_call = ledger.reserve(
                binding=first_binding,
                run_id="one",
                requested_attempt=1,
                file_name=first.name,
            )
            second_call = ledger.reserve(
                binding=second_binding,
                run_id="one",
                requested_attempt=1,
                file_name=second.name,
            )
            self.assertEqual(first_call["call_number"], 1)
            self.assertEqual(second_call["call_number"], 1)
            self.assertNotEqual(first_call["ledger_path"], second_call["ledger_path"])

    def test_same_source_with_changed_image_binding_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / "photo.jpg"
            _write_image(image, "white")
            source_id = _source_id(image)
            ledger = LifetimeModelCallLedger(audit_dir=root / "audit")
            original_binding = _binding(source_id, image)
            ledger.reserve(
                binding=original_binding,
                run_id="one",
                requested_attempt=1,
                file_name=image.name,
            )

            _write_image(image, "black")
            changed_binding = _binding(source_id, image)
            with self.assertRaises(LifetimeModelCallBindingError):
                ledger.reserve(
                    binding=changed_binding,
                    run_id="two",
                    requested_attempt=1,
                    file_name=image.name,
                )

    def test_missing_attempt_one_plus_task_attempt_three_blocks_new_call(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audit = root / "audit"
            audit.mkdir()
            image = root / "photo.jpg"
            _write_image(image, "white")
            source_id = _source_id(image)
            binding = _binding(source_id, image)
            trace = audit / "v1945_evidence_trace.jsonl"
            trace.write_text(
                "\n".join(
                    json.dumps(
                        _trace_row(
                            source_id=source_id,
                            source_path=image,
                            input_hash=binding.input_image_sha256,
                            run_id="missing-first-pass",
                            attempt=attempt,
                        )
                    )
                    for attempt in (2, 3)
                )
                + "\n",
                encoding="utf-8",
            )

            ledger = LifetimeModelCallLedger(
                audit_dir=audit,
                evidence_trace_path=trace,
            )
            with self.assertRaises(LifetimeModelCallCapReached):
                ledger.reserve(
                    binding=binding,
                    run_id="restart",
                    requested_attempt=1,
                    checkpoint_attempt=0,
                    task_attempt=3,
                    file_name=image.name,
                )
            entry_path = (
                audit
                / "model_call_lifetime_ledger_v1"
                / source_id[:2]
                / f"{source_id}.json"
            )
            entry = json.loads(entry_path.read_text(encoding="utf-8"))
            self.assertEqual(entry["reserved_calls"], 3)
            self.assertEqual(entry["bootstrap"]["task_attempt_floor"], 3)
            self.assertEqual(
                entry["bootstrap"]["trace_sequence_gaps"],
                ["missing-first-pass"],
            )

    def test_existing_trace_with_other_full_image_hash_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audit = root / "audit"
            audit.mkdir()
            image = root / "photo.jpg"
            _write_image(image, "white")
            source_id = _source_id(image)
            binding = _binding(source_id, image)
            trace = audit / "v1945_evidence_trace.jsonl"
            trace.write_text(
                json.dumps(
                    _trace_row(
                        source_id=source_id,
                        source_path=image,
                        input_hash="f" * 64,
                        run_id="old-binding",
                        attempt=1,
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            ledger = LifetimeModelCallLedger(
                audit_dir=audit,
                evidence_trace_path=trace,
            )
            with self.assertRaises(LifetimeModelCallBindingError):
                ledger.reserve(
                    binding=binding,
                    run_id="new-binding",
                    requested_attempt=1,
                    file_name=image.name,
                )

    def test_three_bound_calls_ignore_89_missing_hash_rows_and_clamp_cap(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audit = root / "audit"
            audit.mkdir()
            image = root / "photo.jpg"
            _write_image(image, "white")
            source_id = _source_id(image)
            binding = _binding(source_id, image)
            trace = audit / "v1945_evidence_trace.jsonl"
            rows = [
                _trace_row(
                    source_id=source_id,
                    source_path=image,
                    input_hash=binding.input_image_sha256,
                    run_id="fully-bound",
                    attempt=attempt,
                    lifetime_model_call_count=attempt,
                )
                for attempt in (1, 2, 3)
            ]
            for index in range(89):
                row = _trace_row(
                    source_id=source_id,
                    source_path=image,
                    input_hash="",
                    run_id=f"legacy-missing-hash-{index}",
                    attempt=1,
                )
                row["source_path"] = str(root / f"unavailable-{index}.jpg")
                rows.append(row)
            self.assertEqual(len(rows), 92)
            trace.write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n",
                encoding="utf-8",
            )

            ledger = LifetimeModelCallLedger(
                audit_dir=audit,
                evidence_trace_path=trace,
            )
            with self.assertRaises(LifetimeModelCallCapReached) as caught:
                ledger.reserve(
                    binding=binding,
                    run_id="restart",
                    requested_attempt=1,
                    file_name=image.name,
                )
            self.assertEqual(caught.exception.consumed_calls, 3)
            entry = json.loads(
                (
                    audit
                    / "model_call_lifetime_ledger_v1"
                    / source_id[:2]
                    / f"{source_id}.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(entry["reserved_calls"], 3)
            self.assertEqual(entry["bootstrap"]["distinct_trace_calls"], 92)
            self.assertEqual(entry["bootstrap"]["trace_fully_bound_calls"], 3)
            self.assertEqual(
                entry["bootstrap"]["trace_missing_input_hash_calls"],
                89,
            )
            self.assertEqual(
                entry["bootstrap"][
                    "trace_ignored_missing_hash_calls_after_terminal_cap"
                ],
                89,
            )

    def test_missing_hash_legacy_row_counts_when_exact_source_file_matches(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audit = root / "audit"
            audit.mkdir()
            image = root / "photo.jpg"
            legacy_source = root / "legacy-staging.jpg"
            _write_image(image, "white")
            legacy_source.write_bytes(image.read_bytes())
            source_id = _source_id(image)
            binding = _binding(source_id, image)
            row = _trace_row(
                source_id=source_id,
                source_path=image,
                input_hash="",
                run_id="legacy",
                attempt=1,
            )
            row["source_path"] = str(legacy_source.resolve())
            trace = audit / "v1945_evidence_trace.jsonl"
            trace.write_text(json.dumps(row) + "\n", encoding="utf-8")

            ledger = LifetimeModelCallLedger(
                audit_dir=audit,
                evidence_trace_path=trace,
            )
            reservation = ledger.reserve(
                binding=binding,
                run_id="restart",
                requested_attempt=1,
                file_name=image.name,
            )
            self.assertEqual(reservation["call_number"], 2)
            self.assertEqual(
                reservation["bootstrap"]["trace_safe_legacy_file_bound_calls"],
                1,
            )

    def test_missing_hash_legacy_row_without_source_path_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audit = root / "audit"
            audit.mkdir()
            image = root / "photo.jpg"
            _write_image(image, "white")
            source_id = _source_id(image)
            binding = _binding(source_id, image)
            row = _trace_row(
                source_id=source_id,
                source_path=image,
                input_hash="",
                run_id="legacy",
                attempt=1,
            )
            row.pop("source_path")
            trace = audit / "v1945_evidence_trace.jsonl"
            trace.write_text(json.dumps(row) + "\n", encoding="utf-8")

            ledger = LifetimeModelCallLedger(
                audit_dir=audit,
                evidence_trace_path=trace,
            )
            with self.assertRaises(LifetimeModelCallBindingError):
                ledger.reserve(
                    binding=binding,
                    run_id="restart",
                    requested_attempt=1,
                    file_name=image.name,
                )

    def test_missing_hash_legacy_row_with_different_source_bytes_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audit = root / "audit"
            audit.mkdir()
            image = root / "photo.jpg"
            legacy_source = root / "legacy-staging.jpg"
            _write_image(image, "white")
            _write_image(legacy_source, "black")
            source_id = _source_id(image)
            binding = _binding(source_id, image)
            row = _trace_row(
                source_id=source_id,
                source_path=image,
                input_hash="",
                run_id="legacy",
                attempt=1,
            )
            row["source_path"] = str(legacy_source.resolve())
            trace = audit / "v1945_evidence_trace.jsonl"
            trace.write_text(json.dumps(row) + "\n", encoding="utf-8")

            ledger = LifetimeModelCallLedger(
                audit_dir=audit,
                evidence_trace_path=trace,
            )
            with self.assertRaises(LifetimeModelCallBindingError):
                ledger.reserve(
                    binding=binding,
                    run_id="restart",
                    requested_attempt=1,
                    file_name=image.name,
                )

    def test_global_trace_count_is_not_added_again_to_legacy_run_attempts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audit = root / "audit"
            audit.mkdir()
            image = root / "photo.jpg"
            _write_image(image, "white")
            source_id = _source_id(image)
            binding = _binding(source_id, image)
            trace = audit / "v1945_evidence_trace.jsonl"
            rows = [
                _trace_row(
                    source_id=source_id,
                    source_path=image,
                    input_hash=binding.input_image_sha256,
                    run_id="legacy-run",
                    attempt=1,
                ),
                _trace_row(
                    source_id=source_id,
                    source_path=image,
                    input_hash=binding.input_image_sha256,
                    run_id="legacy-run",
                    attempt=2,
                ),
                _trace_row(
                    source_id=source_id,
                    source_path=image,
                    input_hash=binding.input_image_sha256,
                    run_id="ledger-run",
                    attempt=3,
                    lifetime_model_call_count=3,
                ),
            ]
            trace.write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n",
                encoding="utf-8",
            )
            ledger = LifetimeModelCallLedger(
                audit_dir=audit,
                evidence_trace_path=trace,
            )

            with self.assertRaises(LifetimeModelCallCapReached) as caught:
                ledger.reserve(
                    binding=binding,
                    run_id="restart",
                    requested_attempt=1,
                    file_name=image.name,
                )
            self.assertEqual(caught.exception.consumed_calls, 3)
            entry = json.loads(
                (
                    audit
                    / "model_call_lifetime_ledger_v1"
                    / source_id[:2]
                    / f"{source_id}.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(entry["reserved_calls"], 3)
            self.assertEqual(
                entry["bootstrap"]["trace_legacy_consumed_floor"],
                2,
            )
            self.assertEqual(
                entry["bootstrap"]["trace_lifetime_call_floor"],
                3,
            )

    def test_orchestrator_reservation_updates_checkpoint_after_ledger_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            staging = root / "staging"
            audit = root / "audit"
            assets = root / "assets"
            staging.mkdir()
            audit.mkdir()
            assets.mkdir()
            original = root / "original.jpg"
            processing = staging / "photo.jpg"
            _write_image(original, "white")
            processing.write_bytes(original.read_bytes())
            source_id = _source_id(original)
            (staging / ".ocr_source_map.json").write_text(
                json.dumps(
                    {
                        "items": {
                            processing.name: {
                                "source_item_id": source_id,
                                "original_source_path": str(original.resolve()),
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            model_list = root / "models.txt"
            model_list.write_text("S27D300GAC\n", encoding="utf-8")
            orchestrator = BatchOrchestrator(
                {
                    "image_dir": str(staging),
                    "output_dir": str(root / "output"),
                    "audit_dir": str(audit),
                    "evidence_trace_path": str(
                        audit / "v1945_evidence_trace.jsonl"
                    ),
                    "assets_dir": str(assets),
                    "model_list_file": str(model_list),
                }
            )
            orchestrator.active_image_dir = str(staging)
            orchestrator.source_metadata_map = (
                orchestrator._load_source_metadata_map(str(staging))
            )
            orchestrator.current_run_id = "integration-test"
            input_hash = hashlib.sha256(processing.read_bytes()).hexdigest()

            reservations = [
                orchestrator.reserve_actual_model_call(
                    filename=processing.name,
                    input_image_sha256=input_hash,
                    requested_attempt=attempt,
                )
                for attempt in (1, 2, 3)
            ]
            self.assertEqual(
                [item["call_number"] for item in reservations],
                [1, 2, 3],
            )
            checkpoint = json.loads(
                (staging / ".ocr_retry_queue.json").read_text(encoding="utf-8")
            )
            self.assertEqual(checkpoint["auto_attempts"][processing.name], 3)
            ledger_entry = json.loads(
                Path(reservations[-1]["ledger_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(ledger_entry["reserved_calls"], 3)

            with self.assertRaises(LifetimeModelCallCapReached):
                orchestrator.reserve_actual_model_call(
                    filename=processing.name,
                    input_image_sha256=input_hash,
                    requested_attempt=4,
                )

    def test_direct_attempt_three_bootstraps_two_missing_prior_slots(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            staging = root / "staging"
            audit = root / "audit"
            assets = root / "assets"
            staging.mkdir()
            audit.mkdir()
            assets.mkdir()
            image = staging / "photo.jpg"
            _write_image(image, "white")
            source_id = _source_id(image)
            (staging / ".ocr_source_map.json").write_text(
                json.dumps(
                    {
                        "items": {
                            image.name: {
                                "source_item_id": source_id,
                                "original_source_path": str(image.resolve()),
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            model_list = root / "models.txt"
            model_list.write_text("S27D300GAC\n", encoding="utf-8")
            orchestrator = BatchOrchestrator(
                {
                    "image_dir": str(staging),
                    "output_dir": str(root / "output"),
                    "audit_dir": str(audit),
                    "evidence_trace_path": str(
                        audit / "v1945_evidence_trace.jsonl"
                    ),
                    "assets_dir": str(assets),
                    "model_list_file": str(model_list),
                }
            )
            orchestrator.active_image_dir = str(staging)
            orchestrator.source_metadata_map = (
                orchestrator._load_source_metadata_map(str(staging))
            )
            input_hash = hashlib.sha256(image.read_bytes()).hexdigest()

            reservation = orchestrator.reserve_actual_model_call(
                filename=image.name,
                input_image_sha256=input_hash,
                requested_attempt=3,
            )
            self.assertEqual(reservation["call_number"], 3)
            self.assertEqual(reservation["remaining_calls"], 0)
            self.assertEqual(
                reservation["bootstrap"]["task_attempt_floor"],
                2,
            )
            with self.assertRaises(LifetimeModelCallCapReached):
                orchestrator.reserve_actual_model_call(
                    filename=image.name,
                    input_image_sha256=input_hash,
                    requested_attempt=3,
                )

    def test_lm_studio_call_path_has_one_write_ahead_reservation_and_no_hidden_retry(
        self,
    ):
        source_path = Path(__file__).resolve().parents[1] / (
            "samsung_ocr_batch_processor.py"
        )
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        create_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "completions"
        ]
        reservation_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "reserve_actual_model_call"
        ]
        self.assertEqual(len(create_calls), 1)
        self.assertEqual(len(reservation_calls), 1)
        self.assertLess(reservation_calls[0].lineno, create_calls[0].lineno)
        self.assertLessEqual(
            create_calls[0].lineno - reservation_calls[0].lineno,
            12,
        )
        self.assertNotIn('os.environ.get("OCR_MAX_RETRIES"', source)

        openai_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "OpenAI"
        ]
        self.assertGreaterEqual(len(openai_calls), 1)
        for openai_call in openai_calls:
            max_retry_keyword = next(
                (
                    keyword
                    for keyword in openai_call.keywords
                    if keyword.arg == "max_retries"
                ),
                None,
            )
            self.assertIsNotNone(max_retry_keyword)
            self.assertIsInstance(max_retry_keyword.value, ast.Constant)
            self.assertEqual(max_retry_keyword.value.value, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

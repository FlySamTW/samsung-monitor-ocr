"""Crash-safe lifetime model-call budget for one bound source photo.

The staging retry checkpoint is intentionally local to one work directory and
therefore cannot enforce the permanent three-call rule across a restart,
revision, or rebuilt staging directory.  This module stores one small,
source-sharded write-ahead ledger below the global audit directory.

Every reservation is persisted *before* the caller may invoke LM Studio.  A
crash after reservation can conservatively consume a slot, but can never make
a hidden fourth call possible.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


LEDGER_SCHEMA = "samsung-ocr-lifetime-model-call-ledger/v1"
MAX_LIFETIME_MODEL_CALLS = 3
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PROCESS_LOCK = threading.RLock()


class LifetimeModelCallLedgerError(RuntimeError):
    """Base class for fail-closed lifetime-ledger errors."""


class LifetimeModelCallBindingError(LifetimeModelCallLedgerError):
    """The same source identity was observed with a different image binding."""


class LifetimeModelCallCapReached(LifetimeModelCallLedgerError):
    """The bound source has no remaining LM Studio call slot."""

    def __init__(self, source_item_id: str, consumed_calls: int, detail: str = ""):
        self.source_item_id = str(source_item_id)
        self.consumed_calls = max(0, int(consumed_calls))
        self.detail = str(detail or "")
        super().__init__(
            f"lifetime three-call cap reached for {self.source_item_id}: "
            f"{self.consumed_calls} consumed"
            + (f" ({self.detail})" if self.detail else "")
        )


@dataclass(frozen=True)
class SourceImageBinding:
    """Stable source identity plus both durable and request-image hashes."""

    source_item_id: str
    original_source_path: str
    source_file_sha256: str
    input_image_sha256: str

    @property
    def binding_key(self) -> str:
        seed = (
            f"{self.source_item_id}|{self.source_file_sha256}|"
            f"{self.input_image_sha256}"
        )
        return hashlib.sha256(seed.encode("ascii")).hexdigest()

    def as_dict(self) -> dict[str, str]:
        return {
            "source_item_id": self.source_item_id,
            "original_source_path": self.original_source_path,
            "source_file_sha256": self.source_file_sha256,
            "input_image_sha256": self.input_image_sha256,
            "binding_key": self.binding_key,
        }


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normal_path(value: object) -> str:
    try:
        return os.path.normcase(str(Path(str(value or "")).resolve()))
    except (OSError, ValueError):
        return os.path.normcase(str(value or ""))


def build_source_image_binding(
    *,
    source_item_id: object,
    original_source_path: str | Path,
    input_image_sha256: object,
) -> SourceImageBinding:
    source_id = str(source_item_id or "").strip().lower()
    input_hash = str(input_image_sha256 or "").strip().lower()
    original = Path(original_source_path).resolve()
    if not _SHA256_RE.fullmatch(source_id):
        raise LifetimeModelCallBindingError("source_item_id is not one SHA-256")
    if not original.is_file():
        raise LifetimeModelCallBindingError(
            f"original source image is unavailable: {original}"
        )
    if not _SHA256_RE.fullmatch(input_hash):
        raise LifetimeModelCallBindingError(
            "prepared full-image binding is not one SHA-256"
        )
    return SourceImageBinding(
        source_item_id=source_id,
        original_source_path=str(original),
        source_file_sha256=sha256_file(original),
        input_image_sha256=input_hash,
    )


@contextmanager
def _exclusive_file_lock(path: Path, timeout_seconds: float = 15.0) -> Iterator[None]:
    """Cross-process lock using a separate stable lock file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.1, float(timeout_seconds))
    with _PROCESS_LOCK:
        with path.open("a+b", buffering=0) as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())

            acquired = False
            while not acquired:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except (OSError, BlockingIOError):
                    if time.monotonic() >= deadline:
                        raise LifetimeModelCallLedgerError(
                            f"timed out acquiring lifetime ledger lock: {path}"
                        )
                    time.sleep(0.025)
            try:
                yield
            finally:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


class LifetimeModelCallLedger:
    """Source-sharded, globally durable three-call reservation ledger."""

    def __init__(
        self,
        *,
        audit_dir: str | Path,
        evidence_trace_path: str | Path | None = None,
    ) -> None:
        self.audit_dir = Path(audit_dir).resolve()
        self.root = self.audit_dir / "model_call_lifetime_ledger_v1"
        self.evidence_trace_path = Path(
            evidence_trace_path
            or self.audit_dir / "v1945_evidence_trace.jsonl"
        ).resolve()
        self._trace_index_lock = threading.RLock()
        self._trace_index_loaded = False
        self._trace_index: dict[str, list[dict[str, Any]]] = {}
        self._malformed_trace_sources: set[str] = set()

    def _entry_path(self, source_item_id: str) -> Path:
        return self.root / source_item_id[:2] / f"{source_item_id}.json"

    def _lock_path(self, source_item_id: str) -> Path:
        return self.root / source_item_id[:2] / f".{source_item_id}.lock"

    def consumed_calls(self, source_item_id: object) -> int:
        """Return the durable reservation count without reserving another call.

        Retry checkpoints may legitimately lose their local ``auto_attempts``
        entry while still retaining the filename in ``retry_queue`` (for
        example after an offline revalidation).  The orchestrator must be able
        to reconcile that queue against the lifetime ledger *before* invoking
        the processor.  This read-only probe intentionally does not bootstrap
        from the trace and never creates or mutates a ledger entry; ``reserve``
        remains the sole write-ahead authority for a new model call.
        """

        source_id = str(source_item_id or "").strip().lower()
        if not _SHA256_RE.fullmatch(source_id):
            raise LifetimeModelCallBindingError(
                "source_item_id is not one SHA-256"
            )
        path = self._entry_path(source_id)
        if not path.is_file():
            return 0
        with _exclusive_file_lock(self._lock_path(source_id)):
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
                reserved = int(payload.get("reserved_calls"))
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise LifetimeModelCallLedgerError(
                    f"invalid lifetime ledger entry for {source_id}: {exc}"
                ) from exc
            if (
                payload.get("schema") != LEDGER_SCHEMA
                or str(payload.get("source_item_id") or "").strip().lower()
                != source_id
                or reserved < 0
                or reserved > MAX_LIFETIME_MODEL_CALLS
            ):
                raise LifetimeModelCallLedgerError(
                    f"invalid lifetime ledger entry for {source_id}"
                )
            return reserved

    @staticmethod
    def _call_identity(row: dict[str, Any], raw_line: str) -> str:
        trace_id = str(row.get("trace_id") or "").strip().lower()
        if _SHA256_RE.fullmatch(trace_id):
            return f"trace:{trace_id}"
        parsed = row.get("parsed_output") or {}
        request_id = str(parsed.get("request_id") or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{32}", request_id):
            return f"request:{request_id}"
        return "line:" + hashlib.sha256(raw_line.encode("utf-8")).hexdigest()

    def _ensure_trace_index(self) -> None:
        with self._trace_index_lock:
            if self._trace_index_loaded:
                return
            index: dict[str, list[dict[str, Any]]] = {}
            malformed_sources: set[str] = set()
            if self.evidence_trace_path.is_file():
                with self.evidence_trace_path.open(
                    "r", encoding="utf-8-sig", errors="strict"
                ) as handle:
                    for line in handle:
                        raw_line = line.rstrip("\r\n")
                        if not raw_line.strip():
                            continue
                        try:
                            row = json.loads(raw_line)
                        except (TypeError, ValueError, json.JSONDecodeError):
                            for source_id in re.findall(
                                r'"source_item_id"\s*:\s*"([0-9a-fA-F]{64})"',
                                raw_line,
                            ):
                                malformed_sources.add(source_id.lower())
                            continue
                        if not isinstance(row, dict):
                            continue
                        source_id = str(
                            row.get("source_item_id")
                            or row.get("source_identity")
                            or ""
                        ).strip().lower()
                        if not _SHA256_RE.fullmatch(source_id):
                            continue
                        parsed = row.get("parsed_output") or {}
                        index.setdefault(source_id, []).append(
                            {
                                "call_id": self._call_identity(row, raw_line),
                                "run_id": str(row.get("run_id") or "").strip(),
                                "attempt": row.get("attempt"),
                                "source_path": str(row.get("source_path") or ""),
                                "original_source_path": str(
                                    row.get("original_source_path") or ""
                                ),
                                "source_file_sha256": str(
                                    row.get("source_file_sha256")
                                    or row.get("source_sha256")
                                    or parsed.get("source_file_sha256")
                                    or parsed.get("source_sha256")
                                    or ""
                                ).strip().lower(),
                                "input_image_sha256": str(
                                    parsed.get("input_image_sha256") or ""
                                ).strip().lower(),
                                "lifetime_model_call_count": parsed.get(
                                    "lifetime_model_call_count"
                                ),
                                "model_call_reservation_id": str(
                                    parsed.get("model_call_reservation_id") or ""
                                ).strip().lower(),
                                "trace_id": str(row.get("trace_id") or ""),
                            }
                        )
            self._trace_index = index
            self._malformed_trace_sources = malformed_sources
            self._trace_index_loaded = True

    def _trace_consumed_floor(
        self, binding: SourceImageBinding
    ) -> tuple[int, dict[str, Any]]:
        self._ensure_trace_index()
        source_id = binding.source_item_id
        if source_id in self._malformed_trace_sources:
            raise LifetimeModelCallBindingError(
                "evidence trace for this source contains malformed JSON"
            )

        rows = list(self._trace_index.get(source_id, []))
        unique: dict[str, dict[str, Any]] = {}
        binding_conflicts: set[str] = set()
        for row in rows:
            call_id = str(row.get("call_id") or "")
            if call_id:
                unique.setdefault(call_id, row)

        bound_rows: list[dict[str, Any]] = []
        missing_input_hash_rows: list[dict[str, Any]] = []
        for row in unique.values():
            original = str(row.get("original_source_path") or "")
            if original and _normal_path(original) != _normal_path(
                binding.original_source_path
            ):
                binding_conflicts.add("original_source_path_mismatch")
            source_hash = str(row.get("source_file_sha256") or "")
            if source_hash and source_hash != binding.source_file_sha256:
                binding_conflicts.add("source_file_sha256_mismatch")
            input_hash = str(row.get("input_image_sha256") or "")
            if not _SHA256_RE.fullmatch(input_hash):
                missing_input_hash_rows.append(row)
            elif input_hash != binding.input_image_sha256:
                binding_conflicts.add("input_image_sha256_mismatch")
            else:
                bound_rows.append(row)
        if binding_conflicts:
            raise LifetimeModelCallBindingError(
                "existing trace cannot be bound to the current source: "
                + ",".join(sorted(binding_conflicts))
            )

        # Once three distinct calls are already fully bound to this exact
        # request image, the lifetime budget is conclusively exhausted.  Older
        # rows without an input-image hash remain visible in bootstrap
        # metadata, but must not turn that terminal fact into a migration
        # failure or inflate the persisted reservation count above the hard
        # three-call maximum.
        safely_file_bound_legacy_rows: list[dict[str, Any]] = []
        if len(bound_rows) < MAX_LIFETIME_MODEL_CALLS:
            for row in missing_input_hash_rows:
                source_path_text = str(row.get("source_path") or "").strip()
                if not source_path_text:
                    binding_conflicts.add("trace_source_path_missing")
                    continue
                source_path = Path(source_path_text)
                try:
                    source_path_matches = (
                        source_path.is_file()
                        and sha256_file(source_path) == binding.source_file_sha256
                    )
                except OSError:
                    source_path_matches = False
                if not source_path_matches:
                    binding_conflicts.add("trace_source_path_sha256_mismatch")
                    continue
                safely_file_bound_legacy_rows.append(row)
            if binding_conflicts:
                raise LifetimeModelCallBindingError(
                    "existing trace cannot be bound to the current source: "
                    + ",".join(sorted(binding_conflicts))
                )

        trusted_unique = {
            str(row.get("call_id") or ""): row
            for row in (*bound_rows, *safely_file_bound_legacy_rows)
            if str(row.get("call_id") or "")
        }
        lifetime_floors: list[int] = []
        legacy_rows: list[dict[str, Any]] = []
        for row in trusted_unique.values():
            raw_lifetime_count = row.get("lifetime_model_call_count")
            if raw_lifetime_count in (None, ""):
                legacy_rows.append(row)
                continue
            try:
                lifetime_count = int(raw_lifetime_count)
            except (TypeError, ValueError) as exc:
                raise LifetimeModelCallLedgerError(
                    "evidence trace lifetime model-call count is invalid"
                ) from exc
            if lifetime_count <= 0:
                raise LifetimeModelCallLedgerError(
                    "evidence trace lifetime model-call count is invalid"
                )
            lifetime_floors.append(lifetime_count)

        # Legacy attempts restarted from 1 in each run, so their conservative
        # floor is summed by run.  New traces carry an explicit lifetime count;
        # that value already includes the legacy bootstrap and must be combined
        # with max(), never added again (1+2 from an old run plus global call 3
        # is three calls, not six).
        by_run: dict[str, list[dict[str, Any]]] = {}
        for row in legacy_rows:
            run_id = str(row.get("run_id") or "") or "__missing_run__"
            by_run.setdefault(run_id, []).append(row)

        legacy_consumed_floor = 0
        run_floors: dict[str, int] = {}
        sequence_gaps: list[str] = []
        for run_id, run_rows in by_run.items():
            attempts: list[int] = []
            for row in run_rows:
                try:
                    attempt = int(row.get("attempt") or 0)
                except (TypeError, ValueError):
                    attempt = 0
                if attempt > 0:
                    attempts.append(attempt)
            highest = max(attempts, default=0)
            floor = max(len(run_rows), highest)
            run_floors[run_id] = floor
            legacy_consumed_floor += floor
            if highest and sorted(set(attempts)) != list(range(1, highest + 1)):
                sequence_gaps.append(run_id)

        lifetime_floor = max(lifetime_floors, default=0)
        observed_consumed_floor = max(
            len(trusted_unique),
            legacy_consumed_floor,
            lifetime_floor,
        )
        consumed_floor = min(
            MAX_LIFETIME_MODEL_CALLS,
            observed_consumed_floor,
        )
        return consumed_floor, {
            "distinct_trace_calls": len(unique),
            "trace_fully_bound_calls": len(bound_rows),
            "trace_missing_input_hash_calls": len(missing_input_hash_rows),
            "trace_safe_legacy_file_bound_calls": len(
                safely_file_bound_legacy_rows
            ),
            "trace_ignored_missing_hash_calls_after_terminal_cap": (
                len(missing_input_hash_rows)
                if len(bound_rows) >= MAX_LIFETIME_MODEL_CALLS
                else 0
            ),
            "trace_observed_consumed_floor": observed_consumed_floor,
            "trace_consumed_floor": consumed_floor,
            "trace_legacy_consumed_floor": legacy_consumed_floor,
            "trace_lifetime_call_floor": lifetime_floor,
            "trace_runs": run_floors,
            "trace_sequence_gaps": sorted(sequence_gaps),
        }

    @staticmethod
    def _validate_entry_binding(
        entry: dict[str, Any], binding: SourceImageBinding
    ) -> None:
        if entry.get("schema") != LEDGER_SCHEMA:
            raise LifetimeModelCallLedgerError("lifetime ledger schema mismatch")
        expected = binding.as_dict()
        for field in (
            "source_item_id",
            "original_source_path",
            "source_file_sha256",
            "input_image_sha256",
            "binding_key",
        ):
            actual = str(entry.get(field) or "")
            wanted = str(expected[field])
            if field == "original_source_path":
                matches = _normal_path(actual) == _normal_path(wanted)
            else:
                matches = actual == wanted
            if not matches:
                raise LifetimeModelCallBindingError(
                    f"lifetime ledger binding mismatch: {field}"
                )

    def reserve(
        self,
        *,
        binding: SourceImageBinding,
        run_id: object,
        requested_attempt: int,
        checkpoint_attempt: int = 0,
        task_attempt: int = 0,
        file_name: object = "",
    ) -> dict[str, Any]:
        """Atomically reserve and consume the next lifetime model-call slot."""

        source_id = binding.source_item_id
        entry_path = self._entry_path(source_id)
        lock_path = self._lock_path(source_id)
        with _exclusive_file_lock(lock_path):
            if entry_path.is_file():
                try:
                    entry = json.loads(entry_path.read_text(encoding="utf-8-sig"))
                except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                    raise LifetimeModelCallLedgerError(
                        f"lifetime ledger is unreadable: {entry_path}"
                    ) from exc
                if not isinstance(entry, dict):
                    raise LifetimeModelCallLedgerError(
                        "lifetime ledger entry is not an object"
                    )
                self._validate_entry_binding(entry, binding)
                trace_meta = dict(entry.get("bootstrap") or {})
            else:
                trace_floor, trace_meta = self._trace_consumed_floor(binding)
                entry = {
                    "schema": LEDGER_SCHEMA,
                    **binding.as_dict(),
                    "max_calls": MAX_LIFETIME_MODEL_CALLS,
                    "reserved_calls": 0,
                    "reservations": [],
                    "bootstrap": trace_meta,
                    "created_at": time.strftime(
                        "%Y-%m-%dT%H:%M:%S%z", time.localtime()
                    ),
                }
                observed_bootstrap_floor = max(
                    0,
                    int(trace_floor),
                    int(checkpoint_attempt or 0),
                    int(task_attempt or 0),
                )
                entry["reserved_calls"] = min(
                    MAX_LIFETIME_MODEL_CALLS,
                    observed_bootstrap_floor,
                )
                entry["bootstrap"].update(
                    {
                        "observed_bootstrap_floor": observed_bootstrap_floor,
                        "checkpoint_attempt_floor": max(
                            0, int(checkpoint_attempt or 0)
                        ),
                        "task_attempt_floor": max(0, int(task_attempt or 0)),
                    }
                )

            try:
                consumed = max(
                    0,
                    int(entry.get("reserved_calls") or 0),
                    int(checkpoint_attempt or 0),
                    int(task_attempt or 0),
                )
            except (TypeError, ValueError) as exc:
                raise LifetimeModelCallLedgerError(
                    "lifetime ledger call count is invalid"
                ) from exc
            entry["reserved_calls"] = consumed
            entry["updated_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%S%z", time.localtime()
            )

            if consumed >= MAX_LIFETIME_MODEL_CALLS:
                # Persist a trace/checkpoint bootstrap even when no new call is
                # allowed, so the next restart cannot reinterpret missing trace
                # attempt 1 as fresh capacity.
                _atomic_json(entry_path, entry)
                raise LifetimeModelCallCapReached(
                    source_id,
                    consumed,
                    detail="trace/checkpoint/ledger lifetime floor",
                )

            call_number = consumed + 1
            reservation = {
                "reservation_id": uuid.uuid4().hex,
                "call_number": call_number,
                "run_id": str(run_id or ""),
                "file_name": str(file_name or ""),
                "requested_attempt": max(1, int(requested_attempt or 1)),
                "checkpoint_attempt": max(0, int(checkpoint_attempt or 0)),
                "task_attempt": max(0, int(task_attempt or 0)),
                "reserved_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%S%z", time.localtime()
                ),
                "write_ahead_consumed": True,
            }
            reservations = list(entry.get("reservations") or [])
            reservations.append(reservation)
            entry["reservations"] = reservations[-MAX_LIFETIME_MODEL_CALLS:]
            entry["reserved_calls"] = call_number
            entry["updated_at"] = reservation["reserved_at"]
            _atomic_json(entry_path, entry)
            return {
                **reservation,
                "source_item_id": source_id,
                "binding_key": binding.binding_key,
                "consumed_calls": call_number,
                "remaining_calls": MAX_LIFETIME_MODEL_CALLS - call_number,
                "ledger_path": str(entry_path),
                "bootstrap": trace_meta,
            }

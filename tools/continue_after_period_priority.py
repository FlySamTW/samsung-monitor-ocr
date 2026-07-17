#!/usr/bin/env python3
"""Safely hand a completed priority period back to a preserved staged rerun.

This monitor owns no OCR backend and never restarts the model.  It only:

1. keeps the exact priority staging folder running if it becomes idle early;
2. waits for that folder and its streaming upload queue to finish;
3. switches the existing backend to the exact preserved target staging folder;
4. starts ``rerun_staged_candidates.py --resume-existing-then-continue`` hidden.

Every path is explicit and any unexpected runtime state fails closed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import psutil
from PIL import Image, ImageOps


MIN_EVIDENCE_GUARD = (20260717, 42)


def utcish_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def append_event(path: Path, event: str, **values: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"timestamp": utcish_now(), "event": event, "pid": os.getpid(), **values}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def normalized(path: str | Path) -> Path:
    return Path(path).resolve()


def guard_revision(value: Any) -> tuple[int, int]:
    parts = str(value or "").strip().split(".")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return (0, 0)
    return int(parts[0]), int(parts[1])


def request_json(
    backend_url: str,
    endpoint: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    data = None
    headers: dict[str, str] = {}
    method = "GET"
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
        method = "POST"
    request = urllib.request.Request(
        backend_url.rstrip("/") + endpoint,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{endpoint} returned HTTP {exc.code}: {detail}") from exc


def owned_runner_processes(repo_root: Path) -> list[psutil.Process]:
    del repo_root  # Any staged runner can mutate the one shared backend.
    matches: list[psutil.Process] = []
    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            command = " ".join(process.info.get("cmdline") or [])
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
        folded = command.casefold()
        process_name = str(process.info.get("name") or "").casefold()
        if (
            "rerun_staged_candidates.py" in folded
            and process_name.startswith(("python", "py.exe"))
        ):
            matches.append(process)
    return matches


def image_count(folder: Path) -> int:
    return sum(
        1
        for item in folder.iterdir()
        if item.is_file() and item.suffix.casefold() in {".jpg", ".jpeg", ".png"}
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepared_input_sha256(path: Path) -> str:
    """Reproduce the exact full-scene bytes hashed before a model request.

    Production keeps raw source bytes when the image is already within the
    2560-pixel long-edge bound.  Larger images are EXIF-oriented, resized with
    Pillow's thumbnail implementation, converted to RGB, and encoded as JPEG
    quality 95.  This hash is intentionally distinct from the source-file hash
    stored in the Drive receipt.
    """
    with Image.open(path) as image:
        oriented = ImageOps.exif_transpose(image)
        if max(oriented.width, oriented.height) <= 2560:
            return sha256_file(path)
        prepared = oriented.copy()
        prepared.thumbnail((2560, 2560))
        import io

        buffer = io.BytesIO()
        prepared.convert("RGB").save(buffer, format="JPEG", quality=95)
        return hashlib.sha256(buffer.getvalue()).hexdigest()


def candidate_groups(input_csv: Path, source_root: Path) -> list[tuple[str, Path, int]]:
    groups: list[tuple[str, Path, int]] = []
    counts: dict[tuple[str, str], int] = {}
    order: list[tuple[str, str]] = []
    with input_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            period = str(row.get("period") or "").strip()
            source_folder_text = str(row.get("source_folder") or "").strip()
            if not re.fullmatch(r"20\d{4}", period) or not source_folder_text:
                raise RuntimeError("candidate CSV contains an invalid period/source_folder row")
            source_folder = normalized(source_folder_text)
            try:
                source_folder.relative_to(source_root)
            except ValueError as exc:
                raise RuntimeError(
                    f"candidate source folder is outside source root: {source_folder}"
                ) from exc
            key = (period, str(source_folder))
            if key not in counts:
                counts[key] = 0
                order.append(key)
            counts[key] += 1
    for period, source_folder_text in order:
        groups.append((period, Path(source_folder_text), counts[(period, source_folder_text)]))
    if not groups:
        raise RuntimeError("candidate CSV contains no continuation groups")
    return groups


def validate_continuation_identity(config: "MonitorConfig") -> list[str]:
    if config.priority_dir == config.target_dir:
        raise RuntimeError("priority and target directories must be different")
    for parent, child in (
        (config.priority_dir, config.target_dir),
        (config.target_dir, config.priority_dir),
    ):
        try:
            child.relative_to(parent)
        except ValueError:
            continue
        raise RuntimeError("priority and target directories must not be nested")

    groups = candidate_groups(config.input_csv, config.source_root)
    first_period, first_source, first_count = groups[0]
    digest = hashlib.sha1(str(first_source).encode("utf-8")).hexdigest()[:8]
    if not (
        config.target_dir.name.startswith(f"{first_period}_")
        and config.target_dir.name.endswith(f"_{digest}")
    ):
        raise RuntimeError("target staging directory does not match the first candidate group")
    if image_count(config.target_dir) != first_count:
        raise RuntimeError("target staging image count does not match the candidate group")

    priority_match = re.match(r"^(20\d{4})_", config.priority_dir.name)
    if not priority_match:
        raise RuntimeError("priority staging directory has no period prefix")
    periods = [period for period, _source, _count in groups]
    if priority_match.group(1) in periods:
        raise RuntimeError("priority period must not appear in preserved continuation candidates")
    if len(periods) != len(set(periods)):
        raise RuntimeError("candidate CSV contains more than one source group for a period")
    if periods != sorted(periods):
        raise RuntimeError("candidate periods are not in ascending continuation order")
    return periods


@dataclass(frozen=True)
class MonitorConfig:
    repo_root: Path
    source_root: Path
    output_dir: Path
    backend_url: str
    priority_dir: Path
    target_dir: Path
    staging_root: Path
    input_csv: Path
    output_csv: Path
    run_summary_csv: Path
    log_path: Path
    runner_stdout: Path
    runner_stderr: Path
    receipt_path: Path
    poll_seconds: int
    timeout_minutes: int
    monitor_timeout_minutes: int
    no_progress_minutes: int


class SingleInstance:
    def __init__(self, path: Path):
        self.path = path
        self.owned = False

    def __enter__(self) -> "SingleInstance":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    payload = json.loads(self.path.read_text(encoding="utf-8"))
                    owner = int(payload.get("pid") or 0)
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    owner = 0
                if owner > 0 and psutil.pid_exists(owner):
                    raise RuntimeError(f"continuation monitor already active: pid={owner}")
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                continue
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"pid": os.getpid(), "started_at": utcish_now()}, handle)
            self.owned = True
            return self
        raise RuntimeError("could not acquire continuation monitor lock")

    def __exit__(self, _type, _value, _traceback) -> None:
        if self.owned:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass


class ContinuationMonitor:
    def __init__(
        self,
        config: MonitorConfig,
        requester: Callable[..., dict[str, Any]] = request_json,
        sleeper: Callable[[float], None] = time.sleep,
        launcher: Callable[[MonitorConfig], subprocess.Popen] | None = None,
        runner_finder: Callable[[Path], list[psutil.Process]] = owned_runner_processes,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ):
        self.config = config
        self.requester = requester
        self.sleeper = sleeper
        self.launcher = launcher or self._launch_runner
        self.runner_finder = runner_finder
        self.clock = clock
        self.wall_clock = wall_clock
        self.last_progress: tuple[int, int] | None = None
        self.expected_periods = validate_continuation_identity(config)
        self.started_at = self.clock()
        self.last_progress_at = self.started_at

    def validate_status(self, status: dict[str, Any]) -> Path:
        if str(status.get("version") or "").startswith("v19.45") is False:
            raise RuntimeError(f"unexpected backend version: {status.get('version')}")
        if status.get("status_contract_version") != "compact-v2":
            raise RuntimeError("unexpected backend status contract")
        if status.get("accuracy_profile") != "strict":
            raise RuntimeError("backend accuracy profile is not strict")
        if guard_revision(status.get("evidence_guard_revision")) < MIN_EVIDENCE_GUARD:
            raise RuntimeError(
                f"evidence guard is older than required: {status.get('evidence_guard_revision')}"
            )
        if status.get("runtime_health_fuse"):
            raise RuntimeError("runtime health fuse is active")
        current = status.get("current_relative_dir") or status.get("image_dir")
        if not current:
            raise RuntimeError("backend did not report its current work directory")
        return normalized(str(current))

    def start_existing_batch(self, work_dir: Path) -> None:
        response = self.requester(
            self.config.backend_url,
            "/api/start_batch",
            {
                "dir": str(work_dir),
                "restart": False,
                "confirmed": True,
                "reprocess_last_n": 0,
            },
            timeout=30,
        )
        if response.get("status") != "started":
            raise RuntimeError(f"backend refused safe continuation: {response}")
        self.verify_started(work_dir)

    def verify_started(self, expected_dir: Path) -> dict[str, Any]:
        status = self.requester(self.config.backend_url, "/api/status", timeout=15)
        current = self.validate_status(status)
        if current != expected_dir or not bool(status.get("is_running")):
            raise RuntimeError(
                f"backend did not start the requested directory: expected={expected_dir}, actual={current}"
            )
        return status

    def switch_to_target(self) -> None:
        response = self.requester(
            self.config.backend_url,
            "/api/set_work_dir",
            {"dir": str(self.config.target_dir)},
            timeout=30,
        )
        if response.get("status") != "success":
            raise RuntimeError(f"backend refused target work directory: {response}")
        self.start_existing_batch(self.config.target_dir)
        append_event(
            self.config.log_path,
            "target_started",
            target_dir=str(self.config.target_dir),
        )

    def validate_priority_completion(self, status: dict[str, Any]) -> None:
        stats = dict(status.get("stats") or {})
        total = int(stats.get("total") or 0)
        terminal = {
            "success": int(stats.get("success") or 0),
            "verified": int(stats.get("verified") or 0),
            "failed": int(stats.get("failed") or 0),
            "review_required": int(stats.get("review_required") or 0),
            "verification_unknown": int(stats.get("verification_unknown") or 0),
        }
        if (
            terminal["success"] != total
            or terminal["verified"] != total
            or terminal["failed"] != 0
            or terminal["review_required"] != 0
            or terminal["verification_unknown"] != 0
        ):
            raise RuntimeError(f"priority batch has non-final terminal outcomes: {terminal}")

        records_payload = self.requester(
            self.config.backend_url,
            "/api/success_records",
            timeout=60,
        )
        if not isinstance(records_payload, list):
            raise RuntimeError("success record API did not return a list")
        records = list(records_payload)
        names = {str(record.get("file_name") or "") for record in records}
        invalid = [
            str(record.get("file_name") or "")
            for record in records
            if record.get("auto_verified") is not True
            or record.get("auto_review_required") is True
            or record.get("stream_upload_queued") is not True
        ]
        if len(records) != total or len(names) != total or invalid:
            raise RuntimeError(
                "priority completion lacks one verified, upload-queued record per source "
                f"(records={len(records)}, unique={len(names)}, total={total}, invalid={invalid[:5]})"
            )

        receipts_dir = self.config.output_dir / "_drive_upload_stream" / "receipts"
        failed_dir = self.config.output_dir / "_drive_upload_stream" / "failed"
        receipt_errors: list[str] = []
        for record in records:
            source_item_id = str(record.get("source_item_id") or "")
            input_image_sha256 = str(record.get("input_image_sha256") or "")
            source_path = normalized(str(record.get("source_path") or ""))
            original_source_path = normalized(str(record.get("original_source_path") or ""))
            receipt_path = receipts_dir / f"{source_item_id}.json"
            failed_path = failed_dir / f"{source_item_id}.json"
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                published_path = normalized(str(receipt.get("published_path") or ""))
                receipt_original_path = normalized(
                    str(receipt.get("original_source_path") or "")
                )
                valid = bool(
                    re.fullmatch(r"[0-9a-f]{64}", source_item_id)
                    and source_path.is_file()
                    and re.fullmatch(r"[0-9a-f]{64}", input_image_sha256)
                    and prepared_input_sha256(source_path) == input_image_sha256
                    and original_source_path.is_file()
                    and receipt.get("schema") == "samsung-ocr-stream-receipt-v1"
                    and receipt.get("source_item_id") == source_item_id
                    and receipt_original_path == original_source_path
                    and receipt.get("source_sha256")
                    == sha256_file(original_source_path)
                    and receipt.get("period") == "202606"
                    and receipt.get("evidence_guard_revision")
                    == record.get("evidence_guard_revision")
                    and guard_revision(receipt.get("evidence_guard_revision"))
                    >= MIN_EVIDENCE_GUARD
                    and receipt.get("run_id") == record.get("run_id")
                    and receipt.get("drive_file_id")
                    and receipt.get("remote_path")
                    and published_path.is_file()
                    and receipt.get("published_sha256") == sha256_file(published_path)
                    and not failed_path.exists()
                )
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                valid = False
            if not valid:
                receipt_errors.append(source_item_id)
        if receipt_errors:
            raise RuntimeError(
                "priority completion lacks exact Drive readback receipts: "
                f"{receipt_errors[:5]} (count={len(receipt_errors)})"
            )

        upload = dict(status.get("stream_upload") or {})
        worker_pid = int(upload.get("worker_pid") or 0)
        if (
            upload.get("worker_state") != "running"
            or worker_pid <= 0
            or not psutil.pid_exists(worker_pid)
            or not upload.get("last_uploaded_at")
            or int(upload.get("canonical_uploaded") or 0) <= 0
        ):
            raise RuntimeError("stream uploader does not have a live, durable upload status")

    def _launch_runner(self, config: MonitorConfig) -> subprocess.Popen:
        python = config.repo_root / ".venv" / "Scripts" / "python.exe"
        executable = str(python if python.is_file() else Path(sys.executable))
        command = [
            executable,
            str(config.repo_root / "tools" / "rerun_staged_candidates.py"),
            "--source-root",
            str(config.source_root),
            "--output-dir",
            str(config.output_dir),
            "--backend-url",
            config.backend_url,
            "--input-csv",
            str(config.input_csv),
            "--output-csv",
            str(config.output_csv),
            "--run-summary-csv",
            str(config.run_summary_csv),
            "--staging-root",
            str(config.staging_root),
            "--execute",
            "--resume-existing-then-continue",
            "--keep-staging",
            "--poll-seconds",
            "10",
            "--timeout-minutes",
            str(config.timeout_minutes),
        ]
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        config.runner_stdout.parent.mkdir(parents=True, exist_ok=True)
        with (
            config.runner_stdout.open("a", encoding="utf-8") as stdout,
            config.runner_stderr.open("a", encoding="utf-8") as stderr,
        ):
            process = subprocess.Popen(
                command,
                cwd=config.repo_root,
                stdout=stdout,
                stderr=stderr,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
                start_new_session=os.name != "nt",
            )
        return process

    def ensure_runner(self) -> subprocess.Popen:
        existing = self.runner_finder(self.config.repo_root)
        if existing:
            raise RuntimeError(
                f"another staged rerun is already active; refusing duplicate: {len(existing)}"
            )
        if self.config.output_csv.exists() or self.config.run_summary_csv.exists():
            raise RuntimeError("continuation output/summary path already exists; refusing stale proof")
        process = self.launcher(self.config)
        self.sleeper(2)
        if process.poll() is not None:
            raise RuntimeError(
                f"staged continuation runner exited immediately: pid={process.pid}, "
                f"exit={process.returncode}"
            )
        append_event(self.config.log_path, "runner_started", runner_pid=process.pid)
        return process

    def verify_runner_summary(self, launched_at: float) -> None:
        if not self.config.run_summary_csv.is_file():
            raise RuntimeError("staged continuation runner exited without a run summary")
        if self.config.run_summary_csv.stat().st_mtime + 1 < launched_at:
            raise RuntimeError("staged continuation runner summary predates this launch")
        with self.config.run_summary_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        periods = [str(row.get("period") or "") for row in rows]
        if periods != self.expected_periods:
            raise RuntimeError(
                f"staged continuation summary periods do not match: {periods}"
            )
        bad = [
            period
            for period, row in zip(periods, rows)
            if str(row.get("aborted") or "").casefold() not in {"", "0", "0.0", "false"}
        ]
        if bad:
            raise RuntimeError(f"staged continuation summary contains aborted groups: {bad}")
        groups = candidate_groups(self.config.input_csv, self.config.source_root)
        for row, (period, source_folder, expected_count) in zip(rows, groups):
            staging_dir = normalized(str(row.get("staging_dir") or ""))
            digest = hashlib.sha1(str(source_folder).encode("utf-8")).hexdigest()[:8]
            try:
                staging_dir.relative_to(self.config.staging_root)
            except ValueError as exc:
                raise RuntimeError(
                    f"runner summary staging is outside root: {staging_dir}"
                ) from exc
            if (
                normalized(str(row.get("folder") or "")) != source_folder
                or not staging_dir.name.startswith(f"{period}_")
                or not staging_dir.name.endswith(f"_{digest}")
                or int(row.get("queued") or -1) != expected_count
                or int(row.get("staged") or -1) != expected_count
                or int(row.get("processed") or -1) != expected_count
            ):
                raise RuntimeError(f"runner summary identity/count mismatch for {period}")

    def watch_runner(self, process: subprocess.Popen, launched_at: float) -> None:
        runner_pid = int(process.pid)
        append_event(self.config.log_path, "runner_watch_started", runner_pid=runner_pid)
        last_cursor: tuple[str, int, int] | None = None
        last_progress_at = self.clock()
        while process.poll() is None:
            if self.clock() - self.started_at > self.config.timeout_minutes * 60:
                raise TimeoutError("staged continuation runner exceeded its allowed runtime")
            status = self.requester(self.config.backend_url, "/api/status", timeout=15)
            current = self.validate_status(status)
            stats = dict(status.get("stats") or {})
            cursor = (
                str(current),
                int(stats.get("processed") or 0),
                int(status.get("presentation_sequence") or 0),
            )
            if cursor != last_cursor:
                last_cursor = cursor
                last_progress_at = self.clock()
            elif self.clock() - last_progress_at > self.config.no_progress_minutes * 60:
                raise TimeoutError("staged continuation runner made no backend progress")
            self.sleeper(self.config.poll_seconds)
        if int(process.returncode or 0) != 0:
            raise RuntimeError(
                f"staged continuation runner failed: pid={runner_pid}, exit={process.returncode}"
            )
        self.verify_runner_summary(launched_at)
        write_json_atomic(
            self.config.receipt_path,
            {
                "schema": "period-priority-continuation/v1",
                "completed_at": utcish_now(),
                "priority_dir": str(self.config.priority_dir),
                "target_dir": str(self.config.target_dir),
                "periods": self.expected_periods,
                "runner_pid": runner_pid,
                "status": "complete",
            },
        )
        append_event(self.config.log_path, "runner_watch_complete", runner_pid=runner_pid)

    def run(self, max_polls: int = 0) -> int:
        polls = 0
        while max_polls <= 0 or polls < max_polls:
            if self.clock() - self.started_at > self.config.monitor_timeout_minutes * 60:
                raise TimeoutError("priority continuation monitor exceeded its allowed runtime")
            polls += 1
            status = self.requester(self.config.backend_url, "/api/status", timeout=15)
            current_dir = self.validate_status(status)
            stats = dict(status.get("stats") or {})
            processed = int(stats.get("processed") or 0)
            total = int(stats.get("total") or 0)

            if current_dir == self.config.target_dir:
                if not bool(status.get("is_running")) and total > 0 and processed < total:
                    self.start_existing_batch(self.config.target_dir)
                    append_event(
                        self.config.log_path,
                        "target_resumed",
                        processed=processed,
                        total=total,
                    )
                launched_at = self.wall_clock()
                process = self.ensure_runner()
                runner_pid = int(process.pid)
                write_json_atomic(
                    self.config.receipt_path,
                    {
                        "schema": "period-priority-continuation/v1",
                        "started_at": utcish_now(),
                        "priority_dir": str(self.config.priority_dir),
                        "target_dir": str(self.config.target_dir),
                        "runner_pid": runner_pid,
                        "status": "runner_active",
                    },
                )
                self.watch_runner(process, launched_at)
                return runner_pid

            if current_dir != self.config.priority_dir:
                raise RuntimeError(f"unexpected backend work directory: {current_dir}")
            if total <= 0:
                raise RuntimeError("priority batch reported a zero total")

            progress = (processed, total)
            if self.last_progress is None or processed != self.last_progress[0]:
                self.last_progress_at = self.clock()
            if progress != self.last_progress and (self.last_progress is None or processed % 25 == 0):
                append_event(
                    self.config.log_path,
                    "priority_progress",
                    processed=processed,
                    total=total,
                    running=bool(status.get("is_running")),
                )
            self.last_progress = progress
            if self.clock() - self.last_progress_at > self.config.no_progress_minutes * 60:
                raise TimeoutError("priority batch made no progress before the health deadline")

            if bool(status.get("is_running")):
                self.sleeper(self.config.poll_seconds)
                continue
            if processed < total:
                self.start_existing_batch(self.config.priority_dir)
                append_event(
                    self.config.log_path,
                    "priority_resumed",
                    processed=processed,
                    total=total,
                )
                self.sleeper(self.config.poll_seconds)
                continue

            upload = dict(status.get("stream_upload") or {})
            pending = int(upload.get("pending") or 0)
            working = int(upload.get("working") or 0)
            if pending > 0 or working > 0:
                append_event(
                    self.config.log_path,
                    "waiting_for_upload_drain",
                    pending=pending,
                    working=working,
                )
                self.sleeper(self.config.poll_seconds)
                continue

            self.validate_priority_completion(status)
            append_event(
                self.config.log_path,
                "priority_complete",
                processed=processed,
                total=total,
            )
            self.switch_to_target()
            self.sleeper(2)
        raise TimeoutError("maximum monitor polls reached before continuation")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--backend-url", default="http://127.0.0.1:5002")
    parser.add_argument("--priority-dir", required=True)
    parser.add_argument("--target-dir", required=True)
    parser.add_argument("--staging-root", required=True)
    parser.add_argument("--input-csv", required=True)
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--run-summary-csv", required=True)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--timeout-minutes", type=int, default=10080)
    parser.add_argument("--monitor-timeout-minutes", type=int, default=2880)
    parser.add_argument("--no-progress-minutes", type=int, default=90)
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Validate all paths and candidate identities without calling the backend.",
    )
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> MonitorConfig:
    repo_root = normalized(args.repo_root)
    output_dir = normalized(args.output_dir)
    audit_dir = output_dir / "_ocr_audit"
    log_dir = repo_root / "logs"
    stamp = datetime.now().strftime("%Y%m%d")
    config = MonitorConfig(
        repo_root=repo_root,
        source_root=normalized(args.source_root),
        output_dir=output_dir,
        backend_url=str(args.backend_url).rstrip("/"),
        priority_dir=normalized(args.priority_dir),
        target_dir=normalized(args.target_dir),
        staging_root=normalized(args.staging_root),
        input_csv=normalized(args.input_csv),
        output_csv=normalized(args.output_csv),
        run_summary_csv=normalized(args.run_summary_csv),
        log_path=log_dir / f"period_priority_continuation_{stamp}.jsonl",
        runner_stdout=log_dir / f"period_priority_runner_{stamp}.out.log",
        runner_stderr=log_dir / f"period_priority_runner_{stamp}.err.log",
        receipt_path=audit_dir / "period_priority_continuation_receipt.json",
        poll_seconds=max(10, int(args.poll_seconds)),
        timeout_minutes=max(60, int(args.timeout_minutes)),
        monitor_timeout_minutes=max(120, int(args.monitor_timeout_minutes)),
        no_progress_minutes=max(30, int(args.no_progress_minutes)),
    )
    required_files = [
        config.repo_root / "tools" / "rerun_staged_candidates.py",
        config.input_csv,
    ]
    required_dirs = [
        config.source_root,
        config.output_dir,
        config.priority_dir,
        config.target_dir,
        config.staging_root,
    ]
    missing = [str(path) for path in required_files if not path.is_file()]
    missing.extend(str(path) for path in required_dirs if not path.is_dir())
    if missing:
        raise RuntimeError(f"required continuation paths are missing: {missing}")
    try:
        config.target_dir.relative_to(config.staging_root)
    except ValueError as exc:
        raise RuntimeError("target staging directory is outside the declared staging root") from exc
    validate_continuation_identity(config)
    return config


def main() -> int:
    args = parse_args()
    config = build_config(args)
    if args.preflight_only:
        print(
            json.dumps(
                {
                    "valid": True,
                    "periods": validate_continuation_identity(config),
                    "priority_dir": str(config.priority_dir),
                    "target_dir": str(config.target_dir),
                },
                ensure_ascii=False,
            )
        )
        return 0
    lock_path = config.output_dir / "_ocr_audit" / "period_priority_continuation.lock"
    try:
        with SingleInstance(lock_path):
            append_event(
                config.log_path,
                "monitor_started",
                priority_dir=str(config.priority_dir),
                target_dir=str(config.target_dir),
            )
            runner_pid = ContinuationMonitor(config).run()
            append_event(config.log_path, "monitor_finished", runner_pid=runner_pid)
            return 0
    except Exception as exc:
        append_event(config.log_path, "monitor_failed_closed", error=str(exc))
        write_json_atomic(
            config.output_dir / "_ocr_audit" / "period_priority_continuation_alert.json",
            {
                "schema": "period-priority-continuation-alert/v1",
                "failed_at": utcish_now(),
                "status": "fail_closed",
                "error": str(exc),
            },
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

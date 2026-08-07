"""Batch-finalize exhausted photos from existing bound trace evidence.

The trace is scanned exactly once into temporary per-source shards.  Every
candidate must then pass the existing cross-run, whole-history
no-cherry-picking consensus rule and the existing source-byte binding
validator.  The default mode is read-only.  ``--apply`` uses the established
single-photo deterministic recovery path to write the terminal result and
enqueue its upload; this module never calls a model or changes a service/fuse.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any, TextIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.recover_consumed_cap_missing_result import (
    _load_cross_run_trace_calls,
    _raw_structured_distant_consensus,
    _raw_structured_single_consensus,
    _validate_three_trace_bindings,
    recover,
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _task_file_name(task: dict[str, Any]) -> str:
    image = str((task.get("data") or {}).get("image") or "").replace("\\", "/")
    return image.rsplit("/", 1)[-1]


def _finalized_names(staging_dir: Path, result_file: Path) -> set[str]:
    paths = {path.resolve() for path in staging_dir.glob("*-OCR成功.json")}
    if result_file.is_file():
        paths.add(result_file.resolve())
    names: set[str] = set()
    for path in paths:
        tasks = _read_json(path)
        if not isinstance(tasks, list):
            raise RuntimeError(f"result file is not a task list: {path}")
        names.update(
            name
            for task in tasks
            if isinstance(task, dict) and (name := _task_file_name(task))
        )
    return names


def _close_handles(handles: OrderedDict[str, TextIO]) -> None:
    while handles:
        _, handle = handles.popitem(last=False)
        handle.close()


def _build_trace_shards(
    *,
    trace_path: Path,
    expected_by_source: dict[str, str],
    shard_dir: Path,
    max_open_handles: int = 64,
) -> tuple[dict[str, Path], int, int]:
    """Scan one trace once and retain only rows for pending source identities."""

    shard_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    handles: OrderedDict[str, TextIO] = OrderedDict()
    scanned = 0
    matched = 0
    try:
        with trace_path.open("r", encoding="utf-8-sig") as source:
            for line in source:
                scanned += 1
                try:
                    payload = json.loads(line)
                except (TypeError, ValueError):
                    continue
                source_id = str(
                    payload.get("source_item_id")
                    or payload.get("source_identity")
                    or ""
                )
                expected_name = expected_by_source.get(source_id)
                if not expected_name or str(payload.get("file_name") or "") != expected_name:
                    continue
                matched += 1
                path = paths.setdefault(source_id, shard_dir / f"{source_id}.jsonl")
                handle = handles.pop(source_id, None)
                if handle is None:
                    handle = path.open("a", encoding="utf-8", newline="\n")
                handles[source_id] = handle
                handle.write(line if line.endswith("\n") else line + "\n")
                if len(handles) > max_open_handles:
                    _, oldest = handles.popitem(last=False)
                    oldest.close()
    finally:
        _close_handles(handles)
    return paths, scanned, matched


def _reason_key(exc: BaseException) -> str:
    message = str(exc)
    known = (
        "trace lacks three clean same-input consensus calls",
        "historical trace source file is missing",
        "historical trace source bytes do not match",
        "staged and original source bytes do not match",
        "trace original source path does not match",
        "current staged or original source file is missing",
    )
    for prefix in known:
        if prefix in message:
            return prefix
    return message or type(exc).__name__


def _screen_no_cherry_pick(
    *,
    shard_path: Path,
    source_item_id: str,
    file_name: str,
    staged_path: Path,
    original_source: Path,
) -> dict[str, Any]:
    """Return the material authority only if the whole clean history agrees."""

    calls = _load_cross_run_trace_calls(
        shard_path,
        source_item_id=source_item_id,
        file_name=file_name,
    )
    _validate_three_trace_bindings(
        calls,
        staged_path=staged_path,
        original_source=original_source,
        allow_cross_run=True,
    )
    try:
        authority = _raw_structured_single_consensus(calls)
        kind = "single"
    except RuntimeError:
        authority = _raw_structured_distant_consensus(calls)
        kind = "distant"
    return {
        "kind": kind,
        "view_type": authority.get("view_type"),
        "model": authority.get("model"),
        "price": authority.get("price"),
        "complete_screen_count": authority.get("complete_screen_count"),
    }


def finalize_batch(
    *,
    staging_dir: Path,
    trace_path: Path,
    result_file: Path,
    upload_output_dir: Path,
    apply: bool = False,
    include_incompatible: bool = False,
    recover_fn=recover,
) -> dict[str, Any]:
    """Find or apply every safe zero-model recovery in one trace scan."""

    staging_dir = staging_dir.resolve()
    trace_path = trace_path.resolve()
    result_file = result_file.resolve()
    upload_output_dir = upload_output_dir.resolve()
    source_map_path = staging_dir / ".ocr_source_map.json"
    if not staging_dir.is_dir():
        raise FileNotFoundError(staging_dir)
    if not trace_path.is_file():
        raise FileNotFoundError(trace_path)
    source_map = _read_json(source_map_path)
    items = source_map.get("items") or {}
    if not isinstance(items, dict):
        raise RuntimeError("source map items are not an object")

    finalized_before = _finalized_names(staging_dir, result_file)
    pending: list[tuple[str, str, dict[str, Any]]] = []
    for file_name, raw_info in items.items():
        if file_name in finalized_before:
            continue
        info = dict(raw_info or {})
        source_id = str(info.get("source_item_id") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", source_id):
            raise RuntimeError(f"invalid source_item_id for {file_name}")
        pending.append((str(file_name), source_id, info))
    pending.sort(key=lambda item: item[0])

    candidates: list[dict[str, Any]] = []
    incompatible: list[dict[str, str]] = []
    reason_counts: Counter[str] = Counter()
    trace_lines_scanned = 0
    trace_rows_matched = 0
    with tempfile.TemporaryDirectory(prefix="safe-consumed-cap-") as temp:
        shard_dir = Path(temp)
        shards, trace_lines_scanned, trace_rows_matched = _build_trace_shards(
            trace_path=trace_path,
            expected_by_source={
                source_id: file_name for file_name, source_id, _ in pending
            },
            shard_dir=shard_dir,
        )
        for file_name, source_id, info in pending:
            shard_path = shards.get(source_id)
            if shard_path is None:
                reason = "trace has no rows for pending source"
                reason_counts[reason] += 1
                incompatible.append({"file_name": file_name, "reason": reason})
                continue
            try:
                screen = _screen_no_cherry_pick(
                    shard_path=shard_path,
                    source_item_id=source_id,
                    file_name=file_name,
                    staged_path=staging_dir / file_name,
                    original_source=Path(
                        str(info.get("original_source_path") or "")
                    ),
                )
                dry = recover_fn(
                    staging_dir=staging_dir,
                    trace_path=shard_path,
                    result_file=result_file,
                    upload_output_dir=upload_output_dir,
                    file_name=file_name,
                    apply=False,
                )
                material = ("view_type", "model", "price")
                if any(dry.get(field) != screen.get(field) for field in material):
                    raise RuntimeError(
                        "single-photo recovery disagrees with whole-history consensus"
                    )
                candidates.append({**dry, "consensus_kind": screen["kind"]})
            except (FileNotFoundError, RuntimeError, ValueError) as exc:
                reason = _reason_key(exc)
                reason_counts[reason] += 1
                incompatible.append({"file_name": file_name, "reason": reason})

        applied: list[dict[str, Any]] = []
        apply_failures: list[dict[str, str]] = []
        if apply:
            for candidate in candidates:
                file_name = str(candidate["file_name"])
                source_id = str(candidate["source_item_id"])
                shard_path = shards[source_id]
                try:
                    applied.append(
                        recover_fn(
                            staging_dir=staging_dir,
                            trace_path=shard_path,
                            result_file=result_file,
                            upload_output_dir=upload_output_dir,
                            file_name=file_name,
                            apply=True,
                        )
                    )
                except (FileNotFoundError, RuntimeError, ValueError) as exc:
                    apply_failures.append(
                        {"file_name": file_name, "reason": _reason_key(exc)}
                    )

    report: dict[str, Any] = {
        "status": (
            "partial_failure"
            if apply and apply_failures
            else "applied"
            if apply
            else "dry_run"
        ),
        "source_total": len(items),
        "already_finalized_count": len(finalized_before),
        "pending_count": len(pending),
        "safe_count": len(candidates),
        "incompatible_count": len(incompatible),
        "applied_count": len(applied) if apply else 0,
        "apply_failure_count": len(apply_failures) if apply else 0,
        "trace_scan_passes": 1,
        "trace_lines_scanned": trace_lines_scanned,
        "trace_rows_matched": trace_rows_matched,
        "model_calls_made": 0,
        "service_or_fuse_touched": False,
        "incompatible_reasons": dict(reason_counts.most_common()),
        "candidates": candidates,
    }
    if apply:
        report["applied"] = applied
        report["apply_failures"] = apply_failures
    if include_incompatible:
        report["incompatible"] = incompatible
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--trace-path", type=Path, required=True)
    parser.add_argument("--result-file", type=Path, required=True)
    parser.add_argument("--upload-output-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--include-incompatible", action="store_true")
    args = parser.parse_args()
    report = finalize_batch(
        staging_dir=args.staging_dir,
        trace_path=args.trace_path,
        result_file=args.result_file,
        upload_output_dir=args.upload_output_dir,
        apply=args.apply,
        include_incompatible=args.include_incompatible,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if report["status"] == "partial_failure" else 0


if __name__ == "__main__":
    raise SystemExit(main())

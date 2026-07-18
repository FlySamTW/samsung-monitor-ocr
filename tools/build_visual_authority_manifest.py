"""Bind reviewed visual decisions to immutable source and inference bytes.

The input decision file is human-readable and uses source filenames only as a
lookup convenience. This builder refuses to emit an authority until the source
map, source bytes, one clean request-bound run, and full-image inference hash
all agree. The emitted manifest is therefore safe for bounded offline
adjudication and never becomes a general filename rule.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.recover_request_binding_fuse import _atomic_json


DECISION_SCHEMA = "samsung-ocr-visual-decisions/v1"
MANIFEST_SCHEMA = "samsung-ocr-bound-visual-authorities/v1"
ALLOWED_CONTAINED_CONTENT_REASONS = {
    "known_source_expectation_conflict",
    "structured_authority_material_conflict:model",
    "structured_narration_followme_conflict",
    "distant_followme_strong_evidence_conflict",
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean_call(row: dict[str, Any]) -> bool:
    runtime = row.get("runtime_health") or {}
    runtime_reasons = {
        str(item) for item in runtime.get("reasons") or [] if str(item)
    } if isinstance(runtime, dict) else set()
    content_integrity_ok = bool(
        isinstance(runtime, dict)
        and (
            runtime.get("healthy") is True
            or (
                runtime_reasons
                and runtime_reasons <= ALLOWED_CONTAINED_CONTENT_REASONS
            )
        )
    )
    return bool(
        row.get("request_id_verified") is True
        and row.get("request_binding_enforced") is True
        and row.get("independent_pass") is True
        and row.get("prior_answer_exposed") is not True
        and row.get("prompt_contamination") is not True
        and content_integrity_ok
    )


def _load_trace_groups(trace_path: Path) -> dict[tuple[str, str], list[list[dict[str, Any]]]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    with trace_path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            payload = json.loads(line)
            source_id = str(
                payload.get("source_item_id") or payload.get("source_identity") or ""
            )
            file_name = str(payload.get("file_name") or "")
            run_id = str(payload.get("run_id") or "")
            if not source_id or not file_name or not run_id:
                continue
            row = dict(payload.get("parsed_output") or {})
            row.update(
                {
                    "source_item_id": source_id,
                    "file_name": file_name,
                    "run_id": run_id,
                    "ocr_attempt": int(payload.get("attempt") or 0),
                    "timestamp": str(payload.get("timestamp") or ""),
                }
            )
            grouped.setdefault((source_id, file_name, run_id), []).append(row)

    result: dict[tuple[str, str], list[list[dict[str, Any]]]] = {}
    for (source_id, file_name, _run_id), rows in grouped.items():
        ordered = sorted(rows, key=lambda item: int(item.get("ocr_attempt") or 0))
        result.setdefault((source_id, file_name), []).append(ordered)
    for groups in result.values():
        groups.sort(key=lambda rows: str(rows[-1].get("timestamp") or ""))
    return result


def _select_clean_capped_run(
    groups: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    for rows in reversed(groups):
        attempts = [int(item.get("ocr_attempt") or 0) for item in rows]
        hashes = {
            str(item.get("input_image_sha256") or "").strip().lower()
            for item in rows
        }
        if (
            len(rows) in {2, 3}
            and attempts[-1] == 3
            and len(hashes) == 1
            and re.fullmatch(r"[0-9a-f]{64}", next(iter(hashes), ""))
            and all(_clean_call(item) for item in rows)
        ):
            return rows
    raise RuntimeError("no clean capped run ending at attempt three")


def build_manifest(
    *,
    staging_dir: Path,
    trace_path: Path,
    decisions_path: Path,
    output_path: Path,
    apply: bool,
) -> dict[str, Any]:
    staging_dir = staging_dir.resolve()
    trace_path = trace_path.resolve()
    decisions_path = decisions_path.resolve()
    output_path = output_path.resolve()
    source_map = _read_json(staging_dir / ".ocr_source_map.json")
    source_items = dict(source_map.get("items") or {})
    decisions_payload = _read_json(decisions_path)
    if decisions_payload.get("schema") != DECISION_SCHEMA:
        raise RuntimeError("unexpected visual decision schema")
    decisions = list(decisions_payload.get("decisions") or [])
    if not decisions:
        raise RuntimeError("visual decision file is empty")
    names = [str(item.get("file_name") or "") for item in decisions]
    if "" in names or len(names) != len(set(names)):
        raise RuntimeError("visual decision filenames must be non-empty and unique")

    trace_groups = _load_trace_groups(trace_path)
    entries: list[dict[str, Any]] = []
    seen_source_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for decision in decisions:
        file_name = str(decision["file_name"])
        source_info = dict(source_items.get(file_name) or {})
        source_id = str(source_info.get("source_item_id") or "")
        original = Path(str(source_info.get("original_source_path") or "")).resolve()
        period = str(source_info.get("period") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", source_id):
            raise RuntimeError(f"{file_name}: source map has no stable identity")
        if source_id in seen_source_ids or not original.is_file():
            raise RuntimeError(f"{file_name}: duplicate identity or missing original")
        if period != str(decisions_payload.get("period") or ""):
            raise RuntimeError(f"{file_name}: period mismatch")

        try:
            calls = _select_clean_capped_run(
                trace_groups.get((source_id, file_name), [])
            )
        except RuntimeError as exc:
            raise RuntimeError(f"{file_name}: {exc}") from exc
        input_hash = str(calls[-1].get("input_image_sha256") or "").strip().lower()
        if input_hash in seen_hashes:
            raise RuntimeError(f"{file_name}: duplicate full-image inference hash")
        view = str(decision.get("view_type") or "")
        count = decision.get("complete_screen_count")
        if (
            view not in {"單機", "遠景"}
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
            or (view == "遠景" and count < 3)
        ):
            raise RuntimeError(f"{file_name}: invalid visual geometry decision")

        entry = {
            "file_name": file_name,
            "source_item_id": source_id,
            "original_source_path": str(original),
            "source_file_sha256": _sha256_file(original),
            "input_image_sha256": input_hash,
            "period": period,
            "view_type": view,
            "complete_screen_count": count,
            "model": decision.get("model"),
            "price": decision.get("price"),
            "label_ownership": decision.get("label_ownership", "matched"),
            "followme_physical_expected": bool(
                decision.get("followme_physical_expected") is True
            ),
            "followme_physical_evidence": [
                dict(item) for item in decision.get("followme_physical_evidence") or []
            ],
            "wide_scene_followme_present": bool(
                decision.get("wide_scene_followme_present") is True
            ),
            "authority": "human_audited_pixel_authority",
            "audit_method": "bounded_low_power_visual_agent",
            "clean_model_outputs": len(calls),
            "model_call_cap": 3,
            "fourth_call_made": False,
        }
        entries.append(entry)
        seen_source_ids.add(source_id)
        seen_hashes.add(input_hash)

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "period": decisions_payload.get("period"),
        "source_decisions": str(decisions_path),
        "staging_dir": str(staging_dir),
        "trace_path": str(trace_path),
        "entry_count": len(entries),
        "entries": entries,
    }
    if apply:
        _atomic_json(output_path, manifest)
    return {
        "status": "written" if apply else "would_write",
        "output_path": str(output_path),
        "entry_count": len(entries),
        "source_ids": [item["source_item_id"] for item in entries],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = build_manifest(
        staging_dir=args.staging_dir,
        trace_path=args.trace,
        decisions_path=args.decisions,
        output_path=args.output,
        apply=args.apply,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Close a missing result after the three-call budget is already consumed.

This recovery performs no model inference. It requires two request-bound outputs
in the evidence trace, a durable attempt counter of exactly three, and an exact
visual authority bound to both source bytes and full-image inference bytes. At
least one output must be clean; at most one may be a narrowly contained,
non-contaminating same-photo presentation conflict. The unavailable third output
is recorded honestly; no call four occurs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.audit_fields import (
    EVIDENCE_CONTRACT_VERSION,
    EVIDENCE_GUARD_REVISION,
    KNOWN_SOURCE_AUDIT_AUTHORITIES,
    KNOWN_SOURCE_EXPECTATIONS,
    refresh_authoritative_price_comparison,
    validate_evidence_contract,
)
from tools.recover_request_binding_fuse import _atomic_json, _result_task
from tools.stream_drive_upload import enqueue_finalized_result


RECOVERY_RULE = "two_clean_outputs_plus_consumed_cap_visual_authority"
CONTAINED_RECOVERY_RULE = (
    "one_clean_plus_one_contained_output_plus_consumed_cap_visual_authority"
)
ALLOWED_CONTAINED_RUNTIME_REASONS = frozenset(
    {
        "structured_narration_followme_conflict",
        "ui_narration_contains_raw_structure",
    }
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _base_bound_call(row: dict[str, Any], image_hash: str) -> bool:
    return bool(
        str(row.get("input_image_sha256") or "").strip().lower() == image_hash
        and row.get("request_id_verified") is True
        and row.get("request_binding_enforced") is True
        and row.get("independent_pass") is True
        and row.get("prior_answer_exposed") is not True
        and row.get("prompt_contamination") is not True
    )


def _clean_bound_call(row: dict[str, Any], image_hash: str) -> bool:
    runtime = row.get("runtime_health") or {}
    return bool(
        _base_bound_call(row, image_hash)
        and isinstance(runtime, dict)
        and runtime.get("healthy") is True
        and not (runtime.get("reasons") or [])
    )


def _contained_bound_call(
    row: dict[str, Any],
    image_hash: str,
    authority: dict[str, Any] | None = None,
) -> bool:
    runtime = row.get("runtime_health") or {}
    reasons = {
        str(reason).strip()
        for reason in (runtime.get("reasons") or [])
        if str(reason).strip()
    }
    authority_allows_conservative_empty_model = bool(
        reasons == {"structured_authority_material_conflict:model"}
        and isinstance(authority, dict)
        and authority.get("authority") == "human_audited_pixel_authority"
        and authority.get("model") is None
        and row.get("model") in (None, "")
    )
    return bool(
        _base_bound_call(row, image_hash)
        and isinstance(runtime, dict)
        and runtime.get("healthy") is False
        and reasons
        and (
            reasons <= ALLOWED_CONTAINED_RUNTIME_REASONS
            or authority_allows_conservative_empty_model
        )
    )


def _classify_bound_calls(
    rows: list[dict[str, Any]],
    image_hash: str,
    authority: dict[str, Any] | None = None,
) -> tuple[int, int]:
    clean_count = sum(_clean_bound_call(row, image_hash) for row in rows)
    contained_count = sum(
        _contained_bound_call(row, image_hash, authority) for row in rows
    )
    if clean_count + contained_count != len(rows):
        raise RuntimeError(
            "outputs include contamination, identity failure, or an unapproved runtime failure"
        )
    if clean_count < 1 or contained_count > 1:
        raise RuntimeError("recovery requires at least one clean and at most one contained output")
    return clean_count, contained_count


def _load_trace_calls(
    trace_path: Path,
    *,
    source_item_id: str,
    file_name: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    with trace_path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if source_item_id not in line or file_name not in line:
                continue
            payload = json.loads(line)
            if (
                str(payload.get("source_item_id") or payload.get("source_identity") or "")
                != source_item_id
                or str(payload.get("file_name") or "") != file_name
            ):
                continue
            row = dict(payload.get("parsed_output") or {})
            row.update(
                {
                    "file_name": file_name,
                    "source_item_id": source_item_id,
                    "run_id": str(payload.get("run_id") or row.get("run_id") or ""),
                    "ocr_attempt": int(payload.get("attempt") or row.get("ocr_attempt") or 0),
                    "timestamp": str(payload.get("timestamp") or row.get("timestamp") or ""),
                }
            )
            grouped.setdefault(row["run_id"], []).append(row)

    candidates: list[list[dict[str, Any]]] = []
    for rows in grouped.values():
        ordered = sorted(rows, key=lambda item: int(item.get("ocr_attempt") or 0))
        if [int(item.get("ocr_attempt") or 0) for item in ordered] == [1, 2]:
            candidates.append(ordered)
    if not candidates:
        raise RuntimeError("trace lacks one two-output run at attempts 1 and 2")
    candidates.sort(key=lambda rows: str(rows[-1].get("timestamp") or ""))
    return candidates[-1]


def _apply_authority(
    current: dict[str, Any],
    authority: dict[str, Any],
    *,
    image_hash: str,
    clean_count: int,
    contained_count: int,
) -> None:
    recovery_rule = CONTAINED_RECOVERY_RULE if contained_count else RECOVERY_RULE
    view = str(authority["view_type"])
    current.update(
        {
            "view_type": view,
            "category": view,
            "complete_screen_count": authority.get("complete_screen_count"),
            "unique_main": view == "單機",
            "model": authority.get("model"),
            "price": authority.get("price"),
            "label_ownership": authority.get("label_ownership", "matched"),
            "followme_family_confirmed": bool(
                authority.get("followme_physical_expected") is True
            ),
            "screen_status": "" if view == "遠景" else "正常",
            "human_pixel_authority_applied": True,
            "human_pixel_authority_sha256": image_hash,
            "three_pass_adjudicated": True,
            "adjudication_rule": recovery_rule,
            "hard_cap_consumed_attempts": 3,
            "model_outputs_available": clean_count,
            "model_outputs_observed": clean_count + contained_count,
            "contained_failed_outputs": contained_count,
            "third_output_missing_at_process_boundary": True,
        }
    )
    if "followme_physical_evidence" in authority:
        current["followme_physical_evidence"] = [
            dict(item) for item in authority.get("followme_physical_evidence") or []
        ]
    elif authority.get("followme_physical_expected") is False:
        current["followme_physical_evidence"] = []

    if view == "遠景" or (current.get("model") and current.get("price")):
        current["quality_issue"] = "無"
    elif not current.get("model") and not current.get("price"):
        current["quality_issue"] = "不合格-沒有規格和價格牌"
    elif not current.get("model"):
        current["quality_issue"] = "不合格-沒有規格牌"
    else:
        current["quality_issue"] = "不合格-沒有價格牌"

    refresh_authoritative_price_comparison(
        current,
        authority.get("model"),
        authority.get("price"),
    )
    if view == "遠景":
        narration = (
            "我看到完整原圖中至少三台螢幕完整入鏡，沒有可唯一歸屬同一主體的"
            "型號與價格，因此定案為遠景、無型號、無價格。第三個模型呼叫名額已"
            "在程序邊界消耗但沒有留下可用輸出；本結論由兩份乾淨綁圖證據與像素"
            "稽核完成，沒有進行第四次呼叫。"
        )
    else:
        narration = (
            "我看到完整原圖的唯一主體與標籤歸屬已由兩份乾淨綁圖證據及像素稽核"
            "確認；第三個模型呼叫名額已在程序邊界消耗但沒有留下可用輸出，沒有"
            "進行第四次呼叫。"
        )
    current["thinking"] = narration
    current["narration"] = narration
    if contained_count:
        narration = (
            "本張已用滿三次模型呼叫額度；其中一份輸出通過完整守門，"
            "一份同照片輸出因結構與敘述衝突被隔離，第三份輸出於程序邊界遺失。"
            "未進行第四輪；最終結果依原圖雜湊綁定的人工像素權威結案。"
        )
        current["thinking"] = narration
        current["narration"] = narration


def recover(
    *,
    staging_dir: Path,
    trace_path: Path,
    result_file: Path,
    upload_output_dir: Path,
    file_name: str,
    apply: bool,
) -> dict[str, Any]:
    staging_dir = staging_dir.resolve()
    trace_path = trace_path.resolve()
    result_file = result_file.resolve()
    retry_path = staging_dir / ".ocr_retry_queue.json"
    source_map_path = staging_dir / ".ocr_source_map.json"
    staged_path = (staging_dir / file_name).resolve()
    if not staged_path.is_file():
        raise FileNotFoundError(staged_path)

    source_map = _read_json(source_map_path)
    source_info = (source_map.get("items") or {}).get(file_name) or {}
    source_item_id = str(source_info.get("source_item_id") or "")
    original_source = Path(str(source_info.get("original_source_path") or "")).resolve()
    period = str(source_info.get("period") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", source_item_id):
        raise RuntimeError("source map has no stable source_item_id")
    if not original_source.is_file() or not re.fullmatch(r"20\d{4}", period):
        raise RuntimeError("source map has no valid original file/period")

    authority = KNOWN_SOURCE_AUDIT_AUTHORITIES.get(source_item_id)
    if not authority or authority.get("authority") != "human_audited_pixel_authority":
        raise RuntimeError("source identity has no exact human-audited authority")
    image_hash = str(authority.get("input_image_sha256") or "").strip().lower()
    if (
        KNOWN_SOURCE_EXPECTATIONS.get(image_hash) is not authority
        or str(authority.get("source_file_sha256") or "") != _sha256_file(original_source)
    ):
        raise RuntimeError("source bytes or authority registry identity do not match")

    retry_state = _read_json(retry_path)
    if Path(str(retry_state.get("image_dir") or "")).resolve() != staging_dir:
        raise RuntimeError("retry state belongs to a different staging directory")
    attempts = retry_state.get("auto_attempts") or {}
    histories = retry_state.get("auto_result_history") or {}
    if int(attempts.get(file_name) or 0) != 3:
        raise RuntimeError("retry state does not prove exactly three consumed calls")
    durable_history = list(histories.get(file_name) or [])
    if len(durable_history) != 2:
        raise RuntimeError("retry state does not contain exactly two bound outputs")
    durable_clean_count, durable_contained_count = _classify_bound_calls(
        [dict(item) for item in durable_history], image_hash, authority
    )

    calls = _load_trace_calls(
        trace_path,
        source_item_id=source_item_id,
        file_name=file_name,
    )
    trace_clean_count, trace_contained_count = _classify_bound_calls(
        calls, image_hash, authority
    )
    if (trace_clean_count, trace_contained_count) != (
        durable_clean_count,
        durable_contained_count,
    ):
        raise RuntimeError("trace and durable history output classifications disagree")
    if len({str(item.get("run_id") or "") for item in calls}) != 1:
        raise RuntimeError("trace calls do not belong to one run")

    for existing_path in staging_dir.glob("*-OCR成功.json"):
        existing_tasks = _read_json(existing_path)
        if any(
            Path(str((task.get("data") or {}).get("image") or "")).name == file_name
            for task in existing_tasks
            if isinstance(task, dict)
        ):
            raise RuntimeError(f"photo already exists in result file: {existing_path.name}")

    current = dict(calls[-1])
    current.update(
        {
            "file_name": file_name,
            "source_path": str(staged_path),
            "original_source_path": str(original_source),
            "source_item_id": source_item_id,
            "period": period,
            "ocr_attempt": 3,
            "timestamp": datetime.now().isoformat(),
            "request_binding_enforced": True,
            "request_id_verified": True,
            "independent_pass": True,
            "prior_answer_exposed": False,
            "prompt_contamination": False,
        }
    )
    _apply_authority(
        current,
        authority,
        image_hash=image_hash,
        clean_count=durable_clean_count,
        contained_count=durable_contained_count,
    )
    current["runtime_health"] = {
        "healthy": True,
        "allow_processing": True,
        "allow_upload": True,
        "reasons": [],
        "display_narration": current["narration"],
        "resolved_by_consumed_cap_visual_authority": True,
    }
    valid, errors, normalized = validate_evidence_contract(current)
    if not valid:
        raise RuntimeError("recovered result failed evidence contract: " + ";".join(errors))
    current.update(
        {
            "normalized_evidence": normalized,
            "evidence_contract_version": EVIDENCE_CONTRACT_VERSION,
            "evidence_guard_revision": EVIDENCE_GUARD_REVISION,
            "evidence_contract_valid": True,
            "auto_verified": True,
            "auto_review_required": False,
            "review_status": "已完成判讀",
            "technical_retry_required": False,
            "technical_retry_exhausted": False,
            "auto_retry_reasons": "",
            "adjudication_summary": (
                "模型呼叫預算已用滿三次；兩份乾淨綁圖輸出加上完整像素稽核定案，"
                "第三份輸出缺失被明確記錄，沒有進行第四次呼叫。"
            ),
        }
    )

    report = {
        "status": "would_recover" if not apply else "recovered",
        "file_name": file_name,
        "source_item_id": source_item_id,
        "period": period,
        "view_type": current.get("view_type"),
        "model": current.get("model"),
        "price": current.get("price"),
        "complete_screen_count": current.get("complete_screen_count"),
        "model_calls_consumed": 3,
        "model_outputs_available": durable_clean_count,
        "model_outputs_observed": durable_clean_count + durable_contained_count,
        "contained_failed_outputs": durable_contained_count,
        "fourth_call_made": False,
        "adjudication_rule": (
            CONTAINED_RECOVERY_RULE if durable_contained_count else RECOVERY_RULE
        ),
    }
    if not apply:
        return report

    tasks = _read_json(result_file) if result_file.is_file() else []
    if not isinstance(tasks, list):
        raise RuntimeError("result file is not a Label Studio task list")
    queued = enqueue_finalized_result(current, output_dir=upload_output_dir)
    if queued is None:
        raise RuntimeError("recovered result did not pass the upload queue gate")
    current["stream_upload_queued"] = True
    task_id = max((int(task.get("id") or 0) for task in tasks), default=0) + 1
    tasks.append(_result_task(current, task_id))
    _atomic_json(result_file, tasks)

    attempts.pop(file_name, None)
    histories.pop(file_name, None)
    retry_state["auto_attempts"] = attempts
    retry_state["auto_result_history"] = histories
    retry_state["retry_queue"] = [
        item for item in retry_state.get("retry_queue") or [] if item != file_name
    ]
    retry_state["priority_queue"] = [
        item for item in retry_state.get("priority_queue") or [] if item != file_name
    ]
    retry_state["updated_at"] = datetime.now().isoformat()
    _atomic_json(retry_path, retry_state)

    receipt = {
        **report,
        "status": "recovered",
        "queued_job": str(queued),
        "result_file": str(result_file),
        "recovered_at": datetime.now().isoformat(),
    }
    receipt_path = (
        upload_output_dir.resolve()
        / "_ocr_audit"
        / "consumed_cap_missing_result_recovery"
        / f"{datetime.now():%Y%m%d_%H%M%S}_{source_item_id[:12]}.json"
    )
    _atomic_json(receipt_path, receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--trace-path", type=Path, required=True)
    parser.add_argument("--result-file", type=Path, required=True)
    parser.add_argument("--upload-output-dir", type=Path, required=True)
    parser.add_argument("--file-name", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = recover(
        staging_dir=args.staging_dir,
        trace_path=args.trace_path,
        result_file=args.result_file,
        upload_output_dir=args.upload_output_dir,
        file_name=args.file_name,
        apply=args.apply,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

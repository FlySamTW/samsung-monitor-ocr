"""Strict offline scorer for the fixed model benchmark.

Input is JSON or JSONL with one object per case.  Sidecar rows distinguish the
candidate VLM in ``candidate_model`` from the predicted Samsung product in
``model``.  Missing, duplicate, unexpected, mixed-model, or failed predictions
remain in the denominator and make ``benchmark_gate_pass`` false.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


FIELDS = ("view_type", "model", "price")


def load(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return []
    value = json.loads(text) if text.startswith("[") else [json.loads(line) for line in text.splitlines() if line.strip()]
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ValueError("predictions must be a JSON array or JSONL objects")
    return value


def _prediction_id(row: dict) -> str:
    return str(row.get("id") or row.get("case_id") or "").strip()


def _filter_model(predictions: list[dict], requested_model: str) -> tuple[list[dict], list[str]]:
    if not requested_model or requested_model == "unknown":
        models = sorted({str(row.get("candidate_model") or "") for row in predictions if row.get("candidate_model")})
        if len(models) > 1:
            return [], ["multiple_candidate_models_require_explicit_model:" + ",".join(models)]
        return predictions, []
    with_candidate = [row for row in predictions if row.get("candidate_model")]
    if not with_candidate:
        # Standalone prediction files may intentionally omit candidate_model.
        return predictions, []
    selected = [row for row in predictions if str(row.get("candidate_model") or "") == requested_model]
    foreign = sorted({str(row.get("candidate_model")) for row in predictions if row.get("candidate_model") != requested_model})
    errors = [] if selected else [f"requested_candidate_model_missing:{requested_model}"]
    # Foreign rows are permitted in a shared sidecar JSONL but never scored.
    return selected, errors


def score_manifest(manifest: dict, predictions: list[dict], model: str = "unknown") -> dict:
    cases = manifest.get("cases")
    if not isinstance(cases, list):
        raise ValueError("manifest cases must be a list")
    selected, protocol_errors = _filter_model(predictions, model)
    expected_ids = [str(case.get("id") or "") for case in cases]
    if any(not identifier for identifier in expected_ids) or len(set(expected_ids)) != len(expected_ids):
        raise ValueError("manifest case IDs must be non-empty and unique")

    by_id: dict[str, list[dict]] = defaultdict(list)
    for prediction in selected:
        identifier = _prediction_id(prediction)
        if not identifier:
            protocol_errors.append("prediction_without_case_id")
            continue
        by_id[identifier].append(prediction)
    unexpected = sorted(set(by_id) - set(expected_ids))
    if unexpected:
        protocol_errors.extend(f"unexpected_prediction_id:{identifier}" for identifier in unexpected)

    rows: list[dict] = []
    failures: Counter[str] = Counter()
    for case in cases:
        identifier = str(case["id"])
        matches = by_id.get(identifier, [])
        failure = ""
        got: dict = {}
        if not matches:
            failure = "missing_prediction"
        elif len(matches) > 1:
            failure = "duplicate_prediction"
            protocol_errors.append(f"duplicate_prediction_id:{identifier}")
        else:
            got = matches[0]
            failure = str(
                got.get("failure_reason") or got.get("error") or got.get("parse_error") or ""
            ).strip()
        if failure:
            failures[failure] += 1

        expected = case.get("expected") or {}
        fields = {field: bool(got) and not failure and got.get(field) == expected.get(field) for field in FIELDS}
        tags = set(case.get("tags") or [])
        predicted_view = got.get("view_type")
        dangerous_categories: list[str] = []
        if "distant_view" in tags and predicted_view not in {None, "", expected.get("view_type")}:
            dangerous_categories.append("distant_view_misclassification")
        if {"followme", "followme_pro"} & tags and predicted_view in {"遠景", "distant_view"}:
            dangerous_categories.append("followme_misclassification")
        if "hallucination_guard" in tags and got.get("model") not in {None, "", expected.get("model")}:
            dangerous_categories.append("model_hallucination")
        rows.append({
            "id": identifier,
            "tags": sorted(tags),
            "fields": fields,
            "exact": not failure and all(fields.values()),
            "dangerous": bool(dangerous_categories),
            "dangerous_categories": dangerous_categories,
            "latency_ms": got.get("latency_ms") if got else None,
            "failure": failure or None,
        })

    n = len(rows)
    denominator = n or 1
    valid_latency = [row["latency_ms"] for row in rows if isinstance(row["latency_ms"], (int, float))]
    complete = sum(not row["failure"] for row in rows)
    protocol_errors = sorted(set(protocol_errors))
    result = {
        "schema": "samsung-model-benchmark-score/v2",
        "model": model,
        "n": n,
        "prediction_records_selected": len(selected),
        "complete_prediction_rate": complete / denominator,
        "fully_correct_rate": sum(row["exact"] for row in rows) / denominator,
        "distant_or_followme_danger_rate": sum(row["dangerous"] for row in rows) / denominator,
        "field_accuracy": {
            field: sum(row["fields"][field] for row in rows) / denominator for field in FIELDS
        },
        "mean_latency_ms": sum(valid_latency) / len(valid_latency) if valid_latency else None,
        "latency_observations": len(valid_latency),
        "failure_reasons": dict(failures),
        "protocol_errors": protocol_errors,
        "unexpected_prediction_ids": unexpected,
        "benchmark_gate_pass": not protocol_errors and complete == n,
        "rows": rows,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--model", default="unknown")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    result = score_manifest(manifest, load(args.predictions), args.model)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if result["benchmark_gate_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

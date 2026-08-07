"""Read-only v19.45 live validation for three known cases.

This calls the already-loaded LM Studio model sequentially and never writes OCR
records, staging files, manifests, or images. Only the audit JSON is written.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from openai import OpenAI

import samsung_ocr_batch_processor as production
from skills.audit_fields import immediate_retry_decision
from skills.image_processing import ImageProcessor
from skills.prompt_versioning import PromptManager


CASES = (
    "M-新北市-汐止區-TK3C-汐止-1609.jpg",
    "M-新北市-林口區-eLife-林口館-193.jpg",
    "M-嘉義縣-東　區-TK3C-中埔-1180.jpg",
)


def json_safe(value):
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items() if k not in {"thumb_b64", "image_b64", "base64"}}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", default=r"D:\00_商化\00_已OCR照片\_ocr_staging\20260713_214353\202604_商化照片-202604_a6dfe521")
    parser.add_argument("--endpoint", default="http://127.0.0.1:1234/v1")
    parser.add_argument("--model", default="qwen/qwen3-vl-8b")
    parser.add_argument("--output", default=None)
    parser.add_argument("--only", default=None, help="Run one named case only")
    args = parser.parse_args()

    root = Path(args.source_root).resolve()
    output = Path(args.output or (Path(__file__).resolve().parents[1] / "logs" / f"v1945_live_validation_{time.strftime('%Y%m%d_%H%M%S')}.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    # Never let the SDK issue an uncounted second request.  This validation
    # helper must obey the same transport contract as production.
    client = OpenAI(
        base_url=args.endpoint,
        api_key="lm-studio",
        timeout=180.0,
        max_retries=0,
    )
    production.api_client = client
    production.model_name_global = args.model
    production.orchestrator = None
    prompt_mgr = PromptManager(str(Path(__file__).resolve().parents[1] / "assets"))
    image_processor = ImageProcessor({"max_size": None, "max_dimensions": (2560, 1440), "detect_label_card": True, "bottom_label_strip": False, "bottom_center_zoom": False})
    records = []
    cases = tuple(fname for fname in CASES if not args.only or args.only in fname)
    if args.only and not cases:
        raise SystemExit(f"unknown case: {args.only}")
    for fname in cases:
        path = root / fname
        if not path.is_file():
            records.append({"file_name": fname, "error": "source_missing"})
            continue
        previous = []
        passes = []
        for attempt in range(1, 4):
            started = time.perf_counter()
            processed = image_processor.process(str(path))
            result = production.process_single_image(
                fname=fname,
                image_b64=processed["base64"],
                prompt_mgr=prompt_mgr,
                image_processor=image_processor,
                processed_image=processed,
                ocr_attempt=attempt,
                previous_results=previous,
            )
            decision = immediate_retry_decision(result, attempt, previous, 3)
            elapsed = round(time.perf_counter() - started, 3)
            safe_result = json_safe(result)
            pass_record = {"attempt": attempt, "latency_seconds": elapsed, "parsed_output": safe_result, "guard_decision": json_safe(decision)}
            passes.append(pass_record)
            previous.append(result)
            if not decision.get("retry"):
                break
        final = passes[-1]
        final_decision = final["guard_decision"]
        records.append({
            "file_name": fname,
            "source_path": str(path),
            "passes": passes,
            "final_decision": final_decision,
            "expected_class": "FollowMe_or_review" if "林口館" in fname else "distant_or_review",
        })
    output.write_text(json.dumps({"contract": "v19.45", "model": args.model, "endpoint": args.endpoint, "concurrency": 1, "records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PROJECT_ROOT / "tools" / "qwen_vl_regression_cases_202603_all.json"
DEFAULT_API_BASE = "http://127.0.0.1:1234/v1"
DEFAULT_MODEL = os.environ.get("LOCAL_LLM_MODEL", "qwen/qwen3-vl-8b")


def ensure_local_llm(api_base: str, model: str) -> None:
    manager = PROJECT_ROOT / "tools" / "local_llm_manager.py"
    if not manager.exists():
        return
    env = os.environ.copy()
    env.setdefault("LOCAL_LLM_API_BASE", api_base)
    env.setdefault("LOCAL_LLM_MODEL", model)
    if "LOCAL_LLM_MODEL_KEY" not in env:
        if "4b" in model.lower():
            env["LOCAL_LLM_MODEL_KEY"] = "qwen/qwen3-vl-4b"
        elif "8b" in model.lower():
            env["LOCAL_LLM_MODEL_KEY"] = "qwen/qwen3-vl-8b"
    subprocess.run([sys.executable, str(manager), "ensure"], cwd=PROJECT_ROOT, env=env, check=True)


def check_lm_studio(api_base: str, model: str) -> None:
    url = f"{api_base.rstrip('/')}/models"
    response = requests.get(url, timeout=5)
    response.raise_for_status()
    data = response.json().get("data", [])
    ids = {item.get("id") for item in data}
    if model not in ids:
        available = ", ".join(sorted(item for item in ids if item))
        raise RuntimeError(f"LM Studio reachable, but model '{model}' is not loaded. Available: {available}")


def count_cases(path: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return len(data)


def load_cases(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def run_regression(command: list[str]) -> int:
    completed = subprocess.run(command, cwd=PROJECT_ROOT)
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Samsung OCR Qwen-VL prompt guard regression.")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--quick", action="store_true", help="Run only the first 6 cases for a fast smoke check.")
    parser.add_argument("--limit", type=int, default=0, help="Override number of cases to run.")
    parser.add_argument("--max-side", type=int, default=2400)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--skip-llm-start", action="store_true", help="Do not auto-start LM Studio via tools/local_llm_manager.py.")
    args = parser.parse_args()

    cases_path = Path(args.cases)
    if not cases_path.is_absolute():
        cases_path = PROJECT_ROOT / cases_path
    if not cases_path.exists():
        raise FileNotFoundError(cases_path)

    limit = args.limit
    if args.quick and limit <= 0:
        limit = 6

    total = count_cases(cases_path)
    run_count = min(limit, total) if limit > 0 else total
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"limit{run_count}" if limit > 0 else "full"
    output = PROJECT_ROOT / "runs" / f"qwen_vl_guard_{suffix}_{stamp}.json"

    if not args.skip_llm_start:
        ensure_local_llm(args.api_base, args.model)
    check_lm_studio(args.api_base, args.model)

    command = [
        sys.executable,
        str(PROJECT_ROOT / "tools" / "qwen_vl_regression.py"),
        "--api-base",
        args.api_base,
        "--model",
        args.model,
        "--cases",
        str(cases_path),
        "--max-side",
        str(args.max_side),
        "--bottom-label-strip",
        "--normalize-backend",
        "--timeout",
        str(args.timeout),
        "--output",
        str(output),
    ]
    if limit > 0:
        command.extend(["--limit", str(limit)])

    print(f"cases={run_count}/{total}")
    print(f"model={args.model}")
    print(f"output={output}")
    run_regression(command)

    results = load_cases(output)
    failed_names = {item.get("name") for item in results if not item.get("passed")}
    if not failed_names:
        return 0

    original_cases = load_cases(cases_path)
    retry_cases = [case for case in original_cases if case.get("name") in failed_names]
    retry_cases_path = PROJECT_ROOT / "runs" / f"qwen_vl_guard_retry_cases_{stamp}.json"
    retry_output = PROJECT_ROOT / "runs" / f"qwen_vl_guard_retry_{stamp}.json"
    retry_cases_path.write_text(json.dumps(retry_cases, ensure_ascii=False, indent=2), encoding="utf-8")

    retry_command = [
        sys.executable,
        str(PROJECT_ROOT / "tools" / "qwen_vl_regression.py"),
        "--api-base",
        args.api_base,
        "--model",
        args.model,
        "--cases",
        str(retry_cases_path),
        "--max-side",
        str(min(args.max_side, 2200)),
        "--bottom-label-strip",
        "--bottom-center-zoom",
        "--normalize-backend",
        "--timeout",
        str(args.timeout),
        "--output",
        str(retry_output),
    ]
    print(f"retry_failed={len(retry_cases)}")
    print(f"retry_output={retry_output}")
    run_regression(retry_command)

    retry_results = {item.get("name"): item for item in load_cases(retry_output)}
    merged = [retry_results.get(item.get("name"), item) if not item.get("passed") else item for item in results]
    output.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    passed = sum(1 for item in merged if item.get("passed"))
    print(f"merged_summary={passed}/{len(merged)} passed")
    return 0 if passed == len(merged) else 1


if __name__ == "__main__":
    raise SystemExit(main())

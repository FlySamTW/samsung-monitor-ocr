"""Bounded, local-only VLM benchmark sidecar.

The sidecar is intentionally separate from the OCR runtime.  It refuses to
run while OCR or rerun work is active, requires --execute for model changes,
and writes one immutable JSONL record per (model, case).  It never uploads
images or contacts a non-local endpoint.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs" / "model_benchmark_manifest.json"
DEFAULT_OUT = ROOT / "runs" / "model_benchmark_sidecar"
DEFAULT_RUNTIME_OUTPUT = Path(r"D:\00_商化\00_已OCR照片")
LOCK_NAME = "model_benchmark.lock"
DEFAULT_MODEL = "qwen/qwen3-vl-8b"
DEFAULT_CONTEXT = 16384
DEFAULT_CANDIDATES = [
    DEFAULT_MODEL, "qwen3.5-9b-vlm", "gemma-4-12b-it-qat",
    "qwen/qwen2.5-vl-7b", "minicpm-v-4.6", "internvl3_5-8b",
]


class SafetyError(RuntimeError):
    pass


def benchmark_lock_path(output_dir: Path) -> Path:
    return output_dir / "_ocr_audit" / LOCK_NAME


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire_benchmark_lock(path: Path, models: list[str], *, recover_stale: bool = False,
                           stale_age_seconds: int = 3600,
                           pid_exists: Callable[[int], bool] = _pid_exists) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            owner = int(current.get("pid", 0))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise SafetyError(f"benchmark lock exists but is unreadable: {path}") from exc
        age = max(0.0, time.time() - path.stat().st_mtime)
        if not recover_stale or age < stale_age_seconds or pid_exists(owner):
            raise SafetyError(f"benchmark lock already exists: {path}")
        path.unlink()
    payload = {"pid": os.getpid(), "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "created_epoch": time.time(), "models": list(models)}
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except FileExistsError as exc:
        raise SafetyError(f"benchmark lock was claimed concurrently: {path}") from exc
    return payload


def release_benchmark_lock(path: Path, owner_pid: int) -> None:
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
        if int(current.get("pid", -1)) == owner_pid:
            path.unlink(missing_ok=True)
    except FileNotFoundError:
        return
    except (OSError, ValueError, json.JSONDecodeError):
        # Never delete an unreadable lock in cleanup; conservative recovery is explicit.
        return


def local_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise SafetyError("benchmark endpoint must be a local HTTP LM Studio endpoint")


def get_json(url: str, timeout: int = 10) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def lms_path() -> str:
    found = shutil.which("lms")
    if found:
        return found
    candidate = Path(os.environ.get("USERPROFILE", "")) / ".lmstudio" / "bin" / "lms.exe"
    if candidate.exists():
        return str(candidate)
    raise SafetyError("LM Studio lms CLI was not found")


def run_lms(lms: str, args: list[str], timeout: int = 600) -> str:
    p = subprocess.run([lms, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    if p.returncode:
        raise SafetyError(f"lms {' '.join(args)} failed: {p.stdout}\n{p.stderr}")
    return (p.stdout or p.stderr).strip()


def loaded_snapshot(lms: str) -> dict[str, dict[str, Any]]:
    out = run_lms(lms, ["ps"], 30)
    result: dict[str, dict[str, Any]] = {}
    for line in out.splitlines():
        parts = line.split()
        if not parts or parts[0].upper() in {"IDENTIFIER", "NO"}:
            continue
        context = next((int(x) for x in parts[1:] if x.isdigit() and int(x) >= 1024), None)
        result[parts[0]] = {"context_length": context, "line": line}
    return result


def project_processes() -> list[str]:
    if os.name != "nt":
        return []
    command = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "$items=@(Get-CimInstance Win32_Process | ForEach-Object { "
        "if ($_.CommandLine) { [string]$_.CommandLine } }); "
        "ConvertTo-Json -InputObject $items -Compress"
    )
    p = subprocess.run(["powershell", "-NoProfile", "-Command", command],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
    if p.returncode:
        raise SafetyError(f"cannot enumerate project processes: {p.stderr.strip() or p.stdout.strip()}")
    try:
        values = json.loads(p.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise SafetyError("cannot parse project process inventory") from exc
    if not isinstance(values, list):
        values = [values]
    root = str(ROOT).casefold()
    return [str(value) for value in values if value and root in str(value).casefold()]


def assert_idle(
    status_url: str,
    processes: list[str] | None = None,
    status_getter: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    status = status_getter() if status_getter else get_json(status_url)
    if status.get("is_running"):
        raise SafetyError("OCR backend is running; benchmark refused")
    processes = project_processes() if processes is None else processes
    active = [p for p in processes if re.search(
        r"auto_rerun|rerun_staged|rerun_questionable|recursive_ocr|watcher|uploader|rclone", p, re.I)]
    if active:
        raise SafetyError("rerun/recursive/watcher/uploader process is active; benchmark refused")
    return status


def visible_models(lms: str) -> set[str]:
    return {line.split()[0] for line in run_lms(lms, ["ls"], 30).splitlines()
            if line.strip() and not line.startswith(("You have", "LLM", "EMBEDDING"))}


def encode_image(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def crop_bytes(path: Path) -> list[str]:
    """Build deterministic evidence crops from the original sample image."""
    try:
        from PIL import Image
        from io import BytesIO
        with Image.open(path) as im:
            w, h = im.size
            boxes = [(0, int(h * .55), w, h), (int(w * .2), int(h * .2), int(w * .8), int(h * .8))]
            result = []
            for box in boxes:
                crop = im.crop(box)
                buf = BytesIO()
                crop.save(buf, format="JPEG", quality=95, subsampling=0)
                result.append(base64.b64encode(buf.getvalue()).decode("ascii"))
            return result
    except Exception as exc:
        raise SafetyError(f"cannot create deterministic image evidence: {exc}") from exc


def parse_prediction(text: str) -> tuple[dict[str, Any] | None, str | None]:
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.S | re.I)
    candidate = fenced.group(1) if fenced else candidate
    try:
        value = json.loads(candidate)
        return (value if isinstance(value, dict) else None), None
    except json.JSONDecodeError as exc:
        return None, f"json_parse_error: {exc.msg}"


def post_completion(api_base: str, model: str, prompt: str, images: list[str], timeout: int) -> str:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    content += [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{x}"}} for x in images]
    payload = {"model": model, "temperature": 0, "top_p": 1, "messages": [{"role": "user", "content": content}]}
    req = urllib.request.Request(api_base.rstrip("/") + "/chat/completions",
                                 json.dumps(payload).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = json.loads(response.read().decode())
    return data["choices"][0]["message"]["content"]


def score(case: dict[str, Any], prediction: dict[str, Any] | None, latency: float, error: str | None) -> dict[str, Any]:
    expected = case.get("expected", {})
    got = prediction or {}
    fields = {k: got.get(k) == expected.get(k) for k in ("view_type", "model", "price")}
    tags = set(case.get("tags", []))
    dangerous = []
    if "distant_view" in tags and got.get("view_type") not in {None, expected.get("view_type")}:
        dangerous.append("distant_view_misclassification")
    if ("followme" in tags or "followme_pro" in tags) and got.get("view_type") in {"distant_view", "遠景"}:
        dangerous.append("followme_misclassification")
    if "hallucination_guard" in tags and got.get("model") not in {None, expected.get("model")}:
        dangerous.append("model_hallucination")
    return {"id": case["id"], **{k: got.get(k) for k in ("view_type", "model", "price")},
            "fields": fields, "exact": all(fields.values()), "dangerous_categories": dangerous,
            "latency_ms": round(latency * 1000, 2), "parse_error": error}


def run(args: argparse.Namespace, *, status_getter: Callable[[], dict[str, Any]] | None = None,
        process_getter: Callable[[], list[str]] | None = None, lms: str | None = None,
        completion: Callable[[str, str, list[str], int], str] | None = None) -> dict[str, Any]:
    local_url(args.api_base)
    status_getter = status_getter or (lambda: get_json(args.backend_url.rstrip("/") + "/api/status"))
    status = assert_idle(
        args.backend_url.rstrip("/") + "/api/status",
        process_getter() if process_getter else None,
        status_getter=status_getter,
    )
    manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    requested = bool(args.models)
    lms = lms or lms_path()
    available = visible_models(lms)
    candidates = args.models or manifest.get("candidates", DEFAULT_CANDIDATES)
    selected = [m for m in candidates if m in available] if not requested else candidates
    missing = [m for m in selected if m not in available]
    if missing:
        raise SafetyError("requested model(s) are not fully downloaded/visible: " + ", ".join(missing))
    snapshot = loaded_snapshot(lms)
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    raw = output / "raw.jsonl"
    done = {json.loads(x)["key"] for x in raw.read_text(encoding="utf-8").splitlines() if x.strip()} if raw.exists() else set()
    prompt = args.prompt.read_text(encoding="utf-8")
    cases = manifest["cases"]
    results: list[dict[str, Any]] = []
    lock = benchmark_lock_path(args.runtime_output_dir)
    lock_owner = acquire_benchmark_lock(lock, selected, recover_stale=args.recover_stale_lock,
                                        stale_age_seconds=args.stale_lock_age_seconds)
    try:
        # Claim first, then re-check idle state to close the watcher/sidecar race.
        status = assert_idle(
            args.backend_url.rstrip("/") + "/api/status",
            process_getter() if process_getter else None,
            status_getter=status_getter,
        )
        for model in selected:
            for identifier in list(loaded_snapshot(lms)):
                run_lms(lms, ["unload", identifier], 120)
            run_lms(lms, ["load", model, "--context-length", str(args.context_length), "--gpu", "max", "--identifier", model, "--parallel", "1", "--yes"], 600)
            actual_context = loaded_snapshot(lms).get(model, {}).get("context_length", args.context_length)
            for case in cases:
                key = f"{model}:{case['id']}"
                if key in done:
                    continue
                image = ROOT / "samples" / "ocr_demo_50" / case["image"]
                started = time.perf_counter()
                error = None
                prediction = None
                try:
                    text = (completion or post_completion)(args.api_base, model, prompt, [encode_image(image), *crop_bytes(image)], args.timeout)
                    prediction, error = parse_prediction(text)
                except Exception as exc:
                    error = f"inference_error: {exc}"
                    text = ""
                row = {"key": key, "candidate_model": model, "case_id": case["id"], "context_length": actual_context,
                       "raw_text": text, "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       **score(case, prediction, time.perf_counter() - started, error)}
                with raw.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                results.append(row)
    finally:
        try:
            for identifier in list(loaded_snapshot(lms)):
                run_lms(lms, ["unload", identifier], 120)
            run_lms(lms, ["load", DEFAULT_MODEL, "--context-length", str(args.context_length), "--gpu", "max", "--identifier", DEFAULT_MODEL, "--parallel", "1", "--yes"], 600)
        finally:
            release_benchmark_lock(lock, int(lock_owner["pid"]))
    summary = {"schema": "samsung-model-benchmark-sidecar/v1", "status_snapshot": status,
               "loaded_models_before": snapshot,
               "baseline_model": DEFAULT_MODEL, "baseline_context_length": snapshot.get(DEFAULT_MODEL, {}).get("context_length", args.context_length),
               "models": selected, "raw_jsonl": str(raw), "new_records": len(results),
               "dangerous_errors": sum(len(x["dangerous_categories"]) for x in results)}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    p = argparse.ArgumentParser(description="Fail-closed local LM Studio benchmark sidecar")
    p.add_argument("--execute", action="store_true", help="required to load models and infer")
    p.add_argument("--api-base", default="http://127.0.0.1:1234/v1")
    p.add_argument("--backend-url", default="http://127.0.0.1:5000")
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    p.add_argument("--prompt", type=Path, default=ROOT / "samsung_ocr_prompt.txt")
    p.add_argument("--output", type=Path, default=DEFAULT_OUT)
    p.add_argument("--runtime-output-dir", type=Path, default=DEFAULT_RUNTIME_OUTPUT,
                   help="OCR output root containing _ocr_audit/model_benchmark.lock")
    p.add_argument("--context-length", type=int, default=DEFAULT_CONTEXT)
    p.add_argument("--timeout", type=int, default=120)
    p.add_argument("--models", nargs="*", default=[])
    p.add_argument("--recover-stale-lock", action="store_true")
    p.add_argument("--stale-lock-age-seconds", type=int, default=3600)
    args = p.parse_args()
    if not args.execute:
        print(json.dumps({"dry_run": True, "execute_required": True, "models": args.models or DEFAULT_CANDIDATES}, ensure_ascii=False, indent=2))
        return 0
    try:
        print(json.dumps(run(args), ensure_ascii=False, indent=2))
        return 0
    except (SafetyError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "fail_closed": True, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

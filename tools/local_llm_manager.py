import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_API_BASE = "http://127.0.0.1:1234/v1"
DEFAULT_CONTEXT_LENGTH = 16384
DEFAULT_GPU = "max"
DEFAULT_PARALLEL = 1
DEFAULT_PRIMARY_IDENTIFIER = "qwen3vl8b-ocr"
DEFAULT_PRIMARY_MODEL_KEY = "qwen/qwen3-vl-8b"
DEFAULT_FALLBACK_IDENTIFIER = "qwen3vl4b-ocr"
DEFAULT_FALLBACK_MODEL_KEY = "qwen/qwen3-vl-4b"


def env(name, default):
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def find_lms():
    found = shutil.which("lms")
    if found:
        return found

    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        candidate = Path(user_profile) / ".lmstudio" / "bin" / "lms.exe"
        if candidate.exists():
            return str(candidate)
    return None


def run_command(args, timeout=120):
    completed = subprocess.run(
        args,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    return completed.returncode, completed.stdout.strip()


def api_url(api_base, suffix):
    return f"{api_base.rstrip('/')}/{suffix.lstrip('/')}"


def fetch_json(url, timeout=5):
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def list_api_models(api_base):
    data = fetch_json(api_url(api_base, "models"))
    return [item.get("id") for item in data.get("data", []) if item.get("id")]


def wait_for_server(api_base, timeout_sec=30):
    deadline = time.time() + timeout_sec
    last_error = None
    while time.time() < deadline:
        try:
            return list_api_models(api_base)
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"LM Studio server did not become ready: {last_error}")


def parse_loaded_models(ps_output):
    loaded = {}
    for raw_line in ps_output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("IDENTIFIER") or line.startswith("No models"):
            continue
        parts = line.split()
        if len(parts) < 6:
            continue
        identifier = parts[0]
        context = None
        for token in parts[3:]:
            if token.isdigit() and int(token) >= 1024:
                context = int(token)
                break
        loaded[identifier] = {
            "line": line,
            "context": context,
        }
    return loaded


def get_loaded_models(lms_path):
    code, output = run_command([lms_path, "ps"], timeout=30)
    if code != 0:
        raise RuntimeError(f"lms ps failed:\n{output}")
    return parse_loaded_models(output)


def start_server(lms_path, port):
    code, output = run_command(
        [lms_path, "server", "start", "--port", str(port), "--bind", "127.0.0.1"],
        timeout=60,
    )
    if code != 0:
        raise RuntimeError(f"lms server start failed:\n{output}")
    return output


def unload_model(lms_path, identifier):
    code, output = run_command([lms_path, "unload", identifier], timeout=120)
    if code != 0:
        raise RuntimeError(f"lms unload {identifier} failed:\n{output}")
    return output


def load_model(lms_path, model_key, identifier, context_length, gpu, parallel):
    command = [
        lms_path,
        "load",
        model_key,
        "--context-length",
        str(context_length),
        "--gpu",
        gpu,
        "--identifier",
        identifier,
        "--parallel",
        str(parallel),
        "--yes",
    ]
    code, output = run_command(command, timeout=600)
    if code != 0:
        raise RuntimeError(f"lms load {model_key} failed:\n{output}")
    return output


def model_pairs(args):
    primary_identifier = env("LOCAL_LLM_MODEL", args.model)
    primary_model_key = env("LOCAL_LLM_MODEL_KEY", infer_model_key(primary_identifier, args.model_key))
    fallback_identifier = env("LOCAL_LLM_FALLBACK_MODEL", args.fallback_model)
    fallback_model_key = env("LOCAL_LLM_FALLBACK_MODEL_KEY", infer_model_key(fallback_identifier, args.fallback_model_key))
    return [
        (primary_identifier, primary_model_key),
        (fallback_identifier, fallback_model_key),
    ]


def infer_model_key(identifier, default):
    text = str(identifier or "").lower()
    if "4b" in text:
        return DEFAULT_FALLBACK_MODEL_KEY
    if "8b" in text:
        return DEFAULT_PRIMARY_MODEL_KEY
    return default


def ensure(args):
    api_base = env("LOCAL_LLM_API_BASE", args.api_base)
    context_length = int(env("LOCAL_LLM_CONTEXT_LENGTH", str(args.context_length)))
    gpu = env("LOCAL_LLM_GPU", args.gpu)
    parallel = int(env("LOCAL_LLM_PARALLEL", str(args.parallel)))
    port = urllib.parse.urlparse(api_base).port or 1234
    lms_path = find_lms()
    if not lms_path:
        raise RuntimeError(
            "找不到 LM Studio CLI (lms)。請先安裝 LM Studio，或確認 C:\\Users\\<你>\\.lmstudio\\bin 在 PATH。"
        )

    print(f"[LLM] lms: {lms_path}")
    print(f"[LLM] API: {api_base}")

    try:
        list_api_models(api_base)
        print("[LLM] LM Studio server 已在運行。")
    except (urllib.error.URLError, TimeoutError, RuntimeError):
        print("[LLM] LM Studio server 未啟動，正在用 lms server start 啟動...")
        start_server(lms_path, port)
        wait_for_server(api_base)

    loaded = get_loaded_models(lms_path)
    last_error = None

    for identifier, model_key in model_pairs(args):
        if identifier in loaded:
            loaded_info = loaded.get(identifier, {})
            current_context = loaded_info.get("context")
            if current_context and current_context < context_length:
                print(f"[LLM] {identifier} context={current_context}，低於 {context_length}，重新載入...")
                unload_model(lms_path, identifier)
                loaded = get_loaded_models(lms_path)
            else:
                print(f"[LLM] 已載入模型：{identifier}")
                write_state(api_base, identifier, context_length, gpu, parallel, model_key)
                return identifier

        try:
            print(f"[LLM] 載入模型：{model_key} -> {identifier}")
            load_model(lms_path, model_key, identifier, context_length, gpu, parallel)
            wait_for_server(api_base)
            loaded = get_loaded_models(lms_path)
            if identifier not in loaded:
                raise RuntimeError(f"模型載入後 lms ps 未看到 identifier: {identifier}")
            write_state(api_base, identifier, context_length, gpu, parallel, model_key)
            print(f"[LLM] 完成：{identifier}")
            return identifier
        except Exception as exc:
            last_error = exc
            print(f"[LLM] 載入 {model_key} 失敗：{exc}")

    raise RuntimeError(f"主要與備援模型都無法載入。最後錯誤：{last_error}")


def write_state(api_base, identifier, context_length, gpu, parallel, model_key):
    state_path = Path(__file__).resolve().parents[1] / ".local_llm_runtime.json"
    state = {
        "api_base": api_base,
        "model": identifier,
        "model_key": model_key,
        "context_length": context_length,
        "gpu": gpu,
        "parallel": parallel,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def status(args):
    api_base = env("LOCAL_LLM_API_BASE", args.api_base)
    lms_path = find_lms()
    print(f"[LLM] lms: {lms_path or 'not found'}")
    print(f"[LLM] API: {api_base}")
    if lms_path:
        code, output = run_command([lms_path, "server", "status"], timeout=30)
        print(output if output else f"lms server status exited with {code}")
        code, output = run_command([lms_path, "ps"], timeout=30)
        print(output if output else f"lms ps exited with {code}")
    try:
        print("[LLM] /v1/models:", ", ".join(list_api_models(api_base)) or "(empty)")
    except Exception as exc:
        print(f"[LLM] /v1/models unavailable: {exc}")


def main():
    parser = argparse.ArgumentParser(description="Start and validate the local LM Studio OCR model.")
    parser.add_argument("action", nargs="?", choices=["ensure", "status"], default="ensure")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--model", default=DEFAULT_PRIMARY_IDENTIFIER)
    parser.add_argument("--model-key", default=DEFAULT_PRIMARY_MODEL_KEY)
    parser.add_argument("--fallback-model", default=DEFAULT_FALLBACK_IDENTIFIER)
    parser.add_argument("--fallback-model-key", default=DEFAULT_FALLBACK_MODEL_KEY)
    parser.add_argument("--context-length", type=int, default=DEFAULT_CONTEXT_LENGTH)
    parser.add_argument("--gpu", default=DEFAULT_GPU)
    parser.add_argument("--parallel", type=int, default=DEFAULT_PARALLEL)
    args = parser.parse_args()

    try:
        if args.action == "status":
            status(args)
            return 0
        identifier = ensure(args)
        print(f"[LLM] READY model={identifier}")
        return 0
    except Exception as exc:
        print(f"[LLM] ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

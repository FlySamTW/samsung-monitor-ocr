#!/usr/bin/env python3
"""Resume the original recursive OCR batch: backend + watcher."""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = Path(r"D:\00_商化\00_未整理商化照片")
OUTPUT_DIR = Path(r"D:\00_商化\00_已OCR照片")
BACKEND_URL = "http://127.0.0.1:5002"
API_BASE = "http://127.0.0.1:1234/v1"
API_KEY = "lm-studio"
MODEL = "qwen/qwen3-vl-8b"


def json_request(method: str, path: str, payload=None, timeout=30):
    import urllib.request
    import json
    url = BACKEND_URL.rstrip("/") + path
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
    headers = {"Content-Type": "application/json; charset=utf-8"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_for_backend(timeout_seconds=120):
    deadline = time.time() + timeout_seconds
    last_error = None
    while time.time() < deadline:
        try:
            json_request("GET", "/api/status", timeout=5)
            print("[INFO] Backend ready.", flush=True)
            return
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"Backend not ready: {last_error}")


def main():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    backend_cmd = [
        sys.executable,
        str(ROOT / "samsung_ocr_batch_processor.py"),
        "--api_base", API_BASE,
        "--api_key", API_KEY,
        "--model", MODEL,
        "--dir", str(SOURCE_ROOT),
        "--no_followme_auto_update",
    ]
    print(f"[INFO] Starting backend...", flush=True)
    backend_proc = subprocess.Popen(
        backend_cmd,
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    watcher_cmd = [
        sys.executable,
        str(ROOT / "tools" / "recursive_ocr_flat_export.py"),
        "--source-root", str(SOURCE_ROOT),
        "--output-dir", str(OUTPUT_DIR),
        "--backend-url", BACKEND_URL,
        "--api-base", API_BASE,
        "--api-key", API_KEY,
        "--model", MODEL,
        "--poll-seconds", "20",
        "--timeout-minutes", "360",
        "--watch",
        "--watch-sleep-seconds", "60",
    ]

    watcher_proc = None
    try:
        wait_for_backend()
        print(f"[INFO] Starting watcher...", flush=True)
        watcher_proc = subprocess.Popen(
            watcher_cmd,
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"[INFO] Backend PID: {backend_proc.pid}, Watcher PID: {watcher_proc.pid}", flush=True)
        # Keep this script alive; watcher runs indefinitely.
        while True:
            if backend_proc.poll() is not None:
                raise RuntimeError("Backend exited unexpectedly")
            if watcher_proc.poll() is not None:
                raise RuntimeError("Watcher exited unexpectedly")
            time.sleep(10)
    except KeyboardInterrupt:
        print("[INFO] Stopping...", flush=True)
    finally:
        for proc in (watcher_proc, backend_proc):
            if proc:
                try:
                    proc.terminate()
                    proc.wait(timeout=10)
                except Exception:
                    proc.kill()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] {e}", flush=True)
        sys.exit(1)

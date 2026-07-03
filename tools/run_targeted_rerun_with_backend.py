#!/usr/bin/env python3
"""Start backend, run targeted rerun folder, wait for completion, then stop."""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / "runs"
IMAGE_DIR = ROOT / "targeted_rerun_2026_temp"
BACKEND_URL = "http://127.0.0.1:5000"
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


def wait_for_done(timeout_minutes=240):
    deadline = time.time() + (timeout_minutes * 60)
    last_print = 0
    while time.time() < deadline:
        try:
            status = json_request("GET", "/api/status", timeout=30)
            stats = status.get("stats") or {}
            running = bool(status.get("is_running") or stats.get("is_running"))
            if time.time() - last_print >= 10:
                print(
                    f"[PROGRESS] processed={stats.get('processed', 0)}/{stats.get('total', 0)} "
                    f"success={stats.get('success', 0)} failed={stats.get('failed', 0)} running={running}",
                    flush=True,
                )
                last_print = time.time()
            if not running and stats.get("processed", 0) > 0:
                print("[INFO] Batch completed.", flush=True)
                return status
        except Exception as exc:
            print(f"[WARN] Status error: {exc}", flush=True)
        time.sleep(2)
    raise RuntimeError("Timeout waiting for batch completion")


def main():
    parser = argparse.ArgumentParser(description="Run targeted OCR rerun with backend.")
    parser.add_argument("--bottom-label-strip", action="store_true", help="Add lower full-width price-label strip crop")
    parser.add_argument("--bottom-center-zoom", action="store_true", help="Add enlarged lower-center price-label crop")
    parser.add_argument("--image-dir", type=Path, default=IMAGE_DIR, help="Temp folder with images to rerun")
    args = parser.parse_args()

    image_dir = Path(args.image_dir)
    if not image_dir.exists() or not any(image_dir.iterdir()):
        raise FileNotFoundError(f"Target folder empty or missing: {image_dir}")

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    backend_cmd = [
        sys.executable,
        str(ROOT / "samsung_ocr_batch_processor.py"),
        "--api_base", API_BASE,
        "--api_key", API_KEY,
        "--model", MODEL,
        "--dir", str(image_dir),
        "--no_followme_auto_update",
    ]
    if args.bottom_label_strip:
        backend_cmd.append("--bottom_label_strip")
    if args.bottom_center_zoom:
        backend_cmd.append("--bottom_center_zoom")

    print(f"[INFO] Starting backend: {' '.join(backend_cmd)}", flush=True)
    backend_proc = subprocess.Popen(
        backend_cmd,
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        wait_for_backend()

        # Set work dir and start batch
        json_request("POST", "/api/set_work_dir", {"dir": str(image_dir)}, timeout=30)
        payload = {"dir": str(image_dir), "restart": True, "confirmed": True, "reprocess_last_n": 0}
        response = json_request("POST", "/api/start_batch", payload, timeout=30)
        if response.get("status") == "needs_confirmation":
            response = json_request("POST", "/api/start_batch", payload, timeout=30)
        if response.get("error"):
            raise RuntimeError(response["error"])
        print(f"[INFO] Start batch response: {response}", flush=True)

        wait_for_done()

        # Find the latest run dir
        run_dirs = sorted(RUNS_DIR.glob(f"*{image_dir.name}*"), key=lambda p: p.stat().st_mtime, reverse=True)
        if run_dirs:
            print(f"[INFO] Run dir: {run_dirs[0]}", flush=True)
        else:
            print("[WARN] No run dir found", flush=True)

    finally:
        print("[INFO] Stopping backend...", flush=True)
        try:
            json_request("POST", "/api/stop", timeout=10)
        except Exception:
            pass
        try:
            backend_proc.terminate()
            backend_proc.wait(timeout=10)
        except Exception:
            backend_proc.kill()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] {e}", flush=True)
        sys.exit(1)

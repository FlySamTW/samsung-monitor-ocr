"""Publish temporary same-origin GPU telemetry until the backend exposes it."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.system_resources import read_gpu_resources


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def backend_has_gpu(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return (payload.get("resources") or {}).get("gpu") is not None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-file", type=Path, required=True)
    parser.add_argument("--status-url", default="http://127.0.0.1:5002/api/status?compact=1")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()

    while True:
        if backend_has_gpu(args.status_url):
            return 0
        payload = {
            **read_gpu_resources(cache_seconds=0),
            "updated_at": datetime.now().astimezone().isoformat(),
        }
        _atomic_json(args.output_file.resolve(), payload)
        time.sleep(max(0.5, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())

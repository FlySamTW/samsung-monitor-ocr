"""Low-overhead host resource telemetry for the OCR Dashboard."""

from __future__ import annotations

import subprocess
import threading
import time
from typing import Any, Callable


_GPU_CACHE_LOCK = threading.Lock()
_GPU_CACHE_AT = 0.0
_GPU_CACHE_VALUE: dict[str, Any] = {
    "gpu": None,
    "vram_used_mb": None,
    "vram_total_mb": None,
    "vram_percent": None,
}


def _reset_gpu_cache_for_tests() -> None:
    global _GPU_CACHE_AT, _GPU_CACHE_VALUE
    with _GPU_CACHE_LOCK:
        _GPU_CACHE_AT = 0.0
        _GPU_CACHE_VALUE = {
            "gpu": None,
            "vram_used_mb": None,
            "vram_total_mb": None,
            "vram_percent": None,
        }


def read_gpu_resources(
    *,
    runner: Callable[..., Any] = subprocess.run,
    now_fn: Callable[[], float] = time.monotonic,
    cache_seconds: float = 2.0,
) -> dict[str, Any]:
    """Return cached NVIDIA utilization without opening a console window."""
    global _GPU_CACHE_AT, _GPU_CACHE_VALUE
    now = float(now_fn())
    with _GPU_CACHE_LOCK:
        if _GPU_CACHE_AT and now - _GPU_CACHE_AT < max(0.0, cache_seconds):
            return dict(_GPU_CACHE_VALUE)

    value = {
        "gpu": None,
        "vram_used_mb": None,
        "vram_total_mb": None,
        "vram_percent": None,
    }
    try:
        completed = runner(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        line = next(
            item.strip()
            for item in str(completed.stdout or "").splitlines()
            if item.strip()
        )
        utilization, used, total = (
            float(part.strip()) for part in line.split(",")[:3]
        )
        if total <= 0:
            raise ValueError("NVIDIA memory total must be positive")
        value = {
            "gpu": round(utilization, 1),
            "vram_used_mb": round(used),
            "vram_total_mb": round(total),
            "vram_percent": round(used * 100.0 / total, 1),
        }
    except (OSError, ValueError, TypeError, StopIteration, subprocess.SubprocessError):
        pass

    with _GPU_CACHE_LOCK:
        _GPU_CACHE_AT = now
        _GPU_CACHE_VALUE = dict(value)
    return value

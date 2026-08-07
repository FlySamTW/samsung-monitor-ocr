from __future__ import annotations

import subprocess
import unittest

from skills.system_resources import (
    _reset_gpu_cache_for_tests,
    read_gpu_resources,
)


class GpuResourceTests(unittest.TestCase):
    def setUp(self):
        _reset_gpu_cache_for_tests()

    def test_parses_utilization_and_vram(self):
        calls = []

        def runner(*args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args[0], 0, "87, 12425, 16303\n", "")

        first = read_gpu_resources(runner=runner, now_fn=lambda: 10.0)
        second = read_gpu_resources(runner=runner, now_fn=lambda: 11.0)
        self.assertEqual(first["gpu"], 87.0)
        self.assertEqual(first["vram_used_mb"], 12425)
        self.assertEqual(first["vram_total_mb"], 16303)
        self.assertEqual(first["vram_percent"], 76.2)
        self.assertEqual(second, first)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def test_failure_returns_bounded_unknown_values(self):
        def runner(*_args, **_kwargs):
            raise subprocess.TimeoutExpired("nvidia-smi", 2)

        self.assertEqual(
            read_gpu_resources(runner=runner, now_fn=lambda: 20.0),
            {
                "gpu": None,
                "vram_used_mb": None,
                "vram_total_mb": None,
                "vram_percent": None,
            },
        )


if __name__ == "__main__":
    unittest.main()

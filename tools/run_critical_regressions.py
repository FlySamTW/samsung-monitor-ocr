"""Critical presentation regression entry point."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    commands = [
        [sys.executable, str(ROOT / "tools" / "test_presentation_soak.py")],
        [sys.executable, str(ROOT / "tools" / "test_runtime_safety_guards.py")],
    ]
    for command in commands:
        print("[critical]", " ".join(command), flush=True)
        result = subprocess.run(command, cwd=ROOT)
        if result.returncode:
            return result.returncode
    print("[critical] live 3-transition check is browser-gated; inspect logs/ui_sync_v1944_live.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import argparse
import json
import os
import platform
import subprocess
import sys
from typing import Dict, List


TARGET_SCRIPT = "samsung_ocr_batch_processor.py"


def list_windows_ocr_processes() -> List[Dict[str, str]]:
    pattern = f"*{TARGET_SCRIPT}*"
    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        (
            "$ErrorActionPreference='Stop'; "
            "$names=@('python.exe','pythonw.exe','python'); "
            f"$pattern='{pattern}'; "
            "Get-CimInstance Win32_Process | "
            "Where-Object { $names -contains $_.Name.ToLowerInvariant() -and $_.CommandLine -like $pattern } | "
            "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
        ),
    ]
    completed = subprocess.run(command, text=True, encoding="utf-8", errors="replace", capture_output=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "無法列出 Windows 程序。")
    text = completed.stdout.strip()
    if not text:
        return []
    data = json.loads(text)
    if isinstance(data, dict):
        data = [data]
    return [
        {
            "pid": str(item.get("ProcessId") or ""),
            "command": str(item.get("CommandLine") or ""),
        }
        for item in data
        if item.get("ProcessId")
    ]


def stop_windows_process(pid: str) -> None:
    completed = subprocess.run(
        ["taskkill", "/PID", pid, "/T", "/F"],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if completed.returncode not in {0, 128}:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or f"taskkill PID {pid} 失敗。")


def main() -> int:
    parser = argparse.ArgumentParser(description="停止本專案既有 Samsung OCR 後端，避免連到舊程式。")
    parser.add_argument("--dry-run", action="store_true", help="只列出會停止的程序，不實際停止。")
    args = parser.parse_args()

    if platform.system().lower() != "windows":
        print("[OCR 後端] 非 Windows 環境，略過程序清理。")
        return 0

    current_pid = str(os.getpid())
    processes = [item for item in list_windows_ocr_processes() if item["pid"] != current_pid]
    if not processes:
        print("[OCR 後端] 沒有發現既有後端程序。")
        return 0

    for item in processes:
        print(f"[OCR 後端] {'將停止' if args.dry_run else '停止'} PID={item['pid']} {item['command']}")
        if not args.dry_run:
            stop_windows_process(item["pid"])

    print(f"[OCR 後端] 已處理 {len(processes)} 個既有後端程序。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

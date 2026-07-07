from __future__ import annotations

import argparse
from pathlib import Path

from huggingface_hub import hf_hub_download


def main() -> int:
    parser = argparse.ArgumentParser(description="Download selected Hugging Face files into a local directory.")
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--target-dir", required=True)
    parser.add_argument("--files", nargs="+", required=True)
    args = parser.parse_args()

    target_dir = Path(args.target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    for filename in args.files:
        print(f"download {args.repo_id} {filename}", flush=True)
        path = hf_hub_download(
            repo_id=args.repo_id,
            filename=filename,
            local_dir=str(target_dir),
            resume_download=True,
        )
        print(f"done {path}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

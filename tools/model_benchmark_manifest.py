"""Build a stable, blind model-comparison manifest from the portable demo set.

This module is deliberately read-only with respect to LM Studio.  It never loads,
unloads, or switches a model; ``preflight`` only reports the server and loaded IDs.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "samples" / "ocr_demo_50" / "labels.json"
DEFAULT_OUT = ROOT / "docs" / "model_benchmark_manifest.json"
CANDIDATES = [
    "qwen/qwen3-vl-8b",
    "qwen3.5-9b-vlm",
    "gemma-4-12b-it-qat",
    "qwen/qwen2.5-vl-7b",
    "minicpm-v-4.6",
]


def tags(row: dict) -> list[str]:
    category = row.get("category")
    model = row.get("model") or ""
    price = row.get("price") or ""
    out = ["single_unit" if category == "單機" else "distant_view"]
    if "FollowMe" in model:
        out.append("followme_pro" if "Pro" in model else "followme")
    if category == "遠景":
        out.append("three_or_more_monitors")
        out.append("hallucination_guard")
    if category == "單機" and model == "型號未辨識":
        out.append("promo_or_unreadable_label")
    if price in {"無價格", "？＄17990", "？＄14990"}:
        out.append("price_uncertain_or_missing")
    if price not in {"無價格", ""}:
        out.append("price_present")
    return out


def build(out: Path) -> None:
    data = json.loads(LABELS.read_text(encoding="utf-8-sig"))
    cases = []
    for row in data["labels"]:
        cases.append({
            "id": row["id"],
            "image": row["sample_photo"],
            "tags": tags(row),
            "expected": {
                "view_type": row.get("category"),
                "model": row.get("model"),
                "price": row.get("price"),
            },
        })
    manifest = {
        "schema": "samsung-model-benchmark/v1",
        "source": "samples/ocr_demo_50/labels.json",
        "blind_protocol": "Send image plus the production OCR prompt; do not expose expected fields.",
        "cases": cases,
        "candidates": CANDIDATES,
        "coverage": dict(Counter(t for c in cases for t in c["tags"])),
        "known_limitations": [
            "The portable 50-photo set has no explicit human tag separating a promotional board from a FollowMe product card.",
            "It has no dedicated non-Samsung/other-brand row; add an independently reviewed fixture before using that metric.",
            "Price clarity is represented conservatively by present/uncertain-or-missing labels; visual blur is not inferred from filenames.",
        ],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(cases)} cases)")
    print(json.dumps(manifest["coverage"], ensure_ascii=False, sort_keys=True))


def preflight() -> None:
    """Read-only status check; intentionally does not use local_llm_manager.ensure."""
    api = os.environ.get("LOCAL_LLM_API_BASE", "http://127.0.0.1:1234/v1")
    try:
        import urllib.request
        with urllib.request.urlopen(api.rstrip("/") + "/models", timeout=5) as response:
            models = json.loads(response.read().decode("utf-8"))
        ids = [x.get("id") for x in models.get("data", [])]
        print(json.dumps({"api": api, "api_visible_models": ids, "loaded_model_unknown": True}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"api": api, "error": str(exc)}, ensure_ascii=False))
    lms = Path(os.environ.get("USERPROFILE", "")) / ".lmstudio" / "bin" / "lms.exe"
    if lms.exists():
        result = subprocess.run([str(lms), "ps"], capture_output=True, text=True, timeout=20)
        print(result.stdout.rstrip())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["build", "preflight"])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    build(args.out) if args.command == "build" else preflight()


if __name__ == "__main__":
    main()

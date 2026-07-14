"""Offline scorer for model benchmark predictions.

Input is JSON or JSONL with one object per case: id, view_type, model, price,
latency_ms, and optional error/failure_reason.  No model or server is contacted.
"""
from __future__ import annotations
import argparse, json
from collections import Counter, defaultdict
from pathlib import Path

def load(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text: return []
    data = json.loads(text) if text.startswith("[") else [json.loads(x) for x in text.splitlines() if x.strip()]
    return data

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("manifest", type=Path)
    p.add_argument("predictions", type=Path)
    p.add_argument("--model", default="unknown")
    p.add_argument("--out", type=Path)
    a = p.parse_args()
    manifest = json.loads(a.manifest.read_text(encoding="utf-8-sig"))
    pred = {str(x.get("id")): x for x in load(a.predictions)}
    rows, failures = [], Counter()
    for case in manifest["cases"]:
        got = pred.get(case["id"], {})
        exp = case["expected"]
        fields = {k: got.get(k) == exp.get(k) for k in ("view_type", "model", "price")}
        dangerous = ("distant_view" in case["tags"] and got.get("view_type") in {"單機", "FollowMe"}) or ("followme" in case["tags"] or "followme_pro" in case["tags"]) and got.get("view_type") == "遠景"
        failure = got.get("failure_reason") or got.get("error")
        if failure: failures[str(failure)] += 1
        rows.append({"id": case["id"], "tags": case["tags"], "fields": fields, "exact": all(fields.values()), "dangerous": dangerous, "latency_ms": got.get("latency_ms"), "failure": failure})
    n = len(rows) or 1
    valid_latency = [r["latency_ms"] for r in rows if isinstance(r["latency_ms"], (int, float))]
    result = {"schema":"samsung-model-benchmark-score/v1", "model":a.model, "n":len(rows), "fully_correct_rate":sum(r["exact"] for r in rows)/n, "distant_or_followme_danger_rate":sum(r["dangerous"] for r in rows)/n, "field_accuracy":{k:sum(r["fields"][k] for r in rows)/n for k in ("view_type","model","price")}, "mean_latency_ms":sum(valid_latency)/len(valid_latency) if valid_latency else None, "latency_observations":len(valid_latency), "failure_reasons":failures, "rows":rows}
    text=json.dumps(result,ensure_ascii=False,indent=2)+"\n"
    if a.out: a.out.write_text(text,encoding="utf-8")
    print(text,end="")
if __name__ == "__main__": main()

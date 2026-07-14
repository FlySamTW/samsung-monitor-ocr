#!/usr/bin/env python3
"""Prepare/evaluate prompt candidates; optional DSPy compile adapter."""
from __future__ import annotations
import argparse, json, urllib.request
from pathlib import Path

RISK = ("distant_view", "followme", "model_hallucination")
def validate_endpoint(endpoint: str) -> str:
    if not endpoint or not endpoint.startswith(("http://", "https://")):
        raise ValueError("endpoint must be an HTTP(S) OpenAI-compatible URL")
    return endpoint.rstrip("/")
def dspy_optimizer(name: str):
    """Return the installed DSPy optimizer class; construction stays caller-owned."""
    if name == "none": return None
    try: import dspy
    except ImportError as exc: raise RuntimeError("需要 DSPy 才能使用 GEPA/MIPROv2：pip install dspy") from exc
    candidates = ("GEPA",) if name == "gepa" else ("MIPROv2", "MIPRO")
    for candidate in candidates:
        cls = getattr(dspy, candidate, None)
        if cls is not None: return cls
    raise RuntimeError(f"目前安裝的 DSPy 沒有 {name} optimizer；請升級：pip install -U dspy")

def compile_with_dspy(program, trainset, metric, optimizer="gepa", **kwargs):
    """Actually invoke DSPy compile. Caller owns the program and trainset.

    This is deliberately an adapter: this module never invents a DSPy signature or
    evaluates holdout rows. The returned program is a candidate only.
    """
    cls=dspy_optimizer(optimizer)
    try: compiler=cls(metric=metric, **kwargs)
    except TypeError: compiler=cls(metric=metric)
    return compiler.compile(program, trainset=trainset)

def score(rows):
    total=max(1,len(rows)); exact=sum(str(r.get("prediction",r.get("input",""))).strip()==str(r.get("target","")).strip() for r in rows)/total
    return {"exact_match": exact, **{k: sum(bool(r.get(k, False)) for r in rows)/total for k in RISK}}
def promotion_allowed(baseline, candidate):
    return candidate["exact_match"] > baseline["exact_match"] and all(candidate[k] <= baseline[k] for k in RISK)
def lm_studio_ping(endpoint: str, model: str) -> dict:
    endpoint=validate_endpoint(endpoint)
    body=json.dumps({"model":model,"messages":[{"role":"user","content":"Reply OK"}],"temperature":0}).encode()
    req=urllib.request.Request(endpoint.rstrip("/")+"/chat/completions", body, {"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=30) as response: return json.loads(response.read())
def main() -> int:
    ap=argparse.ArgumentParser(description="Prepare/evaluate a candidate; DSPy compile is exposed as compile_with_dspy()."); ap.add_argument("--data", required=True); ap.add_argument("--baseline-metrics", required=True); ap.add_argument("--candidate-metrics", required=True); ap.add_argument("--prompt", required=True); ap.add_argument("--endpoint"); ap.add_argument("--model", default="local-model"); ap.add_argument("--optimizer", choices=["none","gepa","mipro"], default="none"); ap.add_argument("--run-dir", default="runs/manual_prompt_optimization")
    a=ap.parse_args(); out=Path(a.run_dir); out.mkdir(parents=True, exist_ok=True)
    if a.optimizer != "none":
        raise SystemExit("CLI only prepares/evaluates. Call compile_with_dspy() from Python to run DSPy compile.")
    if a.endpoint: print(json.dumps(lm_studio_ping(a.endpoint,a.model), ensure_ascii=False)[:500])
    base=json.loads(Path(a.baseline_metrics).read_text(encoding="utf-8")); cand=json.loads(Path(a.candidate_metrics).read_text(encoding="utf-8"))
    approved=promotion_allowed(base["holdout"], cand["holdout"])
    (out/"candidate_prompt.txt").write_text(Path(a.prompt).read_text(encoding="utf-8"), encoding="utf-8")
    (out/"promotion_decision.json").write_text(json.dumps({"approved":approved,"holdout_used_for_optimization":False,"baseline":base,"candidate":cand},ensure_ascii=False,indent=2),encoding="utf-8")
    print("PROMOTED" if approved else "REJECTED"); return 0 if approved else 2
if __name__ == "__main__": raise SystemExit(main())

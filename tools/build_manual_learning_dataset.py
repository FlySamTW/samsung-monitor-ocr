#!/usr/bin/env python3
"""Build leakage-safe JSONL from the audited CSV schema."""
from __future__ import annotations
import argparse, csv, hashlib, json, re
from pathlib import Path

DEFAULT_AUDIT = Path(r"D:\00_商化\00_已OCR照片\_ocr_audit")
def read_csv(path):
    if not path.exists(): raise FileNotFoundError(f"missing CSV: {path}")
    with path.open(encoding="utf-8-sig", newline="") as f: return list(csv.DictReader(f))
def _s(row, key): return str(row.get(key, "") or "").strip()
def canonical_path(raw, image_root):
    p=Path(raw)
    if not p.is_absolute(): p=image_root/p
    try: p=p.resolve()
    except OSError: return str(p), False
    return str(p), p.is_file() and p.suffix.lower() in {".jpg",".jpeg",".png",".webp"}
def target_from(row):
    # Preserve human intent as structured data; empty fields remain explicit nulls.
    return {"view_type": _s(row,"corrected_view_type") or None, "model": _s(row,"corrected_model") or None,
            "price": _s(row,"corrected_price") or None, "price_symbol": _s(row,"corrected_price_symbol") or None,
            "note": _s(row,"note") or None}
def filename_group(source):
    stem=Path(source).stem
    parts=stem.split("-")
    if len(parts)>=6 and parts[0]=="M":
        # M-city-district-TK3C-store-serial: remove only the terminal serial.
        serial=re.match(r"^(.*?)(\d+)$", parts[-1])
        prefix="-".join(parts[:-1])
        return prefix.lower() + ("-" + serial.group(1).lower() if serial and serial.group(1) else "")
    return re.sub(r"(?:[_-]\d+)+$", "", stem).lower()
def build_rows(corrections, rules, image_root, require_exists=True):
    hints=[_s(r,"rule_hint") for r in rules if _s(r,"rule_hint")]
    rows=[]; seen=set()
    for raw in corrections:
        original=_s(raw,"source_path")
        if not original: continue
        source, exists=canonical_path(original, image_root)
        if require_exists and not exists: continue
        target=target_from(raw)
        if not any(target.values()): continue
        ident=hashlib.sha256((source+"\0"+json.dumps(target,sort_keys=True,ensure_ascii=False)).encode()).hexdigest()[:20]
        if ident in seen: continue
        seen.add(ident)
        rows.append({"id":ident,"source_path":source,"input":_s(raw,"note"),"target":target,
                     "store_group":filename_group(source),"rule_context":hints})
    return rows
def split_rows(rows, ratios=(.8,.1,.1)):
    groups={}
    for row in rows: groups.setdefault(row["store_group"],[]).append(row)
    ordered=sorted(groups.items(),key=lambda x:hashlib.sha256(x[0].encode()).hexdigest())
    total=len(rows); a,b=total*ratios[0],total*sum(ratios[:2]); out=[]; n=0
    for _, members in ordered:
        split="train" if n<a else "dev" if n<b else "holdout"
        out.extend({**r,"split":split} for r in members); n+=len(members)
    return out
def write_jsonl(path, rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",encoding="utf-8",newline="\n") as f:
        for row in rows: f.write(json.dumps(row,ensure_ascii=False)+"\n")
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--audit-dir",default=str(DEFAULT_AUDIT)); ap.add_argument("--output",required=True); ap.add_argument("--allow-missing",action="store_true"); a=ap.parse_args()
    audit=Path(a.audit_dir); rows=build_rows(read_csv(audit/"manual_corrections.csv"),read_csv(audit/"manual_learning_rules.csv"),Path(r"D:\00_商化\00_未整理商化照片"),not a.allow_missing); write_jsonl(Path(a.output),split_rows(rows)); print(f"rows={len(rows)} output={a.output}")
if __name__=="__main__": raise SystemExit(main())

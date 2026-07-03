import argparse
import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set
from urllib import request
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from skills.audit_fields import enrich_result_for_review
from skills.evaluation import Evaluator
from skills.field_extraction import FieldNormalizer
from skills.followme_reference import build_followme_prompt_section
from skills.image_processing import ImageProcessor


PROMPT = """你是三星商化照片 OCR 助理。每張照片都是全新任務，只看目前圖片。
任務：判斷 view_type，讀取主角三星螢幕的型號與店內價格。
分類規則：
1. 遠景：完全看不到清楚型號或價格，且畫面主要是多台螢幕陳列。遠景不填 model/price。
2. FollowMe：主角螢幕有白色/銀色直立支架、圓形底座或托盤，才可判定。只用下方參考表輔助，不可把 LG、ASUS、MSI 或其他品牌當三星。
3. 單機：一般三星螢幕或可看到主角價牌。若讀不到型號或價格，仍輸出單機並留空。
價格規則：只讀實體商品價牌；活動告示、電信方案、分期月付、配件或 3000 元以下價格不可當螢幕價格。
輸出規則：先用 1 句繁體中文描述你看到的重點，下一行只輸出 JSON。
JSON 格式固定為：{"view_type":"遠景或單機","category":"遠景或單機或FollowMe","model":null,"price":null,"screen_status":"","quality_issue":"","black_screen":false,"thinking":""}
model 可讀才填字串，price 可讀才填整數；不確定就用 null，不要猜。
"""


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def filename_from_labelstudio_task(task: Dict[str, Any]) -> Optional[str]:
    image = ((task.get("data") or {}).get("image") or "").replace("\\", "/")
    if not image:
        return None
    return image.rsplit("/", 1)[-1]


def existing_processed(image_dir: Path) -> Set[str]:
    processed: Set[str] = set()
    success: Set[str] = set()
    for path in image_dir.glob("*OCR成功.json"):
        data = read_json(path)
        if not isinstance(data, list):
            continue
        for item in data:
            if isinstance(item, dict):
                name = filename_from_labelstudio_task(item)
                if name:
                    success.add(name)
                    processed.add(name)

    for path in image_dir.glob("*OCR失敗.json"):
        data = read_json(path)
        if not isinstance(data, list):
            continue
        for item in data:
            if not isinstance(item, dict):
                continue
            name = item.get("filename") or item.get("file_name")
            if name and name not in success:
                processed.add(str(name))
    return processed


def iter_images(image_dir: Path) -> Iterable[Path]:
    files = [
        p
        for p in image_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png"}
    ]
    return sorted(files, key=lambda p: (p.stat().st_mtime, p.name))


def post_json(url: str, payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    candidates = re.findall(r"\{[\s\S]*?\}", text or "")
    for candidate in reversed(candidates):
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def call_model(api_base: str, model: str, prompt: str, image_b64: str, fname: str, timeout: int, max_tokens: int) -> Dict[str, Any]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"圖片：{fname}\n請輸出一句描述，下一行輸出 JSON。"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                ],
            },
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
    }
    url = api_base.rstrip("/") + "/chat/completions"
    response = post_json(url, payload, timeout)
    text = (((response.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    parsed = extract_json(text) or {}
    if "thinking" not in parsed or not parsed.get("thinking"):
        parsed["thinking"] = text.split("{", 1)[0].strip()
    parsed["raw_response"] = text
    return parsed


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def clear_stuck_model(model: str) -> None:
    try:
        subprocess.run(["lms", "unload", model], cwd=str(ROOT), timeout=120, capture_output=True, text=True)
        env = os.environ.copy()
        env.setdefault("LOCAL_LLM_MODEL", model)
        env.setdefault("LOCAL_LLM_MODEL_KEY", "qwen/qwen3-vl-8b")
        env.setdefault("LOCAL_LLM_CONTEXT_LENGTH", "16384")
        env.setdefault("LOCAL_LLM_GPU", "max")
        env.setdefault("LOCAL_LLM_PARALLEL", "1")
        subprocess.run(["python", "tools\\local_llm_manager.py", "ensure"], cwd=str(ROOT), timeout=240, env=env)
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--api-base", default=os.environ.get("LOCAL_LLM_API_BASE", "http://127.0.0.1:1234/v1"))
    parser.add_argument("--model", default=os.environ.get("LOCAL_LLM_MODEL", "qwen/qwen3-vl-8b"))
    parser.add_argument("--max-size", type=int, default=int(os.environ.get("OCR_FAST_MAX_SIZE", "640")))
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--max-tokens", type=int, default=280)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--session-id", default=datetime.now().strftime("%Y%m%d-%H%M-direct"))
    parser.add_argument("--flush-every", type=int, default=10)
    args = parser.parse_args()

    image_dir = Path(args.dir)
    output_jsonl = image_dir / f"{args.session_id}-OCR直接批次.jsonl"
    output_success = image_dir / f"{args.session_id}-OCR成功.json"
    output_failed = image_dir / f"{args.session_id}-OCR失敗.json"

    processed = existing_processed(image_dir)
    processed.update(row.get("file_name", "") for row in load_jsonl(output_jsonl))

    processor = ImageProcessor()
    processor.config["max_size"] = args.max_size
    processor.config["detect_label_card"] = False
    processor.config["bottom_label_strip"] = False
    processor.config["bottom_center_zoom"] = False
    normalizer = FieldNormalizer()
    evaluator = Evaluator()
    prompt = PROMPT + build_followme_prompt_section()

    all_results = load_jsonl(output_jsonl)
    failures = read_json(output_failed)
    if not isinstance(failures, list):
        failures = []

    candidates = [p for p in iter_images(image_dir) if p.name not in processed]
    if args.limit:
        candidates = candidates[: args.limit]

    print(f"[direct] dir={image_dir}")
    print(f"[direct] already_processed={len(processed)} pending_this_run={len(candidates)} output={output_jsonl.name}", flush=True)

    for index, image_path in enumerate(candidates, start=1):
        start = time.time()
        print(f"[direct] {index}/{len(candidates)} {image_path.name}", flush=True)
        try:
            processed_image = processor.process(str(image_path))
            if not processed_image:
                raise RuntimeError("image preprocessing failed")
            raw = call_model(
                args.api_base,
                args.model,
                prompt,
                processed_image["base64"],
                image_path.name,
                args.timeout,
                args.max_tokens,
            )
            normalized = normalizer.normalize(raw)
            price_digits = re.sub(r"[^\d]", "", str(normalized.get("price") or ""))
            if price_digits and int(price_digits) < 3000:
                normalized["price"] = None
                normalized["quality_issue"] = (
                    (str(normalized.get("quality_issue") or "") + "；" if normalized.get("quality_issue") else "")
                    + "價格低於3000，疑似方案/月付/配件價"
                )
            normalized.setdefault("view_type", normalized.get("category") or "單機")
            normalized.setdefault("category", normalized.get("view_type") or "單機")
            normalized["file_name"] = image_path.name
            normalized["timestamp"] = datetime.now().isoformat()
            normalized["duration"] = round(time.time() - start, 2)
            normalized["run_id"] = args.session_id
            normalized = enrich_result_for_review(normalized)
            append_jsonl(output_jsonl, normalized)
            all_results.append(normalized)
        except (TimeoutError, URLError) as exc:
            elapsed = round(time.time() - start, 2)
            failure = {
                "filename": image_path.name,
                "reason": f"LLM timeout/error: {type(exc).__name__}",
                "timestamp": datetime.now().isoformat(),
                "duration": elapsed,
            }
            failures.append(failure)
            output_failed.write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
            clear_stuck_model(args.model)
        except Exception as exc:
            elapsed = round(time.time() - start, 2)
            failure = {
                "filename": image_path.name,
                "reason": f"{type(exc).__name__}: {exc}",
                "timestamp": datetime.now().isoformat(),
                "duration": elapsed,
            }
            failures.append(failure)
            output_failed.write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")

        if index % max(1, args.flush_every) == 0:
            evaluator.export_to_label_studio_json(all_results, str(output_success))
            print(f"[direct] flushed results={len(all_results)} failures={len(failures)}", flush=True)

    evaluator.export_to_label_studio_json(all_results, str(output_success))
    output_failed.write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[direct] done results={len(all_results)} failures={len(failures)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

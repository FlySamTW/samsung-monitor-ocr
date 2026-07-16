#!/usr/bin/env python3
"""Regression test for per-photo immediate accuracy retries."""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from skills.audit_fields import immediate_retry_decision
from skills.batch_orchestrator import BatchOrchestrator


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        image_dir = root / "202605"
        image_dir.mkdir()
        for name in ("A.jpg", "B.jpg"):
            Image.new("RGB", (640, 480), "white").save(image_dir / name, quality=95)
        model_list = root / "models.txt"
        model_list.write_text("S24F332EAC\n", encoding="utf-8")
        assets = root / "assets"
        assets.mkdir()

        calls: list[tuple[str, int, int]] = []

        def processor(fname, image_b64, prompt_mgr, image_processor, processed_image=None, ocr_attempt=1, previous_results=None):
            calls.append((fname, ocr_attempt, len(previous_results or [])))
            binding = {
                "request_id_verified": True,
                "input_image_sha256": __import__("hashlib").sha256(__import__("base64").b64decode(image_b64)).hexdigest(),
            }
            if fname == "A.jpg" and ocr_attempt == 1:
                return {
                    "view_type": "單機", "category": "單機", "model": None, "price": None,
                    "quality_issue": "沒有規格和價格牌", "thinking": "唯一主角但標籤仍需仔細重讀。",
                    "complete_screen_count": 1, "unique_main": True,
                    "label_ownership": "matched", "followme_physical_evidence": [],
                    **binding,
                }
            return {
                "view_type": "單機", "category": "單機", "model": "S24F332EAC", "price": "2390",
                "quality_issue": "", "thinking": "唯一主角自己的規格牌與價格牌清楚可讀。",
                "complete_screen_count": 1, "unique_main": True,
                "label_ownership": "matched", "followme_physical_evidence": [],
                **binding,
            }

        orchestrator = BatchOrchestrator({
            "image_dir": str(image_dir),
            "output_dir": str(root / "out"),
            "assets_dir": str(assets),
            "model_list_file": str(model_list),
            "max_dimensions": (2560, 1440),
            "max_auto_attempts": 3,
        })
        orchestrator.set_processor_function(processor)
        orchestrator.set_result_review_function(immediate_retry_decision)
        old_session = image_dir / "20200101-0000-OCR成功.json"
        old_session.write_text(json.dumps([{"file_name": "A.jpg", "model": "OLD"}]), encoding="utf-8")
        assert orchestrator.force_rerun("A.jpg")
        assert json.loads(old_session.read_text(encoding="utf-8")) == []
        (image_dir / ".ocr_retry_queue.json").write_text(json.dumps({
            "image_dir": str(image_dir.resolve()),
            "priority_queue": ["A.jpg"],
            "retry_queue": ["A.jpg"],
            "auto_attempts": {"A.jpg": 2},
            "auto_result_history": {"A.jpg": [{"model": "STALE"}]},
        }), encoding="utf-8")
        assert orchestrator.start_batch(restart=True)

        deadline = time.time() + 20
        while orchestrator.is_running and time.time() < deadline:
            time.sleep(0.05)
        assert not orchestrator.is_running
        assert [item[0] for item in calls[:5]] == ["A.jpg", "A.jpg", "B.jpg", "B.jpg", "B.jpg"], calls
        assert calls[1][1:] == (2, 1), calls
        assert calls[3][1:] == (2, 1), calls
        assert len(orchestrator.recent_results) == 2
        assert calls[4][1:] == (3, 2), calls
        assert len(orchestrator.display_queue) == 5
        a_events = [item for item in orchestrator.display_queue if item.get("file_name") == "A.jpg"]
        assert [item.get("pass_index") for item in a_events] == [1, 2]
        assert len({item.get("source_item_id") for item in a_events}) == 1
        assert len({item.get("presentation_id") for item in a_events}) == 2
        b_events = [item for item in orchestrator.display_queue if item.get("file_name") == "B.jpg"]
        assert [item.get("pass_index") for item in b_events] == [1, 2, 3]
        assert "跨照片" in "".join(b_events[0].get("retry_reason") or [])
        assert all(row.get("model") == "S24F332EAC" for row in orchestrator.recent_results)
        attempts = {row.get("file_name"): row.get("ocr_attempt") for row in orchestrator.recent_results}
        assert attempts == {"A.jpg": 2, "B.jpg": 3}, attempts
        b_result = next(row for row in orchestrator.recent_results if row.get("file_name") == "B.jpg")
        assert b_result.get("auto_review_required") is True
        assert b_result.get("auto_verified") is False

        success_files = sorted(image_dir.glob("*-OCR成功.json"))
        nonempty_success = [
            path for path in success_files if json.loads(path.read_text(encoding="utf-8"))
        ]
        assert len(nonempty_success) == 1
        saved = json.loads(nonempty_success[0].read_text(encoding="utf-8"))
        assert len(saved) == 2
        assert not any(row.get("quality_issue") == "沒有規格和價格牌" for row in saved)
        print("immediate retry queue: ok")


if __name__ == "__main__":
    main()

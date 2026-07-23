#!/usr/bin/env python3
"""Small regression checks for the OCR safety guards introduced in v19.37."""
from pathlib import Path
from threading import Event, RLock
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import samsung_ocr_batch_processor as ocr
from skills.batch_orchestrator import BatchOrchestrator
from skills.audit_fields import immediate_retry_decision
from skills.model_validation import (
    is_placeholder_model,
    strict_known_model,
    unique_embedded_known_model,
)


class _AliveWorker:
    def is_alive(self) -> bool:
        return True


def assert_batch_directory_isolation(tmp_root: Path) -> None:
    first = tmp_root / "first"
    second = tmp_root / "second"
    first.mkdir(parents=True, exist_ok=True)
    second.mkdir(parents=True, exist_ok=True)

    orchestrator = BatchOrchestrator.__new__(BatchOrchestrator)
    orchestrator.image_dir = str(first)
    orchestrator.active_image_dir = str(first)
    orchestrator.config = {"image_dir": str(first)}
    orchestrator.priority_queue = ["old.jpg"]
    orchestrator.retry_queue = ["retry.jpg"]
    orchestrator.session_processed = {"old.jpg"}
    orchestrator.is_running = True
    orchestrator.stats = {"is_running": True}
    orchestrator.stop_event = Event()
    orchestrator._state_lock = RLock()
    orchestrator._worker_thread = _AliveWorker()
    orchestrator.system_logs = []
    orchestrator.stream_buffer = ""
    orchestrator.stream_file = None

    switched, _ = orchestrator.set_work_dir(str(second))
    assert switched is False
    assert Path(orchestrator.image_dir) == first

    orchestrator.stop_batch()
    assert orchestrator.stop_event.is_set()
    assert orchestrator.is_running is True
    assert orchestrator.stats["is_running"] is True

    orchestrator._worker_thread = None
    orchestrator.is_running = False
    orchestrator.stats["is_running"] = False
    switched, _ = orchestrator.set_work_dir(str(second))
    assert switched is True
    assert Path(orchestrator.image_dir) == second
    assert orchestrator.priority_queue == []
    assert orchestrator.retry_queue == []
    assert orchestrator.session_processed == set()


def assert_request_binding_fault_scope() -> None:
    orchestrator = BatchOrchestrator.__new__(BatchOrchestrator)
    orchestrator.runtime_health_incident_sources = {}
    orchestrator.request_binding_incident_events = []
    orchestrator._persist_retry_state = lambda: None

    reasons = ["request_id_mismatch"]
    assert orchestrator._request_binding_incident_repeated_across_sources(
        reasons, {"file_name": "first.jpg"}
    ) is False
    assert orchestrator._request_binding_incident_repeated_across_sources(
        reasons, {"file_name": "first.jpg"}
    ) is False
    assert orchestrator._request_binding_incident_repeated_across_sources(
        reasons, {"file_name": "second.jpg"}
    ) is False
    assert orchestrator._request_binding_incident_repeated_across_sources(
        reasons, {"file_name": "third.jpg"}
    ) is True

    for reason in ("request_id_missing", "request_binding_unverified"):
        assert orchestrator._request_binding_incident_repeated_across_sources(
            [reason], {"file_name": "third.jpg"}
        ) is False
        assert orchestrator._request_binding_incident_repeated_across_sources(
            [reason], {"file_name": "fourth.jpg"}
        ) is False


def main() -> None:
    try:
        ocr.build_runtime_system_prompt("規則" * 15000, "\nFollowMe 參考資料")
    except RuntimeError as exc:
        assert "不可自動切換短提示詞" in str(exc)
    else:
        raise AssertionError("oversized production prompt must fail closed")

    iterated_prompt = "規則" * 9500
    retained_prompt, retained_compacted = ocr.build_runtime_system_prompt(iterated_prompt, "")
    assert retained_compacted is False
    assert retained_prompt == iterated_prompt + "\n\n" + ocr.V1945_OUTPUT_CONTRACT
    assert "narration" in ocr.V1945_OUTPUT_CONTRACT
    assert 'parsed.get("narration")' in (
        PROJECT_ROOT / "samsung_ocr_batch_processor.py"
    ).read_text(encoding="utf-8")
    normalized_self_talk = ocr.build_final_display_thinking(
        {"view_type": "單機", "model": "S24F332EAC", "price": "2390"},
        "我看到中央一台完整螢幕，所以……這不是 FollowMe，是一般單機。",
    )
    assert normalized_self_talk.startswith("我看到")
    assert normalized_self_talk.endswith("所以……")
    assert "這不是 FollowMe，是一般單機" in normalized_self_talk
    assert normalized_self_talk.count("所以……") == 1

    assert not ocr.has_explicit_distant_layout_evidence(
        "這是 3C 賣場，有展示區與多台螢幕，但中間一台是主角。"
    )
    assert ocr.has_explicit_distant_layout_evidence(
        "整體符合「遠景」條件：多台螢幕排成整排展示牆，沒有單一主角或可歸屬的價牌。"
    )
    assert ocr.has_explicit_distant_layout_evidence(
        "前景有堆疊紙箱，背景螢幕超過3台，無法指定唯一主角。整體符合「遠景」條件"
    )
    assert not ocr.has_strong_single_unit_evidence(
        "前景有堆疊紙箱，沒有明確的主角螢幕。整體符合「遠景」條件"
    )

    display_wall_with_prices = (
        "這張照片拍的是 TK3C 門市的螢幕展示區，整排螢幕都擺在木紋展示架上，"
        "下方有實體價牌。畫面中可見多台不同品牌螢幕，包括 ASUS、Acer、NEC、CHIMEI，"
        "價牌上有 6,990、3,990、2,590 等不同價格。整體符合「遠景」條件。"
    )
    assert not ocr.has_strong_single_unit_evidence(display_wall_with_prices)
    assert ocr.has_explicit_distant_layout_evidence(display_wall_with_prices)
    assert ocr.should_block_rescue_from_distant_view("遠景", display_wall_with_prices)
    assert not ocr.should_demote_distant_to_single_review("遠景", display_wall_with_prices)
    assert ocr.has_strong_single_unit_evidence(
        "主角螢幕右側的側標可對應同一台商品，型號與主角自己的實體價牌都清楚。"
    )

    followme_text = "Samsung FollowMe 標牌固定在立式展示螢幕上，旁邊可見白色立柱與圓形底座。"
    assert not ocr.has_confirmed_followme_evidence('FollowMe M7 32"', followme_text)
    assert not ocr.has_explicit_distant_layout_evidence(followme_text)

    negative_followme_samples = (
        "沒有任何一台螢幕附有「FollowMe」或 FM 型號代碼，也沒有移動式支架。",
        "未見任何 FollowMe 標誌、移動式支架或專屬型號標籤。",
        "主角為 MSI 螢幕，無 FollowMe 相關線索。",
        "多台螢幕並排，沒有任何一台螢幕有清晰的 FollowMe 字樣。",
    )
    for sample in negative_followme_samples:
        assert ocr.has_negative_followme_context(sample)
        assert not ocr.has_positive_followme_word(sample)
        assert ocr.infer_followme_from_physical_clues(None, sample) is None

    positive_label = "主角立式展示螢幕自己的標牌清楚寫著 Samsung FollowMe 4K。"
    assert ocr.has_positive_followme_word(positive_label)
    assert not ocr.has_followme_display_fixture_clue(positive_label)

    confirmed_fixture = (
        "主角實機是 Samsung FollowMe，同一台螢幕連著白色垂直支架與圓形落地底座，"
        "自己的托盤位在螢幕正下方。"
    )
    assert ocr.has_followme_display_fixture_clue(confirmed_fixture)
    assert ocr.has_confirmed_followme_evidence('FollowMe M7 32"', confirmed_fixture)

    generic_stand = "主角為 MSI 螢幕，前方有圓形底座與一般托盤，但不是三星產品。"
    assert not ocr.has_strong_followme_physical_signature(generic_stand)
    assert ocr.infer_followme_from_physical_clues(None, generic_stand) is None

    generic_followme = "主角立式螢幕的標牌寫 Samsung FollowMe 4K，沒有讀到尺寸型號。"
    assert ocr.normalize_followme_model("FollowMe Pro M7 43\"", None, generic_followme) is None
    assert ocr.normalize_followme_model("FollowMe", 17990, generic_followme) is None
    explicit_pro = (
        "主角實機右側規格牌清楚寫著 Samsung FollowMe Pro 43吋、型號 S43FM703UC，"
        "同一台螢幕連著白色垂直支架、圓形落地底座與自己的托盤。"
    )
    assert ocr.normalize_followme_model("FollowMe", 17990, explicit_pro) == 'FollowMe Pro M7 43"'
    assert ocr.normalize_followme_model("S43FM703UC", 17990, explicit_pro) == 'FollowMe Pro M7 43"'

    assert is_placeholder_model("SXXTEST001")
    assert strict_known_model("S24F332EAC", ["S24F332EAC"]) == "S24F332EAC"
    assert strict_known_model("S24F532EAC", ["S24F332EAC"]) is None
    assert unique_embedded_known_model(
        "G8 S32DG802SC", ["S32DG802SC", "S24F332EAC"]
    ) == "S32DG802SC"
    assert unique_embedded_known_model(
        "G8 S32DG802SC S24F332EAC", ["S32DG802SC", "S24F332EAC"]
    ) is None
    assert unique_embedded_known_model(
        "G8 S32DG802XX", ["S32DG802SC"]
    ) is None

    distant_record = {
        "file_name": "M-202605-測試.jpg",
        "source_path": r"D:\source\202605\M-202605-測試.jpg",
        "view_type": "遠景",
        "category": "遠景",
        "model": None,
        "price": None,
        "quality_issue": "",
        "complete_screen_count": 4,
        "unique_main": False,
        "label_ownership": "not_visible",
        "followme_physical_evidence": [],
        "thinking": "整排展示牆有三台以上螢幕全部完整入鏡，沒有唯一主角，無法讀取唯一主角自己的規格與價格。",
    }
    first = immediate_retry_decision(distant_record, 1, [], 3)
    assert first["retry"] is True
    history = [dict(distant_record), dict(distant_record)]
    third = immediate_retry_decision(distant_record, 3, history, 3)
    assert third["unresolved"] is False
    assert third["verified"] is True

    partial_three = dict(distant_record)
    partial_three["complete_screen_count"] = 2
    partial_three["thinking"] = "畫面有三台螢幕，但只有中間一台完整入鏡，其餘被裁切。"
    rejected_distant = immediate_retry_decision(partial_three, 3, history, 3)
    assert rejected_distant["unresolved"] is True

    previous = [{"view_type": "遠景", "model": None, "price": None, "reasons": ["需複核"]}]
    pass2_messages = ocr.build_ocr_messages("system", [{"type": "text", "text": "photo"}], 2, previous)
    pass3_messages = ocr.build_ocr_messages("system", [{"type": "text", "text": "photo"}], 3, previous)
    pristine = [{"role": "system", "content": "system"}, {"role": "user", "content": [{"type": "text", "text": "photo"}]}]
    assert pass2_messages == pristine
    assert pass3_messages == pristine
    assert not any("遠景" in str(message.get("content")) for message in pass2_messages)

    source = (PROJECT_ROOT / "samsung_ocr_batch_processor.py").read_text(encoding="utf-8")
    assert 'messages.append({"role": "assistant", "content": full_response_text})' not in source
    assert "第一次暫定結果" not in source
    assert "歷史糾錯紀錄" not in source

    backend_source = (PROJECT_ROOT / "samsung_ocr_batch_processor.py").read_text(encoding="utf-8")
    orchestrator_source = (PROJECT_ROOT / "skills" / "batch_orchestrator.py").read_text(encoding="utf-8")
    assert "OCR_FAST_PROMPT" not in backend_source
    assert "OCR_FAST_BATCH" not in backend_source
    assert "OCR_FAST_BATCH" not in orchestrator_source

    ark_ad = "展示牆可見 Odyssey Ark、Acer、MSI 等品牌廣告，沒有明確主角。整體符合「遠景」條件"
    assert ocr.infer_odyssey_ark_model(ark_ad) is None
    ark_subject = "主角是 Odyssey Ark Mini LED 55吋大型直立曲面桌上機。"
    assert ocr.infer_odyssey_ark_model(ark_subject) == "S55BG970NC"

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        assert_batch_directory_isolation(Path(tmp))
    assert_request_binding_fault_scope()
    print("runtime safety guards: ok")


if __name__ == "__main__":
    main()

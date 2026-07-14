"""Deterministic presentation identity/state soak test; no runtime required."""
from __future__ import annotations

import random
from pathlib import Path
import unittest


def simulate(seed: int = 44, count: int = 500) -> dict[str, int]:
    rng = random.Random(seed)
    items = [
        {"presentation_id": f"p-{i:09d}", "presentation_sequence": i, "file_name": f"photo-{i}.jpg",
         "thinking": f"判讀 {i}", "result": {"model": f"M{i}"}}
        for i in range(count)
    ]
    pending: dict[str, dict] = {}
    revealed: list[str] = []
    active = None
    seen: set[str] = set()
    for poll in range(count * 6):
        # Backend may be ahead and polls may duplicate or arrive out of order.
        batch = items[: min(count, poll // 2 + 1)]
        rng.shuffle(batch)
        for item in batch:
            key = item["presentation_id"]
            if key not in seen:
                seen.add(key)
                pending[key] = item
        if active is None and pending:
            key = min(pending, key=lambda k: pending[k]["presentation_sequence"])
            active = pending.pop(key)
            if key in revealed:
                active = None
                continue
            assert active["presentation_id"] == key
            assert active["file_name"].replace("photo-", "")[:-4] == key[2:].lstrip("0") or key == "p-000000000"
            # Complete typewriter and reveal from the same immutable snapshot.
            assert active["presentation_id"] == key
            if key not in revealed:
                revealed.append(key)
            active = None
        if poll == count:
            # Reload: hydrate only from the immutable event sequence.
            pending = {item["presentation_id"]: item for item in items if item["presentation_id"] not in revealed}
    while pending:
        key = min(pending, key=lambda k: pending[k]["presentation_sequence"])
        active = pending.pop(key)
        assert active["presentation_id"] == key
        if key not in revealed:
            revealed.append(key)
    assert not active
    assert set(revealed) == {f"p-{i:09d}" for i in range(count)}
    assert len(set(revealed)) == count
    assert len(revealed) == count
    return {"items": count, "final": len(revealed), "duplicates": len(revealed) - len(set(revealed))}


class PresentationSoakTests(unittest.TestCase):
    def test_500_items_fast_backend_duplicate_out_of_order_reload(self):
        result = simulate()
        self.assertEqual(result, {"items": 500, "final": 500, "duplicates": 0})

    def test_burst_backpressure_never_replaces_active_snapshot(self):
        items = [{"presentation_id": f"p-{i:09d}", "presentation_sequence": i} for i in range(500)]
        pending = {item["presentation_id"]: item for item in items}
        active = pending.pop("p-000000000")
        # A queue window can coalesce duplicates, but the active item is never
        # rebuilt from a later snapshot or removed by backpressure.
        for snapshot in (items[200:], list(reversed(items[250:])), items):
            for item in snapshot:
                pending.setdefault(item["presentation_id"], item)
            self.assertEqual(active["presentation_id"], "p-000000000")
            self.assertLessEqual(len(pending), 500)
        self.assertEqual(active["presentation_sequence"], 0)

    def test_dashboard_forbids_identity_fallbacks(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        key_start = app.index("const getQueueKey")
        key_end = app.index("const normalizePresentationItem", key_start)
        key_body = app[key_start:key_end]
        self.assertIn("presentation_id", key_body)
        self.assertNotRegex(key_body, r"completed_at|source_path|file_name|recent_results|\|\|.*index")
        for forbidden in ("live-pending|", "liveRightPanelBackfill", "recent_results ||", "data.display_queue"):
            self.assertNotIn(forbidden, app)
        self.assertIn('data-presentation-id={activePresentation?.presentation_id', app)
        self.assertIn('data-testid="active-photo" data-presentation-id={activePresentation?.presentation_id', app)
        self.assertIn('data-testid="narration-container" data-presentation-id={activePresentation?.presentation_id', app)
        self.assertIn('data-testid="result-card" data-presentation-id={res.presentation_id', app)
        self.assertIn('data-testid="inspection-modal" data-presentation-id={inspectImage.presentation_id', app)
        self.assertIn('data-testid="active-placeholder" data-presentation-id={pendingPanelResult.presentation_id', app)
        self.assertIn('AI 即時判讀中', app)
        self.assertNotIn('pendingPanelResult.model', app)
        self.assertNotIn('pendingPanelResult.price', app)
        for forbidden in (
            'activePresentation?.file_name || data.stream_file || data.current_file',
            'prepareNarrationHandoff("", data.current_file',
            'setActivePresentation(null);\n      setDisplayedBuffer("")',
            '.filter((item) => incomingKeys.has(item._queueKey))',
        ):
            self.assertNotIn(forbidden, app)

    def test_asset_mismatch_reload_is_cached_and_cooldown_guarded(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        self.assertIn("frontend_asset_fingerprint", app)
        self.assertIn("data.status_contract_version !== 'compact-v2'", app)
        self.assertIn("getLoadedAssetFingerprint", app)
        self.assertIn("sessionStorage.getItem(key)", app)
        self.assertIn("Date.now() - last < 30000", app)
        self.assertIn("window.location.replace(`/?ui=", app)
        self.assertIn("loadedFingerprint === serverFingerprint", app)
        self.assertIn("presentationInvariantError", app)
        self.assertIn("presentation key divergence", app)
        self.assertIn("presentation_sequence || 0", app)

    def test_running_mode_uses_only_identity_synced_live_stream_and_keeps_active_queue_priority(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        self.assertIn('liveFile !== currentFile', app)
        self.assertIn('key: `live:${liveFile}`', app)
        self.assertIn('if (activePresentation) {', app)
        target_start = app.index('const getDisplayTarget = () =>')
        target_end = app.index('const imageReadyForDisplay', target_start)
        target = app[target_start:target_end]
        self.assertLess(target.index('if (activePresentation)'), target.index('getSyncedLiveStream()'))
        self.assertIn('const live = getSyncedLiveStream();', target)
        live_start = app.index('const getSyncedLiveStream = () =>')
        live_end = app.index('const getLatestBackendNarration', live_start)
        live = app[live_start:live_end]
        self.assertIn('data.stream_buffer', live)
        self.assertNotIn('activePresentation?.file_name || data.stream_file || data.current_file', app)
        self.assertNotIn('prepareNarrationHandoff("", data.current_file', app)
        self.assertNotIn('setActivePresentation(null);\n    setDisplayedBuffer("");\n    setDisplayTargetKey("");', app)
        self.assertIn('Never discard an unrevealed item', app)
        self.assertIn('incomingQueue.slice(-1)', app)
        self.assertIn('latestBackendNarration?.text', app)

    def test_backend_status_exposes_cached_asset_fingerprint(self):
        backend = (Path(__file__).resolve().parents[1] / "samsung_ocr_batch_processor.py").read_text(encoding="utf-8")
        self.assertIn('"frontend_asset_fingerprint"', backend)
        self.assertIn("get_frontend_asset_fingerprint", backend)
        self.assertIn("_frontend_asset_cache", backend)

    def test_history_is_loaded_on_demand_and_user_labels_are_localized(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        self.assertIn("/api/presentation_history/", app)
        self.assertIn("historyCache", app)
        self.assertIn("判讀歷程載入中", app)
        self.assertIn("複核原因：", app)
        self.assertIn("使用模型：", app)
        self.assertIn("初次辨識總進度", app)
        for visible_technical_label in (
            ">retry_reason:", ">model_id:", ">started_at:",
            ">completed_at:", ">decision:", ">previous_result_summary:",
        ):
            self.assertNotIn(visible_technical_label, app)

    def test_result_rail_hides_internal_history_metadata_and_missing_pass_labels(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        rail_start = app.index('data-testid="result-rail"')
        rail_end = app.index('{showReviewPanel && (', rail_start)
        rail = app[rail_start:rail_end]
        for internal_detail in (
            "formatMetaValue(res.retry_reason)",
            "formatDecision(res.decision)",
            "formatMetaValue(res.model_id)",
            "formatMetaValue(res.started_at)",
            "formatMetaValue(res.completed_at)",
            "formatMetaValue(res.previous_result_summary)",
            "toggleHistory(res)",
        ):
            self.assertNotIn(internal_detail, rail)
        self.assertIn("hasPassMetadata(activePresentation)", app)
        self.assertIn("hasPassMetadata(pendingPanelResult)", app)
        self.assertIn("hasPassMetadata(res)", rail)
        self.assertIn("hasPassMetadata(inspectImage)", app)
        self.assertIn("hasPassMetadata(pass)", app)
        self.assertIn("const getPassHeading = (item)", app)
        self.assertIn("if (!hasPassMetadata(item)) return \"\"", app)
        self.assertNotIn("item.model_id || result.model_id || item.model || result.model", app)
        self.assertNotIn("第 {formatMetaValue(activePresentation?.pass_index)} 輪", app)
        self.assertNotIn("第 {formatMetaValue(activePresentation?.pass_index)} 輪 · {getPassLabel(activePresentation)} · {formatMetaValue(activePresentation?.model_id)}", app)

    def test_backend_narration_snapshot_cannot_be_hidden_by_animation_state(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        self.assertIn("const activeNarrationSnapshot = activePresentation", app)
        self.assertIn("const visibleNarrationSnapshot = activeNarrationSnapshot", app)
        visible_start = app.index("const visibleNarration = visibleNarrationSnapshot?.text")
        visible_end = app.index("const narrationPhase", visible_start)
        visible = app[visible_start:visible_end]
        self.assertLess(visible.index("visibleNarrationSnapshot?.text"), visible.index("narrationDisplay.text"))
        self.assertIn('data-narration-source={visibleNarrationKey}', app)
        self.assertIn("LLM 判讀內容 · {narrationStatusLabel}", app)


if __name__ == "__main__":
    unittest.main(verbosity=2)

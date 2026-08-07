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
        self.assertIn('const visiblePresentationId = visibleNarrationKey.startsWith("live:")', app)
        self.assertIn('? visibleNarrationKey', app)
        self.assertIn('data-testid="active-photo" data-presentation-key={expectedVisualKey} data-presentation-id={visiblePresentationId}', app)
        self.assertIn('data-testid="narration-container"', app)
        self.assertIn('data-presentation-id={visiblePresentationId}', app)
        self.assertIn('data-testid="result-card" data-presentation-id={res.presentation_id', app)
        self.assertIn('data-testid="inspection-modal" data-presentation-id={inspectImage.presentation_id', app)
        self.assertIn('data-testid="active-placeholder" data-presentation-id={pendingPanelResult.presentation_id', app)
        self.assertIn('LLM 即時判讀中', app)
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

    def test_gpu_card_rejects_stale_static_snapshot_without_changing_six_card_layout(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        self.assertIn("gpuTelemetryAgeMs <= 10000", app)
        self.assertIn("const freshGpuTelemetry =", app)
        self.assertIn("data.resources?.gpu ?? freshGpuTelemetry.gpu", app)
        self.assertNotIn("data.resources?.gpu ?? gpuTelemetry.gpu", app)
        self.assertIn("gridTemplateColumns:'repeat(3, 1fr)'", app)
        for label in ("完成判讀", "自動定案中", "失敗", "GPU", "記憶體", "近期平均"):
            self.assertIn(f"l:'{label}'", app)

    def test_live_stream_does_not_leave_a_false_stalled_invariant(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        watchdog_start = app.index("const watchdog = setInterval")
        watchdog_end = app.index("return () => clearInterval(watchdog);", watchdog_start)
        watchdog = app[watchdog_start:watchdog_end]

        self.assertIn("const watched = displayWatchdogRef.current", watchdog)
        self.assertIn("active._queueKey !== watched.key", watchdog)
        self.assertIn('prev.startsWith("presentation stalled:") ? "" : prev', watchdog)
        self.assertIn("Date.now() - watched.updatedAt", watchdog)
        self.assertNotIn("const latestKeys =", watchdog)

    def test_running_mode_keeps_current_photo_during_empty_stream(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        self.assertIn('const rawSameFileStream = liveFile === currentFile', app)
        self.assertIn('const sameFileStream = humanizeStructuredModelOutput(rawSameFileStream, pendingNarration)', app)
        self.assertIn('const hasModelText = Boolean(rawSameFileStream || detailedThinking);', app)
        self.assertNotIn('if (!hasModelText && (activePresentation || pendingQueue.length > 0)) return null;', app)
        self.assertIn('the left panel belongs exclusively to the current', app)
        self.assertIn('key: `live:${liveDir}|${currentFile}|pass:${livePassIndex}`', app)
        self.assertIn("const visiblePassPresentation = liveStreamSnapshot ? livePendingResult : activePresentation", app)
        self.assertIn("getPassHeading(visiblePassPresentation)", app)
        self.assertIn('if (activePresentation) {', app)
        target_start = app.index('const getDisplayTarget = () =>')
        target_end = app.index('const imageReadyForDisplay', target_start)
        target = app[target_start:target_end]
        self.assertIn('const live = getSyncedLiveStream();', target)
        self.assertLess(target.index('const live = getSyncedLiveStream();'), target.index('if (activePresentation)'))
        live_start = app.index('const getSyncedLiveStream = () =>')
        live_end = app.index('const getLatestBackendNarration', live_start)
        live = app[live_start:live_end]
        self.assertIn('data.stream_buffer', live)
        self.assertNotIn('activePresentation?.file_name || data.stream_file || data.current_file', app)
        self.assertNotIn('prepareNarrationHandoff("", data.current_file', app)
        self.assertIn('const liveFileOwnsPreview = Boolean(currentFile && currentFile !== "None");', app)
        self.assertIn('setPendingQueue([]);', app)
        self.assertIn('setActivePresentation(null);', app)
        self.assertIn('Never discard an unrevealed item', app)
        self.assertIn('incomingQueue.slice(-1)', app)
        self.assertIn('const visibleNarrationSnapshot = liveStreamSnapshot', app)
        self.assertIn('|| (!isRunning ? latestBackendNarration : heldNarrationSnapshot)', app)
        self.assertIn('currentFileThinking', app)
        self.assertIn('!value.startsWith("這張已完成辨識：")', app)

    def test_live_display_target_snapshots_its_own_filename_not_stale_active_filename(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        target_start = app.index('const getDisplayTarget = () =>')
        target_end = app.index('const imageReadyForDisplay', target_start)
        target = app[target_start:target_end]

        self.assertIn(
            'if (live) return { target: live.text, isQueue: false, key: live.key, fileName: live.fileName || "" };',
            target,
        )
        self.assertIn('fileName: activePresentation.file_name || ""', target)
        self.assertIn('const displayTargetSnapshotRef = useRef({ key: "", fileName: "", target: "", isQueue: false });', app)
        self.assertIn('displayTargetSnapshotRef.current = nextTarget;', app)
        self.assertIn('prepareNarrationHandoff(nextTarget.key, nextTarget.fileName);', app)
        self.assertNotIn('prepareNarrationHandoff(nextTarget.key, activePresentation?.file_name', app)

    def test_held_snapshot_cannot_borrow_or_cross_connect_image_identity(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        held_start = app.index('const heldNarrationSnapshot =')
        held_end = app.index('// The API snapshot is the display authority.', held_start)
        held = app[held_start:held_end]
        match_start = app.index('const matchingVisibleImage =')
        match_end = app.index('const heldPresentation =', match_start)
        matching_image = app[match_start:match_end]

        self.assertIn('fileName: narrationDisplay.fileName || ""', held)
        self.assertIn('key: narrationDisplay.key || ""', held)
        self.assertNotIn('visibleImageTarget.fileName', held)
        self.assertNotIn('visibleImagePresentationKey', held)
        self.assertIn('(!visibleNarrationKey || effectiveVisibleImagePresentationKey === visibleNarrationKey)', matching_image)

    def test_empty_live_stream_uses_identity_bound_task_narration(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        helper_start = app.index("const buildLivePendingNarration =")
        helper_end = app.index("const getLoadedAssetFingerprint", helper_start)
        helper = app[helper_start:helper_end]
        live_start = app.index("const getSyncedLiveStream = () =>")
        live_end = app.index("const getLatestBackendNarration", live_start)
        live = app[live_start:live_end]

        self.assertIn("safeFileName", helper)
        self.assertIn("passIndex", helper)
        self.assertIn("reviewMode", helper)
        self.assertIn("FollowMe 實體線索", helper)
        self.assertIn("AI 正在整理這張照片的可見證據", helper)
        self.assertNotIn("未提供", helper)
        self.assertIn("sameFileStream\n      || detailedThinking\n      || pendingNarration", live)
        self.assertIn("fileName: currentFile", live)
        self.assertIn("passIndex: livePassIndex", live)
        self.assertIn("reviewMode: String(data.review_progress?.mode || \"\")", live)
        self.assertIn("key: `live:${liveDir}|${currentFile}|pass:${livePassIndex}`", live)
        self.assertIn('!value.includes("AI 本輪未回傳完整判讀文字")', live)
        self.assertIn('!text.includes("AI 本輪未回傳完整判讀文字")', live)
        self.assertLess(live.index("rawSameFileStream"), live.index("buildLivePendingNarration"))
        self.assertLess(live.index("buildLivePendingNarration"), live.index("humanizeStructuredModelOutput"))

    def test_same_photo_pass_handoff_keeps_preview_identity_synchronized(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        self.assertIn('const getLivePhotoIdentityKey = (key)', app)
        self.assertIn('.replace(/\\|pass:\\d+$/, "")', app)
        self.assertIn('data.review_progress?.current_pass, isRunning, revealedResults[0]?._queueKey]);', app)
        self.assertIn('getLivePhotoIdentityKey(prev.key) === getLivePhotoIdentityKey(live.key)', app)
        self.assertIn('return samePhoto ? { ...prev, key: live.key, fileName: live.fileName } : prev;', app)
        self.assertIn('const effectiveVisibleImagePresentationKey = isSameLivePhotoPassHandoff', app)
        self.assertIn('effectiveVisibleImagePresentationKey === expectedVisualKey', app)
        self.assertIn('data-presentation-key={effectiveVisibleImagePresentationKey}', app)

    def test_unresolved_completed_event_never_renders_fallback_as_final_result(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        unresolved_start = app.index("const isExplicitlyUnresolved =")
        unresolved_end = app.index("const hasPassMetadata", unresolved_start)
        unresolved = app[unresolved_start:unresolved_end]
        status_start = app.index("const getUnresolvedCardStatus =")
        status_end = app.index("const hasPassMetadata", status_start)
        unresolved_status = app[status_start:status_end]
        rail_start = app.index('data-testid="result-rail"')
        rail_end = app.index('{showReviewPanel && (', rail_start)
        rail = app[rail_start:rail_end]

        self.assertIn('"retry_scheduled", "review_required", "failed"', unresolved)
        self.assertIn('if (decision === "accepted") return false;', unresolved)
        self.assertIn('item.evidence_unresolved === true', unresolved)
        self.assertIn('item.auto_review_required === true', unresolved)
        self.assertIn('item.accepted === false', unresolved)
        self.assertIn('data-review-state={isStaleGuardRevision(res) ? "stale-revision" : isTerminalTechnicalFailure(res) ? "technical-failure" : isExplicitlyUnresolved(res) ? "auto-finalizing" : "completed"}', rail)
        self.assertIn('label: "技術錯誤／該張未上傳"', unresolved_status)
        self.assertIn(
            'detail: "已達三輪上限；等待自動修復器定案或重新排程"',
            unresolved_status,
        )
        self.assertIn('{getUnresolvedCardStatus(res).label}', rail)
        self.assertIn('{getUnresolvedCardStatus(res).detail}', rail)
        self.assertIn('label: "第三輪已完成／自動定案中"', unresolved_status)
        self.assertIn('detail: "完成後立即排入逐張上傳"', unresolved_status)
        self.assertIn("!isExplicitlyUnresolved(res) && res.view_type !== '遠景'", rail)
        self.assertIn('!isExplicitlyUnresolved(res) && res.view_type &&', rail)

    def test_backend_status_exposes_cached_asset_fingerprint(self):
        backend = (Path(__file__).resolve().parents[1] / "samsung_ocr_batch_processor.py").read_text(encoding="utf-8")
        self.assertIn('"frontend_asset_fingerprint"', backend)
        self.assertIn("get_frontend_asset_fingerprint", backend)
        self.assertIn("_frontend_asset_cache", backend)
        self.assertIn('"presentation_sequence_durable": True', backend)

    def test_processed_and_review_metrics_are_not_presented_as_quality_success(self):
        root = Path(__file__).resolve().parents[1]
        backend = (root / "samsung_ocr_batch_processor.py").read_text(encoding="utf-8")
        app = (root / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        self.assertIn('"verified": verified', backend)
        self.assertIn('"review_required": review_required', backend)
        self.assertIn('判讀記錄 ({stats.success})', app)
        self.assertNotIn('待人工校正 ({stats.review_required ?? 0})', app)
        self.assertIn('自動定案紀錄', app)
        self.assertIn("{l:'完成判讀', v:stats.success", app)
        self.assertIn("{l:'自動定案中', v:cappedAdjudicationCount", app)
        self.assertIn("Number(data.capped_adjudication?.count || 0)", app)
        self.assertNotIn('成功記錄 ({stats.success})', app)

    def test_history_is_loaded_on_demand_and_user_labels_are_localized(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        self.assertIn("/api/presentation_history/", app)
        self.assertIn("historyCache", app)
        self.assertIn("判讀歷程載入中", app)
        self.assertIn("複核原因：", app)
        self.assertIn("使用模型：", app)
        self.assertIn("初次辨識總進度", app)
        self.assertIn("初次辨識總進度 {formatCount(overallProcessed)}/{formatCount(overallTotal)} 張", app)
        self.assertIn("`${overallPercent}%`", app)
        self.assertIn("formatCount(overallProgress.remaining_images)", app)
        self.assertIn('data-testid="review-pass-progress"', app)
        self.assertIn("const completedPassCount = Math.max(0, Number(data.presentation_sequence || 0));", app)
        self.assertIn("data.presentation_sequence_durable === true", app)
        self.assertIn(": '本次服務判讀'", app)
        self.assertIn("` · ${completedPassLabel} ${formatCount(completedPassCount)} 次`", app)
        self.assertIn("` · 本張第 ${reviewProgress.current_pass}/3 輪`", app)
        self.assertIn("recent_durations: (Array.isArray(apiResult?.recent_results)", app)
        self.assertIn("const recentDurations = Array.isArray(data.recent_durations)", app)
        self.assertIn("recentAverageDuration || data.metrics?.last_duration", app)
        self.assertIn("{l:'近期平均', v:recentAverageDuration", app)
        self.assertIn('const isStructuredModelOutput = (text)', app)
        self.assertIn('const humanizeStructuredModelOutput = (text, fallback)', app)
        self.assertIn('AI 正在逐項核對本張照片', app)
        self.assertIn('const rawSameFileStream = liveFile === currentFile', app)
        self.assertIn('humanizeStructuredModelOutput(rawSameFileStream, pendingNarration)', app)
        self.assertIn("<span>資料匣 {formatCount(folderDone)}/{formatCount(folderTotal)}</span>", app)
        self.assertIn('<span aria-hidden="true">·</span>', app)
        self.assertNotIn("primaryProgressProcessed", app)
        self.assertNotIn("primaryProgressPercent", app)
        self.assertIn("reviewPeriodMatches.at(-1)", app)
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
        self.assertIn("hasPassMetadata(visiblePassPresentation)", app)
        self.assertIn("hasPassMetadata(pendingPanelResult)", app)
        self.assertIn("hasPassMetadata(res)", rail)
        self.assertIn("hasPassMetadata(inspectImage)", app)
        self.assertIn("hasPassMetadata(pass)", app)
        self.assertIn("const getPassHeading = (item)", app)
        self.assertIn("if (!hasPassMetadata(item)) return \"\"", app)
        self.assertNotIn("item.model_id || result.model_id || item.model || result.model", app)
        self.assertNotIn("第 {formatMetaValue(activePresentation?.pass_index)} 輪", app)
        self.assertNotIn("第 {formatMetaValue(activePresentation?.pass_index)} 輪 · {getPassLabel(activePresentation)} · {formatMetaValue(activePresentation?.model_id)}", app)

    def test_llm_panels_never_render_bare_structured_json(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        self.assertIn("isStructuredModelOutput(text)", app)
        self.assertIn("humanizeStructuredModelOutput(rawSameFileStream, pendingNarration)", app)
        self.assertIn("parsed?.narration || parsed?.desc", app)
        self.assertIn("isStructuredModelOutput(text.replace(/^\\[THINK\\]\\s*/, ''))", app)

    def test_stale_guard_revision_cards_are_never_presented_as_accepted(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        self.assertNotIn('const CURRENT_GUARD_REVISION', app)
        self.assertIn('const serverGuardRevision = String(data.evidence_guard_revision || "").trim()', app)
        self.assertIn('const isStaleGuardRevision = (item)', app)
        self.assertIn('String(item.evidence_guard_revision || "") !== serverGuardRevision', app)
        self.assertIn('if (isStaleGuardRevision(item))', app)
        self.assertIn('label: "等待新版複核"', app)
        self.assertIn('detail: "新版守門接手後自動處理"', app)
        self.assertIn('decision === "retry_scheduled" && passIndex > 0 && passIndex < 3', app)
        self.assertIn('label: "第三輪已完成／自動定案中"', app)

    def test_active_content_repair_is_not_labeled_idle(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        self.assertIn("const contentRepairActive = Boolean(", app)
        self.assertIn("data.pipeline_pause?.reason", app)
        self.assertIn("data.pipeline_pause?.paused_at", app)
        self.assertIn("? '內容守門修復中'", app)
        self.assertIn("{operationStatusLabel}", app)

    def test_stream_upload_is_visible_work_and_reports_automatic_handoff(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        self.assertIn("const streamUploadActive = data.stream_upload?.worker_state === 'running'", app)
        self.assertIn("const visibleOperationActive = isRunning", app)
        self.assertIn("|| zeroModelAdjudicationActive", app)
        self.assertIn("|| streamUploadActive", app)
        self.assertIn("? '逐張上傳中'", app)
        self.assertIn("逐張上傳中 · OCR 自動銜接", app)
        self.assertIn("上傳佇列清空後，Supervisor 會自動銜接下一個資料匣", app)
        self.assertIn("'系統處理內容' : 'LLM 判讀內容'", app)

    def test_idle_completed_folder_is_visible_as_automatic_handoff_not_idle(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        self.assertIn("const automaticHandoffActive = !isRunning", app)
        self.assertIn("data.overall_progress?.current_folder?.status === 'active_idle'", app)
        self.assertIn("['photo_ocr', 'historical_continuation'].includes(pipelineTelemetry.phase)", app)
        self.assertIn("pipelineTelemetryAgeMs <= 90000", app)
        self.assertNotIn("&& Number(data.stream_upload?.worker_pid || 0) > 0;", app)
        self.assertIn("|| automaticHandoffActive", app)
        self.assertIn("? '自動銜接中'", app)
        self.assertIn("年度完成核對中 · OCR 自動銜接下一個資料匣", app)

    def test_zero_model_adjudication_is_active_and_never_impersonates_live_llm(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        self.assertIn(
            "data.pipeline_pause?.reason === 'capped_zero_model_adjudication_apply'",
            app,
        )
        self.assertIn("const zeroModelAdjudicationActive = !isRunning", app)
        self.assertIn("? '零模型自動定案中'", app)
        self.assertIn('"零模型證據定案中（未呼叫 LLM）"', app)
        self.assertIn(
            "zeroModelAdjudicationActive ? '零模型證據定案中' : 'LLM 即時判讀中'",
            app,
        )
        self.assertIn("const zeroModelProgressText =", app)
        self.assertIn("zeroModelActivity.processed", app)
        self.assertIn("zeroModelActivity.total", app)
        self.assertIn("disabled={visibleOperationActive}", app)

    def test_zero_model_mode_does_not_depend_on_the_capped_counter(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        start = app.index("const zeroModelPauseActive =")
        end = app.index("const zeroModelAdjudicationRepair =", start)
        mode_contract = app[start:end]
        self.assertIn("data.pipeline_pause?.reason", mode_contract)
        self.assertIn("zeroModelActivity.active === true", mode_contract)
        self.assertNotIn("cappedAdjudicationCount", mode_contract)

    def test_result_rail_refuses_blank_folder_scope(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        self.assertIn(
            "if (!currentResultRailBatchKey || currentResultRailBatchKey === resultRailBatchKey) return;",
            app,
        )
        self.assertIn(
            'normalizeBatchPath(data.source_root || data.image_dir || "")',
            app,
        )
        self.assertIn('serverGuardRevision ? `revision:${serverGuardRevision}` : ""', app)

    def test_backend_narration_snapshot_cannot_be_hidden_by_animation_state(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        self.assertIn("const activeNarrationSnapshot = activePresentation", app)
        self.assertIn("const visibleNarrationSnapshot = liveStreamSnapshot", app)
        self.assertIn("const narrationAnimationOwnsDisplay", app)
        self.assertIn('displayTargetKey === visibleNarrationKey', app)
        self.assertIn('displayedBuffer || "正在接收本張照片的 LLM 判讀文字..."', app)
        self.assertIn('data-narration-source={visibleNarrationKey}', app)
        self.assertIn("'系統處理內容' : 'LLM 判讀內容'} · {narrationStatusLabel}", app)
        self.assertIn("!isRunning && visibleNarrationSnapshot?.text", app)

    def test_idle_dashboard_uses_latest_completed_history_without_fake_live_state(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        self.assertIn("const latestCompleted = revealedResults.find(isCompletedRailResult) || null", app)
        self.assertIn("const latest = !isRunning && latestCompleted", app)
        self.assertIn("if (!isRunning && revealedResults[0])", app)
        self.assertIn("key: `latest:${latest._queueKey}`", app)
        self.assertIn('!isRunning && visibleNarration\n    ? "最新完成判讀"', app)

    def test_verified_result_cannot_be_downgraded_by_legacy_review_placeholder(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        normalize_start = app.index("const normalizePresentationItem =")
        normalize_end = app.index("const getResultRailIdentity", normalize_start)
        self.assertIn("auto_verified: item.auto_verified ?? result.auto_verified", app[normalize_start:normalize_end])
        unresolved_start = app.index("const isExplicitlyUnresolved =")
        unresolved_end = app.index("const hasPassMetadata", unresolved_start)
        unresolved = app[unresolved_start:unresolved_end]
        self.assertIn("if (item.auto_review_required === true) return true", unresolved)
        self.assertIn("if (item.auto_verified === true) return false", unresolved)
        self.assertLess(unresolved.index("if (item.auto_verified === true) return false"), unresolved.index("const reviewStatus"))

    def test_photo_and_narration_share_one_presentation_identity(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        self.assertIn("currentImagePresentationKey === expectedVisualKey", app)
        self.assertIn("effectiveVisibleImagePresentationKey === expectedVisualKey", app)
        self.assertIn("setCurrentImageTarget({", app)
        self.assertIn("setVisibleImageTarget({", app)
        self.assertIn('data-presentation-key={effectiveVisibleImagePresentationKey}', app)
        self.assertIn('data-testid="active-photo" data-presentation-key={expectedVisualKey}', app)
        self.assertIn("為避免照片與判讀錯配", app)
        self.assertNotIn("{visibleImage && <img", app)

    def test_header_filename_uses_the_visible_presentation_identity(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        label_start = app.index("const currentFileLabel =")
        label_end = app.index("const narrationPhase", label_start)
        label = app[label_start:label_end]
        self.assertIn("displayedFileName", label)
        self.assertIn("data.current_file", label)
        self.assertLess(label.index("displayedFileName"), label.index("data.current_file"))

    def test_running_handoff_never_bypasses_queue_with_latest_result(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        self.assertIn("const heldNarrationSnapshot = !activePresentation && !liveStreamSnapshot", app)
        self.assertIn("|| heldNarrationSnapshot", app)
        self.assertIn("|| (!isRunning ? latestBackendNarration : heldNarrationSnapshot)", app)
        target_start = app.index("const getDisplayTarget = () =>")
        target_end = app.index("const activeVisualKey", target_start)
        target = app[target_start:target_end]
        self.assertIn("if (!isRunning)", target)
        self.assertNotIn("if (latest) return", target.split("if (!isRunning)", 1)[0])

    def test_result_rail_accumulates_independently_from_live_narration(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        self.assertIn("const mergeResultRailItems", app)
        self.assertIn("item?.source_item_id || item?.source_path || item?.file_name", app)
        self.assertIn("const comparePresentationsDescending", app)
        self.assertIn(".sort(comparePresentationsDescending)", app)
        self.assertIn("Date.parse(item?.completed_at || item?.started_at || \"\")", app)
        self.assertIn("setRevealedResults((prev) => mergeResultRailItems([...completed, ...prev]))", app)
        self.assertIn("data.review_progress?.processed", app)
        self.assertIn("data.stats?.processed", app)
        self.assertIn("rehydrate durable", app)
        self.assertIn("samsung_ocr_result_rail_v2", app)
        self.assertIn("normalizeBatchPath(data.source_root || data.image_dir || \"\")", app)
        self.assertIn('/api/presentation_history?limit=200&scope=current_batch', app)
        self.assertIn('fetch("/api/presentation_history?limit=200")', app)
        self.assertIn("&& isCompletedRailResult(item)", app)
        self.assertIn("data.overall_progress?.processed_images", app)
        self.assertIn("data.stream_upload?.canonical_uploaded", app)
        self.assertIn('allowed.has(String(item?.source_item_id || ""))', app)
        self.assertIn('String(item?.evidence_guard_revision || "") === serverGuardRevision', app)
        self.assertNotIn('String(item.run_id || "") === expectedRunId', app)
        self.assertNotIn('data.presentation_run_id || "legacy"', app)
        self.assertIn('setCurrentImageTarget({ src: "", key: "", fileName: "" })', app)
        self.assertIn('setVisibleImageTarget({ src: "", key: "", fileName: "" })', app)
        self.assertIn('isRunning && data.current_file && data.current_file !== "None"', app)
        self.assertIn("saved?.batchKey === currentResultRailBatchKey", app)
        self.assertIn("items: revealedResults", app)
        self.assertIn("cappedItems: cappedRailItems", app)
        rail_start = app.index("// The live LLM stream must never block completed photos")
        rail_end = app.index("// Never let a stale async update", rail_start)
        self.assertNotIn("getSyncedLiveStream", app[rail_start:rail_end])
        self.assertNotIn("if (!isRunning) return;", app[rail_start:rail_end])
        self.assertIn("}, [data.presentation_queue]);", app[rail_start:rail_end])
        self.assertNotIn("slice(-1)", app[rail_start:rail_end])

    def test_capped_adjudication_count_is_separate_from_completed_result_cards(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        self.assertIn("MAX_CLIENT_CAPPED_ADJUDICATIONS = 20", app)
        self.assertIn("normalizeCappedAdjudicationItem", app)
        self.assertIn('const sourceItemId = String(item.source_item_id || "").trim();', app)
        self.assertIn('const sourcePath = String(item.source_path || "").trim();', app)
        self.assertIn('const fileName = String(item.file_name || "").trim();', app)
        self.assertIn("normalizedSourcePath.startsWith(`${normalizedBatchPath}\\\\`)", app)
        self.assertIn("_queueKey: `capped:${sourceItemId}`", app)
        self.assertIn('data-testid="capped-adjudication-summary"', app)
        self.assertIn("不占用完成結果縮圖區", app)
        self.assertNotIn('data-testid="capped-adjudication-card"', app)
        self.assertNotIn("{cappedPanelItems.map((res) => (", app)
        completed_cards = app.index("{rightPanelItems.map((res, i) => (")
        capped_summary = app.index('data-testid="capped-adjudication-summary"')
        self.assertLess(completed_cards, capped_summary)

    def test_result_rail_excludes_retry_review_and_technical_history(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        helper_start = app.index("const isCompletedRailResult =")
        helper_end = app.index("const getUnresolvedCardStatus =", helper_start)
        helper = app[helper_start:helper_end]
        self.assertIn("isStaleGuardRevision(item)", helper)
        self.assertIn("isTerminalTechnicalFailure(item)", helper)
        self.assertIn("isExplicitlyUnresolved(item)", helper)
        self.assertIn('decision === "accepted"', helper)
        self.assertIn("item.auto_verified === true", helper)
        self.assertIn("item.accepted === true", helper)
        self.assertIn(
            "const rightPanelItems = revealedResults\n    .filter(isCompletedRailResult)\n    .slice(0, MAX_REVEALED_RESULTS);",
            app,
        )

    def test_result_rail_preserves_recent_completed_cards_across_folder_transitions(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        key_line = next(line for line in app.splitlines() if "const currentResultRailBatchKey =" in line)
        key_block = app[app.index(key_line):app.index("const resultRailStorageKey", app.index(key_line))]
        self.assertIn("normalizeBatchPath", key_block)
        self.assertIn("source_root", key_block)
        self.assertIn("image_dir", key_block)
        self.assertIn("serverGuardRevision", key_block)
        self.assertNotIn("current_relative_dir", key_block)
        self.assertNotIn("presentation_run_id", key_line)
        history_start = app.index('fetch("/api/presentation_history?limit=200&scope=current_batch")')
        history_end = app.index("// The backend exposes only a compact recent window", history_start)
        history = app[history_start:history_end]
        self.assertIn("isAllowedCurrentRevision", history)
        self.assertIn("allowed.has", history)
        self.assertIn("serverGuardRevision", history)
        self.assertNotIn("expectedRunId", history)
        self.assertIn("...prev.filter", app)
        self.assertIn("serverGuardRevision", app)
        self.assertNotIn("setRevealedResults(restored)", app)
        self.assertIn('data-testid="result-rail-hydrating"', app)

    def test_legacy_status_polling_is_bounded_non_overlapping_and_lightweight(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        self.assertIn("const LEGACY_STATUS_POLL_MS = 5000", app)
        self.assertIn("const MAX_CLIENT_STATUS_PRESENTATIONS = 24", app)
        self.assertIn("sanitizeStatusPayload(await response.json())", app)
        self.assertIn("recent_results: []", app)
        self.assertIn("if (cancelled || inFlight) return", app)
        self.assertIn("timerId = window.setTimeout(poll, delay)", app)
        polling_start = app.index("// Poll API with Dynamic Interval")
        polling_end = app.index("useEffect(() => {\n    console.log(\"App: useEffect (Initial Styles) Start\")", polling_start)
        self.assertNotIn("setInterval", app[polling_start:polling_end])

    def test_dashboard_containment_preserves_finalized_half_screen_layout(self):
        app = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")
        css = (Path(__file__).resolve().parents[1] / "dashboard" / "src" / "index.css").read_text(encoding="utf-8")
        self.assertIn('className="dashboard-body" style={{ flex: 1, minWidth: 0, minHeight: 0', app)
        self.assertIn('className="monitor-workspace" style={{ flex: 1, minWidth: 0, minHeight: 0', app)
        self.assertIn("flexDirection: 'column', minWidth: 0", app)
        self.assertIn("flex: '0 0 50%'", app)
        self.assertIn("width: clamp(360px, 23vw, 430px) !important", app)
        self.assertIn("flex: 1 1 0 !important;\n  height: auto !important;", css)
        self.assertNotIn("height: 100% !important;\n  min-height: 0 !important;", css)
        self.assertIn("gridTemplateColumns: 'minmax(0, 1.15fr) minmax(0, 0.95fr) minmax(0, 2.35fr) max-content'", app)
        self.assertNotIn("minmax(520px, 38vw) 118px", app)
        self.assertIn("overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap'", app)
        self.assertIn('const currentFolderPath = activeDirectoryText || String(overallProgress.current_folder || "")', app)
        self.assertIn('`商化照片-${reviewPeriodLabel}`', app)
        self.assertIn('title={currentFolderPath || currentFolderLabel}', app)
        self.assertNotIn('title={data.image_dir || currentFolderLabel}', app)
        self.assertIn("上傳總數 {formatCount(streamUpload.canonical_uploaded)} · 待上傳 {formatCount(streamUpload.pending)}", app)
        self.assertNotIn("系統技術重試", app)
        self.assertNotIn("本張第 ${reviewProgress.current_pass} 輪技術重試", app)

if __name__ == "__main__":
    unittest.main(verbosity=2)

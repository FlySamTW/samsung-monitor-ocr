---
description: Technical Rulebook & Post-Mortem for Samsung OCR Project
---

# SAMSUNG_OCR_EXPERIENCE (Project Bible)

## Highest iron rule: correctness belongs to the local program

- Permanent priority is **photo correctness > OpenAI/Codex token savings > elapsed time**.
- Correctness must be enforced by stable local code, evidence contracts, at most three stateless LM Studio calls, deterministic terminal invariants, per-photo upload receipts, and permanent regression tests. Codex/OpenAI must not become a per-photo OCR worker, a daily rule editor, or a required human-in-the-loop component of the production pipeline.
- Formal batch OCR runs only through the local LM Studio model and local scripts. Codex fixes systemic root causes, validates regressions, reads compact local summaries at 09:00 and 21:00, and intervenes only when the local monitor emits an anomaly.
- Repeated detection of the same error class proves a program defect. Stop only at the affected photo boundary, repair the shared cause, add a reproducing test, and auto-resume the saved checkpoint. Never keep rerunning, patch only the current photo, clear the warning, or wait for the next Codex monitor.
- Healthy monitoring must stay local and compact. Do not send full status payloads, model logs, images, or the whole history to Codex on every cycle. Token savings may never weaken the accuracy gates.
- `samsung_ocr_prompt.txt` is a proven Qwen 2.5 baseline created through repeated real-photo iteration. A move to Qwen 3 VL 8B or any other LLM must prove that the new model preserves this prompt's meaning on a fixed, versioned regression corpus covering view, complete-screen count, FollowMe, model, price, label ownership, terminal naming, and upload. Do not deploy the new model before non-regression is demonstrated.
- Never broadly rewrite, shorten, or replace the formal prompt merely to accommodate a new model. If accuracy falls, recover the last proven high-accuracy prompt from Git first, then make the smallest context/output compatibility change with recorded diffs, corpus results, and an immediate rollback commit.

## Revision `.36` 型號身分規則（2026-07-18）

- `skills/model_catalog_rules.py` 是一般型號正規化、六款 FollowMe 名稱與面板對照的唯一共用權威；不得在各工具另寫一份價格或尺寸推導表。
- 官網完整碼只移除前導 `L` 與結尾 `XZW`。型號只能精確命中、唯一補齊 1–3 個尾碼，或在同尺寸同系列內做唯一的有限近似修正；模糊或多候選時不得自動選型號。
- FollowMe 正式名稱為 M5 27、M5 32、M7 32、Pro M7 32、M7 43、Pro M7 43。只確認 FollowMe 家族時固定輸出 `FollowMe 型號未細分`。
- 面板 SKU 只能核對一般版的系列與尺寸；`S32FM703UC`、`S43FM703UC` 等共用 SKU 不能證明 Pro。Pro 必須有同一台實機或附著牌面明確的 `Pro` 證據。
- 價格只能作為現場價與官網價資料，不能選擇或更正型號。所有 FollowMe 都是 Smart 系列，不需要 OSD。
- `.36` 只會在程式下次安全重啟後生效；Git 更新本身不得中斷執行中的 OCR。

## Revision `.25` per-photo completion rule (2026-07-16, supersedes older review-isolation text)

- Every content photo finishes with one truthful result: `遠景`, or `單機` with model+price, model only, price only, or neither.  Missing model/price is data, not a reason to abandon the photo.
- A no-complete-screen scene (`count=0`, no unique main, no owned label, no strong FollowMe fixture) may finalize as `遠景／無型號／無價格` with two usable votes.  Counts 1-2 remain unsafe as distant because they may hide a partial main unit.
- Round 2 and round 3 must receive only the fixed rules and the current pristine image.  Never expose a prior answer, correction, prior model/price, retry prose, or a previous photo to the model.
- Each photo has a hard maximum of three total model calls, including transport/parser failures.  Persist the call budget before inference; configuration, restart state or exception handling may never create call/pass 4–6.  After the third call, `finalize_three_pass_outcome` makes the bounded evidence decision or emits one terminal technical result.  View needs two usable votes.  Model and price each need two field-safe votes; never construct a model/price pair that no two passes jointly supported.  Two passes with sufficient same-subject FollowMe physical evidence establish the FollowMe family; when M5/M7/Pro lacks two-pass support, save `FollowMe 型號未細分` instead of guessing or reverting to distant.
- A photo-local FollowMe narration/structure conflict is contained inside that photo's three-call budget. Repetition of the same local conflict class on another photo is monitoring evidence, not proof of memory infection. Only direct prior-answer leakage, copied cross-photo identity, prompt contamination, or request/image binding failure may stop the whole batch.
- A bare `S32FM...` or `S43FM...` SKU is a Smart Monitor panel identity, not proof of a FollowMe mobile bundle. Establish FollowMe first from explicit on-unit branding or sufficient same-subject fixture evidence; only then may the SKU confirm the regular family and size. A shared panel SKU never proves Pro. After three calls, any disagreement among FollowMe variant/price pairs clears both fields and finalizes the truthful family result as `FollowMe（型號未細分）／無價格`.
- If all three bound calls describe a whole row, display wall, multi-level shelf, or wide aisle, every call lacks model/price/FollowMe hardware, and at least two structurally count 3+ complete monitors, finalize `遠景／無型號／無價格` even when all three mistakenly label the view as single. Structured scene evidence overrides that correlated label error.
- If all three calls are healthy, stateless, bound to the same image hash, and the only invalid contract is a `遠景` claim with 1–2 complete screens, that pass cannot vote as distant and the photo cannot become a permanent technical hole. Without another safe view majority, finalize conservatively as `單機` with unsupported model/price left empty and upload it.
- `complete_screen_count` is counted exactly once from the first original full image. A monitor counts only when all four outer bezel sides and all four bezel corners are inside that original frame. Any monitor touching or crossing an original image edge is incomplete even when most of the panel is visible. Every pass keeps the pristine full image and label crops but receives no duplicate full-height scene tile; isolated replay proved that even one center duplicate can corrupt the count. Before using the full-center plus edge-cut-neighbor example, scan the entire original by left/center/right and top/middle/bottom and count all complete monitors outside the center. That layout has count 1 only when no other complete monitor exists anywhere else; upper/lower rows, distant shelves, and other fixtures must not be omitted. The edge-cut exception is only for a tight close composition and never applies to a whole row, display wall, multi-level shelf, or wide aisle. One healthy 3+ distant structural pass vetoes two weak single votes with no bound model/price; it never vetoes two identity-bound single passes or two FollowMe physical-evidence passes. Pass 2 inventories positions and pass 3 counter-scans outside the center to avoid correlated example bias. A lower-left-center shelf/card crop may improve small text but may never contribute to monitor counting or replace ownership checks against the pristine image. Brand names or advertisements rendered inside screen pixels are signal content, never the physical monitor brand and never grounds to reject an aligned Samsung product card. Narration and structured price digits must match for every physical SKU, not only friendly FollowMe names; disagreement withdraws that pass and triggers an independent retry.
- Content uncertainty never becomes `三輪衝突／已隔離`, never waits for a slow model or unspecified person, and never blocks upload.  Only request/image binding failure, prior-answer or prompt contamination, cross-photo drift, invalid runtime/evidence output, changed source bytes, or failed Drive readback is a technical stop for that photo.
- Existing rows that already consumed three model calls must be repaired deterministically from their bound trace or full-image human pixel authority and immediately enqueued; never call the model a fourth time. If attempt 1 was consumed before a process-boundary fuse but missed its trace append, recovery is allowed only for an exact audited image hash when persisted attempt numbering proves the remaining clean calls are attempts 2 and 3 and the saved result records the three-call hard limit.
- On every finalized result, atomically enqueue one `_drive_upload_stream` job.  The hidden single worker uploads that photo while OCR continues.  Exact same-name stale remote content is replaced in place, `_2` is forbidden, and no receipt is written until unique size+MD5 readback succeeds. A receipt is reusable only when guard revision, source bytes, and deterministic target name still match; an older-revision receipt is archived under `superseded_receipts` and must not suppress the corrected job.
- Price symbols compare the observed store price with the reliable Samsung/PChome reference: `↑` higher, `↓` lower, `✓` equal, `?` no reliable reference.  Preserve that symbol in the deterministic output name.
- Dashboard copy is fixed: `第三輪已完成／自動定案中` and `完成後立即排入逐張上傳`.  Never show slow-model, manual-adjudication, isolation, or "will not upload" wording for ordinary content results.
- Planned deployment is green-first: code, targeted tests and built assets first; hidden spare-port health proof second; original-address handoff last.  Never stop the only live OCR before the replacement is ready.  Header title/version/full progress/live-upload status use non-overlapping grid columns; the 50/50 workspace and right rail remain unchanged.

## Presentation identity iron rules

Dashboard asset freshness is also a presentation invariant: `/api/status`
reports the fingerprint of the built script/css assets, and the frontend may
perform one cache-busting `/?ui=` replace only on mismatch, guarded by a
30-second session cooldown. A matching fingerprint must not reload, and this
UI refresh must never restart OCR or the backend.

The results rail may show one active placeholder above `revealedResults`. This
is not a result: it must be the exact active snapshot and key, showing only
the thumbnail, filename, `處理中`, and `AI 即時判讀中`. It disappears when the
same snapshot becomes the top revealed card; never create it from current
file, recent results, index, or filename joins.

- Backend `presentation_id` is the sole identity truth; snapshots are
  immutable and carry a monotonic sequence.
- Photo, AI narration, revealed card, and modal must reference the same
  snapshot. Never join them with filename, index, or `source_path`.
- The visible `目前檔案` header is part of that same snapshot. During a handoff,
  it must keep the still-visible presentation filename until the next live
  presentation owns both photo and narration; raw backend `current_file` may
  be used only as a fallback when no visible presentation identity exists.
- The right rail reads only revealed results. During OCR, neither
  `recent_results` nor `current_file` may enter presentation as fallback.
- Keep the previous image until the next image is loaded; never blank the main
  preview during a normal transition.
- The fixed operator label is `LLM 判讀內容`; the visible body must be the current model's readable narration, never bare JSON, mojibake, backend-generated fake thinking, or another photo's text.
- The visible `目前資料匣` value is the complete business label (`商化照片-YYYYMM` during review), never a clipped `_ocr_staging` implementation path. Preserve the full path only as hover detail and regression-test it together with the far-right upload status, narration, thumbnails, and 50/50 layout.
- Every presentation change requires the 500-item soak, dashboard build, and
  live 3-transition browser verification. Preserve `logs/ui_sync_v1944_live.*`.

## Compact status and clean result-rail rule (2026-07-14)

- `/api/status` is bounded live transport. `compact-v2` exposes at most 12 recent presentation events (hard maximum 24), no inline image/base64/raw evidence, and a small compatibility-only `recent_results`. Durable pass history is read by stable `source_item_id` through the history API.
- Treat a status response above 500 KB as a production failure. Large repeated payloads can make the AI narration disappear or appear frozen while OCR itself remains healthy.
- The right result rail shows only thumbnail, filename and concise operator result/badges. Internal retry reason, model id, timestamps, decision code, previous-result summary and expanded history belong only in the click-through inspection view.
- If pass metadata is absent, hide the pass row. Never render `第 未提供 輪 · 未提供 · 未提供` or equivalent placeholder chains.
- Frontend-only fixes may be deployed without stopping OCR by placing hashed assets first and replacing `dist/index.html` last. Backend upgrades must wait for two consecutive idle/complete/no-worker observations and pass compact/history/fingerprint verification before releasing the shared interlock.

## v19.37 Non-Regression Guard (2026-07-10)

- Local LM Studio instances may run Qwen3-VL with only an 8K context. The full historical prompt is longer than that after image tokens are included. Runtime requests must use `build_runtime_system_prompt()`; when the full file is too long, it must select the maintained compact ruleset rather than sending an over-limit request. A context-limit error is a retryable configuration fault, never a valid OCR outcome.
- A FollowMe rescue may prevent a false distant result from becoming upload-ready, but it must not overwrite or conceal contradictory model narration. Preserve the contradiction in trace, withdraw it from completed display, and retry/review; never fabricate a fallback that looks like model evidence.
- Never promote `單機 + 無型號/無價格` to `遠景` merely because its narration says `展示區`, `貨架`, or `多台`. Promotion needs the explicit final distant conclusion plus at least two display-wall layout signals, and no single-unit or FollowMe evidence. Ambiguous cases remain single-unit review rows and stay blocked from Drive.
- `display_queue` is a live batch-session transport only. Clear it before every new batch and do not expose it from `/api/status` while idle. Otherwise the dashboard can replay a deleted staging file and desynchronize photo, AI narration, and the right-side card.
- Required regression command after touching these paths: `python tools/test_runtime_safety_guards.py`, then `npm --prefix dashboard run build`.

## Dashboard Live Sync Rule (2026-07-01)

For live OCR runs, preview image, AI narration, and parsed result must never be mixed across filenames.

- `current_file`: the active image being processed and shown in the main preview.
- `stream_file`: the image that owns `stream_buffer`.
- `latest_result_file`: the newest completed OCR record for history/recent-results UI.

Do not use `recent_results[0]` to update the main preview while a batch is running. It is usually the previous completed image and will make the UI look one image out of sync. The frontend should update the main image only when `current_file` changes, and should show live AI narration only if `stream_file === current_file`.

## Delayed Result Panel Rule (2026-07-03)

The user-facing sequence must be:

1. Show the photo in the main preview.
2. Play that photo's AI narration/typewriter text to completion.
3. Do not show unfinished backend items in `辨識紀錄`. The right rail is for completed, frontend-revealed records only.
4. When that AI narration has finished but the next photo's narration has not started, keep the completed narration visible as a held previous-summary state. Never let the AI narration pane go blank or collapse to a black empty area during normal running.
5. Only after AI narration completes, reveal that photo's parsed thumbnail/model/price/status in `辨識紀錄`.

The backend may finish multiple photos ahead, but the right panel must never reveal a photo's model/price/status before its AI narration has finished. It also must not leave the previous completed result at the top while a new photo is typing, because that looks like mismatched metadata. In `dashboard/src/App.jsx`, prefer the display queue and already-presented cutoff over `recent_results` whenever a queue exists. `recent_results` is backend-speed data and will confuse viewers if shown early.

Interface presentation is a first-class requirement. If the AI pane blanks out between photos, the monitor looks broken even when OCR data is correct. Preserve visual continuity: previous narration may stay softly visible until the next narration begins, with a clear previous-summary label.

## Dashboard Stage Clock Rule (2026-07-07)

The dashboard is judged by viewers before they understand the backend. Treat the live monitor like a staged presentation, not a raw log tail.

- The visible AI pane must be independent from the internal typing cursor. Clearing `displayedBuffer` for the next item must never create a black or empty narration area.
- During the gap between photos, keep the previous narration visible with a calm status label such as "previous summary held / next photo judging".
- The sequence is a visual contract: new photo appears, the AI pane immediately has either held narration or live typing, then the right-side result is revealed.
- A completed photo can arrive between two status polls after the compact live queue has already cleared. Rehydrate durable same-run history whenever the completed-photo counter changes; otherwise the final processed card can be missing even though the button counter is correct. Do not solve this with an extra high-frequency poll.
- At the exact moment the right-side model/price/status is revealed, the AI pane label must switch from "live judging" to a completed/revealed state. Never show "AI is still judging" while the formal result is already visible.
- Once a stable queue key has been revealed, later buffer updates for that same key must not downgrade the visible phase back to "typing/live judging".
- AI narration needs a minimum readable dwell time. Do not make the typewriter or revealed-summary hold so fast that narration flashes by just to chase backend throughput. Backend speed is allowed to run ahead; the monitor must still feel intentionally paced.
- If the backend runs ahead, catch up by trimming or dropping stale display-only queue items, not by blanking the AI area.
- User-facing dashboard copy must say `AI`, never `LLM`, and must never expose the old four-character internal shorthand (`自言` + `自語`).
- A UI that looks paused, black, or mismatched is a product failure even when OCR files are correct. Fix presentation defects with the same urgency as OCR correctness defects.

## Live Presentation Catch-Up Rule (2026-07-06)

The dashboard must balance two user-facing truths: no mixed metadata, and no frozen-looking preview. A pure "wait for every queued AI narration to finish" approach fails during fast OCR because the frontend can lag dozens of photos behind the backend.

- Keep the main preview, AI narration, and right-side thumbnail scoped to the same stable queue key.
- Keep a bounded local presentation queue; when the backend display queue is full, discard stale display-only items that are no longer in the backend's latest queue.
- Trim long AI narration for display so one photo cannot monopolize the boss-facing monitor.
- Add a watchdog: if the presentation layer stops advancing for several seconds, clear the stale active presentation and resume from the newest safe queue slice.
- Add `key={currentImage}` to the main preview image so React remounts it whenever the photo URL changes.
- This catch-up behavior only affects dashboard presentation. It must never delete OCR records, copied output photos, or audit rows.

## Live Sync No-Regression Rule (2026-07-07)

This is a non-negotiable rule for every future AI or developer touching the dashboard.

- The photo, visible AI narration, and right-side thumbnail/model/price must share the same stable item key.
- Preserve the visual trick: the user may be watching the previous completed item while the backend is already processing the next item, but the right-side result must only reveal after that item's AI narration has finished.
- Do not replace `pendingQueue`, `activePresentation`, and `revealedResults` with raw `recent_results` during OCR. That is the known path back to desync.
- `recent_results` is only an idle/history fallback. It must not drive the live main preview.
- Stopping a batch or switching folders must clear stale live presentation state. The backend may retain old `current_file` for audit, but the UI must not show that stale file as an active photo/AI pair.
- Keep the current photo visible until the next full-resolution image is ready; never blank or dim between photos during a normal run.
- Any UI timing change must be verified in the browser after rebuild with the sequence: photo visible -> AI text types -> right-side result reveals. If these three do not match, the change is not done.

## Overall Progress Rule (2026-07-06)

The dashboard header must show global OCR progress, not only the current folder's `processed/total`. `/api/status` exposes `overall_progress`, computed from `_ocr_audit/folder_discovery.csv`, `_ocr_audit/folder_summary.csv`, missing-result rerun summaries, and the active folder's live stats.

- Show total processed images, total source images, remaining images, completed folders, total folders, and the current folder progress.
- Do not scan the source tree from the browser. The backend owns this aggregation.
- The progress number is operational guidance, not upload readiness; Google Drive readiness still comes from `_drive_upload/drive_upload_summary.json`.

**Purpose**: To document critical engineering failures and strict rules for future development, ensuring mistakes are never repeated.

## 🔄 最新改動日誌 (v18.99+)

### [2026-06-30] 歷年接力與改名規格鎖定

**使用者已確認的邊界**：
- 可實作工具與文件，但不可直接開始跑全量歷年照片；全量執行要由使用者指定來源與輸出資料夾。
- 2K 指以 `2560` 長邊為基準；大於 2K 時長邊縮到 `2560`，短邊按原比例自然縮放，不裁切、不補白、不硬拉伸。
- `HEIC`、`WebP` 可以不處理；工具要列出略過，不可算進完成率。
- 改名後照片可以全部放同一層新資料夾，因為新檔名已包含年月。
- `去年以前不需要比價` 的工作解讀：以 2026 年執行時，`2025` 含以前不做 Samsung 官網價格比對。
- 當年度有官網比價時，檔名價格欄必須保留 `↑/↓/✓/？`，例如 `S27CG552EC-↑＄4990-1005.jpg`；歷史年度不比價時不加符號。
- `D:\00_歷年商化照片` 是這台電腦的外部照片資料位置，不是專案固定路徑；另一台電腦 pull 專案後可能使用不同照片根目錄。

**接力實作方向**：
1. 沿用現有 Dashboard / Flask 後端機制，不重寫 OCR 核心。
2. 自動化應呼叫 `/api/set_work_dir`、`/api/start_batch`、`/api/status`，讓一個資料匣跑完後自動切下一個。
3. 排序從最新月份往前。
4. 完成後用正式 `results.csv` 產改名計畫，再複製到同一層新輸出資料夾；不原地裸改照片。
5. 正式接力工具是 `tools/recursive_ocr_flat_export.py`，可由 `run_recursive_ocr_flat_export.bat` 啟動。
6. 另一台電腦上的 AI 接手執行時，先讀 `docs/ai_handoff_runbook.md`。
7. Long recursive OCR runs must keep refreshing source discovery. New folders added under the source root should be picked up automatically; if an already-handled folder changes during the same run, it should be re-queued instead of waiting for a manual restart.
8. 輸出資料夾不可等於來源根資料夾、不可放在來源根資料夾底下、也不可是來源根資料夾的上層，避免重跑時掃到自己輸出的照片或混入無關檔案。
9. `run_recursive_ocr_flat_export.bat` 啟動前要先用 `tools\validate_recursive_ocr_inputs.py` 預檢來源、輸出路徑、是否至少有一張 `.jpg/.jpeg/.png`，以及輸出第一層是否已有照片但缺少 `_ocr_audit\folder_summary.csv`；預檢失敗時不可啟動 LLM 或 OCR 後端。
10. `run_ocr.bat` 與 `run_recursive_ocr_flat_export.bat` 啟動前都要先用 `tools\stop_ocr_server.py` 清理既有 `samsung_ocr_batch_processor.py` 後端，避免連到舊程式。
11. AI、排程或非互動環境執行批次檔時要設定 `OCR_NO_PAUSE=1`，避免成功或錯誤結尾卡在 `pause`。
12. `run_recursive_ocr_flat_export.bat` 接力結束後預設清理本次 OCR 後端；若需要保留後端觀察狀態，可設定 `OCR_KEEP_SERVER=1`。
13. 接力器預設用 `_ocr_audit\folder_summary.csv` + `copied.csv` 續跑；已完整複製、來源照片數與最新修改時間未變、且目標檔案仍存在的資料夾標為 `skipped_existing`，避免中斷重跑時產生 `_2` 重複檔。
14. `run_recursive_ocr_flat_export.bat` 會在接力器跑完後自動用 `tools\recursive_ocr_audit_report.py --output-dir <輸出資料夾>` 驗收；手動拆跑 Python 接力器時也必須補跑驗收，通過才可回報全量完成。驗收摘要在 `_ocr_audit\audit_summary.json`，內含驗收時間、審計檔路徑與主要數量；失敗時看 `_ocr_audit\audit_report.csv`。
15. 若使用者只說 `GIT`，也要同步本專案專屬 SKILL；本檔就是本專案優先更新的 SKILL。
16. `tools\photo_rename_planner.py` 必須用 `period` 決定價格欄是否可帶比價符號；歷史年度即使 `results.csv` 殘留 `price_symbol`，輸出檔名也只能保留店內價格。修改後至少執行 `tools\test_photo_rename_planner.py`。

### [2026-03-05] 日誌與結果列表去重修復

**問題**：UIÂ 中辨識紀錄區和日誌區出現重複內容
- 辨識記錄列表：首列和最後各重複顯示一筆
- 日誌區：同一段思考文字顯示兩次（含重複「思考:」標題）
- 獨白欄（串流緩衝）：顯示多餘「思考:」前綴

**修復**：
1. **[skills/batch_orchestrator.py L972]** — 移除多餘 `recent_results.append()`
   - 原因：`insert(0, ...)` 已經加入記錄，再 `append` 造成首尾各一筆
   - 修法：只保留 `insert(0, ...)` 邏輯，刪除後續的 `append`

2. **[samsung_ocr_batch_processor.py L1113]** — 移除重複的思考日誌輸出
   - 原因：前面驗證區已發出 `[THINK]` 標記的日誌，此處 `💭 思考:` 為冗餘
   - 修法：刪除 `orchestrator.log_system(f"💭 思考: {thinking_text}")` 行

3. **[samsung_ocr_batch_processor.py L527]** — 清除獨白欄的「思考:」前綴
   - 原因：模型串流原文含 `思考:` 前綴，獨白欄不需顯示標題
   - 修法：在設定 `stream_buffer` 前用 regex 去除 `^思考[:：]\s*`

**架構理念確認**：
- ✅ Prompt 集中在 `samsung_ocr_prompt.txt`，規範不硬寫
- ✅ 程式碼只負責執行與日誌，不修改系統邏輯
- ✅ LM Studio 設定全在 LM Studio 面板，無硬寫
- ✅ 模型自動檢測：啟動時從 API 讀取實際加載的模型

---

## ⚠️ CRITICAL ENGINEERING RULES (Blood Lessons)

### 1. Process Management (The "Zombie" Rule)

- **Failure**: Old Python processes lingered in background, causing code updates to be ignored.
- **Rule**: ALL startup scripts (`.bat`) MUST forcefully terminate old processes before starting.
- **Command**: `powershell "Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force"`
- **Never**: Rely on user to manually close windows.

### 2. Path Handling (The "Relative Path" Trap)

- **Failure**: Backend crashed because relative paths (`./images`) resolved incorrectly when run from different contexts (VS Code vs Terminal).
- **Rule**: ALWAYS use **Absolute Paths**.
- **Code**: `os.path.abspath(args.dir)` or `os.path.join(os.getcwd(), ...)`

### 3. Frontend/Backend Sync (The "Cache" Illusion)

- **Failure**: User saw old UI version because browser cached `index.html`.
- **Rule**: Backend MUST set `Cache-Control: no-store` for `index.html`.
- **Rule**: Frontend assets MUST be hashed (`index-Hash.js`).

### 4. JSON Serialization (The "datetime" Crash)

- **Failure**: API returned 500 Error because `datetime` objects are not JSON serializable by default.
- **Rule**: ALWAYS implement a `CustomJSONEncoder` in Flask to handle `datetime`, `decimal`, etc.

### 5. Windows Encoding (The "???" Corruption)

- **Failure**: Modifying files with PowerShell `Set-Content` without encoding flags corrupted JS files with UTF-16.
- **Rule**: When patching files via shell, ALWAYS specify encoding (e.g., `-Encoding UTF8`).
- **Better**: Use Python scripts for file manipulation, not shell one-liners.

---

## OCR Logic & Business Rules

### 1. View Type Definitions

- **Distant View (遠景)**: >3 monitors, no readable labels.
- **Single Unit (單機)**: Readable label OR single dominant monitor OR FollowMe stand.
- **Other Brand Single Unit**: If rerun/recognition confirms the main monitor is not Samsung, set `model` to `它牌(BRAND)` such as `它牌(ACER)`, `它牌(ASUS)`, or `它牌(LG)`. Do not store the non-Samsung product model; do not leave it as `無型號`.

### 2. FollowMe Identification

- **Physical**: White Stand + Round Base + Tray.
- **Pricing**: $9,900 (M5 32"), $12,900 (M7 32").

### 3. Quality Assurance

- **Model**: Must verify character-by-character.
- **Price**: Must be on SAME label. Must have comma/symbol.

---

**This file serves as the memory of the project. Read it before writing code.**

---

## 2026-07-02 Active Handoff For Next AI

Use this section when taking over the Samsung OCR overnight job.

### Current live state

- Backend is running on `http://127.0.0.1:5000`.
- Model is `qwen/qwen3-vl-8b`.
- Runner is `tools/recursive_ocr_flat_export.py --watch`.
- Source is `D:\00_商化\00_未整理商化照片`.
- Flat output is `D:\00_商化\00_已OCR照片`.
- Hourly automation `samsung-ocr-hourly-monitor-and-email` should monitor and email `sam.lai@live.com`.

### Non-negotiable business rules

1. 2026/future photos require price comparison.
   - Prefer Samsung Taiwan official price.
   - If Samsung has no price, use PChome 24h Shopping, not marketplace.
   - If no reference price is found, stop/export-block and ask for manual review; do not silently emit final `？` filenames.

2. 2025 and older photos do not compare price.
   - They must not show `↑`, `↓`, `✓`, or `？` price compare symbols.
   - UI should not show a red `?` badge for historical/not-compared rows.

3. Never output `停產`.
   - Lookup failure is `unknown`, not discontinued.
   - Legacy `-` or `discontinued` must be normalized to `？` or blocked review.

4. `遠景` filenames omit model and price.
   - Correct format: `M-period-city-district-channel-store-遠景-serial.jpg`.
   - Do not output `遠景-型號未辨識-無價格`.

5. Low real monitor prices are valid.
   - `S24F332EAC / 2390` is a real monitor price.
   - Do not use the old 3000 cutoff. Current cutoff is `<2000`, with context checks for plans/accessories.
   - Handwritten clearance exception: if the same physical card clearly says `促銷價`, `展示出清`, `出清`, `展示機`, `福利品`, `清倉`, or `特賣`, a handwritten 4-digit price such as `1999` is valid. Without that context, low plan/monthly/accessory prices remain invalid.

6. Non-Samsung main monitors are not `無型號`.
   - Use `它牌(BRAND)` for the model field.
   - Keep only the brand, not the non-Samsung model code.

7. Current-year distant views require `.22` evidence finalization before upload.
   - For 2026 and future periods, a first-pass `遠景` enters the bounded second/third-call guard; once `.22` verifies the same-image structural result it is complete and uploads immediately.
   - Reason: false distant views can be single monitors or FollowMe, but verified distant photos must not wait behind a whole-year gate.
   - Historical distant views may remain ready unless other review reasons apply.

### Google Drive upload rule

- Upload destination is the user's shared Drive folder. Use year-only child folders (`2026`, `2025`, ...); do not create month folders.
- Run `tools/prepare_drive_upload_manifest.py` against `D:\00_商化\00_已OCR照片` before each upload batch.
- Legacy bulk upload accepts only `ready` rows staged under `_drive_upload\staging`.  The `.22` per-photo stream separately accepts every verified truthful result, including `遠景` and filenames containing `無型號` or `無價格`.
- Upload manifests are newest-period first, so the unattended uploader should send `2026` before `2025` before `2024`.
- After each successful upload, append the exact Drive-returned file name and ID to `_drive_upload\drive_upload_uploaded.csv`, then rerun the manifest. This is the resume guard and prevents duplicate uploads.

### Manual review panel rule

- The old manual-correction drawer is not part of the `.22` operator workflow and must not appear in the dashboard.  Technical failures are shown explicitly and retried after repair.
- Do not place this queue inside the normal boss-facing monitor. It should stay behind the toolbar button so the live OCR view remains clean.
- `記錄` writes `_ocr_audit\manual_corrections.csv`; `學規則` writes `_ocr_audit\manual_learning_rules.csv`; `標記重跑需求` records that another safe candidate CSV must be generated before rerun.
- Treat `manual_corrections.csv` as authoritative human feedback for later repair/export scripts. Do not upload a row that is still only in review unless a follow-up script has rebuilt a safe filename and the manifest marks it `ready`.
- Use `tools/apply_manual_review_corrections.py` to turn recorded manual corrections into a dry-run rename plan. Add `--apply` only after reviewing `_ocr_audit\manual_correction_rename_plan_*.csv`, then rerun the Drive manifest.
- The quick ARK action means Odyssey Ark / Ark Mini LED / 55-inch upright or curved desk displays => `S55BG970NC`; still do not borrow nearby S27/S32 monitor labels.

### Known unresolved defects

- Current-year repaired export dry-run still blocks: 202605 has 79 rows with store price but unknown Samsung/PChome reference.
- Some completed 2026 filenames still contain `？＄`; these must be repaired or moved to manual review.
- Some legacy completed rows are `model + 無價格` although thinking text contains a valid price. Narration rescue is permitted only when the structured `price` field is absent; an explicitly null v19.45 field must stay null and use a focused independent rerun/review instead.
- Some obvious distant views are still classified as `單機/(無型號)/price`, e.g. `M-台南市-永康區-TK3C-中華-362.jpg`.
- Odyssey Ark / Ark Mini LED 55-inch upright or curved desk displays should be `S55BG970NC`; keep the guard that blocks borrowing nearby S27/S32 labels.
- Newer model comparison is not complete. qwen3-vl-8b is active; Gemma 4 12B QAT and Qwen3.5 9B VLM were downloading and not fully evaluated.

### First actions for takeover

1. Check status:

```powershell
Invoke-RestMethod http://127.0.0.1:5000/api/status
```

2. Do not rerun all 5951 completed 2026 OCR files. Use audit CSVs first.

3. Run verification:

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe -m py_compile samsung_ocr_batch_processor.py skills\official_price.py tools\recursive_ocr_flat_export.py tools\repair_current_year_price_compare_outputs.py
.\.venv\Scripts\python.exe tools\test_photo_rename_planner.py
npm.cmd --prefix dashboard run build
.\.venv\Scripts\python.exe tools\repair_current_year_price_compare_outputs.py --output-dir "D:\00_商化\00_已OCR照片" --period-prefix 2026 --dry-run
```

4. If UI changes are not visible, refresh browser and ensure backend is serving the latest `dashboard/dist/assets/index-*.js`.
## 2026-07-03 Portable Handoff Rules

- Start new handoffs from `docs/handoff_2026_ocr_resume.md`.
- Use `samples/ocr_demo_50/photos` and `samples/ocr_demo_50/labels.json` for smoke tests on another PC.
- Do not add production photo folders, generated flat output, audit backups, rerun CSVs, or logs to Git.
- Preferred local model is `qwen/qwen3-vl-8b` through LM Studio at `http://127.0.0.1:1234/v1`.
- Keep boss-facing dashboard language polished. Do not expose internal wording such as playback queue.
- Never enlarge `thumb_b64` in the main preview; show a clean loading/placeholder state until the full image loads.
- Include `source_path` in new OCR records so cross-folder dashboard entries can load full images.

## 2026-07-04 UI And Runner Rules

- Current dashboard build: `v19.29 (AI判讀視覺微調)`.
- Boss-facing sequence must look like: photo appears, AI narration types, the completed narration stays visible until the next narration begins, then the parsed result appears in `辨識紀錄` after AI narration finishes. Do not show unfinished backend queue items in the right rail.
- Backend may process ahead, but the visible filename, preview, AI narration, and lower-left `辨識中` panel must all refer to the same displayed photo.
- If backend logic would change `遠景` to `單機-FollowMe`, do not manufacture a corrected narration. Preserve the raw contradiction for audit, withdraw it from the operator-facing completed state, and retry or mark review-required.
- The lower-left panel must preserve the historical AI judgment record, including `[THINK]` summaries and final classification lines. Filter only raw `JSON Error`, initialization/debug messages, batch start/stop noise, and internal queue wording.
- Do not let `圖片損壞`, `無法識別圖片格式`, stop/interruption messages, or queue maintenance logs fill the lower-left panel. If live logs are only noise, show readable recent `display_queue` summaries instead.
- Never black out, dim, or collapse the main preview between photos. Keep the previous full-resolution photo visible until the next full-resolution image has loaded.
- On a stop/idle transition, clear active live labels and narration state, but do not forcibly blank the visible photo. Showing `上一張畫面保留` is acceptable.
- The right-side column must be readable but not oversized: target roughly 360-430 px on desktop, use wrapped filenames and hover titles for full names, and keep the column on the right side of the desktop monitor layout. This project runs on a GPU workstation, not a phone UI; do not add responsive rules that push the result sidebar below the preview.
- The right-side rerun action text is `再辨識`, not `重跑`.
- If the local VLM repeats a token/spec endlessly, backend must close that stream and retry instead of letting the UI loop forever.
- Large Drive uploads use rclone remote `samsung_ocr_drive` through `tools\rclone_drive_upload.py` or `UPLOAD_READY_PHOTOS_TO_GOOGLE_DRIVE.bat`; keep year-only folders and use the `_drive_upload\rclone_drive_upload.lock` guard.
- If an rclone child stays on one batch without updating `drive_upload_uploaded.csv`, restart the uploader with a smaller batch such as `--limit 100` and keep `--rclone-timeout-seconds` enabled.
- For risky outputs, `tools/rerun_questionable_records.py --input-csv ... --execute` can resume from a filtered candidate list.
- Before queueing any risky rerun, verify the candidate image exists inside the row's real source folder. Do not rerun a filename under a guessed month folder; skip it and regenerate candidates instead.
- If a `missing_result` rerun is already active, do not stop the backend. Start `tools\continue_after_missing_rerun.ps1` once; it waits for the rerun to finish, restarts the backend only when idle, runs recursive flat export in resume mode, audits, then resumes rclone upload.
- A corrupted image or unresolved `missing_result` should be isolated to `blocked_after_rerun.csv` / `blocked_after_recursive.csv`; do not let one bad source image stop the whole folder's safe output.
- `/api/image` should serve original source photos and must not fall back to enlarged thumbnails for boss-facing preview.
- Non-Codex users should use `SETUP_FIRST_TIME.bat`, `START_OCR.bat`, `START_FULL_AUTO_OCR.bat`, and `CHECK_STATUS.bat`. Do not tell ordinary users to type Python commands unless the BAT flow fails.
- The dashboard main toolbar should expose safe continue/resume (`續跑`) for normal production work. Do not expose a global restart button to ordinary users, because `restart=true` purges OCR JSON history in the current source folder before rerunning.
- If a production recursive run stalls with `name 'Path' is not defined`, ensure `skills/batch_orchestrator.py` imports `Path` from `pathlib`, restart the backend process, then resume with the recursive launcher and existing `_ocr_audit` state.
- If `_ocr_audit\folder_summary.csv` suddenly contains only a few rows after a restart, rebuild it with `tools\rebuild_recursive_folder_summary.py --output-dir D:\00_商化\00_已OCR照片`, then restart `tools\recursive_ocr_flat_export.py` in normal resume mode. Never use `--no-resume` to recover this.
- Unattended production PCs should install the scheduled task via `INSTALL_WATCHDOG_TASK.bat`. It creates `SamsungOCR_PipelineWatchdog`, which runs `tools\ocr_upload_watchdog.ps1` every 4 hours to resume missing OCR/upload helpers without clearing history.
- Recursive traversal must treat the source root as live. Refresh discovery between folders and run watchdog-started recursive OCR with `--watch`; do not rely on a one-time folder list captured at startup.
- The watchdog must check real progress, not just process existence. If OCR `processed/ready/current file` remains unchanged beyond the stall window, restart only the OCR backend and recursive runner and resume from audit state; keep rclone upload alive.

## 2026-07-08 current-year priority gate

- Current/future years are not complete just because first-pass OCR copied files. If Drive review still has current-year rows, older folders must wait.
- Older-year continuation has no boolean bypass. The supervisor and recursive runner must share a canonical content-bound `historical_continuation_receipt.json`; it requires the root-bound explicit request, current guard revision, exact 2026 pending-zero marker/proof, current-year review zero, no fuse/benchmark lock, and idle canonical backend.
- Before historical OCR, freeze `source_inventory_v1.csv/json` with stable folder IDs and per-photo relative path, size, `mtime_ns`, and content SHA-256. Bind it into the receipt; verify the next folder locally and the entire tree at completion. Never silently refresh on rename/add/replace/drift.
- Resume is complete only when photo/success/copy counts are equal, all errors are zero, and every copied target is byte-identical to its current source. Do not trust count + max-mtime alone.
- `tools/recursive_ocr_flat_export.py` exits at a safe folder boundary with `paused_reason=current_year_review_gate` before starting older folders when current-year review rows exist.
- `tools/auto_rerun_questionable_after_recursive.ps1` runs current-year questionable reruns first, without `--include-older`, then runs all-year questionable passes.
- For 2026, distant-view rows are risky because many are false positives. Treat `遠景`, no model, no price, unknown compare symbol, bad/unclear photo, and price-compare failures as blocked until rerun/repair/manual correction clears the Drive manifest.
- When a slow fallback vision model is needed, do not run it over the entire source folder for a small risky subset. Use `tools\rerun_staged_candidates.py`: it stages only filtered source photos, processes that staging folder, merges records back into the original audit folder, and rebuilds flat outputs.
- For the 2026 distant-view false-positive recovery, regenerate source candidates from audit with `tools\rerun_questionable_records.py`, then staged-rerun only rows whose `reason` contains `遠景`. Never use the Drive review CSV paths directly as rerun source paths; those point at flat output photos.
- The watchdog must recognize `rerun_staged_candidates.py` as active OCR work. During staged rerun it can keep Drive upload alive, but it must not start recursive OCR, auto questionable rerun, or OCR stall recovery.
- Prefer an auxiliary backend on port 5001 for staged reruns while the visible dashboard remains on port 5000. Launch it with `SAMSUNG_OCR_NO_BROWSER=1` and `--port 5001`, then point staged rerun tools at `http://127.0.0.1:5001`. This prevents the formal monitor from showing temporary `_ocr_staging` folders.
- Staged rerun results must be discarded, not merged, if logs show context-length errors (`n_keep >= n_ctx`, `context length`, or `number of tokens to keep`) or if a large batch collapses into suspicious `單機 / 無型號 / 無價格` rows. qwen3.5 9B failed this way on 2026-07-08 at 8K context and is not a safe replacement until retested at 16K+ context.
- Model fallback status: keep `qwen/qwen3-vl-8b` as the main production model. qwen3.5 9B VLM and Gemma 4 12B QAT may be used only as guarded last-pass candidates after loading with sufficient context and passing a staged sample. MiniCPM-V-4.6 was not usable through the current LM Studio OpenAI-compatible image request path. When comparing logs, remember that `tools/qwen_vl_regression.py` previously mis-normalized `FollowMe Pro M7 43"` as `FollowMe M7 32"`; logs made before that fix may underrate alternative models.
- FollowMe false-distant guard is mandatory. A foreground white floor circular base, vertical pole/stand, upright white frame, tray, or FollowMe Pro 4K / FollowMe 4K product card means the image must stay a FollowMe/single-unit candidate even if the background contains many TV/QLED/OLED displays. Do not merge staged rerun output that converts those candidates to `遠景 / 無型號`.
- FollowMe model names must be standardized on the write path too. `skills/batch_orchestrator.py` normalizes `FOLLOWME...` variants before frontend state, CSV, and Label-Studio JSON export. This prevents a rerun from judging FollowMe correctly but saving an inconsistent model string for rename/export.
- If taking over on 2026-07-08 or later, first check whether `tools\rerun_staged_candidates.py` is still running for the 2026 priority rerun on backend `http://127.0.0.1:5001`. Logs are `logs\priority_2026_all_qwen3_aux5001_20260708_210327.*.log`; summary is `_ocr_audit\priority_2026_all_qwen3_aux5001_summary_20260708_210327.csv`. Do not start older-folder recursive OCR until this current-year rerun finishes or safely aborts.

## 2026-07-08 FollowMe Staged Rerun Patch

- Staged rerun may rescue obvious foreground FollowMe false-distant outputs to `單機` plus a conservative FollowMe model before merge. Missing price still remains blocked by current-year upload guards, so this prevents false `遠景` upload without treating incomplete rows as finished.
- `_ocr_staging` folders must be removed on abort or staging-copy failure to avoid filling `D:`.
- After rerun, do a visual spot-check of remaining current-year distant risks. On 2026-07-09, `_ocr_audit\distant_followme_risk_2026_latest_visual_spotcheck.csv` still showed 6 likely single and 1 unclear among 10 risks, so "completed rerun" was not enough to approve upload.

## 2026-07-08 FollowMe With Nearby Non-Samsung Products

- Do not let nearby LG/appliance/cashier/phone-counter content suppress a visible Samsung FollowMe unit. If the evidence contains Samsung FollowMe plus a standing display, white stand/base/tray, or similar FollowMe structure, treat it as a FollowMe/single-unit candidate even when other brands are also visible.
- Do still block true LG/StanbyME/MyView cases when Samsung FollowMe is negated or only mentioned as absent.
- FollowMe rescue must not require the classic white circular base. If `Samsung FollowMe` / `FollowMe` is on a visible standing or vertical display/product label, keep it as FollowMe review evidence even when the model says the white base is absent. Reject only pure posters/ads that are not tied to a visible standing display/product.
- v19.34 correction: when the same narration says a visible standing/vertical display has a `Samsung FollowMe` / `FollowMe` label, that positive display-sign evidence overrides negative wording about missing classic white stand/base. Do not clear the FollowMe model, classify it as `遠景`, or show contradictory narration solely because `白色圓形底座` is not visible.
- User-confirmed sample `M-台中市-南屯區-TK3C-台中嶺東-697.jpg` must resolve to a `單機-FollowMe...` output, not `遠景`. If price is not readable it must stay blocked from current-year upload as `無價格`.
- If post-processing rescues a row to FollowMe, the contradictory narration must be rejected by evidence/runtime health rather than rewritten. Never show the contradictory raw text as a completed card, but keep it in the trace for diagnosis.

## 2026-07-08 Distant-View Quality Audit

When current-year distant-view records are rerun, accuracy must be audited, not only process health. Use `tools\audit_distant_followme_risk.py --output-dir "D:\00_商化\00_已OCR照片" --year 2026 --include-medium --sample-csv "D:\00_商化\00_已OCR照片\_ocr_audit\distant_followme_risk_2026_latest_sample.csv"` to produce `_ocr_audit\distant_followme_risk_2026_latest.csv/json` plus a deterministic sample CSV for visual spot checks.

The audit catches records saved as `遠景` even though evidence still contains FollowMe, Samsung Follow, S32FM/S43FM, white stand/base, vertical pole, tray, side-label/model clues, or single-unit wording such as `主角是`, `一台`, `單台`, or `判斷是單機`. It also catches `critical_followme_result_conflict`: final output/filename is FollowMe but the saved narration still says `不是 FollowMe`, `沒有 FollowMe`, or `整體符合「遠景」條件`. Corrected wording such as `不能判為遠景` is not a conflict. Baseline on 2026-07-09 after exposing this missing class: 303 current-year risk rows, including 275 FollowMe result/narration conflicts. After focused 2026-07-09 reruns, the current-year FollowMe/distant risk count fell to 9 rows; those remain blocked until rerun/repair/manual justification clears them.

The sample CSV is not a rerun list. It includes high-risk rows and a deterministic sample of apparently true distant rows so another AI can estimate whether distant precision is improving after reruns. If a user-confirmed FollowMe or single foreground monitor appears in this sample, expand the risk rules before allowing 2026 uploads.

`tools\prepare_drive_upload_manifest.py` reads `_ocr_audit\distant_followme_risk_*_latest.csv`; any listed output file is review-required with `current_year_followme_or_distant_risk_needs_rerun` and must not be uploaded until rerun/repair clears it.

## 2026-07-09 Pause Handoff Skill Note

- If taking over after the pause, read `docs\handoff_20260709_pause.md` first.
- The project is intentionally stopped. Do not assume a missing OCR process is a crash.
- Resume 2026 before older years. v19.36 pass3 completed `202605`; continue `202604`, `202603`, `202602`, then `202601` from `_ocr_audit\current_year_distant_and_risk_v1936_pass3_selected_20260709_1605.csv`.
- Current-year `遠景` is not automatically trusted. Re-audit with `tools\audit_distant_followme_risk.py`, rebuild upload manifest, and upload only ready rows.
- `tools\rerun_staged_candidates.py` is disk-safe by default and should not create huge flat-output backups unless `--keep-flat-output-backup` is explicitly requested.
- Never upload a technical-integrity failure.  Ordinary three-pass content uncertainty is finalized conservatively by `.22`, not converted into a permanent `review_required` row.

## 2026-07-08 FollowMe Risk Rerun Waiter

- `tools\run_followme_risk_rerun_after_current.ps1` waits for the current staged rerun to finish, refreshes `distant_followme_risk_2026_latest.csv/json`, restarts only backend port 5001 so the latest FollowMe rules are loaded, and then staged-reruns just those risk rows.
- It does not interrupt an active staged rerun and does not touch the visible dashboard on port 5000.
- Current live waiter was started on 2026-07-08 around 23:38 with output logs `logs\followme_risk_waiter_*.log` and main script log `logs\followme_risk_after_current_*.log`.

## 2026-07-09 Current-Year Distant Escalation

Benchmark interlock: `tools/model_benchmark_sidecar.py` and the four-hour watcher use `00_已OCR照片\_ocr_audit\model_benchmark.lock`. The watcher may remain alive but must wait while this lock exists; it must not launch backend, staged/recursive runner, or uploader. Stale locks are never auto-deleted: recovery requires explicit recovery mode, proof that the owner PID is absent, and an age threshold. This does not stop normal OCR or require closing the UI.

Unattended continuity uses `tools\ocr_continuity_supervisor.ps1` through the existing `SamsungOCR_PipelineWatchdog` task (startup, logon, and five-minute repetition). Its atomic lock and RepoRoot process matching are authoritative: healthy work is a no-op; hung backend, unavailable LM Studio with a different loaded model, or ambiguous staging is alert-only and fail-closed. It preserves `_ocr_audit`/staging/history, never uses restart/no-resume, and starts uploads only from ready-pending rows.
If protected Task Scheduler registration is denied, install the non-admin fallback with `tools\install_ocr_continuity_daemon.ps1 -Action install`. It uses one hidden HKCU Run/Startup daemon, one atomic lock, immediate plus five-minute checks, bounded child execution, and a shutdown marker. Never install a second daemon or disable the existing four-hour backstop.
The same installer creates `SamsungOCR_UserContinuityEnsure` as a current-user LIMITED five-minute `schtasks.exe` task. Its `ensure` action only starts the exact RepoRoot daemon when absent and refuses stale-lock recovery without proof; it must not create duplicate PowerShell trees.
Drive corrections use `tools\reconcile_drive_corrections.py` in dry-run first. Stale uploaded rows remain frozen until corrected output has fresh gates, exact local identity/hash, verified new remote receipt/readback, and an explicit recoverable-trash receipt for the uniquely identified old Drive file. Never delete ambiguous IDs, duplicate names, or rows lacking hashes.
The pre-v19.45 correction ledger is not authoritative because it contains stale gates and mojibake paths. After evidence backfill and manifest regeneration, rebuild it with `tools\build_drive_correction_reconciliation.py`; execution is fail-closed unless every stale row has one current `copied.csv` source and manifest row. Use `--phase discover-old` only for unique read-only Drive ID discovery. Same-path content must become `unchanged_remote_verified` and must never enter the trash phase.

- Current-year distant-view precision is not trusted. A visual sample after rerun still showed many likely single/FollowMe foreground products, so 2026 distant-view rows are blocked by default.
- Upload gate: current/future-year distant-view output must not go to Drive unless it is explicitly approved in `_ocr_audit\current_year_distant_upload_approval.csv` or corrected to a concrete `單機`, `FollowMe`, or `它牌(...)` result. Visual spot-check files are only for measuring rule quality and must not be treated as upload approval.
- `tools\prepare_drive_upload_manifest.py` writes `_drive_upload\drive_upload_stale_uploaded_review_required.csv` for current-year files that were uploaded before stricter gates but are now review-required. Treat those as stale remote deliverables to remove or replace after rerun.
- `tools\cleanup_stale_drive_review_uploads.py` can remove those stale current-year Drive files. It dry-runs by default; `--execute` also removes matching rows from `drive_upload_uploaded.csv` so corrected or accepted files are not accidentally skipped later.
- Active 2026 repair flow: scan with `tools\rerun_questionable_records.py`, then run `tools\rerun_staged_candidates.py --reason-contains 遠景` against the resulting current-year candidate CSV, then refresh risk audit and upload manifests.
- `tools\rerun_staged_candidates.py` must restore the backend work directory to the original source folder before deleting `_ocr_staging`; otherwise the dashboard can keep polling a deleted staging path and repeatedly show "Failed to list actual files".
- Backend/UI rule: `build_final_display_thinking()` preserves image-grounded model narration and may only provide a bounded missing-text fallback. It must never generate `最終校正` prose. Contradictory text is retained in audit evidence while runtime health supplies the withdrawn-message to the visible completed state.
- Live 2026 distant rerun started 2026-07-09: log `logs\current_year_distant_staged_rerun_20260709_103552.out.log`, candidates `_ocr_audit\current_year_distant_staged_rerun_candidates_20260709_103552.csv`, summary `_ocr_audit\current_year_distant_staged_rerun_summary_20260709_103552.csv`.
## 本機模型評測安全規範

模型比較只能使用 `tools/model_benchmark_sidecar.py` 的 bounded sidecar。它必須以 fixed `ocr_demo_50` blind set、同一 production prompt 與同一影像證據執行；不能把 benchmark 當成訓練或修改 OCR 權重。dry-run 不會碰 LM Studio，真正執行必須明確 `--execute`。

sidecar 的硬守門是：API `is_running=false`、沒有任何 rerun/recursive/watcher/uploader、所有指定模型已完整下載、endpoint 為 localhost、raw JSONL 可重入保存。任何模型切換後都必須在 finally 還原 `qwen/qwen3-vl-8b` 與 context；不得停止或重啟正常 OCR，也不得上傳照片。
Windows 程序清單必須由 UTF-8 JSON 安全解析，列舉失敗或空白解析錯誤一律拒絕 benchmark；不能把錯誤當作「沒有 runner」。取得 lock 前後都要重查 API 與程序，以關閉競態。FollowMe 被判成中文 `遠景` 或英文 `distant_view` 都屬危險誤判。
Benchmark raw JSONL 必須用 `candidate_model` 保存候選 VLM，`model` 只代表預測出的 Samsung 產品型號。評分前依候選隔離；缺少、重複、未知、混模、解析錯誤或 inference error 都留在固定 50 張分母並令 `benchmark_gate_pass=false`。未通過 protocol gate 不得比較速度或升級主線。
Benchmark manifest 必須是 v2：它固定 labels、每張原圖、case ID/tag/expected 的 SHA-256 契約。sidecar 必須為每個 case 只準備一次全圖與 deterministic crops，將 production prompt 與解碼後 evidence 組成 `input_fingerprint`，並寫入每筆 raw row。續跑時任一 manifest/prompt/image/crop 指紋不符、指紋缺失、重複 candidate/case 或 candidate/key 不一致都要拒絕，不可將不同輸入混在同一 output directory。已完成的候選不得再觸發 unload/load，最後必須恢復執行前捕捉的 baseline context。

採用候選模型前，先要求整體 exact/field accuracy 改善或至少不退步；遠景、FollowMe、型號幻覺等危險錯誤率全部不得退步。latency 只作 accuracy 通過後的次順位，不得用速度掩蓋辨識退化。InternVL 只有在本機下載完成並通過完整性檢查後才列入執行候選。
## v19.45 Evidence Contract

The machine-readable evidence contract is authoritative for acceptance: screen count, unique main subject, label ownership, and same-subject FollowMe physical evidence. Prose keywords never rescue a result. Missing content fields remain empty; usable cross-pass evidence is finalized by `.22`. Technical contract or binding failures retry only inside the three-call cap and never enter the stream outbox.

> 型號身分一律以本文件最上方 `.36` 為準；下方 `.15`–`.22` 是事故演進紀錄。舊版曾把 43 吋、S43FM 或 17,990 當作 Pro 輔助證據，該做法已廢止，不得重新實作。

The contract version and guard implementation identity are separate authorities. Every new result and trace produced by the complete three-layer rules must carry `evidence_guard_revision=20260716.22`. A legacy `v19.45 verified` trace without that revision is unverified under the current rules and must be emitted by the backfill builder. Revision `.15` includes `.5` stateless pass 2/3 and runtime health fuse, `.6` discontinued/unlisted SKU photo consensus, `.8` short-SKU completion, `.9` complete distant evidence with semantic cross-pass comparison, `.10` narration support without integer duplication, `.11` explicit FollowMe negation handling, `.12` common-schema evidence fields, `.13` sub-three distant rejection, `.14` pre-rescue structured authority, and `.15` material structured-authority/runtime-health checks plus narration/physical-evidence consistency that cannot be washed by later passes. Revision `.16` binds the current request and full image and blocks adjacent cross-photo core repetition. Revision `.17` requires same-photo Pro/43/S43FM/17,990 evidence before Pro 43 can be accepted, prevents generic signage language from erasing an explicit FollowMe model backed by sufficient same-pass structured fixtures, and requires every 2026 FollowMe pass to agree on model and price. Revision `.18` recognizes only the established same-variant friendly-name/physical-SKU aliases and narrows the narration fuse to non-negated FollowMe identity or unmistakable white mobile-stand fixtures; a black short stand plus tray explicitly described as non-FollowMe is not a batch-stopping conflict. Revision `.19` forces three independent identical passes for any current-year single-unit candidate reporting three or more complete screens and binds human-audited risk pixels to the expected view by full-image SHA-256; an expectation conflict is an immediate durable-fuse condition. An explicitly present `model` or `price` field, including null, may never be refilled from narration; prose rescue is legacy-only when that field is absent. A dominant foreground portrait display with a same-subject white round base and attached tray remains a FollowMe single-unit candidate even with three or more background televisions. Large scenes receive a central full-height tile in pass 1 and left/center/right tiles on retries. Screen advertising cannot negate physical fixtures. Narration that exposes two or more positive fixture cues while structured evidence is still insufficient invalidates the pass; a single-unit record already carrying direct branding or two independent same-subject strong cues does not fuse merely for one omitted orientation/card detail, while distant answers never receive this allowance. Prompt/rule echo, overlong narration, and `最終校正` correction prose are runtime-health failures; the backend preserves raw narration and never rewrites it to conceal conflict. The durable fuse stores a bounded record snapshot. Only a contained first-pass FollowMe/view conflict gets one stateless retry; a repeat on pass 2 or a model/price/prompt/UI fault stops immediately. Any material `structured_authority_blocked_fields` value in `view_type`, `model`, or `price` invalidates the pass. Cross-pass disagreement is resolved only by the bounded `.22` evidence rules; an unsupported model/price stays empty rather than being guessed. Missing request binding, prior-answer exposure, prompt contamination, cross-photo drift, or invalid evidence keeps upload closed, and no case may trigger a fourth model call. The `.16` 15-photo smoke is a failed semantic trial, not production proof.

The runtime health fuse is durable, not an in-memory stop flag. A trip atomically writes `_ocr_audit/runtime_health_fuse.json`. The batch start API, continuity supervisor, upload watchdog, manifest gate, shared upload proof builder, and uploader all fail closed while that marker exists. A genuine systemic incident is cleared only after the cause is fixed and the critical regressions plus a bounded live proof pass. A narrowly proven photo-local attempt-1/2 binding fault may be archived by the deterministic recovery tool only when its invalid payload is discarded, the consumed call count is preserved, the same source is requeued, and the rolling-window systemic threshold is not met.

Continuity launches are executable work, not dry-run discovery. The supervisor
must pass `--execute` whenever it invokes `rerun_staged_candidates.py`, including
with `--resume-existing-then-continue`, and must wait through the asynchronous
accepted-to-running API gap before attaching. If a photo has consumed all three
calls but only two outputs survived a process boundary, never issue call four:
only an exact source-item/source-SHA/input-SHA pixel authority plus at least one
clean bound output may close it through the deterministic consumed-cap recovery,
which enqueues before committing the terminal result.

The three-call cap is source-level and survives staging/run boundaries. A
current-revision source that already ended at attempt three with review required
must never be copied into a fresh staging directory for another three calls.
Rebuild its saved trace and run deterministic zero-model closure; if proof is
still insufficient, retain one durable repair item while unrelated sources
continue, rather than creating a recurring inference candidate.

While the fuse remains active, `/api/start_batch` has exactly one constrained diagnostic exception: an explicit `runtime_health_trial=true` request whose folder is under `_ocr_staging`, contains `runtime_health_smoke` in its relative path, contains 1-15 images, has no success/failure session JSON, and is protected by `model_benchmark.lock`. This exception cannot resume production or open upload. A new incident archives the previous fuse before atomically refreshing the active marker. After the bounded smoke passes and its trace/UI evidence is audited, archive and manually remove the active fuse before normal continuation.

Monitoring means progress plus content quality plus presentation health plus upload isolation. A counter that advances while answers are contaminated is a failure, not progress. The recurring monitor must audit all four dimensions and must not blindly resume a genuine runtime-health incident. Once a deterministic repair has archived a narrowly proven local fault and regressions pass, the continuity controller must resume the saved checkpoint automatically; no manual button, browser reload, new tab, or Dashboard outage is allowed.

Every change starts and ends with a manual checkpoint against `docs/development_guide.md`, this skill, and `docs/continuity_handoff.md`. Previously fixed presentation and evidence failures must have permanent regressions. In particular, an idle/restarted compact-status response must keep the history-recovered `presentation_sequence`; an empty live queue is not authority to display zero.

An empty `current_run_id` is also not authority to load legacy right-rail history. Idle recovery may return only the newest nonempty durable run inside the selected source-ID scope and must return that exact recovered run ID; otherwise it returns no cards. The frontend clears any blank-run recovery response. Stale guard-revision cards remain hidden from accepted fields and, when intentionally shown for audit, are labeled `等待新版複核` rather than pretending to be current manual-review work.

The four-hour upload watchdog must fail closed at its main entry whenever `_ocr_audit/model_benchmark.lock` exists. It may not repair summaries, restart the backend, launch recursive/questionable work, rebuild upload proof, or start an uploader while the planned backend/current-revision backfill interlock is active.

At every repair/resume boundary, reread `docs/development_guide.md` and `docs/continuity_handoff.md`, then compare a live sample's raw structured JSON with its final parsed fields. Progress-only monitoring is insufficient. A material `structured_authority_blocked_fields` entry (`view_type`, `model`, or `price`), raw/final material mismatch, memory exposure, prompt contamination, UI identity mismatch, raw JSON/garbling, duplicate browser tab, uploader activity, or runtime fuse is a stop-and-investigate signal. Equivalent category normalization such as `一般單機→單機` is not material; older category-only trace flags must be evaluated by normalized scene meaning.

When proving that the dashboard has only one Chrome tab, inspect the user's actual open-tab inventory. The browser controller's bound-tab list is not authoritative after cleanup/finalization and may be empty while duplicate user tabs still exist. Reuse and verify the newest healthy `localhost:5000` tab, close only confirmed duplicate dashboard tabs, and never open a new tab/window for monitoring.

Anti-bypass invariants are part of that contract:

- Structured fields from the current independent pass are authoritative over narration heuristics. Narration may expose a contradiction and force retry/review, but it must never change an explicit `遠景`/`單機`, refill an explicitly null model/price, or replace one non-empty SKU/price with a materially different one. Material replacements are cleared and recorded in `structured_authority_blocked_fields`; only cosmetic case, punctuation, and currency formatting normalization is allowed.
- `view_type` and `category` may not assert different scene types. A structured single-unit result whose narration explicitly concludes `遠景`, or `label_ownership=matched` whose narration assigns the label to a neighboring product, must retry/fail closed.
- FollowMe friendly names and physical SKUs are equivalent for gating. `S32FM50x`, `S32FM70x`, and `S43FM70x` families require same-subject physical evidence and a current-year second pass; ordinary `S32FM80x/S32FM90x` Smart Monitor SKUs do not.
- Screen content is not hardware brand evidence. Text such as `螢幕顯示 ASUS Demo 畫面` must never replace a valid Samsung SKU with `它牌(ASUS)`.
- A photographed price at least 20% away from the official reference requires one independent reread. If a later pass independently confirms the same model, same photographed price, and matched label ownership, preserve the photographed store price; the external reference is not allowed to overwrite it.
- Dashboard `完成判讀` means `.22` has produced a truthful finalized result.  The stream outbox still requires `auto_verified=true` and `auto_review_required=false`; technical failures remain visibly unuploaded.

## v19.45 Three-Layer Accuracy Gate

`docs/three_layer_accuracy_gate.md` is the authoritative design and verification specification. The mechanism is conditional escalation, not three-pass voting:

- Pass 1 establishes the baseline structured evidence. A complete ordinary single-unit photo may be accepted immediately.
- Pass 2 is used only when pass 1 is incomplete or risky and receives no prior answer, summary, reason, or conversation history. A complete current-year FollowMe single may finish on pass 1 only when the owned model and store price are clear and the same physical unit carries direct branding or sufficient strong fixture evidence; a poster or screen content alone is never physical proof.
- Pass 3 receives no prior answer in model messages, uses the stronger lower-center crop, and produces an independent observation before the guard compares all passes. A newer answer cannot overwrite a prior unresolved core conflict. It is also the absolute final model-call boundary.
- Current-year distant view requires bounded same-image structural evidence: ordinarily the conditional second/third pass, while two independently bound zero-screen or structural distant results may settle `遠景／無型號／無價格`. No result may trigger a fourth call.
- `verified`, `retry`, and technical failure are guard decisions, not model opinions. Intermediate guesses never enter formal output; only the `.22` finalized result enters the stream outbox.

Any change to `build_ocr_messages()`, `immediate_retry_decision()`, the retry queue, v19.45 trace, presentation history, or upload manifest must preserve the validation matrix in the authoritative document and run `tools/test_v1945_evidence_contract.py`, `tools/test_immediate_retry_queue.py`, and `tools/run_critical_regressions.py`.

## Current-Year Upload Finalization Contract

The legacy current-year **bulk** upload lane is globally closed until the whole authoritative source inventory is finalized. This does not block `.22` streaming upload: every individually verified photo is queued and uploaded immediately after its own exact gate succeeds.

- The finalization proof binds the candidate-builder summary, exact candidate/result sets, every folder run summary, canonical success/rename/copied authorities, unique source identities, existing outputs, and zero remaining unverified v19.45 sources.
- Contract version alone is insufficient: current-year completion requires the current `evidence_guard_revision`. The boundary retains its interlock until a fresh builder proves zero remaining candidates and verified source count equals the authoritative inventory; the continuity supervisor independently self-heals any later idle gap by rebuilding and resuming remaining revision candidates.
- `tools/build_upload_gate_proof.py` is the shared exact-content authority. Manifest/review-split failure, stale audit, hash/count mismatch, or blocked pending rows closes and removes the proof. No intermediate review phase may start an uploader. Current-year completion requires a successful uploader exit, regenerated proof, and pending count zero before historical continuation.
- Recursive all-year completion is not a process-exit guess. Missing, changed, `error`, or `blocked` folders force a nonzero runner exit. The full marker must bind the current discovery and folder-summary hashes with equal discovered/completed counts and zero errors; the supervisor rejects stale markers and resumes.
- Zero candidates are acceptable only when the inventory is non-empty and every source is already verified; otherwise fail closed.
- The risk audit writes an `audit_input_sha256`. The manifest must recompute it, block every current-year row on drift, and bind the exact next-batch CSV SHA-256.
- Explicit distant approval is valid only for the same backfill run and audit input, and must bind source identity, target content SHA-256, and approval time.
- `rclone_drive_upload.py` revalidates the rebuilt batch before staging or rclone. The watchdog order is audit → proof → manifest → hash gate → uploader; the supervisor requires a fresh content-bound gate receipt.
- Drive correction ledger integrity does not imply replacement authority. Gate readiness, old-ID discovery, new upload readback, and recoverable old-file trash receipt are separate phases. `upload-new` requires `status=new_ready`; ambiguous or duplicate identities never proceed.

Required regression coverage is `tools/test_current_year_upload_finalization.py`, `test_rclone_upload_safety_unit.py`, watchdog/supervisor tests, Drive correction builder/reconciler tests, and `tools/run_critical_regressions.py`.

## Fail-Closed Direct Upload Rule (2026-07-16)

- Treat direct `rclone_drive_upload.py --execute` as untrusted entry. It must use canonical `_drive_upload`, canonical `drive_upload_uploaded.csv`, approved remote `samsung_ocr_drive`, canonical backend `http://127.0.0.1:5000`, a fresh shared proof, explicit idle backend, no owned OCR runner, and clear runtime/benchmark interlocks before preparation, after proof work, and immediately before each yearly copy.
- Current-year upload uses `--years 2026`. Historical rows require a separate `historical_upload_authorization.json` bound to the current-year zero-pending marker and the current all-year discovery/summary hashes and counts after questionable review. A fresh shared proof alone is insufficient historical authority.
- Exact next-batch binding compares all trusted manifest fields, including `content_sha256`, not only filename/source identity. The staging map carries the same SHA-256, and staged bytes are rehashed immediately before copy. Nonempty pending with an empty batch, duplicate identity, missing hash, year/scope substitution, status/reason substitution, content substitution, or count/hash drift closes the proof. Atomic proof write is followed by a complete authority readback; mid-build risk/audit/manifest drift deletes the proof.
- A Drive receipt requires one exact-name remote object with the same byte size and MD5 as the SHA-256-authorized staged file. `--ignore-existing` never turns a same-name mismatch into success. Readback failure, duplicate name, missing hash, mismatch, or timeout leaves the row pending and cannot exit as successful completion. A retryable timeout returns success only when another repeat cycle is guaranteed; non-repeat or final max-cycle timeout returns 124.
- Every receipt mutation invalidates the old proof and rebuilds the canonical manifest. Watchdog proof writers must recheck the runtime fuse and benchmark lock immediately before atomic proof write.

## Presentation Synchronization Iron Rule

`presentation_id` and `presentation_sequence` are the only UI identity truth. Photo, AI live interpretation, active placeholder, revealed card, and inspection modal must render from the same immutable snapshot. Running presentation state must not use filename/index/source-path joins or `current_file`, `stream_file`, or `recent_results` fallbacks. The right card appears only after the same snapshot's narration finishes. Active items are never dropped by watchdog or backpressure; a previous image remains visible until the next image is ready, so continuity never produces a black frame. Every dashboard presentation change requires the 500-item duplicate/out-of-order/overflow/remount soak and a fresh build.

## Content-drift containment rule (2026-07-16)

- Monitoring is not progress polling. Sample the actual structured evidence, readable narration, final guard decision, prior-answer exposure, prompt contamination, UI identity, process uniqueness, and upload isolation.
- An uncontained structured/narration contradiction is a batch-stopping defect.
- Legacy contradiction rows remain stale and must be reprocessed from the pristine image under `.22`; they are never carried forward as current verified results.  The new finalizer converts usable content evidence to a truthful result while preserving technical failures as fail-closed.
- The `.11` reference smoke must remain exactly four verified true distant views and three unresolved counterexamples, with zero prior-answer exposure and zero prompt contamination across all 21 passes.
- The `.12` single-unit prompt smoke must keep all four evidence fields in every raw JSON response for photos 665/666/667. Expected model passes are 2/1/2 respectively; a third pass caused by omitted schema fields is a stop-worthy prompt regression.
- Before and after every operational or code change, re-read the current development-guide checkpoint and latest continuity handoff, then verify content, UI, unique hidden processes, and upload isolation. Old regressions are permanent tests.
- Revision `.16` binds every response to the current call with a full 128-bit exact `request_id` echo and traces the full-image payload SHA-256. The final health gate independently requires `request_id_verified=true` and a valid 64-hex image fingerprint. A missing or mismatched ID invalidates that call completely: it consumes one of the same photo's three slots, contributes no business evidence, and may continue only with a pristine stateless call for that photo. One affected source is contained locally; the same binding fault appearing on a different source is systemic and trips the durable batch fuse. If two adjacent distinct source identities return the exact same model and price, even when the earlier photo is unresolved, the later photo must consume no more than three stateless calls and then receive a bounded `.22` result or one terminal technical result; it never enters an unspecified human/slow-model queue. Repeating the same answer twice or three times never clears this suspicion. This detector remains outside the prompt so it cannot create the memory contamination it is designed to catch.

## Frame-edge and wide-scene regression rule (`20260716.28`)

- Count a monitor only when all four physical outer bezel sides and all four corners are inside the first original image. Any monitor cut by the original left/right/top/bottom edge contributes zero.
- For the canonical three-panel close-up, left bezel out of the left edge + complete center + right bezel out of the right edge equals one complete monitor, never three.
- A broad row/display-wall answer that admits multiple complete screens but has no bound SKU, price, matched label, or same-subject FollowMe fixtures is not usable single-unit evidence. It cannot win the generic two-single finalizer lane.
- Keep pixel-bound human regression authorities for `台中旗艦-940=單機` and `中清-1528=遠景`. They supplement the general prompt/guard and must never be replaced by filename-only logic.
- A pixel-authority conflict may consume only the remaining independent passes of that same photo. It must not stop unrelated photos, exceed three model calls, or enter upload; the third pass must settle truthfully or fail that photo closed.
- When this class regresses, stop the formal OCR loop but keep backend/dashboard/uploader/tab continuity. Resume only after offline regressions and a new isolated live smoke prove both directions plus FollowMe preservation.
- Permanent live acceptance run `20260716_221131_225238` proves five photos at revision `.28`, each in exactly three stateless passes with no prior-answer exposure or prompt contamination. Required outcomes are `940=單機/count 1/S32FM803UC/12900`, `1528=遠景`, `1385=遠景`, `939=FollowMe Pro M7 43\"/17990`, and `646=單機/S27D300GAC/3090`.

## Revision `.29` operational invariants

- Never use narration length as an instruction-echo fuse. Match actual instruction/template echo only.
- The server owns `evidence_guard_revision`; the UI never hard-codes it. A durable fuse is shown as repair-in-progress, not idle, and public status never exposes raw evidence.
- Idle staging progress reads the cached `.ocr_source_map.json.items` denominator; do not substitute processed count and create `N/N` false completion.
- Pixel-bound human adjudication requires exactly three stateless, request-bound calls and a known full-image SHA-256. It may finalize the audited fields on call 3 but never create call 4. The `.29` permanent set is 649/668/673/674; 668 explicitly has no FollowMe fixture evidence.
- Three bound wide-scene calls with counts >=3 and no model/price finish as distant when at least one call explicitly says distant. An empty matched-label claim is not identity.
- FollowMe: no off-frame base, no shelf rail as tray, no Smart Monitor family name as direct branding, and no white-pole-only proof.
- When stream-upload pending is nonzero, verify the worker PID exists and receipts advance. Restart only that worker hidden when its status PID is stale; do not restart OCR or open a terminal/browser.
- `restart=true` must delete/ignore the prior durable retry state and reset attempt/history/incident maps before the first call. A displayed pass number is never proof; audit the trace call sequence as `1,2,3`.

## Revision `.33` local-content completion addendum (2026-07-17)

- A sole structured model-authority omission on a request-bound, uncontaminated `單機` vote with a unique main subject, owned label and 1–3 complete screens is photo-local content uncertainty. Preserve calls 1/2 as business evidence and finish on call 3; do not stop unrelated photos. A missing price is an allowed truthful field, not a fuse.
- A known-pixel expectation plus that same local model omission remains one photo-local event only when the exact input SHA-256 is in `KNOWN_SOURCE_EXPECTATIONS`. Any prompt, prior-answer, cross-photo, binding, price, or extra runtime reason remains batch-stopping.
- The three-pass finalizer may use those local votes only for view structure. It never refills an explicit null model from narration. Wide 3+ row/wall evidence may finish distant; supported single identity fields require safe repeated evidence.
- Process-boundary recovery may join only the latest exact-source/exact-image `1+3` or `2+3` trace tail with durable proof that three total calls were consumed and a matching pixel authority. Trial traces without `YYYYMM` are never upload candidates. No fourth call is permitted.
- Repair tools enqueue first and write the verified result second. A dead stale upload-lock PID is archived, then only the hidden uploader resumes. Acceptance requires canonical receipts to advance while OCR continues.
- Dashboard responsive wrapping may affect header/status only. It must keep the established half-screen preview/narration layout and accumulated right rail, while making total progress, folder, file, status and upload counters visible in the existing tab.

## Revision `.34` drift-monitoring addendum (2026-07-17)

- A live monitor must sample content, not only counters: compare the latest source pixels, all stateless pass values, final adjudication, and the actual queued/uploaded filename. Pause only the OCR runner at a safe boundary when a new wrong-result pattern appears; keep the dashboard service online.
- A final zoom price may defeat an earlier two-pass value only for the bounded inserted-digit case: same model, current JSON and narration agree, ownership is matched, the longer value is one inserted digit away and over five times the official reference, while the photographed shorter value remains within three times. The official reference detects the typo but never replaces the photographed price.
- One strong wide-scene structural vote (`3+`, no unique subject, no identity) plus two identity-free wide-scene narrations must finish as distant after call three. It must not create a slow-model or human queue.
- Evidence repair always prefers a complete same-run `[1,2,3]` group over a shorter cross-process tail and may re-enqueue an exact pixel-authority correction idempotently without a fourth call.

## Revision `.35` known-pixel completion addendum (2026-07-17)

- In a known-source expectation, `price: null` means the photographed result must have no price. Never stringify it to `"None"`; only a real nonempty digit value is a conflict.
- A bound known-pixel conflict combined with `structured_narration_followme_conflict` remains one-photo content uncertainty through calls 1 and 2. It may reach call 3 but never call 4 and must not stop unrelated photos. Any binding, memory, prompt, cross-photo, absurd-price or transport reason still fuses the batch.
- Human pixel authority is valid only on call 3 with three identical full-image hashes and three independent, request-verified, uncontaminated calls. Once applied and contract-valid, the finalizer must accept that corrected third-pass result instead of reapplying the earlier unresolved decision.
- Permanent live set/run: `317/318/1319/1320/1321/1325`, run `20260717_072657_073759`, 6 verified, 0 review, 0 failed, exactly 18 calls. Preserve the exact outcomes documented in `docs/development_guide.md`.
- Production acceptance additionally requires the existing browser tab to prove three photo/narration/card transitions without reload or overflow, plus three unique stream-upload receipt/canonical closed loops. Passing unit tests alone is insufficient.
- The speed SLO is 1,667 verified-and-uploaded photos per day for the 2026-09-06 target. Below 802 per day threatens the 2026-10-31 conservative commitment and is a reportable operational incident.
- Content-drift monitoring includes complete-frame geometry and subject ownership. If narration says an adjacent screen is partially visible, edge-cut, or lacks a complete bezel, structured `complete_screen_count` must not count it. Nearby/background/screen marketing labels (`Odyssey`, `G7`, `G8`, `M8`, `Smart Monitor`) may not become the main unit's family unless a same-subject physical card proves ownership.
- Permanent `.41` real-image set: `Lalapo-279`, `潭子-1397`, `SMS-348`, `SMS-356`, `SMS-357`. Acceptance is 5 verified, 0 review, 0 failed, exactly 15 calls, no prior-answer exposure or prompt contamination. Preserve the pixel-bound outcomes in `docs/development_guide.md`.
- Forecast completion from verified, upload-eligible photos in a rolling 24-hour window, not calls or UI events. Report the honest forecast separately from the target throughput. The 2026-07-17 baseline was 259/day with 84,990 remaining => 2027-06-11; target 2026-09-06 requires 1,667/day (69.4/hour, 6.43x).

## Revision `.41` isolated-binding and bounded-finalization addendum (2026-07-17)

- A single `request_id_missing` or `request_id_mismatch` is a bad response, not proof that every later photo is corrupted. The response is unusable, never enters view/model/price consensus, still consumes its call number, and the same photo may use only its remaining slots up to call three.
- Repetition on the same source ends that photo as a terminal technical result after call three but does not create calls four through six. The first occurrence on a second distinct source proves a broader runtime binding fault and must trip the durable batch fuse.
- Three fully healthy, same-image, request-bound, stateless calls may finalize an ordinary unique single unit with null model and null price when at least two calls agree on the single-unit structure and none claims FollowMe. Missing identity fields remain null and are uploaded truthfully; they are not a reason for an unspecified slow-model or human queue.
- Wording such as “螢幕下方有 Samsung 品牌貼紙” describes ownership below the one main monitor. Do not parse it as “another monitor below.” A wide-background interpretation requires explicit wording such as `另有`、`還有` or `可見` another monitor.
- Repair of a result file is enqueue-first and atomic. If the runtime fuse causes a newly finalized job to enter `_drive_upload_stream/failed`, confirm the exact failure reason, clear/archive the fuse only after fix plus full regression, and requeue only those exact jobs. Acceptance requires a unique Drive receipt with nonempty Drive ID.

## Throughput cascade and judge-model contract (2026-07-17)

- Use a fast production first pass and escalate only risky photos. An ordinary single unit with internally consistent structure, model, price, and label ownership may finalize and upload after pass one. Distant/multi-screen scenes, possible FollowMe, a single unit missing model or price, evidence contradictions, or request/image binding faults enter blind adjudication.
- `↑`, `↓`, and `✓` are deterministic comparisons after photo OCR. A difference from the official reference price alone must not trigger another VLM call. Pre-2026 photos do not perform live official-price lookup.
- Every judge call sees only the same original image/crops and a new RequestID. Never expose a prior answer, summary, reason, or conversation history. The entire photo remains capped at three model calls.
- Serial production OCR loads LM Studio with `parallel=1`. A heterogeneous 7B–12B judge must pass the fixed 50-photo blind benchmark and permanent regressions before promotion. Do not load/unload a 27B judge per photo on a 16GB GPU.

## Price-role and de-duplicated progress contract (`20260721.60`)

- Read every amount and its printed role on the same physical card before selecting `price`. Never relabel list/reference MSRP (`市價`, `原價`, `參考價`, `建議售價`) as a current member/sale price. When a prominent current or promotional amount coexists, use that amount; if there is only one amount, say so explicitly.
- A current-year single unit with a readable model and price but `high/low` official comparison gets one stateless price-role confirmation. If two clean passes disagree on price, consume the third and stop. This targeted gate does not force matching-price easy cases into extra rounds and never permits call four.
- Retry prompts and supplemental crop labels are neutral geometry only. No prior answer, price, SKU, correction reason, summary, answer template, or leading crop title may enter attempts 2 or 3. Prompt examples must not contain copyable Samsung product/price answers.
- The canonical regression photo `M-台南市-永康區-TK3C-永康大灣-1415.jpg` is hash-bound to `S27D300GAC`, list price `3,590`, current promotional price `3,290`. Its `.60` acceptance requires exactly three independent calls, final `3,290`, a unique stream receipt, and ID-scoped disposal of the old `3,590` remote object.
- `initial OCR total` counts unique source photos only. Reviews, offline adjudication, filename corrections, and replacement uploads must not inflate it. During re-review, prove liveness with period progress, live photo/narration/card transitions, and exact upload receipts.

## Hash-bound pixel authority and corrected-copy closure (`20260721.60`)

- After three independent, request-bound, uncontaminated calls on one identical input SHA, never make a fourth call. A human full-resolution decision may finalize offline only through a manifest bound to source item ID, original-source SHA-256, and actual model-input SHA-256.
- Count complete monitors from the original image boundary. A neighboring monitor or price card cut by an image edge cannot become the main subject. A readable nearby SKU is not enough: verify that the card is physically attached to the selected complete monitor before changing an existing result.
- A corrected upload is not closed merely because the new receipt exists. Verify the new remote ID, size, MD5/SHA; enumerate every old wrong-name receipt for the same source; trash only those exact old Drive IDs; then prove every old path is absent and the correct new object still exists. Multiple old wrong names may legitimately map to one corrected object.
- An active repair bridge may idempotently preserve exact bound corrections against a legacy backend rewriting the result file, but it must be one hidden worker, make zero model calls, leave OCR/dashboard/uploader/browser uninterrupted, and exit when the evidence revision changes.
- Acceptance spans five layers: bound evidence, durable terminal result, canonical local filename, exact upload receipt, and verified old-copy disposal. Missing any layer means the correction is incomplete.

## Physical FollowMe business-subject priority (`20260721.64`)

- This rule supersedes the old absolute “3+ complete screens always means distant” rule. Scan the full original for a physical FollowMe first. Direct branding on the same unit, or a display physically joined outside the lit screen pixels to both a white vertical stand and a complete round floor base, makes that FollowMe the single business subject regardless of surrounding complete monitors. Preserve the honest full-frame monitor count for audit.
- A stand, cart, tray, base, or FollowMe wording that appears only inside played video, advertising, or UI is `screen_content_only`, `same_subject=false`, and never hardware evidence. Smart Monitor M5/M7 or an S32FM SKU alone is not direct FollowMe proof.
- After three healthy, stateless, image-bound calls, settle view, variant, and price separately. Repeated physical evidence may prove the FollowMe family while an M7/Pro/size disagreement leaves model null. Two independently matched reads of the same attached price may keep that price; one variant disagreement must not erase it. Different prices still clear price, and unrelated field majorities must never be combined.
- A live acceptance set must contain both directions: a physical FollowMe in a wide scene, ordinary wide distant scenes, an ordinary non-FollowMe single, and an adjacent-price trap. Verify revision, request binding, identical image SHA per photo, no prior-answer exposure, no prompt contamination, no call four, and zero runtime-health invariants before clearing a fuse.
- The Dashboard's canonical total remains unique-source processed progress. Current correct baseline is `66,724/151,714`; review passes and isolated smoke never increment it. During review, prove liveness with the period counter, current file/pass, natural-language narration, accumulated cards, and per-photo upload receipts.

## Revision `.73` zero-frame and live-resume contract (2026-07-23)

- A monitor contributes zero unless all four physical bezel sides and all four corners are inside the original-image boundary. The hash-bound `彰化中山-234` authority supersedes the old single-unit conclusion: it is distant, count 0, model null, price null. `嘉義新光-199` remains a one-screen single with both identity fields null because nearby speaker prices and FollowMe promotion are not owned by the central display.
- Before reusing any compatible verified trace, the backfill builder must reject rows that conflict with the current exact pixel authority on view, complete-screen count, model, or price. Compatibility by revision alone is insufficient.
- A known-authority conflict during smoke calls 1–2 is an expected photo-local content reason only when call 3 persists `three_pass_human_audited_pixel_authority`, all calls are request-bound/stateless/same-image, and no other unsafe reason exists. It never relaxes binding, memory, prompt, transport, or cross-photo fuses and never permits call 4.
- After a backend evidence-revision reload, independently reload the single hidden stream uploader and prove it claims a new current-revision pending job. A live PID is not proof: `unapproved pending upload revision`, a non-advancing pending queue, or no new exact receipt means the uploader is stale. Restart only that worker; keep OCR, Dashboard, LM Studio, and the existing browser tab untouched.
- Resume proof requires all four live layers: formal current file/pass/processed change, `stream_file` identity alignment, accumulated presentation cards, and verified pending-to-receipt upload closure. The unique-source top total may stay unchanged during rereview and must not be animated or inflated.

## Revision `.74` sparse-binding and always-visible Dashboard contract (2026-07-23)

- Every missing or mismatched request ID remains unusable evidence and consumes one of the absolute three calls for that photo. No recovery path accepts the invalid answer or permits call four.
- A single explicit mismatch is photo-local. Batch-wide request-binding crosstalk is proven only by three distinct source photos with explicit `request_id_mismatch` inside a rolling ten-minute window. Old or hours-apart mismatches must not accumulate forever and stop an otherwise healthy month.
- Legacy fuses produced by the old staging-lifetime counter may be recovered only by `tools/recover_contained_request_binding_fuse.py`: attempts 1-2, healthy prior bound history, stable source identity, valid prepared-input hash, explicit expected/actual mismatch proof, fewer than three recorded mismatch sources, preserved call count, same-photo retry, and an archived fuse receipt.
- A content fuse pauses OCR/upload only at a photo boundary. Port 5002, the Dashboard/status API, LM Studio, and the visible existing Chrome tab stay online. When the live queue is temporarily empty, the API retains the last coherent presentation only while that source file still exists; it never blanks the boss-facing monitor or replays a deleted staging path.
- Repair, documentation, Git, and periodic monitoring are side work. They never constitute permission to leave the formal runner stopped. After deterministic recovery, the controller resumes the exact staging checkpoint automatically and the current-revision uploader must prove pending-to-receipt closure.
- Completion dates use observed end-to-end verified-plus-uploaded throughput, not model-call latency alone. The 2026 high-risk review may average multiple calls per photo; historical easy photos are measured separately after they start. Recompute the ETA whenever a 12-hour window contains downtime or a materially different call rate.

## Revision `.75` natural-null and third-call recovery contract (2026-07-23)

- Natural narration such as `型號與價格看不清楚，必須填 null` is not prompt echo. Keep explicit template/instruction echo blocked, but never match the broad fragment `必須填` by itself.
- A false presentation fuse after call three does not authorize call four or a counter reset. Recovery requires all three stateless request-bound outputs, one image SHA, no prior-answer exposure or prompt contamination, exact source bytes, and a registered human pixel authority. Enqueue first, persist the terminal task second, archive the fuse and receipt, then auto-resume the same checkpoint.
- Permanent regression source `三創店-498` is `單機`, two complete foreground monitors, model null, price null, ambiguous ownership. Upper monitors cross the original top edge; the multi-product center card cannot own one SKU/price. Bind this only by source item, original SHA and model-input SHA, never by filename.
- Required validation after touching this boundary: the narrow recovery test, runtime-health tests, and `tools/run_critical_regressions.py`. Live acceptance additionally requires current-revision port 5002 progress and exact pending-to-Drive-receipt closure while the existing Dashboard tab remains untouched.
- The responsible forecast uses rolling verified-plus-uploaded throughput. As of this repair the conservative full-project window is 2026-08-28 through 2026-09-01 at 2,350–2,500/day; the old August 10 latency-only date is invalid. Reforecast after any 12-hour outage or when historical one-pass throughput is measured.

## Revision `.76` terminal cross-field consistency contract (2026-07-23)

- Adjudication may restore a correct terminal model or price only if the
  missing-field `quality_issue` is recomputed in the same finalization step.
  A model plus “missing specification”, or a price plus “missing price”, is a
  terminal contradiction and must fail closed before `verified=true`.
- Normalize only missing-model/missing-price wording. Preserve independent
  issues such as blur or screen quality. A distant result truthfully keeps
  model/price null without being marked as a failed single-product extraction.
- Enforce the same invariant again in the stream uploader and when migrating
  an explicitly compatible pending job. Compatible historical verified rows
  must also pass the current invariant before a backfill builder skips them.
- This is zero-model deterministic repair. Never spend a fourth call, reset
  attempts, rebuild staging, duplicate a Drive object, or inflate the canonical
  unique-source total to make review activity look like first-pass progress.

## Selective escalation and no-ritual-third-pass contract (2026-07-24)

- Accuracy-first does not mean three calls for every photo. A clean ordinary
  non-FollowMe single with a valid evidence contract, unique main subject,
  `matched` card ownership, directly supported model and price, and no
  narration/binding/field conflict closes and uploads after pass one.
- Escalate only the risky field: short-SKU completion, 2026 price-role
  confirmation, a missing field, distant/FollowMe evidence, ambiguous
  ownership, or a material cross-pass conflict. If stateless pass two resolves
  the trigger, close immediately. Pass three exists only for a conflict that
  remains after pass two; it is never a fixed ritual. The sole content-audit
  exception is the user-mandated three independent checks for a 2026 distant
  scene or a wide scene that may hide a physical FollowMe. Never apply that
  exception to an ordinary single-unit photo.
- Several visible cards do not by themselves make ownership ambiguous. If the
  narration explicitly aligns one card to the main monitor and explicitly
  assigns the remaining cards to neighbours, neighbour-card wording must not
  contradict `label_ownership=matched`. Continue to fail closed when the main
  card itself is described as unaligned or unowned.
- Permanent regression `M-台南市-新營區-SF-新營-732.jpg`: pass one legitimately
  escalates because `S27CG552` is uniquely completed to `S27CG552EC` and the
  photographed `5,790` requires price-role confirmation. An identical,
  request-bound pass two must close the photo. The legacy three-call row was
  deterministically finalized and uploaded as
  `單機 / S27CG552EC / 5,790` with zero additional model calls.
- Measure first-, second-, and third-pass rates on an unbiased full-month mix.
  A selected high-risk tail is not a throughput baseline. An elevated third
  pass rate in an ordinary mixed batch is a systemic trigger-regression signal,
  not a reason to accept two-to-three-times slower OCR.

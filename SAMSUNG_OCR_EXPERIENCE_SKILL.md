---
description: Technical Rulebook & Post-Mortem for Samsung OCR Project
---

# SAMSUNG_OCR_EXPERIENCE (Project Bible)

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
- The right rail reads only revealed results. During OCR, neither
  `recent_results` nor `current_file` may enter presentation as fallback.
- Keep the previous image until the next image is loaded; never blank the main
  preview during a normal transition.
- Visible copy uses `AI`, never `LLM` or `自言自語`.
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
- A confirmed FollowMe rescue is authoritative over a generic later sentence containing `遠景`. Do not let a final narration fallback overwrite a model that has Samsung FollowMe wording, a fixture/stand clue, or a positive physical clue.
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

7. Current-year distant views are not safe to upload.
   - For 2026 and future periods, `遠景` must enter rerun/review before Drive upload.
   - Reason: many false distant views are actually single monitors, FollowMe, or non-Samsung single units with dense labels.
   - Historical distant views may remain ready unless other review reasons apply.

### Google Drive upload rule

- Upload destination is the user's shared Drive folder. Use year-only child folders (`2026`, `2025`, ...); do not create month folders.
- Run `tools/prepare_drive_upload_manifest.py` against `D:\00_商化\00_已OCR照片` before each upload batch.
- Upload only `ready` rows staged under `_drive_upload\staging`. Rows in `_drive_upload\drive_upload_review_required.csv` must be rerun or reviewed first; filenames containing `無型號` are not safe for Drive.
- Upload manifests are newest-period first, so the unattended uploader should send `2026` before `2025` before `2024`.
- After each successful upload, append the exact Drive-returned file name and ID to `_drive_upload\drive_upload_uploaded.csv`, then rerun the manifest. This is the resume guard and prevents duplicate uploads.

### Manual review panel rule

- The dashboard `待人工校正` drawer reads `_drive_upload\drive_upload_review_required.csv` and is the user-facing inbox for blocked Drive rows.
- Do not place this queue inside the normal boss-facing monitor. It should stay behind the toolbar button so the live OCR view remains clean.
- `記錄` writes `_ocr_audit\manual_corrections.csv`; `學規則` writes `_ocr_audit\manual_learning_rules.csv`; `標記重跑需求` records that another safe candidate CSV must be generated before rerun.
- Treat `manual_corrections.csv` as authoritative human feedback for later repair/export scripts. Do not upload a row that is still only in review unless a follow-up script has rebuilt a safe filename and the manifest marks it `ready`.
- Use `tools/apply_manual_review_corrections.py` to turn recorded manual corrections into a dry-run rename plan. Add `--apply` only after reviewing `_ocr_audit\manual_correction_rename_plan_*.csv`, then rerun the Drive manifest.
- The quick ARK action means Odyssey Ark / Ark Mini LED / 55-inch upright or curved desk displays => `S55BG970NC`; still do not borrow nearby S27/S32 monitor labels.

### Known unresolved defects

- Current-year repaired export dry-run still blocks: 202605 has 79 rows with store price but unknown Samsung/PChome reference.
- Some completed 2026 filenames still contain `？＄`; these must be repaired or moved to manual review.
- Some completed rows are `model + 無價格` although thinking text contains a valid price. Use thinking rescue or focused rerun.
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
- If backend correction changes `遠景` to `單機-FollowMe`, the UI must use the corrected final narration instead of raw live text that still says `不是 FollowMe`, `沒有 FollowMe`, or `整體符合遠景`.
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
- If post-processing rescues a row to FollowMe, the user-facing `thinking`/display narration must also be corrected. Do not show a card or filename as FollowMe while the AI narration says `不是 FollowMe` or `整體符合「遠景」條件`.

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
- Never upload `review_required` rows. Last pause snapshot: `ready_pending=0`, `uploaded_skipped=52122`, `review_required=13424`.

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
- Backend/UI rule: durable AI history and right-side result cards must use the final post-processed narration from `build_final_display_thinking()`. Never leave a corrected FollowMe result beside visible text saying it is not FollowMe or is distant view.
- Live 2026 distant rerun started 2026-07-09: log `logs\current_year_distant_staged_rerun_20260709_103552.out.log`, candidates `_ocr_audit\current_year_distant_staged_rerun_candidates_20260709_103552.csv`, summary `_ocr_audit\current_year_distant_staged_rerun_summary_20260709_103552.csv`.
## 本機模型評測安全規範

模型比較只能使用 `tools/model_benchmark_sidecar.py` 的 bounded sidecar。它必須以 fixed `ocr_demo_50` blind set、同一 production prompt 與同一影像證據執行；不能把 benchmark 當成訓練或修改 OCR 權重。dry-run 不會碰 LM Studio，真正執行必須明確 `--execute`。

sidecar 的硬守門是：API `is_running=false`、沒有任何 rerun/recursive/watcher/uploader、所有指定模型已完整下載、endpoint 為 localhost、raw JSONL 可重入保存。任何模型切換後都必須在 finally 還原 `qwen/qwen3-vl-8b` 與 context；不得停止或重啟正常 OCR，也不得上傳照片。
Windows 程序清單必須由 UTF-8 JSON 安全解析，列舉失敗或空白解析錯誤一律拒絕 benchmark；不能把錯誤當作「沒有 runner」。取得 lock 前後都要重查 API 與程序，以關閉競態。FollowMe 被判成中文 `遠景` 或英文 `distant_view` 都屬危險誤判。

採用候選模型前，先要求整體 exact/field accuracy 改善或至少不退步；遠景、FollowMe、型號幻覺等危險錯誤率全部不得退步。latency 只作 accuracy 通過後的次順位，不得用速度掩蓋辨識退化。InternVL 只有在本機下載完成並通過完整性檢查後才列入執行候選。
## v19.45 Evidence Contract

The machine-readable evidence contract is authoritative for acceptance: screen count, unique main subject, label ownership, and same-subject FollowMe physical evidence. Missing, contradictory, or cross-pass disagreement is `review_required`; prose keywords never rescue a result. Current-year upload readiness requires the v19.45 trace, while historical rows remain governed by their existing gates.

## Presentation Synchronization Iron Rule

`presentation_id` and `presentation_sequence` are the only UI identity truth. Photo, AI live interpretation, active placeholder, revealed card, and inspection modal must render from the same immutable snapshot. Running presentation state must not use filename/index/source-path joins or `current_file`, `stream_file`, or `recent_results` fallbacks. The right card appears only after the same snapshot's narration finishes. Active items are never dropped by watchdog or backpressure; a previous image remains visible until the next image is ready, so continuity never produces a black frame. Every dashboard presentation change requires the 500-item duplicate/out-of-order/overflow/remount soak and a fresh build.

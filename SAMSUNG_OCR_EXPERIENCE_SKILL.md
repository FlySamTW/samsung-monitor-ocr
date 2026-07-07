---
description: Technical Rulebook & Post-Mortem for Samsung OCR Project
---

# SAMSUNG_OCR_EXPERIENCE (Project Bible)

## Dashboard Live Sync Rule (2026-07-01)

For live OCR runs, preview image, LLM self-talk, and parsed result must never be mixed across filenames.

- `current_file`: the active image being processed and shown in the main preview.
- `stream_file`: the image that owns `stream_buffer`.
- `latest_result_file`: the newest completed OCR record for history/recent-results UI.

Do not use `recent_results[0]` to update the main preview while a batch is running. It is usually the previous completed image and will make the UI look one image out of sync. The frontend should update the main image only when `current_file` changes, and should show live self-talk only if `stream_file === current_file`.

## Delayed Result Panel Rule (2026-07-03)

The user-facing sequence must be:

1. Show the photo in the main preview.
2. Play that photo's LLM self-talk/typewriter text to completion.
3. As soon as a new photo is visible in the main preview, show that same photo at the top of `辨識紀錄` as `處理中 / 等待自言自語完成`, even if the next self-talk text has not started yet.
4. When that self-talk has finished but the next photo's self-talk has not started, keep the completed narration visible as a held previous-summary state. Never let the LLM/self-talk pane go blank or collapse to a black empty area during normal running.
5. Only after self-talk completes, replace the placeholder with that photo's parsed thumbnail/model/price/status.

The backend may finish multiple photos ahead, but the right panel must never reveal a photo's model/price/status before its self-talk has finished. It also must not leave the previous completed result at the top while a new photo is typing, because that looks like mismatched metadata. In `dashboard/src/App.jsx`, prefer the display queue and already-presented cutoff over `recent_results` whenever a queue exists. `recent_results` is backend-speed data and will confuse viewers if shown early.

Interface presentation is a first-class requirement. If the LLM pane blanks out between photos, the monitor looks broken even when OCR data is correct. Preserve visual continuity: previous narration may stay softly visible until the next narration begins, with a clear previous-summary label.

## Dashboard Stage Clock Rule (2026-07-07)

The dashboard is judged by viewers before they understand the backend. Treat the live monitor like a staged presentation, not a raw log tail.

- The visible LLM pane must be independent from the internal typing cursor. Clearing `displayedBuffer` for the next item must never create a black or empty self-talk area.
- During the gap between photos, keep the previous narration visible with a calm status label such as "previous summary held / next photo judging".
- The sequence is a visual contract: new photo appears, the LLM pane immediately has either held narration or live typing, then the right-side result is revealed.
- At the exact moment the right-side model/price/status is revealed, the LLM pane label must switch from "live judging" to a completed/revealed state. Never show "LLM is still judging" while the formal result is already visible.
- Once a stable queue key has been revealed, later buffer updates for that same key must not downgrade the visible phase back to "typing/live judging".
- Self-talk needs a minimum readable dwell time. Do not make the typewriter or revealed-summary hold so fast that narration flashes by just to chase backend throughput. Backend speed is allowed to run ahead; the monitor must still feel intentionally paced.
- If the backend runs ahead, catch up by trimming or dropping stale display-only queue items, not by blanking the LLM area.
- A UI that looks paused, black, or mismatched is a product failure even when OCR files are correct. Fix presentation defects with the same urgency as OCR correctness defects.

## Live Presentation Catch-Up Rule (2026-07-06)

The dashboard must balance two user-facing truths: no mixed metadata, and no frozen-looking preview. A pure "wait for every queued self-talk to finish" approach fails during fast OCR because the frontend can lag dozens of photos behind the backend.

- Keep the main preview, self-talk, and right-side thumbnail scoped to the same stable queue key.
- Keep a bounded local presentation queue; when the backend display queue is full, discard stale display-only items that are no longer in the backend's latest queue.
- Trim long self-talk for display so one photo cannot monopolize the boss-facing monitor.
- Add a watchdog: if the presentation layer stops advancing for several seconds, clear the stale active presentation and resume from the newest safe queue slice.
- Add `key={currentImage}` to the main preview image so React remounts it whenever the photo URL changes.
- This catch-up behavior only affects dashboard presentation. It must never delete OCR records, copied output photos, or audit rows.

## Live Sync No-Regression Rule (2026-07-07)

This is a non-negotiable rule for every future AI or developer touching the dashboard.

- The photo, visible LLM self-talk, and right-side thumbnail/model/price must share the same stable item key.
- Preserve the visual trick: the user may be watching the previous completed item while the backend is already processing the next item, but the right-side result must only reveal after that item's self-talk has finished.
- Do not replace `pendingQueue`, `activePresentation`, and `revealedResults` with raw `recent_results` during OCR. That is the known path back to desync.
- `recent_results` is only an idle/history fallback. It must not drive the live main preview.
- Stopping a batch or switching folders must clear stale live presentation state. The backend may retain old `current_file` for audit, but the UI must not show that stale file as an active photo/LLM pair.
- Keep the current photo visible until the next full-resolution image is ready; never blank or dim between photos during a normal run.
- Any UI timing change must be verified in the browser after rebuild with the sequence: photo visible -> LLM text types -> right-side result reveals. If these three do not match, the change is not done.

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
7. 輸出資料夾不可等於來源根資料夾、不可放在來源根資料夾底下、也不可是來源根資料夾的上層，避免重跑時掃到自己輸出的照片或混入無關檔案。
8. `run_recursive_ocr_flat_export.bat` 啟動前要先用 `tools\validate_recursive_ocr_inputs.py` 預檢來源、輸出路徑、是否至少有一張 `.jpg/.jpeg/.png`，以及輸出第一層是否已有照片但缺少 `_ocr_audit\folder_summary.csv`；預檢失敗時不可啟動 LLM 或 OCR 後端。
9. `run_ocr.bat` 與 `run_recursive_ocr_flat_export.bat` 啟動前都要先用 `tools\stop_ocr_server.py` 清理既有 `samsung_ocr_batch_processor.py` 後端，避免連到舊程式。
10. AI、排程或非互動環境執行批次檔時要設定 `OCR_NO_PAUSE=1`，避免成功或錯誤結尾卡在 `pause`。
11. `run_recursive_ocr_flat_export.bat` 接力結束後預設清理本次 OCR 後端；若需要保留後端觀察狀態，可設定 `OCR_KEEP_SERVER=1`。
12. 接力器預設用 `_ocr_audit\folder_summary.csv` + `copied.csv` 續跑；已完整複製、來源照片數與最新修改時間未變、且目標檔案仍存在的資料夾標為 `skipped_existing`，避免中斷重跑時產生 `_2` 重複檔。
13. `run_recursive_ocr_flat_export.bat` 會在接力器跑完後自動用 `tools\recursive_ocr_audit_report.py --output-dir <輸出資料夾>` 驗收；手動拆跑 Python 接力器時也必須補跑驗收，通過才可回報全量完成。驗收摘要在 `_ocr_audit\audit_summary.json`，內含驗收時間、審計檔路徑與主要數量；失敗時看 `_ocr_audit\audit_report.csv`。
14. 若使用者只說 `GIT`，也要同步本專案專屬 SKILL；本檔就是本專案優先更新的 SKILL。
15. `tools\photo_rename_planner.py` 必須用 `period` 決定價格欄是否可帶比價符號；歷史年度即使 `results.csv` 殘留 `price_symbol`，輸出檔名也只能保留店內價格。修改後至少執行 `tools\test_photo_rename_planner.py`。

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

- Current dashboard build: `v19.27 (穩定監看)`.
- Boss-facing sequence must look like: photo appears, LLM self-talk types, the same photo appears as `處理中` in `辨識紀錄`, the completed narration stays visible until the next narration begins, then the parsed result appears after self-talk finishes.
- Backend may process ahead, but the visible filename, preview, self-talk, and lower-left `辨識中` panel must all refer to the same displayed photo.
- The lower-left panel must preserve the historical LLM record, including `[THINK]` summaries and final classification lines. Filter only raw `JSON Error`, initialization/debug messages, batch start/stop noise, and internal queue wording.
- Do not let `圖片損壞`, `無法識別圖片格式`, stop/interruption messages, or queue maintenance logs fill the lower-left panel. If live logs are only noise, show readable recent `display_queue` summaries instead.
- Never black out, dim, or collapse the main preview between photos. Keep the previous full-resolution photo visible until the next full-resolution image has loaded.
- On a stop/idle transition, clear active live labels and narration state, but do not forcibly blank the visible photo. Showing `上一張畫面保留` is acceptable.
- The right-side column must be wide enough for filenames on desktop and must stack below the preview on narrow windows. Do not squeeze the left photo/LLM area into a narrow vertical strip.
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

# AI 接手執行手冊

本文件給另一台電腦上 pull 本專案後的 Codex / AI 使用。目標不是重新規劃，而是在使用者指定照片來源後，直接把既有流程跑起來。

## 任務定義

要完成的工作：

1. 使用該電腦上的 LM Studio 本機視覺模型，預設模型為 `qwen/qwen3-vl-8b`。
2. 讓使用者指定照片來源根資料夾，例如 `D:\00_歷年商化照片`；這個路徑不是專案常數。
3. 遞迴處理來源資料夾與所有子資料夾中的 `.jpg`、`.jpeg`、`.png`。
4. 照片大於 2K 時，長邊縮到 `2560`，短邊按原比例自然縮放；不裁切、不補白、不硬拉伸。
5. 依既有 prompt 跑 OCR，取得類別、型號、價格。
6. 當年度照片才做 Samsung 官網價格比對；以 2026 年執行時，2025 含以前不比價。
7. 產生新檔名，並把改名後照片複製到單一新輸出資料夾，不保留原子資料夾層級。
8. `HEIC`、`WebP` 不處理，但必須寫入略過審計。

不要把照片、OCR 結果、`runs/`、CSV 審計成果或本機設定檔放進 Git。

## 開始前必問

如果目前對話或環境沒有提供，先問使用者這一題：

```text
請提供照片來源根資料夾；若未指定輸出資料夾，我會使用「來源資料夾_OCR整理」。
```

若使用者已提供來源根資料夾，就不要再問；輸出資料夾可預設為：

```text
<照片來源根資料夾>_OCR整理
```

輸出資料夾不可等於來源根資料夾，也不可放在來源根資料夾底下；否則重跑時會掃到自己輸出的改名照片。
輸出資料夾第一層若已有 jpg/jpeg/png 但沒有 `_ocr_audit\folder_summary.csv`，不要直接沿用；請改用新的輸出資料夾，或先移開既有照片。

## 開始前檢查

在專案根目錄執行：

```powershell
git status --short --branch
```

保留既有未提交差異，不要回復使用者或其他 AI 留下的檔案。若要做 Git，只 stage 本次相關 code/docs，不 stage 照片與結果檔。

確認基本檔案存在：

```text
run_recursive_ocr_flat_export.bat
tools\recursive_ocr_flat_export.py
tools\local_llm_manager.py
samsung_ocr_batch_processor.py
samsung_ocr_prompt.txt
型號表.txt
```

## 建議執行方式

PowerShell 中設定來源與輸出，再執行接力批次檔：

```powershell
$env:OCR_SOURCE_ROOT = "D:\你的照片根資料夾"
$env:OCR_OUTPUT_DIR = "D:\你的照片根資料夾_OCR整理"
$env:OCR_NO_PAUSE = "1"
.\run_recursive_ocr_flat_export.bat
```

這個批次檔會：

1. 優先使用 `.venv\Scripts\python.exe`，找不到才用系統 `python`。
2. 用 `tools\validate_recursive_ocr_inputs.py` 預檢來源、輸出路徑與是否至少有一張 `.jpg/.jpeg/.png`；預檢失敗時不啟動 LLM 或 OCR 後端。
3. 用 `tools\stop_ocr_server.py` 清理既有 `samsung_ocr_batch_processor.py` 後端，避免連到舊程式。
4. 用 `tools\local_llm_manager.py ensure` 確認 LM Studio 與模型已啟動。
5. 啟動 `samsung_ocr_batch_processor.py` 作為本機 OCR 後端。
6. 執行 `tools\recursive_ocr_flat_export.py`，逐資料夾接力 OCR 並輸出改名照片。
7. 執行 `tools\recursive_ocr_audit_report.py` 驗收輸出資料夾；驗收失敗時批次檔會以錯誤狀態結束。
8. 預設清理本次 OCR 後端；若要保留後端觀察狀態，執行前設定 `$env:OCR_KEEP_SERVER = "1"`。

接力器預設會續跑：若 `_ocr_audit\folder_summary.csv` 顯示某資料夾已成功複製、來源照片數與最新修改時間未變，且對應 `copied.csv` 裡的目標檔案仍存在，該資料夾會標為 `skipped_existing`，不重跑 OCR，也不再複製出 `_2` 重複檔。只有明確需要全部重跑時才加 `--no-resume`，並應改用新的輸出資料夾。

如果要先只看資料夾排序與略過清單，不跑 OCR：

```powershell
.\.venv\Scripts\python.exe tools\recursive_ocr_flat_export.py `
  --source-root "D:\你的照片根資料夾" `
  --output-dir "D:\你的照片根資料夾_OCR整理" `
  --dry-run
```

## 不用批次檔時

若必須手動分兩步跑，先啟後端：

```powershell
.\.venv\Scripts\python.exe tools\local_llm_manager.py ensure
start "Samsung OCR Server" /min .\.venv\Scripts\python.exe samsung_ocr_batch_processor.py `
  --api_base http://127.0.0.1:1234/v1 `
  --api_key lm-studio `
  --model qwen/qwen3-vl-8b `
  --dir "D:\你的照片根資料夾"
```

再啟動接力器：

```powershell
.\.venv\Scripts\python.exe tools\recursive_ocr_flat_export.py `
  --source-root "D:\你的照片根資料夾" `
  --output-dir "D:\你的照片根資料夾_OCR整理" `
  --backend-url http://127.0.0.1:5000 `
  --api-base http://127.0.0.1:1234/v1 `
  --model qwen/qwen3-vl-8b
```

## 檔名規則

固定格式：

```text
M-年月-縣市-行政區-通路-店名-類別-型號-價格-原流水號.jpg
```

當年度有比價時，價格前要保留符號：

```text
↑＄4990
↓＄4990
✓＄4990
？＄4990
```

歷史年度不比價時，只保留店內價格：

```text
＄4990
```

`tools\photo_rename_planner.py` 會用 `年月` 再檢查一次；歷史年度即使舊 OCR 結果有殘留的 `↑/↓/✓/？`，檔名也不能帶出比價符號。

非三星主角螢幕不是 `型號未辨識`：重新辨識若確認主角是它牌，型號欄寫 `它牌(品牌)`，例如 `它牌(ACER)`、`它牌(ASUS)`、`它牌(LG)`；不填它牌實際型號。

2026 與未來年度的 `遠景` 不是可直接上傳狀態。遠景誤判可能其實是單機、FollowMe 或它牌單機，因此要先進重辨識候選；確認仍是遠景後才放行 Drive。

範例：

```text
M-202605-台北市-萬華區-TK3C-萬大-單機-S27CG552EC-↑＄4990-1005.jpg
M-202512-嘉義市-東區-TK3C-垂楊-單機-FollowMe_M7_32吋-＄12990-1172.jpg
```

## 完成判定

不能只看終端機寫「完成」。`run_recursive_ocr_flat_export.bat` 會自動執行驗收工具；若是手動拆開跑 Python 接力器，或要重驗舊輸出資料夾，請另外執行：

```powershell
.\.venv\Scripts\python.exe tools\recursive_ocr_audit_report.py `
  --output-dir "D:\你的照片根資料夾_OCR整理"
```

若只要確認歷史年度價格符號規則，跑：

```powershell
.\.venv\Scripts\python.exe tools\test_photo_rename_planner.py
```

驗收工具會檢查 `folder_summary.csv`、各資料夾的 `copied.csv`、實際輸出照片是否一致；摘要會寫到 `_ocr_audit\audit_summary.json`，內含驗收時間、審計檔路徑與主要數量。若失敗，批次檔會停在錯誤狀態，明細會寫到 `_ocr_audit\audit_report.csv`。必要時再人工檢查輸出資料夾中的 `_ocr_audit`：

```text
_ocr_audit\folder_discovery.csv
_ocr_audit\skipped_unsupported.csv
_ocr_audit\folder_summary.csv
_ocr_audit\audit_summary.json
_ocr_audit\audit_report.csv
```

完成回報必須包含：

1. `audit_summary.json` 中的 `status`、`audited_at`、`folders_discovered`、`copied_count_total`、`flat_output_images`。
2. `folder_discovery.csv` 中找到幾個含照片資料夾。
3. `folder_summary.csv` 中每個資料夾的狀態是否為 `copied` 或 `skipped_existing`。
4. `missing_result`、`missing_source`、`conflict` 是否為 0。
5. `copied_count` 加總與輸出資料夾內改名照片數是否一致。
6. `skipped_unsupported.csv` 中 HEIC/WebP 略過數。
7. 是否有 `status=error` 或 `status=blocked`。

若有錯誤，不要宣稱全量完成；回報阻塞資料夾、錯誤訊息與對應審計檔路徑。

## 常見阻塞

1. LM Studio CLI 找不到：確認 `lms` 可在 PowerShell 執行，或重新安裝 LM Studio。
2. 模型未載入：先跑 `tools\local_llm_manager.py ensure`。
3. OCR 後端未就緒：確認 `http://127.0.0.1:5000/api/status` 可回應。
4. `missing_result > 0`：代表有照片沒有 OCR 成功結果，不能把該資料夾算完成。
5. `conflict > 0`：代表改名後會撞檔名，不能覆蓋，需先看該資料夾的 `conflicts.csv`。
6. WebP/HEIC：照規格略過，只要列在 `skipped_unsupported.csv`，不要算入完成照片數。
7. 若 `tools\rerun_questionable_records.py` 正在補 `missing_result`，不要中途重啟後端或手動開 recursive。啟動一次 `tools\continue_after_missing_rerun.ps1`，讓它等補跑完成後再安全接遞迴、驗收與上傳。
8. 輸出資料夾在來源資料夾內：更換為來源資料夾旁邊的新資料夾，例如 `<來源>_OCR整理`。
9. 輸出資料夾是來源資料夾上層：更換為來源資料夾旁邊的新資料夾，避免把無關照片與審計檔算進輸出。
10. 輸出資料夾第一層已有 jpg/jpeg/png 但沒有 `_ocr_audit\folder_summary.csv`：改用新的輸出資料夾，或先移開既有照片。
10. 來源資料夾沒有 `.jpg/.jpeg/.png`：確認是否選錯資料夾；只有 HEIC/WebP 依目前規格不會處理，也不能宣稱完成。
11. 想重新跑已完成資料夾：使用新輸出資料夾，或清楚知道後果後才加 `--no-resume`。

## Git 規則

使用者說 `GIT` 時，先更新與本次實作相關的 README、開發手冊、接手手冊與專案 Skill，再 commit + push。

提交前一定要檢查：

```powershell
git diff --cached --name-status
```

不得 stage：

```text
*.jpg
*.jpeg
*.png
runs\
*_OCR整理\
*.csv
*.log
.local_llm_runtime.json
```
# Dashboard Live Sync Handoff Note (2026-07-01)

The live UI must keep photo preview, LLM self-talk, and parsed OCR result aligned by filename:

- Backend exposes `current_file`, `stream_file`, and `latest_result_file`.
- Frontend displays the main photo from `current_file`.
- Frontend displays live `stream_buffer` only when `stream_file === current_file`.
- `recent_results[0]` is history/latest completed output and must not drive the main preview while the batch is running.

If a user reports that photos switch faster than self-talk/results, inspect this contract first before changing prompt/model code.

## Dashboard Sync Update - 2026-07-06

Current dashboard versions:

- `v19.16 (總進度)` added global OCR progress in the header.
- `v19.18 (同步防呆)` fixed the long-run presentation queue so the main preview does not freeze on old photos when OCR is faster than the display animation.
- `v19.21 (右側處理中同步)` adds a same-photo `處理中 / 等待自言自語完成` placeholder at the top of `辨識紀錄` while the left photo's self-talk is typing.
- `v19.22 (自言自語保留)` keeps the previous completed narration visible until the next narration starts, and keeps the right panel's top placeholder on the same currently visible photo, so the LLM pane does not blank out or look one photo behind during normal running.

Operational rules:

- Do not drive the main preview from `recent_results[0]`.
- During a running batch, the right-side record list should come from already revealed frontend presentation items, not raw backend-speed history.
- Once a new photo is visible, do not leave the previous completed result as the top right row. Show the same current photo as `處理中`; replace it with parsed metadata only after self-talk finishes.
- Do not clear the LLM/self-talk pane while waiting for the next displayed narration. A blank pane looks broken to viewers; keep the previous narration in a softened previous-summary state until the next text actually starts.
- Presentation queue items may be discarded when they are stale and no longer present in the backend display queue. This affects only what the dashboard chooses to show; OCR audit/output data must remain intact.
- Long self-talk should be trimmed for monitor display so a single result cannot hold the preview for too long.
- If the preview appears stuck, check `/api/status` first. If `current_file` and `stats.processed` are changing, the backend is healthy and the issue is frontend presentation lag.
- `/api/status.overall_progress` is the source for the top-bar total progress. It merges folder discovery, folder summary, missing-result rerun summaries, and the current active folder stats.

# 2026-07-02 HANDOFF - Live OCR Continuation

## Live Processes

- Backend: `samsung_ocr_batch_processor.py` on `http://127.0.0.1:5000`, model `qwen/qwen3-vl-8b`.
- Runner: `tools\recursive_ocr_flat_export.py --watch`, source `D:\00_商化\00_未整理商化照片`, output `D:\00_商化\00_已OCR照片`.
- Hourly monitor automation: `samsung-ocr-hourly-monitor-and-email`; it should email `sam.lai@live.com`.
- Do not spend main-thread tokens watching logs unless there is a failure. Let the automation monitor.

## Must Preserve

- Do not delete source photos.
- Do not rerun all completed 2026 OCR only to fix filenames; use `_ocr_audit\*\success_records.csv`.
- Existing bad 2026 flat outputs have been backed up in `_bad_no_compare_2026_backup_*`.
- The formal output folder is flat: `D:\00_商化\00_已OCR照片`.

## Current Open Defects

1. Current-year unknown price:
   - 2026 and future outputs must include `↑`, `↓`, `✓`, or stop for manual review.
   - `？` is acceptable only as a blocked review state, not as silent final output.
   - `tools\repair_current_year_price_compare_outputs.py --dry-run` currently blocks on 202605 with 79 unknown reference prices.

2. PChome fallback:
   - Samsung official price lookup may fail for active products.
   - Fallback is PChome 24h Shopping, not marketplace.
   - Generic FollowMe names must query concrete models; `FollowMe Pro M7 43"` maps to `S43FM703UC`.

3. Low-price OCR:
   - Old logic removed prices `<=3000`; this was wrong for `S24F332EAC / 2390`.
   - New threshold is `<2000`.
   - Narrow exception: if the same physical price card clearly says `促銷價`, `展示出清`, `出清`, `展示機`, `福利品`, `清倉`, or `特賣`, a handwritten 4-digit number such as `1999` is a valid in-store clearance price. Do not apply this exception to monthly plan/accessory/telecom prices.
   - Existing results with model but `(無價格)` may need rerun or thinking-text rescue.

4. Distant view:
   - Some obvious distant-view rows are still `單機/(無型號)/price`.
   - Add/verify guard: no Samsung model + isolated price + no spec label or display-area context => `遠景`, clear model/price.

5. UI:
   - The blue icon button was changed to text `重跑`.
   - Historical/not-compared rows must not show a red `?` price badge.
   - `辨識紀錄` is delayed on purpose: while the photo's LLM self-talk is typing, show that same photo as `處理中 / 等待自言自語完成`; only after typing finishes may model/price/status appear.
   - The LLM pane must not disappear between photos. Keep the previous completed narration visible until the next narration starts.
   - If user still sees old UI, refresh browser and confirm `dashboard/dist/assets/index-*.js` is the latest build.

6. Google Drive upload:
   - User approved year-only folders, not month folders.
   - Parent folder: `https://drive.google.com/drive/folders/1xBaWDRjlcP-gMV-bM0K1S4gOJZ0QJJHK`
   - Existing child folders: `2026` (`1JejKATTb7COE7qTP9mIC5F9IQbHWK2L4`) and `2025` (`1UluPo7m5HCq_iVpdkioOVq292E6LAbC-`).
   - Use `tools/prepare_drive_upload_manifest.py --output-dir D:\00_商化\00_已OCR照片 --limit-ready 25` before each batch.
   - Upload only `_drive_upload\staging_map.csv` rows, then append exact Drive-returned IDs to `_drive_upload\drive_upload_uploaded.csv` and rerun the manifest. Do not upload `drive_upload_review_required.csv` rows.
   - If a year appears under-uploaded, check `_drive_upload\drive_upload_review_required.csv` first. For example, 2026 may have complete OCR/export but still be blocked because current-year rows lack `↑/↓/✓`, price, or model.
   - Dashboard API `/api/review_queue?year=2026` reads the blocked upload rows. The toolbar button `待人工校正` opens the review drawer; it records corrections in `_ocr_audit\manual_corrections.csv` and optional reusable rules in `_ocr_audit\manual_learning_rules.csv`.
   - The review drawer does not bulk-rename by itself. After human review, a repair/export script must consume the correction CSV and regenerate safe output before Drive upload.
   - Use `tools\apply_manual_review_corrections.py --output-dir D:\00_商化\00_已OCR照片` to produce a dry-run rename plan from `manual_corrections.csv`; add `--apply` only after checking the plan, then rerun `tools\prepare_drive_upload_manifest.py`.

## Recent Changes (2026-07-03)

1. **Display queue** (`samsung_ocr_batch_processor.py` + `dashboard/src/App.jsx`): backend accumulates completed results while the UI drains them at typewriter speed, reducing blank gaps between photos.
2. **Repair script improvements** (`tools/repair_current_year_price_compare_outputs.py`):
   - Always merges `folder_summary.csv` rows with fallback audit-folder scan so all 2026 periods are repaired.
   - `--allow-no-symbol-for-unknown` lets 2026 records with no Samsung/PChome reference price output without a price symbol instead of blocking flat output.
   - Pre-marks `manual_reference_price` models to skip repeated slow network lookups.
3. **Targeted rerun tooling** (`tools/prepare_targeted_rerun.py`, `tools/merge_targeted_rerun.py`, `tools/run_targeted_rerun_with_backend.py`) supports `--bottom-label-strip` and `--bottom-center-zoom`.
4. **Distant-view guard** (`samsung_ocr_batch_processor.py`): no model + no price + thinking mentions distant-view keywords => force `view_type=遠景` and clear model/price.
5. **Dashboard fixes**:
   - Main preview now loads full-resolution image from `/api/image` instead of staying on the blurred 400 px thumbnail.
   - Source path shows live `image_dir` from backend status when running.
   - `/api/list_dirs` now lists actual photo source folders under `D:\00_商化\00_未整理商化照片` instead of repo subdirectories.
6. **Resume script** (`tools/resume_original_batch.py`): watch sleep reduced from 1800 s to 60 s so the watcher moves to the next folder faster.

## Known Issues for Next AI

1. **右方縮圖與播放佇列同步**：2026-07-03 已改為延後顯示結果，LLM 自言自語播完後該筆才進 `辨識紀錄`；已實測縮圖點擊會開完整 `/api/image/<source_path>` 檢視。後續若改 UI，需重新驗證這個順序。
2. **91 筆 null-model 候選**：兩輪 targeted rerun 後仍有 91 張照片 model 為空，thinking 中也無可救回型號。需決定是否第三輪 `--bottom-center-zoom` 重跑，或改標為遠景/不合格。
3. **8 筆 S27CG552EC 價差**：thinking 可讀到 `S27CG552EC`，但店內價格（9990–29900）遠高於 PChome 參考價 4990，需人工確認是否為套組或誤判。
4. **黑屏/照不清楚未寫入檔名**：`screen_status`（黑屏）與 `quality_issue`（照不清楚）目前未出現在輸出檔名中，需規劃命名規則並更新 `photo_rename_planner.py`。
5. **codex-runtime 重複子程序**：本機 `python.exe` 會透過 codex-runtime 再啟動一層 `.venv\python.exe`，導致程序樹重複，需持續監控。
6. **長期執行啟動方式**：目前使用 Windows 工作排程器 `SamsungOCR_ResumeBatch` 作為後端+接力器長駐的權宜方案，建議未來改用更穩定的服務/守護程序。

## Recommended Recovery Commands

```powershell
# Status
Invoke-RestMethod http://127.0.0.1:5000/api/status

# Dry-run repaired 2026 exports; must pass before real copy
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe tools\repair_current_year_price_compare_outputs.py --output-dir "D:\00_商化\00_已OCR照片" --period-prefix 2026 --dry-run

# Tests
.\.venv\Scripts\python.exe -m py_compile samsung_ocr_batch_processor.py skills\official_price.py tools\recursive_ocr_flat_export.py tools\repair_current_year_price_compare_outputs.py
.\.venv\Scripts\python.exe tools\test_photo_rename_planner.py
npm.cmd --prefix dashboard run build
```
## 2026-07-03 Resume Pointer

For the latest portable handoff, read `docs/handoff_2026_ocr_resume.md` first.

Current portable sample set:

- `samples/ocr_demo_50/photos`
- `samples/ocr_demo_50/labels.json`

Important dashboard rule: do not enlarge `thumb_b64` in the main preview. It made photos look blurry in front of users. Use full image URLs with `source_path`, otherwise show a clean loading/placeholder state.

## 2026-07-03 No-Blur / Safe-Rerun Note

- If the UI still looks blurry, first restart the backend and reload the browser. The current build serves original images via `/api/image` and uses `thumb_b64` only as a last-resort fallback.
- Do not trust old risky-rerun CSV rows blindly. The rerun script must validate that each `file_name` exists inside the intended `source_folder` and period before queueing it.
- If a candidate's period and resolved source path disagree, skip it and regenerate candidates; do not let the backend log those as corrupted photos.
## 2026-07-07 dashboard stage-clock handoff

Current UI contract: the monitor must look smooth to supervisors. The photo, LLM self-talk area, and right-side result panel are a staged presentation, not raw backend state.

- Frontend version `v19.27 (穩定監看)` separates the visible LLM text (`narrationDisplay`) from the internal typing cursor (`displayedBuffer`), keeps the preview alive between photos, and fixes responsive layout.
- When advancing, catching up, or waiting for the next LLM stream, do not blank the LLM pane. Keep the previous narration visible with a calm handoff label until new typing begins.
- The lower LLM history pane must show readable history, not raw operational noise. Filter `圖片損壞`, `無法識別圖片格式`, `JSON Error`, stop/interruption text, and internal queue maintenance lines. If `lm_logs` has only noise, show `display_queue` summaries.
- Right-side records may show the current photo as "processing", but model/price/status are revealed only after self-talk finishes.
- When model/price/status are revealed, the LLM label must switch to a completed/revealed state in the same beat.
- If the backend runs ahead, drop stale display-only queue items and keep moving; never solve lag by showing an empty black LLM block.
- Do not black-screen or fade the photo between images. Keep the previous full-resolution image until the next full-resolution `/api/image/<source_path>` load succeeds.
- Right rail action text is `再辨識`; keep the rail wide on desktop and stack it below the preview on narrow windows.
- Verification must include actual browser observation, not only `npm run build`.

## 2026-07-07 recursive progress resume fix

If `folder_summary.csv` shrinks after restarting recursive OCR, do not restart from scratch. Rebuild the summary from audit folders and continue:

```powershell
.\.venv\Scripts\python.exe tools\rebuild_recursive_folder_summary.py --output-dir "D:\00_商化\00_已OCR照片"
```

Then restart `tools\recursive_ocr_flat_export.py` in normal resume mode. Keep rclone upload running unless the uploader itself is failing.
The recursive runner must refresh source discovery between folders. Do not change it back to a one-time startup scan; Sam may add or move folders while the run is already active.

## 2026-07-07 unattended watchdog

This machine now has a Windows scheduled task named `SamsungOCR_PipelineWatchdog`.

- Installer: `INSTALL_WATCHDOG_TASK.bat`
- Script: `tools\ocr_upload_watchdog.ps1`
- Interval: every 4 hours
- Behavior: preserve audit/output state, rebuild a shrunk `folder_summary.csv`, start the backend only if missing, start recursive OCR only if no runner is active and work remains, ensure exactly one questionable-rerun watcher, and start rclone upload only if ready rows remain and no uploader/rclone is active.
- When the watchdog starts recursive OCR, it must include `--watch --watch-sleep-seconds 300` so new folders added after a cycle are picked up later.
- It also records OCR progress heartbeats. If `processed/ready/current file` do not change for the configured stall window, it restarts only the OCR backend and recursive runner, then resumes from `_ocr_audit`; it must not clear output or stop Drive upload.
- It must never use `--no-resume`, clear `_ocr_audit`, delete output photos, or upload `review_required` rows.

## 2026-07-08 staged rerun rule

Important implementation notes:
- If the main dashboard on port 5000 is being watched, run staged reruns against a second backend on port 5001 (`SAMSUNG_OCR_NO_BROWSER=1`, `samsung_ocr_batch_processor.py --port 5001`) so the visible monitor does not show `_ocr_staging`.
- Do not merge staged rerun records when backend logs contain context-length errors (`n_keep >= n_ctx`, `context length`, `number of tokens to keep`). The qwen3.5 9B 2026 distant-view staged attempt on 2026-07-08 failed at 8K context and was rolled back from 202605/202604.
- Model fallback note: `qwen/qwen3-vl-8b` remains the approved production model. qwen3.5 9B VLM and Gemma 4 12B QAT are only guarded fallback candidates until they pass a 16K+ staged sample. MiniCPM-V-4.6 was attempted, but LM Studio rejected image requests for the currently loaded model. Also note that old model-eval logs may show false FollowMe Pro failures because `tools/qwen_vl_regression.py` used to normalize `FollowMe Pro M7 43"` as generic `FollowMe M7 32"`; that eval helper has been corrected.
- Do not merge staged rerun records that turn a foreground FollowMe candidate into `遠景 / 無型號`. Positive FollowMe physical clues include a white floor circular base, vertical pole/stand, upright white frame, tray, or FollowMe Pro 4K / FollowMe 4K product card. Background QLED/OLED/TV display walls are not enough to override that foreground subject.

- Current/future year review rows must be cleared before older folders continue. For 2026, distant-view rows are blocked because many are false positives: FollowMe or a single main monitor with multiple spec cards.
- Slow fallback VLMs must use `tools\rerun_staged_candidates.py` instead of rerunning full source folders. The tool copies only filtered risky source photos into `_ocr_staging`, lets the backend process that small staging folder, merges new records back into the original audit `success_records.csv`, and rebuilds the flat output.
- For false distant-view repair, regenerate source candidates from audit with `tools\rerun_questionable_records.py`, then staged-rerun only rows whose `reason` contains `遠景`.
- Never feed `_drive_upload\drive_upload_review_required.csv` paths directly to OCR as source paths; those paths point to already renamed flat output photos, not the original source folders.
- `tools\ocr_upload_watchdog.ps1` treats `rerun_staged_candidates.py` as active OCR work. While staged rerun is active, watchdog may keep Drive upload alive but must not start recursive OCR, auto questionable rerun, or OCR stall recovery.

## 2026-07-08 current-year priority gate

- Current/future years must be fully cleared for upload before older folders continue. First-pass copied output is not enough when Drive review still has current-year rows.
- `tools/recursive_ocr_flat_export.py` now pauses before older folders with `paused_reason=current_year_review_gate` if `_drive_upload\drive_upload_review_required.csv` contains current/future-year rows.
- `tools/auto_rerun_questionable_after_recursive.ps1` now runs current-year questionable reruns first, without `--include-older`, then runs all-year questionable passes.
- For 2026, `遠景`, no model, no price, unknown compare symbol, bad/unclear photo, and price-compare failures are blocked from Drive until rerun/repair/manual correction clears the manifest.

## 2026-07-08 FollowMe/staged-rerun handoff

- `samsung_ocr_batch_processor.py` now treats foreground FollowMe physical clues as a single-unit candidate even when background TVs/QLED/OLED displays make the scene look busy. True display-wall distant views still stay `遠景` when there is no unique foreground subject.
- `skills/batch_orchestrator.py` now standardizes FollowMe model strings before UI state, CSV, and Label-Studio JSON export. Keep this guard; otherwise `FOLLOWME PRO...` can leak into saved records even when backend logic normalized it.
- Smoke test result before starting priority rerun: `M-台中市-太平區-TK3C-太平-1256.jpg` stayed `遠景/null/null`; `M-台中市-南　區-TK3C-台中旗艦-1453.jpg` became `單機/FollowMe M7 32"/null`, so it is no longer a false distant view but remains blocked from Drive until price/model confidence is resolved.
- Active 2026 priority rerun was started with `tools\rerun_staged_candidates.py --backend-url http://127.0.0.1:5001 --input-csv D:\00_商化\00_已OCR照片\_ocr_audit\current_year_scan_after_minicpm_20260708.csv --execute`. Logs: `logs\priority_2026_all_qwen3_aux5001_20260708_210327.out.log` and `.err.log`. Summary: `D:\00_商化\00_已OCR照片\_ocr_audit\priority_2026_all_qwen3_aux5001_summary_20260708_210327.csv`.
- Let the 2026 priority rerun finish or abort by guard. Do not launch older-year recursive OCR while this rerun is active.

## 2026-07-08 FollowMe Staged Rerun Patch

- `tools\rerun_staged_candidates.py` now performs conservative FollowMe rescue before the abort check: obvious foreground FollowMe false-distant results become `單機` with an inferred FollowMe family model, while missing prices remain blocked by current-year upload guards.
- The tool also removes `_ocr_staging` folders on abort or staging-copy failure. If `D:` fills again, check stale `_ocr_staging` first.

## 2026-07-08 FollowMe With Nearby Non-Samsung Products

- User-confirmed bad sample: `M-台中市-南屯區-TK3C-台中嶺東-697.jpg` was described as LG CordZero foreground plus `Samsung Follow Me` standing display and incorrectly treated as distant view. The backend now allows Samsung FollowMe rescue even when LG text appears nearby, as long as Samsung FollowMe/standing-display evidence exists.
- 2026-07-09 verification: the corrected flat output for that sample is `M-202604-台中市-南屯區-TK3C-台中嶺東-單機-FollowMe_M7_32吋-無價格-697.jpg`. It is no longer `遠景`, but remains blocked from Drive upload because current-year FollowMe price is still missing.
- 2026-07-09 follow-up: it is not enough for the filename to be rescued. If the saved narration still says `不是 FollowMe` or `整體符合「遠景」條件`, treat the row as `critical_followme_result_conflict`, rerun/repair it, and do not upload it.

## 2026-07-08 Distant-View Quality Audit

- Do not report only that rerun processes are alive. For 2026, distant-view accuracy must be audited after each staged rerun.
- Run `tools\audit_distant_followme_risk.py --output-dir "D:\00_商化\00_已OCR照片" --year 2026 --include-medium --sample-csv "D:\00_商化\00_已OCR照片\_ocr_audit\distant_followme_risk_2026_latest_sample.csv"` and check `_ocr_audit\distant_followme_risk_2026_latest.csv/json` plus the sample CSV.
- The audit flags records saved as `遠景` while thinking/filename evidence still contains FollowMe, Samsung Follow, S32FM/S43FM, white stand/base, vertical pole, tray, side-label/model clues, or single-unit wording such as `主角是`, `一台`, `單台`, or `判斷是單機`.
- The audit also flags `critical_followme_result_conflict`: final output is FollowMe but the narration contradicts it. 2026-07-09 baseline after this fix: 303 current-year risk rows, including 275 FollowMe result/narration conflicts.
- `tools\prepare_drive_upload_manifest.py` reads the latest risk CSV and blocks listed rows with `current_year_followme_or_distant_risk_needs_rerun`. These must be treated as high-priority rerun/reupload targets, not complete deliverables.
- `tools\ocr_upload_watchdog.ps1` now refreshes the fixed latest risk CSV/JSON/sample during its 4-hour check. If risk rows remain after a staged current-year rerun, do not resume older-year recursive OCR yet.
- The sample CSV is not a rerun list. It includes high-risk rows and a deterministic sample of apparently true distant rows so another AI can estimate whether distant precision is improving after reruns.

## 2026-07-08 FollowMe Risk Rerun Waiter

- `tools\run_followme_risk_rerun_after_current.ps1` waits for the current staged rerun to finish, refreshes `distant_followme_risk_2026_latest.csv/json`, restarts only backend port 5001 so the latest FollowMe rules are loaded, and then staged-reruns just those risk rows.
- It does not interrupt an active staged rerun and does not touch the visible dashboard on port 5000.
- Current live waiter was started on 2026-07-08 around 23:38 with output logs `logs\followme_risk_waiter_*.log` and main script log `logs\followme_risk_after_current_*.log`.

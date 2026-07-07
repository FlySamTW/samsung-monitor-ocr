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

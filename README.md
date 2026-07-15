# 三星通路照片 OCR 自動辨識系統

> 使用 Qwen3-VL 視覺模型自動批次辨識三星螢幕照片的型號與價格

## 🚀 快速開始

### 1. 啟動系統
雙擊執行：run_ocr.bat

系統會自動：
- 清理快取
- 用 LM Studio CLI 啟動本機 LLM（不需要開 LM Studio 視窗）
- 啟動 OCR 伺服器 (Port 5000)
- 開啟 Dashboard (http://localhost:5000)

若只想先啟動本機 LLM，可雙擊 `start_local_llm.bat`。預設會優先載入 `qwen/qwen3-vl-8b`，找不到 8B 時改用 `qwen/qwen3-vl-4b`。

### 2. 使用 Dashboard
1. 選擇照片資料夾（如：商化照片-202512）
2. 點擊「開始執行」
3. 即時監控辨識進度

---

## 📂 核心檔案

| 檔案 | 說明 |
|------|------|
| run_ocr.bat | Dashboard / 單資料夾 OCR 啟動腳本 |
| run_recursive_ocr_flat_export.bat | 歷年照片遞迴接力與單一資料夾輸出啟動腳本 |
| start_local_llm.bat | 只啟動本機 LM Studio CLI / Qwen3-VL |
| samsung_ocr_batch_processor.py | 主程式 (v18.99) |
| samsung_ocr_prompt.txt | OCR Prompt |
| 型號表.txt | 型號清單 |
| skills/ | 功能模組 |
| dashboard/ | Web 介面 |
| tools/local_llm_manager.py | 本機 LLM 啟動與檢查 |
| tools/validate_recursive_ocr_inputs.py | 啟動前預檢來源、輸出路徑與支援照片 |
| tools/stop_ocr_server.py | 啟動前清理既有 OCR 後端，避免連到舊程式 |
| tools/run_qwen_vl_guard.py | Prompt 守門測試 |
| tools/photo_rename_planner.py | 依 OCR 結果產生照片改名計畫，預設不改照片 |
| tools/recursive_ocr_flat_export.py | 遞迴接力 OCR，完成後複製改名照片到單一資料夾 |
| tools/recursive_ocr_audit_report.py | 驗收遞迴接力輸出是否完整 |
| INSTALL_WATCHDOG_TASK.bat | 安裝每 4 小時自動續跑 OCR / 上傳的 Windows 排程 |
| docs/ai_handoff_runbook.md | 另一台電腦上的 AI 接手執行手冊 |

## 🏷️ 歷年照片改名規格

改名目標是讓檔名保留門市資料，並追加 OCR 辨識出的類別、型號、價格；流水號永遠放最後。
照片來源資料夾是外部資料，不屬於 Git 專案；每台電腦可放在不同位置，例如 `D:\00_歷年商化照片`，工具與文件不可把這個路徑寫死成唯一位置。

固定格式：

```text
M-年月-縣市-行政區-通路-店名-類別-型號-價格-原流水號.jpg
```

範例：

```text
M-202603-台中市-大甲區-SF-大甲-遠景-型號未辨識-無價格-911.jpg
M-202603-台中市-大甲區-SF-大甲-單機-S27CG552EC-↑＄4990-914.jpg
M-202603-台中市-大甲區-SF-大甲-單機-FollowMe_M7_32吋-✓＄12990-915.jpg
M-202603-台中市-大甲區-SF-大甲-單機-FollowMe_Pro_M7_43吋-？＄17990-916.jpg
```

- `年月` 優先從資料夾名稱推得，例如 `商化照片-202603` 會產生 `202603`。
- `FollowMe` 是型號，不是另一個檔名分類；類別仍以 `單機` 或 `遠景` 表示。
- `FollowMe` 型號需細分為 `FollowMe_M5_32吋`、`FollowMe_M7_32吋`、`FollowMe_Pro_M7_43吋`。
- 價格預設使用全形 `＄`，可用工具參數改成半形 `$`。
- 當年度照片若有官網比價，價格前要保留比價符號：`↑` 店內高於官網、`↓` 店內低於官網、`✓` 相同、`？` 官網未知。
- 正式改名以前，必須先產生 `rename_plan.csv`、`conflicts.csv`、`rollback.csv`。
- 大量整理時，改名後照片可全部輸出到同一個新資料夾，因為檔名已包含年月與門市資訊；若發生同名衝突，必須加尾碼避免覆蓋。
- 只處理 `.jpg`、`.jpeg`、`.png`；`HEIC`、`WebP` 可記錄為未支援並略過。
- 送本機視覺模型前，照片若大於 2K，長邊縮到 `2560`，短邊按原比例自然縮放；不裁切、不補白、不硬拉伸。例：`4000x3000` 會變成 `2560x1920`。
- 官網價格比對只適用當年度照片；以 2026 年執行時，`2025` 含以前都不做官網價格比對，但仍保留 OCR 讀到的店內價格。
- 改名工具會再依 `年月` 做一次防線：歷史年度即使舊 OCR 結果殘留 `↑/↓/✓/？`，輸出檔名仍會自動移除，只保留店內價格。

同一層輸出範例：

```text
D:\00_歷年商化照片_OCR整理\M-202605-台北市-萬華區-TK3C-萬大-單機-S27CG552EC-↑＄4990-1005.jpg
D:\00_歷年商化照片_OCR整理\M-202605-新北市-板橋區-TK3C-新埔-遠景-型號未辨識-無價格-1002.jpg
D:\00_歷年商化照片_OCR整理\M-202512-嘉義市-東區-TK3C-垂楊-單機-FollowMe_M7_32吋-＄12990-1172.jpg
```

### 遞迴接力 OCR 並輸出到單一新資料夾

另一台電腦的 Codex / AI 接手時，先讀：

```text
docs/ai_handoff_runbook.md
```

另一台電腦 pull 專案後，可雙擊：

```text
run_recursive_ocr_flat_export.bat
```

AI 或非互動環境建議用 PowerShell 先指定路徑，再跑批次檔：

```powershell
$env:OCR_SOURCE_ROOT = "D:\你的照片根資料夾"
$env:OCR_OUTPUT_DIR = "D:\你的照片根資料夾_OCR整理"
$env:OCR_NO_PAUSE = "1"
.\run_recursive_ocr_flat_export.bat
```

輸出資料夾不可等於來源資料夾、不可放在來源資料夾底下、也不可是來源資料夾的上層；請使用來源旁邊的新資料夾，避免掃到自己輸出的照片或混入無關檔案。
若輸出資料夾第一層已經有 jpg/jpeg/png，但沒有 `_ocr_audit\folder_summary.csv`，批次檔會先擋下來；請改用新的輸出資料夾，或先移開既有照片。

此工具會從最新月份往前處理含子資料夾的照片，使用既有 Dashboard/Flask 後端 API 跑 OCR，完成後把改名照片複製到同一層輸出資料夾。審計檔會放在輸出資料夾的 `_ocr_audit`。
批次檔啟動前會先預檢來源、輸出路徑與是否至少有一張 `.jpg/.jpeg/.png`；預檢失敗時不會啟動 LLM 或 OCR 後端。預檢通過後才清理既有 `samsung_ocr_batch_processor.py` 後端，避免連到舊程式或被舊程序佔用連接埠。
遞迴接力結束後會自動清理本次 OCR 後端；若需要保留後端觀察狀態，可先設定 `$env:OCR_KEEP_SERVER = "1"`。
接力器預設會續跑：已成功複製、來源照片數與最新修改時間未變、且目標檔案仍存在的資料夾會標為 `skipped_existing`，避免重跑時產生 `_2` 重複檔。

`run_recursive_ocr_flat_export.bat` 跑完會自動執行驗收工具；只有手動拆開執行 Python 接力器，或要重驗舊輸出資料夾時，才需要另外執行：

```powershell
.\.venv\Scripts\python.exe tools\recursive_ocr_audit_report.py `
  --output-dir "D:\你的照片根資料夾_OCR整理"
```

價格符號規則的最小回歸測試：

```powershell
.\.venv\Scripts\python.exe tools\test_photo_rename_planner.py
```

驗收通過時會顯示 `status=passed`，摘要會寫到 `_ocr_audit\audit_summary.json`，內含驗收時間、審計檔路徑與主要數量；若失敗，批次檔會停在錯誤狀態，細節會寫到 `_ocr_audit\audit_report.csv`。

只產生改名計畫，不改照片：

```powershell
.\.venv\Scripts\python.exe tools\photo_rename_planner.py `
  --image-dir "D:\00_歷年商化照片\商化照片-202603" `
  --results "runs\<本次批次>\results.csv"
```

改用半形 `$`：

```powershell
.\.venv\Scripts\python.exe tools\photo_rename_planner.py `
  --image-dir "D:\00_歷年商化照片\商化照片-202603" `
  --results "runs\<本次批次>\results.csv" `
  --price-symbol '$'
```

---

## ✅ Prompt 守門測試

修改 Prompt 或規則後，先跑快速檢查：

```powershell
.\.venv\Scripts\python.exe tools\run_qwen_vl_guard.py --quick
```

正式檢查跑完整 52 張標準答案照片：

```powershell
.\.venv\Scripts\python.exe tools\run_qwen_vl_guard.py
```

測試會確認 FollowMe、遠距 FollowMe、一般單機、遠景、3000 元以下價格排除、電信方案價、大於 3000 價格保留、五位數價格不可誤判低價、型號可讀但 3000 元以下時只清價格不清型號、多品牌價牌不可借價、LG 可移動螢幕不可算 Samsung FollowMe、Samsung Smart Monitor M5/M7 不可誤判 LG、Smart Monitor 桌上型短支架不可誤判 FollowMe、Smart Monitor 不硬配 G5、Smart Monitor 無 FollowMe 支架時不可標準化成 FollowMe、品牌名不等於型號、FollowMe 排除語句、Follow Me 4K 上牌不可誤升 Pro 43、G5/G7 型號讀取、型號尾碼錯讀校正、遠景不可救回零散價牌、非三星遠景排除與 Odyssey Ark 等規則沒有被改壞。守門工具預設會加一張「下方整條價牌帶」輔助圖，避免非置中的價牌漏讀；若個別照片失敗，會自動只針對失敗案例加下方中央放大圖重跑一次，再合併報告。

---

## 🔧 常見問題

**Q: 如何更新 Prompt？**
直接編輯 samsung_ocr_prompt.txt，然後重啟 run_ocr.bat

**Q: Port 5000 被佔用？**
run_ocr.bat 會自動清理

**Q: 系統不穩定？**
執行 run_ocr.bat 會自動清理快取

---

## 📅 更新日誌

**v18.99 (2026-03-05)** — UI 日誌去重修復
- 移除辨識紀錄區首尾重複記錄
- 移除日誌區重複的思考輸出
- 清除獨白欄多餘的「思考:」前綴
- [詳見 SAMSUNG_OCR_EXPERIENCE_SKILL.md]

**v18.75 (2026-01-30)**
- PromptManager 配置管理系統

---

版本：v19.x (Qwen-VL Prompt Guard)
更新：2026-06-06
# Dashboard Live Sync Rule (2026-07-01)

The live dashboard keeps three UI surfaces tied to the same filename:

- `current_file`: the photo currently being processed and displayed in the main preview.
- `stream_file`: the filename that owns the live LLM self-talk in `stream_buffer`.
- `latest_result_file`: the most recently completed OCR result.

The frontend must not replace the main preview with `recent_results[0]` while a new `current_file` is already active. That makes the photo change faster than the LLM self-talk/result text and creates an off-by-one display. Only update the preview when `current_file` changes, and only show `stream_buffer` when `stream_file === current_file`.

# 2026-07-02 HANDOFF - Current State And Known Issues

Read this before continuing the overnight OCR job.

Current live run:
- Backend is running on `http://127.0.0.1:5000` with model `qwen/qwen3-vl-8b`.
- Recursive runner is running in watch mode against source `D:\00_商化\00_未整理商化照片` and flat output `D:\00_商化\00_已OCR照片`.
- Last checked status: active file around `M-台南市-永康區-TK3C-鹽行-786.jpg`; runner/backend were alive.
- Hourly monitor automation exists: `samsung-ocr-hourly-monitor-and-email`; it checks progress and emails `sam.lai@live.com`.

Completed/partial output:
- 2026 root flat output had 5951 images regenerated once. Earlier bad no-price-compare output was backed up under `_bad_no_compare_2026_backup_*`.
- Do not rerun all 5951 OCR files just to fix filenames. Use audit `success_records.csv` and planner/repair scripts where possible.
- `tools/repair_current_year_price_compare_outputs.py` now preflights current-year unknown price rows before moving/copying files. It currently blocks because 202605 has 79 prices with no Samsung/PChome reference.

Critical unresolved issues (updated 2026-07-03):
- Current-year price `?`: For 2026 and future folders, if OCR has a store price but Samsung/PChome reference is unknown, the export stops and writes `price_review_required.csv`; use `--allow-no-symbol-for-unknown` only when the business rule accepts outputting 2026 records without a price symbol.
- PChome fallback: `skills/official_price.py` now tries PChome 24h Shopping after Samsung. FollowMe generic names are mapped to product codes (`FollowMe Pro M7 43"` -> `S43FM703UC`). Verify this before trusting old `？` filenames such as `FollowMe_Pro_M7_43吋-？＄12990`.
- Low price bug: The old 3000 cutoff wrongly erased real prices like `S24F332EAC / 2390`. Code was changed to allow prices >= 2000 when a Samsung monitor label is clear. Handwritten clearance/sale tags are a narrow exception: if the physical card clearly says `促銷價` / `展示出清` / `出清` / `展示機` / `福利品` / `清倉` / `特賣`, a handwritten 4-digit price such as `1999` is valid. Existing completed rows with `(無價格)` may still need rerun or thinking-text rescue.
- UI: Main preview no longer stays on the blurred 400 px thumbnail; source path now shows live backend folder. The right panel is intentionally delayed: it must not show a thumbnail's parsed result until that photo's AI 即時判讀 has finished playing.
- Distant-view classification: A stronger guard was added (`samsung_ocr_batch_processor.py`): no Samsung model + no price + thinking mentions distant-view keywords => force `view_type=遠景` and clear model/price. Already-processed misclassified rows still need repair or rerun.
- 91 null-model candidates remain after two targeted reruns; 8 S27CG552EC records have store prices much higher than the PChome reference price (4990) and need manual review.
- Black-screen / unclear detections (`screen_status`, `quality_issue`) are not yet reflected in output filenames; naming rule and `photo_rename_planner.py` need updating.
- Duplicate codex-runtime Python child processes persist on this machine; the long-running batch is currently started via a Windows scheduled task `SamsungOCR_ResumeBatch` as a workaround.

Next recommended order:
1. Keep the live qwen3-vl-8b run alive; restart only to load backend/dashboard code changes.
2. If changing dashboard presentation again, verify that preview image, self-talk, and right-panel results stay sequential: the current photo must not appear in `辨識紀錄` until its self-talk finishes.
3. Finish guard fixes for black-screen/unclear filename tagging.
4. Run focused reruns for remaining null-model candidates if token budget allows, or mark them as distant-view/不合格 after sampling.
5. Resolve the 8 S27CG552EC price-mismatch rows manually.
6. Re-run `tools/repair_current_year_price_compare_outputs.py --dry-run`; only run non-dry when preflight passes or review CSV has been manually resolved.
7. Update docs/tests, then commit/push.

Google Drive upload handoff:
- Target parent folder: `https://drive.google.com/drive/folders/1xBaWDRjlcP-gMV-bM0K1S4gOJZ0QJJHK`
- Folder policy: year folders only (`2026`, `2025`, ...), no month folders. Cross-month search relies on the full renamed filename.
- Prepare upload manifests with: `python tools/prepare_drive_upload_manifest.py --output-dir D:\00_商化\00_已OCR照片 --limit-ready 25`
- The script writes `_drive_upload\drive_upload_ready.csv`, `_drive_upload\drive_upload_review_required.csv`, `_drive_upload\drive_upload_next_batch.csv`, `_drive_upload\staging_map.csv`, and `_drive_upload\drive_upload_summary.json`.
- Only upload rows from `drive_upload_next_batch.csv` / `staging_map.csv`. Do not upload `review_required` rows; they need rerun or manual review first.
- Current-year upload is globally closed until the full-year v19.45 finalization proof, risk audit input SHA-256, manifest gate, and exact next-batch SHA-256 all match. The uploader rechecks this after every manifest rebuild and before staging/rclone; an empty candidate CSV alone is never completion proof.
- Upload batches are newest-period first (`2026` before `2025` before `2024`). Filenames containing `無型號` are review rows and must not be uploaded until corrected/rerun.
- Record completed uploads in `_drive_upload\drive_upload_uploaded.csv`; the next manifest run skips those files so uploads can resume safely on another machine.
- Stale uploaded corrections are never deleted directly from the manifest. After the 2026 evidence backfill and a fresh manifest, rebuild the UTF-8 replacement ledger with `python tools/build_drive_correction_reconciliation.py --output-dir D:\00_商化\00_已OCR照片` (dry-run first). `--execute` may write a structurally valid local ledger for read-only discovery, but `safe_to_upload_new` and `safe_to_replace` remain separate closed gates. Missing historical Drive IDs use `reconcile_drive_corrections.py --execute --phase discover-old`; `upload-new` requires `new_ready`, and upload/readback must verify ID, size, and MD5 before the separate recoverable-trash phase.

Manual review panel:
- The dashboard has a separate `待人工校正` drawer for rows blocked by `_drive_upload\drive_upload_review_required.csv`; keep it out of the main boss-facing monitor unless someone opens it intentionally.
- The panel is a review inbox, not a bulk rename engine. `記錄` appends corrections to `_ocr_audit\manual_corrections.csv`; `學規則` also appends to `_ocr_audit\manual_learning_rules.csv`; `標記重跑需求` records that a safe rerun candidate should be generated.
- Quick ARK fill sets `view_type=單機`, `model=S55BG970NC`, and a reusable rule hint for Odyssey Ark / Ark Mini LED / 55-inch upright or curved desk displays.
- Apply recorded manual corrections with `python tools/apply_manual_review_corrections.py --output-dir D:\00_商化\00_已OCR照片` first; it is dry-run by default and writes `_ocr_audit\manual_correction_rename_plan_*.csv`. Add `--apply` only after checking the plan.
- 2026 OCR/export can be complete while Drive upload remains partial. In that case, inspect `drive_upload_review_required.csv` and the dashboard review drawer instead of rerunning all 2026 folders.

# 2026-07-03 Portable Resume

For another PC or another AI agent, start from `docs/handoff_2026_ocr_resume.md`.
The repo intentionally includes only the small portable sample set at `samples/ocr_demo_50`; do not add the full production photo folders or generated output folders to Git.

# 2026-07-04 Operator Notes

- Historical build at the time of this note was `v19.14`; see the latest section below for the current UI contract.
- The user-facing flow must look sequential: main photo appears, AI 即時判讀 types out, then the thumbnail/result is revealed in `辨識紀錄`.
- The backend may process the next photo early, but the UI must not show that parsed result before its AI narration finishes.
- The lower-left log area is intentional and must keep the historical AI record visible (`[THINK]` and final classification lines). Filter only internal noise such as initialization/debug/JSON errors.
- Google Drive upload is handled by rclone remote `samsung_ocr_drive`; use year folders only (`2026`, `2025`, ...).
- Odyssey Ark / Ark Mini LED 55-inch upright or curved desk displays are treated as `S55BG970NC`; do not borrow nearby S27/S32 small-monitor labels.
- Non-Python upload entrypoint: `UPLOAD_READY_PHOTOS_TO_GOOGLE_DRIVE.bat`.
- rclone upload batches have a timeout guard; if one batch stalls, restart with `tools\rclone_drive_upload.py --execute --repeat --limit 100 --rclone-timeout-seconds 1200`.
- Missing-result rerun candidate builder: `tools\build_missing_result_rerun_candidates.py`.
- A corrupted image or unresolved `missing_result` must not stop a whole rerun/export batch. The scripts now write unsafe rows to `blocked_after_rerun.csv` / `blocked_after_recursive.csv` and continue copying safe rows.
- After a long `missing_result` rerun is already active, use `tools\continue_after_missing_rerun.ps1` as the unattended bridge: it waits for the rerun to finish, safely restarts the backend to load current code, runs recursive flat export in resume mode, audits the output, then resumes rclone Drive upload for ready rows.
# 一般使用者入口

不用 Codex、不用手打 Python 指令：

1. 第一次使用先雙擊 `SETUP_FIRST_TIME.bat`
2. 平常開 dashboard 雙擊 `START_OCR.bat`
3. 要整批遞迴 OCR 並輸出平面照片，雙擊 `START_FULL_AUTO_OCR.bat`
4. 查目前狀態，雙擊 `CHECK_STATUS.bat`
5. 要讓電腦每 4 小時自動檢查並安全續跑 OCR/上傳，雙擊 `INSTALL_WATCHDOG_TASK.bat`

設定來源與輸出資料夾請改 `user_settings.cmd`。完整說明見 `docs/user_quick_start.md`。

# 2026-07-06 Dashboard Progress And Sync Notes

- Current dashboard build is `v19.18 (同步防呆)`.
- The header shows global OCR progress from `/api/status.overall_progress`: total processed photos, total source photos, remaining photos, completed folders, and current folder progress.
- Do not read the old current-folder counter as the whole project count. It is only the active folder.
- The live monitor keeps preview photo, displayed self-talk, and right-side result scoped to the same queue key.
- If OCR runs faster than the display animation, the dashboard may fast-forward stale display-only queue items and trim long self-talk so the main preview does not look frozen.
- This fast-forward is visual only. It must never delete OCR records, copied output photos, or audit rows.
- Google Drive upload progress is separate. Check `D:\00_商化\00_已OCR照片\_drive_upload\drive_upload_summary.json` for uploaded, ready-pending, and review-required counts.
- Unattended production machines should install `SamsungOCR_PipelineWatchdog` with `INSTALL_WATCHDOG_TASK.bat`. It runs every 4 hours, preserves existing audit/output state, restarts only missing OCR/upload helpers, and never uses `--no-resume`.

### Critical presentation regression gate

After any dashboard/backend presentation change, run
`.\.venv\Scripts\python.exe tools\run_critical_regressions.py` and
`npm.cmd --prefix dashboard run build`. With the local runtime already up,
also verify at least 3 complete photo -> AI -> revealed-card transitions and
retain `logs/ui_sync_v1944_live.json` plus `logs/ui_sync_v1944_live.png`.

# 2026-07-14 Compact Status And Clean Dashboard

- The operator-facing result rail shows only the thumbnail, filename, concise model/price/status and normal badges. Retry reasons, internal model ids, timestamps, decision codes, previous-pass summaries and expanded pass history belong in the click-through inspection view.
- Missing legacy pass metadata is hidden. The dashboard must never display placeholder chains such as `第 未提供 輪 · 未提供 · 未提供`.
- `/api/status` uses the bounded `compact-v2` contract after the next safe backend boundary: at most 12 live presentation events (hard maximum 24), no inline image/base64/raw evidence, and a response below 500 KB. Full pass history is loaded from `/api/presentation_history/<source_item_id>` only when requested.
- `tools/safe_backend_boundary_upgrade.ps1` waits for the complete active watcher plus two consecutive idle/complete/no-worker observations, atomically migrates the legacy repo-root v19.45 trace into `_ocr_audit` with stable source identities, verifies the port-5000 process tree, restarts only the backend, then validates compact status, history API and frontend fingerprint. Before releasing its interlock it builds and starts the resumable 2026 full-year evidence backfill. Any unresolved trace/source row blocks the restart or backfill and retains the interlock.
- The left AI panel consumes only a stream whose `stream_file` exactly matches `current_file`; when the live stream is temporarily empty it retains the latest completed narration. Legacy 200-event status payloads hydrate only the newest event so historical replay cannot hide the current AI output.
- A frontend-only repair can stay live during OCR: build to staging, place hashed assets first, and replace `dashboard/dist/index.html` last. Do not empty the live asset directory while the monitor is open.

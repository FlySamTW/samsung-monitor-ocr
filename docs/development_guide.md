# 開發手冊

本專案目標是用 LM Studio 本機視覺模型辨識 Samsung 通路陳列照片，輸出類別、型號、價格，再用結果輔助人工審核與歷年照片檔名整理。

## 重要原則

1. `samsung_ocr_prompt.txt` 是 Qwen 正式提示詞，已經多輪調整，不可大幅簡化。
2. `samsung_ocr_prompt_opencode_go.txt` 只給雲端 OpenCode Go 路徑使用，不是本機 LM Studio 正式路徑。
3. `tools/direct_ocr_batch.py` 曾用來快速完成批次，但內建提示詞較簡化；正式改名流程要優先使用 `samsung_ocr_batch_processor.py` 產出的 `results.csv`。
4. 修改 Prompt、型號表、FollowMe 規格表或後處理邏輯後，先跑守門測試。
5. 照片檔名改名必須先產生計畫表，不可直接裸改。
6. 照片來源根目錄是部署環境設定，不是專案常數；同一個 Git 專案在不同電腦上可能對應不同的歷年照片根目錄。

## 檔名規格

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

規則：

1. `年月` 放在 `M` 後方，例如 `M-202603-...`。
2. 門市資料必須在辨識結果前方。
3. 原流水號放最後。
4. `FollowMe` 是型號，不是檔名分類；類別仍用 `單機` 或 `遠景`。
5. `FollowMe` 必須細分：`FollowMe_M5_32吋`、`FollowMe_M7_32吋`、`FollowMe_Pro_M7_43吋`。
6. 型號讀不到寫 `型號未辨識`；價格讀不到寫 `無價格`。
7. 價格預設用全形 `＄`，必要時可改半形 `$`。
8. 當年度有官網比價時，價格前要保留 `↑/↓/✓/？`；歷史年度不比價時不加符號。
9. Windows 檔名不可用字元需清理，雙引號改成 `吋`，空白改成 `_`，半形 `?` 要轉成全形 `？`。
10. 批量整理輸出可全部放同一層新資料夾；年月已在檔名中，若同名則加尾碼，不可覆蓋。
11. `HEIC`、`WebP` 目前可不處理；接力或審計工具應列出略過數，不可把略過檔案算進完成率。

## 歷年接力規則

另一台電腦上的 AI 要先讀 `docs/ai_handoff_runbook.md`。那份文件是正式執行清單；本節只記錄開發規則。

1. 正式批次應沿用既有 Dashboard / Flask 後端機制，不重寫 OCR 核心。
2. 接力器應使用 `/api/set_work_dir`、`/api/start_batch`、`/api/status` 逐一切資料夾、繼續執行、等待完成。
3. 處理順序由最新月份往前，例如 `商化照片-202605`、`商化照片-202604`、`商化照片-202603`。
4. 2K 縮放定義為：大於 2K 時長邊縮到 `2560`，短邊按原比例自然縮放；不裁切、不補白、不硬拉伸。例：`4000x3000` 會變成 `2560x1920`。
5. 官網價格比對只適用執行當年度照片；以 2026 年執行時，`2025` 含以前只做 OCR 與改名，不做 Samsung 官網價格比對。
6. 改名階段必須用 `period` 再擋一次；歷史年度就算 OCR 結果有 `price_symbol`，檔名也不得輸出 `↑/↓/✓/？`。
7. 年度判斷應從資料夾或檔名年月取得，不可用外部照片根路徑推斷。

正式接力入口：

```powershell
$env:OCR_SOURCE_ROOT = "D:\你的照片根資料夾"
$env:OCR_OUTPUT_DIR = "D:\你的照片根資料夾_OCR整理"
$env:OCR_NO_PAUSE = "1"
.\run_recursive_ocr_flat_export.bat
```

價格符號規則改動後，至少跑：

```powershell
.\.venv\Scripts\python.exe tools\test_photo_rename_planner.py
```

批次檔會先預檢來源、輸出路徑與是否至少有一張 `.jpg/.jpeg/.png`；預檢通過後才清理既有 `samsung_ocr_batch_processor.py` 後端，再啟動 LM Studio 檢查、OCR 後端、接力器與輸出驗收。接力結束後預設清理本次 OCR 後端；需要保留時設定 `OCR_KEEP_SERVER=1`。若要拆成手動兩步，照 `docs/ai_handoff_runbook.md`；不要只單跑 `tools/recursive_ocr_flat_export.py` 後就以為後端會自動啟動或自動驗收。

接力器會把審計檔寫到輸出資料夾的 `_ocr_audit`，包含 `folder_discovery.csv`、`skipped_unsupported.csv`、每個資料匣的 `rename_plan.csv` 與總表 `folder_summary.csv`。
輸出資料夾不可等於來源根資料夾、不可放在來源根資料夾底下、也不可是來源根資料夾的上層，避免重跑時掃到自己輸出的照片或混入無關檔案。
輸出資料夾第一層若已有 jpg/jpeg/png，但沒有 `_ocr_audit\folder_summary.csv`，預檢會擋下來；這代表該資料夾不像可續跑的正式輸出資料夾。
接力器預設使用 `_ocr_audit\folder_summary.csv` 與各資料匣的 `copied.csv` 續跑；已完整複製、來源照片數與最新修改時間未變、且目標檔案仍存在的資料匣會標成 `skipped_existing`，不再重跑 OCR 或複製 `_2` 檔。除非使用新的輸出資料夾，否則不要輕易使用 `--no-resume`。
`run_recursive_ocr_flat_export.bat` 會在接力器跑完後自動用 `tools\recursive_ocr_audit_report.py --output-dir <輸出資料夾>` 驗收；通過才回報全量完成。驗收摘要在 `_ocr_audit\audit_summary.json`，內含驗收時間、審計檔路徑與主要數量；失敗時看 `_ocr_audit\audit_report.csv` 找阻塞資料夾或缺檔。

## 改名工具

只產生計畫，不改照片：

```powershell
.\.venv\Scripts\python.exe tools\photo_rename_planner.py `
  --image-dir "D:\00_歷年商化照片\商化照片-202603" `
  --results "runs\<本次批次>\results.csv"
```

輸出：

- `rename_plan.csv`：所有照片的新舊檔名、狀態與來源。
- `conflicts.csv`：重名或既有目標檔名衝突。
- `rollback.csv`：正式套用後的還原清單；dry run 時會是空表。

正式原地改名時才加：

```powershell
--apply
```

套用前必要檢查：

1. `missing_result=0`。
2. `missing_source=0`。
3. `conflict=0`。
4. 抽查至少 20 張 `rename_plan.csv`，確認門市資料、型號、價格與流水號位置正確。

## 模型評估

正式跑歷年照片前，先用固定驗證集比較模型，不要直接換模型跑全量。

建議比較：

1. 基準：目前正式本機模型 `qwen/qwen3-vl-8b`。
2. 候選：新的 8B 視覺模型，例如 LM Studio 可載入的 Qwen3-VL 8B。

控制變因：

1. 同一批照片。
2. 同一份 `samsung_ocr_prompt.txt`。
3. 同一套後處理。
4. `temperature=0`。
5. 同一套改名工具。

評估項目：

1. `view_type/category` 是否正確。
2. 型號是否逐字正確。
3. 價格是否正確。
4. `FollowMe` 是否能正確細分。
5. 是否出現自信錯讀；自信錯讀比 `型號未辨識` 更嚴重。

## 守門測試

快速檢查：

```powershell
.\.venv\Scripts\python.exe tools\run_qwen_vl_guard.py --quick
```

完整檢查：

```powershell
.\.venv\Scripts\python.exe tools\run_qwen_vl_guard.py
```

守門失敗時，不要直接改全域 Prompt；先確認是否是模型版本、圖片裁切、後處理或特定案例標準答案問題。
# Dashboard Live Sync Contract (2026-07-01)

When changing the live dashboard, keep the preview, LLM self-talk, and OCR result scoped by filename:

- `current_file` is the active photo and owns the main preview.
- `stream_file` is the active photo that owns `stream_buffer`.
- `latest_result_file` is only the newest completed result for history/side panels.

Never use `recent_results[0]` to drive the main preview during a running batch. It is normally the previous completed image, while `current_file` has already advanced to the next image. The frontend should change the preview only when `current_file` changes, and should blank live self-talk unless `stream_file === current_file`.

# 2026-07-02 Development Handoff

## Code Changes Already Made

- `tools/photo_rename_planner.py`
  - Current-year price symbols are allowed only for current/future periods.
  - Legacy `discontinued` / `-` maps to `？`; filenames must never contain `停產`.
  - `遠景` filename format was changed to omit model and price: `M-period-store...-遠景-serial.jpg`.

- `skills/official_price.py`
  - Lookup failure no longer means discontinued.
  - PChome 24h Shopping fallback was added after Samsung lookup.
  - FollowMe generic names are mapped to concrete query codes, especially `S43FM703UC` for FollowMe Pro 43.

- `samsung_ocr_batch_processor.py`
  - Low-price filter changed from `<=3000` to `<2000`.
  - Prompt text was updated so clear Samsung monitor labels may keep prices >= 2000.
  - Handwritten clearance/sale exception was added: a physical card with `促銷價`, `展示出清`, `出清`, `展示機`, `福利品`, `清倉`, or `特賣` may keep a handwritten 4-digit price such as `1999`; plan/monthly/accessory keywords still block it.

- `dashboard/src/App.jsx`
  - Rerun button text changed from icon to `重跑`.
  - Price compare badge should render only when `price_symbol` exists.
  - Unknown price tooltip says Samsung/PChome lookup needs confirmation.
  - `辨識紀錄` must be delayed until the photo's LLM self-talk has finished typing. Do not show parsed thumbnail results for the current queue item while its self-talk is still playing.

- `tools/repair_current_year_price_compare_outputs.py`
  - Repairs existing current-year outputs using audit records without rerunning OCR.
  - Preflights unknown current-year prices before moving/copying output.
  - Rescues prices from thinking text when JSON price was cleared by old logic.

- `tools/recursive_ocr_flat_export.py`
  - Watch mode exists.
  - Current/future rows with store price but `price_status=unknown` now write `price_review_required.csv` and block copy.

- `tools/prepare_drive_upload_manifest.py`
  - Builds safe Google Drive upload batches from the flat OCR output folder.
  - Excludes internal `_` folders and questionable filenames, stages the next batch as ASCII `upload_0001.jpg` files, and uses `_drive_upload\drive_upload_uploaded.csv` as the resume/duplicate guard.
  - Keep Drive organization year-only (`2026`, `2025`, ...); filename carries month/store/search detail.

## Remaining Work

1. Add a stronger distant-view guard in backend and regression helper:
   - If no Samsung model is found and only an isolated price exists, do not call it `單機`.
   - If thinking mentions display area, many monitors, no spec label, poster, ad stand, or unclear store wall, set `view_type/category=遠景` and clear price.
   - Known bad sample: `M-台南市-永康區-TK3C-中華-362.jpg`.

2. Repair existing 2026 outputs:
   - Current dry-run blocks on 202605 with 79 unknown reference prices.
   - Resolve by improving PChome fallback/mappings or writing review CSV for manual values.
   - Only run non-dry export after dry-run passes.

3. Rerun focused bad classes, not all photos:
   - `model + 無價格` where thinking contains a price.
   - `(無型號) + price`.
   - current-year `？＄` filenames.

4. Model comparison is incomplete:
   - `qwen/qwen3-vl-8b` is currently running and works.
   - User reported Gemma 4 12B QAT and Qwen3.5 9B VLM downloads were still in progress.
   - Do not claim those newer models were tested until real local runs are completed.

## Verification Required Before Handoff Completion

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe -m py_compile samsung_ocr_batch_processor.py skills\official_price.py tools\recursive_ocr_flat_export.py tools\repair_current_year_price_compare_outputs.py
.\.venv\Scripts\python.exe tools\test_photo_rename_planner.py
npm.cmd --prefix dashboard run build
.\.venv\Scripts\python.exe tools\repair_current_year_price_compare_outputs.py --output-dir "D:\00_商化\00_已OCR照片" --period-prefix 2026 --dry-run
```
## 2026-07-03 Resume Development Notes

- Portable handoff entrypoint: `docs/handoff_2026_ocr_resume.md`.
- Demo/regression photos: `samples/ocr_demo_50/photos`.
- Expected labels: `samples/ocr_demo_50/labels.json`.
- Do not commit production photo folders, generated flat output, audit backups, temporary rerun CSVs, or logs.
- Dashboard no-blur rule: never enlarge `thumb_b64` in the main preview. Show a loading/placeholder state until a full image URL loads.
- Backend result records should include `source_path` so cross-folder dashboard queue entries can load the full source image.
- `tools/rerun_questionable_records.py` supports `--input-csv` for safe resume from a filtered candidate list.

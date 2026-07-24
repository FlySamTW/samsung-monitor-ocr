# 開發手冊

本專案目標是用 LM Studio 本機視覺模型辨識 Samsung 通路陳列照片，輸出類別、型號、價格，再用結果輔助人工審核與歷年照片檔名整理。

## 最高鐵律：固定本機程式自行保證正確

1. 優先序永久固定為：**照片辨識正確性 ＞ 節省 OpenAI／Codex tokens ＞ 完成時間**。
2. 正確性必須由本專案寫好的固定本機程式、證據契約、最多三輪無記憶守門、終局 invariant、逐張上傳收據與永久回歸測試自行保證；不得把 Codex／OpenAI 的逐張看圖、逐張 OCR、每日改規則或人工救火設計成正式流水線的一部分。
3. 正式逐張 OCR 只能由 LM Studio 的本機模型與本機腳本執行。Codex 只負責修正系統性根因、執行回歸驗證、每日 09:00／21:00 讀取本機產生的精簡摘要，以及本機腳本主動報出異常時介入；不得用 OpenAI/Codex 代跑整批照片。
4. 若每次監督都抓到相同類型錯誤，代表程式尚未具備穩定能力，必須在照片邊界修正共因並加入可重現的永久測試；不得只修當張、清除警告、重跑整批或等待下一次監督再次發現。
5. 健康監督本身必須低耗：正常狀態由本機常駐腳本自行續跑、逐張上傳並寫精簡 KPI；不得反覆把完整 `/api/status`、LLM 日誌、圖片或歷史對話送入 Codex。只有異常摘要才喚醒 Codex，且不得以節省 tokens 為理由放寬內容正確性。
6. `samsung_ocr_prompt.txt` 是以 Qwen 2.5 實拍照片多輪迭代、已證實能維持高準確率的基準資產。更換 LLM（包含 Qwen 3 VL 8B）時，責任在程式端證明新模型能承接這套語意：必須用固定、版本化的代表照片集，比對「Git 中最後一個已證實高準確 Prompt／模型基準」與「同 Prompt／新模型」的 view、完整螢幕數、FollowMe、型號、價格、欄位歸屬及終局上傳結果。未證明不退步前不得上正式批次。
7. 禁止為迎合新模型而大幅重寫、縮短或另造一套正式 Prompt；禁止以少數個案、速度或 token 長度作為覆蓋基準 Prompt 的理由。若新模型準確率下降，先從 Git 找回最後一個已證實高準確版本，再做最小且可回歸的 context／輸出格式相容調整。任何 Prompt 變更都要保留舊版、差異、固定樣本結果與可回退 commit。

## v19.37 關鍵執行規則

1. OCR 請求必須符合 LM Studio 實際設定的 context。`samsung_ocr_prompt.txt` 是維護來源，不保證全文都能放進 8K 模型；送出請求時一律走 `build_runtime_system_prompt()`。
2. 當年度不確定照片寧可維持「單機待複核」，不可只因為描述出現展示區、貨架或多台螢幕就轉成遠景。錯誤遠景會清掉後續複核最需要的型號與價格線索。
3. 已確認有 FollowMe 產品或支架線索的結果，後段遠景備援不得覆蓋。
4. Dashboard 的展示佇列只屬於一個 live batch。啟動新批次要清空，idle API 也不可回傳舊 staging 紀錄；任何修改都要驗證「照片 -> AI 判讀 -> 同一張右側卡片」。
5. 修改 OCR 守門或展示流程後，至少跑 `tools/test_runtime_safety_guards.py` 與 dashboard production build。

## 固定防走偏節奏

這不是建議，而是每次接手、修復、續跑與每半天完整監督都必須執行的清單：

1. 動手前重讀本手冊、`docs/continuity_handoff.md` 與 `docs/three_layer_accuracy_gate.md` 的相關章節；先確認目前正式工作目錄、守門修訂碼、保留中的 lock／fuse，以及不可改動的 Dashboard 版面與上傳隔離條件。
2. 修正前先寫清楚「觀察到的錯誤、真正根因、不可退步的既有規則」。不得為解一個畫面或個案而放寬三層證據、把上一輪答案帶進下一輪，或改變已定稿的 50/50 主畫面配置。
3. 修正後必須做相稱的回歸驗證；涉及 OCR／守門時比對同一獨立輪次的原始結構欄位與最終欄位，涉及 Dashboard 時核對主圖、自然語句 AI 判讀、右欄處理中卡與頂部目前檔案屬於同一張照片。只看到進度增加不算健康。
4. 正式運行中只在每日 09:00、21:00 做一次四維完整核對：進度確實前進、內容沒有跑歪、介面持續同步且無裸 JSON／亂碼、逐張上傳持續閉環。中間不重複輪詢、不洗版；只有發現卡住、介面不同步、記憶污染、超過三輪或系統性內容錯誤才立即通知並依照片邊界 fail-safe。自動監督的名稱、提示與文字編碼也屬健康範圍；不得把舊 `evidence_guard_revision` 寫死，必須先讀本手冊與現行程式定義再判斷。
5. 發現實質結構改寫、跨輪記憶污染、原始／最終內容漂移、介面照片與判讀錯配、右欄不累積、重複瀏覽器分頁、可見終端機反覆彈出、runtime fuse 或未授權 uploader 時，立即停止該批 runner 並保留 backend／既有 Dashboard 供查證；不得自動重啟掩蓋問題。
6. 每次查到新的根因、守門例外或舊錯重犯路徑，都要在同一修正中同步更新本手冊、移交文件或專案 SKILL，並新增能重現該錯誤的測試。文件內容若與實際程式行為不同，以失敗封閉處理，先停下核對，不可猜測後繼續跑。

## 重要原則

1. `samsung_ocr_prompt.txt` 是 Qwen 正式提示詞，已經多輪調整，不可大幅簡化。
2. `samsung_ocr_prompt_opencode_go.txt` 只給雲端 OpenCode Go 路徑使用，不是本機 LM Studio 正式路徑。
3. `tools/direct_ocr_batch.py` 曾用來快速完成批次，但內建提示詞較簡化；正式改名流程要優先使用 `samsung_ocr_batch_processor.py` 產出的 `results.csv`。
4. 修改 Prompt、型號表、FollowMe 規格表或後處理邏輯後，先跑守門測試。
5. 照片檔名改名必須先產生計畫表，不可直接裸改。
6. 照片來源根目錄是部署環境設定，不是專案常數；同一個 Git 專案在不同電腦上可能對應不同的歷年照片根目錄。

## 全案終點與跨年度接續

- 2026 複核完成只代表當年度階段完成，不是專案完成。正式終點是來源盤點內全部支援照片都有如實終局結果與 Google Drive 精確收據；現行基準為 `151,714` 張、`137` 個資料夾。
- 當年度依月份完成後，必須自動接續 2025、2024，逐年往前至 2015。歷史年度仍要辨識遠景／單機、型號與店內價格，但不做 2026 官網價格比對，也不輸出 `↑/↓/✓/？`。
- 不得把「2026 已完成」顯示或報告成「全案已完成」。總盤、守護程序與完成收據都必須以全年度 frozen inventory 為權威；只有全部年度都閉環才可解除全案接續請求。
- 現行 202601–202605 staged runner 結束後，五分鐘隱藏 continuity supervisor 必須接手歷史年度；不得因當年度 runner 自然退出而把 Dashboard 留在待機。若歷史接續尚未取得內容綁定 receipt，狀態只能是「等待安全接續」，不能宣告完成。

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
5. `FollowMe` 有足夠同主體實體證據時必須保留產品家族；型號有兩輪一致證據才細分為 `FollowMe_M5_32吋`、`FollowMe_M7_32吋`、`FollowMe_Pro_M7_43吋`。若只確認產品家族但版本不一致，寫 `FollowMe_型號未細分`，不可退回遠景，也不可猜版本。
6. 型號讀不到寫 `型號未辨識`；價格讀不到寫 `無價格`。
   - 例外：若重新辨識後確認主角是非三星螢幕，型號欄寫 `它牌(品牌)`，例如 `它牌(ACER)`、`它牌(ASUS)`、`它牌(LG)`；不需要也不應填它牌實際型號。
7. 價格預設用全形 `＄`，必要時可改半形 `$`。
8. 當年度有官網比價時，價格前要保留 `↑/↓/✓/？`；歷史年度不比價時不加符號。
9. Windows 檔名不可用字元需清理，雙引號改成 `吋`，空白改成 `_`，半形 `?` 要轉成全形 `？`。
10. 批量整理輸出可全部放同一層新資料夾；年月已在檔名中，若同名則加尾碼，不可覆蓋。
11. `HEIC`、`WebP` 目前可不處理；接力或審計工具應列出略過數，不可把略過檔案算進完成率。
12. 當年度（2026 與未來）`遠景` 必須完成最多三輪的同圖無記憶複核；至少兩輪安全結構證據確認無唯一主角、無主角自有價牌且無 FollowMe 實體線索後，該張立即排入 Drive。不得累積整年，也不得等待未指定的人工作業。

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

When changing the live dashboard, keep the preview, AI narration, and OCR result scoped by filename:

- `current_file` is the active photo and owns the main preview.
- `stream_file` is the active photo that owns `stream_buffer`.
- `latest_result_file` is only the newest completed result for history/side panels.

Never use `recent_results[0]` to drive the main preview during a running batch. It is normally the previous completed image, while `current_file` has already advanced to the next image. The frontend should change the preview only when `current_file` changes, and should blank live AI narration unless `stream_file === current_file`.

# Dashboard Presentation Queue Contract (2026-07-06)

The live monitor is supervisor-facing, so it must look alive without mixing metadata between photos.

- `dashboard/src/App.jsx` owns a frontend presentation queue: `pendingQueue`, `activePresentation`, and `revealedResults`.
- `narrationDisplay` is the user-visible stage text and must not be cleared just because the internal typing buffer is reset. This prevents the AI narration pane from becoming a black empty block while the backend judges the next photo.
- The right panel should show `revealedResults` first while OCR is running, and may backfill older self-contained `display_queue` items below the active placeholder so long-running sessions do not lose the thumbnail stack. `recent_results` is allowed only as an idle historical fallback.
- Each completed item needs a stable queue key: `presentation_id`, then `completed_at + file_name`, then `source_path`, then `file_name`.
- Long AI narration is trimmed for display. This is presentation-only and must not alter OCR audit data.
- When the backend display queue is full and the frontend is behind, discard stale display-only queue items that no longer appear in the backend's latest queue. Otherwise the preview looks frozen on old photos.
- A watchdog clears a stale `activePresentation` if the displayed text stops advancing for several seconds.
- The main preview `<img>` must use `key={currentImage}` so image changes force a real remount.
- UI polish is a correctness gate. The expected stage rhythm is photo visible -> held or live AI narration visible -> typewriter completes -> right-side result reveal. Never trade this rhythm for a raw "latest result" jump.
- When right-side model/price/status appears, the AI label must already say the summary is complete/revealed. A "still judging" label beside a revealed result is considered out of sync.
- Do not let later `displayedBuffer` updates downgrade a revealed queue key back to a typing/live-judging label.
- Keep named pacing constants for the typewriter interval and revealed-summary hold. The live monitor must be readable, so avoid magic numbers that make AI narration flash too quickly during high-throughput batches.
- User-facing UI copy must say `AI`, never `LLM`, and must never expose the old four-character internal shorthand (`自言` + `自語`). Use labels such as `AI 即時判讀中`, `照片已切換 · 等待 AI 開始輸出`, and `本張摘要完成 · 右側結果已揭露`.
- The AI narration panel must never render blank while a batch is running. A live stream is eligible only when `stream_file === current_file`; otherwise keep the latest completed presentation narration visible. On first hydration from a legacy backend, accept only the newest event instead of replaying the full 200-event payload before showing live output.
- Asset-fingerprint reloads are enabled only after the backend reports `status_contract_version=compact-v2`. A legacy backend can retain a pre-deploy fingerprint in memory; treating that stale value as authoritative causes a 30-second reload loop and repeatedly clears the narration state.

# Dashboard Sync No-Regression Contract (2026-07-07)

This project has repeatedly regressed the live monitor. Treat the following as a hard engineering contract, not a suggestion.

- The main photo, visible AI narration, and right-side thumbnail/result must always come from the same stable queue key.
- Keep the staged time-gap illusion: while the user watches photo A and its AI narration, the backend may already process photo B. Photo B must not appear as the active top result until B's own AI narration has visibly completed.
- Do not remove the frontend-owned queue (`pendingQueue`, `activePresentation`, `revealedResults`) when optimizing speed. Raw backend `recent_results` is too fast and will desynchronize the screen.
- Never drive the main preview from `recent_results[0]` during a running batch.
- The right panel may show a pending placeholder for the active item, but it must not reveal model/price/status early.
- Do not blank, dim, or replace the main photo between items. Keep the previous full-resolution photo visible until the next full-resolution photo has loaded.
- When a batch is stopped, between folders, or idle, do not display stale backend `current_file`, `current_relative_dir`, `stream_buffer`, or old live narration as if it were active. Idle state may show history, but it must not pretend a stale photo is currently being judged.
- If you change dashboard timing, run a browser check after a rebuild: confirm the visible sequence is photo -> AI narration -> right-side result, and confirm stopping a batch clears live current photo/file instead of showing a stale old folder.
- If any change breaks this contract, revert or fix the UI before touching OCR logic or upload scripts. A visually mismatched monitor is a production bug.

# Overall Progress Contract (2026-07-06)

`/api/status` returns `overall_progress` so the dashboard can show total OCR progress across all discovered source folders.

- Backend aggregation reads `_ocr_audit/folder_discovery.csv`, `_ocr_audit/folder_summary.csv`, missing-result rerun summaries, and live active-folder stats.
- `overall_progress.next_pending_folder` must exclude `blocked`, `skipped_blocked`, and `error` rows. Expose blocked work separately as `next_blocked_folder` so the dashboard does not imply a blocked historical folder is the next runnable folder.
- Frontend must display both global progress and current-folder progress.
- Do not let the browser scan the source tree or output folder on every poll.
- Google Drive upload progress is separate and comes from `_drive_upload/drive_upload_summary.json`.

# Dashboard v19.29 Monitor Layout Contract (2026-07-07)

The dashboard is boss-facing and must remain visually stable during long runs.

- Current dashboard build is `v19.29 (AI判讀視覺微調)`.
- Never black out, dim, or collapse the main preview between photos. Keep the previous full-resolution photo visible until the next full-resolution `/api/image/<source_path>` image has loaded.
- When a batch stops or changes folders, clear active live labels/narration state, but do not forcibly set the visible photo to blank. The idle filename may say `上一張畫面保留`.
- The AI lower history pane must always contain readable history. Filter backend noise such as `圖片損壞`, `無法識別圖片格式`, `JSON Error`, stop/interruption messages, and internal queue wording. If `lm_logs` only contains noise, fall back to recent `display_queue` summaries.
- The right-side `辨識紀錄` column must remain on the right side of the desktop monitor layout, but it must not dominate the main photo. Keep it bounded around 360-430 px on normal desktop widths, with two/three-line filenames and hover titles instead of making the whole rail excessively wide. This project is operated from a GPU workstation, not a phone UI; do not add responsive rules that push the result sidebar below the preview.
- Right-side action text is `再辨識`, not `重跑`.
- `目前資料匣` must show the complete human-readable business folder name (for a review run: `商化照片-YYYYMM`). Never render the long `_ocr_staging` path as visible text and then rely on an ellipsis; keep the full technical path only in the hover title.
- Header/status/preview/narration/result-rail are one regression contract. Fixing one column must not clip the upload status, truncate the business folder label, blank the LLM narration, desynchronize thumbnails, or alter the finalized 50/50 layout. Every UI rebuild runs `tools.test_presentation_soak` and verifies all of them together in the existing tab.
- If a UI rebuild changes these surfaces, refresh the browser and verify in the real app:
  - `status-current-folder` shows the complete business folder label, while its hover title retains the full path.
  - `llm-history-log` is not blank and has no damage/noise spam.
  - `result-rail` is visible and uses `再辨識`.
  - The preview image stays present while the next image loads.

# Recursive Progress Resume Contract (2026-07-07)

`tools/recursive_ocr_flat_export.py` must preserve previously completed folder summaries when restarted.

- In resume mode, load existing `_ocr_audit/folder_summary.csv` before writing new rows.
- Merge previous summary rows with currently discovered folders; never rewrite the file with only the current run's first few rows.
- During a production run, refresh source-folder discovery between folders. The source root is live: new or moved folders must be picked up without a full restart, and the newest period/folder must remain first.
- If a bad restart already shrank `folder_summary.csv`, rebuild it with:

```powershell
.\.venv\Scripts\python.exe tools\rebuild_recursive_folder_summary.py --output-dir "D:\00_商化\00_已OCR照片"
```

- After rebuilding, restart recursive OCR in normal resume mode. Do not use `--no-resume`, do not delete audit folders, and do not stop the rclone uploader unless it is the failing process.

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
  - `辨識紀錄` must be delayed until the photo's AI narration has finished typing. Do not show parsed thumbnail results for the current queue item while its AI narration is still playing.

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
  - Pending batches must be newest-period first and must keep `無型號` rows in review until a rerun or manual correction resolves the model.

- `samsung_ocr_batch_processor.py` review APIs
  - `/api/review_queue?year=2026&limit=300` reads `_drive_upload\drive_upload_review_required.csv` and returns blocked upload rows for the dashboard `待人工校正` drawer.
  - `/api/review_correction` appends human decisions to `_ocr_audit\manual_corrections.csv`; when `learn_rule=true`, it also appends `_ocr_audit\manual_learning_rules.csv`.
  - These APIs are deliberately append-only. A separate repair/export step must consume the CSVs and rebuild safe filenames before Drive upload.
  - The ARK quick action records `S55BG970NC` for Odyssey Ark / Ark Mini LED / 55-inch upright or curved desk displays, while preserving the rule that nearby S27/S32 labels cannot be borrowed.

- `tools/rclone_drive_upload.py`
  - Uses rclone remote `samsung_ocr_drive` for large resumable uploads to the approved Google Drive parent folder.
  - Calls `tools/prepare_drive_upload_manifest.py`, uploads only `ready` rows, groups by year folder, and records uploaded filenames in `_drive_upload\drive_upload_uploaded.csv`.
  - Has a lock file at `_drive_upload\rclone_drive_upload.lock`; do not start a second uploader while it exists.

- `tools/recursive_ocr_flat_export.py`
  - Refreshes source-folder discovery before each folder handoff so newly added folders are picked up during the same long run.
  - If a folder already handled in the current run later changes image count or newest modified time, it is re-queued instead of waiting for a manual restart.

- `tools/build_missing_result_rerun_candidates.py`
  - Reads `_ocr_audit\folder_summary.csv` and emits a safe CSV for `tools\rerun_questionable_records.py`.
  - Use it for folders blocked by `missing_result` instead of restarting everything.

## Dashboard Presentation Rule

- Current production dashboard is `v19.29 (AI判讀視覺微調)`.
- Boss-facing order must remain: photo first, AI narration second, parsed thumbnail/result last.
- As soon as a photo is visible in the main preview, the top row of `辨識紀錄` must show that same photo as a `處理中 / AI 即時判讀中` placeholder until parsed metadata is allowed to appear. Do not leave the previous completed result at the top, because users read that as a metadata mismatch.
- Parsed model/price/status badges for the current photo may appear only after that same photo's AI narration has finished.
- The AI narration pane must never go blank during normal running. If the next photo is loading or the next narration has not started, keep the previous completed narration visible as a softened previous-summary state until new text begins.
- The compact live queue can become empty in the same status transition that completes a photo. Rehydrate the same-run durable presentation history whenever the completed-photo counter advances, so the final photo cannot disappear from the right rail between two browser polls. This must be event-paced by the counter change, not a new high-frequency polling loop.
- The lower-left panel must always stay presentable and must preserve the historical AI judgment record (`[THINK]` summaries and final classification lines). Never replace it with blank space or result summaries only; filter only raw `JSON Error`, initialization/debug wording, and internal playback wording.

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
   - Odyssey Ark / Ark Mini LED 55-inch upright or curved desk displays should resolve to `S55BG970NC`; never borrow nearby S27/S32 small-monitor labels.

4. Model comparison status:
   - `qwen/qwen3-vl-8b` is still the only model approved for the main OCR line.
   - `qwen3.5-9b-vlm` and `gemma-4-12b-it-qat` have local smoke-test evidence, but they are not safe replacements yet: 8K context failed, 16K can run, and throughput is much slower than Qwen3-VL 8B.
   - `MiniCPM-V-4.6` was attempted through LM Studio, but the current local server reported that the loaded model did not support image inputs in the OpenAI-compatible chat request format.
   - `tools/qwen_vl_regression.py` now checks FollowMe Pro / 43-inch clues before generic M7 clues. Older eval logs may undercount qwen3.5/Gemma because the eval helper previously normalized `FollowMe Pro M7 43"` back to `FollowMe M7 32"`.
   - Do not switch the production model unless the candidate passes the same guarded 2026 staged sample with no context errors and no suspicious collapse into `單機 / 無型號 / 無價格`.

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

## Current-Year Priority Gate

### 背景視窗與瀏覽器

正式入口 `START_OCR.bat` 與 `START_FULL_AUTO_OCR.bat` 透過 `tools/windows_user_launcher.ps1` 啟動 backend。backend 使用 hidden window，recursive 流程沿用同一個隱藏 backend；使用者只會看到瀏覽器 dashboard，不會累積 PowerShell 視窗。只有明確的互動式瀏覽器啟動保持可見。

- The production order is current/future year first, then older years.
- `tools/prepare_drive_upload_manifest.py` keeps current-year questionable rows out of Drive, including current-year distant-view rows until they have been safely rerun or corrected.
- `tools/recursive_ocr_flat_export.py` must not continue into older folders on the basis of a boolean override. The former `--ignore-current-year-review-gate` is forbidden and removed. Historical work requires the canonical content-bound `historical_continuation_receipt.json`; missing/tampered authority exits with `paused_reason=historical_continuation_gate`.
- `tools/auto_rerun_questionable_after_recursive.ps1` must run current-year questionable photos first (`--include-older` off), then run all-year questionable passes. This is the fix for the 2026 distant-view false-positive issue where first-pass completion was mistaken for final completion.
- Do not upload current-year rows that are still in `review_required`. For 2026, distant view, no model, no price, unknown compare symbol, bad/unclear photo, and current-year price-compare failures must be resolved by rerun/repair/manual correction before Drive upload.
- Slow fallback VLMs must not be launched against full 2026 source folders just to repair a subset. Use `tools\rerun_staged_candidates.py` with a filtered candidate CSV so only risky rows are copied to a temporary staging folder, processed, merged back into the original audit `success_records.csv`, and re-exported to the flat output.
- For the 2026 false-distant problem, regenerate candidates from audit state with `tools\rerun_questionable_records.py` first, then run staged rerun with `--reason-contains 遠景`. This preserves original source-folder mapping and avoids accidentally rerunning already renamed flat-output photos as if they were source photos.
- `tools\ocr_upload_watchdog.ps1` must treat `rerun_staged_candidates.py` as an active OCR job. While staged rerun is active, the watchdog may keep upload alive but must not start recursive OCR, auto questionable rerun, or OCR stall recovery.
- Staged reruns should use an auxiliary backend on port 5001 when the production dashboard on port 5000 is visible. Start `samsung_ocr_batch_processor.py --port 5001` with `SAMSUNG_OCR_NO_BROWSER=1`, then pass `--backend-url http://127.0.0.1:5001` to `tools\rerun_staged_candidates.py`. This keeps the supervisor-facing dashboard from showing `_ocr_staging` folders.
- Never merge staged rerun output if the backend logs contain model context errors such as `n_keep >= n_ctx`, `context length`, or `number of tokens to keep`. The 2026-07-08 qwen3.5 9B attempt failed at 8K context and produced false `單機 / 無型號 / 無價格` rows; it was rolled back. qwen3.5 may only be retried after loading with 16K+ context and passing the same guarded candidate sample.
- FollowMe false-distant guard: if the foreground subject has a white floor circular base, vertical pole/stand, upright white frame, tray, or a FollowMe Pro 4K / FollowMe 4K product card, do not let background QLED/OLED/TV display walls force `遠景`. The backend helper `has_positive_followme_physical_clue()` and staged rerun guard `followme_distant_risk()` protect this case. If a staged rerun turns such a candidate into `遠景 / 無型號`, abort and do not merge.
- FollowMe result names must be standardized before saving frontend/CSV/Label-Studio JSON state. `skills/batch_orchestrator.py` now normalizes variants such as `FOLLOWME PRO M7 43"` to `FollowMe Pro M7 43"` after model matching and before `recent_results`, `session_results`, CSV, and JSON export. Do not remove this write-path guard; otherwise rerun may classify correctly but save an inconsistent model name.
- 2026-07-08 active priority rerun: `tools\rerun_staged_candidates.py` was started against auxiliary backend `http://127.0.0.1:5001` with `current_year_scan_after_minicpm_20260708.csv`, output logs `logs\priority_2026_all_qwen3_aux5001_20260708_210327.*.log`, and summary `D:\00_商化\00_已OCR照片\_ocr_audit\priority_2026_all_qwen3_aux5001_summary_20260708_210327.csv`. Let this finish or abort by its own guards before resuming older recursive OCR.

## 2026-07-08 FollowMe Staged Rerun Patch

- `tools\rerun_staged_candidates.py` now rescues obvious foreground FollowMe false-distant rerun outputs before applying the abort guard: if a candidate has FollowMe/stand/base/tray evidence but the model returns `遠景 / 無型號`, it is converted back to `單機` with a conservative FollowMe family model.
- 現行 `.22` 會在最多三輪後如實定案；價格讀不到就記為無價格，該張仍立即逐張上傳，不再等待人工桶。
- The tool now removes `_ocr_staging` folders when a group aborts or staging copy fails. If `D:` becomes full again, check stale `_ocr_staging` before rerunning OCR.

## 2026-07-08 FollowMe With Nearby Non-Samsung Products

- A visible Samsung FollowMe unit must not be classified as distant view just because a nearby LG vacuum, appliance display, cashier area, or other non-monitor product is large in the frame.
- Backend helper `should_block_followme_due_to_other_brand()` now blocks LG/StanbyME false positives only when there is no positive Samsung FollowMe sign/standing-display clue. A case with `Samsung Follow Me` plus a standing display/white stand/base/tray is rescued as FollowMe even if LG text appears elsewhere in the scene.
- Do not require the classic white circular base for every FollowMe rescue. If a `Samsung FollowMe`/`FollowMe` product label is attached to a visible standing/vertical display, treat it as FollowMe review evidence even when the model says the base is not white or not visible. Do not confuse this with a pure poster/ad: the sign must be tied to a standing display/product area.
- 2026-07-09 v19.34 fix: the positive FollowMe display-sign clue must override old negative checks like `沒有白色支架`, `沒有圓形底座`, or `不是 FollowMe` when the same narration also says a visible standing/vertical display has a `Samsung FollowMe`/`FollowMe` product label. This prevents the backend from rescuing the filename but leaving the row as `遠景` or showing contradictory narration.
- Regression check: text equivalent to `LG CordZero ... Samsung Follow Me ... 展示用的立式螢幕` should infer `FollowMe M7 32"`; text equivalent to `LG StanbyME ... 沒有 Samsung FollowMe` should infer `None`.
- User-confirmed sample `M-台中市-南屯區-TK3C-台中嶺東-697.jpg` must resolve to a `單機-FollowMe...` output, not `遠景`. If price is not readable it is finalized as `無價格` and uploaded under the per-photo `.22` rule.
- Post-processing must never fabricate a replacement narration such as `最終校正...` to hide a raw model contradiction. Preserve the image-grounded raw narration in audit evidence; if it contradicts the structured result, the content-health/evidence gate must withdraw the operator-facing narration and retry or mark review-required. A result card must never show a corrected FollowMe value beside contradictory prose, but the remedy is fail-closed rejection, not prose rewriting.

## 2026-07-08 Distant-View Quality Audit

When current-year distant-view records are rerun, accuracy must be audited, not only process health. Use `tools\audit_distant_followme_risk.py --output-dir "D:\00_商化\00_已OCR照片" --year 2026 --include-medium --sample-csv "D:\00_商化\00_已OCR照片\_ocr_audit\distant_followme_risk_2026_latest_sample.csv"` to produce `_ocr_audit\distant_followme_risk_2026_latest.csv/json` plus a deterministic sample CSV for visual spot checks.

The audit catches records saved as `遠景` even though evidence still contains FollowMe, Samsung Follow, S32FM/S43FM, white stand/base, vertical pole, tray, side-label/model clues, or single-unit wording such as `主角是`, `一台`, `單台`, or `判斷是單機`. It also catches `critical_followme_result_conflict`: final output is FollowMe but the narration contradicts it. Do not count corrected narration such as `不能判為遠景` as a conflict. Baseline on 2026-07-09 after exposing this class: 303 current-year risk rows, including 275 FollowMe result/narration conflicts. After the 2026-07-09 focused reruns, risk fell to 9 rows; keep those blocked from Drive until rerun/repair/manual justification clears them.

The sample CSV is not a rerun list. It includes high-risk rows and a deterministic sample of apparently true distant rows so another AI can estimate whether distant precision is improving after reruns. If a user-confirmed FollowMe or single foreground monitor appears in this sample, expand the risk rules before allowing 2026 uploads.

2026-07-09 visual spot-check after v19.34 reruns produced `_ocr_audit\distant_followme_risk_2026_latest_visual_spotcheck.csv`: 3 true distant, 6 likely single, 1 unclear. Therefore "rerun completed" is not sufficient proof; current-year distant rows remain blocked unless the risk audit and visual spot-check no longer show likely single/FollowMe cases.

Important: visual spot-check CSV files are only for estimating rule quality. They must not be used as upload approval. `tools\prepare_drive_upload_manifest.py` intentionally ignores `distant_followme_risk_*_latest_visual_spotcheck.csv` when deciding whether a current/future-year `遠景` file is Drive-ready. A current/future-year distant file can only become upload-ready if it is corrected to a concrete `單機` / `FollowMe` / `它牌(...)` result, or if it appears in an explicit approval file such as `_ocr_audit\current_year_distant_upload_approval.csv` with `upload_approved=approved` or `verified_status=true_distant`.

`tools\prepare_drive_upload_manifest.py` reads `_ocr_audit\distant_followme_risk_*_latest.csv`; listed files are marked `current_year_followme_or_distant_risk_needs_rerun` and must stay out of Drive until rerun/repair clears them.

## 2026-07-09 Dashboard Reveal Guard

- The right-side `辨識紀錄` rail must only show frontend-revealed records while OCR is running. Do not backfill it directly from backend `display_queue`, because that makes the right thumbnail/result appear before the current photo's AI narration has finished and causes long-run drift.
- Do not show a pending/processing card in `辨識紀錄` as if it were a completed OCR result. The visible sequence remains: photo appears, `AI 即時判讀中` types, then the completed record is revealed in the rail.
- If backend logic would change a row from `遠景` to `單機-FollowMe`, the raw contradiction must remain visible to the audit gate and the pass must retry/review. Do not generate a backend-authored “corrected final narration”; operator display uses the bounded withdrawn-message while unhealthy, and durable trace keeps the original model text.

## 2026-07-09 Pause Handoff

- Project was paused by user request after disk cleanup. Do not resume old-year OCR first.
- No backend, staged rerun, recursive OCR, auto-rerun waiter, rclone uploader, or upload helper should be running at handoff.
- Waste folders removed: output `_ocr_staging`, Drive upload staging, repo `logs`, non-venv Python caches, old `flat_output_backup_before_*`, and old `_bad_no_compare_2026_backup_*`.
- Backend version is now `v19.36 (strict distant quarantine and disk-safe rerun)`.
- `tools\rerun_staged_candidates.py` is now disk-safe by default: it removes old flat output for the target folder before rebuilding, instead of moving the entire folder output into huge backup directories. Use `--keep-flat-output-backup` only when explicitly needed.
- If a staged-runner wrapper exits while its backend batch is still healthy, do not start that group again. Use `--execute --resume-existing-then-continue` with the original multi-group candidate CSV and summary paths. It validates the active staging folder by period plus source-folder digest, attaches without restarting the active batch, skips already-finalized earlier groups, restores the dashboard to the original source folder before cleaning the active staging directory, and starts only later groups. Status polling tolerates up to six consecutive transient API failures before failing closed.
- Recovery may encounter a uniquely matched active staging directory that is idle but incomplete (`processed < total`). After the period+source-digest match is proven and before attachment, `resume_existing_then_continue` must call `/api/start_batch` with that exact staging directory, `confirmed=true`, `restart=false`, and `reprocess_last_n=0`; it then attaches and waits for completion. It must not ask the operator to press Continue, must not restart from zero, and must never start an unmatched, ambiguous, complete, or already-running directory.
- Candidate grouping must use a CSV `source_path` directly when its resolved path is inside the configured source root, is an existing file, has the exact candidate filename, and agrees with the candidate period. Never perform one full-tree `rglob` per candidate when a validated bound path already exists; fallback search is only for missing or invalid legacy paths. On the 2026 `.5` backfill, ignoring this field turned 5,942 rows into thousands of ten-year tree scans and delayed attachment for minutes without doing OCR.
- A current-year recovery watcher may use `-SkipCurrentYearFirstPass -AllowPlannedBackendUpgradeInterlock -SkipRecursiveResume` only when a live `backend_upgrade_v1945` lock owner is already waiting for that same watcher. The explicit switches let pass 2/pass 3/distant review finish under the planned interlock, while yielding the next idle boundary to the backend upgrade/evidence backfill instead of starting older recursive OCR.
- Latest v19.36 pass3 completed only `202605` (80/80 success). Remaining current-year pass3 candidates are `202604` 190, `202603` 31, `202602` 176, `202601` 154.
- Continue from `_ocr_audit\current_year_distant_and_risk_v1936_pass3_selected_20260709_1605.csv`; preserve completed `202605`.
- Latest upload manifest had `ready_pending=0`, `uploaded_skipped=52122`, `review_required=13424`. Do not upload `review_required`.
- Full handoff is in `docs\handoff_20260709_pause.md`.

## 2026-07-08 FollowMe Risk Rerun Waiter

- `tools\run_followme_risk_rerun_after_current.ps1` waits for the current staged rerun to finish, refreshes `distant_followme_risk_2026_latest.csv/json`, restarts only backend port 5001 so the latest FollowMe rules are loaded, and then staged-reruns just those risk rows.
- It does not interrupt an active staged rerun and does not touch the visible dashboard on port 5000.
- Current live waiter was started on 2026-07-08 around 23:38 with output logs `logs\followme_risk_waiter_*.log` and main script log `logs\followme_risk_after_current_*.log`.

## 2026-07-09 Current-Year Distant Escalation

- A visual spot-check of 2026 distant-view output found an unacceptable false-distant rate: the sample included many likely single/FolloMe foreground products. Therefore current-year distant-view is not a low-risk class.
- Current/future-year distant-view output must stay out of Drive unless it is explicitly approved in `_ocr_audit\current_year_distant_upload_approval.csv` or corrected to a concrete `單機` / `FollowMe` / `它牌(...)` result. Do not treat spot-check samples as approval.
- `tools\prepare_drive_upload_manifest.py` writes `_drive_upload\drive_upload_stale_uploaded_review_required.csv`. These are files that were already uploaded earlier but are now blocked by stricter review gates; do not count them as done.
- Use `tools\cleanup_stale_drive_review_uploads.py` only after reviewing the stale list. It dry-runs by default; with `--execute` it removes stale current-year remote files and removes those names from `drive_upload_uploaded.csv` so corrected or visually accepted outputs can upload again later.
- The active 2026 repair path is:
  1. scan current-year questionable records with `tools\rerun_questionable_records.py`;
  2. rerun only the distant bucket with `tools\rerun_staged_candidates.py --reason-contains 遠景`;
  3. refresh `tools\audit_distant_followme_risk.py`;
  4. rebuild upload manifests and upload only `ready` rows.
- `tools\rerun_staged_candidates.py` must restore the backend work directory to the original source folder before deleting `_ocr_staging`. Otherwise the dashboard can keep polling a deleted staging path and repeatedly report "Failed to list actual files".
- `build_final_display_thinking()` may only preserve an existing image-grounded narration or emit the bounded missing-narration fallback. It must not rewrite a contradiction as `最終校正`. Raw contradictory text stays in the evidence trace; runtime health prevents it from becoming the visible completed narration or a ready/upload result.

## Presentation synchronization non-regression contract

The status API also exposes `frontend_asset_fingerprint`, calculated from the
built dashboard script/css asset names. The frontend calculates the fingerprint
of its loaded document assets and performs at most one `/?ui=<fingerprint>`
replace when they differ, with a 30-second `sessionStorage` cooldown. Matching
assets do not reload. This is UI-only and safe while OCR is running; it never
restarts or changes the backend.

The frontend orders `presentation_queue` by backend `presentation_sequence`
before applying its 200-item cap. If an asynchronous update ever commits a
different active/narration key, it exposes `data-presentation-invariant`,
clears the active item, and waits for a complete immutable snapshot rather
than pairing fields from different photos.

The results rail has one independent active slot above the read-only revealed
results. It is the active immutable snapshot itself, with the same
`presentation_id` and sequence, thumbnail, and filename; it shows only
`處理中` and `AI 即時判讀中`, never model/price/category. After narration and
the reveal hold complete, that slot disappears and the same snapshot becomes
the top final card. It must never duplicate a final card. With no active item,
there is no active slot.

- Backend `presentation_id` is the only identity truth. It is unique and
  travels with the complete immutable presentation snapshot.
- Photo, AI text, revealed card, and modal must use that same immutable
  snapshot and the same presentation id/sequence.
- The right rail reads only revealed results. Never join state by filename,
  array index, or `source_path`. While OCR is running, never fall back to
  `recent_results` or `current_file`.
- Keep the previous photo visible until the next photo is fully loaded; normal
  transitions must not show a black screen.
- Visible copy says `AI`; never expose `LLM` or `自言自語`.
- Any dashboard/backend presentation change must pass the 500-item soak, a
  production build, and a live browser check of at least 3 transitions. Check
  photo id == AI id, reveal ordering, card id == completed active id, modal id
  == card id, and nonzero image dimensions.

Run the local gate with:

```powershell
.\.venv\Scripts\python.exe tools\run_critical_regressions.py
npm.cmd --prefix dashboard run build
```

## 2026-07-15 Structured-answer authority incident

- Formal `202601` review resumed at `829/1,504`, but content monitoring stopped it at `836/1,504`. The raw model JSON for several display-wall photos explicitly returned `view_type=遠景`, `model=null`, and `price=null`; legacy narration rescue then extracted a nearby label from the prose and silently rewrote the final row to `單機` with a model/price. The `.5` evidence gate kept these rows out of verified output, but continuing would have wasted second/third passes and filled the review queue with parser-created conflicts.
- Machine-readable fields are now authoritative within each independent pass. Narration remains boss-facing evidence and may trigger a contradiction/retry, but it may not change an explicit `遠景` to `單機`, change an explicit `單機` to `遠景`, refill `model`/`price` when those structured fields were explicitly null, or replace one non-empty SKU/price with a materially different SKU/price. A conflicting replacement is cleared and retried; cosmetic case/punctuation/currency normalization remains allowed. Ambiguity must fail closed into an independent retry or review; post-processing must not manufacture certainty.
- `structured_authority_blocked_fields` is persisted with the parsed result when a legacy heuristic attempted a material rewrite. Monitor `view_type`, `model`, and `price` as content-drift evidence. Equivalent scene wording such as `一般單機→單機`, cosmetic case/punctuation, and currency formatting are normalization rather than a blocked override; historic traces may still contain a category-only flag from before this distinction. A material non-empty value means the gate prevented an override and the photo must remain reviewable unless a later independent pass produces internally consistent structured evidence.
- Monitoring is not limited to counters. At each checkpoint, sample raw JSON versus final parsed output, confirm `independent_pass=true`, `prior_answer_exposed=false`, `prompt_contamination=false`, and check the visible dashboard. If raw/final classification or null identity fields diverge, stop immediately before resuming or starting another folder.
- A stop request can be followed by an external resume watcher. To obtain a true repair boundary, terminate only the exact `rerun_staged_candidates.py` parent/child pair, then call `/api/stop` and verify `is_running=false`; keep the port-5000 backend alive so the dashboard remains available. Do not kill unrelated Python processes.
- The backend is headless by default. Only an explicitly interactive launcher may set `SAMSUNG_OCR_OPEN_BROWSER=1`; continuity, supervisor, repair, or backend-only restarts must not open a browser tab/window. Always reuse the existing dashboard tab and verify that only one `localhost:5000` tab exists after a controlled restart.
- Browser-tab audits must inspect the user's actual Chrome tabs, not only the automation client's currently bound tab list. On 2026-07-15 the bound list reported one/zero while `user.openTabs()` exposed five historical `localhost:5000` dashboard tabs left by older auto-open restarts. The newest healthy dashboard was verified and retained; the other four duplicate dashboard tabs were closed without touching unrelated sites. Future checks must prove exactly one actual dashboard tab and must never create a tab merely to test the UI.
- Live verification after the material-identity guard: formal `202601` advanced `842→845/1,504` across 3 photos and 6 model passes. Raw/final view rewrites, null refills, material SKU rewrites, material price rewrites, prior-answer exposure, and prompt contamination were all zero; one cross-pass core disagreement remained unresolved as required. The existing single Chrome tab showed `65,331/150,321`, formal progress, natural AI narration, and current-run cards with no raw JSON or garbling. Continuity then resumed the formal `.5` backfill.
- 2026-07-09 live run: `logs\current_year_distant_staged_rerun_20260709_103552.out.log`, candidates `D:\00_商化\00_已OCR照片\_ocr_audit\current_year_distant_staged_rerun_candidates_20260709_103552.csv`, summary `D:\00_商化\00_已OCR照片\_ocr_audit\current_year_distant_staged_rerun_summary_20260709_103552.csv`.
### 本機 VLM benchmark sidecar

`tools/model_benchmark_sidecar.py` 是獨立、受限的 accuracy-first 模型比較工具，目標資料固定為 `samples/ocr_demo_50`。預設只 dry-run；只有明確加入 `--execute` 才會對 LM Studio 做 load/inference。它會 fail closed：OCR API 必須 idle，且不得有 rerun、recursive、watcher 或 uploader 程序；endpoint 只能是本機 LM Studio，照片與 raw output 不會上傳或呼叫外部服務。

Windows process discovery must use UTF-8 JSON from `Get-CimInstance`; a command failure, invalid JSON, or unreadable inventory is a hard refusal, never an empty-process assumption. The sidecar checks both API idle and the process inventory before claiming the benchmark lock and again immediately after claiming it. FollowMe danger scoring treats both `遠景` and `distant_view` as the same dangerous misclassification.

Interlock: the sidecar and four-hour watcher share `00_已OCR照片\_ocr_audit\model_benchmark.lock`. It is created atomically and records PID, timestamp, and model list. The watcher waits and logs before every backend, staged, recursive, or uploader launch. The sidecar claims the lock, rechecks idle state, and removes its own lock in `finally`; a stale lock requires explicit `--recover-stale-lock`, an absent owner PID, and the age threshold.

The stalled InternVL artifact has a separate download-only helper: `tools\resume_internvl35_range.ps1`. It uses the proven Hugging Face byte range and `curl --continue-at -` against the preserved `.part`; it never loads a model or touches OCR. The helper is single-owner, refuses shrink/duplicate downloaders, requires exact byte count and SHA256 before atomic rename, and preserves the partial plus failure status on every error.

Continuity supervisor: `tools\ocr_continuity_supervisor.ps1` is the no-token local recovery entrypoint. The existing `SamsungOCR_PipelineWatchdog` task runs it at startup, logon, and every five minutes with an atomic single-instance lock. Healthy work is a no-op; hung backend, unavailable LM Studio with a different loaded model, or ambiguous staging is alert-only and fail-closed. It preserves `_ocr_audit`/staging/history, never uses `--no-resume`, and starts uploads only from ready-pending rows.

When Task Scheduler registration requires administrator rights, use the user-level fallback `tools\install_ocr_continuity_daemon.ps1 -Action install`. It registers one hidden `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` entry (or the user Startup folder), runs the supervisor immediately and every five minutes with a bounded child timeout, and writes structured daemon/shutdown logs. Use `-Action status` or `-Action uninstall` for user-level registration; the protected four-hour task remains the backstop.

The installer also creates the current-user LIMITED task `SamsungOCR_UserContinuityEnsure` with `schtasks.exe` every five minutes. It invokes `-Action ensure`, which detects the exact RepoRoot daemon and no-ops when it is already alive; stale locks require an absent owner and age proof. A denied task creation is fail-closed and leaves the HKCU Run setup intact.

受控部署若以 `Stop-Process` 強制停止 continuity daemon，PowerShell 的 `finally` 可能來不及移除 `_ocr_audit\ocr_continuity_daemon.lock`。重新啟動前必須同時證明：系統中不存在指向本 RepoRoot 的 `ocr_continuity_daemon.ps1` 程序，而且 lock JSON 記錄的 owner PID 已不存在。只有兩項證據都成立時，才可刪除這一個精確的 daemon lock；無法讀取 lock、owner 仍存活或程序清冊不明時一律失敗封閉。隱藏重啟後要回讀 lock PID，確認它與唯一存活 daemon 相符。`model_benchmark.lock` 是 OCR／上傳安全邊界，與 daemon 單例鎖無關，絕對不可在此復原流程中刪除。

Drive correction reconciliation is local-only by default: `tools\reconcile_drive_corrections.py --output-dir ...` writes an idempotent `_drive_upload\drive_correction_reconciliation.jsonl`. It records source identity, old/new names and Drive IDs, local/remote hash evidence, gate evidence, and disposal receipt. `--execute` requires an explicit phase and currently remains fail-closed; no ordinary uploader run may trash or replace stale rows.
- Remote readback explicitly requests MD5 via `rclone lsjson --hash-type MD5` and accepts the standard nested `Hashes.MD5` field. An interrupted `old_trash_pending` row is resumable: it first proves the old path is absent and the surviving new file still matches ID, size, and MD5 before writing the verified disposal receipt; it never blindly issues a second deletion.
- The reconciliation ledger generated before the v19.45 backfill is not reusable as authority: it has stale gate evidence and some rows were written with mojibake paths. Rebuild it after the 2026 manifest/evidence audit from UTF-8 source mappings, exact local content hashes, and unique uploaded Drive IDs; do not mutate Drive from the pre-backfill ledger.
- Rebuild with `tools\build_drive_correction_reconciliation.py --output-dir ...` after the fresh 2026 manifest exists. Dry-run is the default. `--execute` may atomically write a structurally valid UTF-8 ledger for read-only `discover-old`, but `ledger_integrity_ok`, `all_rows_accounted`, `all_replacements_gate_ready`, `safe_to_upload_new`, and `safe_to_replace` are separate authorities. Missing historical IDs are filled later by `reconcile_drive_corrections.py --execute --phase discover-old`; a same-name/same-hash remote becomes `unchanged_remote_verified` and is never sent to the trash phase. `upload-new` hard-requires row status `new_ready`.
- The current-year watcher calls this builder only after its final risk audit, manifest refresh, and review split. A mapping failure is logged and leaves the ledger quarantined/fail-closed; it does not call Drive and does not prevent safe ordinary ready rows from continuing through their separate uploader gate.

執行前會確認每個指定模型已完整存在於本機 LM Studio，記錄原本載入模型/context，對所有候選使用同一 production prompt、全景與 deterministic evidence crops、固定 inference settings，逐模型順序測試。raw 結果寫入 `runs/model_benchmark_sidecar/raw.jsonl`，可重入且不覆蓋已完成 `(model, case)`；結果包含原始輸出、延遲、解析錯誤、context 與危險錯誤分類。無論中途成功或失敗，finally 都會恢復 `qwen/qwen3-vl-8b` 與指定 context。

The manifest is immutable schema v2: it carries the labels hash, every source-image hash, and a canonical case-set hash covering ID, image, tags, and expected fields. Before any model is touched, the sidecar recomputes that contract, prepares the full image plus deterministic crops once per case, and fingerprints the production prompt plus decoded evidence bytes. Every raw row records the manifest, case-set, prompt, image, and input fingerprints. Resume is refused if a row lacks those fingerprints, duplicates a candidate/case key, has an inconsistent key, or any input/contract has drifted; use a new output directory instead of mixing runs. A fully completed candidate is skipped without an unload/load cycle, and final restoration uses the baseline context captured before the run rather than the candidate context.

Raw rows use `candidate_model` for the tested VLM and keep `model` for the predicted Samsung product; never overload those fields. `model_benchmark_score.py` v2 filters by candidate and treats missing, duplicate, unexpected, mixed-model, parse-error, and inference-error rows as protocol failures that remain in the 50-case denominator. A model is not eligible for promotion unless `benchmark_gate_pass=true`.

模型決策 gate 以整體 field/exact accuracy 為第一順位，遠景誤判、FollowMe 誤判、型號幻覺等危險分類不得退步；只有 accuracy 不退步時才用 latency 作次順位。benchmark 不會改 production prompt、OCR 權重或 runtime 設定；不要在 OCR 執行中使用 `--execute`。
## v19.45 Evidence Contract

三層即時守門的原理、狀態轉移、遠景／FollowMe 特殊規則、稽核證據與必跑驗證，以 [three_layer_accuracy_gate.md](three_layer_accuracy_gate.md) 為權威說明。修改 `immediate_retry_decision()`、即時插隊、輪次歷程、Dashboard 進度文字或上傳守門時，必須同步檢查該文件。

Every OCR pass must emit validated `complete_screen_count`, `unique_main`, `label_ownership`, and `followme_physical_evidence`. Natural-language thinking can raise review risk but cannot supply missing evidence. Current-year rows without a v19.45 evidence trace carrying the current `evidence_guard_revision` remain review/rerun candidates and are excluded from ready manifests; historical rows through 2025 are not subject to this current-year trace gate. The contract version identifies the evidence schema; the guard revision identifies the exact rule implementation that evaluated it. Never stamp a migrated legacy trace with a new guard revision unless the image was actually evaluated by that revision.

守門不得只檢查欄位是否存在。`view_type/category`、單機結構／明示遠景敘述、`label_ownership=matched`／鄰機價牌敘述任一矛盾都必須 retry/fail closed。裸露的 `S32FM...`／`S43FM...` 是 Smart Monitor 面板 SKU，不是 FollowMe 組合的直接證明；只有同一實機的 FollowMe 品牌或充分白色移動支架／圓底座／附著託盤證據成立後，SKU 才可用來比較家族版本。遠景不得攜帶同主體 FollowMe 強實體線索。畫面播放 ASUS/LG Demo 是內容，不是硬體品牌證據，不可覆蓋正式 Samsung SKU。官方參考價只負責產生 `↑`／`↓`／`✓`；價差本身不得觸發 VLM 重讀，只有照片價牌模糊、荒謬數值或型號／價格歸屬矛盾才升級。

Dashboard API 的 `success` 是歷史相容欄位，語意是「已有非系統失敗的判讀記錄」，可能包含 `review_required`。介面只能稱為 `完成判讀`，並分別顯示 `review_required`；Drive 仍只接受 `auto_verified=true && auto_review_required=false`。`windows_user_launcher.ps1` 在安全邊界啟動時會比較 `dashboard/src` 與 `dashboard/dist/index.html` 時間，來源較新必須先 build，避免新版後端計數配上舊前端文字。

### Current-year per-photo finalization and upload (revision `20260716.26`)

Formal OCR is a per-photo pipeline.  A photo that passes on round 1 is finalized immediately.  A photo with a FollowMe cue, distant view, missing model/price, large price difference, or core-evidence uncertainty receives independent rounds 2 and 3 from the same pristine image, with no previous answer in the prompt.  **The hard limit is three total model calls per photo, including transport/parser retries.** `max_total_attempts=max_auto_attempts<=3`; configuration, restored retry state, exceptions, or UI metadata may never create a fourth call or a pass index above 3.  The call budget is persisted before the model request so a crash/restart cannot reset it.  After the third call, `finalize_three_pass_outcome` makes one bounded evidence decision or emits one terminal technical failure; it never queues round 4.  The result is valid when it is `遠景`, or a `單機` with only a model, only a price, or neither.  Missing fields are recorded as missing; they are not a reason to isolate or abandon the photo.

Content disagreement is not a technical failure. If all three calls are independently bound to the same pristine image, carry no prior answer, and show no prompt or cross-photo contamination, two later passes with sufficient same-subject FollowMe fixture evidence settle an initial false-distant result as a FollowMe-family `單機`. Model and price remain empty unless at least two passes independently agree. The photo then closes and enters per-photo upload without a fourth model call. Only request/image binding, prompt contamination, cross-photo duplication, parser/transport, or runtime-integrity failures may stop upload.

A scene with `complete_screen_count=0`, `unique_main=false`, no owned label and no same-subject FollowMe fixture may finalize as `遠景／無型號／無價格` when at least two usable passes agree.  This covers store/environment photos with no complete monitor and prevents six useless technical retries.  Counts 1 or 2 do not receive this allowance because a partially missed main unit may exist; they still require conservative single/distant evidence resolution.

Every verified result is atomically enqueued to `_drive_upload_stream` as soon as its result JSON is durable.  The single hidden worker publishes the deterministic flat filename and uploads exactly that photo.  It preserves `↑/↓/✓/?` when a price exists, replaces an obsolete same-name remote object in place, never creates `_2`, and writes the canonical receipt only after a unique size+MD5 readback.  OCR does not wait for network throughput.  `tools/stream_drive_upload.py` is this authority; the older completed-run proof and bulk uploader remain only for legacy reconciliation and must not block this per-photo lane.

A receipt is idempotent only for the same current guard revision, source bytes and deterministic target name. An older-revision receipt for the same `source_item_id` must be moved to `superseded_receipts` and the corrected result must enter the pending queue; the mere existence of an old receipt may never silently suppress a new OCR correction.

Only technical-integrity faults may prevent finalization: wrong request/image binding, prior-answer or prompt contamination, cross-photo identity drift, unhealthy runtime output, invalid evidence schema, changed source bytes, or failed Drive readback.  A technical call does not create a visible business pass card, but it still consumes one of the three total calls.  If the budget is exhausted, the photo emits one terminal technical result and later photos continue; no automatic fourth call is allowed.  Ordinary visual ambiguity is never called `三輪衝突／已隔離`, never waits for a nonexistent slow model or unspecified human, and never blocks later photos.

FollowMe family evidence is also a final field, not an informal note. Two usable passes with sufficient same-subject physical evidence establish the FollowMe family even when M5/M7/Pro or price disagrees. In that case `.22` writes `FollowMe 型號未細分`, leaves unsupported price/model detail empty, and uploads the photo as a truthful single unit; it must never fall back to `遠景`.

Revision `.23` closes the remaining permanent-hole case: when all three calls are healthy, stateless, request/image-bound and share one image hash, but one or more passes claim `遠景` with only 1–2 complete screens, the invalid distant claim cannot count as distant and cannot turn the photo into a technical failure. If no safe two-pass view consensus remains, finalize conservatively as `單機／無型號／無價格` (or retain only independently supported fields). This prevents false distant uploads and prevents completed content calls from becoming permanent `技術錯誤／該張未上傳` rows.

Revision `.25` closes both sides of the frame-counting defect. `complete_screen_count` is counted exactly once from the first original full image; all four outer bezel sides and all four bezel corners must be inside that original frame. Any screen touching/crossing an original edge is incomplete even if most of its panel is visible. Before applying the cropped-neighbor example, every pass must scan the entire original image by left/center/right and top/middle/bottom and count complete monitors away from the center, including upper rows, lower rows, distant shelves, and other display fixtures. The centered-complete plus left/right-edge-cut layout has count 1 only when that whole-frame scan finds no other complete monitor anywhere. Pass 2 inventories approximate positions; pass 3 performs a counter-scan outside the center so both passes do not inherit the same example bias. Every pass retains the pristine full image and high-resolution label crops but receives no duplicate full-height scene tile. The lower-left-center label crop is allowed because it contains only the shelf/card band; it may improve small-text reading but must never contribute to monitor counting or replace ownership checks against the pristine full image. Brand names and advertisements rendered inside the screen are signal content, not the monitor hardware brand, and cannot invalidate a spatially aligned Samsung product card. Narration and structured price digits must match for every physical SKU, not just friendly FollowMe names; mismatch clears the price and forces another independent pass. These definitions must appear consistently in the production prompt file, final output contract, per-pass focus prompts, crop annotations, and the near-image user instruction. Regression evidence must contain at least one cropped-neighbor single, one genuine multi-monitor distant view, and one FollowMe foreground unit before production resumes.

Revision `.26` prevents the inverse drift found on the real wide-aisle photo `草屯-670`: the tight edge-cut exception may not be generalized to a whole row, display wall, multi-level shelf, or wide aisle. A single pass that still describes one of those broad scenes but has no bound model/price, no matched label and no FollowMe hardware is only a weak single vote. If one healthy pass supplies valid `3+ / unique_main=false / no owned label` distant structure, it vetoes two such weak votes. Two model/price-bound single passes or two FollowMe physical-evidence passes remain stronger and are not overridden; this preserves the cropped-neighbour `台中旗艦-940` single-unit result.

Photo-local FollowMe narration/structure disagreement is contained inside that photo's three-call budget. Seeing the same local disagreement class on another source is recorded for monitoring but is not proof of cross-photo memory infection and must not stop the batch. Only direct prior-answer leakage, copied cross-photo identity, prompt contamination, request/image binding failure, or another non-local technical fault may trip the batch-wide fuse. This paragraph supersedes older historical notes below that treated a second local conflict as automatic batch-wide drift or mentioned calls 4–6.

### Live-dashboard deployment and header iron rule (revision `20260716.26`)

- A planned repair must not begin by stopping the only live OCR service.  First read this guide and the continuity handoff, finish the code change, run targeted regressions, build `dashboard/dist`, and bring up a hidden green backend on a spare port.  Only after its `/api/status`, asset fingerprint, guard revision, process uniqueness and upload isolation pass may the old OCR loop be paused and the original dashboard address be replaced.  The user's existing tab stays on the same address and reloads through the asset fingerprint; never open a terminal window, browser window, or monitoring tab.
- The production handoff is incomplete until the original address reports `is_running=true`, the current file/pass advances, exactly one backend listener and one stream worker remain, and the existing page shows the new fingerprint.  `待機中` during an avoidable planned repair is a deployment failure, not an acceptable intermediate state.
- The header must preserve four non-overlapping regions at the production viewport and browser zoom: title, version, full-program progress, and live/upload status.  Use a bounded grid with `minWidth:0`, a dedicated progress column, and a fixed status column.  The total `65,331/150,321`, folders, current review progress, current pass, upload total, and pending upload count must remain readable and may never paint over one another.  This rule does not change the finalized 50/50 photo/narration workspace or right accumulated-card rail.
- Every change to the header or deployment path must run `tools/test_presentation_soak.py`, a production Vite build, and a live same-tab verification showing one current photo, readable LLM narration, advancing right cards, advancing review count, and upload status.  The regression must reject `pass_index>3`, `系統技術重試`, overlap-prone flex header markup, stale guard revision, and missing full-program totals.

The legacy completed-run proof is still required only when operating `tools/rclone_drive_upload.py` for old bulk manifests. `tools/audit_distant_followme_risk.py` must first prove the builder summary, selected candidate/result set, every folder summary, canonical `success_records.csv` / `rename_plan.csv` / `copied.csv`, source identity uniqueness, output existence, and zero remaining unverified v19.45 sources. A zero-row candidate CSV is valid only when the authoritative source inventory is non-empty and every source is already verified.

The risk JSON binds those authorities with `audit_input_sha256`; `tools/prepare_drive_upload_manifest.py` recomputes it, globally blocks current-year rows on drift, and emits `current_year_finalization_proof`, `current_audit_input_sha256`, `current_year_upload_gate_open`, and `next_batch_sha256`. Explicit distant approval must bind the same run/input hash, source identity, target content hash, and approval timestamp.

The uploader must rebuild and validate the manifest on every cycle before staging or rclone. The watchdog order is audit → finalization proof → manifest rebuild → batch/content hash proof → uploader. The continuity supervisor may launch an uploader only from a fresh content-bound `upload_gate_proof.json`; missing, stale, changed, partial, duplicate, or unreadable evidence always closes the gate. Run the tools tests from the repository root with module discovery, for example `.venv\Scripts\python.exe -m unittest -v tools.test_current_year_upload_finalization`; invoking that file directly makes Python use `tools` as the script path and can falsely fail on the project-root `skills` import. Also run `test_rclone_upload_safety_unit.py`, the watchdog/supervisor tests, both reconciliation tests, and `tools/run_critical_regressions.py` after modifying this chain.

`tools/build_upload_gate_proof.py` is the shared proof authority for the current-year watcher and supervisor. Manifest or review-split nonzero exit, a closed/stale gate, any authority hash mismatch, a blocked pending row, or a next-batch count mismatch must remove/refuse the proof. Intermediate review phases cannot launch the uploader. The current-year marker is written only after the verified uploader exits successfully, the manifest is rebuilt, the proof is regenerated, and exact pending count is zero; it records the audit input, manifest/pending hashes, and backfill run identity. Historical continuation must match those fields against the current proof.

Each pass is recorded in a bounded, idempotent `v1945_evidence_trace.jsonl` without image bytes or secrets. Boundary upgrades must finish the entire active staged runner, verify idle, then start v19.45 with the existing staging/history preserved. `tools/migrate_legacy_v1945_trace.py` resolves legacy staging-only rows through the current-year and 202603 recovery candidate CSVs, adds stable original-source identities, deduplicates by trace ID, and atomically writes `_ocr_audit/v1945_evidence_trace.jsonl`. Any invalid, unresolved, or ambiguous row is fail-closed and must block the backend restart. Do not restart mid-folder or between months of the same staged runner.

After the compact-v2 backend is verified, `tools/build_v1945_evidence_backfill.py` reads every 2026 `copied.csv` source mapping and emits only source identities that have a trace verified by both v19.45 and `evidence_guard_revision=20260716.19`. Old v19.45 traces without the current revision are deliberately re-emitted. The builder is resumable and fail-closed for missing, invalid, or conflicting sources. Revision `.5` removes prior-answer paths and adds the runtime health fuse. Revision `.6` preserves an unlisted SKU only through three-pass same-photo consensus. Revision `.8` completes the unique 1–3 trailing-character retailer-short-SKU rule and persists its pipeline-owned marker through post-processing. Revision `.9` requires complete distant-view evidence and compares only the material `3+ / no unique main / no owned label / no strong FollowMe` meaning across passes. Revision `.10` prevents true distant views from being over-blocked merely because readable narration says “multiple monitors” instead of repeating the exact structured integer; the integer remains mandatory in the evidence contract, narration must still describe the multi-screen layout and no unique main, and explicit sub-three narration remains fail-closed. Revision `.11` recognizes only local explicit negations such as `無 FollowMe` and `沒有看到 FollowMe` as negative wording, preventing a clean exclusion sentence from being treated as a positive cue while leaving unnegated FollowMe text and all strong structured physical evidence blocked. Revision `.12` moves all four evidence fields into the explicit common JSON Schema and repeats the single-unit requirements in branch C. Revision `.13` repeats at the common output and stateless retry boundary that 0/1/2 complete screens can never be distant, and that a dominant centered complete monitor with its spatially aligned readable label/price remains a single-unit candidate even when one neighboring screen is partial or visible. Revision `.14` moves structured authority ahead of narration identity rescue: an explicitly present `model` or `price` field, including null, can never be refilled from prose; conservative narration rescue exists only for legacy responses where that structured field is absent. Revision `.15` makes material structured-authority blocks fail closed, detects narration with material FollowMe fixture evidence when the structure cannot establish the same foreground subject, and prevents an unsafe prior pass from being washed by later agreement. A single-unit record that already carries direct branding or at least two independent same-subject strong physical cues is not fused merely because narration mentions one additional orientation/card detail; distant answers never receive this allowance. Large scenes receive a central full-height evidence tile on pass 1 and left/center/right independent tiles on retries. Screen advertising is weak content evidence that cannot negate a visible base/stand/tray. Prompt/rule echo, overlong narration, and `最終校正`-style backend prose are unhealthy; narration is preserved rather than rewritten. The runtime fuse stores a bounded machine-readable snapshot. A containable same-photo FollowMe/view narration conflict may receive independent passes 2 and 3; it can never become verified while the unsafe history remains. After pass 3 it is fixed as unresolved and the batch continues. The same conflict class appearing on a different source identity in the same monitoring epoch is evidence of cross-photo drift and trips the durable fuse. Model, price, prompt, UI, binding, and non-containable content classes still fuse immediately. Revision `.16` binds every response to a fresh 128-bit request ID and full-image SHA-256 and blocks adjacent cross-photo model/price repetition from first-pass success. Revision `.17` requires observable same-photo Pro/43/S43FM/17,990 evidence for a Pro 43 answer, preserves an explicit FollowMe model when the same response supplies sufficient same-subject structured fixtures, and requires all 2026 FollowMe passes to agree on model and price; a later two-to-one majority never washes out an identity conflict. Revision `.18` compares only established friendly-name/physical-SKU aliases within the same FollowMe variant, never across model families or sizes. Its runtime narration fuse requires either a non-negated FollowMe identity or unmistakable white mobile-stand fixtures; an ordinary black short stand and tray described as non-FollowMe must remain a review decision rather than stopping the batch. Revision `.19` requires three independent identical passes for every current-year single-unit candidate whose structured evidence reports three or more complete screens. Human-audited high-risk pixels are bound by full-image SHA-256 to their expected view; a conflicting model pass is a non-containable runtime-health failure and must trip the durable fuse immediately.

Runtime content failure must survive process exit. The live loop atomically creates `_ocr_audit/runtime_health_fuse.json`; `/api/start_batch`, `ocr_continuity_supervisor.ps1`, `ocr_upload_watchdog.ps1`, manifest/proof generation, and `rclone_drive_upload.py` all reject work while it exists. Never clear this marker from a scheduler. Clear it only after the defect is corrected, critical regressions pass, and an isolated five-photo smoke run proves independent rounds and synchronized presentation.

The smoke run does not require prematurely clearing the fuse. The start API permits only an explicit `runtime_health_trial=true` request to a fresh 1-15 image `_ocr_staging/...runtime_health_smoke...` folder while `model_benchmark.lock` remains present. Production folders, old result folders, ordinary Continue, supervisors, proof builders, and uploaders remain blocked. If the smoke trips again, the prior fuse is archived and the newest incident remains active.

Nearby or background FollowMe material is not foreground product identity. A narration clause such as `旁邊有 FollowMe 商品卡`, a wall poster, background advertising, or nearby signage must not trip the batch-wide narration fuse by itself. The unmistakable foreground fixture rule remains unchanged: a same-subject white vertical stand plus round floor base, or other sufficient structured physical evidence that conflicts with the structured answer, is still a material failure. Keep the production regression `test_nearby_followme_card_is_not_foreground_identity`; do not fix false positives by weakening the strong-fixture guard.

Three independent usable passes are the end of the content retry lane. If their fields differ, the finalizer uses only supported same-image evidence: two structurally valid distant votes finalize `遠景`; two single-unit votes finalize `單機`; model and price are retained only with at least two field-safe votes, and separate majorities may never be combined into a model/price pair that no two passes actually supported. Two strong same-subject FollowMe passes may establish FollowMe, but may not guess an unsupported variant. A fourth through sixth pass is reserved only for technical-integrity recovery and is never a content vote. Operator UI states `第三輪已完成／自動定案中` and `完成後立即排入逐張上傳`; it must not display isolation, a slow-model queue, or unspecified human adjudication. After a fuse smoke finishes, the backend still points at the smoke directory. Before relaunching `rerun_staged_candidates.py --resume-existing-then-continue`, safely switch the idle backend to the exact uniquely matched formal staging leaf with `/api/set_work_dir`; otherwise the runner must and will refuse because the current directory is outside its staging root.

`runtime_health_incident_sources` is persisted in the staging folder retry-state file and is scoped to that exact work directory. Repeated passes of one source do not prove batch-wide drift; a second distinct source with the same containable incident does. A contained unhealthy pass must carry `contained_for_stateless_retry` or `contained_as_unresolved`, must remain `auto_verified=false`, and must never enter an upload-ready manifest. Clearing or archiving a fuse begins a new explicitly audited monitoring epoch; do not fabricate earlier incident-source history when upgrading code that did not yet persist the registry. A `structured_authority_material_conflict:model` may use this same bounded lane only when the current pass independently proves exactly one `單機`, leaves `model=null`, has a valid owned price, and supplies at least two same-subject strong FollowMe fixture cues (or direct on-unit branding). The fixture proves only the FollowMe family, never M5/M7/Pro; pass 3 still unresolved if the variant remains unsupported. Missing/invalid price, weak fixture evidence, view/price conflict, or any second source with the same incident remains fail-closed and trips the durable fuse.

The upgraded boundary keeps its interlock until the backfill runner exits zero and a fresh builder pass proves zero remaining candidates, zero missing/conflicting/invalid sources, and verified count equal to the authoritative source inventory. Because an already-running older boundary process cannot load later script edits, `ocr_continuity_supervisor.ps1` independently rebuilds the remaining candidate set and restarts `rerun_staged_candidates.py --resume-existing-then-continue` whenever an idle gap still has current-revision candidates. A complete model/price row that lacks the guard revision must still receive `evidence_guard_revision_missing`; it can never disappear from recovery scans.

If the planned `backend_upgrade_v1945` lock owner PID is gone, the supervisor may take over only after the live backend proves v19.45, `compact-v2`, strict accuracy, idle state, and no active staged runner. It must retain the existing lock while starting or observing the resumable evidence backfill. The lock may be removed only when a fresh builder proof reports zero candidates, zero missing/conflicting/invalid sources, and verified sources equal the full 2026 inventory. An unreadable lock, live owner, wrong backend contract, active OCR, or ambiguous runner remains fail-closed.

Every supervisor child launch must call `Start-Hidden` with named `-File`, `-ProcessArgs`, `-OutFile`, and `-ErrFile` parameters. A positional array before scalar output paths is unsafe in PowerShell because array expansion can leave later parameters null. The helper rejects empty executables, arguments, or log paths before `Start-Process`, and every launch keeps `-WindowStyle Hidden`.

Historical recursive completion is inventory-bound. `recursive_ocr_flat_export.py` must return nonzero when any discovered folder is missing from the summary, has changed source identity, or remains `error/blocked`. The full-project marker stores discovery/summary SHA-256 and discovered/completed/error counts; the supervisor must reject a missing, stale, changed, or nonzero-error marker and resume instead of declaring completion.

### Direct uploader and historical-year authorization (2026-07-16)

`tools/rclone_drive_upload.py --execute` is a security boundary, not a trusted inner helper. It must independently rebuild the canonical manifest and shared proof, require the exact canonical receipt ledger, reject any remote other than `samsung_ocr_drive`, require the canonical `http://127.0.0.1:5000` health check, and recheck the runtime fuse, `model_benchmark.lock`, backend idle state, and owned staged/recursive/questionable runner inventory before preparation, after proof work, and immediately before every year copy. A supervisor or watchdog check never substitutes for these checks.

The shared proof binds the full pending ledger and exact next-batch CSV. The next batch must be a complete-field prefix across `source_path`, `file_name`, `year`, `period`, `drive_folder`, `size_bytes`, `content_sha256`, `status`, and `reasons`; pending rows with an empty next batch, duplicate identities, missing content hashes, scope drift, count drift, or any blocked row fail closed. Manifest generation hashes only the bounded next batch, copies that authorized content to staging, records the hash in `staging_map.csv`, and the uploader recomputes the staged SHA-256 during load and immediately before copy. The shared authority re-reads and compares every risk/finalization/audit/manifest authority after atomic proof write; any mid-build change removes the proof. `--years 2026` is mandatory in the current-year upload phase so unreviewed historical ready rows cannot ride along. `ready_pending_all_years`, `ready_pending`, `out_of_scope_ready`, and `uploaded_skipped` have distinct meanings and must not be collapsed in operator reports.

Any batch containing a year older than the current year additionally requires `_ocr_audit/historical_upload_authorization.json`. The authorization is written only after the exact current-year completion marker proves pending zero and after all-year questionable review proves the current `folder_discovery.csv` and `folder_summary.csv` have equal discovered/completed counts, zero errors, unchanged image counts/mtimes, and matching SHA-256. Direct historical-only execution must validate the same marker, backfill run, audit input, inventory paths, hashes, and counts; a stale prior-year marker is not authority.

Historical continuation itself has the same fail-closed boundary. `tools/historical_continuation_gate.py` is shared by the supervisor and recursive runner. A root-bound v1 request, current-revision 2026 completion marker, exact zero-pending shared proof scoped to 2026, zero current/future review rows, absent runtime fuse and benchmark lock, and an idle canonical port-5000 backend are all required to create the receipt. The recursive runner revalidates that receipt before every historical folder; direct CLI execution cannot replace it with a skip switch. The request and receipt bind SourceRoot, OutputDir, current year, evidence guard revision, marker/proof/review hashes and backfill identity. The formal request may be upgraded from the earlier explicit user request only with `--migrate-existing-request`, which preserves its original request time and adds these bindings.

Before the first historical photo, the runner creates or validates `_ocr_audit/source_inventory_v1.csv/json`. This frozen inventory has a stable folder ID independent of discovery order and one row per photo containing relative path, size, `mtime_ns`, and SHA-256 of the actual bytes. The receipt is atomically extended with the exact inventory paths, hashes and counts before historical OCR can start. An existing inventory never silently absorbs new, renamed, replaced or modified files; drift is fail-closed. The loop is driven from the frozen snapshot, verifies only the next folder before OCR/resume, and performs one final full-tree content verification before completion. This replaces the former full 150,321-photo discovery scan after every folder. Resume is accepted only when `image_count == success_records == copied_count`, all error counts and `copy_error` are zero, the copied manifest has exactly that many rows, and every current source file is byte-identical to its recorded target. Full-project markers and historical upload authorization additionally bind both inventory hashes and counts.

Remote receipt is content-based. `rclone lsjson --hash` must return exactly one same-name object whose `Size` and MD5 match the SHA-256-authorized staged file before its Drive ID enters `drive_upload_uploaded.csv`. Missing hash, duplicate name, mismatched content, readback error, timeout, or zero confirmation remains pending and exits nonzero or as an explicit retryable cycle. `--stop-on-timeout` must exit 124; default retry mode may absorb 124 only when another repeat cycle is guaranteed. Non-repeat execution and the final `--max-cycles` cycle return 124 instead of falsely succeeding. After any receipt change, rebuild the manifest and invalidate the old proof.

Required offline regression set for this boundary is `test_rclone_upload_safety_unit`, `tools.test_upload_gate_batch_binding`, current-year finalization, watchdog, supervisor, auto-rerun continuity, questionable-upload guards, and both Drive reconciliation suites, followed by `tools/run_critical_regressions.py`. Parse all edited PowerShell files before commit. These checks use temporary directories only and must not stop OCR, restart the backend, or open/reload a browser tab.

## Complete-screen frame-edge contract (`20260716.28`)

- A complete monitor is a physical bezel whose four outer sides and four outer corners are all inside the first original image. A visible panel, readable screen content, or mostly visible bezel is not sufficient.
- Before counting, explicitly inspect the physical monitor nearest the original left, right, top, and bottom image edges. If any outer bezel is cut by an original image edge, that monitor contributes zero to `complete_screen_count`.
- Canonical regression layout `台中旗艦-940`: left monitor exits the original left edge, center monitor is fully inside, and right monitor exits the original right edge. The truthful count is exactly one and the scene is a single-unit candidate. Never describe all three as complete.
- The inverse canonical regression `中清-1528` is a broad display wall with complete monitors above and below. A single-unit vote with no model, no price, no matched label, and no same-subject FollowMe fixture is weak evidence and may not supply a generic single-view majority. One structurally valid distant pass vetoes two such weak votes.
- Human-audited pixel authorities for both regressions are bound by the processed full-image SHA-256. `940` may never auto-verify as distant and `1528` may never auto-verify as single. This is an additional regression boundary, not a filename heuristic.
- A known-source authority conflict is contained to the same photo for at most three pristine stateless calls. It may trigger the next independent pass, but may not stop unrelated photos, create a fourth pass, or enter upload. The third-pass finalizer must either match the pixel authority or fail that photo closed.
- The main prompt, common output contract, pass-2 focus, pass-3 focus, immediate guard, three-pass finalizer, tests, and handoff must change together. A prompt-only edit is insufficient because a model can make narration and JSON agree on the same visual mistake.
- On detection, stop only the formal photo batch at a photo boundary. Keep the backend, dashboard, stream uploader, and existing browser tab alive. Run offline regressions, then a fresh isolated smoke containing at least `940`, `1528`, one true distant scene, and one FollowMe scene before replacing the backend and resuming formal work.
- Permanent live acceptance set `20260716_221131_225238` contains `940 / 939 / 1528 / 1385 / 646`: all five finalized verified in exactly three independent passes, with `prior_answer_exposed=false`, `prompt_contamination=false`, and revision `.28`. The canonical result for `940` is `單機 / complete_screen_count=1 / S32FM803UC / 12900`; `1528` and `1385` are distant; `939` remains FollowMe; `646` remains an ordinary single unit. Isolated acceptance results never count as formal progress or Drive uploads.

## Revision `.29`: bounded three-call completion and live-status repair (2026-07-17)

- A narration longer than 300 characters is not proof of prompt echo and must never stop a batch by length alone. Only explicit instruction/template echo trips `ui_narration_instruction_echo`; long natural narration is a permanent positive regression.
- `/api/status` publishes the authoritative `evidence_guard_revision`; the dashboard compares cards with that value and has no hard-coded guard revision. An active fuse is displayed as `內容守門修復中`, never as idle. The public fuse payload is allowlisted and excludes raw model output, narration, request IDs, and raw response objects.
- After an idle restart, a staging folder denominator comes from the cached `.ocr_source_map.json.items` count before any processed-count fallback. The 202601 formal staging denominator is 1,500, so `12/12` is a correctness defect; the truthful state is `12/1,500`.
- Human-audited pixel authority may settle a result only when the full-image SHA-256 matches and exactly three request-bound, stateless, uncontaminated model calls completed. It is a bounded manual adjudication, never a filename heuristic and never permission for a fourth call. Permanent authorities include `649=S27CG552EC/4990`, `668=S32FM703UC/9990` without FollowMe fixture evidence, `673=S27FG532EC/4990`, `674=S27D300GAC/3090`, `940=S32FM803UC/12900`, `942=S32CG552EC/6990`, `943=S27F612EAC/4990`, and `1257=C34G55TWWC/9900`; all are single-unit with `complete_screen_count=1`. If a process-boundary fuse occurs after attempt 1 but before its trace append, recovery is allowed only when persisted attempt numbering proves later calls are attempts 2 and 3, both traces are clean and bound to the exact audited SHA, the saved terminal row proves the three-call hard limit, and the recovery records the missing-attempt-1 trace explicitly. Never issue a fourth call to repair missing logging.
- A wide scene finishes as distant after three calls when all calls are image-bound and structurally report at least three complete screens with no model/price, while at least one call explicitly reports distant. Empty `label_ownership=matched` is not bound identity. This prevents a pair of malformed single labels from forcing an unresolved fourth-call loop.
- FollowMe evidence must be physically visible in the original frame: an off-frame/cropped round base cannot be claimed, a white pole alone is insufficient, a continuous shelf price rail is not an attached tray, and Smart Monitor M7/M5 or an `S32FM...` SKU alone is not direct FollowMe branding.

- Revision `.32` makes the three-call endpoint operational instead of leaving a permanent review queue. Three bound stateless passes may finalize a normal single from two identical non-FollowMe model/price reads or from a three-pass unique-main consensus; a broad scene may finalize distant from structural consensus. Two sufficient same-subject FollowMe fixture passes establish only the FollowMe family. If any pass supplies a different variant/price pair, clear both variant and price and upload as `FollowMe（型號未細分）／無價格`; this is safer than attaching one of several nearby cards. A photo-local narration/fixture conflict may be marked resolved only after this bounded adjudication succeeds; preserve the contained reason, set runtime health back to upload-safe, and never convert it into a fourth call or unspecified manual backlog. `tools/finalize_existing_three_pass_reviews.py` applies the same rules to already-finished three-call rows and enqueues each repaired result one by one.
- Revision `.33` catches a broad-scene label failure: when all three bound calls describe a whole row, display wall, multi-level shelf, or wide aisle; no call has a model, price, owned identity or FollowMe fixture; and at least two calls structurally count 3+ complete monitors, the fixed structure overrides three mistaken `view_type=單機` labels. Finalize `遠景／無型號／無價格` and upload immediately. This is content drift detection, not a reason for a fourth call or manual queue.
- Acceptance run `20260717_003815_889248` proves the four pixel authorities as `4 verified / 0 review / 0 failed`, exactly three passes each, revision `.29`. Formal restart then proved `664` finishes as distant on call 3 rather than entering review.
- Streaming upload is a separate always-on process. `worker_pid` must exist when pending jobs are nonzero; stale `idle` status with a dead PID requires a hidden worker restart. Progress reports must include both pending jobs and receipt growth, because canonical count may remain unchanged when a corrected result replaces the same source identity.
- A full `restart=true` is a new evidence run: clear the durable retry queue, attempt counters, prior-pass history, and incident-source state before scanning. Never restore `.ocr_retry_queue.json` on a full restart; otherwise the first real call can be mislabeled as pass 3. Formal run `20260717_012002_816887` proves 674 used actual calls `1,2,3` and finalized verified.

## Compact status and operator-facing metadata contract (2026-07-14)

- `/api/status` is a live monitor transport, not the durable history store. It must report `status_contract_version=compact-v2`, expose at most the bounded recent presentation window, and never include `thumb_b64`, base64 images, raw model output, or full evidence objects.
- The current bound is 12 presentation events (hard maximum 24). `recent_results` is compatibility-only, capped at 10 and stripped to display fields. Full per-photo pass history is loaded on demand from `/api/presentation_history/<source_item_id>`.
- A production status response must remain below 500 KB. Multi-megabyte polling is a UI correctness defect because parsing/backpressure can blank or stall the AI pane even when OCR is healthy.
- The results rail is an operator summary. Do not print retry reason, internal model id, timestamps, previous-result summary, decision codes, or expanded pass history on every card. Those fields belong in the click-through inspection/history view.
- Never render placeholder copy such as `第 未提供 輪 · 未提供 · 未提供`. A pass label is shown only when at least one pass metadata field exists; missing legacy metadata is hidden.
- Deploy frontend assets without an empty-file interval: copy hashed assets first and replace `dist/index.html` last. Backend source upgrades use `tools/safe_backend_boundary_upgrade.ps1`, require two consecutive quiet-boundary observations, complete and verify the fail-closed legacy trace migration before stopping the backend, verify the port-5000 process tree, then validate compact-v2, payload size, history API and fingerprint before releasing the interlock.

## Runtime health and boss-facing idle UI contract (2026-07-15)

The monitor is part of the correctness boundary. Progress-only monitoring is insufficient: every observation must also check content quality, photo/narration/card identity, and upload isolation. If any dimension drifts, stop or retain the durable runtime fuse before more photos accumulate.

### Mandatory pre/post-change checkpoint

Before every operational or code change, re-read this section, `SAMSUNG_OCR_EXPERIENCE_SKILL.md`, and the latest `docs/continuity_handoff.md`. After the change, verify all four dimensions again: content accuracy, presentation identity/continuity, process uniqueness/hidden launch, and upload isolation. A previously fixed defect is a permanent regression case, not an informal reminder.

The durable `presentation_sequence` is boss-facing state. `/api/status` must expose the orchestrator's history-recovered counter even when the bounded live queue is empty after an idle backend restart. It may take the higher live-queue value while running, but it must never replace a valid durable counter with zero. `tools/test_presentation_history_api.py` permanently covers this restart case.

Legacy history can contain a high sequence followed by a process-reset segment starting at one. Once a newer service resumes from the recovered logical total and persists absolute cumulative values, later restarts must adopt those absolute values; they must not add the old segment again. The permanent history regression covers two restarts so the boss-facing total can neither reset nor inflate.

- Pass 2/3 must be stateless. Never send a prior answer, correction wording, previous price/model, invalid model output, mistake-book example, or assistant message back to the model. Retry prompts describe only the current image task.
- Prompt, parser, evidence normalization, runtime-health checks, persistence, and UI normalization form one schema. A field added to one layer must be accepted, copied, guarded, persisted, rendered, and regression-tested by all relevant layers.
- The production prompt must not contain a complete copyable JSON answer example. The model may emit one JSON object with a readable `narration`; the UI must render that narration, never raw JSON.
- Repetition detection examines narration text, not repeated structural evidence keys. Exact nested `evidence` may be flattened only when it contains exclusively allowed evidence keys and creates no duplicate; otherwise fail closed.
- Explicit `auto_review_required=true` wins first. Otherwise an accepted decision or `auto_verified=true` must not be downgraded by the legacy placeholder `review_status=待審核`.
- While running, the existing presentation queue remains authoritative. While idle, the main photo, filename, AI narration, and top card must all fall back to the newest completed item from the selected work directory; never display “下一張判讀中” while idle.
- `/api/presentation_history?scope=current_batch` is the only recovery source for the right result rail. The response includes the selected directory's `source_item_ids`; the frontend must remove restored/session cards outside that set. Global pass history is for per-photo audit, not the current-batch rail.
- An idle backend may have an empty `current_run_id`; empty is never a valid rail identity. In that state the history API may recover only the newest nonempty durable run within the selected source-ID scope and must return that recovered run ID. If no nonempty run exists, return no cards. The frontend must clear/refuse a blank-run response. Never reinterpret an empty run as permission to load every legacy no-run event.
- Source identity scopes a work directory, but it does not distinguish repeated trials of the same photos. Every batch start must create a stable `run_id`; compact live events, durable presentation history, `/api/status.presentation_run_id`, the history recovery response, and the frontend result-rail key must carry that exact run. Recovery must filter the exact active run, or the latest nonempty run after a restart. Reusing source IDs without run scoping is a cross-run contamination defect and requires stopping the trial.
- Every batch writes `.ocr_presentation_run.json` atomically inside its work directory before the runner is marked active. Work-directory switches and backend restarts load that marker so runtime recovery cannot substitute a newer smoke run that used the same original photos. A legacy work directory without a marker keeps the orchestrator's active `current_run_id` empty; it must never mutate active runtime state to a newer trial run. This is separate from the read-only idle rail endpoint above, which may display the newest nonempty scoped durable run without making it active. Switching directories clears `current_file`, `stream_file`, `latest_result_file`, stream text, presentation queue, recent/session results, and retry interpretation state before the new directory is shown.
- Frontend recovery must filter both the current source-ID set and the exact history-response `run_id`. Browser session storage is versioned whenever this scope contract changes so already-contaminated cached cards cannot win a newest-timestamp merge after the backend is corrected.
- Compact live presentation events must retain `evidence_guard_revision`, `auto_verified`, `auto_review_required`, and `review_status`. Omitting them can make accepted cards look unresolved or hide an obsolete guard, so transport compaction may never remove correctness-state fields.
- The final completion event can arrive in the same status response that changes `is_running` to false. Result-rail hydration must consume every completed event regardless of the running flag. A completed batch showing `N-1` cards is a correctness defect, not an acceptable polling delay; compare the idle backend count, exact source identities, latest filename, card count, and review-label count before declaring the UI healthy.
- A dashboard verification is incomplete until the existing browser tab proves: total progress remains visible, layout remains 50% main preview, latest photo and narration match, result-card count matches the selected batch, review labels match backend metrics, and no prior-batch card appears.
- Runtime-health validation grows from an isolated 5-photo smoke to a 15-photo smoke covering normal single units, FollowMe, true distant views, missing model/price, label ownership, and price/brand conflicts. Keep the production fuse and benchmark lock active throughout. Only after both content and UI evidence pass may the active fuse be archived and production work restored.
- Backend replacement is allowed only at a proven idle boundary and must use the project `.venv` plus a hidden process window. Verify the existing port-5000 owner before stopping it, verify exactly one replacement listener afterward, and never use a launcher action that opens another browser when an existing tab is already in use.
- `tools/ocr_upload_watchdog.ps1` must exit before summary repair, backend restart, recursive/questionable launch, proof refresh, or uploader launch whenever `_ocr_audit/model_benchmark.lock` exists. This planned backend/backfill interlock is not a stale ordinary watchdog lock and may be removed only by the current-revision zero-candidate proof boundary.

Permanent checks for this contract live in `tools/test_v1945_evidence_contract.py`, `tools/test_presentation_history_api.py`, `tools/test_presentation_soak.py`, and `tools/run_critical_regressions.py`. Build `dashboard/dist` and inspect the real browser after every UI or history-scope change.

### Contained contradiction versus batch failure (2026-07-16)

Content monitoring must distinguish a contradiction that escaped the evidence gate from one the gate already isolated. A structured/narration contradiction with no explicit containment remains a batch-stopping `structured_narration_conflict`. If the final record explicitly carries `auto_review_required=true`, `evidence_unresolved=true`, or a recognized manual-review status, it must stay excluded from automatic acceptance/export and be counted as `contained_review_conflicts`; it must not stop later candidates from being evaluated. This does not weaken the evidence contract: the row remains unresolved, cannot become `auto_verified`, and cannot enter upload readiness.

The isolated `.11` seven-photo convergence smoke is the permanent reference set. Original-image review expected four true distant views and three must-review counterexamples. Revision `20260715.11` produced exactly 4 verified and 3 unresolved; all 21 passes recorded `prior_answer_exposed=false` and `prompt_contamination=false`. An executor that stops this set only because one of the three unresolved rows contains contradictory narration is over-stopping, not protecting accuracy. Regression coverage must preserve both directions: uncontained contradiction stops the batch; explicitly contained contradiction is recorded and processing continues.

Revision `.12` adds a second permanent prompt-conformance smoke: the three single-unit photos ending 665, 666, and 667. Their raw model JSON—not merely normalized output—must contain all four evidence fields on every pass. The expected pacing is 665 two passes for price-difference confirmation, 666 one pass, and 667 two passes for short-SKU completion confirmation; any missing evidence field, third pass caused only by schema omission, prior-answer exposure, or prompt contamination is a regression. Passing content/API tests does not substitute for the existing-tab browser verification required by the UI checkpoint.
# Presentation Synchronization Iron Rule

The backend `presentation_id` is the sole identity key. The active photo, AI live interpretation, active right-side placeholder, revealed card, and inspection modal must all use one immutable presentation snapshot and the same `presentation_id` and sequence. Running UI state must never join by filename, index, source path, `current_file`, `stream_file`, or `recent_results`. Identity is stronger than freshness: while an active snapshot exists, never prefer a newer live stream, latest result, or history row. Reveal a right-side card only after that snapshot's narration completes; never discard the active item through watchdog or backpressure. A previous image may remain visible only while its previous presentation remains active. Once the active key advances, hide the old image until the new image bearing the same key is ready; an image-load failure must never pair the old image with the new narration. Completed events prefer the same result's detailed `thinking` / `full_ai_narration`, and cross-photo narration fallback is forbidden. Any dashboard presentation change requires the deterministic 500-item soak, same-ID assertions for photo/narration/card, and a rebuilt dashboard.

Staged rerun finalization must fail closed on any `structured_narration_conflict`: if the saved structure says `單機` but its own `thinking`, `stream_buffer`, or raw JSON explicitly concludes `遠景`, do not merge or publish the group. The negation context around single-subject phrases must include wording such as `無法鎖定唯一主角` and `無法讀取唯一主角自己的規格`; those sentences are not positive single-unit evidence. The intended review order remains: first audit FollowMe incorrectly classified as distant, then rerun single-unit rows missing model or price in second/third passes.

## Cross-photo semantic contamination checkpoint (revision `.16`)

Every model JSON must echo the exact full 128-bit per-call `RequestID` in `request_id`. A missing identifier may receive only one pristine-image transport retry; a mismatched identifier is a runtime-health failure and trips the durable fuse. The final runtime-health gate must itself require `request_id_verified=true` and a 64-hex `input_image_sha256`; upstream code cannot merely claim it performed the check. The pipeline records the SHA-256 of the actual full-image payload so the trace can prove which bytes were sent. Separately, when two adjacent, distinct source identities produce the exact same model and price, regardless of whether the prior photo was verified or already review-required, the later photo can never be verified on pass 1. The suspicion marker must survive pass history, force all three stateless passes, and remain unresolved for human or heterogeneous-model review even when the same wrong core repeats. Two identical answers can never wash it clean. This duplicate-core signal is guard-only and is never exposed to the model. This is a drift detector, not a progress counter.

## 2026-07-17 `.33` 同張內容收斂、斷點修復與持續上傳

- `structured_authority_material_conflict:model` 若是唯一健康理由，且同張回應仍證明 `單機 + unique_main=true + label_ownership=matched + complete_screen_count 1..3`，屬同張內容不確定，不是跨照片記憶污染。第 1、2 次必須保存為同圖無記憶內容票，第 3 次交由三輪定案器；不得升級為整批 fuse。價格可缺，但若存在必須通過荒謬價格檢查。
- 已綁定人工像素權威時，`known_source_expectation_conflict + structured_authority_material_conflict:model` 可視為同一個照片本地事件，前提是完整影像 SHA-256 精確命中權威。提示污染、前輪答案暴露、request/image 綁定錯誤、價格衝突或任何額外理由仍立即 fail closed。
- 三輪定案器可把上述本地 model omission 當作結構票，但永遠不得用 narration 回填明示為空的 model。三張寬景結構票若都為 3+、無型號／無價格且敘述為整排／展示牆，可定案為 `遠景／無型號／無價格`；單機只保留至少兩輪安全支持的欄位。
- 程序邊界若已消耗三次呼叫但遺失其中一筆 trace，只可在相同 `source_item_id`、相同完整影像 SHA-256、最新相鄰 trace 為 `1+3` 或 `2+3`、結果檔保存 `three_call_hard_limit_reached`，且像素權威精確命中時修復；不得第 4 次呼叫。沒有 canonical `YYYYMM` period 的 smoke trace 不得壓過正式 trace。
- 既有三輪修復必須先以冪等方式排入逐張上傳，再原子寫回 verified 結果；禁止先寫 verified 後因 enqueue 失敗留下半完成狀態。
- 串流上傳若 pending 增加但 receipt 不動，必須核對 lock PID。PID 已不存在時將 stale lock 歸檔後只恢復 uploader，不重啟 OCR。驗收需看到 `uploaded/canonical/last_uploaded_at` 實際前進。
- Windows 上 Dashboard／監控讀取 `status.json` 時，可能在 `os.replace` 的瞬間短暫阻止目的檔替換。`stream_drive_upload.py` 必須以有限退避重試狀態檔原子替換；此類展示狀態寫入競爭不得終止 durable uploader。只有重試耗盡後才可失敗，且 pending 工作仍須原封不動保留。恢復驗收仍要求唯一 hidden worker、無可見終端機、pending 下降及正式 receipt 增加。
- 低功耗視覺稽核輸出的「候選定案」不是上傳權威。必須逐張核對原圖完整螢幕數、唯一主角、FollowMe 實體、同主體牌面與型號／價格歸屬；候選與像素不符時，以 `source_item_id + source_file_sha256 + input_image_sha256` 三重綁定人工像素權威。只有已完成三次獨立、request-bound、無前輪答案的照片可離線套用，嚴禁增加第 4 次模型呼叫或用檔名建立通用規則。
- 現行 Chrome 既有分頁在縮放後有效寬度下，header/status 於 `max-width:2400px` 換成兩欄兩列，確保總進度、目前資料匣、目前檔案與執行狀態可見；主預覽、LLM 自言自語與右側累積卡片的既定半螢幕比例不得改動。
- 2026-07-17 04:56 正式證據：`202601 131/1,500`、verified 131、review 0、failed 0、fuse inactive；逐張上傳 `81→105`、canonical `53,052→53,072`、pending 1，最近上傳時間 04:55:56。636 已定案並上傳為 `單機-S24F332EAC-✓＄2390`，637 已在隔離驗收定案為遠景。
- 完工估算固定公開兩層：依目前可持續淨產能約 1,666 張／日，84,990 張剩餘量的實測目標日為 `2026-09-06`；對長官保守承諾日為 `2026-10-31`。任何停機日都必須重算，不得只回報「持續執行中」。

## 2026-07-17 `.34` 內容跑歪監控與三輪必結案

- 監控不得只看張數。正式批次每個檢查點都要抽查最新照片、三輪原始價格、結構欄位、最終定案與實際上傳檔名；發現錯誤要在污染更多照片前停在安全邊界，介面服務仍保持在線。
- 三輪最後一次使用最強價牌放大。若前兩輪價格只因多插入一個數字而形成錯誤多數、第三輪 JSON 與同輪獨白一致、價牌歸屬 matched，且長值相對官方參考價超過五倍而短值仍在三倍內，採第三輪照片實讀值；官方價只用來辨識多字錯誤，不得覆寫照片價格。`太平-1105` 永久回歸為 `S27CG552EC / 7,490 / ↑`，不是 `74,990`。
- 同圖三輪若一輪明確給出 `3+` 完整螢幕、`unique_main=false`、無可歸屬型號與價格，其餘兩輪也只是描述整排／展示牆且沒有身分欄位，必須在第三輪結案為遠景；不得留下慢模型、人工裁決或技術待辦。`太平-1099` 永久回歸為遠景、無型號、無價格。
- 修復工具不得用同來源的兩筆 `[2,3]` 尾端覆蓋同一正式 run 已存在的完整 `[1,2,3]` 證據。已完成但被新版像素權威更正的列可冪等重新排入逐張上傳，不增加第 4 次模型呼叫。
- 續跑證據：`140/1,500`、verified 140、review 0、failed 0、fuse inactive；修正後 `1105` 已以 `↑$7,490` 上傳。完成日基準仍為量測 `2026-09-06`、保守承諾 `2026-10-31`。

## 2026-07-17 `.35` 已知像素三輪定案與不中斷續跑

- `known_source_expectation_conflict` 比對預期空價格時，`None`／空字串代表「無價格」，不得與字面文字 `"None"` 比較而製造永久衝突；實際非空價格仍必須被攔截。
- 精確命中 `KNOWN_SOURCE_EXPECTATIONS` 的同張照片若同時出現 `structured_narration_followme_conflict`，只可在第 1、2 次保存為同圖無記憶內容票並前進到第 3 次；不得在第 2 次停整批。任何額外的 request/image、prior-answer、prompt、cross-photo 或價格完整性理由仍立即 fuse。
- 人工像素權威只能在第 3 次且三筆都為相同 input SHA、request-bound、independent、`prior_answer_exposed=false`、`prompt_contamination=false` 時套用。套用後的第三輪若 evidence contract 與 runtime health 均健康，三輪定案器必須回傳 verified，不得再被前兩輪的內容差異打回 unresolved。這不是跳過模型，也不得產生第 4 次呼叫。
- 永久實拍驗收 run `20260717_072657_073759`：317=`遠景/null/null/count3`；318=`FollowMe Pro M7 43\"/17990/count3` 且只保留 direct branding；1319=`S24F332EAC/2590/count1`；1320=`S27D300GAC/3290/count1`；1321=`S27F612EAC/4990/count1`；1325=`FollowMe M7 32\"/14990/count3`。6/6 verified、0 review、0 failure、每張恰三輪；18 筆 trace 的 request binding、獨立性與記憶污染欄位全數通過。
- 完整 `tools/run_critical_regressions.py` 通過後，正式 port 5002 從 192/1,500 原位續跑，不使用 restart。既有分頁驗證 `1109→1110→1111→1112` 四張的目前檔名、預覽、LLM 逐字區與最上方卡片完全一致；202601 子進度 196→199、上傳總數 53,121→53,125、無水平溢位。
- 串流驗收三筆 `.35` 均完成 working→receipt→canonical，source key／原始路徑／目標檔名／Drive ID 各自唯一；`.35` failure=0、duplicate filename/Drive ID=0。修正舊來源時 canonical 不一定每張 `+1`，必須以逐筆 receipt 與 canonical 同 ID 證明閉環。
- 速率管理：84,990 張剩餘要在 `2026-09-06` 完成，需平均約 1,667 張／日或 69.5 張／小時；低於 802 張／日已無法守住 `2026-10-31` 保守承諾，必須在固定報告點列為速度事故，而不是只說持續運行。

## 2026-07-17 `.41` 內容漂移守門與誠實交期

1. 監控必須抽看原圖、自然敘述、結構欄與最終結果是否一致；只看到 `processed`、輪次或 GPU 在動不算健康。發現系統性內容漂移時，只在照片邊界停止正式 OCR，port 5002 Dashboard 與逐張 uploader 保持在線。
2. 敘述若說鄰機「部分可見／局部露出／未見完整外框／被照片邊界裁切」，結構不得把它算成完整台數。完整台數仍以原圖四邊四角皆在畫面內為唯一標準。
3. 鄰機、背景、螢幕畫面或附近宣傳卡上的 `Odyssey / G7 / G8 / M8 / Smart Monitor` 不得借給主角。沒有同主體實體價牌證明時，只保留可逐字讀出的 SKU，不自行補系列名稱。
4. 人工像素權威只能綁定完整影像 SHA-256，不能依檔名猜答案；套用後仍須有三次同圖獨立呼叫、無前輪答案暴露、無提示污染。`.41` 真圖驗收 5/5 verified、每張恰三輪、0 review、0 failed。
5. 交期公式固定為：`ETA = 今天 + 剩餘張數 / 最近 24 小時現行 revision verified 且可逐張上傳的張數`。目標日期與實際預測必須分開。2026-07-17 基準為 259 張／日、剩餘 84,990，預測 `2027-06-11`；`2026-09-06` 目標需 1,667 張／日或 69.4 張／小時，為基準 6.43 倍。

## 2026-07-17 `.41` 相容熱修：單張 request 綁定隔離與無身分單機結案

1. 單一來源第一次出現 `request_id_missing` 或 `request_id_mismatch` 時，該回覆必須完全作廢：不進入視角／型號／價格證據、不進人工像素權威，但仍消耗該張的一次模型呼叫。只允許同張使用剩餘額度，總數仍不得超過三次。
2. 同一來源重複錯誤不代表另一張也被污染；第三次仍失敗時，該張留下終端技術結果，正式批次繼續下一張。第一個「不同來源」再出現同類綁定錯誤，才證明系統性 request 串線並寫 durable fuse。此分界同時避免整批被單一傳輸瑕疵卡死，也保留跨照片跑歪的主動停止能力。
3. 三輪全部為同圖、request-bound、獨立、無前輪答案／提示污染、runtime 與 contract 健康時，至少兩輪一致支持 `單機 + unique_main=true + count 1..2 + 非 FollowMe`，即可定案；型號與價格沒有兩輪安全證據就保持空值。`統一時代二-150` 與 `台中大遠百-389` 是永久測試案例。
4. `_weak_single_claim_in_wide_multiscreen_scene` 不得把「螢幕下方有 Samsung 品牌貼紙」誤讀為下方另有一台螢幕。只有 `另有／還有／可見` 等明示另一主體的用語，才能支持寬景背景判斷。
5. 正式事故為 `內湖旗艦-882` 第 2 輪 request ID 不符。修正前 durable fuse 正確阻止該回覆與上傳；完整 critical regressions 退出碼 0 後，fuse 留存到 `runtime_health_fuse_history`，正式批次從同張第 3 輪原位續跑並已跨到下一張，沒有重跑 431 張，也沒有第 4 輪。
6. 修復工具先排入上傳再寫回 verified。若排隊當下 fuse 尚在，job 會落入 failed；只能核對錯誤確為該 fuse 後，把這些精確 job 重新排回 pending。不得整批搬動其他 failed job。

## 2026-07-17 推論速度與分級判官契約

大量照片必須使用「快速首輪、風險照片才升級」的級聯流程，不能讓每張照片無條件跑滿三輪。

1. 第一輪由 production `qwen/qwen3-vl-8b` 讀取原圖與既有 deterministic crops。若結構、型號、價格與標籤歸屬完整且互不矛盾，第一輪即可結案並逐張上傳。
   - 這條也適用於 FollowMe：同一實機的直接品牌或充分強實體線索、自己的型號與店內價格都完整一致時，不強制浪費第二輪。
2. 只有下列照片升級第二輪／判官：
   - 遠景或多螢幕場景，需要排除被誤判的 FollowMe。
   - 單機缺型號或缺價格。
   - FollowMe 身分只有弱宣傳線索、實體證據不足，或完整螢幕數、唯一主角、價牌歸屬、敘述與結構互相矛盾。
   - request/image 綁定、輸出結構或跨照片污染偵測失敗。
3. 官方參考價的 `↑`、`↓`、`✓` 是首輪 OCR 完成後的 deterministic comparison。照片價格與官方價不同本身不是 OCR 錯誤，不得只因價差而額外呼叫 VLM；只有價牌文字模糊或型號／價格歸屬衝突才升級。
4. 2026 以前的照片不做即時官方價格查詢；只完成照片內容辨識與必要的風險複核。
5. 第二輪與第三輪必須是 stateless blind adjudication：只看同一張原圖與新 RequestID，不得帶入上一輪答案、摘要、理由或對話歷史。判官最多把整張照片的模型呼叫總數補到三次，不能出現第四輪以上。
6. 在單張串行 OCR 工作負載下，LM Studio production model 固定以 `--parallel 1` 載入；`parallel 4` 是並行請求吞吐設定，不適合這條逐張管線。context 先維持 32768，只有固定盲測證明 16384 不截斷影像／prompt 且準確率不退步後才能調低。
7. 異質判官不得直接換上 production。先用固定 50 張盲測與永久回歸照片比較完整正確率、遠景／FollowMe 危險誤判率、型號／價格欄位準確率與 latency。16GB GPU 不允許逐張 load/unload 27B 判官；若無第二張 GPU，優先評估可常駐或可在維護窗測試的 7B–12B VLM。
8. 效能監控同時報告每次推論 median/P90、每張平均模型呼叫數、首輪結案率與升級率。只看「每輪秒數」或只看「處理張數」都不足以判斷是否跑歪。

## 2026-07-17 新月份優先插入（202606）

`tools/prepare_period_priority.py` 只負責在不中斷正式 OCR 的情況下，為新到月份建立固定 staging、逐張原始來源對照、候選 CSV 與稽核目錄，並以原子方式加入資料夾清單；它不會自行切換後端或重啟批次。`--execute` 前先 dry-run，執行後必須核對照片數、source-map 筆數、manifest `complete=true` 與來源位元雜湊。

正式切換只能在照片邊界：先停止目前資料夾並保存 processed/verified/upload 斷點，維持同一個 Dashboard 分頁與 port 5002；再把工作目錄指向唯一的 202606 staging leaf，以 `restart=false` 啟動。每張通過守門後立即進逐張上傳；202601 的斷點保留，202606 完成後接續，不得整批重跑。

### 202606 完成後的唯一接續方式

- `tools/continue_after_period_priority.py` 是新月份插入後的窄範圍交接守門。它不擁有後端、不重載模型、不另開 Dashboard，也不建立第二套 OCR；只監看明確指定的 202606 staging leaf。
- 202606 若意外變成 idle 且 `processed < total`，守門只對同一 leaf 呼叫 `restart=false` 的 Continue。若資料夾不是明確指定的 202606 或保留中的 202601、runtime fuse 存在、後端 contract／accuracy profile／evidence revision 不符，必須失敗封閉，不猜測下一個資料夾。
- 只有 202606 的每一張都已成為唯一 `auto_verified=true`、`auto_review_required=false`、`stream_upload_queued=true` 的終局記錄，且每個 source_item_id 都同時通過三層像素綁定：終局記錄的 `input_image_sha256` 必須等於正式影像前處理實際送模的 full-scene bytes SHA（長邊超過 2560 時為 EXIF 轉正、等比縮至長邊 2560、RGB JPEG quality 95；否則為 raw bytes），Drive receipt 的 `source_sha256` 必須等於 `original_source_path` 原圖檔案 SHA，receipt 的 `published_sha256` 必須等於發布檔 SHA；三者不可混用。receipt 另須具有相同 run、revision、非空 Drive ID、遠端路徑，failed 目錄沒有同一 source_item_id。最後還必須滿足 `processed=success=verified=total`、failed/review/unknown 全為 0、後端 idle、唯一 uploader 存活、逐張上傳 `pending=0/working=0`，才可把同一後端切回保留的 202601 staging leaf。
- 切換前必須先證明候選 CSV 第一群組與 202601 staging 的 period、來源資料匣 SHA-1 短碼、照片數完全相符；202606 不得混入候選群組。切換後再次讀回 `/api/status`，證明同一後端真的在指定 leaf 以 `restart=false` 運行。
- 接回 202601 後，只能有一個 hidden `rerun_staged_candidates.py --resume-existing-then-continue --keep-staging`；它依候選 CSV 固定順序完成 202601、202602、202603、202604、202605。monitor 的單例鎖保留到 runner 以 exit 0 結束；summary 必須晚於本次啟動，且五個月份的來源 digest、staging、queued/staged/processed 數均完全相符。任何舊 summary 或其他 staged runner 都會阻止第二個 runner 啟動。
- monitor 與 runner 都使用背景無視窗模式，stdout/stderr 寫入 `logs/`。成功交接證據寫入 `_ocr_audit/period_priority_continuation_receipt.json`；異常、90 分鐘無進度或整體逾時寫入 alert 並失敗封閉，不得用反覆彈出的終端機重試。
- `台中LalaportSES-301` 的完整原圖清楚顯示 Samsung Follow Me 4K 品牌、同一前景主體的白色直立移動架、托盤與圓形底座，但沒有足夠像素證據安全細分 M5/M7/Pro 或價格。其 full-image SHA 已加入人工稽核像素權威：三次獨立、request-bound、無記憶污染的呼叫完成後，必須降階結案為 `單機／FollowMe（型號未細分）／無價格` 並逐張上傳，不得保留模型猜測的 `Pro M7 43"`，也不得呼叫第 4 次模型。

Dashboard 的正式總進度必須把 staging leaf 透過 `.ocr_source_map.json` 映回原始資料夾後再合併 live stats；不能直接拿 staging 路徑和 `folder_discovery.csv` 的來源路徑比較。新增月份處理第 1 張後，`overall_progress.processed_images` 就必須增加，不能等整個月份完成才跳數。永久測試為 `test_staging_progress_maps_to_original_folder_and_moves_total_counter`。

## 2026-07-17 request binding 單張隔離與上傳版本同步（`.43`）

- 第三次模型呼叫若因逾時／回傳缺少 request echo 而得到 `request_binding_unverified`，該次內容一律作廢，不可參與分類、型號或價格投票；但這個單張傳輸錯誤也不得停止整個資料夾。`request_id_missing`、`request_id_mismatch` 與正規化後的 `request_binding_unverified` 必須走同一條單張 containment 路徑。
- 每張仍維持最多三次模型呼叫，禁止第 4 輪。只有在前兩次都是同一 input SHA、獨立、無前輪答案、request-bound、runtime healthy、且完全同意同一個非 FollowMe 單機 SKU／價格與主體歸屬時，才可用 `two_bound_pass_consensus_discarded_unbound_third` 結案；第三次未綁定回覆要保存在 `discarded_unbound_call` 稽核欄位，不得冒充有效票。
- 實例 `M-台中市-西屯區-TK3C-新大雅-1178.jpg`：前兩次有效結果皆為 `單機／S32CG552EC／6,990`，第三次 request binding 失敗。離線 recovery 以兩次有效共識結案、沒有第 4 次呼叫，result、retry state、fuse history、recovery receipt 與逐張 upload job 依序原子落盤；Google Drive exact readback receipt 已於 `2026-07-17 19:34:38` 取得。
- evidence revision 變更後，backend 與 `stream_drive_upload.py` 必須在同一照片邊界同步換版。只換 backend 會讓舊 uploader 把新 revision job 判為 `stale or invalid stream upload job`。本次 `.43` 邊界曾產生 32 個可證明的同版本失敗 job；只把 `revision=.43` 且 error 完全相符的 32 個 job 移回 pending，舊有 95 個其他失敗紀錄不動，換版後 uploader 已開始取得新收據。
- Dashboard 視覺驗收必須直接在既有 Chrome 分頁完成，不得新開分頁：精確總數、資料夾子進度、目前圖片／檔名、LLM 自然敘述、右側累積卡片及上傳 pending／總數都要同時核對。`.43` 驗收看到 `65,578/151,714`、202606 `247/1,393`、LLM 即時文字與圖片同步、右側卡片累積、近期平均 `13.39 秒`；修復上傳程序後精確總數續增至 `65,589`，新收據也由 `53,516` 增至 `53,517`。

## 2026-07-17 單張內容衝突不得停批與 Drive 新根目錄（`.45`）

- 持續運轉鐵律的判斷邊界：單張照片的 `view_type/model/price/FollowMe` 內容矛盾是 photo-local；只允許同一照片最多三次獨立、request-bound、無前輪答案的判讀，之後使用三輪共識或已綁定的人工像素權威如實定案。它不得建立 active 全域 fuse、不得呼叫第 4 次，也不得阻塞下一張。請求綁定、跨照片記憶、提示詞污染、版本整體失配等能影響多張照片的系統性技術錯誤，仍維持全域 fail-closed。
- `微風南山-742` 在三次呼叫後，模型把寬廣 Samsung 展示牆誤帶入唯一型號／價格；第三輪同時留下 model 與 narration FollowMe 衝突，舊規則誤停整批。原圖人工像素權威固定為 `遠景／complete_screen_count=5／無型號／無價格／無 FollowMe 實體證據`。`tools/recover_photo_local_content_fuse.py` 驗證三個不同 RequestID、同一 input SHA、同一來源原圖 SHA、三次獨立性與 source identity 後離線結案；第四次呼叫為 false。
- 人工像素權威在遠景分支套用後，必須清除第三輪模型留下的 `structured_authority_blocked_fields` 等暫時衝突旗標，再以權威後的結構與自然敘述重跑證據契約；否則正確結果仍會被舊旗標二次阻擋。
- `.45` 恢復證據：正式進度由 202606 `356/1,393` 增至至少 `369/1,393`，總盤到 `65,700/151,714`；runtime fuse 不存在；Dashboard 實際顯示正在執行、LLM 自然語言、同步照片／檔名、右側累積卡片與上傳數。742 以遠景檔名立即上傳，Drive ID `1x5naroxGTEOgrScGsbIrMf7PsG7Z-y-W`，exact readback receipt revision 為 `.45`。
- Google Drive 正式根目錄改為 `00_商化照片`（ID `16X5qALC3zRYc7PpnexXLYprorBzBtT_f`）。`2022`、`2023`、`2024`、`2025`、`2026` 直接位於該根目錄，不再有 `已整理` 中介層；舊來源資料夾 ID `1xBaWDRjlcP-gMV-bM0K1S4gOJZ0QJJHK` 已搬空但保留。`202607` 明確不在本次搬移範圍。
- rclone remote `samsung_ocr_drive` 的 `root_folder_id` 必須是新根 ID。逐張 uploader 只寫 `年份/完整新檔名`，每張完成即進持久化佇列；網路慢只允許 pending 累積與續傳，不能讓 OCR 等待整年或整批才開始上傳。

## 2026-07-18 級聯推論容量基準與介面真實狀態

### 正式容量基準

下表是 2026-07-17 以最近正式批次量測建立的容量規劃基準，用來防止系統日後退回「每張無條件過度複核」：

| 機制 | 平均模型呼叫 | 約耗時／張 | 理論日處理量 |
|---|---:|---:|---:|
| 現行過度複核基準 | 2.17 次 | 64.9 秒 | 約 1,332 張 |
| 快速首輪＋風險件一次判官 | `1 + 44.8%` | 23.4 秒 | 約 3,685 張 |
| 快速首輪＋風險件最多兩次判官 | `1 + 44.8% × 2` | 36.9 秒 | 約 2,342 張 |

- 「單機＋型號清楚＋店內價格清楚＋價牌歸屬一致＋結構無衝突」必須首輪結案並立即排入逐張上傳；`↑`、`↓`、`✓` 由 deterministic price comparison 產生，不是加跑模型的理由。
- 只有遠景／多螢幕疑似 FollowMe、單機缺型號或缺價格、FollowMe 實體證據不足、主體／價牌／結構互相衝突、request/image 綁定或污染檢查失敗，才可升級。
- 第二、三輪必須 blind、stateless、同圖新 RequestID；整張照片模型呼叫上限固定三次。單張內容衝突不得停止整批，也不得出現第四輪。
- 以當時剩餘 84,990 張及新增 202606 的 1,393 張、共 86,383 張估算，在 24 小時穩定運轉且沒有長時間卡住的前提下：平均一次判官約 23–24 天，目標約 2026-08-10；多數風險件需要兩次判官約 37 天，目標約 2026-08-23。這是容量規劃，不得拿來取代以最近 24 小時 verified/uploaded 實績重新計算的動態 ETA。
- 品質排序固定為：照片辨識正確 > tokens 使用 > 完成時間。節省 tokens 的正確方式是讓容易件首輪結案、把判官留給風險件，不是降低證據標準。

### 介面與目標狀態不得混淆

- Codex 工作回合顯示「暫停／等待／已結束」不代表 OCR 停止。Dashboard 唯一權威是 port 5002 `/api/status` 的 `is_running`、目前檔名、processed/verified、presentation sequence、stream upload worker 與最新更新時間。
- Dashboard 不得因 Codex 回合切換、監控喚醒、文件工作或新訊息而顯示假待機。若 port 5002 正在前進，介面必須顯示「正在執行」；若即時資料停止更新，應修復顯示同步，不能重啟正常 OCR 來掩蓋前端問題。
- 最高鐵律不變：辨識、介面與逐張上傳持續運轉；單張異常只 containment 該張。除非證明為會污染多張照片的系統性錯誤，否則不得建立全域 fuse 或阻塞下一張。

## 2026-07-18 `.46` 完整單機的 `count=2` 窄範圍首輪結案

- `complete_screen_count=2` 不可單獨成為強制複核理由。若同一輪同時滿足：`view_type=單機`、`unique_main=true`、`label_ownership=matched`、型號與店內價格皆非空、自然敘述明確指出中央主螢幕完整且左右鄰機都被原圖邊界裁切、沒有其他完整展示列，則保留原始結構值供稽核，但不得只因 `count=2` 推入第二、第三輪。
- 上述例外不得擴張到 `count>=3`、遠景、缺型號、缺價格、`unique_main=false`、價牌歸屬不明、FollowMe 證據衝突、request/image 綁定失敗或污染疑慮。這些情況照原守門複核。
- 真實歷史重播：`屏東-SF-446` 第一輪為單機／S24F332EAC／2,390／唯一主角／價牌 matched，且左右鄰機均被邊界裁切；新版首輪 `verified=true`。`屏東-SF-445` 第一輪仍是遠景／count=3／無唯一主角，維持複核並由第三輪定案。
- 這是級聯推論的效率修正，不是降低正確性標準。正式換版只能在照片邊界，保留同一 port 5002 Dashboard、同一 staging 斷點與逐張上傳佇列；換版前相關 evidence、三輪、runtime-health、即時重試、上傳及介面測試必須全數通過。

## 2026-07-18 `.47` 單張模型欄位衝突與寬景結案

- `structured_authority_material_conflict:model` 只表示同一回覆的自然敘述提到型號／品牌，但結構欄位明確留空；結構權威仍須清空該型號，該輪不得上傳，但這不是跨照片記憶或 request 串線證據。只要 request/image 綁定、獨立輪次、無前輪答案、無提示污染、價格格式與證據契約仍有效，就限制在同一照片三次呼叫內處理，不得建立全域 fuse。
- `view_type`、`price`、request binding、提示污染或跨照片重複核心證據衝突仍是不同風險；不得藉上述 model-only 例外放行。第三輪仍無法定案時，只能留下該張終局保守結果並繼續下一張，禁止第 4 次。
- 三個同圖、request-bound、獨立輪次若都回報至少三台完整螢幕、敘述一致為整排／多層展示牆，且沒有同一實機 FollowMe 強證據，三輪定案必須採用幾何事實：`遠景／無型號／無價格`。附近可讀的非三星價牌或單輪型號文字不得推翻 3+ 台完整陳列，也不得讓單張照片停止整批。
- 正式事故 `M-新北市-中和區-TK3C-中和-1333.jpg` 原圖清楚顯示上下兩層多台完整螢幕。第 1 輪錯回 `單機/count=7/2,988`，第 2 輪錯回 `單機/count=7` 並因 prose/structured model 差異觸發全域 fuse。這是 photo-local 跑歪且守門處置層級錯誤；`.47` 必須讓第 2 輪隔離、完成第 3 輪後以寬景幾何定案並繼續後續照片。
- 永久回歸必須同時證明：上述真實形狀可結案遠景、既有寬景／FollowMe／價格／request binding 測試不退步、全工具測試通過；解除正式 fuse 前仍需保留舊 fuse 歷史與新版驗證證據。

### `.48` 一輪結構遠景否決兩輪 `3+` 台錯標單機

- 若三輪都是同一 input SHA、request-bound、獨立、無前輪答案，三輪結構皆為 `complete_screen_count>=3`、敘述皆明確是整排／多層螢幕陳列、沒有 FollowMe 強實體證據，且至少一輪正確輸出 `遠景`，則該結構遠景必須否決另外兩輪的寬景單機錯標，定案為 `遠景／無型號／無價格`。
- 此否決不依賴型號或附近價牌：多螢幕陳列中的單輪可讀價牌不等於唯一主角歸屬。結案規則為 `distant_structural_veto_over_wide_geometry_single_votes`；只使用既有三輪，不得加跑第 4 輪。
- `中和-1333` 的實際第三輪為 `遠景/count=6/unique_main=false/label_ownership=not_visible`，正確否決前兩輪 `單機/count=7`。舊 `.47` 已做到不再全域熔斷，但定案器仍留下技術待處理；`.48` 補齊終局結果與逐張上傳，不把「繼續跑」誤當成「該張已完成」。
- 若照片邊界換版造成已消耗的第 2 輪在 fuse 落盤後、evidence trace 追加前停止，只能由 `runtime_health_fuse_clearance` 收據與對應的 archived fuse 重建該輪。收據、archive 所在目錄、來源 identity、完整影像 SHA、舊／新 run 的相鄰輪次、request ID 與唯一 model-only 理由必須全部一致；`finalize_existing_three_pass_reviews.py` 才可把跨重啟的 `1/2/3` 輪組合起來離線定案。這是證據重建，不是新模型呼叫。

### `.47` → `.48` 逐張上傳佇列換版契約

- OCR 後端與逐張 uploader 必須使用同一 evidence revision；但安全換版時，舊 uploader 可能仍有已由 `.47` 正式驗證、尚未完成網路傳輸的持久化工作。禁止刪除、略過、直接改字串或讓新版 uploader 把它們當失敗件。
- `stream_drive_upload.py` 只允許明列的相鄰版本遷移，目前唯一允許的是 `.47` → `.48`。啟動新版單一 uploader 時，先把中斷於 `working` 的工作原子退回 `pending`，再逐張核對 schema、64 位 source identity、來源檔仍存在、完整來源 SHA-256 未變、年月一致，以及依 immutable final result 重新計算出的完整目標檔名完全相同。
- 遷移前的完整 JSON 必須存入 `_drive_upload_stream/revision_migrations`；新工作必須記錄舊／新 revision、原始工作 canonical SHA-256、來源 SHA-256、目標檔名、archive 路徑與時間。任何未明列版本、來源變更、身分不符、檔名重算不同或宣稱來自 `.48` 新規則的 `.47` 工作都要 fail closed。
- 遷移只處理尚未上傳的 durable outbox，不改寫既有 Drive receipt，也不降低 remote 守門。正式傳輸仍必須逐張檢查同名重複、唯一 size+MD5 readback 與 Drive ID；若舊 uploader 在網路傳輸途中被同步切換，新 uploader 會先恢復該工作，再以遠端精確查核決定是否需要 copy，不可因沒有 receipt 就盲目重傳。

### Drive 精確查核效能契約

- 禁止為了查核單一照片而每次完整列出年份資料夾；2026 逐張 uploader 曾因此每張耗用約一分鐘，pending 增長速度高於 OCR。也禁止改用 `lsjson <exact-path> --stat`，因 Google Drive 允許同名物件，該作法可能只選到其中一個而漏掉重複檔。
- 使用 rclone 官方 Google Drive `backend query`，先以正式根 ID `16X5qALC3zRYc7PpnexXLYprorBzBtT_f`、年份名稱、folder MIME type 與 `trashed=false` 唯一解析年份資料夾 ID；同一 worker 生命週期可快取該 immutable ID。缺少或重複年份資料夾必須 fail closed。
- 每張照片以 `'<year-folder-id>' in parents and name = '<exact escaped filename>' and trashed = false` 查詢；必須保留所有同名回傳，不可只取第一筆。查核結果仍要轉成並驗證 `Drive ID + size + MD5`，同名超過一筆仍停止該張上傳等待 ID 級處理。
- 正式 read-only 實測：同一 2026 檔案第一次含年份解析約 `1.406s`，快取年份 ID 後約 `0.671s`，兩次都回傳同一唯一 Drive ID；這是查詢縮小，不是降低 remote readback 標準。官方依據：[rclone Google Drive backend query](https://rclone.org/drive/#query)。
- 上傳鎖必須記錄 owner PID。若鎖存在且 PID 仍存活，絕對不得取代；若鎖內容損壞或沒有可驗證 PID，也必須 fail closed。只有 PID 已確定不存在時，才可先把原鎖原子改名封存為 `.stale.<pid>.<timestamp>`，再以 `O_EXCL` 重新取得；若中途被其他 worker 搶先取得就停止。這可讓照片邊界同步換版後自動恢復，但不允許兩個 uploader 同時運作。

## 2026-07-18 `.49` 新莊 1458 精確像素修正

- `M-新北市-新莊區-TK3C-新莊-1458.jpg` 經原圖人工複核，兩台上排完整螢幕加一台下排完整螢幕，共三台完整入鏡，應為「遠景／無型號／無價格」。
- 舊 `.47` 前兩輪把鄰近價牌誤綁到唯一主角，第三輪其實已正確判成遠景，最後卻被兩票單機多數覆蓋並以錯誤單機檔名上傳。
- `.49` 不以檔名套規則；只用原始來源 SHA-256 `2b8c6594...82a5` 與完整推論影像 SHA-256 `66901c0a...f116` 雙重綁定人工像素權威。任一雜湊不同就不得套用。
- 修正沿用既有三次模型呼叫，不得增加第四輪；定案後產生新版遠景檔名、逐張上傳，舊 Drive 物件必須等新版物件完成 `Drive ID + size + MD5` 唯一讀回後，才可依舊 receipt 的 Drive ID 移入垃圾桶。
- `.48 -> .49` 的待上傳工作允許相鄰版遷移，因 `.49` 只新增上述單一雜湊綁定來源，不改變其他 `.48` 定案。未知版本、檔名變更、來源雜湊不符或重複物件仍一律 fail closed。

### 低功耗視覺抽查不得單票改檔

- 子代理用來找出可能跑歪，不是最終像素權威；它的異常報告至少要再做一次嚴格原圖核對。
- 「完整螢幕」必須外框四邊四角都可見；被前景遮住底邊、被原圖裁切或只看到面板局部者不得計數。
- `汐止遠雄-262` 的第一次低功耗抽查誤報「至少八台完整」；第二次按四邊四角規則逐台核對後確認背景設備均被遮擋或裁切。此類互相矛盾的抽查不得直接改結果、不得觸發全批規則變更。
- 只有「原圖事實 + 三輪 trace + source identity + 完整影像雜湊」一致時，才能做照片級修正；否則維持現行結果並列入下一次抽查，不得因監控本身製造新錯誤。

## 2026-07-18 `.50` 複核提示中繼資料誤熔斷修正

- 正式事故 `M-新竹市-東　區-SF-新竹經國-639.jpg` 已有第 1、2 輪健康、同圖、request-bound、無前輪答案的證據；第 3 輪尚未呼叫模型，就因前輪價格 `4990` 恰巧出現在 RequestID、來源檔名或裁切座標等技術中繼資料而觸發 `review_prior_value_present`。
- 前輪答案污染檢查仍必須保留，但比對特定前輪型號／價格前，先排除固定格式的 `圖片:` 行、`RequestID:` 行與 `bbox=[...]`。提示正文其他位置若出現前輪型號、價格、理由、修正語句或 assistant 歷史，仍照常熔斷。
- 這是「模型尚未被呼叫」的假陽性，不能把第 3 輪永久吃掉，也不能允許第 4 輪。`recover_review_metadata_false_fuse.py` 只接受唯一理由 `review_prior_value_present`、attempt=3、空 raw model output、失敗空結果、同 run 的 trace 恰有第 1／2 輪、相同 source identity 與完整影像 SHA，且兩輪都健康、request-bound、無記憶。
- 通過上述證據後，只把該張持久化 attempt 從 3 回復到 2，保留前兩輪 history，封存原 fuse 並寫 clearance receipt；新版 `.50` 再執行真正的第 3 輪。任何已有模型輸出、不同理由、不同圖、缺 trace 或不健康輪次均不得使用此恢復。

## 2026-07-18 `.51` 缺漏回覆綁定只隔離單張、不得卡住整批

- 模型跳針、截斷或不完整 JSON 可能使回覆缺少 request echo／完整結構。這種回覆一律作廢，仍消耗一次實際模型呼叫，但它只證明該次輸出無效，不等於另一張照片的答案串入；`request_id_missing` 與 `request_binding_unverified` 即使出現在不同照片，也只能限制在各自照片最多三次呼叫內，不得建立全域 fuse。
- 只有模型明確帶回「非空、但不是本次」的 request ID，形成 `request_id_mismatch`，且同類錯誤再發生於另一來源，才是跨請求串線的系統性證據並保留全域 fail-closed。任何未綁定回覆都不得參與 view／model／price 投票或上傳。
- `tools/recover_contained_request_binding_fuse.py` 只接受第 1 或第 2 次的 missing／unverified fuse；它不回退已消耗次數，只把同一照片放回 durable retry queue 最前方，保留總上限三次、封存 fuse 並寫 recovery receipt。明確 mismatch、提示污染、前輪答案暴露、第三次失敗或狀態不一致全部拒絕。
- 此規則落實「單張異常不得阻塞整批」：該張用完三次仍無有效證據時留下保守終局技術結果並前進下一張，禁止第 4 次；介面、照片、判讀卡與逐張上傳仍須依同一 durable 狀態同步。
- 已確認錯名的 Drive 物件只能在新版逐張 receipt 已取得唯一 Drive ID、size、MD5 後汰換。`reconcile_drive_corrections.py` 對舊路徑送入垃圾桶後，rclone 可能以 `directory not found` 表示物件已不存在，readback 必須把這個特定狀態視為空集合，再以新物件 ID／size／MD5 驗證存活；`Hashes.md5` 與 `Hashes.MD5` 均須接受。其他 rclone 錯誤仍 fail closed，成功後清除帳本中的舊錯誤與 dry-run 指令。
## 2026-07-18 `.52` 照片級內容衝突、三輪終局與介面同步

- 正式級聯仍是「首輪健康即可結案，只有風險照片才升級，整張照片最多三次模型呼叫」。單機若型號、店內價格、價牌歸屬、唯一主角與自然敘述互相一致，第一輪即完成並立即排入逐張上傳；`↑／↓／✓` 由 deterministic price comparison 產生，不是加跑模型的理由。
- 同一張照片同時出現 `distant_followme_strong_evidence_conflict` 與 `structured_narration_followme_conflict`，屬於 photo-local 內容矛盾。第 1、2 次可繼續同圖 blind adjudication；第 3 次後必須以已取得的三輪證據保守定案並前進下一張，禁止建立全域 fuse、禁止第 4 次呼叫。
- 三輪定案器必須能同時接受上述兩個照片級衝突理由。若至少兩輪對同一實機的白色直立架、托盤或圓形底座形成 FollowMe 實體共識，但沒有兩輪一致支持確切變體與價格，終局應為 `單機／FollowMe（型號未細分）／無價格`；不得把不可靠的 M5、M7、Pro 或鄰近價牌冒充確定結果。
- 內容 fuse 已經消耗的模型呼叫不得遺失或重算。離線恢復只有在 recovery receipt、fuse archive、source_item_id、來源 SHA、實際送模 full-image SHA 與原 RequestID 全部一致時，才可把該次重建為已消耗輪次；恢復後剩餘呼叫數必須等於 `3 - 已消耗次數`。任何欄位缺失或多義都 fail closed，不得藉恢復產生第 4 次呼叫。
- 模型自然敘述明確指出主體為 Smart Monitor M8，但結構欄卻給出 Odyssey／G5／G7 等不相容 SKU 時，守門必須清除不可信型號並進入下一輪；不得從前輪答案或附近價牌補寫。這是污染防護，不是把自然敘述直接改寫成答案。
- 人工像素權威若更正型號或店內價格，必須同步清除並重新計算 `official_price`、`price_diff_percent`、`price_status` 與 `price_symbol`。舊型號留下的比價資料不得附著到新版結果；新版逐張上傳完成唯一 `Drive ID + size + MD5` 回讀後，才可依舊 receipt 的 Drive ID 汰換錯名物件。
- OCR 後端與 uploader 必須載入同一 evidence revision。換版只能在照片邊界完成；相鄰 revision 的 durable outbox 只可經明列遷移規則核對後接續。worker 健康不能只看狀態檔，還必須確認唯一 PID 存活；若 worker 已退出而 pending 增加，只能以 hidden window 啟動一個 replacement，保留 stdout／stderr 日誌，不得彈出終端機。
- Dashboard 的卡片文字必須反映實際輪次：第 1、2 輪有疑點時顯示已排入下一輪，不得預先寫成「第三輪已完成」；只有三次模型呼叫已用完時才能顯示第三輪終局。前端 transport 不得把未完成輪次冒充終局，也不得把終局冒充待人工裁決。
- 每次介面健康核對都必須在既有分頁同時驗證：全案總進度、目前資料夾進度、當前照片與輪次、LLM 自然語言逐字區、右側累積卡片、逐張上傳總數／pending，以及後端 `is_running` 與 runtime fuse。不得只看進度數字；不得重啟瀏覽器、不得新增分頁或視窗。
- `.52` 事故案例 `中壢易飛本店-753`：前兩次照片級內容衝突被安全保留，第三次後以 FollowMe 實體共識降階結案，沒有第 4 次呼叫；Drive receipt 已取得唯一 ID `1xokZj1pKeJf5QQO6_Bp3PpI3kJASsC86`。`中壢環球-429` 由完整影像像素權威更正為 `S32DM803UC／14,900`，比價重新計算為官方 `10,900／↑36.7%`，新版 Drive 物件 ID `1AzfDvbwGQfqyE-v9QozkSbGw-vc_qJS9`；舊錯名物件只在新版讀回後才移入垃圾桶。
- `良興桃園-765` 三次呼叫依序出現寬景單機、單機與遠景衝突；第三輪已正確描述至少三台完整螢幕、非唯一主角。低功耗原圖抽查確認為 `遠景／無型號／無價格`，以 source item、來源 SHA 與 full-image inference SHA 三重綁定的像素權威離線結案，呼叫數仍為 3。修復只能在照片邊界短暫停止寫入同一場次檔，port 5002 Dashboard 保持在線；結案排入逐張上傳後以 `restart=false` 接續。Drive receipt 已取得唯一 ID `1OqqaA6YSaNQ0zUQacTjIKey2acRg9z4J`、size `660675`、MD5 `7c53a3d1ea5b8b1d0e42d60109b894ce`。

### `.52` FollowMe 存在證據不得覆蓋整張照片的寬景幾何

- FollowMe 白色直立架、圓形底座、托盤或同主體產品卡只能證明「畫面內有一台 FollowMe」，不能證明整張照片只有一個主角。原圖若仍有至少三台螢幕四邊四角完整入鏡，整張照片必須定案為 `遠景／無型號／無價格`；其中一台可辨識為 FollowMe 也不得把整張照片改成單機。
- `two_pass_followme_physical_consensus` 只能使用同輪同圖、`view_type=單機`、`unique_main=true` 且沒有敘述額外完整螢幕的票。至少兩輪不得被寬景敘述反證，且其中至少一輪必須正面證明 `complete_screen_count` 為 1–2，或自然敘述明確指出其他鄰機皆不完整／被原圖裁切。兩輪都回報 3+ 台完整螢幕時，即使型號與價格一致也不能進入 FollowMe 單機共識。
- `M-新竹縣-竹北市-SF-竹北-708.jpg` 的三輪為 `遠景/count=5`、`單機 FollowMe/count=5`、`單機 FollowMe/count=3`；低功耗原圖稽核確認至少五台完整螢幕。修正後由 `distant_structural_veto_over_wide_geometry_single_votes` 定案遠景並逐張上傳，Drive ID `1kmfJeRMYladBeW7hEi1n23eAK0PfMszt`。
- `M-台北市-北投區-TK3C-新北投-1414.jpg` 原圖至少五台完整螢幕，右側 `Samsung FollowMe` 只是現場宣傳／展示證據，不能歸屬任何特定橫向螢幕的型號或價牌。修正後定案遠景並逐張上傳，Drive ID `1Fhln7SWm8yEB9BMATdX1xLCP-E0iOlyE`。
- `中壢易飛本店-753` 的真正 FollowMe 單機回歸必須同時保留：兩輪同主體物理證據中至少一輪為單一完整主體，且沒有額外完整螢幕敘述，仍在第三次呼叫後降階結案，不得因寬景修正退回待處理或產生第 4 次。
- `finalize_existing_three_pass_reviews.py --output-dir` 必須傳正式輸出根目錄 `D:\00_商化\00_已OCR照片`，不可傳目前 staging leaf；否則會在 staging 下建立正式 worker 看不到的孤立 outbox。若操作失誤，先把相同 source identity 重新排入正式 outbox、取得唯一 Drive receipt，再把誤放 outbox 完整封存至 `_ocr_audit\misrouted_upload_queue_archive`，不得直接當成已上傳。
- 本補強使用既有 `.52` 三輪證據，不把整個 2026 已驗證集合強制升版重跑；程式碼版本以 Git commit 追溯。正在執行的舊進程不會動態載入 Python 函式，必須等完整安全邊界、pending/working=0、關鍵回歸通過後以 hidden backend replacement 載入；等待期間由終局 review 清冊與離線定案器攔截同型風險，不得中途重啟正式批次。

## 2026-07-18 202606 全批終局、缺輸出硬上限與安全接續

- `202606` 的正式終局為 `1393/1393`：成功、verified 均為 1393，`review_required=0`、`failed=0`、runtime fuse inactive。資料夾完成不代表可略過逐張上傳；每張都必須取得與 source identity、來源 SHA、published SHA 一致的唯一 Drive receipt。
- `Game休閒館統領-408` 與 `TK3C 龜山-1357` 已消耗三個模型呼叫名額，但 durable trace 只留下兩份乾淨輸出。此類「第三次呼叫已消耗、輸出跨程序邊界遺失」不得呼叫第 4 次；只有來源 item、來源 SHA、實際送模 input SHA、三次 consumed slots、兩份乾淨輸出及完整影像像素權威全部精確綁定時，才可用 `recover_consumed_cap_missing_result.py` 如實記錄「3 次已消耗／2 份可用輸出／第 3 份缺失」並保守結案。這不是一般兩輪即可定案的捷徑，technical integrity、memory contamination 或 identity 不明一律不得使用。
- 408 與 1357 均由完整原圖權威確認為 `遠景／無型號／無價格`，且已取得正式 Drive receipts：408 `1bEpcrOIJ01fwmosrdySJK8jcNV6U-qkH`、1357 `1OKoXiNyQZUJCAejRP2ni9mJnMZH7xUgy`。
- 剩餘複核採「清冊與 API 集合完全相等」原則：`data/202606_review_visual_decisions.json` 必須與當下 review-required 集合逐檔完全一致，不可漏張、增張或重複。`build_visual_authority_manifest.py` 只能用檔名定位 source map，權威本身必須綁定 source item、來源 SHA、trace input SHA、三輪獨立性及無前輪污染；檔名文字本身不是辨識證據。
- `finalize_existing_three_pass_reviews.py --authority-manifest` 只接受上述精確綁定的離線 manifest，且必須先把逐張上傳工作寫入 durable outbox，再原子更新結果檔。68 張既有複核均以已消耗的最多三輪證據結案，沒有新增模型呼叫；套用後 `202606 review_required 68→0`。
- 同資料夾的結果被離線更新後，接續監控必須先在 idle 邊界以 `/api/set_work_dir`、`restart=false` 刷新後端磁碟狀態，再重新讀取 status；不得因記憶體內舊快照誤判未完成或重跑。完成驗證應從 `.ocr_source_map.json` 綁定 source identity，從 `v1945_evidence_trace.jsonl` 綁定 input SHA／run，再核對 published source 與 Drive receipt；`/api/success_records` 是介面顯示資料，省略 identity 欄位，不能單獨作為上傳完成證據。
- 期間優先批完成後，既有 Dashboard、port 5002 backend、LM Studio 與 uploader 均保持原進程。hidden continuation monitor 只等待 `pending=working=0`，逐張核對全部 1393 份 receipts 後，才以 `restart=false` 將同一 backend 切回既定 202601 staging 並接續 202601→202605；不得新開瀏覽器分頁、不得啟動第二套 backend／uploader／monitor，也不得在上傳尚未排空時宣稱整批閉環。
## 2026-07-18 frozen guard revalidation and all-years continuation

- 2026 is a priority correctness phase, not the project endpoint. The active
  goal remains every supported source photo across 2015–2026 plus every exact
  Drive receipt. After the fixed `202601 -> 202605` review chain, continuity
  proceeds through 2025, the all-year questionable audit for already
  initially processed folders, and then the remaining historical folders down
  to 2015. Never mark the project complete merely because 2026 closes.
- A result produced by an older evidence revision must never be made current
  by changing its revision string. Use
  `tools/revalidate_frozen_guard_results.py`: it proves the inactive staging
  leaf, source-map identity, original source SHA-256, exact prepared-image
  SHA-256, one run, contiguous attempts within the 1..3 hard limit, request
  binding, independent passes, and absence of prior-answer exposure or prompt
  contamination. It reparses each stored raw JSON object and replays current
  normalization, model validation, runtime-health and deterministic
  adjudication rules. It performs zero model calls.
- Revalidation is allowed to change the old conclusion. This is the point of
  re-adjudicating instead of transport-migrating. A missing current consensus
  remains on the old revision and keeps its unused call budget; it must not be
  restamped, guessed, or silently uploaded as current.
- Multi-photo apply must preflight every current upload plan. Upload enqueue is
  idempotent across retries: `queued_at` and superseded-receipt audit metadata
  are not part of upload intent, while source identity, hashes, target name,
  plan and final result remain exact. Queue first and expose the rewritten task
  only after all accepted rows have durable jobs.
- Formal 202606 application revalidated 16 old `.41` rows under `.52` without
  a model call. The manifest is
  `_ocr_audit/frozen_guard_revalidation/20260718_160411/manifest.json`.
  `M-南投縣-南投市-SF-南投-533.jpg` remains `.41` with two calls because current
  rules reject its incomplete model evidence; it retains exactly one legal
  independent call for a later safe photo boundary.

## 2026-07-18 離線終局結果必須同步所有介面讀取面

- Label Studio 成功檔同時保存 `data.ocr_meta` 與
  `annotations[0].result`。離線三輪定案不得只更新前者；Dashboard
  `success_records` 仍會從第一個 annotation 讀取視角、型號與價格，只改
  `ocr_meta` 會讓正式結果與介面顯示互相矛盾。
- `finalize_existing_three_pass_reviews.py` 現在於同一次原子寫入同步
  `view_type/category`、`model`、`price` 兩套表示。無型號或無價格在
  `ocr_meta` 保持 JSON null，在 Label Studio textarea 使用字串 `null`，
  與既有匯出契約一致。
- 三輪 pass history 是不可改寫的稽核證據；離線定案不得覆寫原三輪，也
  不得虛增第 4 輪。工具會另寫一筆 deterministic「第三輪終局定案」
  presentation event，沿用該來源最後一次真實模型呼叫的 cumulative
  sequence，`decision=accepted`，以同一 `source_item_id` 讓歷程 API 與
  Dashboard 顯示最新終局。
- 終局 event 寫入獨立
  `presentation_finalization_YYYYMMDD.jsonl`，避免與仍在運行的 backend
  競爭寫入正常 presentation stream；相同終局內容的 ID 可重入，不重複
  寫入。
- 事故樣本 `M-台北市-士林區-集雅社-大葉高島屋-182.jpg`：正式
  `ocr_meta` 與逐張上傳工作原本已正確為
  `遠景／無型號／無價格`，舊 annotation 卻殘留
  `FollowMe M7 32"／12,990`。修復後 success API 與終局歷程均回傳遠景；
  Drive 佇列沒有上傳過舊單機結果。

## 2026-07-18 17:30 舊場次像素終局與不中斷介面驗收

- `data/202601_terminal_visual_decisions.json` 是一次性、逐來源檔案的完整原圖決策清冊；正式 manifest 必須再由 `build_visual_authority_manifest.py` 綁定 source item、原始來源 SHA、實際送模 input SHA、乾淨獨立輪次與三次硬上限。決策清冊本身不是可上傳權威。
- 九張已耗盡三輪的 202601 技術列通過上述 manifest 與 finalizer dry-run 後離線定案：七張遠景、兩張單機缺型號／價格；全部寫成 `auto_verified=true`、`auto_review_required=false`，同步 `ocr_meta`、annotation、終局 presentation event 及正式逐張 upload outbox，沒有第 4 次模型呼叫。202601 即時 review-required 因而由 42 降至 33。
- `大葉高島屋-179` 的像素觀察為單機、價格 10,990，但有效 attempt 1 與 attempt 3 分屬不同 `run_id`。不得為了清零 review 放寬 builder 或假造同一 run；該筆保留於清冊的 `deferred_decisions`，必須走能證明持久化 consumed slots 的專用修復，仍禁止第 4 次模型呼叫。
- 介面驗收只接管既有 Dashboard 分頁，沒有新增或重載分頁。15 秒內畫面由 202601 `693→695/1,500`、目前檔案 `1354→1356`、上傳總數 `54,782→54,783`；照片／檔名、輪次、LLM 自然語言、右側累積卡片與上傳數同步，狀態保持「正在執行」。
- `大葉高島屋-182` 目前仍只有正式 pending job，尚未取得 Drive ID／size／MD5 精確收據；不得把 queued 當成 uploaded。逐張 uploader 持續運作，取得唯一收據後再閉環。
- 2026 完成仍只代表當年度階段完成。continuity supervisor 必須接續 2025→2015，直到 `151,714` 張、`137` 個資料匣全部取得如實終局與 Drive 精確收據。

## 2026-07-18 17:46 priority staging 與全案總盤的雙層進度

- `folder_discovery.csv` 已含 202606，但 priority staging 完成後若沒有對應 `folder_summary.csv` row，Dashboard 會把真正已處理的 1,393 張漏掉，並錯列為下一個 pending 資料匣。這是總盤持久化缺口，不代表 OCR 沒跑。
- `record_period_priority_progress.py` 只在 `.period_priority_manifest.json`、source map、來源資料匣、staging 圖片及每張 durable result task 完全集合相等時，才寫入 `processed`。它固定使用 `status=period_priority_processed_unexported`、`ready=0`、`copied_count=0`、`success_records=0`，不產生 copied／upload／Drive receipt 宣稱。
- 202606 的真實雙層狀態為：`processed=1,393`；其中目前 `.52` 完整終局 379，舊守門或未終局 1,014。後者仍必須依照片邊界複核，不得把 processed 當成 current-guard ready。
- 寫入後 `/api/status` 與既有 Dashboard 分頁同步顯示 `66,724/151,714`、`45/137`、剩餘 `84,990`；`ready_images` 仍為 `65,331`。202601 同時前進到 715/1,500、stream upload 54,832、pending 133，backend／browser 均未重啟。
- `continue_after_period_priority.py` 在 priority 本地批次處理完成後、等待 upload drain 之前，會自動呼叫同一精確守門寫入 processed 總盤；這只修正 OCR 進度，不放寬正式 export、upload 或跨年度接續條件。

## 2026-07-18 18:13 frozen-guard partial safe revalidation

- One malformed frozen row must not block every independently safe row, and this must never weaken source binding. `revalidate_frozen_guard_results.py --allow-partial` runs the full binding preflight per photo. Only rows with exact source/prepared hashes, one run, contiguous independent attempts within the 1..3 hard limit, request binding, and no prior-answer exposure or prompt contamination may replay current rules. Rejected rows keep their original file, revision, and remaining call budget.
- Partial mode preserves the original apply boundary: every accepted row gets an idempotent per-photo upload job before its durable result is rewritten. Any enqueue failure prevents the new revision from becoming visible. Frozen replay performs zero model calls and is never a fourth pass.
- Formal 202606 dry-run covered `.51/.50/.48/.47/.45/.43/.42/.41`: 971 rows passed current `.52`; about 40 rows remained old because binding, FollowMe Pro physical identity, model validity, three-pass integrity, or content-consistency proof did not pass. Critical regressions passed before per-revision apply. Evidence manifests are under `_ocr_audit/frozen_guard_revalidation/20260718_180507` through `20260718_181216`.
- Rebuilt priority proof now states `processed=1,393`, `current_guard_final=1,350`, `nonfinal=43`, and `stale_guard=40`. Those 43 photos continue through the normal photo-bounded maximum-three-pass lane; they must not be restamped or represented as complete.
- During apply, formal 202601 review continued from 728 to 760/1,500, the stream uploader stayed running and advanced from 54,870 to 54,941, runtime fuse remained absent, and neither backend nor the existing Dashboard tab was restarted.
- This is still only the 2026 priority phase. Completion remains all supported 2015-2026 photos: 151,714 images, 137 folders, and exact Drive receipts; continuity must continue 2025 down to 2015 after the fixed 2026 chain closes.

## 2026-07-20 `.53`：2026 全面複核、型號／價格證據與逐張上傳鐵律

- 專案優先順序固定為：**照片辨識正確 > 對話 tokens 耗用 > 完成時間**。但「正確性優先」不等於無限重跑；每張照片仍最多三次彼此無記憶、無前輪答案的獨立模型呼叫，三次後必須用已取得證據保守定案並繼續下一張，禁止第 4 次。
- **先完整閉環 2026，再處理 2025→2015。** 2026 閉環必須同時證明：來源清冊逐張有如實終局、使用當前 evidence revision、沒有 review／failed／stale 結果，以及每張皆有唯一 Drive ID、size、MD5 精確收據。舊統計 CSV 或「processed」不得冒充上述完成證明。
- 2026 尚未閉環期間，任何誤啟的歷史年份 stream job 必須在 uploader 停止的安全邊界，以原始 JSON、SHA-256 與 manifest 完整移到 `_ocr_audit/deferred_historical_stream_jobs`；不得刪除、改寫、上傳或讓歷史照片插隊。2026 閉環後才依來源雜湊恢復。
- **每張完成就上傳。** OCR 定案器必須先把該張 verified 結果寫入 durable stream outbox，uploader 隨即逐張傳輸與精確讀回；不得等資料夾、月份或全年完成才開始上傳。網路不穩只允許該工作可重入重試，不得阻塞 OCR 下一張，也不得累積成最後一次性大量傳輸。
- 舊結果全面風險掃描發現兩類候選：模型自然敘述可讀到型號但結構型號被清空，以及模型選到「市價／原價／建議售價／參考價」。這些是需逐張重驗的風險集合，不可直接視為全部錯誤，也不可批次猜值覆寫。
- 型號表／官方目錄沒有舊 SKU 時，不得因此把原圖價牌上的型號直接清成空值。只有當同一張原圖的主價牌可歸屬、敘述與結構各自提供唯一型號、沒有第二個候選，且差異只限三星舊曲面螢幕常見的首字母 `C`／`S` 表記時，才可保留該「未列入目錄但原圖可讀」候選並標記 `official_model_unverified`；任何多候選、遠景、價牌不屬於主機或更大差異都要 fail closed。
- 同一價牌的小字 `市價／原價／建議售價／參考價` 不是店內實售價格；應優先讀取同牌的大字促銷價、會員價、現金價或特價。若只看得到參考價，`price` 必須為空並進入尚未使用的下一個獨立輪次，不得把參考價寫入檔名。三輪已滿則以無價格如實終局。
- `.53` 換版不得把 `.52` 的字串直接改成新版。舊三輪只有在 source identity、來源 SHA、input SHA、request binding、連續輪次、無前輪暴露與 current-rule replay 全部通過時，才可零模型呼叫重驗；未通過者保留原 revision 與合法剩餘呼叫額度。
- 監督程序只允許一套隱藏進程。它以 `lms ps` 核對實際載入模型，正式配置為 `qwen/qwen3-vl-8b`、context `32768`、parallel `1`；後端固定 port `5002`。啟動後必須等待 API 健康才可接續工作。即使 OCR 正常，若 durable stream outbox 有 pending 且沒有 uploader，仍必須先補起唯一隱藏 uploader，不能被 `healthy_noop` 略過。
- Dashboard 只能沿用既有分頁；禁止另開視窗／分頁。每次健康核對要同時驗證照片、檔名、輪次、LLM 自然語言逐字區、右側累積卡片、全案與當期進度、逐張上傳 pending／receipt、backend `is_running` 與 runtime fuse。監控不只看「數字有動」，還要抽查原圖、自然敘述、結構結果與最終檔名是否一致，發現系統性跑歪時只在照片邊界 fail safe。

### 2026-07-20 中斷復原基準

- 中斷盤點時 LM Studio port `1234` 尚存，但 backend port `5002`、stream uploader 與 supervisor 已停止。舊交接 CSV 顯示 202602–202605 尚有 `3,117` 張上傳差額；202601 與 202606 同時存在重複列，因此 `3,117` 只能作為待查下限，不能作為精確缺口。
- `.53` 啟動前已把誤啟的 32 筆 202101 stream jobs完整延後至 `_ocr_audit/deferred_historical_stream_jobs/20260720_195303`，每筆均保留來源身分與 SHA-256；pending／working 歸零，沒有刪除照片。
- 舊 2026 trace 的廣義風險掃描找出 622 個來源曾出現「raw model 非空但 parsed model 為空」，以及 303 個來源曾選到明示參考價。集合包含重複場次、遠景、FollowMe 與後來已修正結果，必須以 `.53` 精確綁定重驗，不得把這兩個數字當成確定錯誤量。
- `M-台中市-北屯區-SF-北屯-456.jpg` 是歷史年份的已知例子：原圖價牌可讀 `C27F390FHE`，大字促銷價 `5,990`，小字市價 `7,990`。它目前不得插隊 2026，也不得做第 4 次呼叫；等 2026 閉環後，以原圖雜湊綁定的既有三輪證據依 `.53` 離線重驗。
- Windows venv 的 launcher 與實際 interpreter 會同時出現在程序清單且攜帶相同命令列；這是一個邏輯 worker，不是兩套 OCR／uploader。監督程序必須折疊同一 parent-child 鏈後才判斷 singleton；兩個彼此獨立的根程序仍要視為重複並 fail closed。
- `lms` 在 Windows PowerShell 被擷取時仍可能輸出 ANSI 顏色碼；監督程序必須先去除 ANSI，且包裝函式參數不得命名為 PowerShell 自動變數 `$Args`，否則實際子命令會遺失，使已載入模型被誤報為不存在。
- staging 路徑同時含執行時間 `20260720_200139` 與來源月份 `202601_商化照片-202601_...`。月份解析只接受獨立且月份為 01–12 的 `YYYYMM`，不得把時間片段 `200139` 顯示成目前資料匣。此修正只在安全後端邊界載入；禁止為了純顯示文字中斷正在推論的照片。
- 20:02–20:09 復原實測：202601 `.53` 從 0 前進至 13/1,478，verified 13、review 0、failed 0；12 份新結果已逐張取得 Drive ID、size、MD5 receipt，沒有等整批完成。三張原圖抽查為：草屯 670 遠景、671 單機 `S27CG552EC／4,990`、672 單機 `S32CG552EC／6,990`，原圖、自然敘述、結構、終局檔名與 receipts 一致。
- 20:19–20:22 低功耗抽查新文心 976、977、978：原圖、完整螢幕數、FollowMe 實體結構、同主體價牌、`.53` 結構結果與新版檔名均一致；三張新版皆取得 Drive receipt。抽查同時發現根目錄與舊上傳帳本仍可能保留同一來源的歷史錯名。**新版 receipt 不等於舊錯名已汰換**：2026 完成 marker 之前必須由更正帳本逐筆完成舊 Drive ID 唯一發現、新檔 ID／size／MD5 讀回、舊檔移入垃圾桶、舊路徑不存在與新檔仍存活的二次讀回。多個不同舊錯名指向同一來源且都映射至同一新版輸出，是必須全部處理的合法多筆更正，不得誤判成 source identity 衝突；只有新版目標不一致、舊路徑重複或舊 ID 重複才 fail closed。歷史階段不得覆寫已完成的 2026 更正帳本。
- 長批次執行中若 backend runtime 檔案比現行 backend 程序新（例如 staging 月份顯示修正），不得中途重啟。supervisor 只在 backend `is_running=false`、`processed=total`、沒有 watcher／staged／recursive runner、沒有 fuse／benchmark lock 的完全閒置邊界，交給唯一隱藏 helper 換版；helper 只停止 port 5002 的 repo-owned backend parent/child tree，不開瀏覽器，並在新版 API、版本、compact status contract 與 evidence revision 全部讀回後才算成功。

## 2026-07-20 `.54`：未收錄型號候選不得在後處理遺失

- `.53` 即時監控發現 `台中五權-1143` 與 `公益-1261` 三輪原始 JSON／自然敘述皆讀到同一主角價牌 `S24D362GAC`，且 pipeline 已標記 `unlisted_model_candidate=true`，但後續欄位處理把 `model` 清成空值，三輪定案器因而只看見三個空值並錯誤 verified。這是系統性守門漏洞，不是模型沒讀到。
- `unlisted_model_candidate` 是 pipeline-owned 標記；若後續層把其型號清空，必須從同輪 `raw_objects` 恢復，而且只接受一個唯一 Samsung 型號、單機、`unique_main=true`、`label_ownership=matched`、自然敘述也明確綁定同主體實體價牌，且沒有型號驗證、結構身分、產品家族或品牌衝突。遠景、多候選、模糊／推測敘述一律不得恢復。
- 此防線必須同時存在於單輪結果離開後端前及批次定案正規化後；三個獨立輪次因此能用真實型號形成共識。已錯誤清空的 2026 結果不得改字串冒充新版，必須依 source identity、原圖 SHA、input SHA、三輪 request binding 與原始 JSON 零模型呼叫重驗；不能證明者才重新呼叫，總呼叫數仍不得超過三次。
- `revalidate_frozen_guard_results.py` 的零模型呼叫重驗也必須先依同一照片價牌規則重建 `unlisted_model_candidate`；禁止把舊 trace 的原始型號先依現行型號表清空，再錯誤宣稱「三輪沒有型號共識」。
- 型號抽取不得先把整段自然敘述壓成純英數再當成一個型號，否則「`S24D362GAC`，售價 `3,490`」會被偽造為第二個候選 `S24D362GAC3490`，使真實價牌型號因「多候選」被清空。整段直讀分支只接受整個欄位本身就是一個 Samsung code；一般敘述一律使用有邊界的型號 token。
- 內容監控在照片邊界主動停止尚未完成的批次後，若需要載入修正版後端，只能由 `reload_backend_at_safe_idle.ps1 -AllowIncompleteStoppedBatch` 明確執行。此模式不代表一般 idle：仍必須證明 backend `is_running=false`、沒有任何 owned OCR runner、沒有 fuse／benchmark lock、只有一個 repo-owned port 5002 listener；換版後也不得自行開始批次或開啟瀏覽器。未指定此 switch 時，`processed=total` 的原安全條件維持不變。
- 上傳 worker 與後端必須載入相同的 `EVIDENCE_GUARD_REVISION`。若後端已安全升版、舊 worker 卻把同版工作退成 `stale or invalid stream upload job: ...`，OCR 不得因此停止；先只熱換隱藏的上傳 worker，再以 `tools/recover_failed_fuse_uploads.py --active-priority` 將「目前年份、目前 revision、原圖雜湊、同 run 潔淨 trace、已終局成功記錄」全部一致的工作封存原失敗 JSON 後原子補回 pending。這個模式不呼叫模型、不改判讀內容，也不得接受其他錯誤原因或舊 revision。

## 2026-07-20 `.54` 三輪內容收斂與不中斷顯示相容

- Dashboard 上方 `初次辨識總進度` 是唯一來源照片的去重完成數。對同一張 2026 照片做第二、第三輪或離線重驗時，該數字不得重複增加；正在執行的真實進展必須另外以 `YYYYMM 複核 processed/total`、`verified/review/failed` 與 `上傳總數/待上傳` 同時顯示。正式總數只在首次完成一張原先未處理的來源照片時增加。
- 同一張照片三次健康、獨立、request-bound 且 input SHA 相同後即結束模型呼叫。中央主螢幕加左右邊界裁切鄰機，若至少兩輪同時支持相同非 FollowMe 型號、相同價格、同主體價牌與邊界裁切幾何，定案規則為 `two_pass_edge_cut_identity_consensus`。模型不得把被邊界裁切的鄰機誤算成三台完整螢幕並抹除中央主體。
- 兩輪無型號／無價格的 3+ 螢幕寬景可否決一個孤立的附近價牌身分票，規則為 `two_wide_geometry_votes_veto_single_identity_outlier`；三輪均證明零台完整螢幕時以 `three_pass_zero_screen_scene_consensus` 定案為遠景；一輪零台與一輪 3+ 台若都一致證明無唯一主角、無同主體價牌及無 FollowMe 實體，使用 `two_pass_distant_scene_consensus`。這些規則只處理同圖內容差異，不得放寬 request binding、來源雜湊或污染守門。
- 已接受且 `auto_verified=true`、`auto_review_required=false` 的照片，`review_status` 必須清為 `已完成`，不得留下先前輪次的 `待審核`。舊 live backend 若仍在記憶體保留修復前的技術錯誤，`get_all_records()` 只能在 durable 列具有明確三輪定案規則、已驗證且記憶列仍未解決時，讓 durable 終局結果優先；一般新記憶結果仍優先，避免舊檔覆蓋真正的新結果。
- `tools/maintain_active_three_pass_repairs.py` 是單一 evidence revision 的零模型呼叫相容橋。它只對已用滿三輪、同圖綁定且能被現行 deterministic finalizer 證明的列套用修復，先保留可稽核快照，再以 optimistic compare-before-replace 合併；舊後端再次寫檔時可冪等重套。它不得停止 OCR、不得重啟 port 5002、不得開啟或重新整理瀏覽器、不得產生第四輪，後端 revision 改變時必須退出。
- 現行介面驗收必須在同一既有分頁看到：`正在執行`、目前檔案、同檔預覽、自然語言 LLM 即時文字、右側累積卡片、移動中的月份複核數，以及持續增加的逐張上傳數。總進度暫時不動而月份複核與上傳持續前進是正常去重語意；月份複核、目前檔案與上傳也一起不動才屬卡住。

## 2026-07-20 `.55`：型號表路徑、規格誤價、停止邊界與 202601 恢復

- 所有型號表、prompt、assets 與價格快取都必須以 repository root 組成絕對路徑，不得依賴程序目前工作目錄。正式程序啟動後要核對型號表載入筆數大於零；即使目錄暫時無法載入，也只能標記 `model_catalog_unavailable` 並阻止自動驗證，不得把同輪原圖已讀到的型號清空後冒充「無型號」。
- 價格欄位必須拒絕明顯來自顯示規格的數字。事故樣本 `台中廣三SMS-351` 把敘述中的 `35,424 2160`（解析度文字）誤作售價 35,424；`.55` 在正規化與 frozen replay 兩條路都會清空這種 spec-like price、標記價格衝突並使用尚未消耗的獨立輪次。三輪後仍無實體價牌就以無價格如實結案。
- `Samsung Follow Me 4K` 只能證明 FollowMe 家族，不能單獨證明 `M5 32`、`M7 32`、`Pro M7 43` 或尺寸。若自然敘述只支持泛稱，而輸出猜了具體款式，必須回報 `followme_specific_identity_evidence_missing`；只有同輪可見文字、實體價牌或 SKU 明確支持相同款式時才可保留具體型號。
- `/api/stop` 必須在照片邊界寫入持久化 `pipeline_pause.json`；continuity supervisor 看見此標記只能健康 no-op，不得自行續跑或換月。只有經確認的 `/api/start_batch` 成功後才清除標記。`wait_for_folder_done` 在 `is_running=false` 且 `processed < total` 時必須報告未完成並保留 staging，絕對不得當成資料匣完成、刪除 staging 或前進下一月份。
- 網路、rclone、Drive readback 或 runtime fuse 等可重試傳輸錯誤不得永久移入 failed。stream uploader 以延遲退避把該工作原子放回 pending，未到重試時間的工作不得擋住後續照片；OCR 仍按每張終局即 enqueue 的鐵律前進。只有內容契約、source identity、雜湊或 revision 不一致等不可恢復錯誤才進永久 failed。
- `maintain_active_three_pass_repairs.py` 必須記錄未解集合 fingerprint；相同 unresolved 集合沒有新終局時不得每輪重寫近 500 MB 快照。零筆定案的交易快照要自動刪除，仍不得改動任何來源照片。
- 本次停止後舊 continuation 曾錯誤把未完成 202601 當完成並切到 202602／202603。恢復時不得沿用誤切月份：以 frozen candidate CSV 重建精確 202601 staging 共 1,478 張與 `.ocr_source_map.json`，再恢復最後完整 206-row 快照。`.54` 原始三輪經 `.55` 綁定 dry-run 後，169 筆安全結果零模型呼叫升版並先 enqueue，37 筆不安全結果從舊 task 檔移除，交回正常最多三輪流程；`台中廣三SMS-349` 與 `351` 都屬後者，沒有冒充安全結果。
- `台中廣三SMS-349` 在 `.55` 三輪的原始結構輸出都讀到 `LS32DG702EC／19,900`，但自然敘述未在每輪重複完整 SKU，嚴格 authority conflict 因而把型號清空；完整原圖實際在主角上方立牌顯示 `S32DG702EC (32")`，右下同主體實體價牌顯示 `LS32DG702ECXZW／19,900 元`，且只有中央螢幕四邊四角完整。此類確定像素事實只能用 `samsung-ocr-bound-visual-authorities/v1` 綁定原圖 SHA、模型輸入 SHA、source item ID 與同一個三輪獨立 run 後離線定案，禁止第 4 次呼叫。正確 `.55` 新檔必須先取得 Drive ID、大小與 MD5 精確讀回，之後才能依舊收據的唯一 Drive ID 將「型號未辨識」舊檔移入垃圾桶，最後再讀回確認新檔仍存在。
- live backend 可能在下一張完成時，以記憶中的舊 unresolved 列覆寫剛完成的離線定案。對這種已由雜湊綁定權威證明且已上傳的列，必須讓 `maintain_active_three_pass_repairs.py` 隱藏橋接器在相同 evidence revision 內冪等重套 durable task；`get_all_records()` 依已存在的 durable-over-stale-memory 規則讓介面計數同步。橋接器不得重排模型、不得重複 enqueue、不得新開終端機或瀏覽器；低功耗視覺代理的幾何結論仍須由原始解析度證據覆核，不可因代理報告而放寬像素守門。
- frozen partial apply 的 `--drop-rejected-for-rerun` 只允許搭配 `--apply --allow-partial`。所有 safe rows 必須先成功建立新版逐張 upload job，才可原子更新 task；被拒列只從舊完成 task 移除，不刪圖片、不刪 trace、不增加第 4 輪，並保留正常 pipeline 可用的合法呼叫額度。
- 換版順序固定為：先在舊 uploader 上讓唯一可證明的舊 revision 網路失敗工作取得精確 receipt，再於 idle 邊界只熱換隱藏 uploader；接著以 `reload_backend_at_safe_idle.ps1 -AllowIncompleteStoppedBatch` 只換 port 5002，禁止開／重整瀏覽器；核對 `.55`、compact-v2、132 筆型號、202601 staging 與 `pipeline_pause` 後，才用 `confirmed=true`、`restart=false` 續跑。既有 Dashboard 分頁驗收由 169/1,478 前進至 186/1,478，上傳總數由 55,792 前進至 55,803，LLM 逐字區與右側縮圖同步，證明不是只看 API 宣稱正常。

## 2026-07-21 `.56`：全圖完整螢幕優先、遠景內 FollowMe 與去重總進度

- Dashboard 上方 `初次辨識總進度 65,336/151,714` 是唯一來源照片的去重初辨識數，不是模型呼叫數，也不是 2026 複核輪數。同一張照片第二、第三輪、離線三輪定案或雜湊綁定修正都不得重複增加；複核期間的真實前進由同一列的 `202601 複核 processed/total`、本張輪次、verified/review/failed 與 `上傳總數／待上傳` 顯示。只有進入尚未初辨識的年份後，65,336 才會再增加。
- 視角分類的權威是第一張全尺寸原圖，不是前景產品身分。完整螢幕必須四邊四角都在原圖內；左右鄰機碰到原圖邊界不計。全圖有 3 台以上完整螢幕時固定定案為 `遠景／無型號／無價格`，即使中央或前景其中一台確實是 FollowMe、有清楚型號或價牌，也不得讓它覆蓋全圖幾何。
- 遠景中的 FollowMe 不是「不存在」。`followme_physical_evidence` 必須如實保留同主體白色直立支架、圓形底座、托盤或直接品牌證據，並以 `wide_scene_followme_present=true` 留供稽核；但遠景仍固定 `unique_main=false`、`label_ownership!=matched`、`model=null`、`price=null`。只有全圖最多 2 台完整螢幕時，清楚且內部一致的 FollowMe 才可走單機，容易件仍可首輪結案。
- `.55` 的正式事故 `M-台北市-信義區-TK3C-信義-1042.jpg` 原圖是多台完整螢幕的寬廣展示牆，前景同時有真實 FollowMe。舊 prompt 的「FollowMe 強制單機、背景台數不得覆蓋」使它錯誤首輪上傳為單機。已用同一來源 identity、原圖 SHA、input SHA 與既有三次獨立 request-bound 呼叫綁定人工像素權威，離線定案為遠景，不增加第 4 次；正確新檔 `M-202601-台北市-信義區-TK3C-信義-遠景-1042.jpg` 已取得 Drive ID `1DuAEXAiVdam-e9zY1osgu7AQNaFMp2xy`、size `692251`、MD5 `70c12ae98d9ffd480cd5c6a0894f6aea`。兩個舊錯名 Drive ID `1hRXEob1YDuGASDrhCtm85jXIGDBfmWpv`、`1ZQL_SWoAluWnDHabUYz_MJl9STWtSNQX` 已在新檔唯一讀回後移入垃圾桶並再次確認新檔存活。
- 同批 `信義-1041` 原圖只有中央一台完整螢幕，左右鄰機被原圖邊界裁切；已用同一雜湊綁定機制定案為 `單機／S24DG302EC／3,690`。這兩張共同鎖定兩端：不能把寬廣多螢幕牆縮成前景單機，也不能把中央完整、左右裁切的近拍誤算為三台遠景。
- `finalize_existing_three_pass_reviews.py` 不得讓 live task 的 `ocr_attempt=1` 蓋掉雜湊權威所綁定的三次既有呼叫；known-pixel repair 進入 finalizer 前固定以三輪證明重建 attempt。`maintain_active_three_pass_repairs.py` 收到新版 authority manifest 時，只移除該 manifest 精確列出的舊 repair key 重新證明，不得清空其他已驗證修復，也不得無限重寫快照。
- 三輪定案新增兩條 deterministic 防線：三個健康、同圖、無記憶、request-bound 輪次都回報 `complete_screen_count>=3` 且沒有兩輪明確邊界裁切的一台敘述時，以 `three_pass_complete_screen_count_distant_authority` 定案遠景；若至少兩輪自然觀察都看到背景／展示牆的額外完整螢幕，且至少一輪結構台數為 3+，以 `two_wide_narration_votes_distant_authority` 定案遠景。兩者都只使用既有三次，禁止第 4 次。
- prompt、即時守門、runtime health、三輪 finalizer、離線補跑說明與錯誤避免手冊必須同步；禁止再保留「FollowMe 不受遠景規則影響」「背景三台以上仍強制單機」等舊句。`.56` 換版前全工具回歸 `538/538` 通過，另有核心 evidence／finalizer／runtime 組合 `238/238` 通過；正式載入仍只能在照片邊界、隱藏程序、沿用同一 port 5002 與同一既有 Dashboard 分頁，不得開新分頁或視窗。
- 三輪 deterministic finalizer 若已把不安全的型號／價格候選降階為終局 `無型號／無價格`，必須同步清除只屬於被淘汰輪次的 `model_validation_failed`、`rejected_model`、`price_conflict_detected`、`requires_structured_retry` 與 `structured_authority_blocked_fields`。不可讓已驗證終局列因舊候選旗標被 upload planner 誤擋；不可清除跨照片記憶、request binding、來源雜湊或提示污染等完整性旗標。不可改寫三輪 trace，也不可呼叫第 4 次。
- `.56` 即時驗收抓到 `微風南京-210`、`復北-1411`、`木新-1508` 三張已三輪自動定案但被上述舊旗標擋住上傳。以同一 source identity、同 revision 與既有三輪 trace 零模型呼叫重新 enqueue 後，三張均取得唯一 Drive receipt；Drive ID 分別為 `1ZcXSZ_2eqyFG1q8HtLvVgRr8NoYYKox_`、`1wjfOGyDksz7LREpq6uN91p8LcX0Yroy6`、`1dGsGBYewrEO2J7HR4L5IGU6f5Mmalg3Q`，`pending=0`。`finalize_existing_three_pass_reviews.py` 必須能針對「current revision、三輪已驗證、trace 有終局規則、task 仍帶 upload blocker」的精確列冪等修復，不得掃改其他已完成列。
- Dashboard 的「累計判讀」不得使用 `presentation_sequence` transport ordering ID 當成模型呼叫數。舊 restart／離線 presentation event 會造成序號大幅跳號，曾把畫面膨脹成 `18,799,922`。介面改用 `v1945_evidence_trace.jsonl` 的實際 durable 行數，後端以檔案 offset 增量計數；transport sequence 僅保留在 API 的 `presentation_transport_sequence` 供排序稽核。正式同分頁驗收顯示 `202601 複核 567/1,478`、`累計判讀 13,602 次`、`上傳總數 55,939／待上傳 0`、目前照片與 LLM 逐字內容同步，沒有重開或重新整理瀏覽器。
- structured authority 比對型號時，完整官方料號與標準短型號必須先用同一個 Samsung 正規化規則判斷等價。例如 `LS32DG802SCXZW` 與 `S32DG802SC` 是同一型號，不得因正規化後字串變短就當成模型偷改答案並清空。真正不同的型號、附近牌借用或 ambiguous ownership 仍必須 fail closed。事故樣本 `台南西門-237` 三輪原始 JSON 均為 `LS32DG802SCXZW／32,900`、唯一單機、matched；已用三輪 request-bound trace 零模型呼叫定案為 `S32DG802SC／32,900`，更正檔取得 Drive ID `1cL-tIIUHXpV5M9U8YJhNVfDOMh066wKy`。修復工具只允許至少兩輪同一已收錄官方 SKU、完整台數 1、unique main、matched ownership 的精確共識，不得把鄰機牌補回。
- 本次補強後核心 evidence／三輪 finalizer／runtime 組合回歸為 `239/239`，全工具回歸為 `544/544`。任何後續修改若使「三輪終局仍被舊欄位阻擋上傳」「完整官方料號被正規化守門誤清空」或「累計判讀採 transport sequence」測試失敗，不得載入正式批次。

## 2026-07-21 `.58`–`.60`：無記憶複核、價牌角色與不中斷正式驗收

- 第二、第三輪及同輪價格補查只能收到當張原圖／裁切、當輪中性問題與全新 128-bit RequestID；不得收到上一輪答案、摘要、價格、型號、錯誤原因或任何可複製答案範例。補充裁切只能描述幾何區域，不得用檔名、標題或 prompt 暗示「價牌在此」「這是 FollowMe」。若 trace 的 `prior_answer_exposure` 或 `prompt_contamination` 為真，該輪不可形成業務共識。
- prompt 中不得保留可被模型照抄的 Samsung SKU、FollowMe 款式或價格範例。FollowMe 只能由同主體實體結構／實體卡證明；螢幕畫面、背景立牌與附近商品卡不是硬體身分。未知細分款式必須如實輸出家族泛稱，不得從上一輪或附近標示補出尺寸、款式或價格。
- 價格判讀必須先列出同一價牌上所有金額與印刷角色。`市價／原價／參考價／建議售價` 不得改稱 `會員價／特價`；若同牌另有醒目的現行促銷價、限時特價、會員價或現金價，檔名價格使用該現行金額。只有一個金額時才可明說只有一個。2026 單機若型號與價格都可讀、但相對官方參考價為 `high/low`，第一輪不得直接放行，必須至少再做一次全新無記憶價牌角色核對；不同獨立輪次價格不一致時必須用完第三輪，整張照片仍最多三次。
- 已知事故 `M-台南市-永康區-TK3C-永康大灣-1415.jpg` 原圖同一價牌為 `S27D300GAC`，小字 `市價 3,590`、大字 `限時特價 3,290`。`.59` 第一輪把 3,590 誤稱會員價並錯誤 verified；內容監控在照片邊界停止 OCR，backend／Dashboard／uploader 與既有分頁保持在線。`.60` 以 source item `3c3ca38a...c6dd5`、原圖 SHA `ae801408...92eb`、input SHA `bf077115...962` 綁定像素權威，只允許三次乾淨、request-bound、無記憶呼叫後定案。
- `.60` 單張隔離實測確實走 `1→2→3` 三輪，終局為 `單機／S27D300GAC／3,290`、`price_status=match`、verified、review=0；正確逐張上傳收據 Drive ID `11sQdNFoXTs4LHGt0gBbfl7qoPLWE-vbp`、size `873038`、MD5 `3808b795ee7f6265a8822639b898025d`。舊錯名 `...↑＄3590-1415.jpg` 只以舊 Drive ID `1Fw45Wjdt3VMpdqnYOuaqfj2x6tBYJtcD` 精確移入垃圾桶。更正清冊共 8 筆全部為 `old_trashed_verified`；不得用名稱模糊刪除。
- `.60` 載入前 `tools/run_critical_regressions.py` 全通過，完整 `tools` discovery 為 `557/557`。換版只在照片邊界同步熱換唯一隱藏 uploader 與 port 5002 backend；不重啟 LM Studio，不開／重整瀏覽器。單張實測完成後切回原正式 staging `20260720_205254\\202601_商化照片-202601_6403a632`，以 `confirmed=true`、`restart=false` 從 `746/1,478` 原位接續。
- 既有 Chrome Dashboard 分頁目視驗收：同畫面可見 `正在執行`、目前照片與檔名、LLM 自然語言逐字區、同輪標籤、右側累積縮圖卡、202601 複核與逐張上傳；畫面由 `757→758/1,478`，沒有另開分頁。09:16 後端已到 `769/1,478`、verified `747`、review `22`、failed `0`、上傳總數 `56,004`、pending/working `0/0`，近期平均 `12.94` 秒。
- 低功耗唯讀稽核再抽查 `.60` 最新五張 `嘉義-701/702/703/704` 與 `嘉義新光-199`：原圖、每輪 trace、終局欄位、上傳檔名及 Drive receipt 全一致，全部 `prior_answer_exposed=false`、`prompt_contamination=false`、最多三輪並已逐張上傳。704 的單一價牌 `S27CG552EC／4,990` 首輪正確結案；199 前兩輪各有錯票，第三輪仍能保守排除為單機／無型號／無價格，沒有讓錯票冒充終局。此樣本未發現系統性跑歪；雙價牌壓力的正式權威仍以永康大灣 1415 為準。
- 上方 `65,336/151,714` 維持不動是正確語意：它只計去重後首次辨識完成的來源照片。202601 的第二／第三輪、離線修復、重新命名與取代上傳都不能重複加總；本階段實際進度看 `202601 複核 processed/1,478` 與上傳總數。完成 2026 並進入尚未首次辨識的歷史年份後，65,336 才會再次增加。

## 2026-07-21 `.60` 完整原圖權威、更正副本閉環與鄰牌防誤綁

- 三輪都已使用完、request-bound、相同 input SHA 且無前輪答案／prompt 污染的照片，不得呼叫第 4 次。若完整原圖人工稽核能確定實體幾何與同主體價牌，使用 `samsung-ocr-bound-visual-authorities/v1` 以 `source_item_id + source_file_sha256 + input_image_sha256` 三重綁定後零模型呼叫定案；未精確綁定者不得套用。
- 本次正式權威清冊為 `data/visual_authorities_202601_pingdong_changhua_20260721.json`：屏東新中正 `1008=單機/S43FM702UC/13,990`、`1011=單機/FollowMe Pro M7 43吋/17,990`、屏東太平洋 `434=單機/FollowMe 型號未細分/無價格`、彰化中山 `234=一般單機/無型號/無價格`。四張均保持三次模型呼叫上限，終局為 `auto_verified=true`、`auto_review_required=false`、`review_status=已完成`。
- `屏東新中正-1009` 不在更正清冊：中央主體價牌清楚屬於 `S27D300GAC/3,290`；左側 `S27CG552EC` 是被裁切鄰機的牌。抽查發現鄰牌候選時，必須先確認價牌與主螢幕的物理歸屬，不能因另一型號較醒目就覆寫已正確的中央主體。
- 每次內容更正都必須逐張立即上傳正確新檔，取得唯一 Drive ID、size、MD5/SHA 讀回後，再依舊 Drive ID 精確移入垃圾桶；禁止以模糊檔名刪除。`434` 的兩個舊錯名 ID `1eXdzABRaAA0VzR0Ncch9E0KYhQZlNEX6`、`1NMFO8KVxU_NNQLO_XHCjBiBDesdAJydn`，以及 `234` 的舊錯名 ID `1A2xjw4SkxfYLJj9P3SlxYEJQ4005tvDu` 均已 `old_trashed_verified`。正確新檔仍分別存在於 ID `1eHX9ddLhN1d9BOMTy0y9igUjNfdKtb21` 與 `17J0gZssmTMA-IRpm_laTFBQPSY2Pf61K`，位元組大小及雜湊讀回一致。
- `tools/maintain_active_three_pass_repairs.py` 可在舊 live backend 持續寫正式 result JSON 時，以單一隱藏 worker 冪等重套上述精確權威；它不得停止 OCR、backend、Dashboard、LM Studio 或 uploader，不得開終端機／瀏覽器，也不得產生新模型呼叫。當 evidence revision 改變時必須自行退出。
- 修復驗收不能只看 result JSON：必須同時證明正式結果、輸出檔名、逐張上傳收據、舊錯名處置帳本與遠端二次讀回；任何一層尚未完成都不得宣稱閉環。
- 修正後低功耗唯讀抽查 `比漾廣場-379/380`、`汐止-1364`：原圖、三輪敘述、結構、終局檔名一致，全部 `prior_answer_exposed=false`、`prompt_contamination=false` 且最多三輪；379/1364 的多螢幕遠景與 380 的單一 FollowMe 直立主體均合理，未發現新的系統性跑歪或鄰牌誤綁。

## 2026-07-21 `.62`：66,724 總盤復原、回覆綁定尾錨與鄰商品價格否決

- Dashboard 曾回退到 `65,336/151,714` 的根因不是 202601 複核不算進初辨識，而是 canonical `folder_summary.csv` 的 202606 列被覆寫成 `blocked/processed=5`，同時 `folder_discovery.csv` 遺失 202606 的 deterministic folder ID。202606 priority staging 已有 1,393 個唯一 durable tasks；經來源集合、source map、任務集合與 current-guard 狀態精確交叉驗證後，正確去重 processed 為 `65,331 + 1,393 = 66,724`。
- 202606 folder ID 固定為 `sha256("商化照片-202606".casefold()) = 8ae67c526e285b524d08822d0767b17ea82d9a48c630542d8c5dc3cc0c593c20`。`record_period_priority_progress.py` 必須接受 canonical summary 的欄位超集並保留 `folder_id`、`source_inventory_sha256` 等額外欄位；不得因 schema 已擴充就拒寫或退回舊列。精確 proof 為 `processed_tasks=1,393`、`current_guard_final_tasks=1,350`、`nonfinal_tasks=43`、`stale_guard_tasks=40`；這只修正 processed 總盤，不冒充 202606 Drive 完成。
- 兩張不同正式照片先後出現 `request_id_mismatch`，證明不是單一照片內容問題。每輪 prompt 現在必須把完整 32 字元 request ID 另以最後一個文字項目尾錨送出；回覆仍須逐字相等，禁止放寬、截短或猜測。fuse snapshot 必須保存 expected/actual binding ID，供事故稽核使用。
- runtime fuse 存在時，backend 換版只允許 `reload_backend_at_safe_idle.ps1 -RuntimeHealthTrialReload`，且 active fuse 與 `model_benchmark.lock` 必須同時存在；試跑只允許 1–15 張新的 `runtime_health_smoke...` 隔離資料匣與明示 `runtime_health_trial=true`，Drive 上傳仍被 lock 阻擋。一般正式路徑、續跑按鈕、supervisor 與 uploader 不得繞過 fuse。
- 嘉義新光 199 的完整原圖證實 `19,900` 是左側 Harman Kardon 喇叭價牌，不屬於中央 Samsung 螢幕。即使 structured `label_ownership=matched`，同輪自然敘述只要寫出「無法確認屬於主角／無法確認對應／無法確認空間對齊」，該輪的 model/price 與 matched 票都不得進入三輪多數決。視角仍可在三輪內如實結案，但不確定的身分欄位必須清為空，禁止把相鄰商品價格寫進檔名。
- `.62` 完整 critical regressions 退出碼 0。新的 5 張隔離實圖驗證共 15 次模型呼叫，5/5 verified、0 review、0 failed；每次 `request_id_verified=true`、`request_binding_enforced=true`、`independent_pass=true`、`prior_answer_exposed=false`、`prompt_contamination=false`、runtime healthy，且每張三輪 input SHA 一致。199 終局已正確成為 `單機／無型號／無價格`，沒有讓 19,900 或 12,990 進入終局。
- fuse 只能由 `clear_runtime_health_fuse_after_smoke.py` 在上述 proof、人工核對案例、revision、benchmark lock 與 trace 全部一致後封存；正式 retry state 不得被清除，也不得授權第 4 輪。此次 fuse receipt 為 `_ocr_audit/runtime_health_fuse_clearance/smoke_20260721_150050_531709.json`，history 為 `_ocr_audit/runtime_health_fuse_history/smoke_20260721_150050_531709.json`。
- 正式批次在既有分頁、同一 port 5002 與唯一隱藏 uploader 下，從高雄 747 已消耗的第 2 輪原位接到第 3 輪；747 正確結案 `S24F332EAC／2,390` 並取得逐張上傳，沒有重跑 1,245 張。既有 Chrome Dashboard 目視驗收顯示 `66,724/151,714`、`45/137`、`202601 1,263/1,478`、上傳總數 `56,198`、`正在執行`，目前照片、自然語言 LLM 區與右側累積卡片同步，沒有新增分頁或視窗。
- `66,724` 是目前正確的去重 processed 總盤，不得再引用 `.56/.60` 時期的 `65,336` 當作現況。202601 複核本身不重複增加此數字；之後進入未初辨識的歷史年份才會繼續增加。202606 的 43 個 nonfinal 與全部 Drive 精確收據仍要在 2026 閉環中完成，之後照 2025→2015 接續至全案 `151,714`。

## 2026-07-21 `.64`：FollowMe 商業主角優先、螢幕像素隔離與不中斷復跑

- **本節取代 `.56`「三台以上一律遠景」的舊規則。** 專案目標是從原本被判遠景的照片找回實體 FollowMe；因此必須先掃描完整原圖內的實體 FollowMe，再套用一般遠景規則。同一台顯示器只要有直接附著的 FollowMe 品牌，或在發亮螢幕像素範圍外實際連著白色直立支架與完整圓形落地底座，就以該實體為商業主角定案 `單機`，即使背景另有三台以上完整螢幕；完整螢幕數仍照實保存供稽核。只有全圖完全沒有實體 FollowMe 候選時，3+ 完整螢幕才走遠景。
- 支架、輪架、圓底座、託盤或 FollowMe 字樣若只出現在螢幕播放的影片、廣告或 UI 像素內，固定記為 `screen_content_only`、`same_subject=false`，不能成為實體證據。一般 Smart Monitor M7/M5 或 S32FM 型號也不能單獨證明 FollowMe。事故樣本 `前鎮-768` 原圖是一般 `S32FM703UC／9,990`，畫面內的移動支架內容不得污染硬體身分。
- 三輪終局必須在視角與身分欄位分開收斂。兩輪以上同圖、健康、request-bound 的實體 FollowMe 證據足以定案單機家族；若 `FollowMe M7` 與 `FollowMe Pro M7` 款式文字不一致，具體款式保持空值，不可猜測。但同一主體價牌若至少兩輪獨立讀到相同價格，款式差異不得連帶把該一致價格抹除；價格本身不同時仍清空。模型與價格各自保留仍受 matched ownership、自然敘述一致及 exact-pair 防拼接守門約束。
- `.64` 永久實圖驗收組為 `台中旗艦-939`、`嘉義-701/702/703/704`、`嘉義新光-199`、`前鎮-767`。run `20260721_183113_327069` 共 7 張、18 次模型呼叫，7 verified、0 review、0 failed、0 binding／memory／runtime invariant 錯誤；沒有第 4 輪。939 正確辨識實體 FollowMe 單機、價牌 `17,990`；701 原圖中央也確實有白色直立支架與完整圓底座，因此 `.60` 文件中「701–703 都是遠景」的舊結論已被本次完整原圖證據取代，701 應為 FollowMe 單機。
- fuse 只由 `clear_runtime_health_fuse_after_smoke.py` 依新 revision、7 張 bound smoke、人工核對樣本與完整 regressions 封存；receipt 為 `_ocr_audit/runtime_health_fuse_clearance/smoke_20260721_183541_314681.json`。benchmark lock 隨後解除，backend 與唯一隱藏 stream uploader 都載入 `.64`，既有瀏覽器分頁沒有重開、重載或新增。
- 五張正式已知錯誤以原圖雜湊綁定、零新增模型呼叫更正並逐張取得 `.64` Drive receipt：`高雄大立-408=S32DG802SC／36,900`、`高雄大立-412/413=FollowMe 單機／型號未細分／無價格`、`前鎮-766=FollowMe 單機／型號未細分／無價格`、`前鎮-768=S32FM703UC／9,990`。新 Drive IDs 依序為 `1fvL-Cn0DDi5FqxGac3qs4Y2AlTBCPevY`、`1QHB1kjhU4YWebYgml-bK0lYZTkegvemT`、`1vSLkgAW4KrbUKDusNfeqwyQp6aDLYrZG`、`1t_RzxnXY_4THXLPhKsf7aqv0y79fK97F`、`1YsI6l87nMdzVrczRiT-qvZvCgzbWXdU8`。全域更正帳本目前仍有 4 筆 mapping error，故舊錯名只可等精確帳本修復後按 Drive ID 清理；不得為清舊檔放寬 gate 或延誤 OCR／新檔逐張上傳。
- 65,336 回退是 202606 summary 被覆寫成 processed=5 的總盤錯誤；現行唯一正確上方數字是 `66,724/151,714`、`45/137`。隔離 smoke 不計正式總數，202601 二、三輪也不重複計數。18:51 既有 Dashboard 已同步顯示 `202601 1,322/1,478`、verified 1,305、review 17、failed 0、上傳 56,224／待傳 0、目前檔案 `岡山-752`，正式 OCR、LLM 自然語言、右側卡片與逐張 uploader 持續前進。

## 2026-07-21 `.70`：總盤、不中斷復原與逐張上傳鐵律

- 上方「初次辨識總進度」是**唯一來源照片**的去重計數，不是模型呼叫數、複核輪次或上傳數。現行 inventory 權威為 `66,724 / 151,714`、`45 / 137`；`65,336` 是未納入 202606 的歷史錯值。202601 的第二、第三輪、離線定案、更正改名與同一來源重新上傳均不得重複加總。Dashboard 必須同時保留當前資料夾／輪次進度，讓複核期間仍能看見實際前進。
- 正式批次的首要要求是「不中斷、正確顯示、照片邊界安全」。一張取得如實終局就立刻 enqueue，stream uploader 逐張上傳；不得等待整月、整年或人工累積。任何修復、文件、Git、清理或低功耗稽核都不可停止正式 OCR、既有 Dashboard 分頁或唯一 uploader。

## 2026-07-21 `.71`：真正無主角寬景票與完整螢幕邊界

- `identity_free_wide_candidates` 的每一票都必須同時滿足 `unique_main=false`、無型號、無價格、價牌非 matched、無 FollowMe 實體及 3+ 完整螢幕寬景敘述。欄位名叫 identity-free 就不能接受 `unique_main=true`；否則一張有唯一主角但暫時沒讀出型號／價格的單機票，會被錯算成寬景多數。
- `strict_multiscreen_distant_fallback` 與 `two_wide_narration_distant_fallback` 也必須至少有兩輪 `unique_main=false`。三輪都把被原圖左右邊界裁切的鄰機誤寫成完整，不得只憑錯誤 count=3 抹除唯一主角與同主體價牌。
- 永久回歸 `文心-645`：第一輪 `單機/unique_main=true`、第二輪真寬景票、第三輪 `單機/S27CG552EC/4,990/matched`，不得使用 `two_wide_geometry_votes_veto_single_identity_outlier`。原始解析度像素權威固定為只有中央一台完整螢幕；左右鄰機碰原圖邊界不計，終局為 `單機/S27CG552EC/4,990/✓`。
- `.70` 只有 `two_wide_geometry_votes_veto_single_identity_outlier` 終局不得直接跳過 `.71` backfill；其他 `.70` 結果未經此缺陷路徑，仍可相容沿用。問題照片只能用零模型規則重驗或精確 source item + source SHA + input SHA 視覺權威結案，不得因升版增加第 4 次模型呼叫。

## 2026-07-22 `.72`：禁止把已讀到的型號／價格洗成空值後冒充成功

- `市價／原價／參考價／建議售價` 不是看到關鍵字就一律丟棄。只有敘述明示該金額非目前售價，或同張價牌另有不同的促銷／會員／現金／優惠價時，才可視為參考價；「市價 8,990、會員售價也是 8,990」必須保留 8,990。
- 若至少兩輪結構答案重複同一組型號與價格，但其中一輪敘述又否定自己的 `label_ownership=matched`，不得把型號與價格清空後標成 verified／上傳。三輪完成後仍有此矛盾，該張必須 fail-closed，保留原始三輪證據供零模型重驗或像素權威結案，不得增加第 4 次模型呼叫。
- `.71` 已驗證結果只有在未經上述欄位清空路徑時才可相容沿用；任何三輪終局的 raw bound response 仍有 model／price、但 final parsed field 被清空者，builder 必須重新列入候選。逐張上傳 worker 不得把舊錯檔名工作直接升版。
- 系統閘不得以「最後一輪 raw 與終局不同」直接判錯，因最後一輪可能是離群值。正式 invariant 是：至少兩輪健康、同圖綁定、`label_ownership=matched` 且同一組 model+price 一致，在沒有另一組具體 pair 衝突時，終局不得清空、改寫或改成遠景；一般非 FollowMe 以 `two_pass_unique_owned_identity_consensus` 結案。若同組 pair 的敘述又否定自己的價牌歸屬，則 fail-closed，不得把矛盾洗成空欄成功。
- 每輪必須持久保存 `raw_structured_model`、`raw_structured_price` 與 `field_suppression_reasons`；三輪摘要、stream job 與 receipt 不得丟棄此 provenance。上傳入口再次執行 adjudication field invariant，任何終局欄位遺失不得建立工作。
- 09:00 全面唯讀盤點顯示 `.71` 的 43 個 verified 終局中，18 個最後一輪 raw 與 final 不同；逐輪共識篩選後有 7 個高確定性欄位清空／視角否決缺陷需重驗。Drive 側 2,894 份現行 receipt 與最新 accepted 結果有 172 張欄位不一致，681 個來源曾產生不同檔名與不同 Drive ID；只可在 exact existence／trashed／size／MD5 對帳及正確 replacement receipt 完成後逐筆處理，禁止批次猜測刪除。
- canonical stream receipt 少 4,438 份不等於 Drive 一定少 4,438 張；202602–202605 必須先以 Drive 實體清單核對，不能把缺本機 receipt 直接當缺上傳。
- 介面連續性是永久鐵律：內容 fuse 只能在照片邊界停止錯誤 OCR／錯誤上傳，port 5002、Dashboard、LM 狀態與 uploader 狀態介面必須持續在線並明示「內容守門修復中」。修復完成後由保存斷點自動續跑，不得等使用者手按、不得關閉／重開瀏覽器、不得新增分頁。
- fuse clearance 的實圖可由精確 hash-bound `three_pass_human_audited_pixel_authority` 收斂單一照片內容矛盾；只有 request binding、獨立輪次、同圖 SHA、無前輪答案、無 prompt 污染全數健康，且非健康理由只限該人工權威照片的 `structured_narration_followme_conflict`／`evidence_thinking_conflict` 時才可封存。transport、binding、memory、prompt 或跨照片錯誤仍一律不得放寬。
- durable `OCR成功.json` 的 `ocr_meta` 必須保存 `adjudication_rule` 與 `three_pass_adjudicated`；不得只在記憶體／Dashboard 顯示後遺失，否則 fuse clearance、builder 與上傳稽核無法證明終局是由哪一條規則產生。
- `新文心-967` 在安全停止邊界已消耗第三次呼叫但尚未落盤，因此只能用兩次乾淨同圖輸出加精確 source item／原圖 SHA／input SHA 像素權威零模型復原；不得再呼叫第四次。終局固定為 `單機/S24F332EAC/2,590`，Drive 收據 ID `1SUhHE9_b4Jexo2eqsTiyLE2kDZ44VuRg` 並保留 `ocr_attempt=3`。此 consumed-cap 路徑不能泛化成跳過第三輪的捷徑。
- 發現此類系統性定案漏洞時，持續運轉鐵律的正確動作是「在照片邊界停止 OCR、保持 Dashboard/LM Studio/uploader 在線、先修與回歸，再從保存斷點續跑」；繼續跑錯比短暫安全停下更違反專案目標。
- `reload_backend_at_safe_idle.ps1` 對 incomplete staging 的 interlock 是資料保護機制：helper 尚未明確釋放前，必須先恢復原正式 staging，並以 `restart=false` 保留已消耗輪次／retry state。此時 supervisor **禁止**建立新的 staging、禁止將中斷批次誤當可重開的新批次；只有原位恢復失敗且保留完整證據時才可 fail-safe。
- uploader 升版必須相容既有 pending jobs：每一輪 claim 前先以現行 schema 遷移 pending payload，再比對 source identity、來源／發布雜湊、目標檔名與既有 Drive receipt。已存在相同 Drive ID 的冪等確認只可更新本地收據，不得產生第二個雲端物件；同一照片的更正副本仍須取得新檔精確 receipt、讀回驗證後，才可依舊 Drive ID 清理舊錯名。
- FollowMe 的實體證據是嚴格幾何關係：必須是與同一主體相連、位於螢幕像素外的**白色**直立支架與完整圓形落地底座，或直接附著的 FollowMe 品牌。一般黑色支架、黑色圓底座、桌架、輪架或螢幕內廣告都不是 FollowMe 證據，絕不可把它們冒充為白色實體裝置。
- `M-高雄市-楠梓區-TK3C-右昌-1148.jpg` 已以完整原圖及雜湊綁定權威定案為 `單機 / S27D300GAC / 3,290`。它的黑色一般支架／底座不得觸發 FollowMe；其 UI 敘述出現內部結構欄位時，先限縮為同一張照片的無記憶重試並清理展示文字，不能污染下一張或停止整批。此權威只適用完全相同的 source identity、來源 SHA 與 input SHA，且不得額外呼叫第 4 輪。

## 2026-07-23 `.73`：原圖邊界零完整螢幕、鄰商品價格與自動復跑閉環

- 本節修正並取代先前把 `彰化中山-234` 寫成一般單機的結論。完整原圖中唯一可見螢幕由右下方進入畫面，右框、下框及右下角均在原圖外，故 `complete_screen_count=0`，終局固定為 `遠景／無型號／無價格`。像素權威仍須同時綁定 source item、source SHA 與實際 input SHA，且只可在三次 request-bound、無記憶、無提示污染的呼叫後離線定案，絕不允許第 4 次。
- `嘉義新光-199` 的左側 `12,990／19,900` 屬 Harman Kardon 喇叭展示，右側 FollowMe 是附近宣傳物；中央唯一完整螢幕沒有足夠同主體 SKU／價格像素。精確權威固定為 `單機／無型號／無價格`，並永久測試模型或價格候選必須被清除。
- backfill builder 在接受 `.64`–`.73` 相容 verified trace 前，必須再比對現行精確像素權威；若舊 verified 的 view、完整台數、model 或 price 與權威不符，該來源不得被算成 current verified。這避免舊 `.72` 的 `234=單機` 被靜默沿用。
- fuse smoke run `20260723_000827_374213` 對 234 恰執行三輪，三輪均 request-bound、independent、同一 input SHA、無前輪答案、無 prompt 污染；終局為 pixel authority 的 `遠景/count 0/null/null`，verified 1、review 0、failed 0、第四輪 0。clearance receipt 為 `_ocr_audit/runtime_health_fuse_clearance/smoke_20260723_001314_840098.json`。
- 內容 fuse 解除後必須由腳本自動切回正式 staging；Dashboard 不關閉、不重開分頁。此次唯一 hidden runner 自 `20260723_001355/202601_商化照片-202601_6403a632` 恢復，介面實際顯示照片與第 1→2→3 輪後連續切換下一張。
- backend 與 stream uploader 是兩個獨立長駐程序。evidence revision 變更後，即使舊 uploader PID 尚在，也可能仍載入舊 revision 而拒絕新 pending job；監督程式必須檢查 pending 是否被 claim、stderr 是否出現 `unapproved pending upload revision`。只可隱藏重啟唯一 uploader，不得重啟 OCR、Dashboard、LM Studio 或瀏覽器。此次 uploader 更新後 pending 歸零，canonical uploaded `56,317→56,319`，最新收據為 `M-202601-台中市-北屯區-TK3C-東山-遠景-1138.jpg`。
- 持續運轉的證明不是 `is_running=true` 一個欄位；至少要同時看到正式月份的 `processed/current_pass/current_file` 前進、`stream_file=current_file`、右側 presentation queue 累積，以及 verified 照片的 pending→receipt 閉環。總盤 `66,724/151,714` 是去重首次辨識數，複核期間可不變；月份複核與逐張上傳數必須持續變動。

## 2026-07-23 revision `.74`: sparse request binding, automatic checkpoint resume, and ETA authority

- Incident: the `.73` run stopped at `202601 242/1209` on `SF-台北-577`. The previous policy counted every explicit request-ID mismatch for the lifetime of the staging folder. Its first mismatch was about two hours and roughly 240 photos earlier, so a second isolated model transcription slip incorrectly became a batch-wide crosstalk fuse.
- Fix: an invalid request-ID response is still discarded, never enters adjudication/upload, and still consumes its photo's call slot. Global crosstalk now requires three distinct source photos with explicit `request_id_mismatch` inside a rolling ten-minute window. Missing/unverified echoes and sparse/expired mismatches stay photo-local; no path permits call four.
- Recovery: `tools/recover_contained_request_binding_fuse.py` accepts the legacy sparse mismatch only on call 1 or 2 with stable source mapping, a valid prepared-input hash, explicit differing expected/actual IDs, healthy earlier history, and fewer than three recorded sources. It archives the fuse, preserves the consumed call, requeues the same photo, and never uses the invalid payload as evidence.
- UI continuity: a fuse pauses OCR/upload only at a photo boundary. The backend/Dashboard/status interface remains online, and `_presentation_payload()` retains the last coherent event while its source still exists. It does not clear the boss-facing LLM/photo/cards merely because `is_running=false`, and it does not replay deleted staging data.
- Verification: presentation tests 19/19, evidence/runtime tests 202/202, narrow recovery tests 3/3, runtime safety guards, and the complete critical regression suite passed. The original port 5002 backend was safely reloaded to `.74` without a browser restart or new tab. The exact checkpoint resumed at 577; status advanced from 242 to 243 and onward, and the current-revision uploader was independently relaunched and proved pending-to-receipt progress.
- Content sample after resume: `SF-台北-581 = S27CG552EC / 4990`, `582 = S27FG532EC / 4990`, and `583 = S32DG802SC / 29900`; each matches the visible physical card in the original image. The first 20 finalized `.74` photos used 50 calls (2.50/photo), median 12.82 s and P90 16.25 s per call, first-pass closure 15%, max attempt 3, and zero accepted unverified request IDs. This is a high-risk 2026 review slice, not the expected historical easy-photo mix.
- ETA rule: the old optimistic date based only on roughly ten seconds per model call is withdrawn. At the observed end-to-end rate of roughly 2,350-2,500 finalized photos/day while actually running, the remaining 2026 accuracy backfill plus 84,990 not-yet-first-pass historical photos gives a conservative target of 2026-08-28 through 2026-09-01 if the pipeline remains continuously available. Historical throughput must be remeasured after 2025 starts; faster easy-photo closure may move the date earlier, but downtime moves it later.

## 2026-07-23 `.75`：自然語句假熔斷與第三輪零追加呼叫結案

- `必須填 null` 是說明看不清楚欄位的自然結論，不是 prompt 指令回音。`ui_narration_instruction_echo` 只攔實際模板措辭（例如「必須填入」與逐欄指示），不得以寬泛 `必須填` 停掉整批。永久測試同時證明自然 null 結論可通過、真正指令回音仍 fail-closed。
- 正式事故 `三創店-498` 已用滿三次 stateless、request-bound、同 input SHA 呼叫；舊 `.74` 在第三輪完成後誤熔斷。完整原圖核對為兩台前景螢幕完整、上排螢幕穿出原圖上緣不計；中央卡列出多個商品，不能歸屬一個精確型號或價格。source item `eb006e7d...fc3a6c`、原圖 SHA `6fa00d35...f2e06e`、input SHA `c2ef77ec...4c5cd` 綁定終局 `單機／count=2／無型號／無價格／ownership=ambiguous`。
- `tools/recover_false_instruction_echo_fuse.py` 只接受唯一 fuse 理由 `ui_narration_instruction_echo`、attempt 3、兩份 durable 先前證據、可解析的第三輪 request ID、三輪同圖且無前輪答案／prompt 污染、修正版 detector 已排除 echo、來源位元組與像素權威精確一致。它先 enqueue、再寫結果、封存 fuse 並清理該張 retry state；任何證明缺失均拒絕，且不允許第 4 輪。
- 本事故 recovery/runtime 47 項測試全通過，完整 `tools/run_critical_regressions.py` 退出碼 0。正式後端以同一 port 5002、同一 staging、原 checkpoint 載入 `.75`；未重啟 LM Studio／Chrome、未新增分頁。202601 已從 `268→273/1,209`，498 的 Drive receipt ID `1MUsxbIb7x6NheREtoQX-pLN-SrcatwEj`、size `833,687`、MD5 `81abbae20cdbf54ac3ee33e5b55276ba`，pending 回到 0。
- 先前 `2026-08-10` 是只按單次模型 latency 推算的錯誤日期，正式撤回。以剩餘約 84,990 張首次辨識及 2026 閉環項目、已觀察端到端 `2,350–2,500 verified+uploaded/日` 計，保守全案完工窗為 `2026-08-28～2026-09-01`；前提是 24 小時不中斷並自動銜接 2026→2025→…→2015。每個 12 小時窗都要用實績重新預測，不得因舊日期掩蓋停機。

## 2026-07-23 15:45：terminal cross-field invariant（尚待程式落實）

- `quality_issue` 是終局結構欄位，不是可以保留的舊輪次備註。若 final model 非空，終局不得仍宣稱「沒有規格／沒有型號」；若 final price 非空，終局不得仍宣稱「沒有價格牌」。adjudication 補回 model／price 時，必須同步重算 `quality_issue`，並原子更新 trace terminal view、durable `OCR成功.json`、presentation event、stream job 與 upload gate。
- `verified=true` 前新增跨欄位一致性 invariant；stream uploader 在 enqueue／claim 時再做第二道相同檢查。任何矛盾都不得以 verified、已完成或健康狀態發布。若 target name 已正確但 metadata／presentation 錯誤，只做零模型 durable normalization，不重複上傳；若 target name 也錯，必須取得正確新 receipt 與遠端讀回後，才可按精確舊 Drive ID 清理。
- 15:44 重掃 current-revision `.75` formal trace 已知至少三筆：`新光A13-303=S32DG702EC/14,900` 仍寫沒有價格牌、`木新-1508=S34D300GAC/2,990` 仍寫沒有規格牌、`新店中正-1511=C34G55TWWC/9,900` 仍寫沒有規格牌。`.75` runtime fuse 沒有攔截，故此問題仍是未完成工作；先全面掃 current-revision terminal rows，不能只補三個案例。
- 修復必須先有永久回歸：補欄後 stale `quality_issue` 會 fail closed；正確 null 欄位仍可保留對應缺欄品質原因；一般黑屏／畫質異常等與 model/price 無關的品質原因不得被誤刪；同一來源 target name 未改時不得產生第二個 Drive 物件。
- 若 live 掃描證明矛盾仍持續，內容處理只可在照片邊界 fail-safe，Dashboard/backend/LM/uploader 狀態介面保持在線。修復與完整 critical regressions 通過後，從原 staging/checkpoint 自動續跑，不得重開瀏覽器、建立新 staging、重置 attempt 或增加第 4 次模型呼叫。

## 2026-07-23 `.76` 終局欄位一致性與不中斷續跑

- `.75` 的問題已在 `.76` 程式化修正：三輪定案可能已恢復正確的
  `model`／`price`，但殘留當輪的「沒有規格牌／沒有價格牌」。這是
  deterministic 終局同步缺陷，不得因此增加第 4 次模型呼叫。
- 三輪定案及像素權威結案後，依最終 `view_type/model/price` 重算缺欄
  `quality_issue`；其他品質問題（例如模糊）保留。所有終局（包含非
  三輪）在 verified 前執行 cross-field invariant：有型號卻寫缺規格、
  有價格卻寫缺價格，一律 fail closed。
- stream uploader 在 enqueue 及相鄰 revision migration 時執行第二道
  相同 invariant。evidence backfill 也不能只因 revision 相容就沿用舊
  verified；每筆相容舊列仍須通過現行欄位一致性。
- 正式換版於照片邊界執行：202601 在 `948/1,209` 停止，舊 uploader
  pending/working 歸零後隱藏熱換 uploader，再以
  `reload_backend_at_safe_idle.ps1 -AllowIncompleteStoppedBatch` 換 port
  5002 backend。沿用 staging
  `20260723_001355\202601_商化照片-202601_6403a632` 並自動續跑；LM
  Studio、現有 Chrome Dashboard 分頁均未重啟、未新增。
- 上線證據：revision `20260723.76` 已從 `948` 前進至至少
  `955/1,209`；canonical upload `56,507→56,508`，pending/working 回到
  `0/0`；新 revision 前 20 筆 trace 的 cross-field contradiction 為
  0。針對性測試 133/133 及完整 critical regressions 均通過。
- `66,724/151,714` 是 authoritative source inventory 的初次處理計數；
  2026 accuracy backfill 不得重複加總。腳本先關閉 2026 正確性與逐張
  Drive receipt，之後 continuity supervisor 自動接續 2025→2015，
  初次辨識總進度才會繼續增加；不得用重複計數假裝進度。

## 2026-07-23 `.76` continuity supervisor 與 consumed-cap 收尾規則

- supervisor 接續正式 staging 時，`rerun_staged_candidates.py` 一律要有
  明確 `--execute`；若使用 `--resume-existing-then-continue`，兩者必須
  同時傳入。不得讓 argparse 每五分鐘立即退出、卻把「已重新啟動」寫入
  監督日誌。
- `/api/start_batch` 是非同步接受。接續器收到 accepted 後，要等待
  `is_running=true` 或 `processed=total` 變成可觀察狀態，才可 attach；
  不得在 accepted 與工作執行緒真正開始之間的短暫 idle gap 誤判失敗。
- 若同一張已消耗三次呼叫、durable retry state 只保存兩份同圖輸出，
  backend 必須拒絕第 4 次。只有精確命中
  `source_item_id + source_file_sha256 + input_image_sha256` 的人工像素權威，
  且至少一份乾淨、至多一份可證明為同圖非污染 containment，才可用
  `recover_consumed_cap_missing_result.py` 零模型收尾。
- `structured_authority_material_conflict:model` 只在精確像素權威本身也
  要求 `model=null`、該輪結構 model 亦為 null 時，才可視為保守空型號的
  單張 containment；不得用此例外接受具體型號衝突、binding／memory／
  prompt／transport 錯誤或其他照片。
- 新竹 1086 的原圖只安全支持 `單機 / count=1 / model=null / price=3690`：
  `Odyssey G6 QLED 1000R` 是可讀產品家族描述，不是可安全抄錄的完整
  Samsung SKU。它以既有三次額度零模型結案、先 enqueue 後寫 terminal
  result，沒有第 4 次；202601 因而從 `1208/1209` 正確收尾為
  `1209/1209`，逐張 Drive receipt 已完成。
- 三次呼叫上限的 identity 必須跨 staging／run 維持，不能只存在於單一
  `.ocr_retry_queue.json`。current-revision task 已有 `ocr_attempt=3` 且
  `auto_review_required=true` 時，builder 不得把同一 source 複製到新
  staging 再呼叫三次；必須先走保存 trace 的 deterministic finalizer，
  能安全清空欄位就如實結案，仍缺證據才留在 durable repair queue。
- 2026-07-23 21:23 的 63 張高風險候選重啟後，前 9 張已有 3 張再次走到
  三輪 review（中清 1530、東山 1140、新東海 958），證明現行 builder
  尚未執行上述跨 staging 上限。已於照片邊界建立
  `systemic_cross_staging_three_call_terminal_review_loop` fuse 與 pipeline
  pause；port 5002、Dashboard、LM Studio、uploader 狀態介面保持在線。
  在跨 staging source-level cap 與 deterministic closure 回歸完成前，
  不得解除 fuse 讓這 63 張循環重跑。

## 2026-07-24 選擇性升級與非必要第三輪禁止規則

- 正確性優先不等於每張固定三輪。正式路徑採 selective cascade：
  一般非 FollowMe 單機若 evidence contract 完整、唯一主角成立、價牌
  歸屬 `matched`、型號與價格可直接讀取、沒有欄位／敘述／binding
  衝突，第一輪立即結案並逐張上傳。
- 只有真正風險欄位才升級：短型號唯一補全、2026 價差價牌角色、缺型號
  或缺價格、遠景、FollowMe 實體疑慮、主體歸屬不明、跨輪不一致，才進
  第二輪。第二輪已用無記憶、request-bound 證據解決時必須立即結案；
  第三輪只處理第二輪後仍存在的實質衝突，禁止作為固定儀式。唯一保留
  的三輪內容稽核是使用者已明定的 2026 遠景／寬景疑似 FollowMe
  篩查；這個例外不可反向套用到一般單機。
- 多張價牌不等於歸屬不明。若敘述已明確指出其中一張與主角螢幕對齊，
  並把其餘價牌明確分配給鄰機，不能因文字同時出現「其他價牌屬於旁邊」
  就否定主角價牌的 `matched`。只有該主角價牌本身被說成無法對齊／
  無法歸屬時才繼續攔截。
- 永久回歸 `M-台南市-新營區-SF-新營-732.jpg`：第一輪的
  `S27CG552` 唯一補全為 `S27CG552EC`，且 `5,790` 為價差照片，因此
  第二輪是必要確認；第二輪再次得到同型號、同價格及 `matched` 後應
  結案，不應只因鄰機價牌文字進第三輪。舊三輪結果已以既有證據零模型
  定案為 `單機 / S27CG552EC / 5,790`，並取得逐張上傳收據；不得新增
  第四輪。
- 吞吐 KPI 必須分母公平：63 張高風險尾端的多輪比例不得當成一般
  15 萬張照片的預期分布。每個新月份另報「混合全量首輪結案率」、
  「升第二輪率」、「升第三輪率」及平均模型呼叫數；若一般混合批次的
  第三輪率異常升高，先查觸發器系統性過寬，不得以增加硬體或放任慢速
  掩蓋。

## 2026-07-24 RequestID 尾碼假污染與原位續跑

- 第二、第三輪 prompt 末尾會再次列出同一個全新 128-bit RequestID，格式為
  `本次只輸出一個 JSON 物件；其中 request_id 必須逐字等於以下本次識別碼：<32hex>`。
  這是當輪 transport binding，不是上一輪答案。`review_prior_value_present`
  檢查在比對前一輪型號／價格前，必須先移除這段完整固定格式；隨機十六進位
  尾碼偶然包含前一輪四位價格，不得誤判為跨輪記憶污染。
- 永久事故 `M-台中市-大甲區-TK3C-大甲-1521.jpg`：第一輪為
  `單機 / S24F332EAC / 2,590`；第二輪尚未送入 LM 時，新 RequestID
  恰含 `2590`，舊檢查器誤觸 durable fuse。這不是模型看過上一輪答案，
  也不是第二輪內容錯誤。
- 預推論誤熔斷只能由 `tools/recover_review_metadata_false_fuse.py`
  原位恢復。工具只接受 attempt 2 或 3、已有 durable history 與 trace
  恰好為先前已完成輪次、相同 source item／run／full-image SHA、所有先前
  輪次皆 request-bound、stateless、無 prior-answer exposure／prompt
  contamination，並只把 attempt counter 回退一格。不得清除證據、重建
  staging、重跑已完成輪次或允許第 4 次呼叫。
- 1521 恢復後真正執行第二輪，兩輪均得到
  `單機 / S24F332EAC / 2,590` 且健康，第二輪即結案。正式 202602
  checkpoint 由 `82/1,598` 持續前進；port 5002、既有 Dashboard、
  LM Studio 與逐張 uploader 均未重啟或另開分頁。
- 必測回歸：RequestID 中文尾碼含前輪價格仍健康、attempt-2 預推論回退
  保留第一輪證據、attempt-3 預推論回退保留前兩輪證據，以及完整
  `tools/run_critical_regressions.py`。修復後必須用兩次即時狀態讀取證明
  `current_file`／period processed 真正前進且 runtime fuse、pipeline
  pause 皆不存在，不能只看 Dashboard 的單一截圖。

## 2026-07-24 可預知熔斷自復原與主管可見結論

- `ocr_continuity_supervisor.ps1` 遇到唯一理由
  `review_prior_value_present`、attempt 2/3、尚未送入 LM、沒有 raw output
  的精確舊式假熔斷時，先以現行 sanitizer 自測固定 RequestID 尾碼，再以
  `recover_review_metadata_false_fuse.py` dry-run 與 apply 雙重驗證。
  驗證通過才原位回退未發生的那一次 attempt、封存 fuse 並沿用同 staging
  checkpoint；同一 source/run 最多自復原一次，第二次必須 fail closed，
  防止真正提示污染形成無限自清迴圈。
- supervisor 的 runtime source mtime 清冊必須包含
  `skills/runtime_health_gate.py` 與 `skills/batch_orchestrator.py`。
  否則守門已修正但 backend 未換版，會讓同一可預知事故重複發生。
- Dashboard／port 5002 是永久在線介面。真正系統風險可在照片邊界暫停
  OCR 與該張上傳，但頁面、狀態 API、LM 與 uploader 狀態仍須可見；
  可安全證明的舊式 metadata 假熔斷由程式自復原，不等待定時 AI 或使用者
  手按。
- 舊 `AGENTS.md` 與輸出契約曾強制模型以「所以……」結尾，後端又會把
  該字串移到最後，正是主管畫面看似賣關子的根因。新永久契約為：
  `我看到本輪結論：遠景/單機，型號/無型號，價格/無價格。`
  第一個完整句先報結論，後面才寫照片可見依據，最後以完整句號結束；
  禁止省略號、裸 JSON 或把結論留到最後。
- 舊模型若仍回傳「所以……」，`build_final_display_thinking()` 只移除
  懸空標記、保留原始可見依據，並以同輪結構欄產生主管可見的結論首句。
  原始 raw response 仍留在 evidence trace，不能用顯示整理洗掉衝突。
- 2026-07-24 正式換版只在照片邊界短暫停止 OCR 寫入，既有 Chrome 分頁
  未重開／未重整；以 `AllowIncompleteStoppedBatch` 從同一 202602 staging
  自動續跑。即時驗收由 `168/1,598` 前進，runtime fuse 與 pipeline pause
  均不存在；新卡片已實際顯示結論優先文字。
- 換版前殘留的唯一技術失敗 `大甲-1519`（舊程式缺少
  `EVIDENCE_GUARD_REVISION` 名稱）不得永久留在 failed。現行程式原位優先
  重跑後，三輪一致定案為遠景、無型號、無價格，`failed` 由 1 歸零，
  並取得逐張上傳檔名 `M-202602-台中市-大甲區-TK3C-大甲-遠景-1519.jpg`；
  正式複核同時前進至至少 `187/1,598`。

## 2026-07-24 當輪規則回音改為照片內技術隔離

- 正式事故 `M-台北市-中山區-采河-本店-1348.jpg` 第一次呼叫不是
  detector 誤報：Request ID、full-image SHA 與 source item 均綁定，
  且無前輪答案／prompt contamination，但模型在自然判讀後逐字抄入
  「完整台數只能看第一張全尺寸照片」等當輪規則。該輸出同時把碰到
  原圖邊界的面板計入六台，故不得靠刪除尾句後冒充健康證據。
- 這類「回覆端 narration 規則回音」與「送入模型前 prompt 已污染」
  必須分流。只有唯一 presentation reason 為
  `ui_narration_instruction_echo`（可伴隨同一 narration 的 FollowMe
  一致性理由）、且 request/image 綁定、independent pass、無 prior
  exposure／prompt contamination 時，才是 photo-local technical
  failure；prompt construction、binding、價格、實質內容或混合理由仍
  維持 durable fail-closed。
- 照片內技術隔離仍消耗該 source 的一次模型額度；第 1／2 次立即把同張
  放回 retry queue，但不進 presentation、business history、success 或
  upload。原始輸出只留在 audit raw evidence，主管 LLM 區只顯示安全隔離
  文字。第 3 次仍回音時該張標為 technical exhausted，主批次繼續，絕不
  產生第 4 次，也不因一張可恢復格式故障停止整個 Dashboard／月份。
- `process_single_image()` 在健康閘完成前先記錄
  `narration_instruction_echo_detected`，並禁止 raw prompt 規則短暫閃入
  `stream_buffer`；健康閘仍依此旗標判定該 call 無效，顯示清理不得洗白
  原始事故。
- 舊式 attempt 1/2 fuse 只能由
  `tools/recover_legacy_instruction_echo_fuse.py` 復原。工具要求唯一理由、
  可解析 32-hex Request ID、兩個 64-hex 身分／影像雜湊、完全綁定且
  stateless 的 snapshot、retry counter/history 與 source map 精確一致；
  它保留已消耗 attempt、只將同張插回 queue，先原子寫 receipt/history
  才刪 active fuse，不呼叫模型、不寫結果、不排上傳。
- 本事故復原後 1348 只使用剩餘 call 2/3，兩輪均健康、request-bound，
  定案遠景／無型號／無價格並逐張上傳；完整 critical regressions 及新增
  recovery tests 全通過。換版沿用同一 202602 staging 與既有瀏覽器分頁，
  即時進度由 `278/1,598` 前進到至少 `283/1,598`，fuse/pause 均為空。

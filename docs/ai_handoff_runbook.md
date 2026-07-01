# AI 接手執行手冊

本文件給另一台電腦上 pull 本專案後的 Codex / AI 使用。目標不是重新規劃，而是在使用者指定照片來源後，直接把既有流程跑起來。

## 任務定義

要完成的工作：

1. 使用該電腦上的 LM Studio 本機視覺模型，預設模型為 `qwen3vl8b-ocr`。
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
  --model qwen3vl8b-ocr `
  --dir "D:\你的照片根資料夾"
```

再啟動接力器：

```powershell
.\.venv\Scripts\python.exe tools\recursive_ocr_flat_export.py `
  --source-root "D:\你的照片根資料夾" `
  --output-dir "D:\你的照片根資料夾_OCR整理" `
  --backend-url http://127.0.0.1:5000 `
  --api-base http://127.0.0.1:1234/v1 `
  --model qwen3vl8b-ocr
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
7. 輸出資料夾在來源資料夾內：更換為來源資料夾旁邊的新資料夾，例如 `<來源>_OCR整理`。
8. 輸出資料夾是來源資料夾上層：更換為來源資料夾旁邊的新資料夾，避免把無關照片與審計檔算進輸出。
9. 輸出資料夾第一層已有 jpg/jpeg/png 但沒有 `_ocr_audit\folder_summary.csv`：改用新的輸出資料夾，或先移開既有照片。
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

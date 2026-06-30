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
.\run_recursive_ocr_flat_export.bat
```

這個批次檔會：

1. 優先使用 `.venv\Scripts\python.exe`，找不到才用系統 `python`。
2. 用 `tools\local_llm_manager.py ensure` 確認 LM Studio 與模型已啟動。
3. 啟動 `samsung_ocr_batch_processor.py` 作為本機 OCR 後端。
4. 執行 `tools\recursive_ocr_flat_export.py`，逐資料夾接力 OCR 並輸出改名照片。

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

範例：

```text
M-202605-台北市-萬華區-TK3C-萬大-單機-S27CG552EC-↑＄4990-1005.jpg
M-202512-嘉義市-東區-TK3C-垂楊-單機-FollowMe_M7_32吋-＄12990-1172.jpg
```

## 完成判定

不能只看終端機寫「完成」。要檢查輸出資料夾中的 `_ocr_audit`：

```text
_ocr_audit\folder_discovery.csv
_ocr_audit\skipped_unsupported.csv
_ocr_audit\folder_summary.csv
```

完成回報必須包含：

1. `folder_discovery.csv` 中找到幾個含照片資料夾。
2. `folder_summary.csv` 中每個資料夾的狀態是否為 `copied`。
3. `missing_result`、`missing_source`、`conflict` 是否為 0。
4. `copied_count` 加總與輸出資料夾內改名照片數是否一致。
5. `skipped_unsupported.csv` 中 HEIC/WebP 略過數。
6. 是否有 `status=error` 或 `status=blocked`。

若有錯誤，不要宣稱全量完成；回報阻塞資料夾、錯誤訊息與對應審計檔路徑。

## 常見阻塞

1. LM Studio CLI 找不到：確認 `lms` 可在 PowerShell 執行，或重新安裝 LM Studio。
2. 模型未載入：先跑 `tools\local_llm_manager.py ensure`。
3. OCR 後端未就緒：確認 `http://127.0.0.1:5000/api/status` 可回應。
4. `missing_result > 0`：代表有照片沒有 OCR 成功結果，不能把該資料夾算完成。
5. `conflict > 0`：代表改名後會撞檔名，不能覆蓋，需先看該資料夾的 `conflicts.csv`。
6. WebP/HEIC：照規格略過，只要列在 `skipped_unsupported.csv`，不要算入完成照片數。

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

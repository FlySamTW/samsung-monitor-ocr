# 另一台電腦安裝與接手手冊

本手冊給另一台電腦上的 Codex 使用，目標是把本專案跑起來、用 LM Studio 本機視覺模型辨識照片，最後把改名後照片複製到單一新輸出資料夾。

AI 接手執行時，先讀：

```text
docs\ai_handoff_runbook.md
```

該文件是實際接力執行清單；本文件主要補安裝與環境設定。

## 目標資料夾

專案路徑可依電腦調整；以下只是建議：

```text
D:\00_程式\20260120_商化自動OCR圖片
```

照片來源根目錄是外部資料，不放進 Git，也不要求每台電腦使用相同路徑。若另一台電腦使用不同磁碟或共享資料夾，啟動時在 Dashboard 選到正確資料夾即可。

照片資料夾會長這樣：

```text
D:\00_歷年商化照片\商化照片-202605
D:\00_歷年商化照片\商化照片-202604
D:\00_歷年商化照片\商化照片-202603
```

正式處理順序：由最新到最舊。
只處理 `.jpg`、`.jpeg`、`.png`；`HEIC`、`WebP` 可以略過並在審計中列出。

## 必備軟體

1. Windows 10/11。
2. Python 3.11 以上。
3. Git。
4. Node.js 20 以上。
5. LM Studio。
6. LM Studio CLI `lms` 可在 PowerShell 執行。

LM Studio 官方文件：

- 安裝與下載：https://lmstudio.ai/
- CLI 文件：https://lmstudio.ai/docs/cli
- 本機伺服器文件：https://lmstudio.ai/docs/app/api/endpoints/openai

## 專案取得

如果已有 Git 遠端：

```powershell
git clone <專案 Git URL> "D:\00_程式\20260120_商化自動OCR圖片"
cd "D:\00_程式\20260120_商化自動OCR圖片"
```

如果是手動複製專案資料夾，也要確認至少包含：

```text
samsung_ocr_batch_processor.py
samsung_ocr_prompt.txt
型號表.txt
run_ocr.bat
start_local_llm.bat
skills\
tools\
dashboard\
docs\
```

不要把大量照片放進 Git。

## Python 環境

```powershell
cd "D:\00_程式\20260120_商化自動OCR圖片"
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install openai flask flask-cors rich requests pillow opencv-python numpy pandas
```

若 `run_ocr.bat` 有補安裝套件，也仍建議先手動確認 `opencv-python`、`pillow`、`numpy` 已存在。

## 前端環境

只有修改 Dashboard 時才需要：

```powershell
cd "D:\00_程式\20260120_商化自動OCR圖片\dashboard"
npm install
npm run build
```

前端修改後必須重新 build `dashboard/dist/`，否則瀏覽器看到的仍是舊版。

## LM Studio 模型

優先使用現有基準：

```text
qwen3vl8b-ocr
```

可評估的新 8B 視覺模型：

```text
qwen/qwen3-vl-8b
Qwen3-VL-8B-Instruct
```

不要直接假設新模型較好；先跑固定驗證集比較。

啟動本機模型：

```powershell
.\start_local_llm.bat
```

或用工具確認：

```powershell
.\.venv\Scripts\python.exe tools\local_llm_manager.py ensure
```

確認 API：

```powershell
Invoke-RestMethod http://127.0.0.1:1234/v1/models
```

## 啟動 OCR

建議使用本機 LM Studio：

```powershell
.\.venv\Scripts\python.exe samsung_ocr_batch_processor.py `
  --api_base http://127.0.0.1:1234/v1 `
  --api_key lm-studio `
  --model qwen3vl8b-ocr `
  --dir "D:\00_歷年商化照片\商化照片-202603"
```

啟動後到 Dashboard 按「繼續執行」或「開始執行」。

## 模型評估流程

先選固定測試集，例如 `tools\qwen_vl_regression_cases_202603_all.json`。

基準模型：

```powershell
setx LOCAL_LLM_MODEL qwen3vl8b-ocr
.\.venv\Scripts\python.exe tools\run_qwen_vl_guard.py
```

候選模型：

```powershell
setx LOCAL_LLM_MODEL <新8B模型名稱>
.\.venv\Scripts\python.exe tools\run_qwen_vl_guard.py
```

比較重點：

1. 類別是否正確。
2. 型號是否逐字正確。
3. 價格是否正確。
4. `FollowMe M5 32吋`、`FollowMe M7 32吋`、`FollowMe Pro M7 43吋` 是否分得出來。
5. 是否有自信錯讀。

若新模型只讓輸出更流暢，但型號或價格更會猜，不能換。

## 產生改名計畫

先跑 OCR，取得 `runs\<run_id>\results.csv`。接著只產生計畫：

```powershell
.\.venv\Scripts\python.exe tools\photo_rename_planner.py `
  --image-dir "D:\00_歷年商化照片\商化照片-202603" `
  --results "runs\<run_id>\results.csv"
```

產出的檔名格式：

```text
M-202603-台中市-大甲區-SF-大甲-遠景-型號未辨識-無價格-911.jpg
M-202603-台中市-大甲區-SF-大甲-單機-S27CG552EC-↑＄4990-914.jpg
M-202603-台中市-大甲區-SF-大甲-單機-FollowMe_M7_32吋-✓＄12990-915.jpg
M-202603-台中市-大甲區-SF-大甲-單機-FollowMe_Pro_M7_43吋-？＄17990-916.jpg
```

若要把改名後照片全部放到同一層新資料夾，可以使用這種輸出形態：

```text
D:\00_歷年商化照片_OCR整理\M-202605-台北市-萬華區-TK3C-萬大-單機-S27CG552EC-↑＄4990-1005.jpg
D:\00_歷年商化照片_OCR整理\M-202512-嘉義市-東區-TK3C-垂楊-單機-FollowMe_M7_32吋-＄12990-1172.jpg
```

大量歷年接力時：

1. 照片大於 2K 時，長邊縮到 `2560`，短邊按原比例自然縮放；不裁切、不補白、不硬拉伸。例：`4000x3000` 會變成 `2560x1920`。
2. 以執行年份判斷官網比價範圍；2026 年執行時，2025 含以前不做官網價格比對。
3. 不要把 `D:\00_歷年商化照片` 寫成程式唯一預設；它只是這台電腦目前的資料位置。
4. 當年度照片比價後，價格欄要保留 `↑/↓/✓/？`；歷史年度不比價時只保留店內價格。

可直接雙擊：

```text
run_recursive_ocr_flat_export.bat
```

建議 AI 用環境變數指定路徑後執行，避免互動輸入卡住：

```powershell
$env:OCR_SOURCE_ROOT = "D:\你的照片根資料夾"
$env:OCR_OUTPUT_DIR = "D:\你的照片根資料夾_OCR整理"
.\run_recursive_ocr_flat_export.bat
```

輸出資料夾不可等於來源根資料夾，也不可放在來源根資料夾底下，避免重跑時掃到自己輸出的改名照片。
中斷後重跑同一個輸出資料夾時，已完整複製、來源照片數與最新修改時間未變的資料夾會被標為 `skipped_existing`，不會再複製出 `_2` 重複檔。

若手動執行 Python 接力器，必須先啟動 `samsung_ocr_batch_processor.py` 後端；完整步驟以 `docs\ai_handoff_runbook.md` 為準：

```powershell
.\.venv\Scripts\python.exe tools\recursive_ocr_flat_export.py `
  --source-root "D:\你的照片根資料夾" `
  --output-dir "D:\你的照片根資料夾_OCR整理" `
  --ensure-llm
```

完成後，所有改名照片會在同一層輸出資料夾；審計表在 `_ocr_audit`。
跑完後請先驗收輸出資料夾；通過才回報全量完成：

```powershell
.\.venv\Scripts\python.exe tools\recursive_ocr_audit_report.py `
  --output-dir "D:\你的照片根資料夾_OCR整理"
```

若驗收失敗，先看 `_ocr_audit\audit_report.csv`，不要直接宣稱完成。

正式改名以前要確認：

```text
missing_result=0
missing_source=0
conflict=0
```

正式原地改名才加：

```powershell
--apply
```

## 不要做的事

1. 不要大幅改寫 `samsung_ocr_prompt.txt`。
2. 不要用 `tools/direct_ocr_batch.py` 的簡化提示詞當正式改名依據。
3. 不要一開始就用 `--apply`。
4. 不要把照片複製進 Git。
5. 不要把雲端模型跑出的整月結果直接當正式真值。

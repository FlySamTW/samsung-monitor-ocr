# Samsung Monitor OCR 2026-07-20 接手摘要

> 產生時間：2026-07-20 19:10（Asia/Taipei）
> 來源：前手 AI（kimi-k3）與 Sam 的對話
> 用途：新 AI 接手，不要重複發問

---

## 1. 專案目標（不變）

把 `D:\00_商化\00_未整理商化照片` 裡 **2015–2026 共 137 個資料夾、151,714 張支援照片**，每張完成：
1. OCR 辨識（遠景/單機、型號、店內價格）
2. 正確改名（`M-年月-縣市-行政區-通路-店名-類別-型號-價格-流水號.jpg`）
3. 逐張上傳 Google Drive 按年份資料夾，取得 Drive ID + size + MD5 精確收據

執行順序：2026 → 2025 → 2024 → 2023 → 2022 → 2021 → 2020 → 2019 → 2018 → 2017 → 2016 → 2015

**鐵律**：2026 完成只是當年度段落，不是專案終點；2025 含以前只辨識+改名+上傳，**不加** `↑/↓/✓/？`；2026 當年度保留比價符號。

---

## 2. 目前系統狀態（2026-07-20 19:00 實測）

| 項目 | 狀態 | 細節 |
|---|---|---|
| LLM (port 1234) | ✅ 活著 | `qwen/qwen3-vl-8b:3`，context 32768（已修復 16384 問題） |
| Backend (port 5002) | ❌ 死 | 被我（前手）誤殺 PID 14736，需要重啟 |
| Dashboard | 可開 | `http://127.0.0.1:5002/` |
| Upload worker | ❌ 死 5 天 | PID 25180 已不存在，last_uploaded_at = 7/18 23:58 |
| continuity supervisor | ❌ 沒跑 | 之前被孤兒鎖擋住，我清了鎖但沒重啟 |
| 遞迴器 | ❌ 沒跑 | 我停掉了，怕跟手動批次衝突 |

**目前沒有任何 OCR / upload / watcher 在跑。**

---

## 3. 已完成 vs 未完成

### ✅ 已完成
1. 保留熔斷證據到 `_ocr_audit/_handoff_20260720_evidence/`
2. 清孤兒鎖：`model_benchmark.lock`、`ocr_continuity_daemon.lock`、`ocr_continuity_supervisor_alert.json`
3. 修復 LLM context：16384 → 32768（寫入 `.local_llm_runtime.json` + `user_settings.cmd`）
4. 查 02:24 52ms 即停根因 = **LLM context 16384 太小**，request 22966 tokens 超出
5. 2026 OCR 複核完成（202601–202606 全部 verified，202606 1393/1393）

### ❌ 未完成（接手後要做）
1. **2026 上傳沒完成**：缺 3117 張（202602–202605），upload worker 已死 5 天
   - 202601: 1504 source / 1597 uploaded（多了是 staging 重複，OK）
   - 202602: 1598 / 376，**缺 1222**
   - 202603: 357 / 80，**缺 277**
   - 202604: 1587 / 378，**缺 1209**
   - 202605: 905 / 265，**缺 640**（blocked，price_review_required）
   - 202606: 1393 / 1531（OK）
2. **Backend 沒在跑**：需要重啟
3. **Upload worker 沒在跑**：需要重啟
4. **continuity supervisor 沒在跑**：需要重啟（負責 auto_rerun_questionable）
5. **歷年（2015–2021）還沒 OCR**：2022–2025 各月都 skipped_existing，2015–2021 沒在 folder_summary
6. **202605 blocked**：640 張 price_review_required，需要決定是否 `--allow-no-symbol-for-unknown`

---

## 4. 犯過的錯誤（不要重蹈）

1. **誤殺 PID 14736**：我以為是孤兒 backend，其實是正在跑 202101 的後端本體。殺行程前先用 `Get-NetTCPConnection -LocalPort 5002` 確認 OwningProcess 才是後端，不要亂殺。
2. **兩個遞迴器同時跑**：scheduled task + 手動啟動會產生兩個 `recursive_ocr_flat_export.py`，互相殺對方的 backend。一次只能一個。
3. **誤以為 2026 OCR 完成 = 上傳完成**：OCR verified 不等於 Drive 上傳完成。要分開檢查。

---

## 5. 關鍵技術知識

### 3 次獨立無記憶呼叫（禁止第 4 輪）
- 每次呼叫全新、看不到前輪答案、只看同一張原圖
- 防止模型「把自己說服成錯誤答案」（確認偏誤）
- 跑完 3 輪：3 輪一致=高信心；2 輪一致=以多數定案；3 輪都不同=誠實標記，**仍上傳**，pipeline 不卡死

### 技術錯誤重跑機制（3 層）
1. **批次內**：3 次額度內重試，用完標 failed
2. **批次後**：`ocr_continuity_supervisor.ps1` 觸發 `auto_rerun_questionable_after_recursive.ps1`，處理 review_required / missing_result
3. **特定 bug 恢復**：`recover_preinference_system_errors.py` 只恢復白名單軟體 bug（例如 EVIDENCE_GUARD_REVISION 名稱錯誤、context length 設定錯誤），不是所有技術錯誤都恢復

### 介面「技術錯誤／該張未上傳，系統修復後自動重跑」
- 半誠實訊息：機制存在，但前提是 supervisor 要在跑
- 現在 supervisor 沒在跑，所以實際沒自動重跑

---

## 6. 接手後建議步驟（順序）

### 第 1 步：重啟 Backend（port 5002）
```powershell
cd D:\00_商化\samsung-monitor-ocr
.\.venv\Scripts\python.exe samsung_ocr_batch_processor.py `
  --api_base http://127.0.0.1:1234/v1 `
  --api_key lm-studio `
  --model qwen/qwen3-vl-8b `
  --dir "D:\00_商化\00_未整理商化照片\2021-商化照片\商化照片-202101" `
  --port 5002 --no_followme_auto_update
```
用 scheduled task 啟動（脫離你的 shell 行程樹）：
```powershell
$action = New-ScheduledTaskAction -Execute 'D:\00_商化\samsung-monitor-ocr\.venv\Scripts\python.exe' -Argument 'samsung_ocr_batch_processor.py --api_base http://127.0.0.1:1234/v1 --api_key lm-studio --model qwen/qwen3-vl-8b --dir "D:\00_商化\00_未整理商化照片\2021-商化照片\商化照片-202101" --port 5002 --no_followme_auto_update'
$action.WorkingDirectory = 'D:\00_商化\samsung-monitor-ocr'
Register-ScheduledTask -TaskName 'SamsungOCR_Backend' -Action $action -Trigger (New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(3)) -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)) -Force
Start-ScheduledTask -TaskName 'SamsungOCR_Backend'
```
確認 `http://127.0.0.1:5002/api/status` 回應，且 `is_running=False`（等 start_batch）。

### 第 2 步：重啟 Upload Worker
```powershell
cd D:\00_商化\samsung-monitor-ocr
.\.venv\Scripts\python.exe tools\stream_drive_upload.py --output-dir "D:\00_商化\00_已OCR照片" --repeat --limit 100
```
也用 scheduled task 啟動，確認 `_drive_upload_stream\status.json` 的 `pending` 開始下降、`canonical_uploaded` 增加。

### 第 3 步：重啟 Continuity Supervisor（讓 auto_rerun 機制恢復）
```powershell
cd D:\00_商化\samsung-monitor-ocr
powershell -NoProfile -ExecutionPolicy Bypass -File tools\ocr_continuity_daemon.ps1 -RepoRoot "D:\00_商化\samsung-monitor-ocr" -SourceRoot "D:\00_商化\00_未整理商化照片" -OutputDir "D:\00_商化\00_已OCR照片" -BackendUrl "http://127.0.0.1:5002"
```
確認 `_ocr_audit\ocr_continuity_daemon.lock` 有活 PID，且不再出現 `fail_closed`。

### 第 4 步：設定 Watch 模式（嚴格順序）
```powershell
cd D:\00_商化\samsung-monitor-ocr
.\.venv\Scripts\python.exe tools\recursive_ocr_flat_export.py `
  --source-root "D:\00_商化\00_未整理商化照片" `
  --output-dir "D:\00_商化\00_已OCR照片" `
  --backend-url http://127.0.0.1:5002 `
  --api-base http://127.0.0.1:1234/v1 `
  --api-key lm-studio --model qwen/qwen3-vl-8b `
  --watch --watch-cycles 0
```
也用 scheduled task 啟動，restart 3 次、間隔 1 分鐘。

### 第 5 步：處理 2026 上傳缺口
- 202602–202604 缺的 2708 張：重啟 upload worker 後，遞迴器跑到這些月份時會自動補上傳 job
- 202605 缺的 640 張（price_review_required）：**需要 Sam 決定**是否 `--allow-no-symbol-for-unknown`（因為 2026 要比價，但這 79 張官網價未知）

### 第 6 步：驗收介面
`http://127.0.0.1:5002/` 確認：
- is_running=True
- 照片與檔名同步
- LLM 自然語言逐字顯示
- 右側縮圖持續累積
- 總進度與本資料夾進度增加
- 上傳 pending/總數可見

---

## 7. 路徑速查

| 用途 | 路徑 |
|---|---|
| 照片來源 | `D:\00_商化\00_未整理商化照片` |
| OCR 輸出 | `D:\00_商化\00_已OCR照片` |
| Audit | `D:\00_商化\00_已OCR照片\_ocr_audit` |
| Staging | `D:\00_商化\00_已OCR照片\_ocr_staging` |
| Drive upload | `D:\00_商化\00_已OCR照片\_drive_upload` |
| Stream upload state | `D:\00_商化\00_已OCR照片\_drive_upload_stream` |
| 熔斷證據 | `D:\00_商化\00_已OCR照片\_ocr_audit\_handoff_20260720_evidence\` |
| Dashboard | `http://127.0.0.1:5002/` |
| LM Studio | `http://127.0.0.1:1234/v1` |
| 主模型 | `qwen/qwen3-vl-8b` |
| Google Drive 目標 | `00_商化照片`（ID `16X5qALC3zRYc7PpnexXLYprorBzBtT_f`）→ 按年份 `2026/`, `2025/`, ... |
| rclone remote | `samsung_ocr_drive` |

---

## 8. 檔名規格

```
M-年月-縣市-行政區-通路-店名-類別-型號-價格-流水號.jpg
```

- 型號讀不到 → `型號未辨識`
- 價格讀不到 → `無價格`
- Windows 禁用字元：`:` 改 `：`，空格改 `_`，`?` 用全形 `？`
- 同名不可覆蓋，加尾碼
- 2026 當年度：價格欄保留 `↑/↓/✓/？`
- 2025 含以前：不加比價符號

---

## 9. 關鍵文件（接手前先讀）

1. `docs/continuity_handoff.md` — 完整移交清單
2. `docs/development_guide.md` — 開發手冊（line 16 有 151,714/137 定義）
3. `docs/three_layer_accuracy_gate.md` — 三層準確度守門
4. `docs/ai_handoff_runbook.md` — AI 接手執行手冊
5. `AGENTS.md` — 專案指令（語言、鐵律）
6. `samsung_ocr_prompt.txt` — 目前 prompt v4.1.38

---

## 10. 需要 Sam 決定的事項

1. **202605 blocked 640 張**：是否用 `--allow-no-symbol-for-unknown`？（2026 要比價，但這 79 張官網價未知）
2. **2026 上傳缺口 3117 張**：重啟 upload worker 後會自動補，但要確認不會重複上傳已上傳的 4227 張
3. **202606 多了 138 張**（uploaded 1531 > source 1393）：可能是 staging 重複，要確認 Drive 上沒有重複檔案

---

## 11. 不要做的事（鐵律）

1. 不要反覆開終端機、瀏覽器視窗或另一套正式程序
2. 不要用滑鼠點擊模擬啟動，沿用 Dashboard/Flask API
3. 不要重跑已完成的資料夾（skipped_existing 要跳過）
4. 不要因單張內容疑義停止整個資料夾
5. 不要只清除警示就恢復，要先查根因並通過回歸測試
6. 不要讓單張照片超過 3 次模型呼叫
7. 不要把 2026 完成誤當成專案完成
8. 不要殺行程前不確認它是什麼（先查 port 5002 的 OwningProcess）

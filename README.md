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

若只想先啟動本機 LLM，可雙擊 `start_local_llm.bat`。預設會優先載入 `qwen3vl8b-ocr`，找不到 8B 時改用 `qwen3vl4b-ocr`。

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
| tools/run_qwen_vl_guard.py | Prompt 守門測試 |
| tools/photo_rename_planner.py | 依 OCR 結果產生照片改名計畫，預設不改照片 |
| tools/recursive_ocr_flat_export.py | 遞迴接力 OCR，完成後複製改名照片到單一資料夾 |
| tools/recursive_ocr_audit_report.py | 驗收遞迴接力輸出是否完整 |
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
.\run_recursive_ocr_flat_export.bat
```

輸出資料夾不可放在來源資料夾底下，避免下次重跑時掃到自己輸出的改名照片。

此工具會從最新月份往前處理含子資料夾的照片，使用既有 Dashboard/Flask 後端 API 跑 OCR，完成後把改名照片複製到同一層輸出資料夾。審計檔會放在輸出資料夾的 `_ocr_audit`。
接力器預設會續跑：已成功複製、來源照片數與最新修改時間未變、且目標檔案仍存在的資料夾會標為 `skipped_existing`，避免重跑時產生 `_2` 重複檔。

`run_recursive_ocr_flat_export.bat` 跑完會自動執行驗收工具；只有手動拆開執行 Python 接力器，或要重驗舊輸出資料夾時，才需要另外執行：

```powershell
.\.venv\Scripts\python.exe tools\recursive_ocr_audit_report.py `
  --output-dir "D:\你的照片根資料夾_OCR整理"
```

驗收通過時會顯示 `status=passed`；若失敗，批次檔會停在錯誤狀態，細節會寫到 `_ocr_audit\audit_report.csv`。

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

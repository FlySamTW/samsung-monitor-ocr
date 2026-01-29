# 三星通路照片 OCR 批次辨識系統 - 完整操作手冊

## 📋 目錄

1. [專案概覽](#專案概覽)
2. [系統需求](#系統需求)
3. [安裝設定](#安裝設定)
4. [檔案結構](#檔案結構)
5. [快速開始](#快速開始)
6. [系統穩定維護 (Nuke 策略)](#系統穩定維護-nuke-策略)
7. [詳細操作說明](#詳細操作說明)
8. [Web 儀表板](#web-儀表板)
9. [進階功能](#進階功能)
10. [故障排除](#故障排除)
11. [API 文件](#api-文件)
12. [附錄](#附錄)

---

## 專案概覽

### 專案目標

使用本地視覺語言模型 (VLM) 批次處理三星通路照片，自動辨識並提取：

- 照片分類（單機/遠景/不合格）
- 螢幕型號
- 價格
- 黑屏狀態

### 技術架構 (v17.05)

```
┌─────────────────┐
│   LM Studio    │ (GLM-4.6V-Flash / Qwen-VL)
│  Local Server  │ (http://localhost:1234)
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────┐
│  samsung_ocr_batch_processor.py │ (v17.05 Single-Stage)
│  - BatchOrchestrator 核心調度    │
│  - Flask API Server (Port 5000) │
└────────┬────────────────────────┘
         │
         ▼
┌─────────────────────────────────┐
│  dashboard/dist/index.html      │ (Vite + Vue/React)
│  - 即時監控 / 失敗紀錄追蹤        │
│  - 強制重跑 (Force Rerun)       │
│  - 人工審核與反饋學習            │
└─────────────────────────────────┘
```

### 系統特性

- ✅ **極速單階段辨識**: 採用縮小化的 Qwen-VL 策略，提升辨識效率。
- ✅ **穩定維護機制**: 內建 Nuke 策略，啟動前自動清理舊進程，避免 Port 衝突。
- ✅ **精確型號校正**: 動態比對 `型號表.txt`，自動標記「未建檔」機型。
- ✅ **失敗紀錄管理**: 專門的 UI 追蹤失敗檔案，支援一鍵「強制重跑」。
- ✅ **斷點續傳**: 自動檢查已存 JSON 結果，跳過已成功檔案。

---

## 系統需求

### 硬體需求

- GPU: NVIDIA RTX 3060 (12GB VRAM) 或更高
- CPU: Intel i7-10700 (8核16執行緒) 或同等
- RAM: 16GB 以上

### 軟體需求

- 作業系統: Windows 10/11
- Python: 3.8 以上
- LM Studio: 0.3.39 或以上

### Python 套件

```bash
pip install flask flask-cors openai pillow pandas psutil rich
```

---

## 安裝設定

### 1. 安裝 LM Studio

1. 下載 LM Studio: https://lmstudio.ai/
2. 下載模型: `zai-org/glm-4.6v-flash` (Q4_K_M 量化版本)
3. 啟動 Local Server:
   - GPU Layers: 32/40
   - CPU 執行緒: 8
   - 上下文: 4096 tokens
   - 開啟 Flash Attention
   - 開啟 Offload KV Cache
   - API URL: http://localhost:1234

### 2. 專案設定

```bash
# 進入專案目錄
cd D:\00_程式\20260120_商化自動OCR圖片

# 安裝 Python 套件
pip install flask flask-cors openai pillow pandas psutil rich

# 檢查檔案結構
dir
```

### 3. 準備資料檔案

確保以下檔案存在：

- `型號表.txt` - 標準型號列表
- `商化照片-202512\` - 待處理照片目錄
- `商化照片-202512-訓練數據\` - 訓練資料目錄 (選用)

---

## 檔案結構

```
20260120_商化自動OCR圖片/
├── samsung_ocr_batch_processor.py    # 主程式 (OCR + Flask API)
├── run_pipeline.ps1                   # 自動化批次腳本
├── dashboard_v2.html                   # Web 儀表板
├── 型號表.txt                          # 標準型號列表
├── project-1-at-2026-01-20-09-01-f1ed471e.json  # 訓練資料
├── few_shot_examples.json             # Few-shot 範例
├── dynamic_data.json                  # 動態學習資料 (自動生成)
├── export_skill.py                    # 技能匯出工具
├── 商化照片-202512/                   # 待處理照片目錄
├── 商化照片-202512-訓練數據/         # 訓練資料目錄
└── 結果檔案 (自動生成)
    ├── results.csv                    # 主要結果 (CSV)
    ├── results.xlsx                   # 主要結果 (Excel)
    └── results.json                   # 原始結果 (JSON)
```

---

## 快速開始

### 方法一：直接執行 Python 腳本

```bash
# 基本執行
python samsung_ocr_batch_processor.py --images "商化照片-202512" --output "results.json"

# 完整參數範例
python samsung_ocr_batch_processor.py \
  --images "商化照片-202512" \
  --output "results.json" \
  --few_shot "project-1-at-2026-01-20-09-01-f1ed471e.json" \
  --model "zai-org/glm-4.6v-flash" \
  --model_list "型號表.txt" \
  --api_base "http://localhost:1234/v1" \
  --max_size 3072
```

### 方法二：使用 PowerShell 自動化腳本

```powershell
# 執行完整流程 (訓練驗證 + 正式處理)
.\run_pipeline.ps1
```

### 方法三：啟動 Web 儀表板

```bash
# 啟動處理引擎 (會同時啟動 Flask API)
python samsung_ocr_batch_processor.py

# 在瀏覽器開啟儀表板 (服務於 Port 5000)
# 直接開啟 http://localhost:5000
```

---

## 系統穩定維護 (Nuke 策略)

為了確保系統長時間運行穩定，避免舊有的 Flask 或 OCR 進程占用 Port 或資源，建議在每次啟動前執行以下腳本：

### 1. 強制重設環境 (Nuke)

當儀表板無法連線或出現 `ERR_CONNECTION_REFUSED` 時使用：

```powershell
# 強制殺掉占用 Port 5000 與 1234 的舊進程
.\nuke_processes.ps1
```

### 2. 標準啟動流程 (推薦)

```powershell
# 自動化啟動：清理環境 -> 檢查配置 -> 啟動伺服器
.\start_ocr_system.ps1
```

### 3. 安全停止

```powershell
# 優雅停止所有背景 OCR 服務
.\stop_services.ps1
```

---

## 詳細操作說明

### 參數說明

| 參數           | 預設值                   | 說明                                  |
| -------------- | ------------------------ | ------------------------------------- |
| `--images`     | `./photos`               | 照片目錄路徑                          |
| `--output`     | `results.json`           | 輸出檔案名稱                          |
| `--max_size`   | `None`                   | [v17.05] 預設不壓縮圖片以確保最高準度 |
| `--model`      | `zai-org/glm-4.6v-flash` | 模型名稱                              |
| `--model_list` | `型號表.txt`             | 標準型號表路徑                        |

### 處理邏輯 (v17.05 特定規則)

1. **FollowMe 判定**: 必須同時看見「白色圓盤底座」與「細長落地支架」才會判定為 FollowMe 系列。
2. **型號校正**: 系統會自動將辨識結果與 `型號表.txt` 比對。若不匹配，會標記為 `(未建檔)` 供管理員確認。
3. **價格檢核**: 價格必須包含 `$` 或千分位逗號 `,` 且為 4-5 位數才視為有效價格。
4. **不合格分類**: 分為「照不清楚」、「沒有規格牌」、「沒有價格牌」、「沒有規格和價格牌」。

---

## Web 儀表板

### 啟動方式

```bash
# 終端 1: 啟動處理引擎
python samsung_ocr_batch_processor.py

# 終端 2: 啟動 Web 伺服器 (若需要單獨啟動)
# Flask API 自動在背景運行於 Port 5000

# 瀏覽器: 直接開啟 http://localhost:5000
```

### 儀表板功能

1. **即時監控 (Main Dashboard)**
   - 顯示當前 LLM 思考過程 (`[THINK]` 標籤)。
   - 即時進度條與熱力圖。
   - 統計資訊：精確統計當前資料夾的成功/失敗總數。

2. **失敗紀錄與修復 (Failed Records)**
   - **專屬頁面**: 展示所有辨識失敗或分類為「不合格」的照片。
   - **強制重跑 (Force Rerun)**: 若修正了 Prompt 或圖片位置，可在此按鈕觸發重新辨識（系統會自動刪除舊 JSON 紀錄並重新排隊）。

3. **人工審核 (Human-in-the-loop)**
   - 點擊圖片可直接修正型號或價格。
   - 修正後的資料會寫回 `xxx_ocr_result.json`，並在重新整理後反映在成功列表中。

4. **系統日誌 (Real-time Logs)**
   - 完整追蹤與 LM Studio 的通訊狀態。
   - 顯示 CPU/Memory 資源占用率。

### API 端點 (v17.05)

#### GET `/api/status`

回傳當前處理狀態

```json
{
  "version": "v17.05...",
  "current_file": "...",
  "stats": {
    "processed": 1356,
    "success": 1317,
    "failed": 39,
    "total": 1356
  },
  "is_running": true,
  "stream_buffer": "當前模型思考中文字..."
}
```

#### POST `/api/start_batch`

帶參數啟動或重跑

```json
{
  "dir": "商化照片-202512",
  "restart": false,
  "reprocess_last_n": 5
}
```

#### GET `/api/image/<filename>`

取得圖片檔案

---

## 進階功能

### 1. 型號智能校正

系統會自動將 OCR 識別的型號與標準型號表比對：

- 精確比對 (忽略大小寫、分隔符號)
- 子字串比對 (部分匹配)
- 模糊比對 (相似度 ≥ 40%)
- 匹配失敗則標記為「不合格」

### 2. 動態 Few-Shot 學習

系統會學習使用者修正案例，自動加入動態範例：

```python
# 存儲在 dynamic_data.json
{
  "feedback_rules": [
    "User Note (xxx.jpg): 這很明顯是遠景"
  ],
  "dynamic_examples": [
    ["xxx.jpg", {"category": "遠景", "model": null, "price": null}]
  ]
}
```

### 3. 技能匯出

將累積的學習經驗匯出為 Skill 檔案：

```bash
python export_skill.py --input dynamic_data.json --output SAMSUNG_OCR_EXPERIENCE_SKILL.md
```

### 4. 斷點續傳

中斷後重新啟動，系統會自動跳過已處理的檔案：

```bash
# 繼續處理
python samsung_ocr_batch_processor.py --images "商化照片-202512" --output "results.json"
```

---

## 故障排除

### 常見問題

#### 1. API 連線失敗 / 儀表板空白

**症狀**: `[Error] API 連線失敗` 或 儀表板頁面顯示不正常。

**解決方案**:

- 確認 `LM Studio` 已開啟且載入模型。
- 執行 `.\nuke_processes.ps1` 清理舊進程，然後重新執行 `python samsung_ocr_batch_processor.py`。
- 檢查瀏覽器控制台 (F12) 是否有 `CORS` 或 `Connection Refused` 錯誤。

#### 2. 統計數字不準（例如已處理數超出總計）

**主要原因**: 歷史紀錄檔 (`project-output.json`) 包含舊資料。

**解決方案**:

- 進入該照片資料夾，刪除舊的 `xxxx-OCR成功.json` 與 `project-output.json`。
- 系統採去重計算法，重新啟動後會以目前資料夾內存在的實體結果檔為準。

#### 3. 處理中斷或卡住

**解決方案**:

- 系統支援斷點續傳，直接 Ctrl+C 停止後重新執行腳本即可。
- 如果是模型思考過久，可調整 `samsung_ocr_prompt.txt` 簡化要求。

---

## API 文件

### 程式庫匯入

```python
from samsung_ocr_batch_processor import SamsungOCRProcessor
```

### 基本使用

```python
processor = SamsungOCRProcessor(
    api_base="http://localhost:1234/v1",
    api_key="lm-studio",
    model_name="zai-org/glm-4.6v-flash",
    image_dir="商化照片-202512",
    output_file="results.json",
    few_shot_file="project-1-at-2026-01-20-09-01-f1ed471e.json",
    model_list_file="型號表.txt",
    max_image_size=3072
)

processor.run(limit=None)  # 處理所有照片
```

### 單張處理

```python
result = processor.process_image("商化照片-202512/xxx.jpg")
print(result)
```

### 載入動態資料

```python
processor._load_dynamic_data()
```

### 儲存動態資料

```python
processor._save_dynamic_data()
```

---

## 附錄

### A. 輸出格式說明

#### CSV 格式

```csv
timestamp,file_name,category,model,price,black_screen,raw_response
2025-01-22T10:30:45,xxx.jpg,單機,S27CG552EC,4990,false,處理成功
```

#### JSON 格式

```json
{
  "file_name": "xxx.jpg",
  "category": "單機",
  "model": "S27CG552EC",
  "price": "4990",
  "black_screen": false,
  "timestamp": "2025-01-22T10:30:45",
  "raw_response": "完整回應內容..."
}
```

### B. 分類定義

| 分類                            | 定義                    | 型號/價格 |
| ------------------------------- | ----------------------- | --------- |
| 單機                            | 單一產品的清晰照片      | 必須填寫  |
| 遠景                            | 超過 3 台產品的遠距照片 | 可為 null |
| 不合格-照片不清楚               | 無法清楚辨識            | 可為 null |
| 不合格-單機但看不清楚價格或型號 | 單機但資訊不足          | 可為 null |
| 失敗                            | 處理失敗                | null      |

### C. 型號表格式

每行一個型號，例如：

```
S24C310EAC
S27CG552EC
FollowMe M7 32"
```

### D. 系統版本

- 當前版本: `3.1.0 (ModelMatch + OpenAI)`
- 主要功能: OpenAI API 支援 + 型號智能校正 + 動態學習

### E. 支援的圖片格式

- JPG / JPEG
- PNG

### F. 聯絡與支援

- 專案位置: `D:\00_程式\20260120_商化自動OCR圖片`
- LM Studio 文件: https://lmstudio.ai/docs

---

## 快速參考

### 一鍵啟動 (推薦)

```bash
# 啟動處理引擎 + Web 儀表板
python samsung_ocr_batch_processor.py
```

### 查看即時進度

```bash
# 開啟瀏覽器，訪問 dashboard_v2.html
```

### 停止處理

```bash
# 按 Ctrl + C 停止
```

### 查看結果

```bash
# Excel 檔案
start results.xlsx

# CSV 檔案
start results.csv
```

---

**手冊版本**: 3.0 (v18.58 交接版)
**最後更新**: 2026-01-29  
**適用版本**: v18.58 (Prompt V2 + Fuzzy Logic Cleanup)

---

## 🚨 待解決問題 (交接給下一位 AI)

請下一位 AI 接手後優先處理以下問題：

### 1. 辨識準確度問題 (優先級: 高)

- **FollowMe 識別失敗**：`768.jpg` (TK3C-大里二) 應識別為 FollowMe (立架+托盤)，但目前誤判。
- **型號讀取錯誤**：
  - `302.jpg`: 應為 S27D300GAC
  - `424.jpg`: 應為 S27D300GNC
  - 請檢查 VLM 對這類標籤的辨識能力，或微調 Prompt。
- **價格混配**：`431.jpg` 抓到 5290，正確應為 4990 (需加強「同一張標籤」的約束)。

4. **型號未建檔問題**：`302.jpg` (S27D300GAC) 與 `424.jpg` (S27D300GNC) 等新出現型號。
   - **User 指示**：不直接修改 `型號表.txt`。
   - **解決方案**：實作「進階模糊匹配邏輯」：
     - **末碼容錯**：忽略最後一碼英文 (e.g., `GNC` vs `GAC` 可視為相同，或 `AC` 結尾忽略)。
     - **數字容錯**：中間數字允許 1 位誤差 (e.g., `300` vs `302` 若其他部分相符)。

### 2. 系統優化

- **日誌清理**：已移除大部分冗餘日誌，保留 Prompt 版本 (啟動時顯示)。
- **Prompt 優化**：已移除具體型號範例，改用 `S??XX???EC` 通配符。

---

## ⚠️ 開發者重要規則 (不可修改)

1. **絕對禁止壓縮圖片**：`ImageProcessor` 必須設定 `max_size = None`。壓縮會導致小字模糊無法辨識。
2. **Prompt 修改**：修改 `samsung_ocr_prompt.txt` 後，系統會自動重新讀取，無需重啟 (除非修改 Python 代碼)。
3. **型號表原則**：`型號表.txt` 是權威來源，不應隨意追加。應透過程式邏輯(模糊匹配)來適應 OCR 誤差。

---

## 訓練案例參考 (標準答案)

| 檔案    | 正確答案                       | 備註                    |
| ------- | ------------------------------ | ----------------------- |
| 412.jpg | 單機 / S24F332EAC / 2390       | 原圖清晰                |
| 417.jpg | 單機 / FollowMe M7 32" / 12990 | 立架+托盤               |
| 768.jpg | 單機 / FollowMe M7 32" / 12990 | 誤判為遠景(待修)        |
| 302.jpg | 單機 / S27D300GAC / 3490       | 誤判為 null(待修)       |
| 431.jpg | 單機 / S27CG552EC / 4990       | 誤判為 5290(待修)       |
| 424.jpg | 單機 / S27D300GNC / 3090       | 誤判為 S27AG500NC(待修) |
| 852.jpg | 單機 / S27CG552EC / 4990       | 正確                    |

_註：本手冊由 AI 助理 Antigravity 更新於 2026-01-29_

# 三星通路照片 OCR 批次辨識系統 - 完整操作手冊

## 📋 目錄
1. [專案概覽](#專案概覽)
2. [系統需求](#系統需求)
3. [安裝設定](#安裝設定)
4. [檔案結構](#檔案結構)
5. [快速開始](#快速開始)
6. [詳細操作說明](#詳細操作說明)
7. [Web 儀表板](#web-儀表板)
8. [進階功能](#進階功能)
9. [故障排除](#故障排除)
10. [API 文件](#api-文件)
11. [附錄](#附錄)

---

## 專案概覽

### 專案目標
使用本地視覺語言模型 (VLM) 批次處理三星通路照片，自動辨識並提取：
- 照片分類（單機/遠景/不合格）
- 螢幕型號
- 價格
- 黑屏狀態

### 技術架構
```
┌─────────────────┐
│   LM Studio    │ (GLM-4.6V-Flash-Q4_K_M)
│  Local Server  │ (http://localhost:1234)
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────┐
│  samsung_ocr_batch_processor.py │ (Python + Flask)
│  - OCR 處理引擎                 │
│  - API Server (Port 5000)       │
└────────┬────────────────────────┘
         │
         ▼
┌─────────────────────────────────┐
│  dashboard_v2.html              │ (Web UI)
│  - 即時監控                      │
│  - 人工審核                      │
│  - 反饋學習                      │
└─────────────────────────────────┘
```

### 系統特性
- ✅ 批次處理，支援斷點續傳
- ✅ 即時 Web 儀表板監控
- ✅ 人工審核與反饋學習 (HITL)
- ✅ 型號智能校正與驗證
- ✅ 動態 Few-Shot 學習
- ✅ 自動匯出 CSV/Excel

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

# 在瀏覽器開啟儀表板
# 開啟 dashboard_v2.html
```

---

## 詳細操作說明

### 參數說明

| 參數 | 預設值 | 說明 |
|------|--------|------|
| `--images` | `./photos` | 照片目錄路徑 |
| `--output` | `results.json` | 輸出檔案名稱 |
| `--max_size` | `3072` | 圖片最大尺寸 (像素) |
| `--few_shot` | `""` | Few-shot 訓練資料路徑 |
| `--model` | `zai-org/glm-4.6v-flash` | 模型名稱 |
| `--model_list` | `型號表.txt` | 標準型號表路徑 |
| `--api_base` | `http://localhost:1234/v1` | LM Studio API 端點 |

### 處理流程

1. **初始化階段**
   - 檢查 LM Studio API 連線
   - 載入標準型號表
   - 載入訓練資料 (若有)
   - 載入動態學習資料

2. **批次處理階段**
   - 掃描照片目錄
   - 檢查已處理檔案 (斷點續傳)
   - 逐一處理照片

3. **OCR 辨識階段**
   - 圖片編碼 (Base64)
   - 構建 Prompt (System + User)
   - 呼叫 LM Studio API
   - 解析 JSON 回應
   - 型號校正與驗證
   - 儲存結果

4. **結果匯出**
   - 即時寫入 CSV
   - 完成後匯出 Excel
   - 儲存 JSON 原始資料

---

## Web 儀表板

### 啟動方式
```bash
# 終端 1: 啟動處理引擎
python samsung_ocr_batch_processor.py

# 終端 2: 啟動 Web 伺服器 (若需要單獨啟動)
# Flask API 自動在背景運行於 Port 5000

# 瀏覽器: 開啟 dashboard_v2.html
```

### 儀表板功能

1. **即時監控區域**
   - 顯示當前處理檔案
   - 即時縮圖預覽
   - 處理進度條
   - 統計資訊 (成功/失敗數)

2. **歷史結果區域**
   - 顯示最近 50 筆結果
   - 每筆結果包含縮圖
   - 顯示分類、型號、價格

3. **系統日誌區域**
   - 顯示處理日誌
   - 模型思考過程
   - 錯誤訊息

4. **人工審核區域**
   - 檢視辨識結果
   - 提供反饋 (正確/錯誤)
   - 提交修正資料
   - 重新排隊重試

### API 端點

#### GET `/api/status`
回傳當前處理狀態
```json
{
  "version": "3.1.0 (ModelMatch + OpenAI)",
  "current_file": "M-台中市-大甲區-SF-大甲-409.jpg",
  "current_thumb": "data:image/jpeg;base64,...",
  "stats": {
    "total": 1000,
    "processed": 450,
    "success": 420,
    "failed": 30,
    "is_running": true
  },
  "recent_results": [...],
  "sys": {"cpu": 45, "mem": 62},
  "lm_logs": [...]
}
```

#### POST `/api/feedback`
提交反饋資料
```json
{
  "file_name": "M-台中市-大甲區-SF-大甲-409.jpg",
  "is_correct": false,
  "correct_data": {
    "category": "遠景",
    "model": null,
    "price": null,
    "black_screen": false
  },
  "reason": "這很明顯是遠景"
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

#### 1. API 連線失敗
**症狀**: `[Error] API 連線失敗`

**解決方案**:
- 檢查 LM Studio 是否正在運行
- 確認 API 端點為 `http://localhost:1234/v1`
- 確認模型已載入
- 檢查防火牆設定

#### 2. VRAM 不足
**症狀**: 處理過程中程式崩潰

**解決方案**:
- 降低 GPU Layers (如 24/40)
- 降低圖片尺寸 (`--max_size 2048`)
- 關閉其他佔用 GPU 的程式

#### 3. JSON 解析失敗
**症狀**: `[解析失敗] 找到 JSON 但解析錯誤`

**解決方案**:
- 系統會自動重試 3 次
- 調整 temperature 參數 (0.1-0.3)
- 檢查模型回應格式

#### 4. 型號識別錯誤
**症狀**: 型號不在標準表中

**解決方案**:
- 更新型號表 (`型號表.txt`)
- 透過 Web 儀表板提交正確答案
- 系統會學習並校正

#### 5. 處理速度過慢
**症狀**: 每張照片超過 60 秒

**解決方案**:
- 降低圖片尺寸 (`--max_size 2048`)
- 確保 GPU 加速啟用
- 關閉不必要的後臺程式

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

| 分類 | 定義 | 型號/價格 |
|------|------|----------|
| 單機 | 單一產品的清晰照片 | 必須填寫 |
| 遠景 | 超過 3 台產品的遠距照片 | 可為 null |
| 不合格-照片不清楚 | 無法清楚辨識 | 可為 null |
| 不合格-單機但看不清楚價格或型號 | 單機但資訊不足 | 可為 null |
| 失敗 | 處理失敗 | null |

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

**手冊版本**: 1.0  
**最後更新**: 2025-01-22  
**適用版本**: 3.1.0 (ModelMatch + OpenAI)

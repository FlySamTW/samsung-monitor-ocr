# 三星通路照片 OCR 自動辨識系統

> 使用 Qwen3-VL 視覺模型自動批次辨識三星螢幕照片的型號與價格

## 🚀 快速開始

### 1. 啟動系統
雙擊執行：run_ocr.bat

系統會自動：
- 清理快取
- 啟動 OCR 伺服器 (Port 5000)
- 開啟 Dashboard (http://localhost:5000)

### 2. 使用 Dashboard
1. 選擇照片資料夾（如：商化照片-202512）
2. 點擊「開始執行」
3. 即時監控辨識進度

---

## 📂 核心檔案

| 檔案 | 說明 |
|------|------|
| run_ocr.bat | **唯一啟動腳本** |
| samsung_ocr_batch_processor.py | 主程式 (v18.75) |
| samsung_ocr_prompt.txt | OCR Prompt |
| 型號表.txt | 型號清單 |
| skills/ | 功能模組 |
| dashboard/ | Web 介面 |

---

## 🔧 常見問題

**Q: 如何更新 Prompt？**
直接編輯 samsung_ocr_prompt.txt，然後重啟 run_ocr.bat

**Q: Port 5000 被佔用？**
run_ocr.bat 會自動清理

**Q: 系統不穩定？**
執行 run_ocr.bat 會自動清理快取

---

版本：v18.75 (PromptManager 配置管理系統)
更新：2026-01-30

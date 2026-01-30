# 🔧 配置管理系統修復 - 快速指南

## 📋 已完成的修復

### 1. 程式碼修改
✅ **samsung_ocr_batch_processor.py v18.75**
- Line 311-328：改用 `prompt_mgr.get_system_prompt()`
- Line 1350-1363：啟動時顯示 Prompt 版本資訊
- 不再硬編碼 `'samsung_ocr_prompt.txt'` 檔名

### 2. 新增工具
✅ **migrate_prompt_to_bundle.py**
- 自動將 txt 檔案遷移到 Bundle 系統
- 自動備份舊檔案到 `backup/` 資料夾
- 建立版本化的 JSON bundle

✅ **deploy_prompt.bat**
- 一鍵部署腳本
- 自動停止伺服器 → 遷移 → 清快取 → 重啟

### 3. 文檔
✅ **CONFIG_MANAGEMENT_DIAGNOSIS.md**
- 完整的問題診斷
- 業界最佳實踐參考
- 詳細的解決方案說明

---

## 🚀 立即執行步驟

### 方案 A：快速驗證（手動）

```powershell
# 1. 停止現有伺服器
Get-Process -Name python -ErrorAction SilentlyContinue | Stop-Process -Force

# 2. 清除快取
Remove-Item -Recurse -Force __pycache__, skills\__pycache__ -ErrorAction SilentlyContinue

# 3. 重啟伺服器（會自動使用新的 PromptManager）
python samsung_ocr_batch_processor.py --server
```

**預期輸出**：
```
📝 Prompt Version: prompt_v2.0_20260130_123456
   Created: 2026-01-30T12:34:56
Samsung OCR Batch System v18.75 (PromptManager 配置管理系統)
```

### 方案 B：完整部署（自動化）

```batch
# 執行部署腳本
deploy_prompt.bat
```

這個腳本會：
1. ✅ 停止伺服器
2. ✅ 備份所有 prompt txt 檔案
3. ✅ 遷移到 Bundle 系統
4. ✅ 清除快取
5. ✅ 重啟伺服器

---

## 🔍 驗證修復是否成功

### 檢查點 1：啟動訊息
伺服器啟動時應該看到：
```
📝 Prompt Version: prompt_v2.0_YYYYMMDD_HHMMSS
   Created: 2026-01-30T...
Samsung OCR Batch System v18.75 (PromptManager 配置管理系統)
```

### 檢查點 2：Bundle 檔案
檢查是否存在：
```
assets/prompt_bundles/
  prompt_v2.0_20260130_123456.json  ✅ 新建立的 bundle
  latest.json                        ✅ 指標檔案
```

### 檢查點 3：備份檔案
檢查是否備份：
```
backup/
  samsung_ocr_prompt_20260130_123456.txt
  samsung_ocr_prompt_v2_20260130_123456.txt
```

### 檢查點 4：重新測試 966.jpg
1. 在 Dashboard 選擇 `M-新北市-三峽區-TK3C-三峽大學-966.jpg`
2. 點擊「重新 OCR」
3. 預期結果：
   - 型號：`S24D300GAC`
   - 價格：`2990`
   - 不再是「照片不清楚」

---

## 🎯 為什麼這次能徹底解決？

### 之前的問題
```
❌ 硬編碼 prompt_file = 'samsung_ocr_prompt.txt'
❌ 手動複製 v2 → 主檔案
❌ 不知道伺服器用哪個版本
❌ BAT 腳本只能清快取
```

### 現在的架構
```
✅ 使用 PromptManager 動態載入
✅ 版本化 Bundle 系統（JSON）
✅ 啟動時顯示版本號
✅ deploy_prompt.bat 自動化部署
✅ 備份機制
```

---

## 📚 未來如何更新 Prompt？

### 舊方法（已淘汰）
```
1. 手動編輯 samsung_ocr_prompt.txt
2. 手動備份成 _v2.txt
3. 重啟伺服器
4. 不知道是否生效
```

### 新方法（推薦）

#### 方法 1：直接修改 Bundle（進階）
```python
# 1. 修改 Bundle JSON
assets/prompt_bundles/prompt_v2.0_xxx.json

# 2. 執行部署
deploy_prompt.bat
```

#### 方法 2：修改 txt 後遷移（簡單）
```batch
# 1. 編輯 samsung_ocr_prompt_v2.txt
notepad samsung_ocr_prompt_v2.txt

# 2. 執行遷移
python migrate_prompt_to_bundle.py

# 3. 重啟
run_ocr.bat
```

#### 方法 3：使用 PromptManager API（自動化）
```python
from skills.prompt_versioning import PromptManager

pm = PromptManager("assets")

# 讀取現有 Bundle
bundle = pm.get_prompt_bundle()

# 修改 System Prompt
bundle['system_prompt'] = "新的 Prompt 內容..."

# 儲存新版本
pm.save_bundle(bundle, "prompt_v2.1_manual")
```

---

## ⚠️ 注意事項

### 相容性
- 修改後的程式碼會先嘗試從 PromptManager 載入
- 如果 Bundle 為空，會 fallback 到 `samsung_ocr_prompt.txt`
- 完全向下相容

### 備份策略
- 所有舊檔案會自動備份到 `backup/` 資料夾
- Bundle 系統保留所有歷史版本
- 可隨時回滾到任何版本

### 疑難排解
**Q: 啟動時看不到 Prompt Version？**
```
→ 執行 python migrate_prompt_to_bundle.py
→ 會自動建立 Bundle
```

**Q: 修改 txt 檔案後沒生效？**
```
→ 需要執行遷移：python migrate_prompt_to_bundle.py
→ 或使用 deploy_prompt.bat
```

**Q: 想回滾到舊版本？**
```python
# 查看所有版本
ls assets/prompt_bundles/

# 載入特定版本
pm.load_active_bundle("prompt_v1.0_20260128_xxx")
```

---

## 📞 後續支援

如果遇到問題：
1. 檢查 `backup/` 資料夾是否有備份
2. 查看啟動日誌中的 Prompt 版本號
3. 執行 `deploy_prompt.bat` 重新部署
4. 參考 `CONFIG_MANAGEMENT_DIAGNOSIS.md` 完整診斷

---

**更新時間**：2026-01-30  
**版本**：v18.75  
**狀態**：已修復，待測試

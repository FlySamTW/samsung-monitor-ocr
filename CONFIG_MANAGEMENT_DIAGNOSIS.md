# 🔴 配置管理根本問題診斷與解決方案

## 問題現況

### 發生的慘案
- **Prompt v2.0** 被存到 `samsung_ocr_prompt_v2.txt`
- 但伺服器硬編碼讀取 `samsung_ocr_prompt.txt`（舊版）
- 結果：更新後的 prompt 完全沒被使用，導致 966.jpg 辨識錯誤

### 為什麼 BAT 腳本還不夠？

即使 `run_ocr.bat` 有暴力清快取機制，但**無法解決配置檔案版本不同步**問題：

```bat
# run_ocr.bat 做了：
✅ 清除 __pycache__
✅ 殺死 Python/Node 進程
✅ 釋放 Port 5000

❌ 但沒有做：配置檔案版本控制與自動部署
```

---

## 根本原因：缺乏「單一真相來源」(Single Source of Truth)

### 現有架構的問題

```
❌ 錯誤的檔案結構：
samsung_ocr_prompt.txt         ← 伺服器讀這個（硬編碼）
samsung_ocr_prompt_v2.txt      ← 新版在這裡，但沒人用
samsung_ocr_prompt_v1_backup.txt  ← 備份在這裡

❌ 有 PromptManager 但沒被使用：
- skills/prompt_versioning.py 有完整的版本管理
- 但 samsung_ocr_batch_processor.py line 312 硬編碼檔名
- PromptManager 形同虛設
```

---

## 🔥 業界最佳實踐（來自 12 Factor App + DevOps 社群）

### 1. **Single Source of Truth (SSOT)**
```
配置檔案應該有唯一的「主版本」
- 不應該有 v1, v2, v3 多個檔案共存
- 版本號應該在「內容中」或「資料庫中」，不是檔案名稱中
```

### 2. **版本化配置 (Versioned Config)**
```
✅ 好的做法：
assets/prompt_bundles/
  prompt_v20260130_190426.json   ← 時間戳
  prompt_v20260129_153324.json   ← 歷史版本
  latest.json                     ← 符號連結或指標檔案

❌ 壞的做法：
samsung_ocr_prompt.txt
samsung_ocr_prompt_v2.txt        ← 多個檔案共存
samsung_ocr_prompt_v1_backup.txt
```

### 3. **環境變數 + 配置載入器**
```python
# 從環境變數決定配置來源
PROMPT_VERSION = os.getenv("PROMPT_VERSION", "latest")
prompt_mgr.load_active_bundle(PROMPT_VERSION)
```

### 4. **自動化部署腳本**
```bat
# deploy_prompt.bat
echo "部署 Prompt 新版本..."
python deploy_prompt.py --version v2.0
# 自動：複製、備份、重啟服務
```

---

## 🛠️ 徹底解決方案：三步驟改造

### 步驟 1：啟用 PromptManager（程式碼修改）

**問題所在**：`samsung_ocr_batch_processor.py` line 312 硬編碼
```python
# ❌ 現狀：硬編碼檔名
prompt_file = 'samsung_ocr_prompt.txt'
with open(prompt_file, 'r', encoding='utf-8') as f:
    prompt_template = f.read()
```

**修改為**：使用 PromptManager
```python
# ✅ 應該這樣：
prompt_template = prompt_mgr.get_system_prompt()
# 自動從 assets/prompt_bundles/latest.json 讀取
```

### 步驟 2：建立 Prompt 遷移腳本

創建 `migrate_prompt_to_bundle.py`：
```python
from skills.prompt_versioning import PromptManager
import shutil

def migrate_to_bundle_system():
    """將現有 txt 檔案遷移到 bundle 系統"""
    pm = PromptManager("assets")
    
    # 讀取當前主檔案
    with open("samsung_ocr_prompt.txt", "r", encoding="utf-8") as f:
        current_prompt = f.read()
    
    # 建立 bundle
    bundle_data = {
        "version_id": "prompt_v2.0_migrated",
        "system_prompt": current_prompt,
        "user_prompt_template": "請分析這張螢幕照片...",
        "few_shot_config": {"source": "dynamic", "k": 1},
        "parameters": {"temperature": 0.1}
    }
    
    # 儲存並標記為 latest
    version_id = pm.save_bundle(bundle_data)
    
    # 備份舊檔案
    shutil.copy("samsung_ocr_prompt.txt", 
                f"backup/samsung_ocr_prompt_{version_id}.txt")
    
    print(f"✅ 遷移完成：{version_id}")
```

### 步驟 3：建立部署腳本

創建 `deploy_prompt.bat`：
```bat
@echo off
echo ==========================================
echo    Prompt 部署工具 v1.0
echo ==========================================

echo [1/4] 停止伺服器...
taskkill /F /IM python.exe /T 2>nul

echo [2/4] 遷移 Prompt 到 Bundle 系統...
python migrate_prompt_to_bundle.py

echo [3/4] 清除快取...
for /d /r . %%d in (__pycache__) do rmdir /s /q "%%d" 2>nul

echo [4/4] 重啟伺服器...
start powershell -NoExit -Command "cd '%CD%'; python samsung_ocr_batch_processor.py --server"

echo.
echo ✅ 部署完成！Prompt 已更新並重啟服務。
pause
```

---

## 🎯 立即執行檢查清單

### Phase 1：診斷（5分鐘）
- [ ] 確認 `skills/prompt_versioning.py` 存在
- [ ] 檢查 `assets/prompt_bundles/` 資料夾是否有內容
- [ ] 確認主程式是否真的使用 PromptManager

### Phase 2：快速修復（今天）
- [ ] 修改 `samsung_ocr_batch_processor.py` line 312-320
- [ ] 改為使用 `prompt_mgr.get_system_prompt()`
- [ ] 測試重啟後是否讀取正確版本

### Phase 3：系統改造（本週）
- [ ] 建立 `migrate_prompt_to_bundle.py`
- [ ] 建立 `deploy_prompt.bat`
- [ ] 將所有 prompt txt 檔案遷移到 bundle 系統
- [ ] 更新文檔與操作手冊

---

## 📊 效益分析

### 改造前
```
❌ 檔案混亂：v1, v2, backup 多個版本
❌ 人工複製貼上容易出錯
❌ 不知道伺服器用哪個版本
❌ BAT 腳本無法解決配置問題
```

### 改造後
```
✅ 單一來源：assets/prompt_bundles/latest.json
✅ 自動化部署：deploy_prompt.bat 一鍵完成
✅ 版本可追溯：所有歷史版本都保留
✅ 明確顯示：啟動時顯示 Prompt 版本號
```

---

## 🔗 參考資料

1. **12 Factor App - Config**  
   https://12factor.net/config  
   → "Store config in the environment"

2. **GitHub Actions 配置管理**  
   → 使用 secrets 與環境變數管理敏感配置

3. **Python ConfigParser + JSON**  
   → 業界標準：配置與程式碼分離

4. **Docker Compose + .env files**  
   → 環境變數 + 配置檔案分層管理

---

## 💡 給開發者的教訓

### 為什麼這次會失敗？

1. **寫了 PromptManager 但沒用**  
   → 建立基礎設施後沒有強制使用

2. **硬編碼檔名 = 技術債**  
   → Line 312 的硬編碼讓 PromptManager 形同虛設

3. **BAT 腳本只能清快取，不能管配置**  
   → 需要配合程式碼層級的修改

### 如何避免再次發生？

✅ **鐵律 1**：配置檔案路徑不應該硬編碼  
✅ **鐵律 2**：配置變更需要自動化腳本  
✅ **鐵律 3**：啟動時強制顯示配置版本號  
✅ **鐵律 4**：Code Review 時檢查硬編碼

---

## 🚀 下一步行動

1. **立即（今天）**：修改 line 312-320 使用 PromptManager
2. **今晚**：建立 `migrate_prompt_to_bundle.py` 和 `deploy_prompt.bat`
3. **明天**：測試完整的部署流程
4. **本週末**：更新操作手冊與 README

---

**建立時間**：2026-01-30  
**作者**：GitHub Copilot  
**狀態**：待執行

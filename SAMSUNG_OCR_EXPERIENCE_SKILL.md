---
description: Technial Rulebook & Post-Mortem for Samsung OCR Project
---

# SAMSUNG_OCR_EXPERIENCE (Project Bible)

**Purpose**: To document critical engineering failures and strict rules for future development, ensuring mistakes are never repeated.

## 🔄 最新改動日誌 (v18.99+)

### [2026-03-05] 日誌與結果列表去重修復

**問題**：UIÂ 中辨識紀錄區和日誌區出現重複內容
- 辨識記錄列表：首列和最後各重複顯示一筆
- 日誌區：同一段思考文字顯示兩次（含重複「思考:」標題）
- 獨白欄（串流緩衝）：顯示多餘「思考:」前綴

**修復**：
1. **[skills/batch_orchestrator.py L972]** — 移除多餘 `recent_results.append()`
   - 原因：`insert(0, ...)` 已經加入記錄，再 `append` 造成首尾各一筆
   - 修法：只保留 `insert(0, ...)` 邏輯，刪除後續的 `append`

2. **[samsung_ocr_batch_processor.py L1113]** — 移除重複的思考日誌輸出
   - 原因：前面驗證區已發出 `[THINK]` 標記的日誌，此處 `💭 思考:` 為冗餘
   - 修法：刪除 `orchestrator.log_system(f"💭 思考: {thinking_text}")` 行

3. **[samsung_ocr_batch_processor.py L527]** — 清除獨白欄的「思考:」前綴
   - 原因：模型串流原文含 `思考:` 前綴，獨白欄不需顯示標題
   - 修法：在設定 `stream_buffer` 前用 regex 去除 `^思考[:：]\s*`

**架構理念確認**：
- ✅ Prompt 集中在 `samsung_ocr_prompt.txt`，規範不硬寫
- ✅ 程式碼只負責執行與日誌，不修改系統邏輯
- ✅ LM Studio 設定全在 LM Studio 面板，無硬寫
- ✅ 模型自動檢測：啟動時從 API 讀取實際加載的模型

---

## ⚠️ CRITICAL ENGINEERING RULES (Blood Lessons)

### 1. Process Management (The "Zombie" Rule)

- **Failure**: Old Python processes lingered in background, causing code updates to be ignored.
- **Rule**: ALL startup scripts (`.bat`) MUST forcefully terminate old processes before starting.
- **Command**: `powershell "Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force"`
- **Never**: Rely on user to manually close windows.

### 2. Path Handling (The "Relative Path" Trap)

- **Failure**: Backend crashed because relative paths (`./images`) resolved incorrectly when run from different contexts (VS Code vs Terminal).
- **Rule**: ALWAYS use **Absolute Paths**.
- **Code**: `os.path.abspath(args.dir)` or `os.path.join(os.getcwd(), ...)`

### 3. Frontend/Backend Sync (The "Cache" Illusion)

- **Failure**: User saw old UI version because browser cached `index.html`.
- **Rule**: Backend MUST set `Cache-Control: no-store` for `index.html`.
- **Rule**: Frontend assets MUST be hashed (`index-Hash.js`).

### 4. JSON Serialization (The "datetime" Crash)

- **Failure**: API returned 500 Error because `datetime` objects are not JSON serializable by default.
- **Rule**: ALWAYS implement a `CustomJSONEncoder` in Flask to handle `datetime`, `decimal`, etc.

### 5. Windows Encoding (The "???" Corruption)

- **Failure**: Modifying files with PowerShell `Set-Content` without encoding flags corrupted JS files with UTF-16.
- **Rule**: When patching files via shell, ALWAYS specify encoding (e.g., `-Encoding UTF8`).
- **Better**: Use Python scripts for file manipulation, not shell one-liners.

---

## OCR Logic & Business Rules

### 1. View Type Definitions

- **Distant View (遠景)**: >3 monitors, no readable labels.
- **Single Unit (單機)**: Readable label OR single dominant monitor OR FollowMe stand.

### 2. FollowMe Identification

- **Physical**: White Stand + Round Base + Tray.
- **Pricing**: $9,900 (M5 32"), $12,900 (M7 32").

### 3. Quality Assurance

- **Model**: Must verify character-by-character.
- **Price**: Must be on SAME label. Must have comma/symbol.

---

**This file serves as the memory of the project. Read it before writing code.**

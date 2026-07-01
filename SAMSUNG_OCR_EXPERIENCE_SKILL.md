---
description: Technical Rulebook & Post-Mortem for Samsung OCR Project
---

# SAMSUNG_OCR_EXPERIENCE (Project Bible)

**Purpose**: To document critical engineering failures and strict rules for future development, ensuring mistakes are never repeated.

## 🔄 最新改動日誌 (v18.99+)

### [2026-06-30] 歷年接力與改名規格鎖定

**使用者已確認的邊界**：
- 可實作工具與文件，但不可直接開始跑全量歷年照片；全量執行要由使用者指定來源與輸出資料夾。
- 2K 指以 `2560` 長邊為基準；大於 2K 時長邊縮到 `2560`，短邊按原比例自然縮放，不裁切、不補白、不硬拉伸。
- `HEIC`、`WebP` 可以不處理；工具要列出略過，不可算進完成率。
- 改名後照片可以全部放同一層新資料夾，因為新檔名已包含年月。
- `去年以前不需要比價` 的工作解讀：以 2026 年執行時，`2025` 含以前不做 Samsung 官網價格比對。
- 當年度有官網比價時，檔名價格欄必須保留 `↑/↓/✓/？`，例如 `S27CG552EC-↑＄4990-1005.jpg`；歷史年度不比價時不加符號。
- `D:\00_歷年商化照片` 是這台電腦的外部照片資料位置，不是專案固定路徑；另一台電腦 pull 專案後可能使用不同照片根目錄。

**接力實作方向**：
1. 沿用現有 Dashboard / Flask 後端機制，不重寫 OCR 核心。
2. 自動化應呼叫 `/api/set_work_dir`、`/api/start_batch`、`/api/status`，讓一個資料匣跑完後自動切下一個。
3. 排序從最新月份往前。
4. 完成後用正式 `results.csv` 產改名計畫，再複製到同一層新輸出資料夾；不原地裸改照片。
5. 正式接力工具是 `tools/recursive_ocr_flat_export.py`，可由 `run_recursive_ocr_flat_export.bat` 啟動。
6. 另一台電腦上的 AI 接手執行時，先讀 `docs/ai_handoff_runbook.md`。
7. 輸出資料夾不可等於來源根資料夾，也不可放在來源根資料夾底下，避免重跑時掃到自己輸出的改名照片。
8. 接力器預設用 `_ocr_audit\folder_summary.csv` + `copied.csv` 續跑；已完整複製、來源照片數與最新修改時間未變、且目標檔案仍存在的資料夾標為 `skipped_existing`，避免中斷重跑時產生 `_2` 重複檔。
9. 接力器跑完後先用 `tools\recursive_ocr_audit_report.py --output-dir <輸出資料夾>` 驗收；通過才可回報全量完成，失敗時看 `_ocr_audit\audit_report.csv`。
10. 若使用者只說 `GIT`，也要同步本專案專屬 SKILL；本檔就是本專案優先更新的 SKILL。

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

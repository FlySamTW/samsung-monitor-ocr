# Windows PATH 環境變數問題 - 根本解決方案

## 問題診斷

當 BAT 檔案中執行命令如 `taskkill` 時，即使設定了 `set PATH=...`，系統仍可能找不到命令：

```bat
:: ❌ 這樣做不夠（即使設了 PATH）
taskkill /F /IM python.exe

:: ⚠️ 原因：BAT 中的 PATH 設定有三個問題
1. PATH 設定可能被子 Process 覆蓋
2. 命令解析順序與系統環境變數優先級不同
3. PowerShell 和 CMD 的 PATH 解析機制不同
```

## 解決方案

### 方案 A：使用完整路徑（推薦快速修復）

```bat
:: ✅ 直接使用完整路徑（最可靠）
C:\Windows\System32\taskkill.exe /F /IM python.exe /T

:: ✅ 系統目錄的標準路徑
System32 命令：C:\Windows\System32\{command}.exe
PowerShell：C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
WMI：C:\Windows\System32\wbem\wmic.exe
```

### 方案 B：使用 system_path.py（推薦長期方案）

`skills/system_path.py` 提供統一的 PATH 和命令執行機制：

```python
from skills.system_path import SystemPathResolver, ProcessManager

# 1️⃣ 獲取命令的正確路徑
taskkill_path = SystemPathResolver.get_command_path("taskkill")

# 2️⃣ 執行系統命令
rc, stdout, stderr = SystemPathResolver.execute_command("taskkill", ["/F", "/IM", "python.exe", "/T"])

# 3️⃣ 使用高層 API
ProcessManager.kill_process_by_name("python.exe")
ProcessManager.kill_process_by_port(5000)
ProcessManager.clear_cache_directories()

# 4️⃣ 設定 Python Process 的 PATH
SystemPathResolver.set_system_path_env()
```

## 為什麼 system_path.py 優於手動 PATH 設定

| 問題 | BAT 中手動設定 | system_path.py |
|------|---|---|
| 子 Process 覆蓋 PATH | ⚠️ 可能發生 | ✅ 自動修正 |
| 命令查找失敗 | ⚠️ 無備用方案 | ✅ 多層查找機制 |
| 跨 Shell 相容性 | ⚠️ CMD 和 PowerShell 不同 | ✅ 統一介面 |
| 可維護性 | ⚠️ 硬編碼路徑 | ✅ 集中管理 |
| 錯誤診斷 | ⚠️ 難以追蹤 | ✅ 詳細日誌 |

## run_ocr.bat 的新架構（v18.73）

```bat
[0/6] 驗證 PATH 環境 (Python + system_path.py)
 ↓ (備用：手動設定)
[1/6] 進程清理 (Python ProcessManager + 備用完整路徑)
 ↓ (備用：C:\Windows\System32\taskkill.exe)
[2/6] Port 解鎖 (Python 或 PowerShell 備用)
[3/6] 快取清理 (Python ProcessManager 或 BAT 備用)
[4/6] 啟動伺服器 (Python 核心)
```

### 關鍵特點

```bat
:: 雙層防禦：Python 優先 + 備用完整路徑
python -c "from skills.system_path import ProcessManager; ProcessManager.kill_process_by_name('python.exe')" 2>nul || (
    C:\Windows\System32\taskkill.exe /F /IM python.exe /T 2>nul
)
```

**優勢：**
- ✅ 優先使用高層 API（可維護、可靠）
- ✅ 備用完整路徑（100% 可用）
- ✅ 自動降級（一個失敗用另一個）

## 常見 Windows 系統命令路徑

| 命令 | 完整路徑 | 用途 |
|------|---------|------|
| `taskkill` | `C:\Windows\System32\taskkill.exe` | 殺死進程 |
| `powershell` | `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe` | 運行 PowerShell |
| `cmd` | `C:\Windows\System32\cmd.exe` | 運行 CMD |
| `wmic` | `C:\Windows\System32\wbem\wmic.exe` | WMI 查詢 |
| `netstat` | `C:\Windows\System32\netstat.exe` | 網絡狀態 |
| `ipconfig` | `C:\Windows\System32\ipconfig.exe` | IP 配置 |
| `systeminfo` | `C:\Windows\System32\systeminfo.exe` | 系統資訊 |

## 使用示例

### 示例 1：在 Python 中清理進程

```python
from skills.system_path import ProcessManager

# 殺死所有 Python 進程
status, _, _ = ProcessManager.kill_process_by_name("python.exe")
if status == 0:
    print("✓ 進程已清理")

# 清空 Port 5000
ProcessManager.kill_process_by_port(5000)

# 清除快取
ProcessManager.clear_cache_directories()
```

### 示例 2：在 BAT 中混合使用

```bat
:: 優先用 Python SKILL，失敗則用完整路徑
python -c "from skills.system_path import ensure_system_path; ensure_system_path()" 2>nul || (
    set "PATH=C:\Windows\System32;%PATH%"
)

:: 驗證命令存在
python -c "from skills.system_path import SystemPathResolver; assert SystemPathResolver.verify_command_exists('taskkill')" 2>nul || (
    echo ⚠️ taskkill 命令未找到
    exit /b 1
)
```

### 示例 3：查診系統 PATH

```python
from skills.system_path import SystemPathResolver

# 打印推薦的 PATH
print("推薦 PATH:")
for path in SystemPathResolver.get_system_path().split(";"):
    print(f"  - {path}")

# 驗證所有常見命令
for cmd in ["taskkill", "powershell", "netstat"]:
    path = SystemPathResolver.get_command_path(cmd)
    print(f"{'✓' if path else '✗'} {cmd} -> {path}")
```

## 測試 system_path.py

```bash
# 在專案根目錄執行
python -m skills.system_path

# 輸出示例：
# === 系統 PATH 檢查 ===
# 
# 📌 當前 PATH:
#   1. C:\Windows\System32
#   ...
#
# === 常見命令驗證 ===
# ✓ taskkill          -> C:\Windows\System32\taskkill.exe
# ✓ powershell        -> C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
# ✓ cmd               -> C:\Windows\System32\cmd.exe
# ✓ netstat           -> C:\Windows\System32\netstat.exe
```

## 總結：PATH 問題的根本原因與解決

| 層級 | 問題 | 解決方式 |
|-----|------|--------|
| BAT 層 | PATH 設定可能被覆蓋 | 使用完整路徑或 system_path.py |
| 子 Process 層 | 環境變數繼承混亂 | 通過 Python 統一管理 |
| 命令查找層 | 多個 Shell 機制不同 | 提供統一的 API |
| 維護層 | 硬編碼路徑難以改變 | 集中管理在 system_path.py |

**推薦流程：**
```
BAT 優先調用 Python → system_path.py 管理 PATH
           ↓ (備用)
       使用完整路徑
```

這樣既能享受高層 API 的便利，也能保證系統的可靠性。

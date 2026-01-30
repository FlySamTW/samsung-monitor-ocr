"""
系統 PATH 和命令執行的通用 SKILL
處理 Windows PATH 環境變數的複雜性，確保命令能正確執行

版本: v1.0
用途: 提供統一的命令執行方式，避免 PATH 問題
"""

import os
import subprocess
import shutil
from pathlib import Path
from typing import Optional, Tuple, List


class SystemPathResolver:
    """Windows PATH 環境變數解析器和命令執行器"""
    
    # Windows 系統目錄 (按優先順序)
    SYSTEM_DIRS = [
        r"C:\Windows\System32",      # 主系統目錄 (最優先)
        r"C:\Windows",               # 輔助系統目錄
        r"C:\Windows\System32\Wbem", # WMI 相關
        r"C:\Windows\System32\WindowsPowerShell\v1.0",  # PowerShell
    ]
    
    # 常見系統命令全路徑
    SYSTEM_COMMANDS = {
        "taskkill": r"C:\Windows\System32\taskkill.exe",
        "powershell": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "cmd": r"C:\Windows\System32\cmd.exe",
        "wmic": r"C:\Windows\System32\wbem\wmic.exe",
        "netstat": r"C:\Windows\System32\netstat.exe",
    }
    
    @classmethod
    def get_system_path(cls) -> str:
        """
        獲取正確的系統 PATH
        
        Returns:
            str: 正確的 PATH 字符串
        """
        # 構建正確的 PATH: System32 first (優先級最高)
        path_parts = cls.SYSTEM_DIRS.copy()
        
        # 添加現有 PATH (保留用戶自訂的)
        if "PATH" in os.environ:
            existing_path = os.environ["PATH"]
            # 過濾掉重複的
            for part in existing_path.split(os.pathsep):
                if part not in path_parts and part:
                    path_parts.append(part)
        
        return os.pathsep.join(path_parts)
    
    @classmethod
    def get_command_path(cls, command_name: str) -> Optional[str]:
        """
        獲取命令的完整路徑
        
        Args:
            command_name: 命令名稱 (如 'taskkill', 'powershell')
            
        Returns:
            str: 完整路徑，若找不到返回 None
        """
        # 1. 先查表 (預先定義的常見命令)
        if command_name.lower() in cls.SYSTEM_COMMANDS:
            full_path = cls.SYSTEM_COMMANDS[command_name.lower()]
            if Path(full_path).exists():
                return full_path
        
        # 2. 在系統目錄中搜尋
        for system_dir in cls.SYSTEM_DIRS:
            potential_path = Path(system_dir) / f"{command_name}.exe"
            if potential_path.exists():
                return str(potential_path)
        
        # 3. 使用 shutil.which 搜尋 (作為備份)
        found = shutil.which(command_name)
        if found:
            return found
        
        return None
    
    @classmethod
    def execute_command(
        cls,
        command_name: str,
        args: List[str] = None,
        use_shell: bool = False,
        check: bool = False,
    ) -> Tuple[int, str, str]:
        """
        執行系統命令 (使用完整路徑)
        
        Args:
            command_name: 命令名稱 (如 'taskkill')
            args: 命令參數列表
            use_shell: 是否使用 Shell 執行
            check: 是否檢查返回值
            
        Returns:
            Tuple[int, str, str]: (返回值, stdout, stderr)
        """
        command_path = cls.get_command_path(command_name)
        
        if not command_path:
            raise FileNotFoundError(f"無法找到命令: {command_name}")
        
        # 構建完整命令
        full_command = [command_path] + (args or [])
        
        try:
            result = subprocess.run(
                full_command,
                capture_output=True,
                text=True,
                shell=use_shell,
                timeout=30
            )
            
            return result.returncode, result.stdout, result.stderr
            
        except subprocess.TimeoutExpired:
            return -1, "", f"命令超時: {command_name}"
        except Exception as e:
            return -1, "", str(e)
    
    @classmethod
    def set_system_path_env(cls) -> None:
        """
        設定當前 Process 的 PATH 環境變數為正確值
        
        注意: 只影響當前 Python Process 及其子 Process
        """
        os.environ["PATH"] = cls.get_system_path()
    
    @classmethod
    def verify_command_exists(cls, command_name: str) -> bool:
        """
        驗證命令是否存在
        
        Args:
            command_name: 命令名稱
            
        Returns:
            bool: 命令是否存在
        """
        return cls.get_command_path(command_name) is not None


class ProcessManager:
    """進程管理工具 (使用正確的 PATH)"""
    
    @classmethod
    def kill_process_by_name(cls, process_name: str, force: bool = True) -> Tuple[int, str, str]:
        """
        根據進程名稱殺死進程
        
        Args:
            process_name: 進程名稱 (如 'python.exe')
            force: 是否強制殺死 (/F)
            
        Returns:
            Tuple[int, str, str]: (返回值, stdout, stderr)
        """
        args = ["/F", "/IM", process_name, "/T"] if force else ["/IM", process_name, "/T"]
        return SystemPathResolver.execute_command("taskkill", args)
    
    @classmethod
    def kill_process_by_port(cls, port: int) -> Tuple[int, str, str]:
        """
        根據 Port 殺死佔用該 Port 的進程
        
        Args:
            port: Port 號
            
        Returns:
            Tuple[int, str, str]: (返回值, stdout, stderr)
        """
        return SystemPathResolver.execute_command(
            "powershell",
            [
                "-NoProfile",
                "-Command",
                f"""
                $procs = Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | 
                         Select-Object -ExpandProperty OwningProcess -Unique;
                if ($procs) {{ 
                    foreach($pid in $procs) {{ 
                        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue 
                    }} 
                }}
                """
            ]
        )
    
    @classmethod
    def clear_cache_directories(cls) -> None:
        """清除 Python 快取目錄 (__pycache__)"""
        root_path = Path.cwd()
        
        for pycache in root_path.rglob("__pycache__"):
            try:
                if pycache.is_dir():
                    import shutil
                    shutil.rmtree(pycache, ignore_errors=True)
                    print(f"✓ 清除: {pycache}")
            except Exception as e:
                print(f"✗ 失敗: {pycache} - {e}")


# 便捷函數
def get_system_command(command_name: str) -> Optional[str]:
    """獲取系統命令的完整路徑"""
    return SystemPathResolver.get_command_path(command_name)


def ensure_system_path() -> None:
    """確保 PATH 環境變數正確設定"""
    SystemPathResolver.set_system_path_env()


def run_system_command(command_name: str, args: List[str] = None) -> Tuple[int, str, str]:
    """執行系統命令 (簡化版)"""
    return SystemPathResolver.execute_command(command_name, args)


def generate_bat_file(bat_path: str = None) -> bool:
    """
    生成正確編碼的 run_ocr.bat 檔案
    
    使用 UTF-8 with BOM 編碼，避免中文亂碼
    
    Args:
        bat_path: BAT 檔案路徑，默認為當前目錄的 run_ocr.bat
        
    Returns:
        bool: 是否成功
    """
    if bat_path is None:
        bat_path = Path.cwd() / "run_ocr.bat"
    
    bat_path = Path(bat_path)
    
    bat_content = """@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1

echo ==========================================
echo    Samsung OCR Launcher (v18.73 PATH修復版)
echo ==========================================
echo.

echo [0/6] 驗證 PATH 環境 (使用 system_path.py)...
python -c "from skills.system_path import ensure_system_path; ensure_system_path(); print('✓ PATH 已修正')" 2>nul || (
    echo ⚠️  無法導入 system_path，使用備用方案
    set "PATH=C:\\Windows\\System32;C:\\Windows;C:\\Windows\\System32\\Wbem;C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\;%PATH%"
)

echo.
echo [1/6] 執行暴力進程清理 (Aggressive Cleanup)...
python -c "from skills.system_path import ProcessManager; ProcessManager.kill_process_by_name('python.exe'); ProcessManager.kill_process_by_name('node.exe')" 2>nul || (
    echo [備用] 直接使用完整路徑
    C:\\Windows\\System32\\taskkill.exe /F /IM python.exe /T 2>nul
    C:\\Windows\\System32\\taskkill.exe /F /IM node.exe /T 2>nul
    C:\\Windows\\System32\\taskkill.exe /F /FI "WINDOWTITLE eq OCR Backend*" /T 2>nul
)

timeout /t 1 /nobreak >nul

python -c "from skills.system_path import ProcessManager; ProcessManager.kill_process_by_name('python.exe')" 2>nul || (
    C:\\Windows\\System32\\taskkill.exe /F /IM python.exe /T 2>nul
)

echo.
echo [2/6] 釋放 Port 5000 (使用 system_path.py)...
python -c "from skills.system_path import ProcessManager; ProcessManager.kill_process_by_port(5000); print('✓ Port 5000 已解鎖')" 2>nul || (
    echo [備用] 使用 PowerShell 解鎖
    C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe -NoProfile -Command "Write-Host '檢查 Port 5000...'; $procs = Get-NetTCPConnection -LocalPort 5000 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique; if ($procs) { foreach($pid in $procs) { Write-Host '>> 正在殺死 PID '$pid'...'; Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue } } else { Write-Host '>> Port 5000 乾淨無佔用。' }"
)

echo.
echo [3/6] 清除所有快取 (使用 system_path.py)...
python -c "from skills.system_path import ProcessManager; ProcessManager.clear_cache_directories(); print('✓ 快取清理完成')" 2>nul || (
    echo [備用] 使用 BAT 命令清理
    for /d /r . %%d in (__pycache__) do (
        if exist "%%d" (
            echo   清除: %%d
            rmdir /s /q "%%d" 2>nul
        )
    )
    for /r . %%f in (*.pyc) do (
        if exist "%%f" (
            echo   刪除: %%f
            del /f /q "%%f" 2>nul
        )
    )
)

echo.
echo [4/6] 啟動核心引擎 (Auto-Load Latest v18.73)...
cd /d "%~dp0"

set PYTHONDONTWRITEBYTECODE=1
set PYTHONUNBUFFERED=1

title OCR Backend Server

echo.
echo [5/6] 準備開啟控制面板 (8秒後)...
start /min "" cmd /c "timeout /t 8 /nobreak >nul && start http://localhost:5000"

echo.
echo [6/6] 核心啟動序列
echo ---------------------------------------------------
echo  ✓ PATH 已驗證
echo  ✓ 進程已清理
echo  ✓ Port 已解鎖
echo  ✓ 快取已清除
echo  ✓ 準備啟動伺服器...
echo ---------------------------------------------------
echo.
python samsung_ocr_batch_processor.py --model qwen/qwen3-vl-4b --api_base http://192.168.0.234:1234/v1

echo.
echo 伺服器已停止。
pause
"""
    
    try:
        # 寫入為 UTF-8 with BOM (Windows BAT 標準編碼)
        with open(bat_path, 'w', encoding='utf-8-sig', newline='\r\n') as f:
            f.write(bat_content)
        print(f"✓ 成功生成 {bat_path.name}")
        return True
    except Exception as e:
        print(f"✗ 生成失敗: {e}")
        return False


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "fix-bat":
        # 生成 BAT 檔案模式
        print("生成正確編碼的 run_ocr.bat...")
        success = generate_bat_file()
        sys.exit(0 if success else 1)
    
    print("=== 系統 PATH 檢查 ===\n")
    
    # 打印現有 PATH
    print("📌 當前 PATH:")
    for i, p in enumerate(os.environ.get("PATH", "").split(os.pathsep)[:5], 1):
        print(f"  {i}. {p}")
    
    print("\n📌 推薦的正確 PATH:")
    for i, p in enumerate(SystemPathResolver.get_system_path().split(os.pathsep)[:5], 1):
        print(f"  {i}. {p}")
    
    print("\n=== 常見命令驗證 ===\n")
    for cmd in ["taskkill", "powershell", "cmd", "netstat"]:
        path = SystemPathResolver.get_command_path(cmd)
        status = "✓" if path else "✗"
        print(f"{status} {cmd:15} -> {path or '找不到'}")
    
    print("\n=== 測試命令執行 ===\n")
    rc, stdout, stderr = ProcessManager.kill_process_by_name("python.exe")
    print(f"taskkill /F /IM python.exe /T")
    print(f"  返回值: {rc}")
    if stdout:
        print(f"  輸出: {stdout[:100]}")
    if stderr:
        print(f"  錯誤: {stderr[:100]}")

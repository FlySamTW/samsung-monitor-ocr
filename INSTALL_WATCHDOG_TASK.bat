@echo off
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PATH=C:\Windows\System32;C:\Windows;C:\Windows\System32\Wbem;C:\Windows\System32\WindowsPowerShell\v1.0\;%PATH%"
cd /d "%~dp0"

if exist "user_settings.cmd" call "user_settings.cmd"

if "%OCR_SOURCE_ROOT%"=="" (
  echo OCR_SOURCE_ROOT is not set. Please edit user_settings.cmd first.
  pause
  exit /b 1
)

if "%OCR_OUTPUT_DIR%"=="" (
  echo OCR_OUTPUT_DIR is not set. Please edit user_settings.cmd first.
  pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\install_watchdog_task.ps1" -RepoRoot "%~dp0" -SourceRoot "%OCR_SOURCE_ROOT%" -OutputDir "%OCR_OUTPUT_DIR%" -IntervalHours 4
set "EXITCODE=%ERRORLEVEL%"
echo.
if "%EXITCODE%"=="0" (
  echo Watchdog task installed. It will check OCR and upload every 4 hours.
) else (
  echo Failed to install watchdog task.
)
echo.
pause
exit /b %EXITCODE%

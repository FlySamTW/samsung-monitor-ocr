@echo off
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PATH=C:\Windows\System32;C:\Windows;C:\Windows\System32\Wbem;C:\Windows\System32\WindowsPowerShell\v1.0\;%PATH%"
cd /d "%~dp0"

if exist "user_settings.cmd" call "user_settings.cmd"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\windows_user_launcher.ps1" start
set "EXITCODE=%ERRORLEVEL%"
echo.
if not "%EXITCODE%"=="0" (
  echo Startup failed. Please read the message above.
  pause
)
exit /b %EXITCODE%

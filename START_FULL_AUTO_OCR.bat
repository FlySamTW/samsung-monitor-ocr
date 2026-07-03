@echo off
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PATH=C:\Windows\System32;C:\Windows;C:\Windows\System32\Wbem;C:\Windows\System32\WindowsPowerShell\v1.0\;%PATH%"
cd /d "%~dp0"

if exist "user_settings.cmd" call "user_settings.cmd"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\windows_user_launcher.ps1" recursive
set "EXITCODE=%ERRORLEVEL%"
echo.
if "%EXITCODE%"=="0" (
  echo Full auto OCR finished.
) else (
  echo Full auto OCR stopped or needs attention. Please read the message above.
)
echo.
pause
exit /b %EXITCODE%

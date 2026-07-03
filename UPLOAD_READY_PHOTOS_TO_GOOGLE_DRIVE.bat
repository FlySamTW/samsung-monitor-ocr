@echo off
setlocal
cd /d "%~dp0"
set PY=.venv\Scripts\python.exe
if not exist "%PY%" set PY=python
"%PY%" tools\rclone_drive_upload.py --execute --repeat --limit 500 --transfers 4 --checkers 8
pause

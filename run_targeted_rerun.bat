@echo off
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "RICH_NO_COLOR=1"
set "TERM=dumb"
cd /d "D:\00_商化\samsung-monitor-ocr"
.venv\Scripts\pythonw.exe samsung_ocr_batch_processor.py --api_base http://127.0.0.1:1234/v1 --api_key lm-studio --model qwen/qwen3-vl-8b --dir targeted_rerun_2026_temp --no_followme_auto_update > targeted_rerun_stdout.log 2> targeted_rerun_stderr.log

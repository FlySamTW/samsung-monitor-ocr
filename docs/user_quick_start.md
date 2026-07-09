# 一般使用者快速啟動

這份專案現在可以不用 Codex、不用手打 Python 指令；一般 Windows 使用者照順序雙擊 BAT 即可。

## 第一次使用

1. 安裝並開啟 LM Studio。
2. 在 LM Studio 下載並載入 `qwen/qwen3-vl-8b`。
3. 雙擊 `SETUP_FIRST_TIME.bat`。
4. 設定完成後，雙擊 `START_OCR.bat`。

`SETUP_FIRST_TIME.bat` 會自動建立 `.venv`、安裝 Python 套件，並確認 dashboard 成品存在。若電腦沒有 Python，它會提示安裝 Python 3.11。

## 平常使用

- `START_OCR.bat`: 啟動 OCR dashboard，開啟 `http://127.0.0.1:5000/`。
- `START_FULL_AUTO_OCR.bat`: 從來源資料夾往下遞迴處理，輸出到平面 OCR 照片資料夾。
- `CHECK_STATUS.bat`: 查看後端、目前資料夾、目前檔案、成功/失敗數。
- `INSTALL_WATCHDOG_TASK.bat`: 安裝每 4 小時自動檢查/續跑 OCR 與上傳的 Windows 工作排程。

`INSTALL_WATCHDOG_TASK.bat` 會建立 `SamsungOCR_PipelineWatchdog`。它只會補起已停止的 OCR 接力器、可疑照片重跑等待器、Google Drive 上傳器，並保留已完成資料；它不會清空歷史，也不會用 restart/no-resume。

## 修改來源與輸出路徑

第一次執行時會自動建立 `user_settings.cmd`。一般使用者只需要用記事本改這兩行：

```bat
set OCR_SOURCE_ROOT=D:\00_商化\00_未整理商化照片
set OCR_OUTPUT_DIR=D:\00_商化\00_已OCR照片
```

其他常用設定：

```bat
set LOCAL_LLM_MODEL=qwen/qwen3-vl-8b
set LOCAL_LLM_CONTEXT_LENGTH=16384
```

`LOCAL_LLM_CONTEXT_LENGTH=16384` 是給 RTX 3060 等中階顯卡較穩的預設值。高階顯卡才建議自行提高。

## 不需要 Node.js

`dashboard/dist` 已納入專案，沒有 Node.js 的使用者也能直接開 dashboard。只有開發者修改 `dashboard/src` 時才需要重新 `npm run build`。

## 常見狀況

- 顯示 LM Studio not ready：請打開 LM Studio、啟用 Local Server，並載入 `qwen/qwen3-vl-8b`。
- dashboard 打不開：先執行 `CHECK_STATUS.bat`，確認後端是否正在跑。
- 想換照片資料夾：改 `user_settings.cmd` 的 `OCR_SOURCE_ROOT`。
- 想換輸出資料夾：改 `user_settings.cmd` 的 `OCR_OUTPUT_DIR`。
- 不要把正式照片資料夾放進 Git；專案只保留 `samples/ocr_demo_50` 作為範例。

## 介面進度怎麼看

- 上方 `總進度` 是所有已發現來源資料夾的總 OCR 進度，不只是目前資料夾。
- `本資料夾` 是現在正在跑的單一資料夾進度。
- 右側 `辨識紀錄` 最上方若顯示 `處理中 / AI 即時判讀中`，代表它正在配合左側同一張照片播放 AI 判讀內容；完成後才會顯示型號、價格與狀態。
- 左側 AI 判讀區若顯示 `上一張摘要保留中 · 下一張判讀中`，代表系統正在準備下一段判讀內容；這是為了避免監看畫面中途變黑或空白。
- 若 OCR 很快，介面會略過太舊的展示項來追上最新照片，但不會刪除 OCR 結果或輸出檔案。
- 上傳 Google Drive 的進度不放在主介面；需要時查看 `D:\00_商化\00_已OCR照片\_drive_upload\drive_upload_summary.json`。
## Pause Handoff Note

If this project is handed to another AI after 2026-07-09, do not press a restart button or resume old-year OCR first. Read `docs\handoff_20260709_pause.md`, then continue the 2026 pass3 rerun before returning to older years. Upload only rows marked ready by the Drive manifest; review-required rows must stay local until rerun or manual correction clears them.

## Dashboard viewing rule

During a live run, the main screen is intentionally staged for viewers: photo first, AI judgment text always visible, then the right-side result is revealed. If the system is already judging the next photo, the AI area may hold the previous summary with a status label instead of going blank.

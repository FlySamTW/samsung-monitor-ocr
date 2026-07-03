# 2026 OCR Resume Handoff

Last updated: 2026-07-03

## Current Goal

Continue the Samsung monitor OCR workflow on another PC without uploading the production photo library.

Primary paths used on the main machine:

- Source root: `D:\00_商化\00_未整理商化照片`
- Flat output: `D:\00_商化\00_已OCR照片`
- Repo: `D:\00_商化\samsung-monitor-ocr`
- Backend: `http://127.0.0.1:5000`
- LM Studio OpenAI-compatible endpoint: `http://127.0.0.1:1234/v1`
- Preferred model: `qwen/qwen3-vl-8b`

## Portable Demo Data

Only the curated demo set is committed:

- Photos: `samples/ocr_demo_50/photos`
- Labels: `samples/ocr_demo_50/labels.json`

Do not commit the full production photo folders, generated flat output, audit backups, logs, or temporary rerun CSVs.

Smoke command:

```powershell
python samsung_ocr_batch_processor.py --api_base http://127.0.0.1:1234/v1 --api_key lm-studio --model qwen/qwen3-vl-8b --dir samples\ocr_demo_50\photos --no_followme_auto_update
```

## Business Rules To Preserve

- For folders named 2026 or later, compare store price with Samsung official price or PChome 24h fallback.
- Filename price marker must be one of `↑`, `↓`, `✓`, `？` for 2026+ comparable records.
- 2025 and older folders do not need official/PChome comparison markers.
- Do not output `停產`.
- If 2026+ official/PChome price cannot be found, write review CSV or stop for review instead of silently finalizing bad data.
- Taiwan wording in prompts/UI should say `3C賣場`, `賣場`, or `門市`, not `電器行`.
- `遠景` output filenames should not include model or price.
- A clear side spec label overrides weaker visual hints. FollowMe 4K/32 side label plus 12,900 or 12,990 usually means `FollowMe M7 32"` unless Pro/43 evidence is explicit.

## UI Fix State

The dashboard was changed so boss-facing UI stays presentable:

- Right panel title is `辨識紀錄`, not internal queue wording.
- Broken thumbnails render as a clean placeholder.
- Main preview no longer enlarges `thumb_b64`; it shows `照片載入中` until a full image loads.
- New backend results include `source_path` so cross-folder queue items can load the full source image.

Important: after pulling these changes on a running machine, restart the backend process so `skills/batch_orchestrator.py` can attach `source_path` to new results.

## Resume Rerun Workflow

The targeted cleanup tool is:

```powershell
python tools\rerun_questionable_records.py --include-older --execute --output-csv questionable_rerun_candidates.csv --run-summary-csv questionable_rerun_summary.csv --poll-seconds 20 --timeout-minutes 360
```

It reruns records that are distant view, missing model, missing price, or otherwise risky, then rebuilds flat output through the rename planner.

If a run must be resumed from a filtered candidate CSV, use:

```powershell
python tools\rerun_questionable_records.py --input-csv remaining_candidates.csv --execute --output-csv remaining_candidates.csv --run-summary-csv remaining_summary.csv --poll-seconds 20 --timeout-minutes 360
```

## Current Known Risks

- Some 2026 Samsung models still need official/PChome reference review.
- Some null-model photos need another focused pass or manual classification.
- `S27CG552EC` high-price cases may be bundles or OCR mistakes; do not auto-accept large price gaps.
- If UI appears blurry, verify the served JS has the no-blur build and that backend was restarted after this change.

## 2026-07-03 UI / Rerun Safety Update

- Dashboard thumbnails now prefer `/api/image/<source_path or file_name>` before `thumb_b64`; `thumb_b64` is only a last fallback.
- `/api/image` now resolves old filename-only records back to the original source photo under `D:\00_商化\00_未整理商化照片` and disables browser caching for served photos.
- `tools/rerun_questionable_records.py` now validates every candidate file against its real source folder before queueing. If the CSV says period `202510`, the resolved photo must also live under a path containing `202510`; otherwise it is skipped, not silently rerun under the wrong folder.
- `BatchOrchestrator.force_rerun()` now refuses to queue a rerun when the current source folder does not contain the requested image. This prevents missing-file cases from being logged as corrupted photos.
- The bad in-progress rerun controller was stopped after 202511 had exported. 202510 was interrupted before export and should be resumed only after regenerating or validating candidates with the patched script.

## 2026-07-03 19:45 Full Auto Resume

- Do not press the dashboard's old global "restart" action for normal production work. It purges OCR JSON history in the current source folder before rerunning. The boss-facing dashboard now exposes only the safe continue button (`續跑`) on the main toolbar.
- `skills/batch_orchestrator.py` now imports `Path`; without this, new results that tried to attach `source_path` failed with `name 'Path' is not defined`, causing a 5-failure meltdown.
- The production recursive runner was restarted with resume enabled, not `--restart` and not `--no-resume`.
- Active log files for this run:
  - `logs/full_auto_recursive_20260703_194356.out.log`
  - `logs/full_auto_recursive_20260703_194356.err.log`
- At restart, completed folders were being marked `skipped_existing`; folder `2025-商化照片\商化照片-202502` resumed from `534/1906` and began increasing normally.
- If this runner stops, inspect `/api/status`, the two log files above, and `_ocr_audit/folder_summary.csv`. Resume with the normal recursive launcher so completed folders remain preserved.

## 2026-07-04 UI / Upload / Missing-Result Update

- Dashboard version: `v19.12 (Staged Reveal)`.
- Boss-facing sequence must stay: photo appears first, LLM self-talk types out, then the parsed thumbnail/result is revealed in `辨識紀錄`.
- Backend is allowed to process ahead, but the UI must not reveal the next result early. Do not re-add immediate `presentedQueueCutoff` advancement when switching to the next displayed photo.
- The lower-left panel must never look missing or expose raw `JSON Error` / debug logs. While the current result is still hidden, it shows a clean `辨識中` status with the current filename.
- Google Drive upload can now use local rclone remote `samsung_ocr_drive`, rooted at the approved shared parent folder. Use year-only folders.
- Batch file for non-Python users: `UPLOAD_READY_PHOTOS_TO_GOOGLE_DRIVE.bat`.
- Script entrypoint: `python tools\rclone_drive_upload.py --execute --repeat --limit 500 --transfers 4 --checkers 8`.
- The uploader has a lock file under `_drive_upload\rclone_drive_upload.lock`; do not run multiple uploaders. It only uploads rows that `tools\prepare_drive_upload_manifest.py` marks `ready`.
- Current missing-result helper: `tools\build_missing_result_rerun_candidates.py`. It reads `_ocr_audit\folder_summary.csv` and creates a safe candidate CSV for `tools\rerun_questionable_records.py`.

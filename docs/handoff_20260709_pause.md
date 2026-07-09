# 2026-07-09 Pause Handoff

Project is intentionally paused for handoff.

## Current State

- No Samsung OCR backend, staged rerun, recursive OCR, auto-rerun waiter, rclone uploader, or upload helper should be running.
- Waste cleanup was completed before this handoff:
  - deleted output `_ocr_staging`;
  - deleted Drive upload staging;
  - deleted repo `logs`;
  - deleted Python caches outside `.venv`;
  - deleted old `flat_output_backup_before_*` folders;
  - deleted old `_bad_no_compare_2026_backup_*` folders.
- `.venv` is preserved.
- `D:` free space after cleanup was about 402,389,962,752 bytes.

## Code State

- Backend version is `v19.36 (strict distant quarantine and disk-safe rerun)`.
- `samsung_ocr_batch_processor.py` now treats current-year distant view as high risk:
  - a foreground/center/main monitor is `單機`, not `遠景`, even when model or price is unreadable;
  - FollowMe labels attached to a visible standing/vertical display must not be dismissed just because the classic white circular base is not visible;
  - distant answers with strong single-unit evidence are demoted to `單機` review rather than allowed to upload.
- `tools/rerun_staged_candidates.py` now avoids massive backup folders by removing old flat output for a folder before rebuilding it. Use `--keep-flat-output-backup` only when a human explicitly wants a full backup.
- The staged rerun tool also demotes unsafe `遠景` outputs with single-unit clues to `單機` review rows before merge.

## Last Completed Work

- The v19.36 pass3 staged rerun completed only `202605`.
- Completed group:
  - `202605`: 80 candidates processed, 80 success, 0 failed.
  - Export merged 80 updated rows and copied 905 output files.
- The run was stopped before completing `202604`, by user request to pause and hand off.

## Remaining 2026 Priority Work

Continue with 2026 before resuming older years.

Use these pass3 files as the latest handoff point:

- `D:\00_商化\00_已OCR照片\_ocr_audit\current_year_distant_and_risk_v1936_pass3_selected_20260709_1605.csv`
- `D:\00_商化\00_已OCR照片\_ocr_audit\current_year_distant_and_risk_v1936_pass3_summary_20260709_1605.csv`

Remaining pass3 candidates after `202605`:

- `202604`: 190
- `202603`: 31
- `202602`: 176
- `202601`: 154

Do not resume old-year recursive OCR until these 2026 review/rerun groups are handled and the upload manifest has been rebuilt.

## Latest Audit Snapshot

After completing the 202605 pass3 group:

- `audit_distant_followme_risk.py` reported:
  - `risk_rows`: 10
  - `sample_rows`: 602
  - `distant_total`: 601
  - `distant_no_followme_risk`: 592
  - `critical_followme_result_conflict`: 1
  - `high_single_unit_conflict`: 9
- `prepare_drive_upload_manifest.py --no-stage` reported:
  - `total_images`: 65,546
  - `ready`: 52,122
  - `uploaded_skipped`: 52,122
  - `ready_pending`: 0
  - `review_required`: 13,424
  - `stale_uploaded_review_required`: 0
- `split_drive_review_required.py` reported:
  - `missing_model_or_price_label`: 9,103
  - `needs_reference_price_compare`: 3,690
  - `current_year_distant_view_needs_rerun`: 621
  - `current_year_followme_or_distant_risk_needs_rerun`: 10

## Upload State

- Safe ready rows have no pending upload at this pause point.
- `ready_pending` was 0.
- `uploaded_skipped` was 52,122.
- Do not upload `review_required` rows.
- If a corrected 2026 row becomes ready, rebuild the manifest first, then upload only ready rows.

## Next AI Instructions

1. Read this file, `docs\ai_handoff_runbook.md`, `docs\development_guide.md`, and `SAMSUNG_OCR_EXPERIENCE_SKILL.md` before restarting anything.
2. Verify no OCR or upload process is already running.
3. Restart backend with the v19.36 code only when ready to continue.
4. Continue 2026 pass3 from `202604`, preserving completed `202605`.
5. After each 2026 group, refresh distant/FollowMe risk audit and upload manifest.
6. Upload only safe ready rows. Do not upload `review_required`.
7. Resume older-year recursive OCR only after 2026 current-year distant/FollowMe risk is under control.


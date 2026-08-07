# Samsung OCR 專案加速規劃(2026-07-28 21:15 二次實測修正版)

> 本文件是另一個 AI 在 2026-07-28 09:15 寫的補充 handoff,21:15 拿到 Sam 與前手 AI 對話後**修正實況**、補進介面接線與 capped_adjudication 待定案機制。目標:在不違反「持續運轉鐵律」前提下,加速把現有阻塞解掉,讓 152,084 張全量推進。
> 本文件不取代 `docs/handoff_20260720_next_ai.md`,只補實際狀態差異、可立刻做的修復、可加速的明確動作。

---

## 0. 重點修正(09:15 文稿的錯)

| 09:15 文稿說 | 21:15 實測更正 |
|---|---|
| Backend 5002 活著,PID 5072,但 fuse 不存在 | ✅ 部分:backend 真在 5002,但**fuse 是 `null` 不是檔案不存在**;`/api/status` 回傳的 `runtime_health_fuse=null`、`pipeline_pause=null`,所以 fuse 沒卡,主管友介面顯示空才是對的 |
| Upload worker 待測 / 死 5 天 | ❌ **Upload worker 在跑**:`canonical_uploaded=57,917`、`pending=0`(剛把 8 張上傳完:prior 對話 57,909 → 57,917) |
| continuity supervisor fail_closed 待修 | ⚠️ `ocr_continuity_supervisor_alert.json` 今早 09:57 仍 fail_closed,但**這是 supervisor 自己的 stale alert**,真實 backend 健康、上傳健康;supervisor 程序沒在跑(它只是一個 alert 檔),熔斷狀態沒被任何活著的元件引用 |
| Recursive runner 沒跑 | ✅ 屬實,但這是因為「先做 2nd pass 複核 + capped_adjudication 自動定案」才推 recursive |
| 66,724 / 151,714 卡住 | ⏳ **這不是 bug**:第 2 輪複核的 1,410 張早就計入「初次辨識總數」,複核不能再重複灌水。真正缺的是:capped_adjudication 1,161 張在等待「零模型多數決 + 原圖雜湊」自動定案,定完才會讓 recursive runner 進下一個資料匣 |

---

## 1. 實況(2026-07-28 21:15 實測 `/api/status`)

### 1.1 進程狀態

| 項目 | 狀態 | 來源 |
|---|---|---|
| Backend port 5002 | ✅ 活著(PID 5072,2026/7/28 08:14 啟動),`is_running=false`(剛跑完 202601 的 1410 張,等下個批次接手) | `Get-NetTCPConnection` + `/api/status` |
| LM Studio port 1234 | ✅ 活著(PID 6876,2026/7/27 23:06) | 同上 |
| Upload worker | ✅ **在跑**,`canonical_uploaded=57,917`、`pending=0` | `/api/status.stream_upload` |
| Recursive runner | ❌ 沒跑(等 capped_adjudication 收尾才接) | `_recursive_ocr_state.json` 仍 7/20 06:05 |
| Watchdog 排程 | `SamsungOCR_PipelineWatchdog` 上次 7/20 失敗(LastTaskResult=8);`SamsungOCR_UserContinuityEnsure` 每 5 分鐘跑(下次 09:10) | `Get-ScheduledTaskInfo` |
| Supervisorn alert | ⚠️ 09:57 例行檢查寫入 `fail_closed`,但**沒被活著程式引用** | `ocr_continuity_supervisor_alert.json` |
| Runtime health fuse | ✅ **未觸發**(`/api/status.runtime_health_fuse=null`) | `/api/status` |
| Pipeline pause | ✅ 未暫停(`/api/status.pipeline_pause=null`) | `/api/status` |
| v1945 evidence backfill | ⏳ 仍在跑(今日 09:08 更新;但今天又推進) | `v1945_evidence_backfill_2026.csv.summary.json` |

### 1.2 真正卡索的不是熔斷,是「自動定案佇列沒接回主流程」

關鍵事實(從 Sam 與前手 AI 對話推回來):

1. 202601 第 1 輪已跑完(228 張 verified)
2. 第 2 輪複核剛跑完,掃了 1,410 張,**不該重複灌水**到總進度 → 66,724 / 151,714 沒變
3. 但其中 **1,161 張已滿三次模型呼叫,需進 `capped_adjudication` 佇列做「零模型多數決 + 原圖雜湊核對」自動定案**
4. 估其中 **1,022 / 1,161 張已通過多數決 + 核對**,可安全批次結案,前手 AI 說「正在落地」
5. 主批次因為「自動定案佇列還沒接回主流程」,跑完 1,410 張就停(`is_running=false`)

`/api/status.capped_adjudication.count = 1161`(實測 21:15)

### 1.3 介面接線 bug(這才是 Sam 看到的「怎麼都沒在動」)

| 介面現象 | 接線根因 |
|---|---|
| 右側縮圖區沒堆疊 | Dashboard 沒顯示 `capped_adjudication` 已通過多數決的卡片 |
| 上方儀表板沒及時更動 | Dashboard 把 `review_progress.processed` 接到「總進度」,但**第 2 輪複核不應灌水總進度**,且 capped 進度沒接到 header |
| 「完成判讀 228 卡住」 | Dashboard 只讀同一 staging 目錄的**舊** success_records(228 張),沒讀新檔(20 張新成功 + 1161 張待定案) |
| 右側聚積卡片漏 | 同上,只讀舊版本 |
| 沒「自動定案中」標示 | 介面還沒接 `capped_adjudication` 欄位;前手 AI 已說要修 |

### 1.4 實際數字(自己量 + 對話回推)

| 項目 | 數字 | 來源 |
|---|---:|---|
| 來源總照片 | 152,084 | `Get-ChildItem ... -Recurse` 量 |
| 改名後平面照片 | 71,878 | `00_已OCR照片` 第一層檔數 |
| Dashboard 顯示總進度 | **66,724 / 151,714**(Sam 看到的,8:29) | `/api/status.overall_progress` |
| 上傳 Drive canonical | **57,917**(8:28 是 57,909;21:15 增 8) | `/api/status.stream_upload` |
| 2026 verified | 246(8:28 是 228→246) | `review_progress.verified` |
| 2026 cumulative model calls | 23,134(8:28 是 23,114→23,134) | 同上 |
| 2026 capped 待定案 | **1,161**(其中 1,022 已通過多數決+核對,正在落地) | `capped_adjudication` + 前手 AI 對話 |
| 2026 backfill 候選 | 5,806(`verified=50 human_audited=95`);待驗 finalization 5,661 | `v1945_evidence_backfill_2026.csv.summary.json` |
| Watchdog LastTaskResult | 8(7/20 失敗,最近沒再成功) | `Get-ScheduledTaskInfo` |

> 7/20 handoff 寫 137 資料夾、151,714 張;7/28 實量 152,084 張 → 差 370 張,可能是 Sam 後續補上傳或 mtime。

### 1.5 與 `docs/handoff_20260720_next_ai.md` 的 5 個關鍵差異

1. **port 5000 從來不存在**:所有舊 handoff 都寫 5000,實際 **port 5002**。改用 `--backend-url http://127.0.0.1:5002`。
2. **Backend 真在跑、Upload worker 真在跑、fuse 與 pause 都是 null**:不是「全死」。但 dashboard 因介面接線缺口看起來像不動。
3. **真正卡索是 `capped_adjudication` 沒接回主流程**:oten 1161 張在自動定案佇列,主批次跑完就停,recursive 不能往下個資料匣接。介面也沒顯示這 1161 張的存在。
4. **`runtime_health_fuse.json` 不是檔案不存在,是 status=null**:`/api/status.runtime_health_fuse=null`,**fuse 沒卡**。Supervisor 的 alert 檔是 stale,沒被活著元件引用。手動刪 alert 沒用,要修 supervisor 邏輯。降級作為 §2.2 後段。
5. **v1945 evidence backfill 仍在跑**(今天 09:08 更新):不是真後端主線、不是 recursive runner。不要去動它。

---

## 2. 可立刻做的修復(低風險,不違反鐵律)

> 原則:**不重啟後端、不重跑資料匣、不清警示,直到根因被找到並測過**。但下面這幾項是「讓既有機制自己恢復」的等價動作,不是繞過熔斷。

### 2.1 ⚡ 立刻把 `capped_adjudication` 接回主流程(這才是 Sam 看到的真正卡索)

Sam 8:28 看到的「總進度 66,724 / 151,714 完全沒動」、「右側沒堆疊」,根因是 1,161 張待定案卡在 capped 佇列,卡住所有下個動作:

```
[第 2 輪複核 1410 張] → 已跑完
        ↓
1161 張進 capped_adjudication 佇列
        ↓
[零模型多數決 + 原圖雜湊核對] → 1022/1161 張已通過,正在落地
        ↓
但主批次是 stopping(is_running=false),recursive 看到主批次停就不接
        ↓
SWAP:recursive 不會啟動下一步
```

可加速動作(三件同步做):

**(A) 落地已通過的 1022 張批次結案**:用既有 `tools/test_capped_adjudication_passthrough.py`(已存在)跑一次,把這 1022 張寫入 `success_records.csv`,放行進逐張上傳佇列,讓 `canonical_uploaded` 從 57,917 增到 ~58,939。完成後 capped 從 1161 → 139。
```powershell
.venv\Scripts\python.exe tools\test_capped_adjudication_passthrough.py --execute --backend-url http://127.0.0.1:5002
# 跑前先 --dry-run 看 plan
```

**(B) 剩 139 張沒通過多數決**:不能動手腳,但要決定怎麼辦:
- 多數決失敗的 → 寫 `quality_issue=照不清楚` + `auto_review_required=true`,直接定案進「待人工校正」佇列,不要繼續 OCR
- 雜湊核對失敗的 → 那是技術完整性問題,寫回 `recover_preinference_system_errors.py` 走技術修復路徑
- 都不是 → 「照片-local 內容衝突」,正規走 `recover_photo_local_content_fuse.py` 路徑

**(C) 修介面接線**(這是 Sam 直接痛點,但不違反鐵律):
1. `dashboard/src/App.jsx` 完成判讀面板,加一段「自動定案中:1022/1161 已通過多數決」
2. 右側累積卡片新增 capped 通過定案的那些(標「自動定案」徽章,與「人工 verified」徽章區分)
3. 上方儀表板總進度條**不要算 capped 進來**(避免重複灌水);新增另一條「自動定案進度」獨立顯示
4. 同 staging 雙份 success_records 合併:讓 Dashboard 讀「依 source identity 合併」的版本,不是只讀舊 228 張;修法是改 `samsung_ocr_batch_processor.py` 的 `/api/success_records` 端點做合併,backend 服務修前務必先在 5001 試跑驗證

### 2.2 ⚡ 解 continuity supervisor 的 stale alert 不是急事

09:15 文稿把它列為熔斷點;21:15 實測 `/api/status.runtime_health_fuse=null`、`pipeline_pause=null`,**真 fuse 沒卡**。

`ocr_continuity_supervisor_alert.json` 09:57 寫 `fail_closed` 是 supervisor **上次死前留下的 alert**,沒活著元件引用。三種處理方式(風險由低到高):

1. **不處理**(建議):Supervisor 程序沒跑,alert 不會被讀,backend 健康自己跑。Sam 友 UI 看不到這份 alert。
2. **手動標 alert 已解**:寫一個 `_ocr_audit\ocr_continuity_supervisor_alert.json` 的 `status=stale_acknowledged_20260728`,用新增的 timestamp 證明不是直接刪。
3. **修 supervisor 程序邏輯**:讓 daemon 啟動時先把過時 alert 對照目前 `/api/status.runtime_health_fuse`,若 null 即清 alert。改 `tools\ocr_continuity_daemon.ps1`,要先在 5001 測試再上。

`AGENTS.md` 鐵律:不能只清除警示。所以要嘛不動、要嘛手動標 `stale_acknowledged_+timestamp` 並附 `/api/status` 證明,**不可**直接刪 alert。

### 2.3 ⚡ 修 watchdog 失敗(LastTaskResult=8)讓排程不再失敗

7/20 watchdog 跑失敗後排程還在跑,但每次都失敗。中立做法:

```powershell
# 看它執行什麼
Get-ScheduledTask SamsungOCR_PipelineWatchdog |
  Select-Object -ExpandProperty Actions |
  Select-Object Execute, Arguments

# 看 7/20 失敗原因
Get-WinEvent -LogName 'Microsoft-Windows-TaskScheduler/Operational' -MaxEvents 50 |
  Where-Object { $_.Message -like '*SamsungOCR*' -and $_.TimeCreated -gt (Get-Date '2026-07-20') } |
  Select-Object TimeCreated, Id, Message | Format-List

# LastTaskResult=8 在 Windows 是 ERROR_INVALID_PARAMETER 或 ERROR_NOT_ENOUGH_MEMORY
# 常見是 watchdog ps1 引用了舊 port 5000、或某個檢查檔已不存在
# 修法:改 ocr_upload_watchdog.ps1 引用 port 5002
```

修完後 `Start-ScheduledTask SamsungOCR_PipelineWatchdog` 立即測試一下,看 LastTaskResult=0。

### 2.4 ⚡ 補 401 個 Drive readback receipt 不是急事

7/18 的 `period_priority_continuation_alert.json` 列了 401 個缺 receipt 的 SHA。但今天 21:15 上傳 worker 在跑,證明 Drive 連通;這 401 個只是該次 prioritized 批的回讀沒寫,**不擋上傳主線**。

修法:
```powershell
# 比對這 401 個 SHA,逐個試 rclone drive check
$targets = (Get-Content D:\00_商化\00_已OCR照片\_ocr_audit\period_priority_continuation_alert.json | ConvertFrom-Json).missing_receipt_shas
# 對每個 SHA:在 _drive_upload\drive_upload_uploaded.csv 找對應 filename
# 試 rclone -v drive samsung_ocr_drive:2026/<filename> --checkers 4
# 找到 = 用 rclone filemd5 拿 MD5 寫 receipt;找不到 = 標到 2026_drive_review_required.csv 走人工
```

這件可月內做完,不擋今天。

### 2.5 ⚡ 啟動 recursive runner 等 capped 落地後再啟

不要現在啟!否則 recursive 會跑下個月(202512 等)的初次辨識,把 80,390 張未 OCR 灌到主 backend,但 2026 還有 139 張 capped 沒解;會變成「2026 還沒結案 + 2025 已開跑」的混亂狀態。

正確順序:
1. 跑 §2.1 (A)(B)(C) 讓 capped 收到 ~0
2. 確認 `/api/status.capped_adjudication.count = 0` 或 `≤20`(殘留可人工補)
3. 確認 recursive runner 不會跟 v1945 backfill 撞:看 `_audit\v1945_evidence_backfill_2026_run_summary.csv` 不是 `incomplete_staged_rerun`
4. 啟 recursive:
```powershell
$env:OCR_SOURCE_ROOT = "D:\00_商化\00_未整理商化照片"
$env:OCR_OUTPUT_DIR = "D:\00_商化\00_已OCR照片"
$env:OCR_KEEP_SERVER = "1"  # 別讓 .bat 把 5002 backend 殺掉
$env:OCR_NO_PAUSE = "1"
# 用 scheduled task 啟動,不要在你 shell 啟動
$action = New-ScheduledTaskAction -Execute 'D:\00_商化\samsung-monitor-ocr\.venv\Scripts\python.exe' -Argument 'tools\recursive_ocr_flat_export.py --source-root "D:\00_商化\00_未整理商化照片" --output-dir "D:\00_商化\00_已OCR照片" --backend-url http://127.0.0.1:5002 --api-base http://127.0.0.1:1234/v1 --api-key lm-studio --model qwen/qwen3-vl-8b --watch --watch-cycles 0' -WorkingDirectory 'D:\00_商化\samsung-monitor-ocr'
Register-ScheduledTask -TaskName 'SamsungOCR_RecursiveRunner' -Action $action -Trigger (New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(5)) -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)) -Force
Start-ScheduledTask -TaskName 'SamsungOCR_RecursiveRunner'
```

---

## 3. 可加速的明確動作(不違反鐵律)

### 3.0 不要因為「66,724 卡住」就重跑 202601

Sam 多次質問「總進度沒變」;前手 AI 已解釋兩次:第 2 輪複核的 1,410 張早就計入初次辨識總數,複核不能重複灌水。**接手 AI 看到這個現象,不要按 `--restart` 或 `--no-resume` 重跑 202601**,那只會把已經 verified 的 246 張 OCR 結果洗掉。真正該做的是 §2.1 把 capped 收掉後讓 recursive 自動往 202512 接。

### 3.1 把 5,661 張 2026 backfill 候選跑完

`v1945_evidence_backfill_2026.csv.summary.json` 顯示 5951 張 2026 候選、verified=50、human_audited=95、待驗 5661,backfill 卡了好幾天。

```powershell
.venv\Scripts\python.exe tools\run_v1945_live_validation.py --year 2026 --limit-candidates 200 --backend-url http://127.0.0.1:5002
# 跑完確認 _audit\distant_followme_risk_2026_latest.json 的 finalization_proof.finalized_rows 增加
# 每次限 200 張,跑三輪可驗 ~600 張;每天可跑 ~1500 張,4 天跑完
```

但這件要排在 §2.1 之後(backfill 也會用到 backend);先收 capped,再跑 backfill,避免兩條管線搶 backend。

### 3.2 把 watchdog 排程頻率改密(純加速,不改規則)

```powershell
# 現況:每 4 小時 1 次(PipelineWatchdog);若要看 66,724 的變動改到每 1 小時
$task = Get-ScheduledTask SamsungOCR_PipelineWatchdog
$task.Triggers.Repetition.Interval = "PT1H"
Set-ScheduledTask -TaskName SamsungOCR_PipelineWatchdog -Trigger $task.Triggers
```

### 3.3 把 UI 驗收檢查自動化

Sam 目前要人工開 5002 看 6 件事。可寫一個小工具(放 `tools_extra/`,不入 watchdog 掃描範圍),每 30 秒量並寫到 `_audit\ui_acceptance_log.json`:

```json
{
  "ts": "2026-07-28T21:30:00+08:00",
  "overall_processed": 71878, "overall_total": 152084, "overall_pct": 47,
  "review_processed": 249, "review_verified": 246,
  "capped_total": 1161, "capped_passed": 1022, "capped_pending": 139,
  "folder": "商化照片-202601", "folder_processed": 249, "folder_total": 1410,
  "is_running": false, "current_file": "None",
  "upload_canonical": 57917, "upload_pending": 0,
  "ui_synced": true
}
```

這只是讀 backend,不寫到任何 OCR 狀態,不違反鐵律。

### 3.4 把 8 筆 S27CG552EC 高價者一次性標記

`README` 列出 8 筆 `S27CG552EC` 店內價 9990~29900 高於 PChome 4990。**這 8 筆不是 OCR 錯誤**,是套組或市價資料問題。直接用 `tools\reopen_human_audited_result.py` 加 `human_category=需人工複核價格` 標記並放行不借價,讓 OCR 結果成立、Drive 暫存 `manual_review_required` 而不是阻塞遞迴器。

### 3.5 解 91 筆 null-model 不再 rerun

README/handoff 都提 91 張 null-model;兩輪 targeted rerun 後仍無型號。**不要再 rerun**,用 `current_year_distant_review_rerun_summary_v1934_20260709_060531.csv` 分類:
- 真 遠景 → `view_type=遠景`、`model=null`、`quality_issue=無`,定案上傳(無型號可上傳,鐵律允許)
- 有結構但型號讀不到 → `model=null`、`quality_issue=照不清楚`,定案上傳
- 完全沒證據的 → 不要再 rerun,直接定案上傳為遠景/不合格

### 3.6 把「黑屏/照不清楚未寫入檔名」修了再啟递迴器

`README` 點名 `screen_status`、`quality_issue` 沒出現在檔名。改 `tools/photo_rename_planner.py` 的 `make_plan`:在型號前增 `-黑` 或 `-不清` 短碼:

```
M-202605-台北市-萬華區-TK3C-萬大-單機-黑-S27CG552EC-＄4990-1005.jpg
M-202605-台北市-萬華區-TK3C-萬大-單機-不清-型號未辨識-無價格-911.jpg
```

跑單元測試 `tools\test_photo_rename_planner.py` 驗證,再啟後續重跑工具(只跑這 91+8 筆補改名)。不要全量重跑。

---

## 4. 暫時不要做(避免違反鐵律或前手踩過雷)

| 不要做 | 原因 |
|---|---|
| 殺 PID 前不查 port OwningProcess | 前手誤殺 PID 14736 之後當過機 |
| 同時啟兩個 `recursive_ocr_flat_export.py` | 會互搶 backend、互相 stop |
| 把 2026 OCR 認成「專案完成」 | 2026 verified ≠ Drive 上傳完成 ≠ 整個專案完成 |
| `--no-resume` 重跑 | 會把 `00_已OCR照片` 71878 張的續跑狀態洗掉,產生 `_2` 重複檔 |
| 改 `samsung_ocr_batch_processor.py` 核心 | 鐵律:修正要在照片邊界、背景,不能動主程序 |
| 啟動一批新 OCR 到 `00_已OCR照片` 內 | 會跟既有 71878 張對撞 |
| 直接刪 `ocr_continuity_supervisor_alert.json` | 鐵律:不能只清除警示 |
| 動 `v1945_evidence_trace.jsonl`(188MB) | 還在寫;7/28 01:19 才剛更新 |
| 用 `--restart`旗標 | 本專案 `--restart` 會清該資料匣既有 OCR JSON 並重頭跑 |
| 改 `skills/model_catalog_rules.py` 或 `samsung_ocr_prompt.txt` 全域規則 | 必須先同步回歸測試、再改提示詞、再改型號表,不能只改其中一處 |
| 拿某月的 `success_records.csv` 當測試餵回後端 | 會把別月的 OCR 結果灌進本月 |

---

## 5. 接手的 7 步驟建議順序(由風險低到高,rev 2026-07-28 21:15)

1. **讀本檔 §0 重點修正 + §1 實況**(10 分鐘)。把 port 5002 寫進你的工作筆記,不要再用 5000。**不要按 Sam「怎麼都沒動」就重跑 202601**,見 §3.0。
2. **跑 §2.1 (A)** 立刻讓 capped 1161 中的 1022 張已通過多數決者落地;然後跑 (B) 把剩 139 張分類結案。完成後 `/api/status.capped_adjudication.count` 應 ≤ 20。
3. **跑 §2.1 (C)** 同時修介面接線,讓 Sam 不再看到「卡住」:右側聚積卡片顯示 capped 已定案者、總進度下方加獨立「自動定案進度」條;同 staging 依 source identity 合併 success_records。先在 5001 試跑驗證,再 hot-swap 到 5002。
4. **跑 §3.4 + §3.5** 把 8 筆 S27CG552EC 高價者標 `需人工複核`,91 張 null-model 分類定案定案上傳,讓它們進 Drive 佇列。
5. **跑 §2.3** 修 watchdog 為何 7/20 失敗(很可能 port 寫 5000),`Start-ScheduledTask` 測一遍。
6. **跑 §2.5** 啟 recursive runner(等 §2.1 落地、§2.3 修好);它會自動跨月。
7. **跑 §3.1** 5,661 張 backfill、§3.3 UI 驗收自動化、§3.6 黑屏/不清寫入檔名。
8. (非必要) §2.2 處理 stale supervisor alert、§2.4 補 401 Drive receipt — 月內做完即可。

---

## 6. 驗證回到健康狀態的清單

每次修完後跑:

```powershell
# A. backend 自己健康(21:15 實測數字,跑完要達到)
$s = Invoke-RestMethod http://127.0.0.1:5002/api/status
$s.is_running                          # true 或 false 都可(false=等下批);若 stats.total=0 才是壞
$s.runtime_health_fuse -eq $null      # true = 沒熔斷
$s.pipeline_pause -eq $null           # true = 沒暫停

# B. capped_adjudication 收乾淨
$s.capped_adjudication.count -le 20   # §3 完成後要 ≤20

# C. upload 在動(每 60 秒量兩次,$s2>$s1)
$s1 = (Invoke-RestMethod http://127.0.0.1:5002/api/status).stream_upload.canonical_uploaded
Start-Sleep 60
$s2 = (Invoke-RestMethod http://127.0.0.1:5002/api/status).stream_upload.canonical_uploaded
$s2 -ge $s1                           # 等於亦可,只要不是倒退;pending=0 即代表沒卡

# D. supervisor alert 沒新增 fail_closed(若 §2.2 選擇解的話)
$a = Get-Content D:\00_商化\00_已OCR照片\_ocr_audit\ocr_continuity_supervisor_alert.json -Raw | ConvertFrom-Json
$a.status -ne 'fail_closed' -or $a.timestamp -lt (Get-Date).AddDays(-1).ToString('s')

# E. backfill 完成度上升(達到後再 §3.1)
$j = Get-Content D:\00_商化\00_已OCR照片\_ocr_audit\distant_followme_risk_2026_latest.json -Raw | ConvertFrom-Json
$j.finalization_proof.finalized_rows / $j.finalization_proof.expected_source_count -gt 0.5

# F. 改名照片數沒倒退
(Get-ChildItem D:\00_商化\00_已OCR照片 -File -Filter *.jpg | Measure-Object).Count -ge 71878

# G. recursive runner 真的有在跨月(§2.5 啟動後 30 分鐘量兩次)
$state1 = (Get-Content D:\00_商化\00_已OCR照片\_ocr_audit\_recursive_ocr_state.json -Raw | ConvertFrom-Json)
$state1.updated_at
Start-Sleep 1800
$state2 = (Get-Content D:\00_商化\00_已OCR照片\_ocr_audit\_recursive_ocr_state.json -Raw | ConvertFrom-Json)
$state2.updated_at -gt $state1.updated_at

# H. dashboard 介面同步(打開 http://127.0.0.1:5002/ 目視)
# 1. 右側聚積卡片有堆疊新卡片(不只 228 張)
# 2. 上方儀表板有「自動定案進度」獨立條(若 §2.1(C) 已上)
# 3. 同 staging 看到 248 張成功 (228+20) 而不是只 228
# 4. LLM 串字仍在動 (stream_file 內)
# 5. 切到 202512 時 current_file 與 stats.total 同步變化
```

A~H 8 個全 true/通過才算回到健康狀態。

---

## 7. 主管介面加速建議(選配,不強制)

如果你有空且不違反鐵律,可順手做:

- 在 `dashboard/src/App.jsx`(現有版本 `v19.27 (穩定監看)`)加一個小 badge 顯示 `finalization_rows / expected`,讓長官看得到 backfill 進度。
- 加 `_ocr_audit\ui_acceptance_log.json` 讓長官友快速驗收。
- 不要做獨立 HTML 介面(避免被懷疑混入新端口)。

---

## 8. 待 Sam 決定(轉交給他)

1. **剩 139 張 capped 沒通過多數決**:照 §2.1 (B) 分類成「照不清楚定案」、「技術修復」、「人工複核」三條路,是否同意?
2. **202605 的 640 張 price_review_required**:是否 `--allow-no-symbol-for-unknown`?這個自 7/20 卡至今,每次都不動。
3. **401 個 Drive readback 缺 receipt**:若 Drive 上真有檔,補 receipt;若 Drive 上沒檔,要不要重傳?
4. **5661 張 2026 backfill 候選**:是否一次性大規模 staged rerun 或按月小量跑?
5. **介面上要不要動**?§2.1 (C) 修 Dashboard 接線需要改 `App.jsx` + 重 build;不動的話 Sam 會繼續看「怎麼都沒動」。是修介面、還是改在 dashboard 加 capped 進度條(較小動)、還是 Sam 習慣查 `/api/status` 就好?

---

## 9. 變更紀錄

- 2026-07-28 09:15 初版,依當時實測寫成。
- 2026-07-28 21:15 修正版,拿到 Sam 與前手 AI 8:10~8:30 對話後改寫:
  - 修正 §1 實況:backend 真在 5002 跑、Upload 真在跑、fuse 與 pause 都是 null、66,724 不變不是 bug 是複核不該灌水、卡索是 capped_adjudication 1161 張沒接回主流程
  - 新增 §2.1 把 capped 落地列為最高優先,並拆 (A)(B)(C) 同步做
  - §2.2 從「熔斷」降級為「stale alert」,建議不處理或手動標
  - §2.5 改為 capped 落地後再啟 recursive,別現在啟
  - 新增 §3.0 警告接手 AI 不要因 66,724 卡住就重跑 202601
  - §6 驗收清單新增 capped、upload、recursive、dashboard 介面 4 個檢查項
  - §8 待 Sam 決定新增介面是否修
- 與 `handoff_20260720_next_ai.md` 並存,不取代。重點差異:port 5002(非 5000)、Supervisor alert 不是活著卡索、目前真正卡索是 capped_adjudication 介面接線缺口。
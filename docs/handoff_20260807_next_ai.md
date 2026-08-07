# Samsung Monitor OCR 現況權威交接手冊（2026-08-07）

> 文件用途：下一位 AI 的第一入口。先讀本文件，再讀 `development_guide.md`、`three_layer_accuracy_gate.md` 與 `ai_handoff_runbook.md`。
>
> 專案根目錄：`D:\00_商化\samsung-monitor-ocr`
>
> 照片來源：`D:\00_商化\00_未整理商化照片`
>
> 正式輸出：`D:\00_商化\00_已OCR照片`
>
> Dashboard：`http://127.0.0.1:5002/`
>
> LM Studio API：`http://127.0.0.1:1234/v1`
>
> 本文件快照：2026-08-07 10:40（Asia/Taipei）。數字會持續增加，接手時必須重新讀取 API，不能用本文件數字覆寫現場。

## 0. 接手者先做什麼

1. **不要停止現行程序、不要重開瀏覽器、不要新增分頁、不要重啟 LM Studio。**
2. 唯讀讀取 `http://127.0.0.1:5002/api/status`，確認：
   - `is_running=true`
   - `evidence_guard_revision=20260807.96`
   - `runtime_health_fuse=null`
   - `pipeline_pause=null`
   - `current_file == stream_file`
   - `overall_progress.processed_images`、資料夾 `stats.processed` 與 `stream_upload.canonical_uploaded`持續增加
3. 確認唯一正式 backend、唯一 uploader、唯一歷年協調程式；Python 虛擬環境的父子程序是同一服務，不可誤殺成重複程序。
4. 使用者已指示：本次交接與 Git 完成後，AI 停止修改；Dashboard、OCR、歷年接力與逐張上傳必須繼續運作，等待下一次明確指示。

## 1. 專案目標與永久鐵律

專案要依最新往最舊，把 frozen inventory 的 **151,714 張、137 個資料夾**逐張完成：

`本機 LM Studio 看原圖 → 最多三次同圖、無記憶、請求綁定的模型呼叫 → 固定程式定案 → 正確檔名 → 逐張上傳 → Drive 大小與 MD5 收據 → 下一張`

永久優先順序：

**照片辨識正確性 ＞ 節省 OpenAI／Codex tokens ＞ 完成時間**

但正確性必須由固定本機程式與回歸測試保證，不能靠 Codex 逐張看 15 萬張照片。正式 OCR 只能使用 LM Studio 本機模型；不得使用 OpenAI／Codex 逐張代判。

其他不可違反規則：

- Dashboard／port 5002 必須一直在線並沿用既有瀏覽器分頁。
- 系統性內容錯誤只能在照片邊界停止 OCR／上傳；Dashboard 不能關閉，修好後必須從原檢查點自動續跑。
- 明確單機且同一主體型號、價格已確立，第一輪即可結案。
- 明確遠景且沒有 FollowMe 直接或實體線索，第一輪即可結案；遠景複核的目的只是在高風險照片找出被漏掉的 FollowMe，不是所有遠景固定三輪。
- FollowMe 家族、精確 SKU、價格是三個獨立欄位；家族成立後不能因 SKU 不清而推翻，價格不清只留空價格。
- 跨重啟、跨版本、業務輪次與內部技術重試合計最多三次真正 LM Studio 呼叫，禁止第 4 次。
- 每張定案後立即排入持久化逐張上傳佇列；不得等待整月或整年完成。
- 單張資料不完整只能照片級處理，不能停止整批。

## 2. 2026-08-07 10:40 現場快照

| 項目 | 現況 |
|---|---:|
| Dashboard | port 5002 在線、同一既有分頁 |
| LM Studio | port 1234，`qwen/qwen3-vl-8b` |
| 守門修訂碼 | `20260807.96` |
| 正式狀態 | `is_running=true` |
| 目前月份 | `202108` |
| 目前檔名／串流檔名 | 兩者相同；快照為 `M-台中市-潭子區-TK3C-TK3C潭子-998.jpg` |
| 202108 | `187/1,369`，verified `170`、review `17`、failed `0` |
| 全案 | `80,599/151,714`（53.13%） |
| 資料夾 | 已完成 `57/137` |
| 上傳 canonical | `67,484` |
| 上傳 pending／working | `0/0` |
| 上傳 last_error | 空白 |
| 平均推論 | 約 `18.04` 秒／模型呼叫（動態值） |
| GPU／VRAM | 快照 `20.0%`／`14,564 MB of 16,303 MB`；推論中會上升 |
| CPU／RAM | 快照 `24.4%`／`33.7%` |
| runtime fuse | 無 |
| pipeline pause | 無 |

續航證據：10:25:19 至 10:25:50，202108 `processed 170→171`、全案 `80,582→80,583`；10:40 已到 `80,599`。逐張上傳稍後為 `67,484`、pending 0。這證明正式照片、全案進度與上傳不是只顯示綠燈，而是真的在前進。

## 3. 本次事故：模型看到了，程式卻把型號／價格清空

### 3.1 使用者發現的共同症狀

多張歷史單機照片的實體價牌非常清楚，例如：

- `F24T350FHC / 3,990`
- `C24F390FHE / 3,990` 或 `4,290`
- 其他清楚的 Samsung SKU 與同牌價格

本機 Qwen 的原始自然語句往往已讀到正確型號與價格，但 Dashboard 終局卻顯示「無型號／無價格」，甚至三次呼叫用滿後標成技術錯誤。這不是照片本身辨識不到，而是後段守門順序與正規化過度嚴格。

### 3.2 `.94` 已先處理的問題

`.94` 修正過執行狀態／自然語句遺失、同價牌原始欄位回復與促銷價格規則；曾用既有證據零模型掃描 481 筆候選，109 筆有足夠證據可安全更正，其餘 372 筆因證據不足保持原狀，沒有猜測或使用第 4 次模型呼叫。

已確認案例：

- `M-台中市-南屯區-TK3C-台中嶺東-959.jpg` → `F24T350FHC / 3990`
- `M-台南市-歸仁區-TK3C-歸仁-1296.jpg` → `C24F390FHE / 3990`

### 3.3 `.95` 找到的第一個真正根因

案例 `M-桃園市-桃園區-TK3C-桃園中平-1242.jpg` 的三次獨立同圖證據足以得到 `C24F390FHE / 4290`，但 finalizer 先走一般內容完整性退出，後面的同價牌修復永遠沒有機會執行。

`.95` 將同價牌回復移到一般完整性退出之前，並補上三次上限與重驗測試。零模型更正：

- 桃園中平 1242 → `C24F390FHE / 4290`
- 桃園旗艦 716 → `C27R500FHC / 5990`
- 桃園旗艦 719 → `S32AM700UC / 12900`

### 3.4 `.96` 找到的剩餘系統性根因

對 `.95` 新一輪 trace 做精簡統計：80 張、188 筆實際模型呼叫，68 張 verified、11 張三次用滿技術錯誤；其中 **7 張的自然語句已有實體價牌型號與價格**。因此問題仍是系統性，不是偶發單張。

確認三個共因：

1. `_normalize_self_consistent_owned_single_pass` 強制要求結構欄先回傳 `label_ownership=matched`。正是結構欄誤回 `not_applicable` 的案例無法被同一張實體價牌救回。
2. 同一張價牌若同時印短型號與完整型號，例如 `C24F390F` 與 `C24F390FHE`，舊程式誤認成兩個互相衝突的型號。
3. finalizer 與歷史同價牌備援對無害的介面自然語句格式警示、首字母 OCR 口誤或少一個尾碼過度嚴格，導致已由型號目錄唯一驗證的結果仍被清空。

## 4. `.96` 已正式啟用的修正

主要程式：`skills/audit_fields.py`，修訂碼 `20260807.96`。

### 4.1 同價牌型號正規化

`_narrated_physical_card_model_price_pair` 現在會：

- 正規化同一實體價牌的 SKU token。
- 只有所有候選都為前綴關係、長度差不超過 3 時，才把短碼與完整碼收斂為最長碼。
- 只有型號目錄能唯一補全、差異不超過 2 字元時，才把零售端短碼補為已驗證完整型號。
- 任何跨型號、跨價牌或價格不一致仍失敗封閉，不能為追進度猜值。

### 4.2 單輪實體價牌修復

新增 `_normalize_narrated_owned_card_single_pass`：

- 只適用 2025 年以前的歷史照片。
- 必須是單機、唯一主角，且自然語句明確指出同一主體的實體價牌。
- 型號必須先通過既有型號目錄；價格必須與該同牌一致。
- 不得有 ownership、品牌、價格或照片身分衝突。
- 結構欄若錯算完整螢幕數，自然語句必須明確證明只有一台完整、其他只是裁切鄰機，才可修正為 1。

### 4.3 三次同圖回復

`_same_card_pass_has_base_integrity` 與 `_historical_same_card_raw_recovery` 現在只忽略已知的本地呈現／結構欄衝突，仍強制要求：

- 同一原圖 SHA-256
- 同一 source identity
- request ID 綁定
- 每輪獨立、無前輪答案
- 無跨圖或提示詞污染
- 同一共同價格
- 型號必須由既有目錄唯一驗證

可接受的狹窄 OCR 差異只有 `S/C/F/P` 首字母口誤，以及最多缺一個尾碼；不能把不相關型號硬合併。

### 4.4 上傳版本相容

`tools/stream_drive_upload.py` 已把 `.86` 至 `.95` 的安全重驗結果遷移到 `.96`；`tools/test_build_v1945_evidence_backfill.py` 也以 `.96` 為現行權威。

## 5. 驗證證據

### 5.1 自動測試

- `.96` 新增 4 個精確案例：錯誤 ownership/count 修復、首字母／尾碼／介面格式差異、同牌短碼與完整碼收斂、finalizer 執行順序。
- 相關證據測試：`355` 項通過。
- 完整 `tools/run_critical_regressions.py`：exit code `0`。
- 沒有提高提示詞上限、沒有放寬三次呼叫上限、沒有把上一輪答案帶入下一輪。

### 5.2 既有 trace 零模型重播

原 11 張三次用滿技術錯誤中，`.96` 可在完全不呼叫模型的情況安全救回 2 張：

- `M-桃園市-觀音區-TK3C-觀音-1135.jpg` → `F24T350FHC / 3990`
- `M-高雄市-三民區-TK3C-澄清-1125.jpg` → `C24F390FHE / 3990`

規則皆為 `three_pass_same_card_raw_field_consensus`。另外 9 張既有證據不夠一致，仍保持 unresolved；不能猜值，也不能給第 4 次模型呼叫。

**重要：**使用者要求本次先恢復介面、完成交接與 Git，然後停止修改；上述 2 張的正式結果檔零模型套用尚待下一次指示。不要把「重播可修」誤報成「正式資料已寫入」。

### 5.3 隔離本機模型驗證

`.96` 在 fuse 與 benchmark lock 保護下執行兩組隔離試跑，正式照片與 Drive 均未被寫入：

1. `202109_runtime_health_smoke_rev96_20260807_094255`
   - 清楚價牌案例正確輸出 `F24T350FHC / 3990`。
   - 遠景案例輸出 `遠景 / 無型號 / 無價格`。
   - 兩張最終皆 verified，守門修訂碼 `.96`。
2. `202109_runtime_health_smoke_rev96_clearance_20260807_095446`
   - 綁定案例輸出 `遠景 / 無型號 / 無價格`。
   - 三輪圖像與請求 invariant 全數通過。
   - 正式 clearance receipt：
     `D:\00_商化\00_已OCR照片\_ocr_audit\runtime_health_fuse_clearance\smoke_20260807_100505_845744.json`

第一組清楚價牌結果的 `auto_verified=true`，但匯出 metadata 曾同時顯示 `evidence_contract_valid=false`。型號／價格終局本身正確，仍應在下一次修改前確認這是匯出 metadata 計算順序還是另一個契約顯示缺口；不要只看 `auto_verified` 就宣告所有 metadata 完全一致。

## 6. 本次正式恢復時間軸

1. 發現 `.95` 正式主線仍有「自然語句讀到、終局清空」的系統性錯誤。
2. 在照片邊界啟用 durable runtime fuse，停止 OCR／上傳；Dashboard／port 5002 保持在線。
3. 曾發現 Supervisor 在一般 operator stop 後意外自動續跑到 202108；因此修復期間以 runtime fuse 而不是單純停止按鈕維持保護。
4. `.96` 以 `RuntimeHealthTrialReload` 在 port 5002 原位載入；沒有重開瀏覽器或 LM Studio。
5. 完成回歸與隔離試跑後，以內容綁定 clearance receipt 清除 fuse，再移除 benchmark lock。
6. 正式批次從 `D:\00_商化\00_未整理商化照片\2021-商化照片\商化照片-202108` 原檢查點 `156/1,369` 續跑。
7. uploader、歷年協調程式、問題照片 watcher 與 continuity daemon 均已恢復；正式進度與 canonical 上傳持續增加。

## 7. 現行程序與不要誤判的父子程序

應存在的服務：

- `samsung_ocr_batch_processor.py`：唯一 port 5002 backend。虛擬環境 launcher 與 bundled Python child 是同一程序樹。
- `stream_drive_upload.py`：唯一逐張上傳 worker；同樣可能看到 launcher + child。
- `recursive_ocr_flat_export.py`：唯一 receipt-bound 歷年協調程式。
- `auto_rerun_questionable_after_recursive.ps1`：歷年主線完成後的問題照片接續 watcher。
- `ocr_continuity_daemon.ps1`：隱藏守護程序。
- LM Studio port 1234：`qwen/qwen3-vl-8b`，正式 parallel 1。

用 `Get-NetTCPConnection` 與完整命令列／父子關係判斷唯一性，不能只看工作管理員有幾個 `python.exe`。

## 8. 下一位 AI 的明確待辦

依優先順序：

1. **先唯讀確認 `.96` 持續運轉。**不要因本文件快照過期而重啟。
2. 取得至少 20 張 `.96` 正式 trace 的精簡統計，確認「三次用滿技術錯誤且自然語句已有同價牌型號／價格」已降為 0。只讀必要欄位，不要把完整 trace 倒進對話。
3. 檢查第一組隔離試跑的 `auto_verified=true` 與 `evidence_contract_valid=false` 是否只是匯出 metadata 計算順序；若是程式缺口，仍須先測試、再於照片邊界安全換版。
4. 使用 `tools/revalidate_completed_source_results.py` 對 `.95` 的 202109 結果先 dry-run；只套用原圖／trace／雜湊完整綁定的 2 張安全更正。不可讓 9 張證據不足者取得第 4 次模型呼叫。
5. 盤點 202109：快照顯示 `1,042/1,199`、ready `880`，但主線已進 202108。不要假設月份已完整；查明協調程式為何先進 202108，並確保剩餘 157 張與 review 欠項會自動回補，不能把「copied」狀態當成全部正確完成。
6. 繼續觀察右側縮圖、目前照片、LLM 自然語句與終局卡是否同一 identity；不得讓舊卡搶回左側預覽。
7. 只有使用者再次明確指示後才繼續修改；在此之前保持本機腳本持續跑。

## 9. 唯讀健康檢查範例

```powershell
$s = Invoke-RestMethod http://127.0.0.1:5002/api/status
[pscustomobject]@{
  running = $s.is_running
  revision = $s.evidence_guard_revision
  folder = $s.current_relative_dir
  file = $s.current_file
  stream_file = $s.stream_file
  folder_progress = "$($s.stats.processed)/$($s.stats.total)"
  verified = $s.stats.verified
  review = $s.stats.review_required
  failed = $s.stats.failed
  overall = "$($s.overall_progress.processed_images)/$($s.overall_progress.total_images)"
  uploaded = $s.stream_upload.canonical_uploaded
  pending = $s.stream_upload.pending
  upload_error = $s.stream_upload.last_error
  fuse = $s.runtime_health_fuse
  pause = $s.pipeline_pause
}
```

程序唯一性：

```powershell
Get-CimInstance Win32_Process |
  Where-Object {
    $_.CommandLine -match 'samsung-monitor-ocr' -and
    $_.CommandLine -match 'samsung_ocr_batch_processor|stream_drive_upload|recursive_ocr_flat_export|ocr_continuity_daemon'
  } |
  Select-Object ProcessId, ParentProcessId, Name, CommandLine
```

## 10. 絕對禁止

- 不得 `git reset --hard`、`git checkout --`、`git clean` 或刪除既有使用者／其他 AI 修改。
- 不得用 OpenAI／Codex 逐張 OCR。
- 不得為了讓數字動，把 review／技術錯誤冒充 verified。
- 不得用上一輪答案提示下一輪。
- 不得超過三次本機模型呼叫。
- 不得整批重跑來修復少數舊結果。
- 不得等整月完成才上傳。
- 不得開新瀏覽器視窗或新分頁。
- 不得只看 GPU、心跳或綠燈就宣稱健康；必須同時確認進度、內容、介面 identity 與上傳收據。

## 11. 本次交接結論

`.96` 已修復目前已證實的共同根因，並在隔離本機模型試跑中正確保留清楚價牌的 `F24T350FHC / 3990`。正式 Dashboard、202108 OCR、歷年接力與逐張上傳已恢復且持續增加。本次 AI 在完成此文件與 Git 後停止修改；正式本機流程保持運作，等待使用者下一次指示。

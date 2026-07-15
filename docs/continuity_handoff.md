# Samsung Monitor OCR 專案完整移交

> 更新時間：2026-07-15（Asia/Taipei）
> 專案根目錄：`D:\00_商化\samsung-monitor-ocr`
> 本文件供下一個 AI 直接接手。所有數字都可能隨執行變動，接手後必須先查 API、程序、audit 與上傳摘要，不可直接沿用本文數字。

## 1. 最終目標與不可違反的鐵律

1. 完成 `D:\00_商化\00_未整理商化照片` 全部照片的 OCR、正確命名、合理壓縮，輸出到 `D:\00_商化\00_已OCR照片`。
2. 準確率第一，速度第二。任何加速不得降低圖片解析度、關閉價牌輔助圖、縮短已迭代成熟的提示詞，或繞過複核與上傳守門。
3. 優先順序固定為最新年份優先，尤其 2026 必須先完成初次辨識、第二輪、第三輪、遠景與 FollowMe 複核，再逐年往前。
4. OCR 展示介面 `http://127.0.0.1:5000/` 在專案未完成前應持續運作；特殊原因中斷時要立即診斷與回報。
5. 不得用 `restart/no-resume`、不得清空歷史、不得覆蓋已完成資料夾、不得上傳 `review_required`。
6. 已確認無誤的照片才可上傳 Google Drive；有疑慮者立即在下一個佇列位置進第二輪，仍有疑慮立即進第三輪，最後才交慢模型或人工校正。
7. 新啟動程序前先辨識並關閉確定無用的舊程序；不得累積多組後端、runner 或 PowerShell 視窗。
8. Git 工作不得回復使用者或其他 AI 的既有修改。使用者口中的 `git` 包含更新 README、使用手冊、開發手冊、SKILL、commit 及 push。
9. 第一、第二、第三輪的三層即時守門，必須遵循 [three_layer_accuracy_gate.md](three_layer_accuracy_gate.md)；第三輪仍衝突者必須留在 `review_required`，不得冒充成功或進入上傳清單。

## 2. 路徑、服務與主要模型

- 來源：`D:\00_商化\00_未整理商化照片`
- 輸出：`D:\00_商化\00_已OCR照片`（照片本體不分月份；audit 與上傳管理資料可分資料夾）
- Audit：`D:\00_商化\00_已OCR照片\_ocr_audit`
- 暫存：`D:\00_商化\00_已OCR照片\_ocr_staging`
- 上傳狀態：`D:\00_商化\00_已OCR照片\_drive_upload`
- Dashboard/API：`http://127.0.0.1:5000/`
- LM Studio API：`http://127.0.0.1:1234/v1`
- 正式主線模型：`qwen/qwen3-vl-8b`
- 目前基準：Qwen3-VL 8B 仍是暫定主線；其他 8B/近似模型的固定 50 張盲測尚未完整形成可取代主線的正式結論。

## 3. 2026-07-14 接手時的即時狀態

### 2026-07-15 16:24 接續更新

- 原有唯一 Dashboard 分頁與 OCR 全程未中斷。兩次實際 DOM 核對從 202601 `577/1,504` 前進到 `607/1,504`，成功數相同、失敗 `0`；第二次主圖、AI 第二輪逐字判讀與右側最上方處理卡均為 `M-台南市-中西區-集雅社-台南西門-241.jpg`，右側其後保留 241 第一輪與 240/239 第三輪卡，證明進度、判讀與累積縮圖同步。
- 上傳 finalization proof 已改為內容綁定：候選 builder 摘要、候選／結果／folder summary、全年 canonical `success_records/rename_plan/copied` 與 v19.45 trace 任一變動，都會令 2026 risk audit 過期。零候選只有在「全年來源數大於 0 且全部已有 verified trace、canonical inventory 完整」時才算完成，不能用空 CSV 冒充。
- `prepare_drive_upload_manifest.py` 現在對所有 2026 row 套用全年完成守門，輸出 normalized proof、目前 audit input SHA-256、next-batch SHA-256 與 gate fail reasons。明確遠景人工核准另綁 backfill run、audit input、原圖 identity、目標內容 hash 與 approved timestamp。
- `rclone_drive_upload.py` 每輪重建 manifest 後，在 staging/rclone 前重算 batch SHA；含 2026 時還會再次核對 finalization counts、缺失／重複來源、risk freshness 與 audit hash。`ocr_upload_watchdog.ps1` 順序固定為 audit → proof → manifest → hash gate → uploader；supervisor 沒有新鮮 `upload_gate_proof.json` 也不得啟動 uploader。
- 897 筆 stale uploaded reconciliation 已把 ledger integrity、全部列已對帳、全部 replacement gate ready、可上傳新檔、可替換舊檔拆成不同布林值；`gate_blocked` 不再可能被 `safe_to_replace` 掩蓋，任何非 `new_ready` row 都不得呼叫 rclone。
- 新增 zero-candidate、canonical tamper、candidate/result mismatch、batch tamper、2026 proof 缺欄、watchdog/supervisor 順序、gate-blocked ledger、duplicate identity 與非 ready upload 拒絕測試；完整 `tools/run_critical_regressions.py` 已通過。未執行任何遠端上傳。
- 唯一安全邊界 watcher 仍為 PID 8668；此批程式只會在整個 current-year runner 自然完成、連續兩次 idle/complete/no-worker 後載入，現在不得手動重啟或提前刷新正式 risk/manifest。

### 2026-07-15 15:40 接續更新

- 15:25 唯讀稽核確認服務於 15:11 恢復後 `presentation_sequence` 從舊的約 1,030 重設為 1；前端若依序號排序，舊縮圖會壓住新卡。
- 已熱修純前端：右側縮圖、本張歷程與待顯示佇列改依 `completed_at/started_at` 排序，新卡已回到最上方；OCR 後端未重啟。
- 當前後端尚未載入持久化計數時，UI 誠實顯示「本次服務判讀」；安全邊界升級後，新後端會回報 `presentation_sequence_durable=true`，UI 才改顯示「累計判讀」。
- `_load_presentation_sequence()` 已改為依時間順序加總每個重啟區段，現有歷程離線復原為 1,080 次，不再只取最大值而漏算重啟後判讀。
- 原分頁驗證：右側最新卡已由舊 1,030 輪更新為目前新照片，10 秒內新卡增至序號 76；LLM 第三輪文字與同張照片識別同步。
- 關鍵回歸、新的重啟區段計數測試、Vite production build 全部通過。
- 上傳仍 fail-closed：現有 2026 manifest 全數 review，`ready_pending=0`、`next_batch=0`；等 202601 完成後才更新 risk audit/manifest，並處理 897 筆 stale uploaded reconciliation。
- 15:44 已補啟動唯一隱藏安全邊界 watcher（PID 8668），`model_benchmark.lock` 擁有者相符，等待上限 72 小時、每 60 秒唯讀檢查一次。它必須等 `running=false`、`processed=total`、staged/uploader 均為 0 且連續兩次成立，才可載入新後端；當前只記錄 `waiting_for_boundary`，未中斷 OCR。

### 2026-07-15 15:20 接續更新

- 三層即時守門的原理、狀態轉移、遠景／FollowMe 特例、稽核證據與必跑驗證已整理為 [three_layer_accuracy_gate.md](three_layer_accuracy_gate.md)。
- Dashboard 進度原文的 `輪 N`將改為 `累計判讀 N 次`與 `本張第 X/3 輪`；後端同步新增從 `_ocr_audit/presentation_history` 恢復最大序號，避免安全重啟後累計值歸零。原始碼與隔離 production build 已通過，線上 dist 與後端載入必須等當前 202601 跑者的安全邊界，不得為了部署文字而中斷 OCR。
- 每五分鐘的 `SamsungOCR_UserContinuityEnsure` 已改用 `wscript.exe //B` 無主控台啟動；自我修復能力保留，不再由可見 PowerShell 視窗執行。
- 15:10 後端與原接力 runner 離開，但 staging、成功 JSON、retry queue 與 evidence trace 均完整；已用原 `resume-existing-then-continue` 路徑恢復，202601 從 `501/1,504`附近續跑，失敗 `0`，未從零重跑。

### 2026-07-14 15:16 接續更新

- Dashboard 已由使用者實際開啟於 `http://127.0.0.1:5000/`；後端與 LM Studio listener 持續存在，沒有因前端修正或測試中斷。
- 202604 已前進至約 `230/366`，成功 `230`、失敗 `0`；即時數字仍會繼續變動。
- 使用者回報的兩個 UI 回歸已修正並以無空窗方式更新前端：AI 區不再顯示 `第 未提供 輪 · 未提供 · 未提供`，右側結果卡不再展開 `model_id`、開始/完成時間、複核原因、上一輪摘要等內部欄位。完整歷程只在點開照片後按需顯示。
- 正式前端入口目前指向 `dashboard/dist/assets/index-BUPjyTM8.js`；舊資源仍保留，更新時先放資源、最後切 index，沒有重啟 OCR。
- 舊後端 `/api/status` 目前仍傳 200 筆 presentation 與 50 筆 `recent_results`，單次約 `6.78 MB`。新版來源已改為 `compact-v2`：live window 最多 12 筆、不得含 base64/raw evidence，完整歷程改走 `/api/presentation_history/<source_item_id>`，守門驗證要求整包小於 500 KB。
- `history API`、穩定 `source_item_id`、每輪 pass metadata、evidence trace 位置、上傳來源映射與 category-specific pass gate 均已完成；完整 critical regression、500-item soak、PowerShell parser 與獨立 Vite build 已通過。
- 背景安全切換器已啟動，lock 為 `_ocr_audit/model_benchmark.lock`、purpose=`backend_upgrade_v1945`。它必須等待整個 current-year watcher 結束，再連續兩次確認 API idle、processed=total、無 staged/recursive/uploader，先將 repo-root v19.45 trace 以唯一原圖身分原子遷移至 `_ocr_audit`，才會依 port 5000 listener 與 repo-owned process tree 切換後端。compact/history/fingerprint 驗證後還會先啟動 2026 全年 evidence backfill，才釋放 lock；任何驗證失敗都保留 lock。
- C 槽約 349.93 GB、D 槽約 365.62 GB 可用，先前 C 槽低空間風險已解除；仍不可刪除 audit、transaction、來源、輸出或上傳 receipt。

### OCR

- 後端版本：`v19.45 (accuracy-first evidence contract)`。
- API 正常，`is_running=true`。
- 正在 202604 暫存批次：`_ocr_staging\20260714_064043\202604_商化照片-202604_a6dfe521`。
- 查核時目前檔案為 `M-台北市-中山區-TK3C-大直-1165.jpg`；檔案會持續變動。
- 查核前批次約為 59/366、成功 59、失敗 0；接手後必須重新查數字。
- 全域唯一初次辨識進度仍顯示 65,331/150,321（43.46%，44/136 資料夾）。這個計數只算「新的初次辨識」，第二、三輪不增加，因此長時間複核時看似停住不等於後端停住。
- `presentation_queue` 上限/當時數量為 200。
- 202605 current-year 第一輪可疑重跑已完成：178 張，救回 FollowMe 遠景 6 張、正式複製 4 張、無中止；摘要：`_ocr_audit\questionable_rerun_summary_current_year_first_pass_20260714_051805.csv`。
- 目前已接續 202604；不得恢復舊年份主線搶占 2026。

### 程序

- 接手查核時有 continuity daemon、唯一一組後端父/子程序與唯一一組 rerun runner 父/子程序。
- 不要只看父 PID；Windows 上 Python 包裝器會產生子程序。要用命令列、父子關係、5000 listener 與 lock 一起判斷。
- 若 API 還在換檔、audit mtime 還在變，不得因全域計數不動而重啟。

### Google Drive

- 最後一份摘要產生於 2026-07-14 00:16，接手後應立即重建：
  - total_images：65,669
  - ready：51,459
  - uploaded_skipped：51,459
  - ready_pending：0
  - review_required：14,210
  - stale_uploaded_review_required：897
- 因 `ready_pending=0`，當時沒有 rclone/uploader 是合理狀態，不是卡住。
- `current_year_risk_audit_fresh=false`，表示 2026 複核後必須重跑風險 audit 與 manifest，不能沿用這份摘要放行。
- 最近 review split 是舊快照（2026-07-11）：missing model/price 9,187、需價格比對 3,700、2026 遠景需重跑 576、FollowMe/遠景風險 7。僅供方向參考，不可當現況。

### 磁碟與環境

- 查核時 C 槽僅約 5.75 GB 可用，屬高風險；D 槽約 392.6 GB 可用。
- 曾出現大量重跑時整個資料夾搬入備份，快速耗盡空間；後續只能保存必要 transaction/audit，不可複製整批既有輸出。

## 4. 已完成的重要修正

1. 建立遞迴 OCR、平面輸出、resume、資料夾 audit、命名規劃、衝突與回復資料。
2. 2025（含）以前不做官網價格比較；2026 與未來年份才需比較 Samsung 官網參考價，找不到再查 PChome 24h（非商城），仍無法確認則停止放行並進 review。
3. FollowMe 已有參考表與硬規則；Odyssey Ark 55 吋大型直立/曲面桌上機規則對應 `S55BG970NC`，禁止借用旁邊小螢幕標籤。
4. 它牌辨識規格：無型號重辨識後若確認非 Samsung，型號欄使用 `它牌(品牌)`，例如 `它牌(ACER)`，不需它牌完整型號。
5. 建立 ready/review 上傳守門、Google Drive 分年上傳、續傳紀錄與 stale-upload 風險欄位。
6. 建立人工校正資料、規則資料集、提示詞候選與回歸工具；但人工校正不等於模型權重訓練，正式流程是否完整熱載入規則仍需核對。
7. 建立 continuity daemon/supervisor、Windows 啟動腳本、一般使用者 BAT/PowerShell 啟動流程。
8. UI 曾多輪修正：正式用語改為「AI 即時判讀」「AI 判讀紀錄」，禁止顯示「自言自語」與面向使用者的「LLM」；右側按鈕稱「再辨識」。

## 5. OCR 分類與命名規則

### 遠景與單機

- 三台以上「完整螢幕」入鏡，且無法讀取唯一主角自己的規格與價格：遠景。
- 畫面雖有三台，但只有中間一台完整呈現、其餘只是部分入鏡，且有唯一主角：單機。
- 多規格牌不等於遠景；有唯一主螢幕、側標、實體價牌或 FollowMe 結構時仍應判單機/FollowMe。
- 遠景不應帶型號與價格，檔名在地點後接流水號。
- `照片不清楚` 只能用於影像本身確實無法辨認，不可把清楚的多台陳列誤標為不清楚。

### FollowMe

- 必須以同一台實機的實體證據判斷：白色長直立支架、圓形/移動底座、托盤、實體 FollowMe 標示與正確歸屬。
- 背景海報、旁邊立牌、螢幕播放內容或遠處的 FollowMe 字樣，不能借給主角。
- 反過來，畫面中真正的 FollowMe 實機不能因背景有多台螢幕就直接判遠景。
- FollowMe 與 FollowMe Pro 必須分清，不能只因 43 吋或行銷字樣推成 Pro。

### 型號與價格

- 側標或同一台實體價牌優先；不可借旁邊商品、背景廣告、螢幕畫面或遊戲內數字。
- 型號須通過 Samsung 型號/產品資料驗證；測試字串如 `SXXTEST001` 絕不可進正式輸出。
- 手寫出清價、促銷價若清楚屬於主角，應視為店內價格。
- 2026 檔名價格比較符號使用 `↑`、`↓`、`✓`；未知不得自行創造「停產」。無法取得參考價時進 review/詢問使用者。
- 2025 以前不比較參考價，因此不應顯示比較符號或紅色問號。

### 輸出

- 新照片需保留足夠 OCR 可讀性並壓到合理大小；不得為速度把 OCR 輸入降到 1280px 或關閉價牌輔助圖。
- 檔名規劃、輸出複製與上傳必須使用同一份結構化結果，避免 UI 正確但檔名錯誤。

## 6. 立即第二、第三輪的正確流程

1. 第 1 輪若有疑慮，該照片立即插入「下一個佇列位置」做第 2 輪，不是等 15 萬張全跑完。
2. 第 2 輪可使用該照片一次性短 context：完整人工規則、第一輪答案與疑點，要求逐項推翻或確認。
3. 第 2 輪仍有疑慮，立即插入下一格做第 3 輪。
4. 第 3 輪必須使用全新 context，避免沿用第一輪錯答造成自我合理化。
5. 程式比較三輪結構化欄位；只有一致或證據充分才放行，否則交較慢候選模型或人工校正。
6. 單張照片的短期 context 完成後立即丟棄，不得把上一張帶到下一張，也不是訓練模型權重。
7. 2026 遠景、無型號、無價格、FollowMe 線索、型號不合法、價牌歸屬矛盾與影像品質疑慮都應進複核。

## 7. UI 的強制同步契約

這是反覆回歸的重大問題，任何 UI 修改後都必須驗證：

1. 每一輪有獨立 immutable `presentation_id`，同一張照片各輪共享 `source_item_id`。
2. 照片、該輪 AI 文字、處理中 placeholder、右側結果卡、放大 modal 必須全部來自同一 presentation snapshot。
3. 固定視覺順序：照片出現 → AI 即時判讀逐字顯示 → 文字完成後才把同一張結果卡加入右側。
4. 後端可預先處理下一張，但下一張不可提前出現在右側；利用前後張時間差維持連續展示，不黑屏、不變暗、不放大小縮圖。
5. 右側只顯示已 reveal 的結果，執行中不得 fallback 到 `recent_results` 插隊。
6. 禁止用 filename、index 或模糊 source path 合併 metadata；只能依穩定 key。
7. 長時間需處理 duplicate、out-of-order、overflow、remount；UI 記憶體只留近期，完整每輪 AI 文字寫 append-only audit，再按 `source_item_id` 讀取歷史。
8. 右側固定在主畫面右方，寬度需足以辨識檔名但不可過寬；本介面為有獨立顯示卡的桌機展示，不必設計手機堆疊。
9. 放大檢視需支援原圖 1:1、拖曳與合理縮放，不能只顯示縮小圖，也不能拉動後版面錯位。
10. 每次建置後必須實際重整瀏覽器並做至少 500-item soak；不能只看 build 成功。
11. **身分優先於新鮮度**：只要畫面已有 active presentation，判讀區必須先取該 presentation 的文字；禁止用下一張的 live stream、latest result 或歷史文字覆蓋。
12. 主照片也必須記錄自己的 presentation key。舊照片可以在「舊 presentation 仍為 active」時保留以避免黑屏；一旦 active key 前進，舊照片必須隱藏，直到同 key 的新圖載入成功。載入失敗時顯示該筆錯誤狀態，絕不可顯示舊照片配新文字。
13. 完成事件的判讀文字優先取同一結果的完整 `thinking` / `full_ai_narration`；一句「已完成辨識」只能當最後退路，且模型未回傳完整文字時必須明說，不得偽裝成完整判讀。
14. 永久回歸測試必須同時驗證：主照片、判讀區、處理中卡與右側結果卡的 `presentation_id` 相同；禁止 `live-first` 與任何跨照片 history fallback。

## 8. 本次部署狀態

以下來源修改已完成與通過測試，但後端仍要等安全邊界才會載入：

- `skills/batch_orchestrator.py`：每輪 immutable event、穩定 `source_item_id`、pass metadata、上一輪摘要、append-only JSONL/gzip history、來源映射與無影像 public history 已完成。
- `samsung_ocr_batch_processor.py`：history API、review progress、`compact-v2` status、12 筆 presentation window 與精簡 recent results 已完成，尚未由目前 live 後端載入。
- `dashboard/src/App.jsx` 與 `dashboard/dist`：正式中文、按需歷程、缺值輪次隱藏、右側卡片清理與 2 秒 legacy polling 已部署；目前可相容舊後端。
- `tools/safe_backend_boundary_upgrade.ps1` 正在等待整個 active staged runner 自然結束，不會在月份切換時搶停。邊界成立後會先執行 `tools/migrate_legacy_v1945_trace.py --execute`；目前真實 trace dry-run 已達 1,437/1,437、unresolved=0、ambiguous=0，但正式檔只會在 trace 停止增長後寫入。安全切換後需再次量測 status payload、history route，並觀察至少 30 張與第 2/3 輪插隊。
- 2026 `copied.csv` 已盤點 5,951 個唯一原圖身分，缺檔=0、衝突=0、歧義=0；`tools/build_v1945_evidence_backfill.py` dry-run 產生 5,951 筆待補證據。平面輸出額外 123 張皆已以 SHA-256 證明為 202605 舊命名副本，暫不刪除，待 Drive stale reconciliation 完成後再處理。
- 2026-07-14 重新確認「live stream 優先」會把下一張文字配到仍在畫面的上一張照片，因此已永久撤銷。現在固定為 active presentation 優先，照片載入也用同一 key 守門；後端完成事件優先攜帶同一結果的完整判讀，前端禁止跨照片 history fallback。另已修正「無法讀取唯一主角自己的規格」因否定詞視窗過短而被反轉成單機的問題，並新增 staged merge 的 `structured_narration_conflict` fail-closed 守門。這些變更不需中斷目前 OCR；後端部分會在既有安全邊界升級後載入。
- 2026-07-15 低耗用 111 秒 live 取樣發現，後端 `current_file` 在新 presentation 建立前會早約 15 秒切到下一張，使頂部「目前檔案」短暫與中央照片／AI 判讀不同步。磁碟版已將該檔名改為同一 visible presentation 身分優先，只在無可見 presentation 時才 fallback 至 `current_file`；不改 50/50 版面，已通過 500-item soak 與獨立暫存 production build，於既有安全邊界部署後再做 live 連續交接驗證。
- 同日晚間以既有 Chrome Dashboard 分頁完成連續交接驗證：4 個時間點中，完成事件的 photo key、image key、narration source、photo/narration presentation ID 均相同；播放中的處理卡也與同一 `presentation_id` 相同，`data-presentation-invariant=ok`，預覽與判讀區高度各約 395/394 px，仍維持左側各半版面。測試過程未開新視窗，先前 3 個額外 Chrome 測試設定檔已刪除。
- 202604 第一輪進行到 325/366 時，新守門器已唯讀抓到 17 筆 `structured_narration_conflict`。因當前 legacy runner 啟動時已載入舊規則，已在 `D:\00_商化\00_已OCR照片\_safety_snapshots\20260714_pre_202604_first_pass_conflict_guard` 保存完整 audit 與 366 個既有平面輸出；舊 watcher 已移除，`tools/protect_staged_conflict_handoff.ps1` 正等待 legacy runner 自然結束，屆時會隔離舊 merge、以 SHA-256 還原安全快照，再啟動新版第二/第三輪 watcher。後端與模型程序未停止。
- 15:47 原 `current_year_first_pass` staged wrapper 因一次 Python traceback 提前退出，但 port 5000 後端仍健康處理 202604，故沒有重啟。已啟動 `--resume-existing-then-continue`：依 period+來源 digest 附著 202604、跳過已完成 202605，之後只續接 202603/202602/202601；另啟動帶 `-SkipCurrentYearFirstPass -AllowPlannedBackendUpgradeInterlock -SkipRecursiveResume` 的 current-year watcher，第一輪完成後只做第二輪、第三輪與遠景/FollowMe review，最後把 idle 邊界交給現有安全升級器。暫時性 status API 錯誤現可連續重試 6 次，其他鎖仍 fail-closed。
- 舊 `drive_correction_reconciliation.jsonl` 共 897 筆，全部仍帶舊 gate 且部分路徑已 mojibake，已隔離為 `_drive_upload\drive_correction_reconciliation.pre_v1945_mojibake_20260714.jsonl`，目前正式 ledger 路徑不存在，禁止拿隔離檔操作 Drive。新 `tools/build_drive_correction_reconciliation.py` 以 UTF-8 copied 原圖身分、目前輸出 SHA-256、fresh manifest 與唯一上傳 ID 重建；現況 dry-run 可映射 893 筆、4 筆因 manifest 尚未刷新而 fail-closed，預估 50 筆需改名替換、847 筆同名只需 hash 驗證、830 筆需用只讀 `discover-old` 取得唯一 Drive ID。必須等 v19.45 backfill 與 manifest fresh 後再 execute。
- 模型 sidecar 原 Windows 程序命令使用錯誤的 `%%`，實測回傳 0 筆而可能漏掉 idle API 下仍存活的 watcher/runner；現已改成 UTF-8 JSON CIM 清單，命令失敗或 JSON 無法解析即 fail closed，並在原子 lock 前後各重查一次。實機唯讀驗證目前能看見 6 個專案程序及至少 watcher/staged runner；沒有啟動 benchmark 或切換模型。中文 `遠景` 的 FollowMe 錯判也已納入 danger score。
- Sidecar raw row 原先把候選 VLM ID 寫在 `model` 後又被產品型號覆蓋，會使多模型結果無法可靠隔離；現改為 `candidate_model`。`model_benchmark_score.py` 已升級 v2，缺失、重複、未知 case、混模、parse/inference failure 都保留在固定 50 張分母並令 `benchmark_gate_pass=false`；只有 protocol 完整後才可比較 field/exact accuracy 與 latency。
- 50 張 manifest 已升級 v2，含 labels、每張圖與 canonical case-set SHA-256；已實際驗證 50/50 圖檔指紋。sidecar 每個 case 只準備一次全圖/crops，將 prompt 與解碼 evidence 組成 `input_fingerprint`，raw row 同時記錄 manifest/case-set/prompt/image 指紋。舊結果缺指紋、重複 candidate/case、candidate/key 不一致或任一輸入漂移時會拒絕 resume；完成的候選不會再切模，恢復時使用執行前 baseline context。已用專案 Python 3.12.13 通過 manifest 2、sidecar 11、scorer 3 項測試；沒有執行 benchmark 或切換模型。
- 2026-07-15 16:40 真實資料抽查確認：當時 639 筆完成判讀中，325 筆為 `auto_verified=true`、314 筆為 `auto_review_required=true`；舊 API/UI 把兩者合稱「成功」會誤導。磁碟版已新增 `verified/review_required/verification_unknown`，UI 不改 50/50 版面，只改稱「完成判讀」並在既有統計格顯示「待複核」。Label-Studio JSON reload 現會保留驗證旗標。
- 同次抽查找到一個已漏網案例：`M-台南市-南　區-TK3C-灣裡-1566.jpg` 的 Samsung `S27D392GAC/4290` 因敘述含「螢幕顯示 ASUS Demo 畫面」被覆蓋為 `它牌(ASUS)` 並自動通過。磁碟版已禁止用畫面內容覆蓋硬體 SKU；若真正的主體品牌敘述與 Samsung SKU 衝突則 fail closed。
- 三層守門另補齊四條可重現漏洞：單機結構／明示遠景敘述衝突、`view_type/category` 衝突、`label_ownership=matched`／鄰機價牌敘述衝突、FollowMe 正式 SKU 繞過實體證據。遠景若仍帶同主體 FollowMe 強實體線索或 `S32FM50x/S32FM70x/S43FM70x` 文字也不得通過。官方參考價差達 20% 以上需獨立重讀一次，但兩輪同型號、同照片價格且價牌歸屬一致時保留實際店價，不用官網價覆蓋照片。
- 新三層守門的實際規則身分為 `evidence_guard_revision=20260715.2`。單有 `v19.45 verified` 但缺少這個修訂碼的舊 live trace 不具新版驗證效力；安全邊界的 backfill builder 會將這些原圖全部重新列入候選，Drive manifest 也會失敗封閉。舊 trace 遷移不得偽造新修訂碼。
- 2026-07-15 新修訂碼 dry-run 已用正式 `_ocr_audit` 驗證：5,951/5,951 個唯一原圖身分全數列入新守門 backfill，舊規則已驗證數 0，缺檔 0、衝突 0、無效列 0。這是舊 live `v19.45` 結果沒有被誤算為新規則完成的實際證明。
- 上述磁碟變更尚未載入目前 active OCR；不曾重啟後端、建置 live `dashboard/dist` 或刷新使用者頁面。`tools/windows_user_launcher.ps1` 現會在既有安全邊界重啟時偵測 `dashboard/src` 比 `dist` 新並自動建置，因此新版後端計數與前端標示會一起套用，不會在 OCR 執行中途出現新舊契約不一致。完整 critical regression、PowerShell parser、Python compile 與不碰 live dist 的暫存 Vite build 均已通過。

## 9. 已知未解決與重大風險

1. **準確率仍未達標**：使用者抽查仍發現 FollowMe/Pro、遠景/單機、清楚/不清楚、價牌歸屬、非法型號等錯誤。
2. **遠景錯誤比例曾很高**：2026 已上傳遠景也需 stale audit；不能因已上傳就視為正確。
3. **UI 同步仍需 live soak**：每輪持久事件、history API 與 deterministic 500-item soak 已完成；待安全切換後仍需實際觀察至少 30 張與第 2/3 輪插隊。
4. **全域進度語意不完整**：65,331 是唯一初次辨識，不含複核。UI 應同時顯示初次總進度、目前資料夾、目前輪次與複核進度。
5. **人工校正學習鏈需核對**：CSV 有資料不代表正式 OCR 已讀取；需確認通用人工規則熱載入、離線回歸與污染防護均真正接線。
6. **模型 benchmark 未正式完成**：必須固定 50 張困難盲測，同 prompt、同影像、temperature 0，比較分類/型號/價格/FollowMe/遠景與耗時；未通過不可換主線。
7. **價格來源與合法型號驗證**：LLM 結果曾被後處理清空或出現虛構型號，需查明資料載入與 validation 是否在每輪一致執行。
8. **上傳 stale 資料**：897 張已上傳但後來變 review；要有可追溯的替換/刪除/重新上傳流程，不可直接忽略。
9. **空間規則仍要保留**：C/D 目前空間充足，但不可因此刪除 audit、transaction、source、output 或上傳 receipt。
10. **關鍵測試已修復**：immediate retry fixture 已補齊 v19.45 evidence，critical regression、history API、500-item soak、upload guard 與 dashboard build 皆已通過；後續不得放寬正式守門。

## 10. Sandbox/權限事件的確切原因

- `D:\00_商化` 的 Windows 擁有者是另一帳號 `DESKTOP-22BTNQH\smart`；目前帳號 `samla` 有一般修改權，但沒有變更 ACL 的 `WRITE_DAC` 權限。
- 2026-07-13 晚間到 2026-07-14 清晨，Codex sandbox 設定嘗試對工作區加入 ACE，Windows 回覆 `SetNamedSecurityInfoW failed: 5`。
- 以前沒發生，是因先前執行設定沒有要求改工作區 ACL；權限差異原本一直存在，只在新 sandbox profile 啟用時被觸發。
- 2026-07-14 08:01 起目前環境改為 unrestricted/disabled，沒有再嘗試改 ACL，因此工具恢復。
- 這不是 OCR 程式錯誤，也不是專案目標被封鎖；目標仍為 active。不要在目前正常運作時任意改 ACL。

## 11. 下一個 AI 的建議接手順序

1. 先查 `api/status` 兩次（間隔約 60 秒）、5000/1234 listener、程序父子關係、lock、目前檔案與 audit mtime；正常就不要中斷。
2. 確認仍在 2026-first 接力，禁止舊年份 runner 搶占。
3. 確認 `safe_backend_boundary_upgrade` guard 仍在等待，不得手動刪除其 interlock 或中途重啟後端。
4. 安全切換完成後確認 `status_contract_version=compact-v2`、status <500 KB、queue <=24、history API 可讀、前端指紋一致。
5. 連續觀察至少 30 張與第 2/3 輪插隊；照片、AI 文字、右側卡片與 modal 的 presentation id 必須一致，且不得再出現缺值輪次或內部欄位。
6. 2026 每批完成後重跑 distant/FollowMe risk audit、manifest 與 review split；只有 fresh ready 可上傳。
7. 處理 stale uploaded 2026 遠景：重新辨識、更新檔名，並安全替換雲端錯檔。
8. 用固定 50 張盲測完成 Qwen3-VL 8B、Gemma 4 12B QAT、Qwen3.5 9B VLM、MiniCPM/InternVL 等候選比較；準確率優先。
9. 2026 全部完成、複核、上傳後，依 2025、2024、2023……往前處理。

## 12. Git 與檔案注意事項

- 工作樹含大量跨多日修改與新檔，不可 reset 或 checkout 回退。
- 不要提交 runtime `logs/`、根目錄 `v1945_evidence_trace.jsonl`、大型照片、憑證或本機密鑰。
- 應提交程式、測試、README、開發手冊、SKILL、正式 dashboard build 與本移交文件。
- 提交前檢查 `.gitignore`、`git diff --check`、測試結果與是否有不應上傳的生成檔。

## 13. 接手後首先應讀的文件

- `README.md`
- `docs/development_guide.md`
- `SAMSUNG_OCR_EXPERIENCE_SKILL.md`
- `docs/ai_handoff_runbook.md`
- `docs/manual_learning_pipeline.md`
- `docs/model_benchmark_20260713.md`
- 本文件 `docs/continuity_handoff.md`

本專案尚未完成。接手者的核心責任不是追求表面跑得快，而是讓每張照片的分類、型號、價格、檔名、UI 展示與雲端檔案保持同一份可追溯的正確結果。

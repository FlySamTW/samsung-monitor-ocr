# Samsung Monitor OCR 專案完整移交

> 更新時間：2026-07-16（Asia/Taipei）
> 專案根目錄：`D:\00_商化\samsung-monitor-ocr`
> 本文件供下一個 AI 直接接手。所有數字都可能隨執行變動，接手後必須先查 API、程序、audit 與上傳摘要，不可直接沿用本文數字。

## 1. 最終目標與不可違反的鐵律

1. 完成 `D:\00_商化\00_未整理商化照片` 全部照片的 OCR、正確命名、合理壓縮，輸出到 `D:\00_商化\00_已OCR照片`。
2. 準確率第一，速度第二。任何加速不得降低圖片解析度、關閉價牌輔助圖、縮短已迭代成熟的提示詞，或繞過複核與上傳守門。
3. 優先順序固定為最新年份優先。每張在同一流程內完成所需的第一／二／三輪，定案後立即排入上傳；不得等全年複核完成才開始上傳。
4. OCR 展示介面目前為 `http://127.0.0.1:5002/`，在專案未完成前應持續運作；特殊原因中斷時要立即診斷與回報。不得另開瀏覽器視窗或分頁。
5. 不得清空歷史、不得將前輪答案驅入後輪、不得用舊守門結果冒充 `.22` 完成。安全重跑前必須先快照舊證據，再從同一原圖無記憶重做。
6. 有疑慮者立即在下一個佇列位置進第二輪，仍有疑慮立即進第三輪。三輪後由結構化證據自動定案；遠景、單機只有型號、只有價格或兩者都無，都是有效結果並須上傳。沒有慢模型或人工最終裁決佇列。
7. 新啟動程序前先辨識並關閉確定無用的舊程序；不得累積多組後端、runner 或 PowerShell 視窗。
8. Git 工作不得回復使用者或其他 AI 的既有修改。使用者口中的 `git` 包含更新 README、使用手冊、開發手冊、SKILL、commit 及 push。
9. 第一、第二、第三輪的三層即時守門，必須遵循 [three_layer_accuracy_gate.md](three_layer_accuracy_gate.md)。內容不一致由 `.25` 定案器保守收斂；每張照片總模型呼叫硬上限為三次，只有請求綁定、跨圖汙染、系統輸出或 Drive 回讀等技術錯誤可不上傳該張。

### 2026-07-16 `.22` 三次呼叫硬上限與介面續航修正

- 根因已確認：舊程式把 `max_total_attempts` 預設成 `max_auto_attempts + 3`，使 1385 跑到第 4 次、1386 跑到第 6 次。`.22` 將總模型呼叫永久鎖在 3，呼叫前持久化額度，舊佇列與重啟也不能偷跑第 4 次；技術錯誤不產生新業務輪次卡。
- 1385 已定案並上傳為遠景；1386 已定案並上傳為遠景。兩者都有 Drive size+MD5 精確回讀 receipt。歷史 trace 保留真實的舊 4/6 次證據，但 Dashboard 只呈現「三輪完成」，新資料不再產生 pass 4–6。
- 介面修正版已回到原地址 `http://127.0.0.1:5002/`，維持既有分頁；上方資訊列改為固定四區 grid，禁止全案進度、資料夾／複核進度、上傳總數與狀態互相覆蓋。50/50 左側照片／LLM 區與右側累積卡片版面不得改動。
- 部署鐵律：先修、測試、build、啟動隱藏 green backend 並驗證，再切換原地址；不得先把唯一正式 OCR 停成 `待機中`。切換完成必須證明原地址 `is_running=true`、進度前進、唯一 backend、唯一 uploader、fingerprint 正確，才算完成。
- FollowMe 遠景救援已增訂不可退回規則：兩輪同主體白色直立支架、圓形底座、託盤等強證據可確認 FollowMe 家族；若 M5／M7／Pro 無兩輪共識，使用 `FollowMe 型號未細分`，不可判回遠景或猜版本。
- 同類 FollowMe 敘述／結構矛盾跨不同照片重複，只記錄為模型弱點監控，不再誤判為跨照片記憶汙染而停止整批。只有前輪答案外洩、跨照片複製身分、提示詞汙染、request/image 綁定失敗等直接技術證據可熔斷整批。
- `.23` 修正三輪內容已完成卻被標成技術錯誤的漏洞：若三輪皆同圖、無記憶汙染且健康，但模型曾把只有 1–2 台完整螢幕的照片判成遠景，無安全視角共識時保守定案為 `單機／無型號／無價格`，立即逐張上傳；不得留下永久未上傳洞。
- `.25` 同時防止裁切鄰機被多算與真正遠景被少算：完整台數只看第一張原始全圖，外框四邊四角都在原圖內才算；任何碰到／穿出原圖邊界的螢幕不計，補充裁切不得新增或重複計數。每輪必須先依左／中／右、上／中／下掃完整張原圖，把中央之外、上／下排、遠處及其他展示架的完整螢幕全部計入。中央完整、左右裁切只有在其他區域沒有任何完整螢幕時才是 1 台單機候選。正式恢復前必須以裁邊單機、真正遠景、FollowMe 前景三種實照同時通過回歸。
- 使用者確認案例 `M-台中市-大里區-SF-大里-632.jpg`：原圖明確具有 FollowMe 白色直立支架、圓形底座與託盤；本機結果為 `單機-S32FM703UC-✓＄12990`。2026-07-16 已將 Drive 同一檔案 ID `15oJjYC0-ciEoHYmV4vfIxUup8QaWOsCj` 從舊 `遠景-632` 原地更名，大小 794069、MD5 `449d36246ce410ecb6c378074fa04903` 回讀一致，沒有 `_2`。

### 2026-07-16 `.21` 實際部署

- 舊 `.19` 正式批次在 76/1,500 時由跨照片內容健康熔斷停住，沒有繼續製造錯誤或上傳舊結果。該 76 張與 fuse 已完整搬到 `D:\00_商化\00_已OCR照片\_safety_snapshots\rev19_before_rev20_20260716_165025`。
- `.21` 已從同一批 1,500 張原圖無舊答案重做，當前介面以藍綠部署使用 `http://127.0.0.1:5002/`；使用者的原 Chrome 分頁原地切換，沒有新開視窗或分頁。穩定後可在下一個安全邊界回到 canonical 5000，不得同時跑兩個後端。
- 實際端到端驗證已成功：`.21` 前兩張均自動驗證，0 張待裁決；逐張 worker 已產生 2 份遠端 size+MD5 回讀收據，canonical 上傳紀錄由 52,965 增至 52,967。
- Dashboard 必須維持 50/50 左側照片／LLM 逐字判讀佈局、右側累積縮圖、全案 65,331/150,321 總數、資料夾 44/136 與本批次輪次進度。禁止顯示任何慢模型、人工裁決、已隔離或不會上傳的內容判讀文字。
- `tools/stream_drive_upload.py` 是逐張上傳 worker。工作只能在隱藏程序中單工執行；同名舊檔內容不同時精確覆寫，不另存 `_2`，並必須回讀 size+MD5 完全一致才寫入上傳收據。

## 2. 路徑、服務與主要模型

- 來源：`D:\00_商化\00_未整理商化照片`
- 輸出：`D:\00_商化\00_已OCR照片`（照片本體不分月份；audit 與上傳管理資料可分資料夾）
- Audit：`D:\00_商化\00_已OCR照片\_ocr_audit`
- 暫存：`D:\00_商化\00_已OCR照片\_ocr_staging`
- 上傳狀態：`D:\00_商化\00_已OCR照片\_drive_upload`
- Dashboard/API：`http://127.0.0.1:5002/`
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
- 新三層守門的實際規則身分為 `evidence_guard_revision=20260715.5`。單有 `v19.45 verified` 但缺少這個修訂碼的舊 live trace 不具新版驗證效力；安全邊界的 backfill builder 會將這些原圖全部重新列入候選，Drive manifest 也會失敗封閉。舊 trace 遷移不得偽造新修訂碼。`.5` 包含 `.3` 的品牌衝突防護與 `.4` 的 `台南三井-330/331` FollowMe 修正，並新增跨輪污染熔斷。
- 2026-07-15 18:20 後因使用者從 `彰化中山-232` 畫面發現第二輪沿用 `17,990`，已主動停止 OCR；後端確認 `is_running=false`，未啟動 uploader。唯讀稽核覆蓋現行 run 650 輪、329 張照片：166/166 個第二輪都曾收到上一輪 assistant payload；12 張有「您指正／感謝提醒／先前」等明確污染語句，其中 4 筆曾被標為 verified；另有 14 輪原始遠景被後處理注入 FollowMe。現行 run 全部不得作為 `.5` 完成證明，5,951 張 2026 原圖均由新 revision 重建。
- `.5` 已移除四條記憶路徑：第二輪 previous-result payload、同一輪無效格式回應回餵、價格衝突值回餵、正式 OCR 的歷史錯題本注入。第二、第三輪只保留固定規則與當前照片；價格衝突交由下一個完全獨立 pass，而不是把錯誤值告訴模型。`skills/runtime_health_gate.py` 已接入正式 prompt 與 batch loop；內容或介面健康失敗會設定 stop event，且 `allow_upload=false`。
- 每三小時監控已更新為四維健康檢查：進度、內容品質、介面同步、上傳隔離；健康閘停止後不得自行續跑。裸 JSON 不再進入 LLM 顯示／歷程，前端改顯示可讀中文狀態。完整 critical regression 與獨立 production build 已通過。
- 將 `.3` 對目前 3,437 筆真實 trace 全量重算後，共找到 14 次、13 張不同照片的「原始 JSON 是 Samsung SKU、最終變它牌」衝突，其中 9 張舊結果曾被標為通過；`.3` 對 13 張全部改為 retry 或 unresolved，0 張繼續 verified。擴大比對所有原始／最終型號後，另計 201 次、106 張型號不相容事件與 1 張價格改寫事件，`.3` 對這些事件的 verified 數同樣均為 0。同次 backfill dry-run 掃描 63,876 筆 copied rows，確認 2026 唯一原圖 5,951、舊修訂已驗證 0、新修訂候選 5,951、缺檔 0、衝突 0、無效列 0；因此舊結果沒有被誤算為新守門完成。
- 同一批 trace 的結構欄位漂移稽核另找到 797 次原始／最終 `view_type` 變化，但舊修訂驗證通過數為 0；`complete_screen_count`、`unique_main`、`label_ownership`、`followme_physical_evidence` 的後處理改寫數均為 0。因此目前沒有再發現可通過的結構證據漏洞。
- 2026-07-15 新修訂碼 dry-run 已用正式 `_ocr_audit` 驗證：5,951/5,951 個唯一原圖身分全數列入新守門 backfill，舊規則已驗證數 0，缺檔 0、衝突 0、無效列 0。這是舊 live `v19.45` 結果沒有被誤算為新規則完成的實際證明。
- 2026-07-15 接力鏈唯讀稽核發現並已加固四條邊界後漏洞：新 boundary 必須等 backfill 完成且驗證數=原圖數才解 lock；目前已在執行、無法載入新腳本的 PID 8668 則由每 5 分鐘 supervisor 以 current guard revision 重建剩餘候選並自癒續跑。Manifest 或 review split 非零、proof 過期／雜湊不符均不得寫 current-year marker 或啟 uploader；中間輪次不得上傳，必須所有規定複核結束、uploader 結束、manifest/proof 再建且 pending=0 才允許歷史年度。歷史 recursive 任一資料匣 `error/blocked/缺 summary/來源變動` 會非零結束，full marker 必須綁定當前 discovery/summary SHA-256 與 error=0，否則 supervisor 繼續補跑。
- 2026-07-15 17:42 以原有內建瀏覽器完成連續即時取樣：複核 749→752、右側累積卡 100→101，每次主圖、逐字判讀與處理中卡的 live key 一致。同時發現舊 completed presentation 在 live stream 接管畫面後可能殘留假 `presentation stalled` 內部警示；磁碟版 watchdog 已改為只監看當前真正擁有判讀畫面的 key，live key 前進時會清除舊 stalled 而不會隱藏真實 key divergence。新增永久回歸測試，20 項 presentation soak 與不碰 live dist 的暫存 production build 皆通過；待既有安全邊界部署。
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

新版內容健康門不只設定記憶體 stop event，還會原子寫入 `_ocr_audit/runtime_health_fuse.json`。後端續跑 API、五分鐘 continuity supervisor、upload watchdog、manifest／proof builder 與 rclone uploader 都必須在此標記存在時失敗封閉。排程不得自動刪除；只能在缺陷修正、critical regressions 與隔離五張試跑全部通過後手動解除。

本專案尚未完成。接手者的核心責任不是追求表面跑得快，而是讓每張照片的分類、型號、價格、檔名、UI 展示與雲端檔案保持同一份可追溯的正確結果。

## 2026-07-15 20:15 接手狀態補充

- 正式 OCR 仍維持 fail-closed；`runtime_health_fuse.json` 與 `model_benchmark.lock` 都未解除，沒有啟動 uploader。
- 隔離 5 張 runtime-health smoke v6 已完成 5/5：3 張自動驗證、2 張因結構證據不一致保留待複核、失敗 0。這證明守門會把衝突留給人工，而不是冒充成功；它仍只是 5 張 smoke，尚未授權正式續跑。
- Prompt 已改成單一 JSON 物件內含自然語句 `narration`，刪除可照抄完整答案模板；第二／第三輪與同輪 retry 都不再帶前輪答案。Parser、nested evidence 正規化、repetition watchdog、explicit verification 狀態及其測試已同步。
- Dashboard 待機狀態已實際在既有 Chrome 分頁驗證：總進度 `65,331/150,321`、目前批次 `5/5`、最新照片 `中華-1065`、`AI 判讀內容 · 最新完成判讀`、右側 5 張卡片與 2 張待複核一致，50% 主畫面配置未改。
- 原右欄會從全域 `/api/presentation_history` 混入舊批次卡片；現改為 `scope=current_batch` 並用 `.ocr_source_map.json` 的 5 個穩定 `source_item_id` 清除跨批次 session/history 污染。瀏覽器驗證舊卡片數由 100+ 降為精確 5，舊 `SF-員林-562` 不再出現。
- 後端目前是單一隱藏 port-5000 進程，工作目錄仍指向 `D:\00_商化\00_已OCR照片\_ocr_staging\20260715_runtime_health_smoke5_v6\202601_health_smoke5_v6`，狀態 idle。第一次替換誤用不含 `psutil` 的通用 Python，30 秒 fail-closed 後查明；現已改用專案 `.venv`，未形成循環重啟或可見終端機。
- 已通過 77 項針對性測試、完整 critical regressions 與 production Vite build。下一步不是立刻解除熔斷器，而是建立 15 張分層 smoke、全量稽核其獨立輪次／內容／UI，再決定是否恢復正式 202601 工作目錄與 `.5` backfill。

## 2026-07-15 20:35 十五張隔離驗證與介面收尾

- 第一次 15 張試跑在 4/15 時由內容／介面監控主動停止：同一批來源照片曾在先前試跑出現，僅靠 `source_item_id` 會恢復舊場次卡片。這批不得算成功，也沒有解除正式熔斷器。
- 已加入每次啟動唯一 `run_id`，並貫穿持久歷程、compact-v2 狀態、current-batch history API 與前端批次 key；compact event 同時保留 guard revision 與 verified/review flags。第二次新場次為 `20260715_202322_313909`，沒有再混入舊卡片。
- 第二次 15 張隔離驗證完成 15/15：7 張自動通過、8 張證據不足或輪次衝突留待複核、失敗 0。共 34 次模型判讀（第 1 輪 15、第 2 輪 11、第 3 輪 8），34/34 為獨立判讀，上一輪答案暴露 0、prompt contamination 0、runtime unhealthy 0，全部使用 `evidence_guard_revision=20260715.5`，且全部禁止上傳。
- 收尾實際抓到前端少最後一張：最後完成事件與 `is_running=false` 同次到達，舊程式因 running gate 忽略它。現已移除該 gate 並補回歸測試；同一既有 Chrome 分頁實測恢復精確 15 張卡片、8 張待複核、最新 `鹽行-1551`、總進度 `65,331/150,321`，舊批次卡片 0、raw JSON 0、破損字元 0，版面未改。
- `runtime_health_fuse.json` 與 `model_benchmark.lock` 目前仍保留，正式 OCR 與 uploader 都尚未恢復。解除前仍須完成最新程式的 critical regressions、Git checkpoint，並以文件規範重新確認正式工作目錄與單一隱藏後端進程。
- 復工前比對手冊又發現：`model_benchmark.lock` 的舊安全升級器 PID 8668 已不存在，但 supervisor 遇到該 planned lock 仍會直接退出，與手冊聲稱的 backfill 接手不符。現已補成 fail-closed takeover：只在擁有者消失、後端為 v19.45/compact-v2/strict、idle 且無 runner 時重建並續接 `.5` backfill；runner 或 OCR 活著時不重複啟動，完整清冊歸零證明成立後才解除 lock。
- runtime fuse 已人工封存至 `runtime_health_fuse_history` 並核對 SHA-256；首次 takeover 以 exit 11 正確 fail-closed，查明既有 `Start-Hidden` 使用位置陣列造成輸出路徑綁定為 null，沒有啟動 runner 或 uploader。所有 supervisor 子程序呼叫已改用具名 `-File/-ProcessArgs/-OutFile/-ErrFile`，空參數會在啟動前拒絕，並用 mock `Start-Process` 驗證參數、log 路徑與 Hidden window 完整傳遞。
- 修正隱藏啟動後首次接力建立 5,942 筆 `.5` 候選，但監控發現後端仍指向 smoke staging；`resume-existing-then-continue` 必然在掃描後因找不到正式月份＋來源摘要而失敗，因此在 OCR 尚未啟動前主動停止該父子 runner。正式 `202601_商化照片-202601_6403a632` 已驗證與候選 202601 群組唯一匹配，API 安全切回後恢復 `829/1,504`、失敗 0。
- 切回正式 staging 時又抓到 15 張 smoke 卡片殘留：source ID 相同且 idle `current_run_id` 為空，舊 recovery 誤選最新非空 smoke run。現加入 work-dir `.ocr_presentation_run.json`、legacy-only fallback、切換時清除跨資料夾 live 指標、前端 source+run 雙重過濾及 session storage v2；需在 idle 邊界載入新版後，先以既有分頁證明正式卡片無 smoke 污染，再重啟回補。
- 原分頁已用純前端補強驗證正式待機：`829/1,504`、總進度 `65,331/150,321`，smoke 檔名／卡片 0、raw JSON 0。排程在 21:01 自動重啟的 runner 雖已指向正式 staging，但仍長時間滿 CPU；查明 `group_candidates` 忽略候選 CSV 既有 `source_path`，對 5,942 張逐張做全來源樹 `rglob`。現改成先驗證 root/name/period 後直接使用 bound path，只有舊資料缺失才 fallback 搜尋，並有測試保證合法路徑不呼叫 tree scan。
- 新 runner 已在約 2 秒完成 5,942 筆／5 群組解析，證明速度修正有效；隨後因正式 staging 為 idle 但未完成 `829/1,504` 而正確拒絕。現補上唯一匹配後的正式續跑順序：runner 先以 exact staging 呼叫 `/api/start_batch`（confirmed continue、非 restart），再 attach/wait/finalize 並接後續月份；測試證明不會對模糊、完成或已在跑的資料夾按 Continue。

## 2026-07-15 21:20 正式復跑內容漂移與停止點

- 正式 `202601` 已由接力器正確從 `829/1,504` 接續，既有 Chrome 分頁實測顯示總進度 `65,331/150,321`、目前複核數持續變動、AI 自然語句逐字顯示、右欄只含本場次卡片，沒有 smoke 卡片、裸 JSON 或亂碼。
- 內容監控在 `836/1,504` 主動停止：原始結構答案是 `遠景 / model=null / price=null`，舊敘述救援卻從同段 narration 的鄰近價牌補回 SKU/價格並把最終結果改成單機。`.5` 守門雖攔住 verified，但這仍是「跑歪」與無效二、三輪來源，不能因進度在增加就繼續。
- 已加入結構答案權威規則：敘述可用來指出矛盾並觸發獨立重讀，但不得覆寫明示的遠景/單機，也不得補回明示 null 的型號或價格。任何被阻止的欄位會記在 `structured_authority_blocked_fields`。
- 目前正式 OCR 停止、後端與介面保留、runner 已停止、uploader 未啟動、`model_benchmark.lock` 保留。完成 critical regression、隔離重播與既有分頁核對前不得恢復正式 backfill。

## 2026-07-15 21:45 結構答案權威規則驗證與恢復

- 兩次完整 critical regression 均通過；v19.45 evidence contract 現有 46 項測試通過。新增測試涵蓋：明示遠景/null 不得被敘述補值、不同非空 SKU/價格不得被後處理替換、僅允許大小寫／標點／貨幣格式等外觀正規化。
- 第一段正式受控驗證在 `839→842/1,504` 共 6 輪，確認遠景改單機 0、null 型號補值 0、null 價格補值 0、記憶暴露 0、prompt contamination 0；另抓到 `S27CG552EC→S32CG552EC` 的非空型號改寫，雖守門已排入 retry，仍再次停機並收緊規則。
- 載入收緊版後第二段正式驗證在 `842→845/1,504` 共 3 張／6 輪：view rewrite 0、null refill 0、material model/price rewrite 0、independent false 0、prior exposed 0、contamination 0。一張跨輪結構衝突正確保留 unresolved，未冒充成功。
- 同一既有 Chrome 分頁實測仍只有 1 個 `localhost:5000` tab；顯示總進度 `65,331/150,321`、正式進度、自然語句逐字判讀與本場縮圖卡，裸 JSON 0、亂碼 0。後端已改為預設 headless，只有明示 `SAMSUNG_OCR_OPEN_BROWSER=1` 才可要求瀏覽器動作。
- continuity supervisor 已恢復正式 `.5` backfill；21:45 狀態為 `202601 846/1,504`、verified 445、review 401、failed 0、單一 runner 父子組、uploader 0、runtime fuse 不存在、`_ocr_audit/model_benchmark.lock` 保留。後續監控必須持續抽查 raw/final 結構漂移，不能只報進度。
- 21:52 實際 Chrome open-tab inventory 發現 5 個歷史 Dashboard 分頁；舊檢查只看 automation-bound tabs，曾錯報為 1。已核對最新頁顯示 `858/1,504`、AI 逐字內容與 15 張當前場次卡，無 raw JSON／亂碼／缺輪次後保留，另外 4 個重複 Dashboard 分頁已關閉。新版 backend 已 headless，後續必須用 actual open tabs 證明只剩一頁。
- 其後低干擾監控在 `863/1,504` 因一筆 `structured_authority_blocked_fields=category` 先行停機。逐欄核對確認該筆只是 raw `category=一般單機` 正規化成 `單機`，view/model/price、結構證據與 verified 決策全部一致，並非內容漂移。現已將等義 category 正規化排除於 blocked override；真正的單機/遠景 category 衝突仍會攔截。歷史 category-only flags 需按正規化場景語意判斷，不得再造成誤停。
- 2026-07-15 已將「固定防走偏節奏」提升為開發手冊硬性清單：每次接手／修復／續跑先重讀開發手冊、移交與三層守門原理；每三小時同查進度、內容、介面與上傳隔離；任何新根因必須同步文件與重現測試。監控不得只報數字，也不得以等待訊息洗版。
- 22:07 內容監控追查 4 筆 raw model→null：`五股-1225` 是前兩輪遠景、第三輪 `S27B610EQ` 的真衝突，保留待複核合理；`土城中央-1487` 則三輪都從同張清楚價牌讀到 `S24D300GAC / 2990`，只因官網／本機型號表未收錄就被清空並誤標「沒有規格牌」，屬過度阻擋。守門升為 `20260715.6`：同輪結構答案與主角實體價牌明確一致時保留未收錄 SKU 候選；只有三輪獨立型號／價格全相同且至少兩輪主角／價牌歸屬明確才驗證，否則保留讀值待複核並禁止上傳。舊 `.5` 不得冒充 `.6`。
- `.6` 首批 4 張 smoke 的內容監控又抓到 `南投-667`：價牌連續讀到短碼 `S27CG552 / 4990`，本機唯一正式型號為 `S27CG552EC`，舊嚴格比對仍把它清空。runner 已立即停止，4 張 `.6` smoke 不算正式完成。守門升為 `.7`：只允許唯一且只追加 1–3 個尾碼字元的正式型號補全，並要求第二輪相同型號／價格／價牌歸屬；非唯一、改中間字元或改尺寸仍失敗封閉。舊 `.6` trace 不得冒充 `.7`。
- `.7` 首批重播發現短碼已正確補成 `S27CG552EC`，但 pipeline-owned `model_prefix_completed` 在 `data_obj→result_json` 白名單複製時遺失，導致第一輪誤通過；監控在 5 張內停止，該 `.7` run 全部作廢。`.8` 將所有未收錄／短碼證據標記納入專用內部白名單並新增整合回歸，確保第二輪門檻實際存在；舊 `.7` trace 不得冒充 `.8`。
- `.8` 切換期間為避免 daemon 自動接回舊 staging，曾受控強停 continuity daemon；強停跳過 `finally`，留下 owner PID `26972` 的 `_ocr_audit\ocr_continuity_daemon.lock`。已先證明系統無任何存活 daemon、PID `26972` 不存在，才只移除該精確 daemon lock 並隱藏重啟；目前唯一 daemon PID `27496`，回讀 lock owner 亦為 `27496`。後續不得把快速重啟的 `duplicate_exit` 誤判為已有健康 daemon，也不得連帶刪除必須保留的 `model_benchmark.lock`。
- 22:56 `.8` 正式 run 自 `10→22/1,504` 的低干擾稽核：新增 trace 全為 `.8`，非獨立輪次、前輪答案暴露、prompt contamination、runtime unhealthy 與 raw→parsed view/model/price 實質漂移皆為 0；遠景結構不一致者全部 retry 或 unresolved，`blocked_verified=0`，uploader／proof 仍為 0。另發現權威三層設計文件的舊段落仍寫第二輪「取得第一輪結果」，與現行 stateless 程式及同文件責任邊界矛盾；已統一修正為第二、第三輪模型都只能收到固定規則、通用反證任務與當前照片／裁切，跨輪結果僅能在模型呼叫完成後由結構化守門器比較。相關 immediate retry 與 runtime health 19 項回歸通過。`model_benchmark.lock` 雖為已死亡的原升級器 PID 8668 所建，但其 purpose=`backend_upgrade_v1945`，現由 supervisor 以 `planned_backend_upgrade_recovery_active` 接手，依手冊必須保留到 `.8` backfill 清冊歸零，不是可刪除的普通殘鎖。
- 23:05 收斂稽核在 `.8` 正式 run `35/1,504` 主動停止 runner、保留 port 5000 後端與 Dashboard：35 張中 14 張遠景全部 unresolved，不是照片全有疑慮，而是分支 A 未要求 `unique_main=false`，模型普遍輸出 null；跨輪守門又把都已達 3 台以上的 3／10／12 等精確計數差，以及不同「非主角自有」價牌狀態，誤當核心衝突。若繼續，全年跑完仍無法清冊歸零，supervisor 會重建同批候選造成無限白跑。守門升為 `20260715.9`：模型必須輸出完整遠景結構證據；只有各輪都明示遠景、count>=3、`unique_main=false`、無 matched 主角價牌、無同主體 FollowMe 強證據時，跨輪 count 才按 `3+`、ownership 按 unowned 語意比較。任何 null／少於 3／唯一主角／matched／FollowMe 強證據仍失敗封閉；舊 `.8` trace 全部不得冒充 `.9`。`model_benchmark.lock` 必須保留到 `.9` 清冊歸零。
- 23:27 `.9` 七張隔離收斂測試完成：兩張真遠景 verified、四張預設反例全部 review-required，未出現錯誤放行；另有一張真遠景三輪結構證據皆完整一致（5／6／5 台、`unique_main=false`、無 FollowMe 實體），只因可讀敘述寫「多台螢幕」而未逐字重複整數，被 `evidence_thinking_conflict` 過度封鎖。守門升為 `20260715.10`：精確整數仍必須逐輪存在於結構化證據且至少為 3；敘述須獨立明說整排／多台螢幕及無唯一主角，但不必複誦同一整數。敘述明說只有 0／1／2 台、任一輪結構少於 3、主角非 false、matched 價牌或 FollowMe 強證據仍失敗封閉。正式 backfill、daemon 與 uploader 在 `.10` 回歸和新 smoke 前保持停止。
- `.10` 首次後端部署又由 boss-facing API 檢查抓到累計判讀從 14,401 虛增為 15,431：舊歷程包含 1,031 後重設為 1／2 的 legacy 區段，新服務已從 1,033 接續寫絕對累計，但原載入器在每次再重啟時仍把舊 1,031 重加一次。載入器已改為偵測後續絕對累計並採用該值，新增兩次重啟永久回歸；`.10` smoke 在正確回復 14,401 前不得開始。
- `.10` 七張重播證明「多台」敘述修正有效，但又抓到 `無 FollowMe` 否定詞漏網：南投-664 與北屯-648 的結構證據三輪皆合格，第三輪明說無 FollowMe 實體，仍被文字風險器錯當正向 FollowMe 線索。北屯-650 經原圖人工核對確為多台無唯一主角的遠景，三輪均為 count=3，驗證屬正確，不再列入 must-block；真正反例為健行-1386（0 台）、北屯-649（少於 3／分支衝突）、文心-643（少於 3／分支衝突）。守門升為 `.11`，只把緊鄰 FollowMe 的明確否定詞視為負向敘述；結構化 FollowMe 強證據仍有最高優先權。
- 2026-07-16 00:00 `.11` 七張隔離驗證完成：南投-664、健行-1385、北屯-648、北屯-650 四張真遠景 verified；健行-1386（0 台）、北屯-649（2 台／分支衝突）、文心-643（0 台／分支衝突）三張 unresolved。21/21 輪 `prior_answer_exposed=false`、`prompt_contamination=false`，runtime fuse 未觸發，uploader 仍為 0，累計 presentation sequence 由 14,422 正常增至 14,443。
- 試跑結束後 runner 曾以 `structured_narration_conflict` 誤判整批失敗：被點名的健行-1386 本來就已 `auto_review_required=true`，矛盾沒有冒充 verified。執行器現改為只有未被隔離的矛盾才停批；已明確待複核者保留 fail-closed、記入 `contained_review_conflicts` 並繼續下一候選。新增回歸同時證明未隔離矛盾仍會停批。證據契約 64、runtime health 19、presentation history 15、presentation soak 24，以及 rerun/backfill 15 項測試已通過。
- 目前正式 backfill、continuity daemon 與 uploader 均未啟動；port 5000 後端／Dashboard 保留，`model_benchmark.lock` 仍在，runtime fuse 不存在。下一步先建立 `.11` 全量候選清冊，確認唯一隱藏 runner 後才復跑；再安裝唯一 daemon 並驗證 lock owner，任何正式復跑前後都依開發手冊同查內容、介面、程序與上傳隔離。
- 2026-07-16 00:13 `.11` 正式清冊為 5,947/5,951，使用全新 staging 隱藏啟動；內容監控在 3/1,500 主動停止。前三張一般單機第一輪都省略 `complete_screen_count`／`unique_main`／`label_ownership`／`followme_physical_evidence`，前兩張因此三輪後 unresolved，若繼續會形成大量白跑。runner 父子 PID 已停止、後端／介面保留、daemon 與 uploader 仍為 0。
- 根因是提示前段共用 JSON Schema 只列六個舊欄位，雖然檔尾 v19.45 說明要求四個證據欄位，模型仍依較明確的舊清單省略。守門升為 `20260716.12`：四欄正式加入共用 Schema，分支 C 明定單機每輪都要輸出實際台數、`unique_main=true`、價牌歸屬與 FollowMe 實體證據；禁止由 narration 反推補值。舊 `.11` trace 不得冒充 `.12`，必須先回歸、重建 dashboard、在原三張做隔離 smoke 才能復跑。
- `.12` 原三張隔離重播完成：665 因照片價與參考價差距而做兩輪，666 第一輪即通過，667 因短型號唯一補全而做兩輪；共 5 輪，5/5 原始 JSON 都完整含四個證據欄位，三張全部 verified，第三輪 0、prior-answer exposure 0、prompt contamination 0、runtime fuse 0。完整 critical regressions、65 項 evidence contract、24 項 presentation soak 與 production dashboard build 通過。
- 後端已在 idle 邊界以專案 `.venv` 隱藏替換，port 5000 單一 listener、compact-v2/strict、presentation sequence 由 14,453 正常續到 14,458，frontend fingerprint=`f758084ddcdbe3c0`，未開新分頁或視窗。既有 Chrome open-tab inventory 只找到一個 OCR Dashboard 且 URL 已含該 fingerprint，但 extension claim 後失去控制，未取得 DOM／截圖；因此不得宣稱完成實畫面 UI 核對。正式 backfill、daemon、uploader 仍保持停止，下一步必須先在既有分頁完成 50/50、總進度、AI 敘述、3 張卡片／照片一致的視覺證明。
- 00:33 延續核對定位右欄污染根因：idle 時 `current_run_id=""`，後端把空值解讀成 legacy-only，前端又接受空 run，因而載入 200 筆舊卡（其中 28 筆敘述疑似裸 JSON），反而漏掉 `.12` 的 5 次 presentation。現改為 idle 只恢復所選 source scope 內最新非空 durable run，回傳其明確 run ID；無可信 run 時回空，前端也直接清空／拒收 blank run。舊 revision 卡若刻意顯示只標 `等待新版複核`，不得冒充本輪人工複核。新增 history API 與 presentation soak 永久回歸。
- 同輪實畫面驗證已完成：受控隱藏替換後唯一 port-5000 listener PID `27644`，compact-v2/strict、sequence `14,458`、fingerprint=`59f77158a628c06e`；工作目錄恢復為原始 `商化照片-202601`，狀態 1,504/1,504、失敗 0。既有 Chrome actual open-tab inventory 為總分頁 1、Dashboard 1；畫面顯示總進度 65,331/150,321、44/136、1,504/1,504，照片區約 370px、下方 LLM/歷程合計約 357px，維持左側各半。主圖／自然語句／右欄首卡皆為南投-667，右欄只有 `.12` 場次 665/666/667 三張完成卡，裸 JSON 0、亂碼 0、舊批次卡 0、錯誤待複核標籤 0。
- 程序／上傳稽核另發現四小時 `SamsungOCR_PipelineWatchdog` 尚在，但舊 `ocr_upload_watchdog.ps1` 未檢查 `model_benchmark.lock`。現已在 watchdog 主入口加入 fail-closed：該 lock 存在時先移除任何 proof 並於任何 summary repair、backend/recursive/questionable、proof 或 uploader 動作前退出；永久測試已加入。正式 `.12` backfill、daemon、uploader 仍未啟動，runtime fuse 不存在，`model_benchmark.lock` 保留，upload proof 不存在。
- 00:45 `.12` 正式 backfill 在前 7 張由內容監控主動停止。前 6 張共 13 輪的 revision、四證據欄、independent、prior exposure、prompt contamination、runtime health 與 invalid-verified 皆為 0 異常；但草屯-674 原圖明顯是中央完整主螢幕＋正下方可讀 `S27D300GAC / 3,090` 價牌、右側另一台局部入鏡，模型前兩輪仍輸出 `遠景 / count=2 / unique_main=false / model=null / price=null`。結構權威守門攔下 narration 企圖補值且未 verified，但 material blocked fields=`view_type,model,price` 依手冊必須停機，否則全年會形成三輪後待複核的白跑。runner 父子 PID 41032/38336 已精準停止，backend PID 27644 與 Dashboard 保留，uploader 0。
- 守門升為 `20260716.13`／prompt v4.1.29：在共用最終輸出契約、分支 A 與無狀態第二／第三輪提示都明定 0／1／2 台完整入鏡絕對不可判遠景；中央完整主螢幕與其空間對齊可讀價牌是單機候選，旁邊局部／次要螢幕不得抹掉主角。舊 `.12` trace 不具 `.13` 驗證效力；必須先通過回歸並以草屯-674 做隔離重播，證明不再輸出 sub-three distant，才能重建清冊與復跑。
- `.13` 草屯-674 隔離重播已一輪正確得到 `單機 / S27D300GAC / 3090`；但正式新 run 前 6 張的內容監控又在南投-669 發現 `structured_authority_blocked_fields`。其結構答案明示單機且 `model=null / price=null`，敘述雖讀到 `S24F332EAC / 2390` 或 `S27E612EAC / 4740`，也明說價牌屬於鄰近商品；舊獨白救援仍先補值，直到結構權威守門才撤銷，形成「先污染、再撤銷」的無效停機訊號。正式 runner 已停止，backend／Dashboard 保留，daemon、uploader、proof 均為 0，`model_benchmark.lock` 保留。
- 守門升為 `20260716.14`：只要可信結構物件包含 `model` 或 `price` 欄位，即使明示 null，所有獨白型號／價格救援入口都必須跳過；只有舊格式完全缺少該欄位時才允許保守補抓。永久回歸重現南投-669 的鄰近標牌敘述，要求結果維持 null 且 `structured_authority_blocked_fields=[]`。舊 `.13` trace 不得冒充 `.14`；完整回歸、669/674 隔離重播、既有單一 Dashboard 分頁的內容與版面核對全部通過前，不得恢復正式 backfill 或 continuity daemon。
- `.14` 的 669/674 隔離重播各一輪完成：兩輪皆 `blocked_fields=[]`、independent、未暴露前輪答案、無 prompt contamination、runtime healthy；669 本次原始結構直接得到 `單機 / S27F612EAC / 4740`，674 得到 `單機 / S27D300GAC / 3090`。669 本次沒有自然重現 null，故 null＋鄰牌情境只以永久重現測試證明，不得把實圖 smoke 說成該情境的直接證據。
- 同一既有 Chrome 分頁的實畫面稽核另抓到右欄只顯示 1/2 張：後端 current-batch durable history 完整有 2 張，但前端只在場次 key 改變時讀一次，最後一張若在兩次 status poll 間完成便會漏卡。現改為已處理張數每前進一次就補讀同場次 durable history，沒有新增高頻輪詢。重新建置、隱藏替換後端並只重載原分頁後，actual tab inventory 仍為 1；總進度 `65,331/150,321`、資料匣 `44/136`、smoke `2/2`、累計 `14,494`，主圖／目前檔案／自然語句／首卡同為草屯-674，右欄 674 與 669 共 2 卡，裸 JSON 0、亂碼 0。左側主區 795px 中照片 398px、LLM／歷程 397px，維持定稿 50/50。
- `.14` 全年清冊重建結果為 5,951/5,951 候選、缺檔 0、衝突 0、無效列 0；smoke staging 身分沒有冒充正式原圖驗證。正式 run `20260716_012422_371387` 已由唯一隱藏 runner 父子組啟動，第一個內容閘涵蓋 8 張／21 輪：revision mismatch、material blocked fields、非獨立輪次、前輪答案暴露、prompt contamination、runtime unhealthy、invalid verified 全為 0。667 的 `S27CG552→S27CG552EC` 是帶專用 marker 的唯一尾碼補全且仍因跨輪證據未收斂而 unresolved；665／668／669 的衝突也都 fail-closed，沒有冒充成功。檢查點 API 為正式完成 7/1,504、累計 14,514；既有單一分頁顯示正式照片、LLM 即時輪次、同張處理中卡與累積結果，裸 JSON／亂碼 0。continuity daemon、uploader 仍為 0，`model_benchmark.lock` 保留，fuse／upload proof 不存在；現行 runner 自己會續跑所有群組，未完成更大內容稽核前不要另開 daemon。
- 2026-07-16 01:40 第二個正式內容閘已擴大到 21/1,504：13 verified、8 review-required、failed/unknown 均為 0，最後 durable sequence `14,544`。最新 50 輪 revision mismatch、`independent_pass` 異常、前輪答案暴露、prompt contamination、runtime unhealthy 均為 0；5 個 `structured_authority_blocked_fields` 都只有 `view_type` 且 0 個被 verified。逐筆掃描 13 筆 verified，所有非空 model/price 都有 matched 價牌歸屬，沒有單機缺型號／價格、荒謬價格、非預期 raw/final 漂移或明顯幻覺，因此沒有停 runner。既有 Chrome Dashboard 分頁在 20/1,504 節點再次取得 DOM 證據：總進度 `65,331/150,321`、資料匣 `44/136`、照片／頂部目前檔案／LLM 第 1 輪／右欄處理中卡同為文心-646，右欄累積 20 張，無「未提供」、裸 JSON 或亂碼；未開新分頁、未重載。原有三小時 heartbeat 的名稱與 prompt 曾為亂碼且寫死舊 `.8`，已原地更新為唯一 `Samsung OCR 三小時四維監控`，排程仍每三小時，現在會先讀開發手冊與現行 revision，再檢查進度、內容跑歪、介面同步與上傳隔離；不得再另建重複監控。
- 01:49 的 30 張內容閘新增 9 張完成結果與 23 輪 trace，sequence `14,544→14,567`；增量 verified 5、review 4、failed 0。revision、獨立輪次、前輪答案暴露、prompt contamination、runtime health、FollowMe／遠景／單機明顯矛盾與 verified 證據異常全為 0；唯一 blocked 為未 verified 的 `view_type`。另有 6 輪 model 被清空，全部因同張未收錄 SKU 不一致、FollowMe 或其他不收斂證據進入 retry／unresolved，沒有冒充成功，故未觸發停機。
- 全專案來源清冊 `folder_discovery.csv` 現有 136 個資料夾、150,321 張支援影像；初次辨識 `folder_summary.csv` 為 65,331 張，剩餘 84,990 張可精確拆成：2025-12 缺 1 張 `M-台南市-東　區-TK3C-台南東寧-1335.jpg`、2022-08 至 2022-01 共 9,629、2021 共 13,114、2020 共 15,732、2019 共 11,968、2018 共 10,577、2017 共 8,913、2016 共 13,011、2015 共 2,045。2025 其餘、2024、2023 與 2022-12 至 2022-09 已有初辨識，但仍須依 all-year questionable pass 完成正確性複核；2 個 202602 HEIC 列在 `skipped_unsupported.csv`，不屬 150,321 支援影像。全年份資料夾順序與續跑防重機制已存在，但沒有 frozen 逐照片 all-years manifest，recursive 每輪會重掃來源樹；這是後續功耗風險，不是重複 OCR 證據，正式切入舊年份前再以正確性不退步為前提評估是否優化。
- 目前 upload proof dry-run 正確失敗封閉：缺 rev14 `v1945_evidence_backfill_2026_run_summary.csv`，且 risk audit/finalization 未完成、Drive manifest 與風險稽核過期、identity/risk/next-batch hashes 不一致；`upload_gate_proof.json` 仍不存在。2026 完成後順序固定為：重建 distant/FollowMe risk audit 與 finalization → 重建 Drive manifest → 產生 content-bound proof → pending 全部安全上傳且歸零 → 寫入含 hashes 的新 current-year completion marker，才可由 supervisor 放行 202512 缺檔與 202208 往前的 recursive OCR；7/11 舊 marker 不具本次授權效力。
- 下一階段離線守門已用專案根目錄的 unittest 模組方式驗證：current-year upload finalization 6/6、continuity supervisor 12/12、auto-rerun continuity 11/11、questionable upload guards 10/10，合計 39/39 通過；所有寫入都在 TemporaryDirectory，未碰正式輸出、未啟停 runtime。直接執行 `python tools/test_current_year_upload_finalization.py` 會因 script path 缺專案根目錄而出現 `ModuleNotFoundError: skills`，這是錯誤啟動方式，不是守門失敗；開發手冊已固定要求從 repo root 使用 `python -m unittest`。
- 02:44 完成直接 uploader／歷史年份 fail-closed 補強，未碰正式 OCR、backend、Dashboard 或 Drive：實際 `--execute` 現在只接受 canonical `_drive_upload`／receipt、`samsung_ocr_drive` 與正式 5000 backend health check，在 prepare、proof 後與每年度 copy 緊前重查 fuse、`model_benchmark.lock`、API idle 與 owned OCR runner；current-year phase固定 `--years 2026`。歷史批次另須 `historical_upload_authorization.json`，其 current-year marker 必須是同一 audit/backfill 的 pending 0，且 discovery/summary 路徑、SHA、唯一資料夾身分、image count、mtime 與 error=0 全相符；只有 `copied`／`skipped_existing` 算完成。next batch 改為含 `content_sha256` 的完整欄位 prefix；manifest 只雜湊本批，staging map 攜帶同一 SHA，staged bytes 在載入與 rclone 緊前重算。shared proof 原子寫入後完整重讀 risk/finalization/audit/manifest authorities，任何競態漂移即刪 proof。Drive receipt 仍需唯一同名＋Size＋MD5；timeout 只有確定會進下一 repeat cycle 才能視為 retry，非 repeat 或最後 max-cycle 回 124。91 項 uploader/finalization/recursive/watchdog/supervisor/continuity/reconciliation 離線測試、Python compile、三支 PowerShell parse、diff check與修改後完整 critical regressions 全通過；正式 `model_benchmark.lock` 仍保留，沒有建立 proof 或啟動 uploader。
- 03:10 依開發手冊再次做「不只看進度、也看流程是否跑歪」的雙重唯讀稽核，發現歷史 recursive 的裸 `--ignore-current-year-review-gate` 可繞過 supervisor、resume 只看張數＋最大 mtime，且每完成一夾會重掃全樹。已離線移除裸開關，新增共享 `historical_continuation_gate.py`：只有 root/revision/content-bound request＋2026 pending 0 marker/proof＋2026 review 0＋fuse/benchmark 不存在＋port 5000 idle 才能產生 receipt；supervisor 與 runner 各自重驗。另新增 frozen `source_inventory_v1.csv/json`，逐照片綁 relative path/size/mtime_ns/SHA-256、穩定 folder ID、每夾處理前核對與結尾全樹核對；不再每夾重掃 15 萬張。Resume 另須照片＝成功＝複製數、error 0 且來源／輸出 bytes 相同；full marker 與 historical upload authorization 也綁 inventory hashes/counts。目前全部只是 Repo 與 TemporaryDirectory 離線修改，未切換現行 `.14` runner、未重啟 backend、未刷新瀏覽器、未建立 upload proof、未接觸 Drive；2026 完成與正式 receipt/inventory 尚未成立前仍禁止開始舊年份。
- 03:11 依使用者既有全專案接力授權，已將正式 `full_project_continuation_requested.json` 原子升級為 v1 root-bound request，保留原 `requested_at=2026-07-14T20:19:21`，新增 SourceRoot、OutputDir、current year 與 `evidence_guard_revision=20260716.14`，SHA-256 為 `6b25af738e08f55beb05a7e2a28fac6275f3772605583c8707ed6744f3b1b956`。這只補強未來授權身分；因正式 `.14` backfill 與 benchmark lock 尚未完成，沒有產生 historical receipt、沒有建立全樹 inventory、沒有啟動舊年份。
- 03:15 此輪 62 項 uploader／supervisor／recursive／historical gate／source inventory 離線測試、所有修改 Python compile、兩支 PowerShell parse、`git diff --check` 與加入新守門測試後的完整 critical regressions 全部通過。Critical runner 現已永久包含 historical receipt 與 per-photo inventory 測試。所有測試只使用 TemporaryDirectory；正式 OCR、backend、Dashboard 分頁與 Drive 未被測試流程啟停或刷新。
- 03:17 唯讀稽核三小時 heartbeat 發現其 automation TOML 在 PowerShell 顯示為 mojibake，且 `target_thread_id` 仍指向舊任務。實際 UTF-8 內容已用 ASCII escape 回讀確認，並只更新既有 automation id `samsung-ocr-v19-44-four-hour-monitor`（未新增重複項）：名稱為「Samsung OCR 三小時四維監控」、週期仍為每 3 小時、target 改為目前 goal task `019f5f58-0b4a-78b0-ac75-29d7f0817527`。Prompt 已明定每次先讀最新版手冊／移交／三層守門／SKILL，四維核對進度、內容跑歪、介面同步與上傳隔離；內容污染或無限白跑時只在安全照片邊界停止唯一 owned runner，保留 backend／Dashboard，不得新開分頁、重啟或洗版。
- 03:48–04:11 依使用者指出的台中超越-913 FollowMe 誤判做 `.15` 隔離診斷，正式 `.14` runner、daemon 與 uploader 全程未恢復；`model_benchmark.lock` 與 runtime fuse 保留。原始缺陷包含：第一輪敘述已看到白色圓形底座／託盤卻輸出遠景、第二輪宣傳畫面反向否定實體、`FollowMe Pro M7 43"` 被舊正規化改成 32 吋、句內「無法鎖定」誤傷正向 FollowMe、以及 `build_final_display_thinking()` 以 `最終校正` 文案掩蓋原始矛盾。現均有永久回歸。
- `.15` 最終邊界：大型照片第一輪附中央全高裁切，第二／三輪附左中右獨立裁切；螢幕廣告只能是弱線索，不能否定底座／直桿／託盤；結構明示 model/price 不得被敘述改值；原始 narration 永不由後端改寫，提示規則照抄、超長敘述、`最終校正` 文案會被健康閘收回。第一輪限定 FollowMe/view 矛盾只允許一次無記憶第二輪，重犯或模型／價格／提示／介面異常立即 durable fuse，fuse 另保存有限 record snapshot。單機結構已有直接品牌或兩項以上同主體強證據時，不因少列一個方向／卡片細節熔斷；遠景仍完全 fail-closed。
- 913 最新隔離 run 第一輪與第三輪均得到 `單機 / FollowMe Pro M7 43" / 17990`，第二輪得到 `單機 / FollowMe M7 32" / 17990`；三輪皆 `independent_pass=true`、`prior_answer_exposed=false`、`prompt_contamination=false`，但因核心型號不一致正確保留 review-required，沒有以二對一冒充 verified，沒有上傳。此結果證明分類／價格已不再錯判遠景，但型號仍需人工或更強模型證據；不得把這張說成已自動驗證完成。
- 後端每次只在 API idle 邊界精準替換 port-5000 父子進程，使用專案 launcher 的 hidden 模式與 `SAMSUNG_OCR_OPEN_BROWSER=0`；未開新分頁／新視窗。正式批次仍需完整 critical regressions、五張再十五張隔離 smoke、現有唯一分頁的同步視覺核對及 `.15` 清冊重建通過後，才可人工解除 fuse。舊 `.14` continuation request 目前尚未遷移，不得先啟動歷史年份。
- 04:15 五張 `.15` 隔離 smoke 未通過：南投草屯-674 後的台中北屯-650 原圖是多台螢幕遠景，卻在首輪沿用 674 的 `S27D300GAC / 3,090`並被誤列 verified。原圖、全圖輸入、下方價牌裁切、中央裁切的 SHA-256 全部不同，排除應用程式重用同一影像，判定為模型跨照片語意漂移未被舊守門攔下。
- `.16` 新增雙層防線：每個 JSON 必須回傳當次 `request_id`，不一致即 durable fuse，並在 trace 保留實際全圖輸入 SHA-256；相鄰不同 source identity 若產生完全相同的型號與價格，後一張不准首輪 verified，必須從原圖無記憶第二輪。正式 runner 繼續封鎖，現有 fuse 保留，未做清冊遷移或 Git 推送。
- 10:43 `.16` 第三次五張隔離 smoke 完整收尾：5/5 處理、4 verified、1 review-required、0 failed。所有 9 輪 trace 皆有 32-hex／128-bit request ID、`request_id_verified=true`、非空 64-hex 全圖 SHA-256、`prior_answer_exposed=false`、`prompt_contamination=false`。650 三輪仍無法可靠判成原圖遠景，但已被固定 unresolved，不再冒充 verified；913 兩輪皆為 `FollowMe Pro M7 43" / 17,990`，第二輪後 verified。
- `.16` 最終邊界再收緊：RequestID 從 8 碼擴成完整 128-bit；最後健康門自行要求 request 綁定與影像指紋；跨照片重複不論前張是 verified 或 unresolved 都必須完成三輪且保留 review-required，錯兩次／三次不得洗白。另已移除結構明示 `view_type` 後仍以敘述改寫分類的舊救援路徑。LM Studio CLI 載入索引模型失敗時，launcher 改用官方 local model-load API，後端仍 hidden，瀏覽器仍 opt-in。

## 2026-07-16 11:25 `.16` 十五張實拍驗收失敗與 `.17` 修正

- `.16` 代表性 15 張隔離 smoke 已處理 15/15：7 verified、8 review-required、0 failed，共 36 輪。36/36 都有正確 128-bit request ID、完整影像 SHA-256、`independent_pass=true`；前輪答案暴露、prompt contamination、runtime unhealthy、跨來源共用影像雜湊均為 0。既有單一 Dashboard 分頁的進度、照片、LLM 自然敘述與右欄卡片同步移動，沒有裸 JSON、亂碼或新增分頁。
- 試跑仍判定失敗：台南-714 原圖只支持 `FollowMe M7 32" / 12,990`，第一輪 raw JSON 也正確如此，但舊 `立牌` 借用阻擋把明確 FollowMe 型號送進一般型號清除；第二輪又無照片證據猜成 `FollowMe Pro M7 43" / 12,990`，舊守門只確認已進第二輪、未比較 model/price，遂錯誤 verified。
- `.17` 修正三處且不使用任何前輪答案：明確 FollowMe model 加本輪足夠同主體結構證據不得被 generic 立牌文字清除；Pro 43 必須有同輪可觀察的 Pro／43／S43FM／17,990 身分證據；2026 FollowMe 所有輪次 model/price 必須全數一致，二對一不得洗白既有衝突。
- 正式總進度仍為 `65,331/150,321`、資料夾 `44/136`、剩餘 `84,990`。正式 runner、continuity daemon 與 uploader 保持停止，Google Drive 未接觸；`model_benchmark.lock` 保留。完成 `.18` 十五張實拍驗收、既有分頁同步證明與 Git push 前不得恢復正式 OCR。

## 2026-07-16 12:46 `.18` 誤熔斷修正與隔離續跑

- `.17b` 在 15 張隔離驗證的第 15 張前安全停止：已完成 14 張，6 張自動驗證、8 張待複核、0 失敗。照片 137 的敘述明示普通 Smart Monitor M7、黑色短架與託盤且「非 FollowMe」，舊健康閘仍誤判為 FollowMe 結構衝突；沒有錯誤結果進入正式進度或上傳。
- `.18` 將友善名稱與實體 SKU 的一致性限制在既定同款映射，並把敘述熔斷條件限縮為未否定的 FollowMe 身分或明確白色移動架組合。113 項針對性測試、完整 critical regressions 與 production dashboard build 已通過。
- 舊 fuse 已保留於 `_ocr_audit/trials/runtime_health_fuse_rev17b_false_positive_20260716_123804.json`。`.18` 新 15 張隔離驗證完成 15/15：8 verified、7 review-required、0 failed，共 34 輪；request ID、影像 SHA-256、獨立輪次、無前輪答案、無提示污染與 runtime health 全部通過。714 三輪皆保持 `FollowMe M7 32" / 12990`，因相鄰同款同價的跨照片疑慮保守留待複核；137 完成三輪且普通黑色短架／託盤的「非 FollowMe」敘述不再誤熔斷。沒有錯誤結果進入正式進度或上傳。
- `.18` 全年清冊重建掃描 63,876 筆、2026 唯一來源 5,951 張；隔離試跑中已有 8 張具 `.18` 有效 trace，待跑 5,943 張，缺檔／衝突／無效列均為 0。正式 202601 第一群組 1,496 張已由唯一隱藏 runner 父子組啟動，既有唯一 Dashboard 分頁顯示「正在執行」、正式總進度 `65,331/150,321`、新版複核進度、自然語言與卡片同步，沒有裸 JSON；runner 會依清冊接續 202602–202605。uploader 與 Google Drive 仍封閉，`model_benchmark.lock` 保留。

## 2026-07-16 13:21 `.18` 正式內容閘失敗與 `.19` 修正

- 25 張內容節點發現已知真遠景 `M-台中市-北屯區-SF-北屯-650.jpg` 再次被第一輪錯列 `單機 / S27D300GAC / 3090` 並冒充 verified。這次前一張不是同款，證明 `.18` 的相鄰重複核心守門無法涵蓋非相鄰語意污染。正式接力器立即停止於 27/1,496，後端與介面保留、uploader 0；本段所有 `.18` 結果失去現行 revision 資格，不得上傳。
- durable fuse 已寫入 `known_distant_auto_verified_as_single` 與 `multiscreen_single_first_pass_escape`。650 全圖 SHA-256 為 `9e182f053a3c893a5c6a791d0abfb52e97eb52b945b0beeb962178d49025e549`，歷史多輪可確認其遠景身分；不得用檔名比對替代像素綁定。
- `.19` 一般規則：2026 單機候選若結構報告至少三台完整螢幕，第一輪永不驗證，必須三輪獨立且 view/model/price/unique_main/ownership 全數一致；任一差異即 unresolved。已人工確認的高風險原圖另以模型輸入像素 SHA-256 綁定期望 view，任何衝突直接成為不可隔離的 runtime-health failure 並熔斷；清冊另用原始檔 SHA-256 核對同一人審權威，兩種雜湊不可混用。123 項針對性測試、完整 critical regressions 與 production dashboard build 已通過。
- `.19` 五張隔離 smoke 完成 5/5、10 輪：3 verified、2 review-required、0 failed；revision/request/image/independent/prior exposure/prompt contamination/runtime health/invalid verified 全部正常。664 遠景證據未收斂、665 核心型號不一致，均正確 unresolved。新版清冊掃描 5,951 個唯一來源，3 張具 `.19` verified trace，650 以雙 SHA 人審遠景權威排除，待跑 5,947、缺檔／衝突／無效列均為 0。
- 正式 `.19` 202601 群組現為 1,500 張（原 1,504 扣除 3 張 `.19` verified 與 1 張人審遠景），由唯一隱藏 runner 父子組執行並會接續 202602–202605。原本唯一 Dashboard 分頁已核對為「正在執行」，總進度 `65,331/150,321`、複核數字、照片、LLM 自然語言與卡片同步，沒有待機、裸 JSON 或 `未提供`；uploader 0、Google Drive 未接觸。

## 2026-07-16 14:30 `.19` 鄰近 FollowMe 文宣誤熔斷與正式續接

- 正式批次在 26/1,500、`M-台中市-北屯區-TK3C-新文心-971.jpg` 第 2 輪安全熔斷。原敘述是「旁邊有 Samsung FollowMe 商品卡，但沒有白色垂直支架，所以不是 FollowMe」，結構為遠景、無強實體證據；舊敘述閘把旁邊商品卡錯當前景主體身分。沒有不健康結果進入 verified 或上傳。
- `narration_has_positive_followme_identity()` 現排除旁邊、背景、牆上、海報、宣傳、廣告與立牌等非主體語境；白色直立支架＋圓形底座等明確同主體結構仍獨立熔斷。實際事故記錄重播、29 項 runtime-health 測試與完整 critical regressions全部通過。
- 帶事故照片的全新五張隔離 smoke 完成 5/5、12 輪：3 verified、2 unresolved、0 failed；revision、request/image binding、前輪答案暴露、prompt contamination、runtime unhealthy、invalid verified 全為 0，971 完成三輪且舊 fuse 時間未變。舊 fuse 已歸檔後解除。
- smoke 完成後首次正式接力因後端仍指向 smoke 目錄而正確拒絕，未啟動 OCR 或 uploader。API 在 idle 狀態切回唯一正式 staging 後，隱藏 runner 由 26/1,500 精確續接；971 完成後成為 unresolved，正式流程跨過中斷點至 27/1,500 並開始下一張，fuse 未復發，uploader 仍為 0。
- 右欄 unresolved 卡片不得寫含糊的「判讀未完成／待複核」，也不得聲稱未配置的慢模型或未指定的人員正在裁決。現行文字為「三輪結果不一致／未通過」及「未列入成功結果，不會上傳」；統計標籤為「未通過」。版面比例不變。
- Drive 沒有新增：canonical receipt 最後一筆仍為 2026-07-11 11:33:57。帳本有 52,965 筆收據；2026-07-14 嚴格重建時只有 51,459 張仍列為 ready/uploaded-skipped，897 張為已上傳但依新規則需重審。完成本次 2026 finalization、重建 proof 並通過遠景/FollowMe 稽核前，不得新增上傳。

## 2026-07-16 15:05 `.19` 單照片內容矛盾收旂與上傳帳本確認

- 正式批次在 30/1,500、`M-台中市-北屯區-TK3C-新文心-975.jpg` 第 2 輪停止。原圖只有黑色圓形桌上底座；敲述前段也說黑色底座，後段卻虛構「白色直立支架與圓形底座已確認」，結構化 FollowMe 實體證據為空。`structured_narration_followme_conflict` 為真實內容漂移警報，不是介面誤報；該輪沒有進入 verified 或上傳。
- 收旂規則已改為：同一 source identity 的可隔離 FollowMe／敲述矛盾最多只做三輪無記憶獨立判讀；第三輪後仍不安全就固定 unresolved，不得 verified、不得上傳，主批次繼續。同一衝突類別在同一工作目錄的不同 source identity 再現時，才視為跨照片漂移而停整批。事件來源清冊持久化於 `.ocr_retry_queue.json`，切換工作目錄才重置。
- 2026-07-16 15:11 正式照片北屯新文心-976 觸發 `structured_authority_material_conflict:model`：同輪結構與實圖均支持單一 FollowMe 實體、白色立架、圓底座、附屬價牌與 12,990，但敘述沒有看見足以區分 M5/M7/Pro 的版本字樣，因此結構權威正確把猜測的 `FollowMe M7 32"` 清為 null。這不是可上傳成功，也不是批次級模型漂移；內容健康閘改為只在「單機＋唯一主角＋價牌歸屬 matched＋有效價格＋足夠同主體 FollowMe 強證據」全部成立時，允許同張照片最多三輪無記憶獨立判讀。第三輪仍無版本證據即隔離 unresolved；同類事故出現在第二個 source identity、或任何價格／視角／弱證據衝突，仍立即 durable fuse。正式復跑前必須以含 976 的全新五張 smoke、完整回歸、既有單一 Chrome 分頁及 Drive 封閉狀態共同驗證。
- 15:24 含 974–978 的全新 fuse-active 五張隔離 smoke 已自然完成 5/5：1 verified、4 unresolved、0 failed，共 14 輪；revision mismatch、request/image binding、非獨立輪次、前輪答案暴露、prompt contamination、invalid verified 全為 0。975 首輪一個 `structured_narration_followme_conflict` 被限縮為同張無記憶重試，沒有 verified；976 本次兩輪皆獨立得到 `FollowMe M7 32" / 12990` 且 runtime healthy，跨過原停機位置後繼續至 977/978。因模型輸出具隨機性，本次實圖沒有再次重現 976 的 model-null 分支；該精確分支由永久單元測試覆蓋「前兩輪可重試、第三輪只可 unresolved、弱證據／缺價／價格衝突仍立即 fuse」。隔離結果不計入正式 65,331，也未啟動 uploader；Drive canonical receipt 仍為 52,965 筆，mtime 2026-07-11 11:33:58。
- 30 項 runtime-health 單測、Python compile、`git diff --check` 與完整 critical regressions 全通過。保留舊 fuse 不先刪除的全新 5 張隔離 smoke 完成 5/5：1 verified、4 unresolved、0 failed，共 13 輪。revision mismatch、request/image binding 錯誤、非獨立輪次、前輪答案暴露、prompt contamination、invalid verified 均為 0。975 在第 2 輪的內容衝突被限制於同照片第 3 輪，最終 unresolved；批次自然結束且舊 fuse 時間／來源均未被偷改。
- 正式狀態回報仍為 65,331/150,321、資料夾 44/136、202601 `.19` 複核 30/1,500（verified 14、待裁決 16、0 failed）。Google Drive 最後新增仍為 2026-07-11 11:33:57，canonical receipt 52,965 筆；2026-07-14 守門重建為 ready/uploaded-skipped 51,459、stale uploaded review 897。隔離 smoke 不計入正式 65,331 進度，也沒有啟動 uploader。

## 2026-07-16 20:35 `.26` 邊緣裁切過度矯正熔斷

- `.25` 已用三張實拍隔離驗收證明 `台中旗艦-940` 為中央一台完整、左右鄰機被原圖邊界裁切的單機，並讀到 `S32FM803UC / 12,900`；`台中旗艦-939` 為 FollowMe Pro，`健行-1385` 為真遠景。三張皆無前輪答案暴露或提示污染。
- 正式 `.25` 重跑到 4/1,500 後，內容監控發現 `草屯-670` 原圖是寬廣走道與多排完整螢幕，卻被第一、三輪的弱單機票以 `two_pass_single_view_consensus` 錯誤定案。正式批次已在下一張開始時經 `/api/stop` 停止並確認 `is_running=false`；port 5002 後端與既有 Dashboard 分頁保留在線。這 4 張 `.25` 結果不得當成 `.26` 正式成果。
- `.26` 把裁切定義限於螢幕外框真正接觸或穿出第一張原圖最外側；貨架、柱子、前景物遮擋不算。緊密近拍例外不得套用一整排、展示牆、多層貨架或寬廣走道。若一輪提供有效 `3+ / unique_main=false / no owned label` 遠景結構，另兩輪只是無型號、無價格、無 matched 價牌、無 FollowMe 實體證據的寬景弱單機票，遠景結構否決弱票；兩輪身分綁定單機或 FollowMe 實體共識仍優先，以免破壞 940。
- 同步修正逐張上傳的舊收據短路：只有同一現行 revision、同一來源 bytes 與同一目標檔名的 receipt 才可視為已完成；舊 revision receipt 會保留到 `superseded_receipts` 後重新排入 corrected job，不能再讓 `.21` 收據吞掉 `.26` 結果。
- 下一步必須先跑完整回歸與包含 `940 / 939 / 1385 / 670` 的隔離實拍驗收；確認四張精確分類、每張最多三輪、無記憶污染後，才可隱藏替換 port 5002、在既有分頁核對 50/50／總進度／自然敘述／縮圖／上傳狀態，再以 `restart=true` 清除 `.25` 四張並重跑 `.26`。Git 只可在這些證據成立後提交。

## 2026-07-16 21:22 `.28` 原圖邊界計數與寬景弱票修正

- 使用者以 `台中旗艦-940` 原圖確認：只有中央螢幕四邊四角完整；左機穿出原圖左界、右機穿出原圖右界。介面第三輪曾錯說三台完整，證明 `.26` 的文字規則仍可能被模型整段看錯。
- 同時抽查正式 `.26` 最新結果發現 `中清-1528` 是整面展示牆、上／下有多台完整螢幕，三輪卻以 `單機(3) / 遠景(7) / 單機(8)` 被 `two_pass_single_view_consensus` 錯定為單機。另有 972 型號錯配、1529 首輪 FollowMe 實證誤報、1530 最終型號遺失，均列入後續內容稽核，不得只看進度數字。
- 正式批次已安全停在 `37/1,500`；port 5002 仍只有一個 listener，Dashboard、既有分頁與逐張 uploader 保持在線。停止時串流上傳已完成該批第 45 張、canonical receipt `53,010`、pending/working 皆 0。
- `.28` 將原圖四邊接觸清單與 `左不完整 + 中央完整 + 右不完整 = 1` 寫入主提示、共同輸出契約及第二／三輪提示。守門擴充「左右兩側」措辭偵測，寬廣多螢幕但無可歸屬型號／價格／標籤／FollowMe 實體的單機票不得直接驗證，也不得供應一般單機多數。
- `940` 與 `1528` 已用原始檔及實際模型輸入像素 SHA-256 加入人工回歸權威；前者不可驗證為遠景、後者不可驗證為單機。這只保護已人工確認像素，通用規則仍由提示與守門負責。
- 針對性 evidence/finalizer/runtime-health 測試 159 項與完整 critical regressions 已通過。隔離實拍 run `20260716_221131_225238` 完成 `940 / 939 / 1528 / 1385 / 646` 五張、全部 verified、每張恰好三輪、revision `.28`、request/image binding 正確、前輪答案暴露與 prompt contamination 均為 0。`940` 最終為 `單機 / complete_screen_count=1 / S32FM803UC / 12900`；`1528`、`1385` 為遠景；`939` 保留 FollowMe；`646` 為普通單機。既有 Chrome 分頁已實際核對 50/50、自然語句與五張卡片；隔離結果未增加 Drive canonical receipt，正式總數仍為 `53,010`。恢復正式 202601 必須用 `restart=true` 丟棄 `.26` 的 37 張舊結果並從 `.28` 乾淨重跑。

## 2026-07-17 04:56 `.33` 正式續跑與上傳恢復

- 正式 636 因單張 model authority omission 被誤升級 fuse；637 是明確整排遠景，也因同類內容理由誤停。已把乾淨同圖 model omission 保存為最多三輪的內容票，不再當傳輸／綁定技術錯誤；真正的 prompt、prior-answer、cross-photo、request/image、price fault 仍 fail closed。
- 兩次針對性測試與完整 critical regressions 通過；最終五張隔離 `1385/939/940/636/637` 為 5 verified、0 review、0 failed。636 完成三輪像素定案為 `單機/S24F332EAC/2390/count 2`；637 定案 `遠景/無型號/無價格`，無第 4 輪。
- 正式 636 的跨程序 trace 為同 source/hash 的 call 1 與 3，持久狀態證明總呼叫已達 3；以綁定像素權威修復並於 04:43:48 上傳，沒有再叫模型。修復工具現為 enqueue-first，再原子寫回結果，並排除沒有 canonical period 的 smoke trace。
- uploader 曾被死 PID 31224 的 stale lock 卡住；已歸檔 lock，只恢復既有 hidden uploader。`uploaded 81→105`、canonical `53,052→53,072`、pending 1，04:55:56 receipt 仍在前進。
- 正式 OCR 04:56 為 `202601 131/1,500`、verified 131、review 0、failed 0、fuse inactive，正在太平-1099 第 2 輪；總盤仍 `65,331/150,321`、44/136、剩 84,990。複核不回灌初次辨識總盤，所以右上總數不會隨每張複核增加，子進度必須持續增加。
- 既有 Chrome 分頁未新增；Dashboard asset `a5139e765ffdec30` 已自動同分頁刷新。header/status 在目前視窗寬度換列，總進度、資料匣與目前檔案可讀；主區既定半螢幕比例未改。
- 完工日期：實測淨產能目標 `2026-09-06`；對長官保守承諾 `2026-10-31`。若再次出現停機日，下一個固定報告點必須用剩餘量／24 小時 verified 與 receipt 增量重新估算。

## 2026-07-17 05:20 `.34` 現場跑歪攔截、修正與續跑

- 內容抽查抓到 `太平-1105` 前兩輪把照片價牌 `7,490` 多插入一位成 `74,990`，舊定案錯用兩票多數。原圖、第三輪 JSON 與第三輪獨白均支持 `7,490`；已加入插入數字守門與完整影像 SHA 像素權威，修正結果為 `S27CG552EC / 7,490 / ↑` 並已重新上傳。
- `太平-1099` 三輪後曾留下技術狀態；完整三輪證據實為一輪 `5台／無唯一主角` 加兩輪無型號無價格的整排陳列描述。已以 `three_pass_wide_scene_structural_consensus` 結案為遠景、無型號、無價格並排入上傳。
- 修復工具已修正完整 `[1,2,3]` 被較短 `[2,3]` 尾端覆蓋的缺陷；正式後端在 idle boundary 以 hidden window 單次替換，既有 Chrome 分頁與主版面未改。
- 續跑起點：202601 `140/1,500`、verified 140、review 0、failed 0、fuse inactive；stream uploaded 115、canonical 53,080、pending 0，最新 receipt 為修正後的 1105。量測日 `2026-09-06`、保守承諾 `2026-10-31` 不變。

## 2026-07-17 07:45 `.35` 六張實拍驗收與正式恢復

- `.34` 最新 20 張內容稽核抓到 6 張明確錯誤：317 寬景被弱單機票定案；318 捏造畫面外底座／託盤；1319/1320/1321 把左右碰圖界螢幕算完整；1325 漏算背景完整螢幕。51 筆舊 trace 的 prior exposure、prompt contamination、request binding、independence 與跨圖重複均為 0，根因是結構／定案規則，不是記憶感染。
- OCR 在正式 192/1,500 的照片邊界停止，port 5002 Dashboard 保持在線；uploader 排空後停止。五張已有完整三輪者用 exact pixel authority 修復並排隊，318 補做第三輪。
- 初次隔離暴露兩個守門缺陷：預期 `price=None` 被字面 `"None"` 誤判衝突；第三輪像素權威已修正內容後，舊 unresolved 決定仍會把它打回。另有 known-pixel + narration FollowMe 複合衝突會在第二輪錯誤停整批。三項均已修正並加入永久測試。
- 採認場次 `20260717_072657_073759` 完成 6/6 verified、0 review、0 failed、每張恰三輪。317 遠景 count3；318 FollowMe Pro M7 43/17990/count3 且只保留 direct branding；1319/1320/1321 分別為 count1 的 S24F332EAC/2590、S27D300GAC/3290、S27F612EAC/4990；1325 FollowMe M7 32/14990/count3。18 筆 trace 無前輪答案、無提示污染、無非獨立或 request 綁定失敗，六張第三輪 runtime health 全健康，active fuse 0。
- 186 項 targeted tests 與完整 critical regressions 通過。正式 5002 隱藏替換為 `20260717.35`，從 192/1,500 原位續跑；隔離 5003 已關閉，OCR/uploader 各只有正式背景程序。
- 現場三次切換核對 `1109→1110→1111→1112`：目前檔名、預覽、LLM 逐字內容、右欄最上方卡片完全一致；子進度 196→199/1,500，累計判讀 16,202→16,209，上傳總數 53,121→53,125，資料匣完整、無水平溢位、未 reload／未新開分頁。
- 三筆 `.35` Drive 閉環已核對 working→receipt→canonical，source／目標／Drive ID 各自唯一；`.35` failed=0、重複 filename/Drive ID=0。正式流程持續運行，下一接手者不得重新啟動或 restart。
- 完工日期仍以 `2026-09-06` 為量測目標（約 1,667 張／日、69.5 張／時）；`2026-10-31` 為保守承諾。固定報告必須同時列出 24 小時 verified 增量與 receipt 增量，不能只報累計總盤。

## 2026-07-17 10:05 `.41` 內容漂移修復與真圖驗收

- 最近 24 小時正式 trace 只有 259 張不同照片通過守門；剩餘 84,990 張照舊速率的誠實預測完成日是 2027-06-11。2026-09-06 仍是加速目標，但須連續達到 1,667 張／日（69.4 張／小時、目前 6.43 倍）後才可成為可信預測。
- 抽查發現 `Lalapo-279`、`潭子-1397`、`SMS-348/356/357` 有邊緣螢幕完整台數、背景系列名稱與主角價牌歸屬漂移。正式 runner 在照片邊界停止，port 5002 Dashboard 與 uploader 保留在線，未讓後續錯誤繼續累積。
- `.41` 加入「部分可見／局部露出／未見完整外框」台數矛盾守門、背景 `Odyssey/G7/G8/M8/Smart Monitor` 借名守門，以及五張完整影像 SHA-256 人工稽核權威。154 項針對測試及完整 critical regressions 均通過。
- 隔離 run 為 5/5 verified、0 review、0 failed；每張恰三輪，無前輪答案暴露、無提示污染、無第 4 輪。最終：279 單機 count2/S40FG752EC/29,900；1397 單機 count1/S27D392GAC/4,290；348 單機 count1/無型號/無價格；356 單機 count1/S32DM803UC/14,900；357 單機 count1/無型號/39,900。
- 正式 backend 已隱藏切換至 `20260717.41`，未新開瀏覽器或終端視窗；八張受影響照片排入正式優先重跑，逐張 uploader 同步以 `.41` 重啟。取得新 revision 收據後繼續 202601。
- 2026 來源盤點為 5,951 張：202601=1,504、202602=1,598、202603=357、202604=1,587、202605=905。現行 backfill staging 為 5,947 張；202601 的 4 張未入 staging 是南投-666/667/669 與北屯-650，均為先前內容事故照片，年度完成前必須另行像素綁定重驗，不得遺漏或冒充已完成。
- 10:27 正式 `.41` 狀態為 202601 `228/1,500`：224 verified、4 content review、0 failed、fuse inactive；Drive canonical 53,154、pending 1、working 1。八張優先修復均已有 `.41` receipt 與非空 Drive ID，正式批次已接回新照片。以當下約 50–55 verified/小時推算，202601 尚需約 23–25 小時；全部 2026 尚需處理 5,719 staged 加 4 張事故照片。滾動 24 小時舊實績 259/日則會拖到約 2026-08-08，只有不中斷維持現場速率才可能在 2026-07-22 左右完成 2026。

## 2026-07-17 15:52 `.42` 總盤前進、選擇性升級與三輪內容結案

- 使用者看到總盤長期停在 `65,331` 的原因有兩層：202601 舊照片複核本來不增加初次辨識總盤；切換全新的 202606 staging 後又發現暫存葉節點沒有回算原來源資料夾，會讓新照片實際完成但總盤不動。來源 map 回算已修正並有永久回歸；正式 202606 已使總盤由 `65,331` 前進至 `65,356/151,714`。
- 正式推論改為選擇性升級：健康的 `單機＋型號＋價格＋證據一致`（包含證據完整的 FollowMe）第一輪即結案；官方價格差異只標示 `↑/↓/✓`，不再單獨觸發模型重跑。遠景、缺欄位、FollowMe 實體疑點、結構／敘述衝突才進第二、第三輪。LM Studio 維持 Qwen3-VL 8B、Parallel 1；現場單輪約 15–18 秒。
- `南投-530` 三輪均為同一原圖 SHA、request binding 正確、無前輪答案、無提示污染；第一輪遠景、後兩輪皆獨立看到同主體白色立架／圓底座／FollowMe 產品卡。舊邏輯誤把內容分歧標成技術錯誤；`.42` 改為兩輪 FollowMe 實體共識定案，型號／價格沒有兩輪一致就維持空白。530 已用既有三輪證據離線結案，沒有第 4 次呼叫，並於 15:48:46 以 `FollowMe_型號未細分-無價格` 確認取得逐張上傳收據。
- 47 項三輪定案測試與完整 critical regressions 全通過。正式 backend 與 uploader 在照片邊界、舊上傳佇列歸零後以 hidden window 換至 `.42`；既有 Chrome 分頁未新開。Dashboard 已實際看到 `65,356/151,714`、202606 `25/1,393`、自然 LLM 判讀、同步圖片／檔名／右欄卡片、上傳總數 53,385 持續增加。
- 邊界停止前 `草屯-541` 的第一輪模型已回應但尚未寫入 evidence trace；換版後只允許剩餘兩次呼叫，因此留下真正的 `three_healthy_bound_passes_required` 技術例外，沒有冒充內容成功，也沒有第 4 輪。原圖人工檢視為明確多排多層螢幕遠景；後續只可用相同完整像素 SHA 的人工稽核權威結案，不可再叫模型。

## 2026-07-17 202606 → 202601 自動交接守門

- 新月份優先批次不再依賴人工在完成後按「繼續」。`tools/continue_after_period_priority.py` 只監看唯一的 202606 staging leaf；若它在未完成時意外 idle，只對原 leaf 做 `restart=false` 原位續跑，不重啟模型、不重跑已完成照片。
- 交接條件固定為：202606 每張都有唯一 verified 且 upload-queued 的終局記錄；記錄的 input SHA 必須逐檔重建並匹配正式送模 full-scene bytes（大圖是縮至長邊 2560 後的 quality-95 JPEG，不是 raw JPG），Drive receipt 的 source SHA 必須逐檔匹配 original source raw file，published SHA 再匹配發布檔，三種 SHA 不得混用；`processed=success=verified=total`，failed/review/unknown 全為 0，後端 idle、逐張上傳 pending/working 為 0、唯一 uploader 存活、runtime fuse 不存在、正式 backend contract 為 v19.45 strict 且 evidence revision 不低於 `.42`。任一不符即 fail closed。
- 切換前會先驗證候選 CSV 第一群組與保留的 `202601_商化照片-202601_6403a632` 在 period、來源資料匣 SHA-1 短碼與照片數均完全一致。條件成立後才把同一個 port 5002 backend 切回 202601，並再次讀回 API 證明以 `restart=false` 運行。
- 後續由唯一 hidden `rerun_staged_candidates.py --resume-existing-then-continue --keep-staging` 原位接續；CSV 固定順序為 202601（1,500）、202602（1,598）、202603（357）、202604（1,587）、202605（905）。monitor 的單例鎖保留到 runner 結束，其他 staged runner、90 分鐘無進度或整體逾時都會寫 alert，不會開第二套程序或可見終端機。
- 十項單元測試與正式路徑唯讀 preflight 已通過；除交接流程外，也覆蓋缺少 Drive 收據與舊 summary 冒充新成功。preflight 證明目前 CSV 順序與 202601 staging 身分相符。正式 monitor 必須由 `Start-Process -WindowStyle Hidden` 啟動，日誌在 `logs/`，完成 receipt／失敗 alert 在 `_ocr_audit/`。
- 內容監控另抓到 `台中LalaportSES-301` 三輪後把清楚的 Follow Me 4K 展示誤細分成 `Pro M7 43"`。主代理已直接檢視完整原圖：同一前景主體具品牌字樣、白色直立移動架、托盤與圓底座，但無足夠型號／價格像素。已把 source/input SHA 綁定的安全真值加入像素權威與永久測試；離線三輪定案只能輸出 `單機／FollowMe（型號未細分）／無價格`，不可第 4 次呼叫或保留猜測版本。

## 2026-07-17 `.43` request binding fuse 修復與正式恢復

- 正式 202606 在 `218/1,393`、總盤 `65,549/151,714` 時，`新大雅-1178` 的第三次呼叫逾時且無法驗證 request binding，舊流程把正規化後的 `request_binding_unverified` 漏出單張 containment，錯誤熔斷整批。不是內容判錯，也不是需要第 4 輪。
- `.43` 將 raw 與 normalized request-binding 同義錯誤統一限制於單張；若前兩次同 SHA、完全獨立且綁定成功，並一致讀到同一非 FollowMe SKU／價格，可丟棄第三次未綁定回覆後結案。`新大雅-1178` 前兩次均為 `S32CG552EC／6,990`，已用 recovery receipt 證明兩次有效、第三次作廢、第四次未呼叫，並取得 Drive receipt。
- 110 項 targeted tests 與語法編譯通過。port 5002 backend 在照片邊界 hidden 換版，既有 Chrome 分頁未新增；正式批次以 `restart=false` 從 202606 原位續跑。唯一 `continue_after_period_priority.py` monitor PID `30480` 已恢復，仍負責 202606 完成後接回 rev19 保存的 202601–202605 staging。
- 視覺驗收顯示總盤 `65,578/151,714`、202606 `247/1,393`，圖片／檔名／LLM 即時自然文字／右欄卡片同步，近期平均 `13.39 秒`。之後總盤續增至 `65,589`。
- 換 `.43` 後發現 uploader 尚載入 `.42`，32 個 `.43` job 被精確標為 `stale or invalid stream upload job`；正式 OCR 未停。已只重排這 32 個可證明同版本 job、保留舊 95 個失敗檔，將 uploader hidden 換到 `.43`。新收據已由 canonical `53,516` 增至 `53,517`，pending 正在依序排空；後續監控必須同時看 processed 與 canonical uploaded，禁止只看 OCR 數字。

## 2026-07-17 `.45` 單張內容衝突恢復與 Drive 根目錄遷移

- 最高鐵律：Dashboard、正式 OCR 與逐張上傳持續正常運作。單張照片內容衝突不得停止整批；最多三次獨立無記憶呼叫後，以共識或綁定原圖像素權威如實結案並立即排入上傳。只有會影響後續多張的系統性技術污染才可全域停止。
- `M-台北市-信義區-秀翔培芝-微風南山-742.jpg` 在第 3 次呼叫後被舊規則誤設 active fuse。三次呼叫已證明 RequestID 皆不同、input SHA 相同、沒有前輪答案或提示污染；人工原圖權威為 `遠景／5 台完整螢幕／無型號／無價格`。已用窄範圍 recovery 結案、清除該張 retry state、排入上傳並保存 fuse history，沒有第 4 次呼叫。
- backend 與 uploader 已同步 hidden 換到 revision `20260717.45`，正式 202606 由 `356/1,393` 恢復後持續增加；畫面實測總盤 `65,700/151,714`、202606 `369/1,393`、狀態正在執行，LLM 自然語言、照片／檔名、右欄卡片及上傳數同步。742 的 Drive exact receipt：ID `1x5naroxGTEOgrScGsbIrMf7PsG7Z-y-W`，revision `.45`。
- `continue_after_period_priority.py` 已重新以單例背景模式啟動，監看唯一 202606 leaf；202606 完成後接回保留的 202601 staging，再依固定 CSV 接續 202602–202605。不得開第二套 OCR 或可見終端機。
- Google Drive 年份資料夾 `2022`–`2026` 已從舊 `00_商化照片(已整理)`（ID `1xBaWDRjlcP-gMV-bM0K1S4gOJZ0QJJHK`）直接移到 `00_商化照片`（ID `16X5qALC3zRYc7PpnexXLYprorBzBtT_f`）之下；沒有 `已整理` 中介層。舊資料夾已空但保留，`202607` 未移動也不得處理。rclone `samsung_ocr_drive` 已改指向新根 ID。

## 2026-07-18 `.52` 與線上回報表同步型號規則

- 新增 `skills/model_catalog_rules.py`，集中管理完整型號正規化、六款 FollowMe 名稱、13 組面板／套裝對照與 `FollowMe 型號未細分`。主程式、型號比對、官網查價、補跑、回歸與改名工具已改用同一份權威。
- 官網碼只移除前導 `L` 與結尾 `XZW`；安全修正限於精確命中、唯一 1–3 字元尾碼補全，或同尺寸同系列內唯一的有限近似候選。多候選時不得自動填入不存在或不確定的型號。
- FollowMe 正式名稱為 M5 27、M5 32、M7 32、Pro M7 32、M7 43、Pro M7 43。面板 SKU 只核對一般版系列與尺寸；Pro 必須有同一台實機或附著牌面的明確 `Pro`。價格、43 吋與共用 SKU 都不得推導 Pro。
- 所有 FollowMe 都是 Smart 系列，不要求 OSD。價格只保存與比對現場價／官網價，不參與型號身分判斷。
- 本次只更新程式、提示詞、測試與 Git，不重啟目前可能執行中的 OCR、不碰照片及雲端資料；`.52` 會在下一次安全重啟後載入。
- 原工作目錄內既有的 Dashboard 暫存檔及其他未追蹤工具不屬於本次提交，必須原樣保留。

## 2026-07-18 `.52` FollowMe 寬景幾何補強與兩張閉環

- 低功耗原圖稽核證實 `竹北 SF-708` 至少五台完整螢幕；舊定案器因第 2、3 輪都辨識到同一 FollowMe 實體，曾準備錯誤輸出 `單機／FollowMe M7 32／12,618`。新版規則將 FollowMe 實體視為「場景內存在證據」，不得覆蓋整張照片 3+ 完整螢幕的遠景幾何。
- `新北投-1414` 亦確認至少五台完整螢幕；右側 FollowMe 宣傳／展示不可建立唯一主角或價牌歸屬。兩張均使用既有三次、同圖、無前輪記憶的證據離線定案為 `遠景／無型號／無價格`，沒有第 4 次呼叫。
- 兩張已逐張上傳且取得正式 receipt：708 Drive ID `1kmfJeRMYladBeW7hEi1n23eAK0PfMszt`；1414 Drive ID `1Fhln7SWm8yEB9BMATdX1xLCP-E0iOlyE`。正式 uploader `pending=working=0` 後才宣告閉環。
- 離線修復第一次誤把 `--output-dir` 指向 staging leaf；兩個工作沒有被正式 uploader 看見、沒有錯傳。已用相同 source identity 排入正式 `D:\00_商化\00_已OCR照片\_drive_upload_stream` 並取得上述 receipts，誤放 outbox 完整封存於 `_ocr_audit\misrouted_upload_queue_archive\20260718_114641_708_1414`。
- 程式新增 708／1414 回歸並保留真正單機 753 與背景裁切 FollowMe 案例；三輪定案測試 58/58、完整 `tools/run_critical_regressions.py` 通過。正式 OCR 與 Dashboard 未重啟；磁碟版規則於下一個完整安全邊界 hidden 載入。
- 2026-07-18 12:06 串流 uploader 因 Windows 短暫拒絕以 `os.replace` 更新 `status.json` 而退出；照片本體、結果與 pending job 均未遺失。已將狀態檔替換改為有限退避重試並新增回歸，僅以 hidden 單例恢復 uploader，未重啟 OCR、Dashboard 或瀏覽器。恢復後 pending `31→27`、receipt `1316→1320`、canonical `54239→54243`，證明逐張上傳重新前進。

## 2026-07-18 `.52` 九張既有三輪複核閉環

- 低功耗原圖稽核逐張否決「只相信離線候選」：台中旗艦 1062 是一台 FollowMe 實體但型號／價格不可安全歸屬；三創 731 與新北投 1413 都是 3+ 完整螢幕遠景；員林 467 是 `S27FG532EC／5,790`；中華 1048 是 `S32DM803UC／19,900`；大葉高島屋 114、新四維 919、高雄大樂 231 是遠景；高雄建國 435 只有兩台完整螢幕，依專案契約定案為單機且型號／價格留空。
- 九張均證明已有第 1／2／3 輪、相同 source identity 與 input SHA、不同 RequestID、`independent_pass=true`、無 prior-answer exposure／prompt contamination。以完整影像雜湊綁定像素權威後離線定案，沒有第 4 次呼叫。
- 九份正式 Drive receipt 全部取得：1062 `11WwIgXjwUIoRliwz653Hcg_raloYtHbg`、731 `1jLDLl1FTZvT2snTt3K3-ds5xkM9L4-bo`、467 `1TbnuDYK7BDPNOILDx6zXBFFSKT9MpBzb`、1048 `1yFgJ2DXfybMNtoemSuyexnpwdak1yOH0`、114 `1ihO0tr7EqlT2GzeA1GQ1S48OVw1TFyLT`、1413 `1QN6b7RQzNyQGiq59kvP3B4J3hclggJfm`、919 `1Kzfa3W_2yvjSOzBIqTXWKfw6LDeCA7DY`、231 `1I4mOHVgKc90RbD6ZvDf4jEznc9QxQYVI`、435 `11CcFRHmJFkdI4gWAECPi6ji4BDvCZUv_`。
- 套用與上傳期間正式 OCR／Dashboard 持續前進，`202606 1334→1364/1393`、失敗 0、fuse inactive；stream pending 最終歸零。新增九張 authority 回歸且完整 `tools/run_critical_regressions.py` 退出碼 0。

## 2026-07-18 14:16 最新接手狀態（高於本文件較早進度）

- `202606` 已完成 `1393/1393`，成功、verified 均為 1393，`review_required=0`、`failed=0`、runtime fuse inactive；這批不再需要模型判讀。
- 最後兩張硬上限案件 `Game休閒館統領-408`、`TK3C 龜山-1357` 均證明三個模型呼叫名額已消耗、只有兩份乾淨 durable 輸出。已用完整影像權威精確綁定為 `遠景／無型號／無價格`，沒有第 4 次呼叫；Drive IDs 分別為 `1bEpcrOIJ01fwmosrdySJK8jcNV6U-qkH`、`1OKoXiNyQZUJCAejRP2ni9mJnMZH7xUgy`。
- 其餘 68 張 review-required 已由低功耗完整原圖稽核，決策清冊與 API 集合精確相等；authority manifest 綁定 source item、來源 SHA、input SHA 與三輪 trace。全部使用既有證據終局結案，沒有追加模型呼叫。
- 14:16 串流 uploader PID `28180` 正常，待傳由 31 持續下降至 21，最後成功上傳時間 `14:15:55`；不得因 OCR 已 idle 就把仍在上傳的資料夾宣告完整閉環。
- hidden continuation monitor PID `38568` 正在等待上傳排空，日誌為 `logs/period_priority_continuation_20260718.jsonl`。待 `pending=working=0` 後會核對 1393 份 source map、trace、published source 與 Drive receipt，再以 `restart=false` 把同一 port 5002 backend 切回 `202601_商化照片-202601_6403a632`，接續既有 202601→202605 清單。
- 接手者不得手動再啟動 monitor、uploader、backend 或瀏覽器；先讀 monitor 日誌與 `/api/status`。若 monitor fail closed，依 alert 的單一原因修正後從照片邊界接續，不可盲目重啟或開出重複進程。
## 2026-07-18 16:04 continuation state

- The project endpoint is not 2026. Source discovery now contains 137 folders
  and 151,714 supported photos after adding 202606. The restored hidden
  `rerun_staged_candidates.py --resume-existing-then-continue` runner owns the
  fixed 202601–202605 chain without restarting the active photo. After current
  year closure, continue the remaining 2025 item, all-year questionable
  verification for already initially processed years, and historical OCR down
  through 2015.
- Port 5002 remained running while repair work proceeded. During the repair
  window 202601 advanced from 532 to at least 556/1,500, fuse remained absent,
  and the upload backlog continued decreasing. A low-power visual audit of
  three recent photos found raw narration, structured evidence and terminal
  results consistent; one incorrect middle-pass single vote was correctly
  rejected and the third-pass distant result was accepted.
- `tools/revalidate_frozen_guard_results.py` was added for frozen old-revision
  results. It replays stored raw responses through current `.52` rules after
  exact source/run/hash/independence proof and never performs another model
  call. Sixteen `.41` tasks were safely revalidated and queued; their durable
  manifest is
  `D:\00_商化\00_已OCR照片\_ocr_audit\frozen_guard_revalidation\20260718_160411\manifest.json`.
- One photo, `M-南投縣-南投市-SF-南投-533.jpg`, remains `.41` at call 2/3:
  current rules reject the incomplete model token and 2026 single-unit missing
  model evidence. Do not restamp it. Use its one remaining independent call at
  a safe photo boundary, then enqueue its truthful terminal result.
- Upload enqueue equivalence now ignores only volatile enqueue timestamps and
  superseded-receipt audit metadata. All identity, hash, plan, target name and
  result fields still have to match. This makes a partially applied
  multi-photo recovery safely retryable without duplicate jobs.

## 2026-07-18 16:15 all-years unattended continuity restored

- The production endpoint remains all 151,714 supported photos, not the 2026
  cycle. The root-bound continuation request was migrated in place to guard
  revision `20260718.52`, preserving the original authorization time
  `2026-07-14T20:19:21`; its source and output paths were byte-checked against
  the existing directories.
- The machine still had the obsolete four-hour `ocr_upload_watchdog.ps1`
  scheduled action even though the repository contract requires
  `ocr_continuity_supervisor.ps1` at five-minute intervals. Re-registering the
  protected task was denied by Windows and made no runtime change. The
  documented non-admin fallback was therefore installed instead: HKCU Run,
  the current-user LIMITED `SamsungOCR_UserContinuityEnsure` five-minute task,
  and exactly one hidden `ocr_continuity_daemon.ps1` process (initial PID
  `35464`) with its atomic lock. Keep the old four-hour task as a backstop; do
  not start a second daemon.
- The daemon's first supervisor check returned
  `planned_backend_upgrade_recovery_active`: the existing staged runner still
  owns live work, so the supervisor correctly performed no restart or switch.
  OCR/uploader/runner PIDs were identical before and after installation,
  backend remained running, and runtime fuse remained absent.
- At 16:15, 202601 was `580/1,500`. A second low-power read-only audit of the
  latest three terminal photos found original pixels, narration, structured
  evidence and final result consistent; there was no prior-answer exposure,
  prompt contamination or fourth call.

## 2026-07-18 16:35 三輪終局補強與全年度終點再確認

- 使用者再次確認 2026 不是終點。正式終點仍是 2015–2026 全部 `151,714` 張、`137` 個資料夾逐張定案並取得 Drive 精確收據；202601–202605 完成後由全年度 continuity supervisor 接續 2025→2015，不得停在當年度待機或把 2026 完成誤報成全案完成。

## 2026-07-18 17:07 離線終局與 Dashboard 雙表示同步

- 唯讀追查 `大葉高島屋-182` 證實正式 `ocr_meta`、durable upload job 與原圖都支持 `遠景／無型號／無價格`；沒有舊 FollowMe 結果的 Drive receipt。錯誤顯示來自成功檔的 `annotations[0].result` 仍保留三輪前的 `FollowMe M7 32"／12,990`，以及 presentation history 沒有離線終局事件。
- 離線 finalizer 已改為原子同步 `ocr_meta` 與 Label Studio annotation，並追加可重入的「第三輪終局定案」presentation event；不覆寫三輪 trace、不增加模型呼叫。`tools/test_three_pass_finalization.py` 61/61 通過。
- 182 已重新套用零模型定案；`/api/success_records` 現為唯一一筆遠景、verified=true、review=false，歷程 API 最後一筆為 `decision=accepted` 的遠景終局卡。正式 pending job 仍是遠景，等待 uploader 的唯一 Drive receipt。
- 唯讀稽核發現部分照片已完成三次乾淨、同圖、無記憶呼叫，仍因終局規則過度保守被標成技術停止。新增窄範圍規則：只有第一輪因鄰近不同照片出現相同型號／價格而警示，後兩輪都以相同 input SHA、不同 request、無提示／前輪污染，且視角、完整台數、唯一主角、價牌歸屬、型號、價格與 FollowMe 實體證據完全一致時，第三輪可清除警示並結案；第二、第三輪再出現跨照片警示、缺身分、runtime 不健康或雜湊不一致仍失敗封閉。
- 多螢幕終局同步補強：敘述明確表示三台以上完整入鏡時，納入寬景幾何證據。一張結構正確的遠景票可否決兩張同時承認三台以上完整螢幕、卻自相矛盾標成單機的票；終局固定為遠景／無型號／無價格，不借用附近價牌。
- `小北門-467` dry-run 現可定案為 `FollowMe Pro M7 43／17,990`；`新景美-1349` 同樣可解除一次性重複警示；`微風本館-194` 可定案為遠景／無型號／無價格。另有 `大葉高島屋-182` 原規則已可定案。這些只完成零寫入 dry-run；正式 result JSON 仍由 live backend 寫入，必須等照片／資料夾安全邊界再套用，避免整檔原子替換覆蓋新結果。
- 三輪終局測試 61/61 及完整 `tools/run_critical_regressions.py` 全部通過。正式 OCR、Dashboard、uploader 與瀏覽器未重啟。

## 2026-07-18 17:30 九張 hash-bound 終局與即時畫面證據

- `data/202601_terminal_visual_decisions.json` 中九張正式 decisions 已由 builder 產生 `_ocr_audit\visual_authority\202601_terminal_manifest.json`；manifest 精確綁定 source item、原圖 SHA、input SHA 與乾淨三輪。finalizer dry-run 九張皆為 `would_finalize`，正式 apply 後為七張遠景、兩張單機缺型號／價格，全部 verified、非 review，且 API 最後一筆歷程都是「第三輪終局定案／accepted」。
- 202601 即時 review-required `42→33`。套用時正式 backend 正在寫 `20260718-1653-OCR成功.json`，離線修復只原子更新舊 `20260718-1528-OCR成功.json`，沒有競爭同一結果檔，也沒有停止 OCR。
- `大葉高島屋-179` 未混入 manifest：有效 attempt 1 與 attempt 3 分屬不同 run。其保守像素決策保存在 `deferred_decisions`；不得放寬 hash-bound builder、不得把跨 run 記錄偽裝成同一乾淨 run、不得呼叫第 4 次模型。
- 既有 Dashboard 分頁實測 15 秒內由 `693→695/1,500`，檔案 `台南東寧-1354→1356`，上傳 `54,782→54,783`；照片／檔名、LLM 自言自語、輪次、右欄累積卡片與上傳數同步。沒有新開、重載或重啟瀏覽器。
- 17:28 stream uploader 為 running，canonical uploaded 54,774、pending 180、working 1、runtime fuse absent。`大葉高島屋-182` 仍在 pending，沒有 exact Drive receipt，不能宣稱已上傳。
- 全案終點維持 2015–2026 共 `151,714` 張、`137` 個資料匣及逐張精確 Drive 收據；2026 結束後由 continuity supervisor 接續 2025→2015。

## 2026-07-18 17:46 全案總盤修正但不冒充 202606 完整終局

- 根因：202606 priority staging 已有 1,393 個 durable OCR tasks，但沒有寫入 recursive `folder_summary.csv`，所以 Dashboard 仍顯示 65,331 並把 202606 錯列 pending。
- 新工具以 period-priority manifest、source map、來源／staging 完整檔名集合及 1,393 個唯一 tasks 交叉驗證後，只補 `processed=1393`。row 明確為 `period_priority_processed_unexported`，`ready=0`、`copied_count=0`、`success_records=0`，manifest 也固定 `drive_upload_complete=false`。
- 更嚴格的盤點同時揭露：1,393 張已處理，但 `.52` current-guard final 只有 379；1,014 張仍為舊 revision 或未終局。不得把 202606 宣告全部完成，這 1,014 張須由後續照片邊界複核清單處理。
- API 與既有分頁已即時更正為 `66,724/151,714`、`44.0%`、`45/137`、剩餘 `84,990`；ready 仍維持 65,331。202601 當下 715/1,500，uploader 54,832、pending 133、fuse absent，沒有重啟或新增分頁。
- continuation monitor 已接入相同 recorder，後續 priority staging 完成會先如實記錄 processed，再獨立等待 export／Drive receipts；總盤更新不再依賴整批上傳完成。

## 2026-07-21 09:16 接手基準（evidence revision `.60`）

- 正式 OCR、Dashboard、LM Studio 與唯一逐張 uploader 均在線。正式 staging 固定為 `D:\00_商化\00_已OCR照片\_ocr_staging\20260720_205254\202601_商化照片-202601_6403a632`；不得誤切隔離 smoke 或其他月份。當下 202601 為 `769/1,478`、verified `747`、review `22`、failed `0`，目前檔案 `M-屏東縣-屏東市-SF-屏東-551.jpg` 第 2 輪，累計模型呼叫 `14,074`。
- 全案上方仍為 `65,336/151,714`、資料匣 `44/137`。這是去重後首次辨識數；202601 複核與重傳不得重複增加。真實活動以月份複核、目前檔案、右側卡片與上傳數判斷。
- stream uploader 已同步載入 `.60`，當下 canonical uploaded `56,004`、pending `0`、working `0`，最近逐張收據為 `M-202601-屏東縣-屏東市-SF-屏東-遠景-550.jpg`。OCR 每張 verified 即 enqueue，不等整月或全年。
- `.60` 修正價牌角色漏洞：同牌若同時存在小字市價／原價與大字現行促銷價，必須逐項辨識角色並使用現行價；2026 首輪價差不得直接 verified，至少一次獨立確認，跨輪價格不同則用第三輪。三輪皆無上一輪答案、摘要或錯誤原因，整張仍禁止第 4 次。
- 永康大灣 1415 的隔離實測已用三輪正確定案 `S27D300GAC／3,290` 並取得 Drive ID `11sQdNFoXTs4LHGt0gBbfl7qoPLWE-vbp`、size `873038`、MD5 `3808b795ee7f6265a8822639b898025d`。舊 3,590 錯名的 Drive ID `1Fw45Wjdt3VMpdqnYOuaqfj2x6tBYJtcD` 已精確清除；8 筆更正清冊全部 `old_trashed_verified`。
- 完整離線回歸 `557/557` 與 critical regressions 均通過。既有 Chrome 分頁目視確認照片、檔名、LLM 逐字內容、輪次、右側縮圖、月份複核及上傳數同步，畫面由 `757→758`，沒有新開或重整分頁。
- `.60` 復跑後低功耗唯讀抽查 `嘉義-701/702/703/704`、`嘉義新光-199`，五張皆為原圖／trace／終局／receipt 一致、無前輪暴露、無 prompt 污染、最多三輪且已上傳。701–703 為正確遠景、704 為首輪正確單機 `S27CG552EC／4,990`；199 的兩張錯票被第三輪終局保守排除，未形成錯名上傳。暫無系統性跑歪證據。
- 下一步：持續完成 202601，並由低功耗唯讀抽查最近 `.60` 原圖／敘述／結構／檔名／receipt 是否一致；若發現新系統性內容錯誤，只在照片邊界停 OCR，保留 backend、Dashboard、LM Studio 與 uploader 在線。2026 全部閉環後按既定順序處理 2025→2015，直到 `151,714` 張與全部精確 Drive 收據完成。

## 2026-07-21 11:09 四張像素權威修正與雲端舊副本閉環

- `data/visual_authorities_202601_pingdong_changhua_20260721.json` 已精確綁定四張正式 202601 原圖、source item、原始 SHA、實際 input SHA 與三次乾淨獨立呼叫。終局為：`屏東新中正-1008=單機/S43FM702UC/13,990`、`1011=單機/FollowMe Pro M7 43吋/17,990`、`屏東太平洋-434=單機/FollowMe 型號未細分/無價格`、`彰化中山-234=一般單機/無型號/無價格`；四張均 verified、非 review、已完成，沒有第 4 次模型呼叫。
- `屏東新中正-1009` 經完整原圖再查維持 `單機/S27D300GAC/3,290`；左側 `S27CG552EC` 價牌屬於被裁切鄰機，不能污染中央主體。這一張不是錯誤，不得改名。
- 四個正確新檔均已逐張取得 Drive receipt：1008=`1RX2airHlkuxiCzZk1gztGXLMr-b2Eg2S`、1011=`1_YoSQpMjvXlp_LYyre4Tqhc67YN1jBo0`、434=`1eHX9ddLhN1d9BOMTy0y9igUjNfdKtb21`、234=`17J0gZssmTMA-IRpm_laTFBQPSY2Pf61K`。
- `434` 的兩個舊錯名 Drive ID `1eXdzABRaAA0VzR0Ncch9E0KYhQZlNEX6`、`1NMFO8KVxU_NNQLO_XHCjBiBDesdAJydn`，以及 `234` 的舊錯名 ID `1A2xjw4SkxfYLJj9P3SlxYEJQ4005tvDu` 已由精確更正帳本移入垃圾桶並讀回為 `old_trashed_verified`；正確新檔 ID、size、MD5/SHA 仍存在且一致。
- 隱藏 active repair bridge PID `12932` 持續在線，只在 live backend 重寫同一正式結果檔時冪等重套精確修復；不重啟、不開視窗、不呼叫模型。三輪定案／修復相關測試 `84/84` 通過。
- 11:09 正式批次仍在前進：202601 `967/1,478`、verified `940`、review `27`、failed `0`；current file `林口三井-294`。canonical uploaded `56,080`，逐張 uploader PID `10960` 在線。上方 `65,336/151,714` 是去重初次辨識數，202601 重驗期間不增加並非卡住。
- 後續仍先完成 202601 與其餘 2026（含 202606），逐張 closed-loop 上傳；再依 2025→2015 接續。`202606` 在總盤若仍顯示 blocked，不得略過，必須在 202601 安全閉環後由精確 inventory/guard 證據解除，而不是直接修改狀態字串。
- 修正後低功耗唯讀抽查 `比漾廣場-379/380` 與 `汐止-1364` 全部 PASS：原圖、每輪敘述、結構終局與檔名一致，無 prior-answer exposure、無 prompt contamination、沒有超過三輪；暫無新的系統性內容漂移證據。

## 2026-07-21 15:08 `.62` 接手基準：總盤 66,724、正式 OCR 與逐張上傳已恢復

- 介面停在 65,336 的根因已證實為 canonical audit ledger regression：202606 的 1,393 個 durable processed tasks 被 summary 覆寫成 5，且 discovery row 遺失 folder ID。已用 deterministic ID `8ae67c526e285b524d08822d0767b17ea82d9a48c630542d8c5dc3cc0c593c20` 與 exact recorder proof 修回；API 與既有 Chrome 分頁現均顯示 `66,724/151,714`、`45/137`、剩餘 `84,990`。`record_period_priority_progress.py` 現接受並保留 canonical schema 超集，回歸測試已覆蓋。
- request binding 已升為 `.61` 尾錨，嘉義新光 199 的鄰商品價格污染否決再升為 `.62`。完整 critical regressions exit 0；`.62` 隔離 smoke run `20260721_145159_326953` 為 5 張、15 次呼叫、0 個 binding／memory／runtime invariant 錯誤，199 終局是 `單機／無型號／無價格`。
- 舊 fuse 已依 receipt `runtime_health_fuse_clearance/smoke_20260721_150050_531709.json` 封存；benchmark lock 已解除。正式 staging 仍是 `D:\00_商化\00_已OCR照片\_ocr_staging\20260720_205254\202601_商化照片-202601_6403a632`。高雄 747 保留 call 2 狀態並只使用剩餘 call 3，正確定案 `S24F332EAC／2,390` 後逐張上傳；沒有第 4 輪、沒有重跑前 1,245 張。
- backend port 5002 為 `.62`、LM Studio 1234 不動；uploader 已以唯一隱藏 parent/child tree 熱換到 `.62`，沒有終端機視窗。既有 Dashboard 分頁可視核對於 15:08 為 `202601 1,263/1,478`、上傳總數 `56,198`、待上傳 1、正在執行，照片／檔名／LLM 自然文字／輪次／右側累積卡片一致。
- 目前批次仍持續前進，禁止為文件或 Git 中斷。右側曾出現高雄 748 的三輪技術終局卡：三輪 request binding 與 input SHA 都正常，但 label ownership／model-price 自相矛盾，因此沒有上傳錯名；它屬 photo-local 待 deterministic 零模型定案，不得做第 4 次。這不阻塞後續照片，也不構成解除全域 fuse 的理由。
- 下一步仍是：完成 202601，處理 202606 的 43 個 nonfinal 與 202602–202605/202606 Drive 精確收據，閉環全部 2026；接著依 2025→2015 完成全案 151,714。每張 verified 即 enqueue，不等月份完成。

## 2026-07-21 18:51 `.64` 最新接手基準（高於前述 `.62`）

- 上方去重總盤已固定為 `66,724/151,714`、`45/137`、剩餘 `84,990`；`65,336` 是漏算 202606 的舊錯值，不得再引用。隔離 smoke 與 202601 第二／三輪都不增加此數字。
- `.64` 已取代 `.56` 的 3+ 螢幕絕對遠景規則：先找原圖中的實體 FollowMe。直接同機品牌，或螢幕像素外同機白色直桿＋完整圓底座，固定以該 FollowMe 為商業主角判單機；只有沒有實體 FollowMe 候選時才以 3+ 完整螢幕判遠景。螢幕播放內容中的支架／底座固定 `screen_content_only`，不得當硬體。
- `.64` acceptance run `20260721_183113_327069`：7 張、18 calls、7 verified、0 review、0 failed、0 runtime/binding/memory invariant。939 正確為實體 FollowMe 單機且價牌 17,990；701 原圖也有實體白色直桿與完整圓底座，舊文件所稱 701 遠景已失效。
- fuse 已依 `_ocr_audit/runtime_health_fuse_clearance/smoke_20260721_183541_314681.json` 封存，benchmark lock 已解除。port 5002 backend 與唯一隱藏 uploader 均為 `.64`，既有 Dashboard 分頁未重開／重載。
- 408、412、413、766、768 五張已用 hash-bound pixel authority 零額外模型呼叫更正，正確新檔均取得 `.64` Drive receipt；上傳總數 `56,217→56,222`。全域舊檔更正帳本尚有 4 筆 mapping error，所以舊錯名暫不批次刪除；先保留新舊雙份比冒險遺失安全，之後只以精確 Drive ID 清理。
- 18:51 正式 202601 已恢復並前進至 `1,322/1,478`，verified 1,305、review 17、failed 0、累計模型呼叫 15,622、目前 `M-高雄市-岡山區-SF-岡山-752.jpg` 第 1 輪。stream uploader PID 1128，canonical uploaded 56,224、pending 0、fuse absent。禁止因文件、Git 或舊檔清理停止正式 OCR。
- 接續順序不變：完成 202601，再閉環其餘全部 2026（含 202606 nonfinal 與 202602–202605/202606 receipts），再 2025→2015，直到 `151,714` 張全部如實終局且逐張有精確 Drive 收據。

## 2026-07-21 `.70` 接手補充：不得重置的總盤與復原規則

- 目前上方唯一正確總盤為 `66,724 / 151,714`、資料夾 `45 / 137`。它表示唯一來源照片的首次辨識完成量；複核、三輪、修復與重新上傳均不加總。若畫面再次出現 `65,336`，先檢查 202606 canonical summary／discovery 記錄是否遭覆寫，不能把它解讀成 202601 卡住。
- 主批次必須保持在線；一張如實結案即逐張 enqueue/upload，不等待月份或全年。修復、文件、Git、雲端舊檔清理、抽查都只能在不干擾 OCR、既有 Dashboard 分頁、LM Studio 與唯一 uploader 的前提下進行。
- 若 safe reload 遇到 incomplete-staging interlock，先復原同一個正式 staging 並 `restart=false` 接續。supervisor 不得建立新 staging、不得重跑已完成照片、不得清掉 call/retry state。interlock 未釋放時只能保留現場並 fail-safe。
- uploader 更新前和每次 claim 前都須遷移／驗證既有 pending payload；相同來源與相同 Drive ID 是冪等收據確認，絕不得另建重複 Drive 物件。更正照片的舊雲端副本只能在新檔 receipt、雜湊與遠端讀回完全成立後，按精確舊 Drive ID 處理。
- FollowMe 證據不能放寬：黑色一般支架或黑色底座不等於白色 FollowMe 直立支架與圓形落地底座；螢幕內畫面也不是實體硬體。`高雄楠梓右昌-1148` 的 hash-bound 終局是 `單機 / S27D300GAC / 3,290`，不允許因黑色支架／底座改判為 FollowMe，也不得新增第 4 輪。

## 2026-07-21 `.71`：文心 645 完整螢幕誤算止損與半日監督

- 23:46 每半天內容抽查抓到 `M-台中市-北屯區-SF-文心-645.jpg` 被 `.70` 錯誤定案為遠景。原圖只有中央一台螢幕四邊四角完整，左右鄰機都被原圖邊界裁切；中央機身貼紙及同列實體價牌支持 `S27CG552EC / 4,990`，且沒有 FollowMe 實體。OCR 已在下一張照片邊界停止，port 5002 Dashboard、LM Studio 與逐張 uploader 保持在線。
- 根因是 `identity_free_wide_candidates` 漏檢 `unique_main is False`；因此第一輪雖明示唯一主角，仍可能被錯算成第二張「無主角寬景票」。`.71` 同時要求真正寬景票的 `unique_main=false`，並要求嚴格 3+／寬景敘述備援至少有兩輪 `unique_main=false`。回歸固定保護「一張真寬景票不得否決兩張唯一主角單機票」；照片總呼叫上限仍為三次。
- 645 已用 source item、原圖 SHA 與 input SHA 綁定的像素權威零新增模型呼叫更正為 `單機 / S27CG552EC / 4,990 / ✓`。正確 Drive ID `13o4fzza7rWvGE7oC0aSEOIWC1E3Sd-FP` 已以 SHA-256 `6a977069de80130595594384100284b792d95224908abc444d036a33b29d2bad` 回讀；錯誤遠景副本 ID `1vNba5Lamr-G1VL21bWokBjHSvsJyD89O` 已精確刪除。
- 665、669 的正確 canonical Drive IDs 分別為 `1AkSlmr5ZXnguBBhUOLeZMV-y1Ae2esUb`、`1ckvwD1V76hwH2qvg3lfHBnZobJJChHAK`，正確 SHA-256 已回讀；兩個「型號未辨識／無價格」錯名副本 IDs `1rPF5ozyjwiZsHfHLSr-47m5Q_JAZW7o3`、`1tIYLsEhFN1TWX9OnC9m6SiiPzSFPTOju` 已精確刪除。
- `.70` 只有使用 `two_wide_geometry_votes_veto_single_identity_outlier` 的終局不得直接視為相容；其他 `.70` 結果沒有走到該缺陷規則，可直接沿用以避免超過三輪或白跑。問題照片只能零模型重驗或由精確 hash-bound authority 結案；文心 645 是目前 trace 中唯一命中該規則者。
- 人工／代理監督改為每日 09:00、21:00 各一次。每次必須報總盤、當前批次、verified/upload 增量、median/P90、平均呼叫、首輪結案率、review/failed、GPU/parallel 與至少三張原圖／敘述／結構／終局一致性；平時不輪詢、不洗版。發現系統性跑歪時仍立即照片邊界停止，不能等下個時段。
- `.71` 已在不新增第 4 次模型呼叫下復原 `新文心-967=單機/S24F332EAC/2,590`；Drive receipt ID `1SUhHE9_b4Jexo2eqsTiyLE2kDZ44VuRg`。builder 重建結果為 2026 唯一來源 5,951、相容 verified 176、hash-bound human audited 88、待續跑 5,692、missing/conflicting/invalid 均 0。

## 2026-07-22 `.72`：09:00 半日監督抓到的欄位清空缺陷

- 半日唯讀抽查發現 `.71` 會把「市價與會員售價相同」誤當參考價而清空，也會在重複型號／價格對遭單輪敘述歸屬矛盾阻擋時，把空欄位冒充 verified 並上傳。runtime fuse 已在照片邊界停止後續發布。
- `.72` 保留相同價格、禁止矛盾欄位洗空成功，並要求 builder 精確排除受影響 `.71` 終局。既有錯名 Drive receipt 只能在新正確檔名完成 exact readback 後逐筆處理，不得批次猜測刪除。
- 全面稽核不是逐張補洞：新增跨所有 adjudication rule 的 model+price 共識不變條件、raw suppression provenance 與 uploader 第二道閘。595 項 tools 測試及完整 critical regressions 通過；正式 fuse 尚未解除，介面維持在線並誠實顯示修復中。
- Drive 唯讀盤點：current receipt 2,894、與最新 accepted 欄位不一致 172、曾產生多檔名／多 Drive ID 的來源 681、確定由有內容退化成空欄 27（皆 202601）。現有 legacy reconciliation dry-run 仍為 897 列、893 gate blocked、4 mapping errors、`safe_to_replace=false`，沒有寫 ledger、沒有動 Drive。
- 00:04 已由唯一 hidden `rerun_staged_candidates.py --resume-existing-then-continue --keep-staging` 接回原 `20260721_233817/202601_...`，不重建 staging、不重跑既有 14 張。接回後 966 正確遠景、967 正確單機、968 正確 `FollowMe M7 32吋/12,990` 且三張皆逐張上傳；後續順序仍為完成全部 2026（含 202606），再 2025→2015。
- 2026-07-22 09:46 後 `.72` 隔離驗證證明 940、976、1528 可正確收斂；199 因第 1 次 request binding 無效且後兩輪欄位衝突，正確被阻擋、未上傳。另一次 234 驗證雖終局保守正確，但第 2 輪仍有內容矛盾，因此 clearance 嚴格檢查未放行。正式 fuse、benchmark lock 與原 `20260721_233817/202601_...` 斷點均保留，尚未冒充正式續跑；port 5002／Dashboard／LM／uploader 狀態介面保持在線且未重開瀏覽器。後續必須取得全 trace 安全的 bound smoke proof 才能解除，不得為追進度放寬 binding、memory、prompt 或跨照片守門。

## 2026-07-23 00:19 `.73` 接手基準：介面、正式 OCR 與逐張上傳已恢復

- `.73` 取代舊文件中的 `彰化中山-234=一般單機`：原圖右下唯一螢幕的右框、下框與右下角在原圖外，完整螢幕數為 0，正確終局是 `遠景／無型號／無價格`。舊「正確」Drive 物件 ID `17J0gZssmTMA-IRpm_laTFBQPSY2Pf61K` 現為待取代副本；必須先取得新遠景檔 exact receipt/readback，才可按 ID 精確清理，禁止先刪。
- `嘉義新光-199` 已加入精確 hash-bound 權威：中央唯一完整螢幕，左側 Harman Kardon 價格與右側 FollowMe 宣傳不得污染主體，終局 `單機／無型號／無價格`。builder 會拒絕與現行權威不符的舊 verified trace。
- 離線 599 項 tools 測試與完整 critical regressions 全通過。fuse-active smoke `20260723_000827_374213` 為 1 張／3 calls／1 verified／0 review／0 failed；binding、independence、same-image、memory、prompt 與 call cap 全通過。fuse 以 receipt `runtime_health_fuse_clearance/smoke_20260723_001314_840098.json` 封存，benchmark lock 已解除。
- port 5002 已載入 `.73`，既有 Dashboard 分頁未重開。唯一 hidden runner 為 `rerun_staged_candidates.py`，正式 staging `20260723_001355/202601_商化照片-202601_6403a632`；00:19 已從 `中清-1530` 連續前進到 `東山-1140`，processed 6、verified 5、review 1、failed 0，介面照片、stream file、輪次與結果卡同步。
- 第一張 `中清-1530` 三輪均 request-bound、無記憶／prompt 污染，但皆有 `structured_authority_material_conflict:model`，所以 photo-local 技術終局、未上傳；它不阻塞後續，也不得呼叫第 4 次，後續只能零模型重驗或精確像素權威處理。
- 舊 stream uploader 雖有 PID，實際仍載入 `.72`，因此對第一個 `.73` pending job 報 `unapproved pending upload revision` 後退出。已只把 uploader 隱藏換版，不動 OCR／Dashboard／LM／瀏覽器；新 worker parent/child `27248→3440` 上線後 pending 歸零，canonical uploaded `56,317→56,319`，最新逐張收據 `東山-1138`。未來升版後必須驗證 receipt 前進，不能只看 PID。
- 接續工作不變：腳本持續完成全部 2026（含 nonfinal、正確新檔與精確 Drive receipt），再依 2025→2015 處理至全案 `151,714`；人工／代理維持每日 09:00、21:00 唯讀內容監督，除系統性跑歪外不干擾 runner。

## 2026-07-23 `.74` live recovery handoff

- Root cause of the 02:40 stop was a false batch-wide `request_id_mismatch` fuse: only two distinct photos had mismatches, separated by about two hours and about 240 processed photos. The old staging-lifetime accumulator treated the second sparse incident as systemic.
- `.74` keeps every invalid binding response excluded and counted against the three-call cap, but trips the global binding fuse only for three distinct mismatched photos inside ten minutes. Sparse faults remain same-photo retries. UI presentation history no longer blanks merely because OCR is temporarily stopped.
- Full critical regressions passed. The legacy fuse for `M-台北市-中正區-SF-台北-577.jpg` was archived with its attempt count preserved; the invalid payload was discarded. Port 5002 was safely reloaded in the existing browser context to revision `.74`, the same staging checkpoint resumed, and the current-revision stream uploader was relaunched hidden.
- Live proof immediately after recovery: `242/1209 -> 243 -> 248` and continued; latest result moved `576 -> 577 -> 582`; uploader moved the new `.74` queue through exact receipts with one transient pending item while the next photo was still processing. No browser tab/window was opened or restarted. The existing Chrome inventory still contained exactly one `Samsung OCR Dashboard` tab at the same fingerprint URL; a fresh DOM capture timed out, so do not claim a post-repair screenshot until a later non-disruptive visual check succeeds.
- Three original-image checks passed: 581 `S27CG552EC/4990`, 582 `S27FG532EC/4990`, 583 `S32DG802SC/29900`. Early `.74` high-risk slice: 20 finalized, 50 calls, median 12.82 s, P90 16.25 s, 2.50 calls/photo, 15% first-pass, max call 3, no accepted unverified request IDs.
- Project completion remains: every supported 2015-2026 source photo gets a truthful view/model/price-or-null result, deterministic filename, and exact year-folder Drive receipt. Finish 2026 correction/upload closure first, then continue 2025 down to 2015. Based on observed end-to-end throughput rather than call latency, current conservative full completion target is 2026-08-28 to 2026-09-01, conditional on uninterrupted service; remeasure after historical batches begin.

## 2026-07-23 `.75` live handoff

- Production is revision `20260723.75`, port 5002, original staging `20260723_001355\202601_商化照片-202601_6403a632`; it resumed from the saved boundary and advanced `268→273/1,209`, verified `251→256`, review 17, failed 0, fuse absent, pending/working `0/0`.
- Root cause fixed: old instruction-echo detection treated natural `必須填 null` as copied prompt text. Never broaden it back to `必須填`.
- `三創店-498` was finalized with zero extra inference as `單機/count 2/model null/price null`; its exact Drive receipt is `_drive_upload_stream/receipts/eb006e7d...fc3a6c.json`, Drive ID `1MUsxbIb7x6NheREtoQX-pLN-SrcatwEj`, confirmed 2026-07-23 09:04:41.
- The hidden `.75` uploader proved pending-to-receipt closure. The old `.74` uploader was stopped before the new job became claimable, preventing incompatible-revision rejection. Backend and uploader process trees are unique.
- Existing Chrome inventory previously proved exactly one Dashboard tab at `http://127.0.0.1:5002/?ui=31584c2c96ae7330`; no tab/window was opened, closed, reloaded or replaced. A later lightweight DOM claim timed out, so this handoff has current API/process/regression proof but no new visual screenshot; do not claim otherwise.
- Honest completion window is 2026-08-28 through 2026-09-01 at 2,350–2,500 verified-and-uploaded photos/day. The former August 10 estimate is withdrawn. Finish 2026 first, then continue 2025 down to 2015 automatically.

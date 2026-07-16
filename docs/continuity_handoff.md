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

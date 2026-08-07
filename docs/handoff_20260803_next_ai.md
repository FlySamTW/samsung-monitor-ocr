# Samsung Monitor OCR 現況權威交接手冊（已由 2026-08-07 版本接替）

> 最新現況請改讀 [handoff_20260807_next_ai.md](handoff_20260807_next_ai.md)。本文件保留 2026-08-03 的完整背景，不得把其中快照數字或 `.92` 修訂碼當成目前現場。

> 文件狀態：現況權威手冊
>
> 現場快照時間：2026-08-03 23:21（Asia/Taipei）
>
> 專案根目錄：D:\00_商化\samsung-monitor-ocr
>
> 正式來源：D:\00_商化\00_未整理商化照片
>
> 正式輸出：D:\00_商化\00_已OCR照片
>
> Dashboard：http://127.0.0.1:5002/
>
> LM Studio API：http://127.0.0.1:1234/v1

本文件是下一位 AI 接手本機正式環境時的第一入口。它整理了專案目標、歷史原因、現行架構、不可違反的規則、即時快照、尚未解決的問題、復原方式與驗收標準。舊的 continuity_handoff.md 保留完整時間軸，但其中早期數字、舊連接埠、舊守門修訂碼與舊處理方式不一定仍有效。

所有快照數字都會持續變動。接手者必須先查現行 API、程序、收據與審計檔，再引用數字；不得因本文數字與現場不同就重啟、清空或重跑。

---

## 0. 文件權威順序

遇到文件互相矛盾時，依下列順序處理：

1. 使用者在目前對話中的明確指示。
2. 專案根目錄 AGENTS.md。
3. docs/development_guide.md 的最新鐵律與永久規則。
4. 本文件的現況、接手順序與已知欠項。
5. docs/three_layer_accuracy_gate.md 與現行程式證據契約。
6. docs/ai_handoff_runbook.md 的一般操作方式。
7. docs/continuity_handoff.md 的歷史時間軸。
8. docs/accelerate_this_project_handoff.md 僅為 2026-07-28 的外部建議，不是現況權威。

任何文件都不能凌駕現行照片雜湊、請求綁定、結構化結果、上傳收據與 frozen source inventory。

---

## 1. 一分鐘接手摘要

- 全案目標不是只完成 2026，而是把 frozen inventory 中 151,714 張、137 個資料夾，依最新往最舊處理至 2015，逐張得到如實終局結果、正確檔名與 Google Drive 精確收據。
- 2026 已封存完成：7,344／7,344，其中 7,164 張為正式驗證，180 張為已封存人工稽核結果。不得再把 2026 掛回正式 OCR。
- 現行主線正在處理 202206。2026-08-03 23:21 快照為全案 69,173／151,714、47／137 個資料夾；202206 為 101／1,477，verified 88、review_required 13、failed 0。
- 本機模型為 LM Studio 的 qwen/qwen3-vl-8b，Q4_K_M，context 32,768；正式逐張 OCR 不使用 OpenAI／Codex。
- Dashboard、OCR、歷年接力、逐張上傳與隱藏 continuity daemon 均在線。runtime health fuse 與 pipeline pause 均未啟用。
- 上傳快照為 canonical 62,828、pending 0、working 0、last_error 空白；上傳與 OCR 是流水式並行，不得等整月或整年完成才傳。
- 目前最重要欠項不是停止主線，而是另外關閉 202207 的 243 張欠項：241 張 review_required、2 張 missing_result。應優先用既有同圖證據零模型定案；只有照片級證據真的不足，才依剩餘額度重跑，絕不可第 4 次呼叫。
- folder_summary.csv 中 202206、202205、202204 的 HTTP 400 是先前嘗試留下的舊列；現行 202206 已證明正在正常跑。不得把舊列當成目前故障，也不得直接刪除，應在月份完成時確認正式摘要覆寫。
- Git main 的 HEAD 仍是 e4ea877；現行 .92 與大量後續修正存在未提交工作目錄，共約 139 筆差異。不得 reset、checkout、clean 或覆蓋。

---

## 2. 專案真正目標

### 2.1 原始問題

來源盤點包含近十多年、分散在不同年度與月份的 Samsung 通路陳列照片。原始照片檔名主要記錄縣市、行政區、通路、店名與流水號，沒有一致的遠景／單機、型號、價格與年度結構，也沒有逐張雲端完成收據。

專案要把每張照片轉成可查證、可整理、可上傳的結果，而不是只把模型文字顯示在介面：

照片原檔
→ 本機視覺模型辨識
→ 固定程式做證據守門與最多三次無記憶複核
→ 每張自動定案
→ 產生正確檔名與審計資料
→ 逐張排入上傳
→ Google Drive 以大小與 MD5 精確回讀
→ 寫入不可混淆的完成收據

### 2.2 正式終點

全案完成必須同時成立：

1. frozen source inventory 的 151,714 張照片都有唯一 source identity。
2. 每張都有如實終局結果，或有可證明且已處理的照片級技術例外。
3. 終局結果與同一原圖、同一請求、同一證據 trace 綁定。
4. 輸出檔名與結構化結果一致。
5. 每張應上傳照片都有精確 Drive 收據；不得只看本機 queued 或 copied。
6. 2015 至最新年度全部閉環；2026 完成不是全案完成。
7. Dashboard 的總進度、目前照片、LLM 判讀與右側結果卡使用同一實體照片 identity。

---

## 3. 永久鐵律

### 3.1 優先順序

永久優先順序為：

照片辨識正確性 ＞ 節省 OpenAI／Codex tokens ＞ 完成時間

這不代表可以無限重跑。正確性要由固定本機程式、證據契約、回歸測試與收據保證，不是讓 Codex 每天逐張看圖或修改規則。

### 3.2 模型與 tokens

- 正式逐張 OCR 只能由 LM Studio 本機模型執行。
- 不得使用 OpenAI／Codex 或其他雲端模型逐張代判 151,714 張照片。
- Codex 只負責系統性根因修正、固定樣本驗證、低頻監督、文件與安全復原。
- 低功耗子代理只能做唯讀健康檢查、靜態測試或少量抽查；不得取代正式本機批次。
- 健康時不要反覆把完整 API、長日誌、圖片或歷史對話送給 Codex。

### 3.3 介面連續性

- Dashboard／port 5002 必須持續在線並在既有瀏覽器分頁顯示。
- 不得開新瀏覽器視窗或新分頁冒充恢復。
- 內容完整性問題只能在照片邊界暫停 OCR／上傳；Dashboard 不得關閉或隱藏。
- 修復完成後必須從保存的 checkpoint 自動續跑，不得等使用者手按。
- 目前照片、檔名、LLM 逐字內容與右側卡片必須是同一張。

### 3.4 單張問題不得停整批

下列問題只可照片級處理：

- 單張空回覆。
- 單張欄位缺漏。
- 單張價牌歸屬不明。
- 單張已用滿三次需要零模型定案。
- 舊紀錄缺欄位。
- 單張上傳重複檔名或回讀失敗。

只有跨照片記憶污染、請求綁定失效、整體 Prompt／證據版本失配、同一時間多重正式 writer 等系統性風險，才可啟用全域保護。即使全域保護啟用，Dashboard 仍須在線。

### 3.5 一張一張閉環

- 照片依最新往最舊處理。
- 第一輪能確定就立即定案。
- 只有不確定欄位或高風險情況才進第二、第三輪。
- 一張照片真正送到 LM Studio 的總呼叫數跨重啟、跨版本仍最多三次。
- 三次後由固定程式零模型定案；不得出現第 4、5、6 輪。
- 定案後立即進 persistent upload queue，不得累積到整月或整年才上傳。
- 網路慢只會讓 pending 暫時增加，不得反向停止 OCR。

---

## 4. 現場快照

### 4.1 2026-08-03 23:21 即時狀態

| 項目 | 快照 |
|---|---:|
| Dashboard | http://127.0.0.1:5002/ |
| is_running | true |
| evidence_guard_revision | 20260803.92 |
| status contract | compact-v2 |
| 前端 fingerprint | 89f2d0c28f435e9c |
| 目前資料夾 | 2022-商化照片\商化照片-202206 |
| 目前照片 | M-台中市-大里區-TK3C-新大里-1516.jpg |
| 全案進度 | 69,173／151,714（45.59%） |
| 資料夾進度 | 47／137；剩餘 90 |
| 202206 | 101／1,477 |
| 202206 verified | 88 |
| 202206 review_required | 13 |
| 202206 failed | 0 |
| 累積本機模型呼叫 | 33,939 |
| 平均推論時間 | 18.62 秒 |
| 上傳 canonical | 62,828 |
| 上傳 pending／working | 0／0 |
| 上傳 last_error | 空白 |
| GPU | 99.0% |
| 顯示記憶體 | 14,543／16,303 MB（89.2%） |
| CPU／RAM | 11.1%／40.5% |
| runtime health fuse | 無 |
| pipeline pause | 無 |

以上是時間點快照，不是要寫死的常數。接手後第一件事是重新讀取 API；目前照片、進度、上傳數與資源使用率理應已改變。

### 4.2 文件完成後續航證明

2026-08-04 00:29 再次唯讀核對：

- is_running 仍為 true，守門修訂碼仍為 20260803.92。
- current_file 與 stream_file 同為 M-台中市-西屯區-TK3C-新東海-1126.jpg。
- 全案由 69,173 增至 69,249。
- 202206 由 101 增至 177，verified 155、review_required 22、failed 0。
- canonical upload 由 62,828 增至 62,893，pending 1、last_error 空白。
- runtime health fuse 與 pipeline pause 仍未啟用。

這證明撰寫交接文件期間沒有中斷 OCR、上傳或 Dashboard；下一位 AI 仍須重新取得自己的接手快照。

### 4.3 2026 封存權威

historical_continuation_receipt.json 已證明：

- sealed_terminal_completion = true
- 2026 unique sources = 7,344
- verified = 7,164
- human audited = 180
- terminal authorized = 7,344
- source inventory = 151,714 張、137 個資料夾
- receipt 綁定 continuation request、current year marker、upload proof、review list、terminal summary 與 frozen inventory 的 SHA-256

因此 2026 不得再被舊 mutable review 報表復活。

---

## 5. 為什麼專案曾長期停滯

使用者的需求本來很單純：最新往最舊逐張辨識；容易照片第一輪結案；不確定才進第二、第三輪；三輪後自動定案；逐張上傳；直到 151,714 張全部完成。

停滯不是因為目標不清楚，而是程式長期出現多層責任混在一起。以下是已確認的主要原因。

### 5.1 把所有單機與遠景儀式性跑二、三輪

早期守門把「正確性優先」誤實作成「多跑幾輪」。明確單機、明確遠景、明確 FollowMe 也被送入第二、第三輪，使每張平均呼叫數升高，速度從約 10 秒延長到數十秒。

正確方式是級聯式判斷：容易照片第一輪結案，只有不確定欄位或高風險照片升級。

### 5.2 跨輪答案污染

曾發現第二輪收到第一輪摘要，同一輪內的價格重試也把錯誤答案再次餵給模型，造成模型迎合上一輪、沿用錯誤價格或說出「您指正得非常正確」等污染語句。

現行要求是 pass 2／3 完全無記憶，只看同一原圖與獨立契約；不得收到前輪文字、前輪 JSON、前輪判斷或可推知前輪答案的提示。

### 5.3 顯示三輪，實際呼叫超過三次

舊程式把業務輪次與內部技術重試分開計數，曾出現畫面第 3 輪、實際已呼叫 4 至 6 次。重驗工具移除結果時也曾忘記保存已消耗額度，使重啟後重新從第 1 次算。

現行規則要求每一次真正送出 LM Studio 前先占用全域照片額度；跨版本、跨重啟、技術重試與業務輪次合計最多三次。

### 5.4 後段守門推翻模型已看對的結果

曾出現：

- 自然敘述說中央只有一台完整螢幕、左右為裁切鄰機，但結構欄誤填三台，後段改成遠景。
- 強 FollowMe 實體證據已成立，卻因精確 SKU 不清而被改成一般單機或遠景。
- 結構權威已撤銷錯誤型號，後段敘述又把型號復活。
- 舊型號未列在目前清單就被清空，浪費二、三輪。

修正方向是欄位分層與證據優先：類型、FollowMe 家族、精確 SKU、價格各自判定；一欄不確定只清空該欄，不得推翻其他已確立欄位或整張重跑。

### 5.5 遠景與 FollowMe 規則混淆

原本遠景複核的目的，是找出被誤判成遠景的 FollowMe 系列，不是讓每張遠景固定三輪。明確多螢幕陳列且沒有 FollowMe 實體線索，第一輪就可定案為遠景。

反之，同一主體有 Samsung FollowMe／FollowMe Pro 直接標示，加上白色直立支架、完整圓形底座或托盤等實體證據時，應先鎖定 FollowMe 家族；不能因精確 SKU 看不清而推翻家族。

### 5.6 全批上傳守門

曾錯誤設成「2026 全批複核完成才可啟動上傳」，造成 OCR 做了很多天但雲端照片完全不增加。使用者已明確否決此設計。

現行設計是每張定案後立即排入 persistent queue，worker 每 5 秒檢查並逐張上傳。

### 5.7 保護粒度太大

單張技術錯誤、舊資料缺欄位或零模型定案未完成，曾觸發全域保護，讓整個介面看似停機。這違反「單張問題不得停整批」。

目前原則是照片級隔離、資料夾級欠項與主線並行；只有真正系統性污染才暫停正式 writer。

### 5.8 總進度沒有單一權威

舊協調摘要曾覆寫封存狀態，使 2026 被重新掛回、總進度長期停在 66,724 或甚至倒退。2026 封存後，已改由 frozen inventory、terminal summary 與 receipt 綁定的歷年 runner 接續。

2026-08-02 至 2026-08-03，主線已從 66,724 前進到本次快照 69,173，證明歷年接續已真正恢復；但總進度仍不能取代逐張 terminal 與 Drive receipt 的最終證明。

### 5.9 Dashboard 多次不同步

已發生的介面問題包括：

- LLM 判讀區空白、裸 JSON、亂碼或只有沒有結果的等待文案。
- 右側卡片不累積、顯示舊輪次錯誤、同張重複或與左圖不同步。
- 上方總進度與目前資料夾數字黏在一起或停住。
- 目前資料夾路徑與最右側內容被裁切。
- GPU 不顯示。
- 新照片第一個 token 尚未到時，左側預覽每隔一張跳回同一張舊女明星照片。

最新預覽回跳根因是 compact queue 保存同一照片多輪事件，而舊完成事件重新取得左側控制權。現行規則是 current_file 對應的實體照片在處理中獨占左側，舊輪次只更新右側卡片。presentation soak 36／36 與 production build 已通過。

---

## 6. 現行系統架構

### 6.1 主資料流

frozen source inventory
→ receipt-bound recursive runner
→ port 5002 backend
→ LM Studio port 1234
→ 結構化證據與照片級守門
→ 終局結果與改名輸出
→ persistent stream upload queue
→ Google Drive 年份資料夾
→ 大小與 MD5 回讀收據
→ Dashboard compact-v2 同步呈現

### 6.2 元件與責任

| 元件 | 責任 | 現況 |
|---|---|---|
| samsung_ocr_batch_processor.py | Dashboard、API、照片處理、守門、展示事件 | port 5002 在線 |
| LM Studio | 本機視覺推論 | port 1234；Qwen3-VL-8B loaded |
| tools/recursive_ocr_flat_export.py | 依 frozen inventory 最新往最舊接力、附著既有批次、輸出月份摘要 | receipt-bound runner 在線 |
| tools/stream_drive_upload.py | 逐張上傳、精確回讀、收據、失敗工作 | worker 在線 |
| tools/ocr_continuity_daemon.ps1 | 每 60 秒確認主線與狀態心跳 | hidden daemon 在線 |
| SamsungOCR_UserContinuityEnsure | 每 5 分鐘安全確認服務；重開機後的正式恢復入口 | Ready，最近結果 0 |
| Dashboard React bundle | 50／50 主畫面、LLM 文字、累積卡、進度與資源 | fingerprint 89f2d0c28f435e9c |

### 6.3 現行程序

快照中的邏輯程序：

- backend launcher PID 6968，runtime child PID 29152。
- recursive launcher PID 16252，runtime child PID 13684。
- uploader launcher PID 20552，runtime child PID 29088。
- continuity daemon PowerShell PID 15168。
- LM Studio 主程序 PID 15660。

venv launcher 加上 bundled runtime child 是同一個邏輯實例，不是兩個 runner。判斷重複程序時要看命令角色、父子關係與 port owner，不能只數 python.exe。

PID 會變動，不可把上述 PID 寫入停止腳本。

### 6.4 不可啟用的舊工作

- SamsungOCR_PipelineWatchdog：Disabled，舊失敗結果 8，不是現行 Supervisor。
- SamsungOCR_ResumeBatch：Disabled，舊任務。
- _handoff_start_backend.bat：綁定舊 202601 路徑，只是歷史工具。
- _handoff_start_recursive.bat：沒有現行 continuation receipt 參數，不可與正式 runner 並行。
- START_FULL_AUTO_OCR.bat：一般使用者入口，但現場已有 receipt-bound 主線時不得再啟動第二組。

---

## 7. 模型與 Prompt 契約

### 7.1 現行模型

正式載入：

- qwen/qwen3-vl-8b
- 類型：VLM
- 量化：Q4_K_M
- context：32,768
- 模型上限：262,144

本機另有但未載入：

- internvl3_5-8b
- minicpm-v-4.6
- gemma-4-12b-it-qat
- qwen3.5-9b-vlm
- qwen/qwen2.5-vl-7b

目前只證明 Qwen3-VL-8B 是正式使用模型，不代表已證明它在所有樣本上絕對最佳。不可因網路評價或單張案例直接換模型。任何候選都必須用固定盲測集比較：

- 遠景／單機。
- 完整螢幕數。
- FollowMe 家族與精確型號。
- 右上／左上側標。
- 型號與價格同卡歸屬。
- 局部鄰機。
- 舊型號。
- 模糊價牌。
- 終局改名與上傳結果。
- 推論 median／P90 與平均呼叫數。

### 7.2 Prompt 的歷史責任

samsung_ocr_prompt.txt 是從 Qwen 2.5 實拍照片多次調整的基準資產。切換到 Qwen 3 時只能做最小相容調整，不能大幅重寫、縮短或另造一套正式 Prompt。

正式送出必須經 build_runtime_system_prompt()，並受 24,000 字元硬上限。先前壓縮 Prompt 時曾誤刪 MANDATORY LAST FRAME CHECK 與 MANDATORY FINAL SELF-CHECK；兩個標記都必須保留。

### 7.3 第二、第三輪

skills/review_pass_contract.py 是 pass 2／3 的永久獨立契約：

- 不讀可替換的 samsung_ocr_prompt.txt。
- 不接收上一輪答案。
- 不接收上一輪摘要。
- 不在同一對話續問。
- 每輪只看相同原圖與該輪獨立任務。
- 最後由確定性程式交叉核對，不由模型自己投票。

---

## 8. OCR 決策順序

判斷必須有優先順序；命中可證明條件後就停止，不得繼續用低優先規則推翻。

### 8.1 第 0 層：請求與照片綁定

先確認：

- source_item_id 唯一。
- 原圖完整 SHA-256 一致。
- request ID 與照片一致。
- presentation key 與 current_file 一致。
- 本輪沒有上一張照片文字或結構欄。
- 已消耗模型呼叫額度可追溯。

任何一項失敗都不是內容複核，而是照片級或系統級技術錯誤。

### 8.2 第 1 層：判斷遠景或單機

優先判斷完整實體螢幕，不以畫面內廣告人物、電視內容或海報當成螢幕數。

單機：

- 有一台清楚、完整、可唯一歸屬的主角螢幕。
- 左右其他螢幕只有局部邊框、裁切、倒影或背景，不得算完整主角。
- 一台主角即使背景有其他商品，仍是單機。

遠景：

- 至少三台完整實體螢幕形成整體陳列。
- 沒有唯一可歸屬的單一主角型號與價格。
- 明確一般遠景且無 FollowMe 實體線索，第一輪即可結案。

### 8.3 第 2 層：FollowMe 家族早停

同一主體若有以下強證據，先鎖定單機／FollowMe 家族：

- 螢幕直接附著 Samsung FollowMe、FollowMe Pro、FollowMe 4K 等標示。
- 白色直立或移動式支架。
- 完整圓形底座。
- 同一支架的小托盤或可轉向結構。
- 同一主體底座、側標或價牌明確寫出 FollowMe。

一旦直接標示與同主體實體結構互相支持，就不得再用遠景規則推翻。精確 SKU 與價格是另外兩欄。

反證也必須有效：若自然敘述與像素都明確是黑色一般桌架、沒有 FollowMe 標示，結構欄單獨幻覺出 white stand 或 round base，不得鎖定 FollowMe。

### 8.4 第 3 層：型號

型號證據優先順序：

1. 同一主角螢幕右上或左上直接附著的側標。
2. 同一螢幕直接附著的規格貼紙。
3. 與主角螢幕位置唯一對齊的價牌／規格牌。
4. 同一底座或展示牌的 FollowMe 型號文字。
5. 其他背景牌僅可作輔助，不得越權。

右上／左上側標清楚寫出 S27CG552EC、S32DG802SC 等 SKU 時，該側標是主角型號權威。下方有多張價牌時，要先以側標鎖定型號，再找同型號價牌；不得任選最近價格。

FollowMe 家族已確立但尺寸或 SKU 不清時，輸出 FollowMe 型號未細分，不得寫成一般的「無型號」，也不得為此固定進二、三輪。

歷史舊型號未列在目前正式清單時，不得自動清空。現行 .91 允許同一實體價牌／側標內完整 Samsung SKU、價格與歸屬都成立時首輪結案；仍需保留清單差異供審計。

### 8.5 第 4 層：價格

價格只能來自與主角型號同一張卡或可唯一對齊同一主體的價牌：

- 先確立主角型號，再找同型號價格。
- 不得把相鄰螢幕價牌混給主角。
- 看不清就輸出無價格；不得猜測。
- 敘述若說「價格不可讀」，結構欄卻填數字，該價格必須撤銷。
- 型號正確但價格不確定，只處理價格欄，不得推翻型號或整張重跑。

2026 照片才與當年度官方參考價比較並產生 ↑、↓、✓、？。2025 含以前只保留店內價格，不做 2026 比價符號。

### 8.6 第一輪直接結案條件

下列照片不應儀式性進二、三輪：

- 單機、同主體型號與價格皆清楚。
- 單機、型號清楚但照片確實沒有可讀價格。
- 明確一般遠景且沒有 FollowMe 實體線索。
- 強 FollowMe 證據已確立家族；精確 SKU 或價格缺漏只誠實留空。
- 歷史舊 SKU 在同一實體卡內可讀、價格可讀、歸屬唯一。

只有實質不確定才升級：

- 遠景疑似 FollowMe。
- 單機可見側標／價牌但模型漏讀。
- 型號與價格歸屬矛盾。
- 完整螢幕數的原始結構與自然敘述衝突。
- 高風險 2026 比價證據不足。

---

## 9. 每張照片可能的有效終局

以下都可以是如實且可上傳的結果：

1. 遠景／無型號／無價格。
2. 單機／有型號／有價格。
3. 單機／有型號／無價格。
4. 單機／無型號／有價格，但必須有唯一主體價格歸屬。
5. 單機／無型號／無價格。
6. 單機／FollowMe 精確型號／有或無價格。
7. 單機／FollowMe 型號未細分／有或無價格。

「沒有型號」或「沒有價格」不是失敗，只要原圖確實無法讀取且證據一致。真正不能冒充成功的情況是：

- 跨照片污染。
- request／source binding 失效。
- 模型結果不是同一張照片。
- 已有清楚側標卻被程式忽略。
- 價格與型號來自不同主體。
- 結果結構無法通過契約。
- Drive 回讀無法證明唯一遠端檔案。

---

## 10. 三次呼叫與自動定案

### 10.1 硬上限

每張照片跨重啟、跨版本、跨業務輪次與內部技術重試，真正送到 LM Studio 的請求總數最多三次。

呼叫前必須先持久化額度。UI 顯示的「第 1／2／3 輪」不能成為額度權威；實際 ledger 與 trace 才是權威。

### 10.2 三次後

三次後不能：

- 再辨識。
- 排入第 4 輪。
- 顯示「待慢模型」。
- 顯示「等待人工最終裁決」。
- 反覆在右側卡片顯示技術錯誤但沒有處理者。

應依既有同圖 immutable trace 執行零模型自動定案：

- 多輪一致者直接收斂。
- 遠景／單機依實體螢幕與唯一主體規則收斂。
- 欄位衝突只撤銷不可信欄位。
- FollowMe 家族與精確 SKU 分開。
- 仍有照片級技術缺口者留在精確修復佇列，不得停主線。

---

## 11. 上傳與 Google Drive

### 11.1 目標結構

正式 Drive 根資料夾為使用者指定的商化照片資料夾，ID：

16X5qALC3zRYc7PpnexXLYprorBzBtT_f

整理後照片直接放在年份資料夾：

- 商化照片／2026
- 商化照片／2025
- 商化照片／2024
- 依此類推

不得再建立「已整理」層，也不再依區域分層。202607 不在本次 frozen inventory 與處理範圍；除非使用者另行明確指示，不得自行加入。

### 11.2 逐張流水式上傳

每張終局完成後：

1. 產生改名檔與照片級 metadata。
2. 原子寫入 persistent queue。
3. uploader 獨立送出。
4. 依遠端唯一檔名、大小與 MD5 回讀。
5. 成功才寫 canonical receipt。

pending 大於 0 通常代表網路速度比 OCR 慢，不是錯誤。只要 pending 有增有減、working 可變、last_uploaded_at 更新且 last_error 空白，主線應繼續。

### 11.3 禁止行為

- 不得等整個月份完成才上傳。
- 不得等 2026 或全案完成才上傳。
- 不得因 pending 增加而停止 OCR。
- 不得遇到遠端同名就建立 _2、_3。
- 不得只看本機 copied 就宣告雲端完成。
- 不得整批重送 failed 目錄。

遠端同名衝突必須以 Drive file ID、大小、MD5 與照片 source identity 精準處理。

---

## 12. Dashboard 固定契約

### 12.1 版面

- 左側主區維持約一半螢幕寬度，不得任意縮小。
- 左側上方顯示目前照片。
- 左側中下方顯示 LLM 有意義的逐字判讀。
- 右側累積顯示已處理照片卡，最新在上。
- 上方固定顯示全案進度、目前資料夾、上傳總數／待上傳、執行狀態與 GPU。
- 六格資源卡版面已定稿；GPU 顯示不得破壞其排列。

### 12.2 同步 identity

同一畫面必須滿足：

- current_file = stream_file。
- 左側照片來源 = 目前檔名。
- LLM 判讀的 source_item_id = 目前照片。
- 右側處理中卡 = 目前照片。
- 完成事件依 source_item_id 去重。
- 舊 pass 事件不可搶回左側。

### 12.3 LLM 文字

自然語句必須結論先行，格式從「我看到本輪結論：」開始，接著說：

- 遠景或單機。
- 型號。
- 價格。
- 最重要的同主體證據。

不得只顯示：

- 正在整理可見證據。
- 判讀文字將接續顯示。
- 第 未提供 輪。
- 裸 JSON。
- 無限省略號。
- 與下一張無關的上一張結論。

處理中可以逐字出現，但要有實際觀察；完成時必須有可讀結論。

### 12.4 右側結果卡

- 每張實體照片只保留一張終局卡。
- 第 1／2／3 輪歷程可以查看，但不能各自佔一張終局卡。
- 縮圖、檔名、類別、型號、價格與上傳狀態必須取自同一 terminal result。
- 舊技術錯誤若已被正確終局取代，不得繼續顯示為主要狀態。
- 不得顯示「尚未完成／尚未上傳」而實際詳情已正確完成。
- 全案跨月份切換時不得清空累積卡。

---

## 13. 進度數字如何解讀

### 13.1 全案進度

overall_progress.processed_images 是已進入全案處理紀錄的 unique source 數。它是主管觀看的主進度，但不是單獨的最終完成證明。

全案真正閉環還要看：

- terminal result。
- verified／review_required。
- rename／export。
- canonical upload receipt。

因此「總進度有增加」代表主線前進；「總進度未增加」可能是重驗已處理照片、跨月略過、離線定案或主線卡住，必須用其他欄位判別。

### 13.2 success、verified、review_required

- success：後端有結果紀錄，不保證已通過正式內容守門。
- verified：正式證據契約通過。
- review_required：已有結果，但仍需零模型或照片級處理。
- failed：技術處理失敗。

不能只報 success，也不能把 review_required 當成永遠等待人工。

### 13.3 completed_folders

completed_folders 是 runner 掃描／輸出的作業狀態，不一定代表該月每張都有 Drive receipt。202207 已標示 copied，但仍有 243 張欠項，是目前最明顯例子。

---

## 14. 單一事實來源

### 14.1 全案盤點

- D:\00_商化\00_已OCR照片\_ocr_audit\source_inventory_v1.json
- D:\00_商化\00_已OCR照片\_ocr_audit\source_inventory_v1.csv

### 14.2 歷年接續授權

- D:\00_商化\00_已OCR照片\_ocr_audit\historical_continuation_receipt.json
- D:\00_商化\00_已OCR照片\_ocr_audit\full_project_continuation_requested.json
- D:\00_商化\00_已OCR照片\_ocr_audit\current_year_rerun_cycle_complete.json

### 14.3 目前歷年 runner

- D:\00_商化\00_已OCR照片\_ocr_audit\_recursive_ocr_state.json
- D:\00_商化\00_已OCR照片\_ocr_audit\folder_summary.csv

folder_summary.csv 是月份作業摘要，不是即時 API。舊 error 列可能已過期；使用前必須與 port 5002、程序與該月最新審計資料交叉核對。

### 14.4 上傳

- D:\00_商化\00_已OCR照片\_drive_upload_stream
- D:\00_商化\00_已OCR照片\_drive_upload\drive_upload_uploaded.csv
- D:\00_商化\00_已OCR照片\_drive_upload\upload_gate_proof.json

### 14.5 現場狀態

- http://127.0.0.1:5002/api/status
- http://127.0.0.1:1234/api/v0/models
- dashboard\dist\pipeline-status.json

pipeline-status.json 必須有真實 Supervisor 心跳。is_running=true 但全案、資料夾與上傳長時間都不動，仍屬假死，不能只看綠燈。

---

## 15. 目前尚未解決的問題

### P0-A：202207 有 243 張尚未正式閉環

證據檔：

D:\00_商化\00_已OCR照片\_ocr_audit\202207_f8f3441d279c_2022-商化照片_商化照片-202207\blocked_after_recursive.csv

現況：

- source：1,240
- success records：1,238
- copied／ready：997
- review_required：241
- missing_result：2
- failed：0

原因：

- 233 張：尚未完成自動定案。
- 8 張：尚未完成自動定案，且舊型號未通過正式清單。
- 2 張：找不到 OCR 結果。

兩張 missing_result：

- M-台北市-內湖區-TK3C-內湖旗艦-249.jpg
- M-台南市-仁德區-TK3C-台南旗艦-524.jpg

正確處理：

1. 主線 202206 繼續，不得為 243 張停整批。
2. 先讀每張既有 source hash、request ID、trace 與呼叫額度。
3. 可由 immutable trace 決定者，用 resolve_capped_adjudication_queue.py 或正式 finalizer 零模型定案。
4. 舊 SKU 同卡證據成立者使用 .91 規則，不因清單未收錄清空。
5. 只有確實未用滿三次且既有證據不足者才可照片級重跑。
6. 定案後逐張上傳並寫 canonical receipt。
7. 不得重跑整個 202207，不得第 4 次呼叫。

### P0-B：202206 review_required 比率要監控內容

23:21 快照為 13／101，約 12.9%。這不是立即證明系統錯誤，但高於理想的第一輪結案率。

接手者應抽查：

- 是否大量明確遠景仍固定跑三輪。
- 是否明確側標未讀。
- 是否一般單機被 FollowMe 規則拖入複核。
- 是否 review 原因只是舊型號清單。
- 是否完成卡與終局內容一致。

若同一錯誤類型反覆出現，必須修共因與回歸測試；不能逐張人工改。

### P0-C：月份摘要有舊 HTTP 400

folder_summary.csv 目前仍有：

- 202206：error／HTTP Error 400: BAD REQUEST
- 202205：error／HTTP Error 400: BAD REQUEST
- 202204：error／HTTP Error 400: BAD REQUEST

但現行 202206 已在 port 5002 正常執行，證明至少 202206 該列是舊狀態。處理方式：

1. 不刪除舊列。
2. 不因舊列重啟目前批次。
3. 202206 完成後確認 runner 寫入新的 copied／ready 摘要。
4. 確認 202205 自動啟動。
5. 若下一月仍回傳 HTTP 400，抓取當下 API response、request body 與 backend log，修 attach／start 契約；只在照片邊界處理。

### P0-D：27 筆歷史上傳失敗工作

API 快照：

- failed = 27
- 現行 pending = 0
- last_error 空白
- canonical receipt 持續增加

已知分類：

- 15 筆 missing_recovery_envelope。
- 8 筆舊格式或無效 job。
- 3 筆舊 runtime-fuse-active。
- 1 筆 2022 遠端同名衝突。

2022 同名衝突：

M-新北市-新莊區-SF-輔大-1062.jpg

錯誤：

duplicate exact remote names require ID-scoped cleanup before upload

正確處理：

- 逐筆確認 source identity、final result、遠端 file ID、大小與 MD5。
- 只修有完整 recovery envelope 的工作。
- 同名工作使用精確 Drive ID 清理或取代。
- 不得整批移回 pending。
- 不得建立 _2。
- 不得讓這 27 筆停止新照片上傳。

### P1-A：Git 與正式執行版本尚未封存

- branch：main
- HEAD：e4ea877b4af4104043de63640d60fe18b2de8781
- HEAD 日期：2026-07-24
- live evidence_guard_revision：20260803.92
- working tree：約 139 筆差異

因此 Git HEAD 不是目前執行中的完整版本。接手者：

- 不得 reset --hard。
- 不得 checkout --。
- 不得 clean。
- 不得把所有未追蹤檔一次加入。
- 要先辨認程式、文件、測試與本機 runtime artifacts。
- 若使用者要求 Git，先做範圍明確的 diff、測試、分批 stage、commit，再 push。

### P1-B：舊 runbook 有 port 5000 範例

現行正式 port 是 5002。ai_handoff_runbook.md 的舊 port 5000 內容只能作一般歷史範例，不能直接複製到本機正式環境。

### P1-C：尚未用瀏覽器控制取得本次新畫面證據

本次已核對：

- API identity。
- asset fingerprint。
- compact queue。
- 程式契約。
- presentation soak。
- production build。

但瀏覽器控制層先前無法附著既有 Chrome，因此不得宣稱已取得本次實際畫面截圖。使用者已切到正確分頁；下一位 AI 若能附著，只可使用既有分頁，不得新增分頁或重新載入冒充同步。

### P2-A：嚴格 JSON schema 尚未安全啟用

目前正式 LM Studio request 未送出嚴格 response_format／json_schema。直接加上現有舊 schema 可能反而拒絕新欄位或降低相容性。

若要改善：

1. 先讓 schema 與現行 evidence contract 完全一致。
2. 用固定盲測集比較格式失敗率、辨識正確率、速度與呼叫數。
3. 驗證 Qwen3-VL-8B 與候選模型。
4. 通過完整回歸後才可在安全照片邊界套用。

---

## 16. 下一位 AI 的接手順序

### 第 1 階段：前 5 分鐘，只讀不動

1. 讀 AGENTS.md。
2. 讀 docs/development_guide.md 的最高鐵律與最新修訂。
3. 讀本文件。
4. 讀 docs/three_layer_accuracy_gate.md 相關章節。
5. 查 git status，不修改或整理工作目錄。
6. 讀 port 5002 API 與 LM Studio models API。
7. 確認 current_file 與 stream_file 相同。
8. 確認 runtime fuse、pipeline pause、上傳 last_error。
9. 確認唯一邏輯 backend、runner、uploader 與 continuity daemon。

### 第 2 階段：證明主線有真實前進

在 30 至 60 秒間隔重新讀兩次：

- overall_progress.processed_images。
- review_progress.processed。
- current_file。
- stream_upload.canonical_uploaded。
- stream_upload.pending。

至少其中一項照片級指標應變化。GPU 高不等於 OCR 有前進；綠燈也不等於健康。

### 第 3 階段：內容抽查

低功耗抽查最近至少三張：

- 原圖與照片類型一致。
- 側標／型號／價格屬於同一主角。
- 自然敘述與結構欄一致。
- terminal result 與右側卡片一致。
- 實際模型呼叫未超過三次。

健康時只記一則精簡報告，不逐張介入。

### 第 4 階段：並行關閉舊欠項

主線 202206 持續跑；另以照片級工具處理 202207 的 243 張。先零模型、後必要重跑，完成一張上傳一張。

### 第 5 階段：驗證跨月接力

202206 完成時必須確認：

- folder_summary 新摘要取代舊 HTTP 400 意義。
- 202205 自動開始。
- Dashboard 不顯示長時間待機。
- 右側累積卡不清空。
- 上傳持續。

---

## 17. 唯讀健康檢查命令

以下命令不會修改正式狀態。

### 17.1 Dashboard

    $s = Invoke-RestMethod 'http://127.0.0.1:5002/api/status'
    $s | Select-Object version,evidence_guard_revision,status_contract_version,is_running,current_relative_dir,current_file,stream_file
    $s.overall_progress | ConvertTo-Json -Depth 6
    $s.review_progress | ConvertTo-Json -Depth 6
    $s.stream_upload | ConvertTo-Json -Depth 6
    $s.resources | ConvertTo-Json -Depth 4
    $s.runtime_health_fuse | ConvertTo-Json -Depth 6
    $s.pipeline_pause | ConvertTo-Json -Depth 6

### 17.2 LM Studio

    Invoke-RestMethod 'http://127.0.0.1:1234/api/v0/models' | ConvertTo-Json -Depth 8

必須看到 qwen/qwen3-vl-8b 的 state = loaded。

### 17.3 Port 與程序

    Get-NetTCPConnection -State Listen -LocalPort 5002,1234

    Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'samsung_ocr_batch_processor|recursive_ocr_flat_export|stream_drive_upload|ocr_continuity_daemon' } | Select-Object ProcessId,ParentProcessId,Name,CommandLine

不要把查詢命令本身誤算成正式程序。

### 17.4 排程

    Get-ScheduledTask -TaskName 'SamsungOCR_UserContinuityEnsure'
    Get-ScheduledTaskInfo -TaskName 'SamsungOCR_UserContinuityEnsure'

### 17.5 Git

    git status --short --branch
    git rev-parse HEAD
    git diff --check

---

## 18. 重開機或服務中斷後的安全恢復

### 18.1 正式入口

重開機後先確認 LM Studio port 1234 與 qwen/qwen3-vl-8b。再確認 SamsungOCR_UserContinuityEnsure 排程是否執行。若排程存在但尚未執行，可手動觸發：

    Start-ScheduledTask -TaskName 'SamsungOCR_UserContinuityEnsure'

該任務使用隱藏 VBS：

tools\ocr_continuity_ensure_hidden.vbs

它是現場正式恢復入口，會依既有程序、receipt 與 checkpoint 確認服務，不應開可見終端機。

### 18.2 驗證順序

1. port 1234 有 loaded model。
2. port 5002 只有一個 listener。
3. API 可讀。
4. historical continuation receipt 存在且雜湊一致。
5. receipt-bound recursive runner 只有一個邏輯實例。
6. uploader 只有一個邏輯實例。
7. current_file 開始變化。
8. canonical_uploaded 開始增加或 pending 正常變動。
9. 使用者原本分頁自行顯示新 fingerprint；不新增分頁。

### 18.3 不可直接按的舊入口

正式主線已存在時，不得同時執行：

- START_FULL_AUTO_OCR.bat
- _handoff_start_recursive.bat
- _handoff_start_backend.bat
- run_recursive_ocr_flat_export.bat

這些一般或歷史入口可能缺少現行 receipt，會製造重複 runner 或把舊年份掛回來。

### 18.4 runtime fuse

只有 runtime_health_fuse 有具體檔案與 staging 綁定時，才可使用：

    .\.venv\Scripts\python.exe tools\recover_contained_request_binding_fuse.py --staging-dir <API 證明的同一 staging> --fuse-file <API 證明的同一 fuse file>

先不加 --apply 做 dry-run。確認 request、source hash、剩餘額度、checkpoint 與修復範圍完全一致後，才可再加 --apply。

不得拿這個工具清除內容跑歪、跨照片污染或未知 fuse。

### 18.5 安全後端換版

只有以下條件成立才可使用 reload_backend_at_safe_idle.ps1：

- current_file 為空或照片已完成。
- runner checkpoint 已保存。
- uploader working = 0，或已證明可安全接續。
- 沒有第二個正式 backend。
- 測試與 build 已通過。
- 不需要重開瀏覽器。

    & .\tools\reload_backend_at_safe_idle.ps1 -RepoRoot 'D:\00_商化\samsung-monitor-ocr' -SourceRoot 'D:\00_商化\00_未整理商化照片' -OutputDir 'D:\00_商化\00_已OCR照片' -BackendUrl 'http://127.0.0.1:5002' -ApiBase 'http://127.0.0.1:1234/v1' -Model 'qwen/qwen3-vl-8b' -AllowIncompleteStoppedBatch

不得為了改 CSS、看畫面或清舊卡片重啟正式 OCR。

---

## 19. 修改程式的固定流程

1. 先寫清楚觀察到的錯誤。
2. 找出系統性根因，不只修單張。
3. 列出不可退步的既有規則。
4. 新增最小可重現測試。
5. 用 apply_patch 修改。
6. 先跑針對性測試。
7. 再跑關鍵回歸。
8. Dashboard 變更要做 production build。
9. 在照片邊界安全換版。
10. 用兩個照片週期與三張內容抽查證明。
11. 更新 development guide、handoff 或 SKILL。
12. 若使用者要求 Git，再做分批 stage、commit、push。

禁止：

- 為單張照片大量改 Prompt。
- 為了提高通過率放寬證據。
- 把上一輪答案帶進下一輪。
- 把 review_required 改名成 success 冒充完成。
- 只修 UI 文案而不修資料來源。
- 用 stale summary 清除 live state。
- 一邊正式 OCR 一邊跑會爭用 GPU 的全模型 benchmark。

---

## 20. 回歸驗證

依修改範圍選擇，至少包含：

### OCR／守門

    $env:PYTHONUTF8='1'
    $env:PYTHONIOENCODING='utf-8'
    $env:PYTHONLEGACYWINDOWSSTDIO='0'
    .\.venv\Scripts\python.exe -m unittest tools.test_review_pass_contract tools.test_three_call_cap tools.test_three_pass_finalization tools.test_v1945_evidence_contract

### Dashboard／同步

    .\.venv\Scripts\python.exe -m unittest tools.test_presentation_soak
    Set-Location dashboard
    npm.cmd run build

### 上傳

    .\.venv\Scripts\python.exe -m unittest tools.test_stream_drive_upload

### 關鍵全套

    .\.venv\Scripts\python.exe tools\run_critical_regressions.py

完整測試通過仍不代表現場一定健康。換版後還要驗證：

- 進度前進。
- 最近三張內容正確。
- current_file／stream_file／卡片一致。
- 平均呼叫數與第一輪結案率沒有惡化。
- 上傳 receipt 增加。

---

## 21. 完成定義

### 單張完成

- 有同圖終局結構結果。
- 不超過三次本機模型呼叫。
- 類型、型號、價格與歸屬一致。
- 檔名與結果一致。
- 已進 upload queue。
- 遠端大小與 MD5 回讀完成。
- canonical receipt 已寫入。

### 資料夾完成

- source count = terminal unique count。
- missing_result = 0。
- review_required 已由自動定案或照片級修復關閉。
- copy／rename conflict = 0。
- 應上傳照片都有 receipt。
- folder_summary 寫入新摘要。

### 年度完成

- 所有月份完成。
- 年度 unique source 全閉環。
- terminal summary 與 Drive proof 一致。
- sealed marker 與 receipt 綁定。
- Supervisor 不再把年度掛回。

### 全案完成

- 151,714／151,714。
- 137／137。
- 2015 至最新納入年度全閉環。
- 無未知 missing result。
- 無未處理的 active failed upload。
- 最終審計、README、開發手冊、SKILL 與 Git 版本一致。

---

## 22. 下一步工作排序

### 立即，不中斷主線

1. 確認 202206 持續前進與上傳持續增加。
2. 每 09:00／21:00 做一次低功耗四維監督：進度、內容、介面、上傳。
3. 抽查 review_required 是否屬同一系統性原因。

### 第一優先欠項

1. 以既有 trace 零模型處理 202207 的 241 張 review_required。
2. 精確處理兩張 missing_result，遵守剩餘呼叫額度。
3. 每張完成立即上傳。
4. 讓 202207 最終 source = terminal = receipt。

### 跨月接力

1. 202206 完成後驗證 folder_summary 更新。
2. 驗證 202205 自動開始，不受舊 HTTP 400 影響。
3. 若重現 HTTP 400，修 attach／start 契約並新增組合測試。

### 上傳欠項

1. 先處理唯一 2022 同名衝突。
2. 再逐筆稽核有 recovery envelope 的舊失敗工作。
3. 其餘舊 2026 failed job 與已封存 2026 收據交叉核對，不可盲目重送。

### 後續改善

1. 對齊 strict JSON schema 後做離線盲測。
2. 比較 Qwen3-VL-8B 與本機候選模型，但不干擾正式主線。
3. 將全案總進度單調性、封存月份不可復活、舊欄位 schema migration 納入永久測試。

---

## 23. 每日精簡報告格式

    Samsung OCR 09:00／21:00
    - 全案：目前／151,714；最近 12 小時新增：
    - 資料夾：YYYYMM，目前／總數；verified／review／failed：
    - 上傳：canonical；最近 12 小時新增；pending／working／failed：
    - 推論：median／P90；平均呼叫數；第一輪結案率：
    - 系統：port 5002、LM Studio、runner、uploader、Supervisor：
    - 資源：GPU、顯示記憶體、parallel／context：
    - 同步：current_file／stream_file／右側卡：
    - 內容抽查：三張結果與證據是否一致：
    - 欠項：只列新增或有變化者：

健康時只回一則，不洗版。異常時說明：

- 何時開始。
- 影響照片／資料夾。
- 是照片級或系統級。
- 是否已在照片邊界保護。
- Dashboard 是否仍在線。
- 自動續跑條件。
- 修復證據與新增測試。

---

## 24. 不得重犯清單

- 不得把 2026 完成說成全案完成。
- 不得把總進度固定顯示成 66,724 或其他舊快照。
- 不得用 success 取代 verified／receipt。
- 不得讓每張單機固定跑二、三輪。
- 不得讓每張明確遠景固定跑三輪。
- 不得因 FollowMe 精確 SKU 不清而推翻 FollowMe 家族。
- 不得忽略螢幕右上／左上直接側標。
- 不得把不同螢幕的型號與價格拼在一起。
- 不得讓 partial neighbor 變成三台完整遠景。
- 不得把上一輪答案送進下一輪。
- 不得超過三次真實模型呼叫。
- 不得留下「待慢模型或人工裁決」的無限佇列。
- 不得把單張技術錯誤升級成整批停止。
- 不得等整月／整年才上傳。
- 不得因網路慢停止 OCR。
- 不得讓舊卡或舊照片搶回左側預覽。
- 不得清空右側跨月累積卡。
- 不得開新瀏覽器視窗或分頁。
- 不得讓可見終端機反覆跳出。
- 不得盲目啟用舊 Scheduled Task。
- 不得把 venv launcher 與 runtime child 誤判成雙實例。
- 不得直接使用舊 port 5000 指令。
- 不得用雲端模型逐張 OCR。
- 不得大幅改寫成熟 Prompt。
- 不得切換模型而不做固定盲測。
- 不得 reset、clean 或覆蓋未提交的 .92 工作目錄。

---

## 25. 接手確認表

下一位 AI 在開始修改前應能明確回答：

- 本專案終點是什麼？
- 為什麼 2026 不得重跑？
- 現在主線是哪個月份？
- 全案與目前月份的即時數字從哪裡讀？
- 為什麼 202207 copied 仍不算全閉環？
- 243 張欠項如何處理且不阻塞主線？
- 真實模型呼叫上限如何跨重啟保存？
- 哪些照片第一輪可直接結案？
- FollowMe 家族、精確 SKU、價格為何要分欄？
- 側標與價牌的證據優先順序是什麼？
- pending 上傳何時是正常、何時是異常？
- 重開機後唯一安全入口是什麼？
- 為什麼不能再按一般 BAT？
- 為什麼目前 Git HEAD 不代表 live .92？
- 哪些變更一定要加回歸與 production build？

若無法回答，先讀文件與證據，不要操作正式程序。

---

## 26. 相關文件

- AGENTS.md：語言、UI、模型與安全規範。
- docs/development_guide.md：永久鐵律、架構與修訂紀錄。
- docs/three_layer_accuracy_gate.md：三層守門與證據契約。
- docs/continuity_handoff.md：完整歷史時間軸。
- docs/ai_handoff_runbook.md：一般環境啟動與操作。
- SAMSUNG_OCR_EXPERIENCE_SKILL.md：專案經驗與修復模式。
- docs/accelerate_this_project_handoff.md：2026-07-28 外部建議，僅供參考。

---

## 27. 最後提醒

這個專案的主要失敗模式不是「模型完全不會看」，而是程式把已看對的證據推翻、錯誤重試、狀態來源互相覆寫、單張問題停止整批，以及介面沒有如實呈現主線。

下一位 AI 的責任不是重新發明整套流程，也不是逐張人工救火。正確做法是：

1. 保住目前正在前進的本機主線。
2. 用固定程式關閉既有欠項。
3. 修系統性共因並加入永久測試。
4. 讓每張照片最多三次、一定定案、立即上傳。
5. 讓 Dashboard 在原分頁持續、同步、如實顯示。
6. 直到 151,714 張與所有 Drive 收據真正閉環。

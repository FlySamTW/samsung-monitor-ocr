# 三層即時守門設計與驗證（v19.45）

本文是 2026 照片複核的權威設計說明。核心目標是：明確的照片不浪費額外輪次；有疑慮的照片立即複核；每張照片最終都必須有如實結果並立即排入逐張上傳。只有請求綁定、跨照片汙染、系統輸出或上傳回讀等技術完整性錯誤可暫停該張。

## 設計原理：有條件升級，不是三輪投票

三層守門是同一張照片最多三次、但角色不同的判讀鏈：

1. **第一層是基準辨識與證據契約**：從原圖建立單機／遠景、唯一主角、價牌歸屬、型號、價格與 FollowMe 實體線索。證據完整且沒有疑點的一般單機可以立即定案。
2. **第二層是無前輪記憶的定向交叉複核**：只在第一層有疑點，或照片屬於規定必須複核的高風險類別時啟動。模型只收到固定規則、通用反證清單與當前照片／定向裁切，不得收到第一輪答案、疑點文字或模型回覆；它會獨立重查漏讀、錯綁價牌與 FollowMe／遠景誤判。第二輪完成後，只有程式守門器可在模型呼叫之外比較兩輪結構化證據。
3. **第三層是去偏誤的獨立裁決**：第二層仍有疑點時才啟動。模型同樣不得收到前兩輪答案，並強制加入更集中的下方價牌裁切，先獨立產生第三份證據；守門器再於模型呼叫之外把三輪結構化證據交叉比對。可用證據以二票定案視角；型號、價格則各自必須有至少兩份安全證據，且不得拼出沒有兩輪共同支持的型號／價格組合。

因此它不是以下三種機制：

- 不是每張照片都固定耗費三輪；完整的一般單機第一輪即可通過。
- 不是無條件「三輪取多數」；只有通過請求綁定、同圖雜湊、獨立性與證據契約的輪次才有投票權。
- 不是「最新答案覆蓋舊答案」；第三輪較新不代表較正確，定案只來自可驗證的同圖共識。

這個分工同時控制兩種風險：第二輪利用固定反證任務精準找錯，但不看舊答案；第三輪再用不同強度的裁切獨立裁決。跨輪答案只供結構化守門器在模型呼叫完成後比較，絕不可回餵模型造成自我合理化。最後的接受權永遠在結構化守門器，不在自然語言敘述或任何單一輪模型輸出。

## 各類照片的最低通過門檻

| 類型 | 最早可通過輪次 | 必要條件 | 未達條件 |
|---|---:|---|---|
| 一般單機 | 第 1 輪 | 唯一主角、自己的型號／價格與價牌歸屬一致，品質與結構證據無疑點 | 下一格立即第 2 輪 |
| 2026 單機缺型號或缺價格 | 第 2 輪 | 新一輪補齊欄位，且沒有與先前核心證據衝突 | 仍缺或衝突就第 3 輪 |
| 2026 FollowMe | 第 2 輪 | 至少兩輪，且 FollowMe 字樣／型號必須綁定同一實機的直接品牌證據，或至少兩項獨立強實體線索 | 宣傳畫面或附近立牌不能單獨通過；仍有疑點就第 3 輪 |
| 2026 遠景 | 第 3 輪 | 至少兩輪可用證據支持至少三台完整入鏡、無唯一主角、無可歸屬的主角型號／價格，並排除 FollowMe 實體線索 | 定案為遠景、型號與價格留空，立即排入上傳 |
| 呼叫、解析或證據契約失敗 | 不得直接通過 | 技術失敗也計入同一張最多三次模型呼叫 | 第三次仍為技術錯誤就記錄終端技術結果；不得出現第 4–6 輪 |

「遠景」的判準不是背景中出現多台螢幕，而是無法鎖定唯一主角及其自己的價牌。相反地，FollowMe 的螢幕內容、海報或附近立牌都只是弱線索；必須看到線索與同一台實機的物理歸屬，才能避免把賣場宣傳物借給錯的主角。

FollowMe 守門只能由明確的 FollowMe 名稱，或同一實機的白色垂直支架、圓形落地底座、附著託盤／產品卡等充分實體證據建立。裸露的 `S32FM...`／`S43FM...` 是 Smart Monitor 面板 SKU，不等於 FollowMe 移動組合；沒有同主體實體證據時必須保留為一般單機，不能因 `FM` 字樣誤套 FollowMe。SKU 只可在 FollowMe 實體身分已先成立後，用來比較 M5／M7／Pro 家族是否一致。

## 計數單位

- `複核 490/1,504` 是已走完守門流程的「照片數」。
- `累計判讀 1,013 次` 是所有照片的「判讀事件數」，包含第二、第三輪，因此可大於照片數。
- `本張第 1/3 輪` 是目前照片的嘗試層級，最多三輪。
- `完成判讀` 只代表已產生非系統失敗的結果，包含 `review_required`；它不等於品質驗證成功。`待複核` 必須另外顯示，只有 `auto_verified=true` 且 `auto_review_required=false` 才是自動驗證通過。

`presentation_sequence` 必須在後端啟動時從 `_ocr_audit/presentation_history` 取回歷史最大值，使後續序號單調增加。如果重啟後從 0 開始，UI 不得稱為「累計判讀」。

## 第一層：結構化證據守門

每輪必須輸出並驗證 `complete_screen_count`、`unique_main`、`label_ownership` 與 `followme_physical_evidence`。自然語言敘述只能提醒風險，不能補造缺失證據。

第一輪可直接通過的一般單機必須同時滿足：

1. 能鎖定唯一主角。
2. 型號、價格與實體價牌屬於同一台主角。
3. 型號、價格、品質與螢幕狀態沒有未解決衝突。
4. 結構欄位、敘述與原始 JSON 的核心結論一致。

以下繞過路徑一律視為衝突：`view_type` 與 `category` 指向不同類型；結構說單機但敘述明確說「符合遠景條件」；`label_ownership=matched` 但敘述說價牌屬於旁邊／鄰機／無法歸屬；已有正式 Samsung SKU 時，不能只因螢幕播放 ASUS/LG Demo 內容就改成它牌。

任一條不成立時，不儲存為定案，立即排入下一個佇列位置做第二輪。

## 第二層：定向交叉複核

第二輪使用新的無狀態模型呼叫、固定反證規則與定向裁切重新判讀。它不得取得第一輪結果、疑點文字或任何舊模型回答；以下條件只由程式守門器決定是否排入第二輪，不得作為帶答案提示注入模型：

- 2026 單機缺型號或缺價格。
- 價牌歸屬不明、唯一主角不明或品質不合格。
- 型號、價格、結構證據與敘述互相矛盾。
- 照片價格與官方參考價差異達 20% 以上時至少獨立重讀一次；若下一輪讀到同一型號、同一照片價格且價牌歸屬仍為 `matched`，照片實證優先於官方參考價。不得強迫照片售價等於官網價。
- FollowMe 候選：2026 FollowMe 至少要兩輪一致，且必須有同一主體的白色垂直支架、圓形底座、託盤或同等實體證據；宣傳立牌單獨不算。
- 遠景候選：2026 遠景不得在第一、第二輪直接定案。

第二輪若證據完整且解決原疑點，可定案；否則立即進第三輪。

## 第三層：獨立裁決與失敗封閉

第三輪使用獨立判讀與另一組決定性裁切，再與前兩輪的核心證據比對。

- 遠景在至少兩輪可用證據支持「無法鎖定唯一主角及其自己的型號／價格」，且沒有 FollowMe 實體證據時，定案為遠景。
- 遠景不是「照片裡有多台螢幕」；若仍能鎖定唯一主角與自己的價牌，就不能只因背景多台而判遠景。
- 第三輪後由 `.26` 定案器決定遠景或單機；無共識的型號／價格保留為空，不得猜測，但照片仍是已完成結果。兩輪同主體 FollowMe 強實體證據可確認 FollowMe 家族；版本無共識時標示 `FollowMe 型號未細分`，不可退回遠景。三輪皆為同圖健康判讀、但遠景輪只數到 1–2 台完整螢幕而無法形成安全視角共識時，保守定案為單機且不猜型號／價格，不得冒充技術錯誤。完整台數只能由第一張原始全圖計算一次；外框四邊四角都在原圖內才算，補充裁切不得重複計數。每輪必須逐區掃完整張原圖，中央完整、左右裁切只有在其他區域沒有任何完整螢幕時才可定為 1；一整排、展示牆、多層貨架或寬廣走道不得套用近拍例外。若一輪提供有效 3+ 遠景結構，而另兩輪只是無型號、無價格、無價牌歸屬與無 FollowMe 實體證據的寬景弱單機票，遠景結構必須否決弱票；有身分或實體證據的單機共識仍優先。每張最多三次模型呼叫，技術錯誤也不得產生第四次呼叫或第四輪卡片。

## 狀態轉移

| 當前結果 | 守門決定 | 下一步 |
|---|---|---|
| 第一或第二輪完整且無疑點 | `verified=true` | `accepted`，計入完成照片 |
| 第一或第二輪有疑點 | `retry=true` | `retry_scheduled`，立即插入下一格 |
| 第三輪完整且三輪一致 | `verified=true` | `accepted` |
| 第三輪欄位無共識 | `three_pass_adjudicated=true` | 定案視角，無共識欄位留空，立即排入上傳 |
| 呼叫或解析失敗 | `failed` | 記錄失敗，不冒充照片完成 |

## 可稽核證據

1. `/api/status.review_progress`：目前照片進度、當前輪次、成功與失敗數。
2. `presentation_queue`：每輪的 `presentation_sequence`、`pass_index`、`decision` 與 `retry_reason`。
3. `_ocr_audit/v1945_evidence_trace.jsonl`：每輪原始結果、正規化證據、`guard_decision` 與接受／拒絕原因。
4. `/api/presentation_history/<source_item_id>`：單張照片所有輪次的可點查歷程。
5. `_drive_upload_stream`：每張 `.22` 已驗證結果的持久上傳佇列；遠景、缺型號或缺價格都是可上傳結果，只有技術完整性錯誤不得入列。

### 守門規則修訂碼

`v19.45` 只代表證據欄位契約，不能單獨證明當時已執行目前完整的三層守門。每筆新結果與 trace 必須同時帶有 `evidence_guard_revision=20260716.19`。`.15` 保留 `.3`–`.14` 的衝突、FollowMe、跨輪隔離、未收錄型號照片共識、價牌短碼補全、遠景語意比較、共用證據 Schema、「0／1／2 台完整入鏡永遠不是遠景」與結構答案權威；並新增不可繞過的硬閘：`view_type/model/price` 的 material structured-authority block 失效，敘述已有至少兩項同主體 FollowMe 實體線索但結構仍不足以建立同一前景主體時失效，任何已有此矛盾的前輪不得被後輪洗白。結構已具直接品牌或至少兩項獨立同主體強證據的單機，不因敘述多提一個方向／卡片細節而熔斷；遠景不適用此寬限。只要本輪結構物件明示 `model` 或 `price`，即使值為 null，敘述都不得用鄰近價牌文字補入；只有舊格式完全缺少該結構欄位時才保留保守救援。大型照片第一輪即附中央全高證據圖，後續輪次使用左右／中央重疊裁切。前景直立螢幕若同時連著白色圓形底座與託盤，即使直桿部分被遮住、背景有三台以上電視，也必須保持 FollowMe 單機候選；螢幕播放廣告只是不足以單獨證明 FollowMe 的弱線索，絕不能反向否定實體結構。自然判讀不得抄提示規則、不得出現 `最終校正` 類後端代寫文案；原始矛盾要留在 trace，由健康閘收回顯示並 retry／review，不得改寫掩蓋。第一輪限定的可隔離 FollowMe／遠景矛盾只允許一次無記憶第二輪；第二輪重犯或模型／價格／提示／介面內容異常會寫入含有限快照的 durable fuse 並停止。遠景模型仍必須明示完整入鏡至少 3、`unique_main=false`、無主角自有價牌且無同主體 FollowMe 強證據。`.16` 另綁定 128-bit request ID、全圖 SHA-256 與跨照片重複核心。`.17` 要求 Pro 43 具備本張可觀察的 Pro／43／S43FM／17,990 身分證據，並要求 2026 FollowMe 各輪型號與價格全數一致；任何衝突都保持待複核，二對一不得洗白。`.18` 僅把已建立的友善名稱與實體 SKU 視為同一款（M5 32↔S32FM50、M7 32↔S32FM70、Pro M7 43↔S43FM70），不同系列或尺寸仍是衝突；敘述健康閘只有在未否定的 FollowMe 身分或明確白色圓底座／託盤、白色直立支架組合時才熔斷，普通黑色短架與託盤即使敘述明示「非 FollowMe」也不得誤停。`.19` 再加入兩道不可繞過的防線：任何 2026 單機候選只要結構聲稱至少三台完整螢幕入鏡，就必須三輪獨立且 view/model/price/unique_main/ownership 全數一致；任一差異即待複核。人工確認過的高風險原圖以完整 SHA-256 綁定期望分類，模型若與既知像素真值衝突，內容健康閘立即熔斷；650 的既知遠景像素永遠不得冒充單機成功。舊 `.18` trace 不得冒充 `.19`。

監控的定義不是只看數字前進，而是同時驗證進度、內容品質、介面健康、上傳隔離。任何一項失敗都必須熔斷；健康閘停止的 run 不得由排程自行續跑。

- 缺少這個修訂碼的舊 `v19.45 verified` 紀錄，仍視為未通過新規則，必須重新複核。
- backfill 候選器只會跳過「契約版本、守門修訂碼、`verified=true`」三者同時正確的原圖。
- 成功 CSV、Label Studio 中繼資料、每輪 trace、重跑完成判定與 Drive manifest 都必須傳遞並重新核對該修訂碼。
- 舊 trace 遷移工具不得自動補上新修訂碼；沒有新規則實際判讀，就不能偽造新驗證身分。

## 程式中的責任邊界

- `samsung_ocr_batch_processor.py::build_ocr_messages()`：每一輪只能帶入固定規則與當前原圖；第二、第三輪都不得注入任何前輪答案。
- `skills/batch_orchestrator.py::BatchOrchestrator`：疑慮照片放入 `retry_queue` 的第一格，使呼叫順序成為 `A1 → A2 →（必要時 A3）→ B1`；中間猜測不得寫入正式成功檔或完成卡片。
- `skills/audit_fields.py::validate_evidence_contract()`：只認結構化螢幕數、唯一主角、價牌歸屬與 FollowMe 同主體實體線索。
- `skills/audit_fields.py::immediate_retry_decision()`：決定 `verified`、`retry` 或 `unresolved`，並執行一般單機、FollowMe、遠景各自的最低輪次。
- `tools/prepare_drive_upload_manifest.py`：上傳端再次失敗封閉；即時介面顯示完成不等於具備上傳資格。

模型負責提出觀察，守門器負責接受或拒絕，manifest 再負責最後的上傳隔離。三者不可合併成「模型說成功就上傳」。

## 必跑驗證

```powershell
.\.venv\Scripts\python.exe tools\test_v1945_evidence_contract.py
.\.venv\Scripts\python.exe tools\test_immediate_retry_queue.py
.\.venv\Scripts\python.exe tools\test_runtime_safety_guards.py
.\.venv\Scripts\python.exe tools\run_critical_regressions.py
.\.venv\Scripts\python.exe tools\test_presentation_soak.py
cd dashboard
npm run build
```

必須有以下正向證明：

- 完整一般單機可在第一輪通過，不強迫浪費輪次。
- 2026 FollowMe 第一輪會排第二輪，第二輪證據一致才通過。
- 2026 遠景必須完成三輪獨立複核。
- 結構說單機、敘述說遠景，或相反的衝突不能定案。
- `view_type/category` 衝突與價牌歸屬敘述衝突不能定案。
- FollowMe 正式 SKU 不得繞過同主體實體證據與第二輪。
- 畫面播放它牌 Demo 不得覆蓋清楚的 Samsung 實體 SKU。
- 大幅官方價差必須至少有一輪獨立同值確認，確認後保留照片上的實際售價。
- 第三輪內容欄位仍衝突時由 `.22` 只保留有兩輪安全證據的部分，缺乏共識的欄位留空，該張仍完成並逐張上傳；只有技術完整性失敗才不可上傳。
- Dashboard 必須顯示同一 `presentation_id` 的照片、AI 判讀、處理中卡片，右側完成卡片只能在該輪結束後顯示。
- 右側縮圖與單張判讀歷程必須依實際完成時間排序，不能只依輪號排序；服務恢復造成輪號重設時，新結果仍須顯示在最上方。
- 累計判讀數必須從持久化歷程恢復；若歷程中出現服務重啟後從 1 開始的新區段，必須把各區段判讀數累加，不能倒退或漏算。

### 驗證矩陣

| 要證明的原理 | 自動驗證 |
|---|---|
| 第三輪不繼承第一、二輪答案 | `test_third_pass_messages_are_independent_of_prior_answers` |
| 一般單機證據完整可第一輪通過 | `test_valid_single_is_auto_verified_without_forcing_extra_passes` |
| FollowMe 第一輪不能定案、第二輪一致才通過 | `test_current_year_followme_requires_second_consistent_pass` |
| FollowMe 正式 SKU 也不能繞過實體證據 | `test_followme_sku_requires_physical_evidence_and_second_pass` |
| FollowMe 弱宣傳線索不能建立型號 | `test_followme_cue_codes_are_atomic_and_weak_cues_cannot_establish_model` |
| 單機結構與明示遠景敘述衝突會拒絕 | `test_single_structure_cannot_ignore_explicit_distant_narration` |
| `view_type/category` 與價牌歸屬矛盾會拒絕 | `test_view_type_and_category_conflict_fails_closed`、`test_matched_label_cannot_contradict_narration_ownership` |
| 它牌 Demo 畫面不能覆蓋 Samsung SKU | `test_screen_content_brand_does_not_override_samsung_sku` |
| 最終它牌與原始 JSON Samsung SKU 衝突必須封閉 | `test_negated_screen_brand_and_raw_samsung_sku_cannot_become_other_brand` |
| 大幅價差需要獨立同值確認 | `test_large_official_price_difference_requires_independent_confirmation` |
| 2026 遠景必須三輪一致 | `test_current_year_distant_requires_three_consistent_passes` |
| 任一核心證據跨輪衝突，第三輪仍須封閉 | `test_third_pass_core_disagreement_is_unresolved` |
| 疑慮照片立即插到下一格，不被 B 照片超車 | `tools/test_immediate_retry_queue.py`，呼叫順序必須為 `A1, A2, B1` |
| 缺少結構化證據不得冒充完成 | `test_confirmed_cases_fail_closed_without_structured_evidence` |
| 未通過者不能進上傳名單 | `test_trace_persistence_shape_and_upload_exclusion` 與 manifest 守門測試 |
| 舊 `v19.45 verified` 缺守門修訂碼時必須重跑 | `test_old_v1945_verified_trace_without_guard_revision_is_reprocessed` |
| 舊成功列缺守門修訂碼時不得完成或上傳 | `test_old_v1945_success_without_guard_revision_is_not_complete` 與 manifest 守門測試 |
| 內容跑歪必須跨程序持久熔斷 | `test_runtime_health_fuse_is_durable_and_fail_closed` 與 `test_every_continuation_and_upload_surface_checks_the_fuse` |

測試通過只證明程式規則沒有被改壞；正式執行還必須抽查 `_ocr_audit/v1945_evidence_trace.jsonl` 的真實三輪紀錄，並確認 Dashboard 同一 `presentation_id` 的照片、逐字判讀與右側卡片一致。單看程序仍在跑、成功數增加或介面看起來正常，都不能代替這項資料驗證。

## 不可放寬的鐵律

- 只有證據完整、同圖綁定且無汙染的輪次才能進入有條件多數決。
- 後一輪不得因為比較新就覆蓋前輪的未解衝突。
- UI 的「完成」只能表示照片已完成證據定案並排入上傳；單機缺型號／缺價格必須如實顯示，不可補猜。
- 上傳只讀取守門後的 ready manifest，不得從 UI 卡片、最新結果或單一輪判讀推斷可上傳。
- `_ocr_audit/runtime_health_fuse.json` 存在時，不得開始 OCR、排程續跑、重建開放型上傳證明或執行上傳；只能在修正、完整回歸與隔離試跑均通過後人工解除。

## 自動定案與技術熔斷（2026-07-16）

三層守門最後必須給每張照片一個真實結果：遠景，或單機的型號／價格完整度任一組合。三輪內容判讀不一致時，`.22` 只採用通過同圖綁定、獨立性、無汙染與證據契約的輪次定案；沒有欄位共識就留空，不得補猜。這類照片不隔離、不等待其他模型或人員，完成後立即排入逐張上傳。模型總呼叫達三次即為硬邊界，不得以「技術重試」之名跑到第 4–6 輪。

`.32` 補強三輪終點：三次同圖、request-bound、無前輪答案的呼叫完成後，普通單機可由兩輪相同非 FollowMe 型號／價格，或三輪一致的唯一主角結構定案；遠景可由至少兩輪多螢幕結構定案。兩輪以上確認 FollowMe 實體時可結案為 FollowMe 家族，但只要三輪對版本／價格組合有任何分歧，就必須顯示 `FollowMe（型號未細分）／無價格`，不得用二比一硬綁鄰近多張價牌。已人工核對的高風險照片以完整影像 SHA-256 綁定像素權威，三輪完成後可直接修正舊待複核列並排入逐張上傳，不得再呼叫模型。

只有技術完整性錯誤才熔斷：例如前輪答案汙染、request/image SHA 綁定不符、不同照片證據串線、模型或解析器失效、來源位元改變、Drive 回讀無法確認。相同的單張 FollowMe 敘述／結構矛盾出現在不同照片，只代表模型有同類視覺弱點，必須各自在三輪內處理，不能據此誤停整批；只有能證明答案或請求跨照片串線時才停止批次。

### `.29` 三次硬邊界補強

- 第三次之後只有兩種狀態：已定案，或已證實的技術完整性失敗。不得用內容分歧製造第 4–6 輪。
- 已人工查看原圖的高風險照片，只能以全圖 SHA-256 綁定權威；完成三次無記憶、request-bound 呼叫後可直接定案。
- 三輪都看到至少三台完整螢幕且都沒有可歸屬型號／價格，其中至少一輪明確判遠景時，第三輪定案為遠景。空的 `matched` 不是單機身分證據。
- 正常自然敘述即使超過 300 字也不能熔斷；只有真正複誦指令才能停批。

## 跨照片語意污染守門（`.16`）

- 模型回覆必須帶回本次完整 128-bit 隨機 `request_id`；缺少只允許一次從原圖無記憶重試，不一致立即熔斷。最後健康門本身還必須看到 `request_id_verified=true` 與 64 碼全圖 SHA-256，不可只信上游宣稱已驗證。
- trace 保留本次實際送出的全圖 SHA-256，用來分辨影像重用與模型語意漂移。
- 相鄰但來源身分不同的兩張照片，若產生完全相同的「型號＋價格」，不論前張是 verified 或 review-required，後一張都必須完成三輪無記憶複核並保留 unresolved；兩輪或三輪重複相同錯誤不得洗白。這個訊號不得寫進模型 prompt。

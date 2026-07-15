# 三層即時守門設計與驗證（v19.45）

本文是 2026 照片複核的權威設計說明。核心目標是：明確的照片不浪費額外輪次；有疑慮的照片立即複核；三輪後仍不一致時寧可待人工處理，不可冒充成功。

## 設計原理：有條件升級，不是三輪投票

三層守門是同一張照片最多三次、但角色不同的判讀鏈：

1. **第一層是基準辨識與證據契約**：從原圖建立單機／遠景、唯一主角、價牌歸屬、型號、價格與 FollowMe 實體線索。證據完整且沒有疑點的一般單機可以立即定案。
2. **第二層是無前輪記憶的定向交叉複核**：只在第一層有疑點，或照片屬於規定必須複核的高風險類別時啟動。模型只收到固定規則、通用反證清單與當前照片／定向裁切，不得收到第一輪答案、疑點文字或模型回覆；它會獨立重查漏讀、錯綁價牌與 FollowMe／遠景誤判。第二輪完成後，只有程式守門器可在模型呼叫之外比較兩輪結構化證據。
3. **第三層是去偏誤的獨立裁決**：第二層仍有疑點時才啟動。模型同樣不得收到前兩輪答案，並強制加入更集中的下方價牌裁切，先獨立產生第三份證據；守門器再於模型呼叫之外把三輪結構化證據交叉比對。任何核心衝突仍存在就失敗封閉為 `review_required`。

因此它不是以下三種機制：

- 不是每張照片都固定耗費三輪；完整的一般單機第一輪即可通過。
- 不是「三輪取多數」；兩輪同意也不能掩蓋缺失證據、價牌歸屬不明或核心衝突。
- 不是「最新答案覆蓋舊答案」；第三輪較新不代表較正確，未解衝突必須保留。

這個分工同時控制兩種風險：第二輪利用固定反證任務精準找錯，但不看舊答案；第三輪再用不同強度的裁切獨立裁決。跨輪答案只供結構化守門器在模型呼叫完成後比較，絕不可回餵模型造成自我合理化。最後的接受權永遠在結構化守門器，不在自然語言敘述或任何單一輪模型輸出。

## 各類照片的最低通過門檻

| 類型 | 最早可通過輪次 | 必要條件 | 未達條件 |
|---|---:|---|---|
| 一般單機 | 第 1 輪 | 唯一主角、自己的型號／價格與價牌歸屬一致，品質與結構證據無疑點 | 下一格立即第 2 輪 |
| 2026 單機缺型號或缺價格 | 第 2 輪 | 新一輪補齊欄位，且沒有與先前核心證據衝突 | 仍缺或衝突就第 3 輪 |
| 2026 FollowMe | 第 2 輪 | 至少兩輪，且 FollowMe 字樣／型號必須綁定同一實機的直接品牌證據，或至少兩項獨立強實體線索 | 宣傳畫面或附近立牌不能單獨通過；仍有疑點就第 3 輪 |
| 2026 遠景 | 第 3 輪 | 三輪都支持至少三台完整入鏡、無唯一主角、無可歸屬的主角型號／價格，並排除 FollowMe 實體線索 | 任一輪核心證據不同就 `review_required` |
| 呼叫、解析或證據契約失敗 | 不得直接通過 | 必須在輪次上限內取得完整有效結果 | 第 3 輪仍失敗就留下失敗／人工複核，不得上傳 |

「遠景」的判準不是背景中出現多台螢幕，而是無法鎖定唯一主角及其自己的價牌。相反地，FollowMe 的螢幕內容、海報或附近立牌都只是弱線索；必須看到線索與同一台實機的物理歸屬，才能避免把賣場宣傳物借給錯的主角。

FollowMe 守門同時辨識友善名稱與正式 SKU。`S32FM50x`、`S32FM70x`、`S43FM70x`（含 `LS...XZW` 完整碼）不得因為字串不是以 `FollowMe` 開頭就繞過實體證據與第二輪要求；`S32FM80x/S32FM90x` 則仍是一般 Smart Monitor，不能誤套 FollowMe 規則。

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

- 遠景只有在三輪都支持「無法鎖定唯一主角及其自己的型號／價格」，且沒有 FollowMe 實體證據時，才能驗證通過。
- 遠景不是「照片裡有多台螢幕」；若仍能鎖定唯一主角與自己的價牌，就不能只因背景多台而判遠景。
- 第三輪仍有任何核心衝突、證據缺失或高風險疑點時，設為 `unresolved/review_required`，交慢模型或人工校正；不得轉為 `accepted`。

## 狀態轉移

| 當前結果 | 守門決定 | 下一步 |
|---|---|---|
| 第一或第二輪完整且無疑點 | `verified=true` | `accepted`，計入完成照片 |
| 第一或第二輪有疑點 | `retry=true` | `retry_scheduled`，立即插入下一格 |
| 第三輪完整且三輪一致 | `verified=true` | `accepted` |
| 第三輪仍有疑點 | `unresolved=true` | `review_required`，禁止上傳 |
| 呼叫或解析失敗 | `failed` | 記錄失敗，不冒充照片完成 |

## 可稽核證據

1. `/api/status.review_progress`：目前照片進度、當前輪次、成功與失敗數。
2. `presentation_queue`：每輪的 `presentation_sequence`、`pass_index`、`decision` 與 `retry_reason`。
3. `_ocr_audit/v1945_evidence_trace.jsonl`：每輪原始結果、正規化證據、`guard_decision` 與接受／拒絕原因。
4. `/api/presentation_history/<source_item_id>`：單張照片所有輪次的可點查歷程。
5. `_drive_upload/drive_upload_review_required.csv`：所有 `unresolved`、高風險遠景、FollowMe 疑慮、缺型號或缺價格者必須留在這裡，不可進 ready manifest。

### 守門規則修訂碼

`v19.45` 只代表證據欄位契約，不能單獨證明當時已執行目前完整的三層守門。每筆新結果與 trace 必須同時帶有 `evidence_guard_revision=20260715.9`。`.9` 保留 `.3`–`.8` 的衝突、FollowMe、跨輪隔離、未收錄型號照片共識與價牌短碼補全，並修正遠景無法收斂：遠景模型輸出必須明示實際完整入鏡台數至少為 3、`unique_main=false`、無主角自有價牌且無同主體 FollowMe 強證據；跨輪比較只把各輪都至少 3 台的精確數量視為同一個 `3+` 證據，不把 3、10、12 台的非關鍵計數差當成核心衝突。任一輪少於 3、null、`unique_main` 不是 false、價牌 matched 或有同主體 FollowMe 強證據仍一律不得驗證。舊 `.8` trace 不得冒充 `.9`。

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
- 第三輪仍衝突時必須是 `review_required`，不能出現於可上傳清單。
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

- 多數決不能代替證據完整性。
- 後一輪不得因為比較新就覆蓋前輪的未解衝突。
- UI 的「完成」只能表示照片已離開即時守門；`review_required` 必須明顯標示待複核，不可呈現為已定案單機。
- 上傳只讀取守門後的 ready manifest，不得從 UI 卡片、最新結果或單一輪判讀推斷可上傳。
- `_ocr_audit/runtime_health_fuse.json` 存在時，不得開始 OCR、排程續跑、重建開放型上傳證明或執行上傳；只能在修正、完整回歸與隔離試跑均通過後人工解除。

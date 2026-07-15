# 三層即時守門設計與驗證（v19.45）

本文是 2026 照片複核的權威設計說明。核心目標是：明確的照片不浪費額外輪次；有疑慮的照片立即複核；三輪後仍不一致時寧可待人工處理，不可冒充成功。

## 計數單位

- `複核 490/1,504` 是已走完守門流程的「照片數」。
- `累計判讀 1,013 次` 是所有照片的「判讀事件數」，包含第二、第三輪，因此可大於照片數。
- `本張第 1/3 輪` 是目前照片的嘗試層級，最多三輪。

`presentation_sequence` 必須在後端啟動時從 `_ocr_audit/presentation_history` 取回歷史最大值，使後續序號單調增加。如果重啟後從 0 開始，UI 不得稱為「累計判讀」。

## 第一層：結構化證據守門

每輪必須輸出並驗證 `complete_screen_count`、`unique_main`、`label_ownership` 與 `followme_physical_evidence`。自然語言敘述只能提醒風險，不能補造缺失證據。

第一輪可直接通過的一般單機必須同時滿足：

1. 能鎖定唯一主角。
2. 型號、價格與實體價牌屬於同一台主角。
3. 型號、價格、品質與螢幕狀態沒有未解決衝突。
4. 結構欄位、敘述與原始 JSON 的核心結論一致。

任一條不成立時，不儲存為定案，立即排入下一個佇列位置做第二輪。

## 第二層：定向交叉複核

第二輪取得第一輪結果、疑點與完整規則，使用新的模型呼叫與定向裁切重新判讀，不是複製第一輪答案。以下情形必須進第二輪：

- 2026 單機缺型號或缺價格。
- 價牌歸屬不明、唯一主角不明或品質不合格。
- 型號、價格、結構證據與敘述互相矛盾。
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
- 第三輪仍衝突時必須是 `review_required`，不能出現於可上傳清單。
- Dashboard 必須顯示同一 `presentation_id` 的照片、AI 判讀、處理中卡片，右側完成卡片只能在該輪結束後顯示。
- 右側縮圖與單張判讀歷程必須依實際完成時間排序，不能只依輪號排序；服務恢復造成輪號重設時，新結果仍須顯示在最上方。
- 累計判讀數必須從持久化歷程恢復；若歷程中出現服務重啟後從 1 開始的新區段，必須把各區段判讀數累加，不能倒退或漏算。

## 不可放寬的鐵律

- 多數決不能代替證據完整性。
- 後一輪不得因為比較新就覆蓋前輪的未解衝突。
- UI 的「完成」只能表示照片已離開即時守門；`review_required` 必須明顯標示待複核，不可呈現為已定案單機。
- 上傳只讀取守門後的 ready manifest，不得從 UI 卡片、最新結果或單一輪判讀推斷可上傳。

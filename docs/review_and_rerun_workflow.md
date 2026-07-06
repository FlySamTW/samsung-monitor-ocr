# 人工審核與 8B 疑難重跑流程

## 建議跑法

1. 第一輪預設用 8B 跑全部照片，保留完整 `results.csv`；硬體不足時才改用 4B。
2. 從 `results.csv` 產生人工審核表與疑難名單。
3. P1 疑難先用同模型 + `--bottom_label_strip` 重跑，讓模型多看一張下方整條價牌帶；這比下方中段更適合處理非置中的價牌。
4. 仍失敗或超高價/高風險的照片，再改用較強模型或人工確認。
5. 人工在審核表標對錯，留下正確分類、型號、價格與備註。
6. 把人工審核資料累積成 prompt 修正、型號表補強或未來微調資料。

## Prompt 守門測試

每次修改 `samsung_ocr_prompt.txt`、型號表、FollowMe 規格表或後處理邏輯後，先跑守門測試。

快速檢查，跑前 6 張：

```powershell
.\.venv\Scripts\python.exe tools\run_qwen_vl_guard.py --quick
```

完整檢查，跑目前 52 張標準答案照片：

```powershell
.\.venv\Scripts\python.exe tools\run_qwen_vl_guard.py
```

輸出會自動放到 `runs\qwen_vl_guard_*.json`。

目前總題組：

- `tools\qwen_vl_regression_cases_202603_all.json`
- 包含 FollowMe、遠距 FollowMe、一般單機、遠景、三台並排、3000 元以下價格排除、電信方案價排除、大於 3000 價格保留、五位數價格不可誤判低價、型號可讀但 3000 元以下時只清價格不清型號、多品牌價牌不可借價、LG 可移動螢幕不可算 Samsung FollowMe、Samsung Smart Monitor M5/M7 不可誤判 LG、Smart Monitor 桌上型短支架不可誤判 FollowMe、活動立牌非 FollowMe、FollowMe 排除語句、Follow Me 4K 上牌不可誤升 Pro 43、品牌名不等於型號、Smart Monitor 不硬配 G5、Smart Monitor 無 FollowMe 支架時不可標準化成 FollowMe、型號尾碼錯讀校正、遠景不可救回零散價牌、非三星遠景排除、G5/G7 型號讀取、Odyssey Ark 不借旁邊型號。
- 守門工具第一輪預設使用 `--bottom-label-strip`，只加下方整條價牌帶，避免 16K context 爆掉；若有失敗案例，工具會自動只針對失敗案例用 `--bottom-center-zoom` 重跑一次並合併報告。
- 守門工具會先呼叫 `tools\local_llm_manager.py`，用 LM Studio CLI 確認 `qwen/qwen3-vl-8b` 或備援模型已載入。

## 新增欄位

- `review_status`: 人工審核狀態，預設 `待審核`。
- `human_is_correct`: 人工標記是否正確，建議填 `對` 或 `錯`。
- `human_category`: 若分類錯，填正確分類。
- `human_model`: 若型號錯，填正確型號。
- `human_price`: 若價格錯，填正確價格。
- `human_notes`: 可記錄錯誤原因，例如「把 LG 認成 FollowMe」或「遠景其實是三台單機」。
- `rerun_priority`: 自動判斷是否值得重跑，`P1` 優先。
- `rerun_reason`: 自動列出疑難原因。
- `rerun_recommended_model`: 目前預設建議用 `qwen/qwen3-vl-8b` 重跑。

## Dashboard 待人工校正面板

主監看畫面右上工具列的 `待人工校正` 會開啟獨立面板，資料來源是 `D:\00_商化\00_已OCR照片\_drive_upload\drive_upload_review_required.csv`。這個面板用來處理「OCR 已輸出，但不能安全上傳到 Google Drive」的照片，尤其是 2026+ 缺少 `↑/↓/✓`、無價格、無型號、或仍有 `？` 的檔名。

操作結果：

- `記錄`: 寫入 `_ocr_audit\manual_corrections.csv`，保留人工確認的視角、型號、價格、符號與備註。
- `學規則`: 同時寫入 `_ocr_audit\manual_learning_rules.csv`，供後續 prompt、後處理守衛或 repair 腳本套用。
- `ARK`: 快速填入 `S55BG970NC`，適用 Odyssey Ark / Ark Mini LED / 55 吋大型直立或曲面桌上機，但仍不可借旁邊 S27/S32 小螢幕標籤。
- `標記重跑需求`: 只記錄需要安全產生 rerun candidate，不會直接猜來源資料夾重跑。

注意：這個面板不是大量改檔工具。校正 CSV 產生後，仍需由 repair/export 腳本讀取校正資料、重建正確檔名，再重新產生 Drive manifest；只有 manifest 標為 `ready` 的照片才能上傳。

## 產生審核表與重跑名單

```powershell
.\.venv\Scripts\python.exe tools\build_rerun_candidates.py `
  --input runs\<本次批次>\results.csv `
  --image-dir "D:\00_歷年商化照片\商化照片-202603"
```

預設會輸出：

- `runs\<本次批次>\results_review.csv`
- `runs\<本次批次>\results_rerun_candidates.json`

## 疑難重跑建議

一般第一輪：

```powershell
.\.venv\Scripts\python.exe samsung_ocr_batch_processor.py `
  --model qwen/qwen3-vl-8b `
  --api_base http://127.0.0.1:1234/v1 `
  --dir "D:\00_歷年商化照片\商化照片-202603"
```

疑難重跑時加下方整條價牌帶：

```powershell
.\.venv\Scripts\python.exe samsung_ocr_batch_processor.py `
  --model qwen/qwen3-vl-8b `
  --api_base http://127.0.0.1:1234/v1 `
  --dir "D:\00_歷年商化照片\商化照片-202603" `
  --bottom_label_strip
```

若價牌確定在下方中間、但整條價牌帶仍讀不到，才加 `--bottom_center_zoom` 做更強的疑難重跑；不要第一輪大量使用兩種輔助圖，容易增加 context 與時間。

2026-06-06 實測：52 張守門題已包含活動告示不可算 FollowMe、側標不可跨商品借用、3000 元以下價格排除、五位數價格保留、S32DG802SC/OLED G8 錯讀校正等案例。4B 仍可能偶發 `no_json` 或截斷，屬模型輸出穩定性問題；正式批次預設改用 `qwen/qwen3-vl-8b`，4B 作為硬體不足時備援。正式 OCR 與守門測試皆使用 `temperature=0`，降低同一照片重跑時的隨機漂移。

## 目前疑難判斷

- 單機缺型號或缺價格。
- 遠景判斷與「一台、兩台、三台、商品標籤、價格牌、型號」等單機線索衝突。
- FollowMe 價格與型號明顯不一致。
- 價格狀態異常。
- 照片不清楚、沒有規格牌、沒有價格牌。
- 系統處理失敗。

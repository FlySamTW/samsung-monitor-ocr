# Samsung OCR 本地視覺模型盲測

## 固定集與範圍

固定集是 `samples/ocr_demo_50/labels.json` 的 50 張 portable demo set。manifest 由 `tools/model_benchmark_manifest.py` 產生，預設包含 34 張單機、16 張遠景、FollowMe Pro/FollowMe、價格存在與缺漏/不確定案例，以及遠景型號幻覺風險標記。v2 manifest 記錄 labels SHA-256、每張原圖 SHA-256 與整批 case-set SHA-256；任一 ID、圖片、tag 或 expected 被改寫都會 fail closed。預期欄位不應送進模型 prompt。

這批資料沒有獨立人工標註「宣傳牌但非 FollowMe」、它牌或純視覺模糊價牌，因此這三項不能冒充已測指標；應先補 fixture，再宣告完整覆蓋。

## 安全執行

目前主線是 `qwen/qwen3-vl-8b`。候選 API 可見不代表已載入。先執行唯讀 preflight：

```powershell
.\.venv\Scripts\python.exe tools\model_benchmark_manifest.py preflight
```

只有在候選已由操作者另行載入、且主線 OCR 已停止或有獨立 LM Studio instance 時，才可把同一批盲測輸出交給評分器。這個 benchmark 工具本身不會 load/unload、啟停 server 或切換模型。

`model_benchmark_sidecar.py` 的實際執行會用 UTF-8 JSON 列舉 Windows 專案程序；列舉失敗即拒絕 benchmark。它在取得獨占 lock 前後各確認一次 API idle 且沒有 watcher、staged/recursive runner 或 uploader，避免在檢查與切模之間發生競態。中文 `遠景` 與英文 `distant_view` 都會計入 FollowMe 危險誤判。

執行前會一次建立每個 case 的全圖與 deterministic crops，對 production prompt 及實際解碼後影像內容計算 `input_fingerprint`，並將 manifest、case set、prompt、image 指紋寫入每筆 raw row。所有候選共用同一批已準備的 evidence；舊 raw row 若缺少指紋、候選/key 不一致，或 prompt/圖片/crop/manifest 曾變動，不得續跑，必須使用新 output directory。

```powershell
.\.venv\Scripts\python.exe tools\model_benchmark_manifest.py build
.\.venv\Scripts\python.exe tools\model_benchmark_score.py docs/model_benchmark_manifest.json predictions.jsonl --model qwen/qwen3-vl-8b --out runs/model_benchmark/qwen3-vl-8b.score.json
```

## 指標定義

`fully_correct_rate` 要求 view type、model、price 三欄全對；`distant_or_followme_danger_rate` 計算遠景被判成單機/FollowMe，或 FollowMe 被判成遠景；`field_accuracy` 分別報告型號、價格與視角欄位；`mean_latency_ms` 只平均有回報 latency 的 case。缺失輸出會列入 failure，不會靜默剔除分母。

Sidecar raw schema separates `candidate_model` (the VLM under test) from `model` (the predicted Samsung product). The v2 scorer filters shared JSONL by `candidate_model`; missing, duplicate, unknown, mixed-model, parse-error, or inference-failure rows remain in the denominator and set `benchmark_gate_pass=false`. Promotion decisions must require this protocol gate before comparing accuracy or latency.

## 目前結論

在本次不中斷主線的限制下，沒有切換候選模型，也沒有偽造候選分數。既有紀錄支持 `qwen/qwen3-vl-8b` 留在 production；Qwen3.5 9B 曾有 8K context failure，Gemma 4 12B QAT 與候選模型仍需獨立載入後用此固定集實測。以準確率第一的門檻，先選能在 16K+ context 通過盲測、且遠景/FollowMe 危險誤判率最低者，再比較速度。

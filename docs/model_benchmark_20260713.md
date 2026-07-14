# Samsung OCR 本地視覺模型盲測

## 固定集與範圍

固定集是 `samples/ocr_demo_50/labels.json` 的 50 張 portable demo set。manifest 由 `tools/model_benchmark_manifest.py` 產生，預設包含 34 張單機、16 張遠景、FollowMe Pro/FollowMe、價格存在與缺漏/不確定案例，以及遠景型號幻覺風險標記。預期欄位不應送進模型 prompt。

這批資料沒有獨立人工標註「宣傳牌但非 FollowMe」、它牌或純視覺模糊價牌，因此這三項不能冒充已測指標；應先補 fixture，再宣告完整覆蓋。

## 安全執行

目前主線是 `qwen/qwen3-vl-8b`。候選 API 可見不代表已載入。先執行唯讀 preflight：

```powershell
.\.venv\Scripts\python.exe tools\model_benchmark_manifest.py preflight
```

只有在候選已由操作者另行載入、且主線 OCR 已停止或有獨立 LM Studio instance 時，才可把同一批盲測輸出交給評分器。這個 benchmark 工具本身不會 load/unload、啟停 server 或切換模型。

`model_benchmark_sidecar.py` 的實際執行會用 UTF-8 JSON 列舉 Windows 專案程序；列舉失敗即拒絕 benchmark。它在取得獨占 lock 前後各確認一次 API idle 且沒有 watcher、staged/recursive runner 或 uploader，避免在檢查與切模之間發生競態。中文 `遠景` 與英文 `distant_view` 都會計入 FollowMe 危險誤判。

```powershell
.\.venv\Scripts\python.exe tools\model_benchmark_manifest.py build
.\.venv\Scripts\python.exe tools\model_benchmark_score.py docs/model_benchmark_manifest.json predictions.jsonl --model qwen/qwen3-vl-8b --out runs/model_benchmark/qwen3-vl-8b.score.json
```

## 指標定義

`fully_correct_rate` 要求 view type、model、price 三欄全對；`distant_or_followme_danger_rate` 計算遠景被判成單機/FollowMe，或 FollowMe 被判成遠景；`field_accuracy` 分別報告型號、價格與視角欄位；`mean_latency_ms` 只平均有回報 latency 的 case。缺失輸出會列入 failure，不會靜默剔除分母。

## 目前結論

在本次不中斷主線的限制下，沒有切換候選模型，也沒有偽造候選分數。既有紀錄支持 `qwen/qwen3-vl-8b` 留在 production；Qwen3.5 9B 曾有 8K context failure，Gemma 4 12B QAT 與候選模型仍需獨立載入後用此固定集實測。以準確率第一的門檻，先選能在 16K+ context 通過盲測、且遠景/FollowMe 危險誤判率最低者，再比較速度。

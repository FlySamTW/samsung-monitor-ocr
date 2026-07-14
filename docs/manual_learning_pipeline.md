# Manual learning pipeline

本工具分成「即時辨識階段」與「本機背景優化階段」。預設以本機 LM Studio endpoint 執行；本次預設流程不會上傳照片。若使用者明確指定其他 OpenAI-compatible endpoint，工具不會以永久規則拒絕。

```powershell
python tools/build_manual_learning_dataset.py --output runs/manual_prompt_optimization/manual_learning.jsonl
```

資料集工具讀取真實欄位 `corrected_view_type/corrected_model/corrected_price/corrected_price_symbol/note`，產生結構化 target JSON；規則只取 `rule_hint`。相對 `source_path` 以 `D:\00_商化\00_未整理商化照片` 解析。`M-城市-區-TK3C-店名-流水號` 會移除流水號形成群組，相鄰流水號不跨 train/dev/holdout。

本機背景優化階段：

```powershell
python tools/optimize_prompt_from_corrections.py --data runs/manual_prompt_optimization/manual_learning.jsonl --baseline-metrics baseline.json --candidate-metrics candidate.json --prompt candidate.txt --optimizer none --endpoint http://127.0.0.1:1234/v1
```

endpoint 預設建議使用本機 LM Studio；明確指定時也可使用外部 endpoint。CLI 是 prepare/evaluate，不會假稱已最佳化；Python 的 `compile_with_dspy(...)` adapter 才會實際呼叫 GEPA/MIPRO compile。未安裝 DSPy 時請執行 `pip install dspy`。

候選 prompt 只寫入 `runs/manual_prompt_optimization`，不覆蓋 `samsung_ocr_prompt.txt`。promotion 必須讓 holdout exact-match 嚴格改善，且遠景、FollowMe、型號幻覺皆不得退步；holdout 不參與優化。

離線測試：

```powershell
\.venv\Scripts\python.exe -m unittest -v tools/test_manual_learning_pipeline.py
```

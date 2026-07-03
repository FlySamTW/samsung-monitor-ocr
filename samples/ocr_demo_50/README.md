# Samsung OCR Demo 50

This folder is the portable sample set for another PC or another AI agent.

- `photos/` contains exactly 50 resized demo photos copied from reviewed flat outputs.
- `labels.json` contains the expected OCR/rename target for each sample.
- This is intentionally small; do not commit the full production photo folders.

Suggested smoke run:

```powershell
python samsung_ocr_batch_processor.py --api_base http://127.0.0.1:1234/v1 --api_key lm-studio --model qwen/qwen3-vl-8b --dir samples\ocr_demo_50\photos --no_followme_auto_update
```

For filename/export checks, compare generated results with `labels.json`.

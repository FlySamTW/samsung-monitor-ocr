@echo off
rem Copy this file to user_settings.cmd if you want to change defaults.
rem Do not use quotes around the values below.

rem Main photo source and flat OCR output folders.
rem Keep the default demo paths for a smoke test.
rem For production, replace these two values with your real folders.
set OCR_SOURCE_ROOT=samples\ocr_demo_50\photos
set OCR_OUTPUT_DIR=_ocr_output

rem LM Studio OpenAI-compatible local server.
set LOCAL_LLM_API_BASE=http://127.0.0.1:1234/v1
set LOCAL_LLM_MODEL=qwen/qwen3-vl-8b
set LOCAL_LLM_MODEL_KEY=qwen/qwen3-vl-8b

rem Safer default for mid-range GPUs such as RTX 3060.
set LOCAL_LLM_CONTEXT_LENGTH=16384
set LOCAL_LLM_GPU=max
set LOCAL_LLM_PARALLEL=1

rem Optional fallback model. Leave as-is unless this model is installed in LM Studio.
set LOCAL_LLM_FALLBACK_MODEL=qwen/qwen3-vl-4b
set LOCAL_LLM_FALLBACK_MODEL_KEY=qwen/qwen3-vl-4b

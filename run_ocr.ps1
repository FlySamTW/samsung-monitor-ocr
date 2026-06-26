# Samsung OCR Launcher - PowerShell 版 (支援 OpenCode Go)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "   Samsung OCR Launcher v19.10 (Vision)  " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# 檢查 Python
try { python --version 2>&1 | Out-Null } catch {
    Write-Host "[ERROR] Python 未安裝" -ForegroundColor Red
    Read-Host "按 Enter 結束"
    exit 1
}

# 安裝必要套件
python -c "import flask,flask_cors,rich,openai,psutil,requests" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing packages..." -ForegroundColor Yellow
    python -m pip install flask flask-cors rich openai psutil requests --quiet
}

# === 選單 ===
Write-Host ""
Write-Host "--- 本機引擎 ---" -ForegroundColor Yellow
Write-Host "  [1] 本機 LM Studio: Qwen3-VL 8B (預設)"
Write-Host "  [2] 本機 LM Studio: Qwen3-VL 4B"
Write-Host "--- OpenCode Go Vision ---" -ForegroundColor Yellow
Write-Host "  [3] OpenCode Go: mimo-v2.5"
Write-Host "  [4] OpenCode Go: mimo-v2-omni"
Write-Host "  [5] OpenCode Go: qwen3.7-max"
Write-Host "  [6] OpenCode Go: qwen3.7-plus"
Write-Host "  [7] 自訂"
Write-Host ""

$choice = Read-Host "請選擇 (預設=1)"
if (-not $choice) { $choice = "1" }

$apiBase = "http://127.0.0.1:1234/v1"
$apiKey = "lm-studio"
$model = "qwen3vl8b-ocr"
$needLocalLLM = $true

switch ($choice) {
    "1" { $model = "qwen3vl8b-ocr" }
    "2" { $model = "qwen3vl4b-ocr" }
    "3" { 
        $apiBase = "https://opencode.ai/zen/go/v1"
        $model = "mimo-v2.5"
        $needLocalLLM = $false
    }
    "4" {
        $apiBase = "https://opencode.ai/zen/go/v1"
        $model = "mimo-v2-omni"
        $needLocalLLM = $false
    }
    "5" {
        $apiBase = "https://opencode.ai/zen/go/v1"
        $model = "qwen3.7-max"
        $needLocalLLM = $false
    }
    "6" {
        $apiBase = "https://opencode.ai/zen/go/v1"
        $model = "qwen3.7-plus"
        $needLocalLLM = $false
    }
    "7" {
        $apiBase = Read-Host "API Base URL"
        $apiKey = Read-Host "API Key"
        $model = Read-Host "Model"
        $needLocalLLM = $false
    }
}

# API Key for OpenCode Go
if (-not $needLocalLLM -and $apiKey -eq "lm-studio") {
    $apiKey = $env:OPENCODE_GO_API_KEY
    if (-not $apiKey) {
        Write-Host "[ERROR] 請設定 OPENCODE_GO_API_KEY 環境變數" -ForegroundColor Red
        Write-Host "  setx OPENCODE_GO_API_KEY `"sk-xxxx`"" -ForegroundColor Yellow
        Read-Host "按 Enter 結束"
        exit 1
    }
}

Write-Host ""
Write-Host "API Base: $apiBase" -ForegroundColor Green
Write-Host "Model:    $model" -ForegroundColor Green
Write-Host ""

# 清理舊程序
Write-Host "[1/4] 清理舊程序..." -ForegroundColor Gray
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Get-Process node -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

# 清 __pycache__
Get-ChildItem -Path $ScriptDir -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# 釋放 port 5000
$port5000 = Get-NetTCPConnection -LocalPort 5000 -ErrorAction SilentlyContinue
if ($port5000) { 
    $port5000.OwningProcess | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
}

# 啟動本機 LLM (如果需要的話)
if ($needLocalLLM) {
    Write-Host "[2/4] 啟動本機 LM Studio..." -ForegroundColor Gray
    python tools\local_llm_manager.py ensure
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] LM Studio 啟動失敗" -ForegroundColor Red
        Read-Host
        exit 1
    }
} else {
    Write-Host "[2/4] 使用 OpenCode Go 雲端模型" -ForegroundColor Gray
}

# 啟動 OCR 後端
Write-Host "[3/4] 啟動 OCR 後端..." -ForegroundColor Gray
$env:LOCAL_LLM_API_BASE = $apiBase
$env:LOCAL_LLM_MODEL = $model
$env:LOCAL_LLM_API_KEY = $apiKey

# 清除 Python 快取
python -c "import shutil,os; [shutil.rmtree(r,True) for r,d,f in os.walk('.') if '__pycache__' in r]" 2>$null

Write-Host "[4/4] 啟動 Flask 伺服器 (http://localhost:5000)" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Cyan
python samsung_ocr_batch_processor.py --api_base "$apiBase" --api_key "$apiKey" --model "$model" --dir "商化照片-202602"

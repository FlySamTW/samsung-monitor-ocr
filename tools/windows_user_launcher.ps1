param(
    [ValidateSet("setup", "start", "recursive", "status")]
    [string]$Action = "start"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:RICH_NO_COLOR = "1"
$env:TERM = "dumb"

function Write-Title($Text) {
    Write-Host ""
    Write-Host "============================================================"
    Write-Host " $Text"
    Write-Host "============================================================"
}

function Write-Step($Text) {
    Write-Host "[OK] $Text"
}

function Write-Info($Text) {
    Write-Host "[..] $Text"
}

function Write-Warn($Text) {
    Write-Host "[!!] $Text" -ForegroundColor Yellow
}

function Fail($Text) {
    Write-Host ""
    Write-Host "[ERROR] $Text" -ForegroundColor Red
    Write-Host ""
    exit 1
}

function Import-UserSettings {
    $settings = Join-Path $RepoRoot "user_settings.cmd"
    $example = Join-Path $RepoRoot "user_settings.example.cmd"

    if (-not (Test-Path $settings) -and (Test-Path $example)) {
        Copy-Item $example $settings
        Write-Step "Created user_settings.cmd from the example file."
    }

    if (-not (Test-Path $settings)) {
        return
    }

    foreach ($line in Get-Content $settings -Encoding UTF8) {
        $text = $line.Trim()
        if (-not $text -or $text.StartsWith("rem") -or $text.StartsWith("@")) {
            continue
        }
        if ($text -match '^set\s+"?([^=]+)=([^"]*)"?$') {
            [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), "Process")
        } elseif ($text -match '^([^=\s]+)=(.*)$') {
            [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), "Process")
        }
    }
}

function Get-Setting($Name, $Default) {
    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $Default
    }
    return $value
}

function Get-SourceRoot {
    $sample = Join-Path $RepoRoot "samples\ocr_demo_50\photos"
    $configured = Get-Setting "OCR_SOURCE_ROOT" $sample
    if (Test-Path $configured) {
        return (Resolve-Path $configured).Path
    }
    if (Test-Path $sample) {
        Write-Warn "OCR_SOURCE_ROOT was not found. Using the demo photos instead."
        return (Resolve-Path $sample).Path
    }
    Fail "Photo source folder not found. Edit user_settings.cmd and set OCR_SOURCE_ROOT."
}

function Get-OutputDir {
    $configured = Get-Setting "OCR_OUTPUT_DIR" (Join-Path $RepoRoot "_ocr_output")
    return $configured
}

function Get-ArrayTail([string[]]$Items) {
    if (-not $Items -or $Items.Length -le 1) {
        return @()
    }
    return @($Items[1..($Items.Length - 1)])
}

function Invoke-Checked {
    param(
        [string]$File,
        [string[]]$ProcArgs,
        [string]$ErrorMessage
    )
    $display = "$File " + ($ProcArgs -join " ")
    Write-Info $display
    & $File @ProcArgs
    if ($LASTEXITCODE -ne 0) {
        Fail $ErrorMessage
    }
}

function Try-PythonCommand([string[]]$Command) {
    try {
        $probe = @($Command + @("-c", "import sys; print(sys.executable)"))
        $output = & $probe[0] @(Get-ArrayTail $probe) 2>$null
        if ($LASTEXITCODE -eq 0 -and $output) {
            return $true
        }
    } catch {}
    return $false
}

function Get-SystemPython {
    $candidates = @(
        @("py", "-3.11"),
        @("py", "-3"),
        @("python"),
        @("python3")
    )

    foreach ($candidate in $candidates) {
        if (Try-PythonCommand $candidate) {
            return $candidate
        }
    }
    return $null
}

function Ensure-PythonAvailable {
    $pythonCommand = Get-SystemPython
    if ($pythonCommand) {
        return $pythonCommand
    }

    Write-Warn "Python 3.11+ was not found."
    if ($Action -eq "setup" -and (Get-Command winget -ErrorAction SilentlyContinue)) {
        $answer = Read-Host "Install Python 3.11 with winget now? Type Y to install"
        if ($answer -match '^(Y|y)$') {
            & winget install -e --id Python.Python.3.11
            $pythonCommand = Get-SystemPython
            if ($pythonCommand) {
                return $pythonCommand
            }
        }
    }

    Fail "Please install Python 3.11 from https://www.python.org/downloads/ and run SETUP_FIRST_TIME.bat again."
}

function Get-VenvPython {
    $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return $venvPython
    }
    return $null
}

function Ensure-Venv {
    $venvPython = Get-VenvPython
    if ($venvPython) {
        return $venvPython
    }

    $pythonCommand = Ensure-PythonAvailable
    Write-Info "Creating local Python environment (.venv)."
    & $pythonCommand[0] @((Get-ArrayTail $pythonCommand) + @("-m", "venv", ".venv"))
    if ($LASTEXITCODE -ne 0) {
        Fail "Could not create .venv."
    }

    $venvPython = Get-VenvPython
    if (-not $venvPython) {
        Fail ".venv was created but python.exe was not found."
    }
    return $venvPython
}

function Ensure-Requirements($Python) {
    $requirements = Join-Path $RepoRoot "requirements.txt"
    if (-not (Test-Path $requirements)) {
        Fail "requirements.txt is missing."
    }

    $stamp = Join-Path $RepoRoot ".venv\requirements.installed"
    $needsInstall = $true
    if ((Test-Path $stamp) -and ((Get-Item $stamp).LastWriteTime -gt (Get-Item $requirements).LastWriteTime)) {
        $needsInstall = $false
    }

    if (-not $needsInstall) {
        Write-Step "Python packages are already installed."
        return
    }

    Invoke-Checked -File $Python -ProcArgs @("-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel") -ErrorMessage "Could not upgrade pip."
    Invoke-Checked -File $Python -ProcArgs @("-m", "pip", "install", "-r", $requirements) -ErrorMessage "Could not install Python packages."
    Set-Content -Path $stamp -Value (Get-Date).ToString("s") -Encoding ASCII
    Write-Step "Python packages installed."
}

function Ensure-Dashboard {
    $distIndex = Join-Path $RepoRoot "dashboard\dist\index.html"
    $dashboardRoot = Join-Path $RepoRoot "dashboard"
    $sourcePaths = @(
        (Join-Path $dashboardRoot "src"),
        (Join-Path $dashboardRoot "public"),
        (Join-Path $dashboardRoot "package.json"),
        (Join-Path $dashboardRoot "vite.config.js")
    )
    $newestSource = Get-ChildItem -LiteralPath $sourcePaths -Recurse -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    $needsBuild = -not (Test-Path -LiteralPath $distIndex)
    if (-not $needsBuild -and $newestSource) {
        $needsBuild = $newestSource.LastWriteTimeUtc -gt (Get-Item -LiteralPath $distIndex).LastWriteTimeUtc
    }
    if (-not $needsBuild) {
        Write-Step "Dashboard build is present and current."
        return
    }

    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
        Fail "dashboard/dist is missing or stale and Node.js was not found. Install Node.js 20+, or pull a current dashboard build."
    }

    Push-Location $dashboardRoot
    try {
        if (-not (Test-Path "node_modules")) {
            Invoke-Checked -File "npm.cmd" -ProcArgs @("install") -ErrorMessage "Could not install dashboard packages."
        }
        Invoke-Checked -File "npm.cmd" -ProcArgs @("run", "build") -ErrorMessage "Could not build dashboard."
    } finally {
        Pop-Location
    }
    Write-Step "Dashboard built."
}

function Get-BackendStatus {
    try {
        return Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/status" -TimeoutSec 3
    } catch {
        return $null
    }
}

function Test-ModelApi($ApiBase, $Model) {
    try {
        $url = $ApiBase.TrimEnd("/") + "/models"
        $data = Invoke-RestMethod -Uri $url -TimeoutSec 5
        $ids = @($data.data | ForEach-Object { $_.id })
        if ($ids -contains $Model) {
            return $true
        }
        if ($ids.Count -gt 0) {
            Write-Warn "LM Studio is running, but $Model is not loaded. Loaded: $($ids -join ', ')"
        }
    } catch {}
    return $false
}

function Ensure-LmStudio($Python) {
    $apiBase = Get-Setting "LOCAL_LLM_API_BASE" "http://127.0.0.1:1234/v1"
    $model = Get-Setting "LOCAL_LLM_MODEL" "qwen/qwen3-vl-8b"

    if (Test-ModelApi $apiBase $model) {
        Write-Step "LM Studio API is ready with model $model."
        return
    }

    Write-Info "Trying to start/load the LM Studio model with the lms CLI."
    & $Python "tools\local_llm_manager.py" ensure
    if ($LASTEXITCODE -eq 0 -and (Test-ModelApi $apiBase $model)) {
        Write-Step "LM Studio model is ready."
        return
    }

    Write-Host ""
    Write-Warn "LM Studio is not ready."
    Write-Host "Open LM Studio, enable the local server, and load this model:"
    Write-Host "  $model"
    Write-Host "Then run START_OCR.bat again."
    exit 1
}

function Ensure-Environment {
    Import-UserSettings
    $python = Ensure-Venv
    Ensure-Requirements $python
    Ensure-Dashboard
    return $python
}

function Start-Backend {
    $python = Ensure-Environment
    Ensure-LmStudio $python

    $existing = Get-BackendStatus
    if ($existing) {
        Write-Step "OCR dashboard is already running."
        Start-Process "http://127.0.0.1:5000/"
        return
    }

    $sourceRoot = Get-SourceRoot
    $apiBase = Get-Setting "LOCAL_LLM_API_BASE" "http://127.0.0.1:1234/v1"
    $model = Get-Setting "LOCAL_LLM_MODEL" "qwen/qwen3-vl-8b"
    $logDir = Join-Path $RepoRoot "logs"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $outLog = Join-Path $logDir "backend.out.log"
    $errLog = Join-Path $logDir "backend.err.log"

    $env:SAMSUNG_OCR_NO_BROWSER = "1"
    $args = @(
        "samsung_ocr_batch_processor.py",
        "--api_base", $apiBase,
        "--api_key", "lm-studio",
        "--model", $model,
        "--dir", $sourceRoot,
        "--no_followme_auto_update"
    )

    Write-Info "Starting OCR backend..."
    Start-Process -FilePath $python -ArgumentList $args -WorkingDirectory $RepoRoot -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog | Out-Null

    for ($i = 0; $i -lt 45; $i++) {
        Start-Sleep -Seconds 1
        if (Get-BackendStatus) {
            Write-Step "OCR dashboard is ready: http://127.0.0.1:5000/"
            Start-Process "http://127.0.0.1:5000/"
            return
        }
    }

    Write-Host "Backend log: $outLog"
    Write-Host "Error log:   $errLog"
    Fail "OCR backend did not start within 45 seconds."
}

function Start-Recursive {
    $python = Ensure-Environment
    Ensure-LmStudio $python

    $sourceRoot = Get-SourceRoot
    $outputDir = Get-OutputDir
    $apiBase = Get-Setting "LOCAL_LLM_API_BASE" "http://127.0.0.1:1234/v1"
    $model = Get-Setting "LOCAL_LLM_MODEL" "qwen/qwen3-vl-8b"

    Invoke-Checked -File $python -ProcArgs @("tools\validate_recursive_ocr_inputs.py", "--source-root", $sourceRoot, "--output-dir", $outputDir) -ErrorMessage "Source/output folder validation failed."

    if (-not (Get-BackendStatus)) {
        Start-Backend
    }

    Write-Title "Full auto OCR"
    Write-Host "Source: $sourceRoot"
    Write-Host "Output: $outputDir"
    Write-Host "Model:  $model"
    Write-Host ""

    & $python "tools\recursive_ocr_flat_export.py" `
        "--source-root" $sourceRoot `
        "--output-dir" $outputDir `
        "--backend-url" "http://127.0.0.1:5000" `
        "--api-base" $apiBase `
        "--api-key" "lm-studio" `
        "--model" $model
    if ($LASTEXITCODE -ne 0) {
        Fail "Full auto OCR stopped before all folders were safely exported. Check the _ocr_audit folder."
    }

    & $python "tools\recursive_ocr_audit_report.py" "--output-dir" $outputDir
    Write-Step "Full auto OCR finished."
}

function Show-Status {
    Import-UserSettings
    $status = Get-BackendStatus
    if ($status) {
        Write-Title "OCR backend status"
        Write-Host "Running: $($status.is_running)"
        Write-Host "Folder:  $($status.current_relative_dir)"
        Write-Host "File:    $($status.current_file)"
        Write-Host "Stats:   $($status.stats.processed)/$($status.stats.total) success=$($status.stats.success) failed=$($status.stats.failed)"
        Write-Host "Model:   $($status.current_model)"
    } else {
        Write-Warn "OCR backend is not running."
    }

    Write-Title "Local runner processes"
    $procs = Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -match "samsung_ocr_batch_processor.py|recursive_ocr_flat_export.py|rerun_questionable_records.py"
    } | Select-Object ProcessId, CommandLine
    if ($procs) {
        $procs | Format-Table -AutoSize
    } else {
        Write-Host "No OCR runner process found."
    }
}

Write-Title "Samsung Monitor OCR"

switch ($Action) {
    "setup" {
        $python = Ensure-Environment
        Write-Step "Setup complete. Python: $python"
        Write-Host "Next: open LM Studio, load qwen/qwen3-vl-8b, then run START_OCR.bat."
    }
    "start" {
        Start-Backend
    }
    "recursive" {
        Start-Recursive
    }
    "status" {
        Show-Status
    }
}

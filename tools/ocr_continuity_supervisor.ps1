param(
    [string]$RepoRoot,
    [string]$SourceRoot,
    [string]$OutputDir,
    [string]$BackendUrl = "http://127.0.0.1:5000",
    [string]$ApiBase = "http://127.0.0.1:1234/v1",
    [string]$Model = "qwen/qwen3-vl-8b",
    [int]$ContextLength = 32768
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$audit = Join-Path $OutputDir "_ocr_audit"
$BenchmarkLockPath = Join-Path $audit "model_benchmark.lock"
$logDir = Join-Path $RepoRoot "logs"
$lockPath = Join-Path $audit "ocr_continuity_supervisor.lock"
$alertPath = Join-Path $audit "ocr_continuity_supervisor_alert.json"
$logPath = Join-Path $logDir ("ocr_continuity_supervisor_{0}.jsonl" -f (Get-Date -Format "yyyyMMdd"))
$python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
New-Item -ItemType Directory -Force -Path $audit,$logDir | Out-Null

function Log-Event([string]$Event, [hashtable]$Data = @{}) {
    $payload = [ordered]@{ timestamp=(Get-Date).ToString("o"); event=$Event; pid=$PID }
    foreach ($k in $Data.Keys) { $payload[$k] = $Data[$k] }
    $payload | ConvertTo-Json -Compress | Add-Content -LiteralPath $logPath -Encoding UTF8
}
function Alert([string]$Reason, [hashtable]$Data = @{}) {
    $payload = [ordered]@{ timestamp=(Get-Date).ToString("o"); status="fail_closed"; reason=$Reason; repo_root=$RepoRoot }
    foreach ($k in $Data.Keys) { $payload[$k] = $Data[$k] }
    $payload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $alertPath -Encoding UTF8
    Log-Event "alert" (@{reason=$Reason} + $Data)
}
function Owned([string]$Pattern) {
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and $_.CommandLine -match $Pattern -and
        $_.CommandLine -match [regex]::Escape($RepoRoot) -and
        $_.CommandLine -notmatch "Get-CimInstance|ocr_continuity_supervisor"
    })
}
function Get-BackendStatus {
    try { return Invoke-RestMethod -Uri "$BackendUrl/api/status" -TimeoutSec 8 } catch { return $null }
}
function Get-LmModels {
    try { return Invoke-RestMethod -Uri ($ApiBase.TrimEnd('/') + "/models") -TimeoutSec 5 } catch { return $null }
}
function Invoke-Lms([string[]]$Args) {
    $lms = Join-Path $env:USERPROFILE ".lmstudio\bin\lms.exe"
    if (-not (Test-Path $lms)) { return $null }
    $out = & $lms @Args 2>&1
    return [pscustomobject]@{ exit=$LASTEXITCODE; output=($out -join "`n") }
}
function Start-Hidden([string]$File, [string[]]$Args, [string]$OutFile, [string]$ErrFile) {
    Start-Process -FilePath $File -ArgumentList $Args -WorkingDirectory $RepoRoot -WindowStyle Hidden `
        -RedirectStandardOutput $OutFile -RedirectStandardError $ErrFile | Out-Null
}

try {
    try {
        $null = New-Item -ItemType File -Path $lockPath -ErrorAction Stop
        Set-Content -LiteralPath $lockPath -Value (([ordered]@{pid=$PID;started_at=(Get-Date).ToString("o");repo=$RepoRoot})|ConvertTo-Json) -Encoding UTF8
    } catch { Log-Event "duplicate_or_locked"; exit 0 }

    $status = Get-BackendStatus
    if (Test-Path -LiteralPath $BenchmarkLockPath) {
        try {
            $planned = Get-Content -LiteralPath $BenchmarkLockPath -Raw | ConvertFrom-Json
            if ($planned.purpose -eq "backend_upgrade_v1945") {
                Log-Event "planned_backend_upgrade_interlock" @{ lock=$BenchmarkLockPath; owner=$planned.pid }
                exit 0
            }
        } catch {
            Log-Event "benchmark_lock_unreadable" @{ lock=$BenchmarkLockPath }
            exit 0
        }
    }
    $backend = @(Owned "samsung_ocr_batch_processor\.py")
    $watcher = @(Owned "auto_rerun_questionable_after_recursive\.ps1")
    $staged = @(Owned "rerun_staged_candidates\.py")
    $recursive = @(Owned "recursive_ocr_flat_export\.py")
    $uploader = @(Owned "rclone_drive_upload\.py|rclone\.exe")

    if ($status -and [bool]$status.is_running) {
        Log-Event "healthy_noop" @{backend=$backend.Count;watcher=$watcher.Count;staged=$staged.Count;recursive=$recursive.Count;uploader=$uploader.Count;folder=$status.current_relative_dir;file=$status.current_file}
        exit 0
    }

    if ($backend.Count -gt 0 -and -not $status) {
        Alert "backend_process_exists_but_api_unhealthy" @{backend_pids=@($backend.ProcessId)}
        exit 3
    }

    $lm = Get-LmModels
    if (-not $lm) {
        Log-Event "lm_server_recovery_attempt"
        $r = Invoke-Lms @("server","start","--bind","127.0.0.1")
        Start-Sleep -Seconds 5
        $lm = Get-LmModels
        if (-not $lm) { Alert "lm_server_unavailable"; exit 4 }
    }
    $loaded = @($lm.data | Where-Object { $_.loaded -eq $true -or $_.status -eq "loaded" })
    if ($loaded.Count -gt 0 -and @($loaded | Where-Object { $_.id -ne $Model -and $_.modelKey -ne $Model }).Count -gt 0) {
        Alert "different_model_already_loaded" @{loaded=$loaded}
        exit 5
    }
    if ($loaded.Count -eq 0) {
        $inventory = Invoke-Lms @("ls","--json")
        if (-not $inventory -or $inventory.output -notmatch [regex]::Escape($Model)) {
            Alert "required_local_model_unavailable" @{model=$Model}
            exit 6
        }
        $load = Invoke-Lms @("load",$Model,"--context-length",$ContextLength,"--yes")
        if (-not $load -or $load.exit -ne 0) { Alert "qwen_load_failed" @{model=$Model;context=$ContextLength}; exit 7 }
    }

    if (-not $status -and $backend.Count -eq 0) {
        $stamp=Get-Date -Format "yyyyMMdd_HHmmss"
        Start-Hidden $python @("samsung_ocr_batch_processor.py","--api_base",$ApiBase,"--api_key","lm-studio","--model",$Model,"--dir",$SourceRoot) (Join-Path $logDir "supervisor_backend_$stamp.out.log") (Join-Path $logDir "supervisor_backend_$stamp.err.log")
        Log-Event "backend_started" @{model=$Model}
    }

    if ($staged.Count -gt 0 -or $recursive.Count -gt 0) {
        Alert "staged_or_recursive_state_ambiguous" @{staged=$staged.Count;recursive=$recursive.Count}
        exit 8
    }
    if ($watcher.Count -eq 0) {
        $stamp=Get-Date -Format "yyyyMMdd_HHmmss"
        Start-Hidden "powershell.exe" @("-NoProfile","-ExecutionPolicy","Bypass","-File","tools\auto_rerun_questionable_after_recursive.ps1","-RepoRoot",$RepoRoot,"-SourceRoot",$SourceRoot,"-OutputDir",$OutputDir,"-BackendUrl",$BackendUrl,"-PollSeconds","300","-PrimaryPasses","2","-CurrentYearOnly") (Join-Path $logDir "supervisor_watcher_$stamp.out.log") (Join-Path $logDir "supervisor_watcher_$stamp.err.log")
        Log-Event "current_year_watcher_started"
    }
    $pending = Join-Path $OutputDir "_drive_upload\drive_upload_ready_pending.csv"
    if ((Test-Path $pending) -and @(Import-Csv $pending).Count -gt 0 -and $uploader.Count -eq 0) {
        $stamp=Get-Date -Format "yyyyMMdd_HHmmss"
        Start-Hidden $python @("tools\rclone_drive_upload.py","--output-dir",$OutputDir,"--execute","--repeat","--limit","100","--transfers","4","--checkers","8","--rclone-timeout-seconds","1200") (Join-Path $logDir "supervisor_uploader_$stamp.out.log") (Join-Path $logDir "supervisor_uploader_$stamp.err.log")
        Log-Event "ready_uploader_started"
    }
} finally {
    Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
}

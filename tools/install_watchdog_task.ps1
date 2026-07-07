param(
    [string]$RepoRoot,
    [string]$SourceRoot,
    [string]$OutputDir,
    [string]$TaskName = "SamsungOCR_PipelineWatchdog",
    [int]$IntervalHours = 4,
    [int]$StartDelayMinutes = 5
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
}
if (-not $SourceRoot) {
    throw "SourceRoot is required"
}
if (-not $OutputDir) {
    throw "OutputDir is required"
}

$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$Watchdog = Join-Path $RepoRoot "tools\ocr_upload_watchdog.ps1"
if (-not (Test-Path -LiteralPath $Watchdog)) {
    throw "Watchdog script not found: $Watchdog"
}

$taskArgs = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", ('"{0}"' -f $Watchdog),
    "-RepoRoot", ('"{0}"' -f $RepoRoot),
    "-SourceRoot", ('"{0}"' -f $SourceRoot),
    "-OutputDir", ('"{0}"' -f $OutputDir)
) -join " "

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $taskArgs -WorkingDirectory $RepoRoot
$start = (Get-Date).AddMinutes([math]::Max(1, $StartDelayMinutes))
$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At $start `
    -RepetitionInterval (New-TimeSpan -Hours $IntervalHours) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Keeps Samsung OCR recursive runner, questionable rerun watcher, and rclone upload moving safely." `
    -Force | Out-Null

$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Output "Installed task: $TaskName"
Write-Output "Next run: $($info.NextRunTime)"
Write-Output "Interval hours: $IntervalHours"

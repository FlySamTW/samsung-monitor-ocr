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
$Watchdog = Join-Path $RepoRoot "tools\ocr_continuity_supervisor.ps1"
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
$triggers = @(
    (New-ScheduledTaskTrigger -AtStartup),
    (New-ScheduledTaskTrigger -AtLogOn),
    (New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650))
)
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $triggers `
    -Settings $settings `
    -Description "Keeps Samsung OCR recursive runner, questionable rerun watcher, and rclone upload moving safely." `
    -Force | Out-Null

$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Output "Installed task: $TaskName"
Write-Output "Next run: $($info.NextRunTime)"
Write-Output "Interval hours: $IntervalHours"

$obsolete = Get-ScheduledTask -TaskName "SamsungOCR_ResumeBatch" -ErrorAction SilentlyContinue
if ($obsolete) {
    $obsoleteInfo = Get-ScheduledTaskInfo -TaskName "SamsungOCR_ResumeBatch"
    if (-not $obsoleteInfo.NextRunTime -or $obsoleteInfo.NextRunTime -lt (Get-Date)) {
        Disable-ScheduledTask -TaskName "SamsungOCR_ResumeBatch" | Out-Null
        Write-Output "Disabled obsolete one-shot task: SamsungOCR_ResumeBatch"
    } else {
        Write-Output "Kept obsolete task because it has a future trigger: SamsungOCR_ResumeBatch"
    }
}

param(
    [ValidateSet("run", "status")][string]$Action = "run",
    [string]$RepoRoot,
    [string]$SourceRoot,
    [string]$OutputDir,
    [string]$BackendUrl = "http://127.0.0.1:5002",
    [int]$IntervalSeconds = 60,
    [int]$ChildTimeoutSeconds = 240
)
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$audit = Join-Path $OutputDir "_ocr_audit"
$logDir = Join-Path $RepoRoot "logs"
$lock = Join-Path $audit "ocr_continuity_daemon.lock"
$shutdown = Join-Path $audit "ocr_continuity_daemon_shutdown.json"
$log = Join-Path $logDir ("ocr_continuity_daemon_{0}.jsonl" -f (Get-Date -Format yyyyMMdd))
$supervisor = Join-Path $RepoRoot "tools\ocr_continuity_supervisor.ps1"
New-Item -ItemType Directory -Force -Path $audit,$logDir | Out-Null
function Event([string]$Name, [hashtable]$Data=@{}) {
    $p=[ordered]@{timestamp=(Get-Date).ToString("o");event=$Name;pid=$PID}
    foreach($k in $Data.Keys){$p[$k]=$Data[$k]}
    $p|ConvertTo-Json -Compress|Add-Content -LiteralPath $log -Encoding UTF8
}
function OwnedDaemon { @(Get-CimInstance Win32_Process|Where-Object{$_.CommandLine -and $_.CommandLine -match 'ocr_continuity_daemon\.ps1' -and $_.CommandLine -match [regex]::Escape($RepoRoot) -and $_.ProcessId -ne $PID}) }
if($Action -eq "status") { [pscustomobject]@{running=(OwnedDaemon).Count -gt 0;lock=(Test-Path $lock);shutdown=(Test-Path $shutdown);log=$log}|ConvertTo-Json; exit 0 }
New-Item -ItemType Directory -Force -Path (Split-Path $lock -Parent) | Out-Null
try { $null=New-Item -ItemType File -Path $lock -ErrorAction Stop; Set-Content $lock (([ordered]@{pid=$PID;started_at=(Get-Date).ToString("o")}|ConvertTo-Json)) -Encoding UTF8 } catch { Event "duplicate_exit"; exit 0 }
try {
    Event "started" @{interval_seconds=$IntervalSeconds;child_timeout_seconds=$ChildTimeoutSeconds}
    while($true) {
        $stamp=Get-Date -Format yyyyMMdd_HHmmss
        $out=Join-Path $logDir "daemon_supervisor_$stamp.out.log"; $err=Join-Path $logDir "daemon_supervisor_$stamp.err.log"
        $args=@('-NoProfile','-ExecutionPolicy','Bypass','-File',$supervisor,'-RepoRoot',$RepoRoot,'-SourceRoot',$SourceRoot,'-OutputDir',$OutputDir,'-BackendUrl',$BackendUrl)
        $child=Start-Process powershell.exe -ArgumentList $args -WorkingDirectory $RepoRoot -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
        $child.WaitForExit($ChildTimeoutSeconds*1000)
        if(-not $child.HasExited){Event "child_timeout" @{child_pid=$child.Id}; Stop-Process -Id $child.Id -Force -ErrorAction SilentlyContinue} else {Event "supervisor_complete" @{child_pid=$child.Id;exit_code=$child.ExitCode}}
        Start-Sleep -Seconds ([math]::Max(60,$IntervalSeconds))
    }
} finally {
    [ordered]@{timestamp=(Get-Date).ToString("o");pid=$PID;reason="shutdown"}|ConvertTo-Json|Set-Content $shutdown -Encoding UTF8
    Remove-Item $lock -Force -ErrorAction SilentlyContinue
}

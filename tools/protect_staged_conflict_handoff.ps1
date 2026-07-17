param(
    [Parameter(Mandatory = $true)][int]$RunnerProcessId,
    [Parameter(Mandatory = $true)][string]$AuditFolder,
    [Parameter(Mandatory = $true)][string]$BackupFolder,
    [Parameter(Mandatory = $true)][string]$CandidateCsv,
    [Parameter(Mandatory = $true)][string]$RepoRoot,
    [Parameter(Mandatory = $true)][string]$SourceRoot,
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [string]$BackendUrl = "http://127.0.0.1:5002",
    [int]$PollSeconds = 3
)

$ErrorActionPreference = "Stop"
$OutputDir = [IO.Path]::GetFullPath($OutputDir)
$AuditFolder = [IO.Path]::GetFullPath($AuditFolder)
$BackupFolder = [IO.Path]::GetFullPath($BackupFolder)
$outputPrefix = $OutputDir.TrimEnd('\') + '\'
$logPath = Join-Path $BackupFolder "handoff_restore.log"

function Write-HandoffLog([string]$Message) {
    $line = "{0} {1}" -f (Get-Date -Format "s"), $Message
    Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
}

function Assert-WithinOutput([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    if (-not $full.StartsWith($outputPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing path outside output root: $full"
    }
    return $full
}

Write-HandoffLog "waiting for legacy staged runner pid=$RunnerProcessId"
while (Get-Process -Id $RunnerProcessId -ErrorAction SilentlyContinue) {
    Start-Sleep -Seconds $PollSeconds
}
Write-HandoffLog "legacy staged runner exited"

# The runner exits only after the backend batch has completed, but verify the
# operator-facing backend is idle before touching deliverables.
for ($attempt = 1; $attempt -le 120; $attempt++) {
    try {
        $status = Invoke-RestMethod -Uri ($BackendUrl.TrimEnd('/') + '/api/status') -TimeoutSec 10
        if (-not [bool]$status.is_running) { break }
    } catch {
        Write-HandoffLog "status check failed attempt=$attempt error=$($_.Exception.Message)"
    }
    if ($attempt -eq 120) { throw "Backend did not become idle before restore" }
    Start-Sleep -Seconds $PollSeconds
}

$candidateNames = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
Import-Csv -LiteralPath $CandidateCsv |
    Where-Object { $_.period -eq '202604' } |
    ForEach-Object { [void]$candidateNames.Add($_.file_name) }

$manifestPath = Join-Path $BackupFolder 'flat_output_manifest.csv'
$manifestRows = Import-Csv -LiteralPath $manifestPath
$oldTargetByName = @{}
foreach ($row in $manifestRows) {
    $oldTargetByName[$row.original_name] = [IO.Path]::GetFullPath($row.target_path)
}

$currentCopiedPath = Join-Path $AuditFolder 'copied.csv'
$currentCopied = if (Test-Path -LiteralPath $currentCopiedPath) {
    @(Import-Csv -LiteralPath $currentCopiedPath)
} else { @() }

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$quarantine = $AuditFolder + ".rejected_structured_narration_conflict_$stamp"
if (Test-Path -LiteralPath $quarantine) { throw "Quarantine already exists: $quarantine" }
Move-Item -LiteralPath $AuditFolder -Destination $quarantine
Copy-Item -LiteralPath (Join-Path $BackupFolder 'audit') -Destination $AuditFolder -Recurse
Write-HandoffLog "audit restored; rejected audit quarantined at $quarantine"

# Remove only newly generated candidate outputs, then restore every pre-pass
# output from the verified safety snapshot.
foreach ($row in $currentCopied) {
    if (-not $candidateNames.Contains($row.original_name)) { continue }
    if (-not $row.target_path) { continue }
    $newTarget = Assert-WithinOutput $row.target_path
    $oldTarget = $oldTargetByName[$row.original_name]
    if ($oldTarget -and -not $newTarget.Equals($oldTarget, [StringComparison]::OrdinalIgnoreCase)) {
        if (Test-Path -LiteralPath $newTarget -PathType Leaf) {
            Remove-Item -LiteralPath $newTarget -Force
        }
    }
}

$flatBackup = Join-Path $BackupFolder 'flat_outputs'
foreach ($row in $manifestRows) {
    $target = Assert-WithinOutput $row.target_path
    $existed = [string]$row.existed -match '^(True|true|1)$'
    if ($existed) {
        $source = Join-Path $flatBackup ([IO.Path]::GetFileName($target))
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
            throw "Missing safety snapshot output: $source"
        }
        Copy-Item -LiteralPath $source -Destination $target -Force
        $actualHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
        if ($row.sha256 -and $actualHash -ne $row.sha256) {
            throw "Restored output hash mismatch: $target"
        }
    } elseif (Test-Path -LiteralPath $target -PathType Leaf) {
        Remove-Item -LiteralPath $target -Force
    }
}
Write-HandoffLog "restored $($manifestRows.Count) pre-pass flat outputs"

$watcherScript = Join-Path $RepoRoot 'tools\auto_rerun_questionable_after_recursive.ps1'
$watcherArgs = @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $watcherScript,
    '-RepoRoot', $RepoRoot,
    '-SourceRoot', $SourceRoot,
    '-OutputDir', $OutputDir,
    '-BackendUrl', $BackendUrl,
    '-PollSeconds', '10',
    '-CurrentYearOnly',
    '-SkipCurrentYearFirstPass',
    '-AllowPlannedBackendUpgradeInterlock',
    '-SkipRecursiveResume'
)
$watcher = Start-Process -FilePath 'powershell.exe' -ArgumentList $watcherArgs -WorkingDirectory $RepoRoot -WindowStyle Hidden -PassThru
Write-HandoffLog "replacement watcher started pid=$($watcher.Id)"

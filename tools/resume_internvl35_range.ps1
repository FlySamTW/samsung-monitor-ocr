param(
    [string]$Url = "https://huggingface.co/lmstudio-community/InternVL3_5-8B-GGUF/resolve/main/InternVL3_5-8B-Q4_K_M.gguf",
    [string]$PartialPath = "$env:USERPROFILE\.lmstudio\models\lmstudio-community\InternVL3_5-8B-GGUF\downloading_InternVL3_5-8B-Q4_K_M.gguf.part",
    [string]$FinalPath = "$env:USERPROFILE\.lmstudio\models\lmstudio-community\InternVL3_5-8B-GGUF\InternVL3_5-8B-Q4_K_M.gguf",
    [Int64]$ExpectedBytes = 5027780512,
    [string]$ExpectedSha256 = "2809043479b8d3aab30378766c7a2a4bd93eedd97c86efc6d65d627fd680faba",
    [string]$LockPath = "$env:USERPROFILE\.lmstudio\models\lmstudio-community\InternVL3_5-8B-GGUF\internvl35.range.lock",
    [string]$LogPath = "",
    [string]$StatusPath = "",
    [switch]$FinalizeOnly,
    [switch]$SharedReadFinalize
)

$ErrorActionPreference = "Stop"
$ErrorActionPreference = "Stop"
if (-not $LogPath) { $LogPath = Join-Path (Split-Path $PSScriptRoot -Parent) "logs\internvl35_range_resume.log" }
if (-not $StatusPath) { $StatusPath = Join-Path (Split-Path $PSScriptRoot -Parent) "logs\model_download_internvl35_status.json" }
$null = New-Item -ItemType Directory -Force -Path (Split-Path $PartialPath -Parent)
$null = New-Item -ItemType Directory -Force -Path (Split-Path $LogPath -Parent)

function Write-Status([string]$State, [hashtable]$Extra = @{}) {
    $payload = [ordered]@{ status=$State; updated_at=(Get-Date).ToString("o"); pid=$PID; url=$Url; partial_path=$PartialPath; final_path=$FinalPath; expected_bytes=$ExpectedBytes; expected_sha256=$ExpectedSha256 }
    foreach ($key in $Extra.Keys) { $payload[$key] = $Extra[$key] }
    $payload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $StatusPath -Encoding UTF8
}

function Test-ExistingDownloader {
    $needle = $PartialPath.Replace("'", "''")
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match "^(curl|curl\.exe)$" -and $_.CommandLine -and $_.CommandLine -like "*$needle*"
    })
}

function Wait-ForExclusiveRead {
    param([string]$Path, [int]$MaxWaitSeconds = 300)
    $deadline = (Get-Date).AddSeconds($MaxWaitSeconds)
    [int]$delay = 2
    while ((Get-Date) -lt $deadline) {
        try {
            $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::None)
            $stream.Dispose()
            return
        } catch [System.IO.IOException] {
            "[$(Get-Date -Format o)] waiting for exclusive read access; retry in ${delay}s" | Add-Content -LiteralPath $LogPath -Encoding UTF8
            Start-Sleep -Seconds $delay
            $delay = [Math]::Min($delay * 2, 30)
        }
    }
    throw "timed out waiting for exclusive read access: $Path"
}

function Get-SourceSnapshot([string]$Path) {
    $item = Get-Item -LiteralPath $Path
    return [ordered]@{ bytes=[Int64]$item.Length; mtime=$item.LastWriteTimeUtc }
}

function Wait-ForStableSource([string]$Path, [int]$Checks = 3, [int]$IntervalSeconds = 2) {
    $previous = Get-SourceSnapshot $Path
    for ($i = 1; $i -lt $Checks; $i++) {
        Start-Sleep -Seconds $IntervalSeconds
        $current = Get-SourceSnapshot $Path
        if ($current.bytes -ne $previous.bytes -or $current.mtime -ne $previous.mtime) {
            throw "source changed during stability checks"
        }
        $previous = $current
    }
    return $previous
}

function Copy-SharedReadAndFinalize([Int64]$ExpectedSourceBytes) {
    $tempPath = "$FinalPath.finalizing.$PID.tmp"
    if (Test-Path -LiteralPath $tempPath) { Remove-Item -LiteralPath $tempPath -Force -ErrorAction Stop }
    $drive = (Get-Item -LiteralPath (Split-Path $tempPath -Qualifier)).PSDrive
    if ($drive.Free -lt ($ExpectedSourceBytes + 1073741824)) {
        throw "insufficient free space for safe finalizing copy"
    }
    $sourceSnapshot = Wait-ForStableSource -Path $PartialPath
    if ($sourceSnapshot.bytes -ne $ExpectedSourceBytes) { throw "source size changed or is not exact" }
    $source = $null
    $target = $null
    try {
        $source = [System.IO.File]::Open($PartialPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        $target = [System.IO.File]::Open($tempPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        $source.CopyTo($target)
        $target.Flush($true)
    } finally {
        if ($target) { $target.Dispose() }
        if ($source) { $source.Dispose() }
    }
    $copied = Get-Item -LiteralPath $tempPath
    if ($copied.Length -ne $ExpectedSourceBytes) { throw "finalizing copy size mismatch" }
    $hash = (Get-FileHash -LiteralPath $tempPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne $ExpectedSha256.ToLowerInvariant()) { throw "SHA256 mismatch in finalizing copy: $hash" }
    $afterSnapshot = Get-SourceSnapshot -Path $PartialPath
    if ($afterSnapshot.bytes -ne $sourceSnapshot.bytes -or $afterSnapshot.mtime -ne $sourceSnapshot.mtime) {
        throw "source changed after finalizing copy"
    }
    if (Test-Path -LiteralPath $FinalPath) { throw "final GGUF appeared; refusing overwrite" }
    Move-Item -LiteralPath $tempPath -Destination $FinalPath
    $finalItem = Get-Item -LiteralPath $FinalPath
    $finalHash = (Get-FileHash -LiteralPath $FinalPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($finalItem.Length -ne $ExpectedBytes -or $finalHash -ne $ExpectedSha256.ToLowerInvariant()) {
        throw "final verification failed after atomic rename"
    }
    $cleanup = "cleanup_pending"
    try {
        Remove-Item -LiteralPath $PartialPath -Force -ErrorAction Stop
        $cleanup = "source_removed"
    } catch {
        "[$(Get-Date -Format o)] final verified but source cleanup pending: $($_.Exception.Message)" | Add-Content -LiteralPath $LogPath -Encoding UTF8
    }
    Write-Status "completed" @{ completed_at=(Get-Date).ToString("o"); bytes=$finalItem.Length; sha256=$finalHash; log=$LogPath; finalize_mode="shared_read"; source_cleanup=$cleanup }
    return $true
}

$lockCreated = $false
$curlStdout = "$LogPath.curl.stdout.tmp"
$curlStderr = "$LogPath.curl.stderr.tmp"
try {
    if (Test-Path -LiteralPath $FinalPath) { throw "final GGUF already exists; refusing to overwrite" }
    $duplicates = @(Test-ExistingDownloader)
    if ($duplicates.Count -gt 0) { throw "another downloader already owns the partial" }
    if (-not (Test-Path -LiteralPath $PartialPath)) { throw "preserved partial is missing" }
    $before = Get-Item -LiteralPath $PartialPath
    if ($before.Length -gt $ExpectedBytes) { throw "partial is larger than expected remote object" }

    try {
        $null = New-Item -ItemType File -Path $LockPath -ErrorAction Stop
        $lockCreated = $true
        Set-Content -LiteralPath $LockPath -Value (([ordered]@{pid=$PID;created_at=(Get-Date).ToString("o");partial=$PartialPath;url=$Url}) | ConvertTo-Json) -Encoding UTF8
    } catch { throw "could not atomically claim downloader lock: $LockPath" }

    $state = if ($FinalizeOnly) { "finalizing" } else { "downloading" }
    Write-Status $state @{ started_at=(Get-Date).ToString("o"); start_bytes=$before.Length; log=$LogPath; lock=$LockPath; finalize_only=[bool]$FinalizeOnly }
    "[$(Get-Date -Format o)] $state start bytes=$($before.Length) url=$Url" | Add-Content -LiteralPath $LogPath -Encoding UTF8
    if (-not $FinalizeOnly) {
        $curlArgs = @(
            "--fail", "--location", "--retry", "10", "--retry-all-errors", "--retry-delay", "5",
            "--connect-timeout", "20", "--continue-at", "-", "--output", $PartialPath, $Url
        )
        $curlProcess = Start-Process -FilePath "curl.exe" -ArgumentList $curlArgs `
            -RedirectStandardOutput $curlStdout -RedirectStandardError $curlStderr -WindowStyle Hidden -Wait -PassThru
        if (Test-Path -LiteralPath $curlStdout) { Get-Content -LiteralPath $curlStdout -Raw -ErrorAction SilentlyContinue | Add-Content -LiteralPath $LogPath -Encoding UTF8 }
        if (Test-Path -LiteralPath $curlStderr) { Get-Content -LiteralPath $curlStderr -Raw -ErrorAction SilentlyContinue | Add-Content -LiteralPath $LogPath -Encoding UTF8 }
        if ($curlProcess.ExitCode -ne 0) { throw "curl failed with exit code $($curlProcess.ExitCode)" }
    }

    if ($SharedReadFinalize) {
        if (-not $FinalizeOnly) { throw "shared-read finalization requires -FinalizeOnly" }
        $null = Copy-SharedReadAndFinalize -ExpectedSourceBytes $ExpectedBytes
        return
    }
    Wait-ForExclusiveRead -Path $PartialPath
    $after = Get-Item -LiteralPath $PartialPath
    if ($after.Length -lt $before.Length) { throw "partial shrank; refusing to continue" }
    if ($after.Length -ne $ExpectedBytes) { throw "download ended at $($after.Length), expected $ExpectedBytes" }
    $hash = (Get-FileHash -LiteralPath $PartialPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne $ExpectedSha256.ToLowerInvariant()) { throw "SHA256 mismatch: $hash" }
    if (Test-Path -LiteralPath $FinalPath) { throw "final GGUF appeared during download; refusing overwrite" }
    Move-Item -LiteralPath $PartialPath -Destination $FinalPath
    Write-Status "completed" @{ completed_at=(Get-Date).ToString("o"); bytes=$after.Length; sha256=$hash; log=$LogPath }
    "[$(Get-Date -Format o)] completed bytes=$($after.Length) sha256=$hash" | Add-Content -LiteralPath $LogPath -Encoding UTF8
} catch {
    $message = $_.Exception.Message
    $partialBytes = $null
    if (Test-Path -LiteralPath $PartialPath) { $partialBytes = (Get-Item -LiteralPath $PartialPath).Length }
    Write-Status "failed_closed" @{ failure_reason=$message; log=$LogPath; partial_bytes=$partialBytes }
    "[$(Get-Date -Format o)] FAIL_CLOSED $message" | Add-Content -LiteralPath $LogPath -Encoding UTF8
    exit 2
} finally {
    Remove-Item -LiteralPath $curlStdout, $curlStderr -Force -ErrorAction SilentlyContinue
    if ($lockCreated -and (Test-Path -LiteralPath $LockPath)) {
        Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
    }
}

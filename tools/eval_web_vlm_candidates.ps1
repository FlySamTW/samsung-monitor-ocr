param(
  [string]$RepoRoot = "D:\00_商化\samsung-monitor-ocr",
  [string]$Cases = "runs\model_eval\current_hard_6.json",
  [int]$Limit = 2,
  [int]$ContextLength = 16384,
  [int]$TimeoutSeconds = 520
)

$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

Set-Location $RepoRoot
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"

function Write-RunLog([string]$Message) {
  $line = "[{0}] {1}" -f (Get-Date -Format "s"), $Message
  Write-Output $line
}

function Summarize-Json([string]$Path) {
  if (!(Test-Path $Path)) { return "no output" }
  $txt = Get-Content $Path -Raw -Encoding UTF8
  $count = ([regex]::Matches($txt, '"passed"\s*:')).Count
  $passed = ([regex]::Matches($txt, '"passed"\s*:\s*true')).Count
  $elapsedMatches = [regex]::Matches($txt, '"elapsed_sec"\s*:\s*([0-9.]+)')
  $sum = 0.0
  foreach ($m in $elapsedMatches) { $sum += [double]$m.Groups[1].Value }
  $avg = if ($elapsedMatches.Count -gt 0) { [math]::Round($sum / $elapsedMatches.Count, 2) } else { 0 }
  return "passed=$passed/$count avg_sec=$avg sum_sec=$([math]::Round($sum, 2))"
}

function Invoke-HfDownload([string]$RepoId, [string[]]$Files, [string]$TargetDir) {
  New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
  & $Python tools\download_hf_files.py --repo-id $RepoId --target-dir $TargetDir --files $Files
  return $LASTEXITCODE
}

function Test-Candidate($Candidate) {
  Write-RunLog "candidate start $($Candidate.Name)"
  Write-RunLog "download repo=$($Candidate.RepoId)"
  $downloadExit = Invoke-HfDownload $Candidate.RepoId $Candidate.Files $Candidate.TargetDir
  Write-RunLog "download exit=$downloadExit"
  if ($downloadExit -ne 0) {
    Write-RunLog "candidate skipped because download failed"
    return
  }

  lms ls
  $loaded = $false
  foreach ($key in $Candidate.LoadKeys) {
    Write-RunLog "try load key=$key id=$($Candidate.Identifier)"
    lms load $key --context-length $ContextLength --gpu max --identifier $Candidate.Identifier --parallel 1 --ttl 900 --yes
    $loadExit = $LASTEXITCODE
    Write-RunLog "load exit=$loadExit key=$key"
    lms ps
    if ($loadExit -eq 0) {
      $loaded = $true
      break
    }
  }

  if (!$loaded) {
    Write-RunLog "candidate skipped because no load key worked"
    return
  }

  $safe = $Candidate.Identifier -replace '[^A-Za-z0-9_-]', '_'
  $outJson = "runs\model_eval\live2_web_${safe}_$Stamp.json"
  $evalLog = "logs\model_eval_web_${safe}_$Stamp.log"
  Write-RunLog "eval model=$($Candidate.Identifier) output=$outJson"
  & $Python tools\qwen_vl_regression.py --model $Candidate.Identifier --cases $Cases --limit $Limit --timeout $TimeoutSeconds --max-side 1800 --normalize-backend --output $outJson *> $evalLog
  Write-RunLog "eval exit=$LASTEXITCODE $(Summarize-Json $outJson) log=$evalLog"
  Write-RunLog "unload $($Candidate.Identifier)"
  lms unload $Candidate.Identifier
  Write-RunLog "unload exit=$LASTEXITCODE"
  Write-RunLog "candidate done $($Candidate.Name)"
}

$lmRoot = Join-Path $env:USERPROFILE ".lmstudio\models"
$candidates = @(
  @{
    Name = "MiniCPM-V-4.6"
    RepoId = "ggml-org/MiniCPM-V-4.6-GGUF"
    Files = @("MiniCPM-V-4.6-Q4_K_M.gguf", "mmproj-MiniCPM-V-4.6-bf16.gguf")
    TargetDir = Join-Path $lmRoot "ggml-org\MiniCPM-V-4.6-GGUF"
    Identifier = "minicpm-v-4.6"
    LoadKeys = @(
      "MiniCPM-V-4.6",
      "MiniCPM-V-4.6-GGUF",
      "ggml-org/MiniCPM-V-4.6-GGUF",
      (Join-Path $lmRoot "ggml-org\MiniCPM-V-4.6-GGUF\MiniCPM-V-4.6-Q4_K_M.gguf")
    )
  },
  @{
    Name = "InternVL3.5-8B"
    RepoId = "lmstudio-community/InternVL3_5-8B-GGUF"
    Files = @("InternVL3_5-8B-Q4_K_M.gguf", "mmproj-model-f16.gguf")
    TargetDir = Join-Path $lmRoot "lmstudio-community\InternVL3_5-8B-GGUF"
    Identifier = "internvl3_5-8b"
    LoadKeys = @(
      "InternVL3_5-8B",
      "InternVL3_5-8B-GGUF",
      "lmstudio-community/InternVL3_5-8B-GGUF",
      (Join-Path $lmRoot "lmstudio-community\InternVL3_5-8B-GGUF\InternVL3_5-8B-Q4_K_M.gguf")
    )
  }
)

Write-RunLog "web candidate eval start"
foreach ($candidate in $candidates) {
  Test-Candidate $candidate
}
Write-RunLog "web candidate eval done"
lms ps

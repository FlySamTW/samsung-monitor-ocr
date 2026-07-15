param([ValidateSet("install","uninstall","status","ensure")][string]$Action="install",[string]$RepoRoot,[string]$SourceRoot,[string]$OutputDir)
$ErrorActionPreference="Stop"; $RepoRoot=(Resolve-Path $RepoRoot).Path
$key='HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'; $name='SamsungOCRContinuityDaemon'
$taskName='SamsungOCR_UserContinuityEnsure'
$daemon=Join-Path $RepoRoot 'tools\ocr_continuity_daemon.ps1'; $ps=(Get-Command powershell.exe).Source
$cmd='powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -RepoRoot "{1}" -SourceRoot "{2}" -OutputDir "{3}"' -f $daemon,$RepoRoot,$SourceRoot,$OutputDir
function Get-Daemon {
  $needle='-File\s+["'']?'+[regex]::Escape($daemon)
  @(Get-CimInstance Win32_Process|Where-Object{$_.CommandLine -and $_.CommandLine -match $needle -and $_.CommandLine -match [regex]::Escape($RepoRoot)})
}
function Start-Daemon {
  Start-Process -FilePath $ps -ArgumentList @('-NoProfile','-WindowStyle','Hidden','-ExecutionPolicy','Bypass','-File',$daemon,'-RepoRoot',$RepoRoot,'-SourceRoot',$SourceRoot,'-OutputDir',$OutputDir) -WorkingDirectory $RepoRoot -WindowStyle Hidden | Out-Null
}
if($Action -eq 'ensure') {
  if(@(Get-Daemon).Count -gt 0){Write-Output 'daemon already present; no-op'; exit 0}
  $lock=Join-Path $OutputDir '_ocr_audit\ocr_continuity_daemon.lock'
  if(Test-Path $lock){$owner=(Get-Content $lock -Raw|ConvertFrom-Json -ErrorAction SilentlyContinue).pid; $alive=Get-Process -Id $owner -ErrorAction SilentlyContinue; $age=((Get-Date)-(Get-Item $lock).LastWriteTime).TotalMinutes; if($alive -or $age -lt 30){Write-Output 'daemon lock owner/age not proven; fail closed'; exit 3}; Remove-Item $lock -Force -ErrorAction Stop}
  Start-Daemon; Write-Output 'daemon started'; exit 0
}
if($Action -eq 'status'){ $v=(Get-ItemProperty -Path $key -Name $name -ErrorAction SilentlyContinue).$name; [pscustomobject]@{registry=$v;startup=(Test-Path (Join-Path ([Environment]::GetFolderPath('Startup')) 'SamsungOCRContinuityDaemon.cmd'))}|ConvertTo-Json; exit 0 }
if($Action -eq 'uninstall'){Remove-ItemProperty -Path $key -Name $name -ErrorAction SilentlyContinue; Remove-Item (Join-Path ([Environment]::GetFolderPath('Startup')) 'SamsungOCRContinuityDaemon.cmd') -Force -ErrorAction SilentlyContinue; & schtasks.exe /Delete /TN $taskName /F 2>$null; Write-Output 'user daemon unregistered'; exit 0}
try { New-Item -Path $key -Force|Out-Null; New-ItemProperty -Path $key -Name $name -Value $cmd -PropertyType String -Force|Out-Null; Write-Output 'registered HKCU Run' } catch { $startup=[Environment]::GetFolderPath('Startup'); $bat=Join-Path $startup 'SamsungOCRContinuityDaemon.cmd'; Set-Content $bat "@echo off`r`nstart \"\" /b powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"$daemon\" -RepoRoot \"$RepoRoot\" -SourceRoot \"$SourceRoot\" -OutputDir \"$OutputDir\"`r`n"; Write-Output 'registered Startup fallback' }
$hiddenEnsure=Join-Path $RepoRoot 'tools\ocr_continuity_ensure_hidden.vbs'
if(-not (Test-Path $hiddenEnsure)){throw "hidden ensure launcher missing: $hiddenEnsure"}
$tr='wscript.exe //B //Nologo "{0}" "{1}" "{2}" "{3}"' -f $hiddenEnsure,$RepoRoot,$SourceRoot,$OutputDir
$created=& schtasks.exe /Create /TN $taskName /SC MINUTE /MO 5 /TR $tr /RL LIMITED /F 2>&1
if($LASTEXITCODE -ne 0){Write-Output 'user ensure task creation denied; existing setup kept'}else{Write-Output "registered LIMITED task: $taskName"}

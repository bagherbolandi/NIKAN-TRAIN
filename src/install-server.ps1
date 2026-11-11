#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
$appDir = $PSScriptRoot
$dataDir = Join-Path $env:ProgramData 'NikanTrainingServer'
New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
# Isolate personnel data from ordinary local accounts. Service account SID is stable across languages.
& icacls.exe $dataDir /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' '*S-1-5-19:(OI)(CI)M' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not secure the database directory' }
$python = Join-Path $appDir 'runtime\python.exe'
if (!(Test-Path (Join-Path $dataDir 'nikan.sqlite3'))) {
    throw 'Run Initialize Server locally and create an administrator first.'
}
& $python (Join-Path $appDir 'server.py') --check-ready --data-dir $dataDir
if ($LASTEXITCODE -ne 0) { throw 'Create an administrator using Initialize Server before registering startup.' }
$action = New-ScheduledTaskAction -Execute $python -Argument ('"{0}" --data-dir "{1}"' -f (Join-Path $appDir 'server.py'),$dataDir) -WorkingDirectory $appDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'S-1-5-19' -LogonType ServiceAccount
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable
Register-ScheduledTask -TaskName 'NikanTrainingServer' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
$backupAction = New-ScheduledTaskAction -Execute $python -Argument ('"{0}" --backup --data-dir "{1}"' -f (Join-Path $appDir 'server.py'),$dataDir) -WorkingDirectory $appDir
$backupTrigger = New-ScheduledTaskTrigger -Daily -At '02:00'
Register-ScheduledTask -TaskName 'NikanTrainingBackup' -Action $backupAction -Trigger $backupTrigger -Principal $principal -Settings $settings -Force | Out-Null
$config = Get-Content (Join-Path $dataDir 'server.json') -Raw | ConvertFrom-Json
if (!(Get-NetFirewallRule -Name 'NikanTrainingIntranet' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name 'NikanTrainingIntranet' -DisplayName 'Nikan Training - Local Subnet' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $config.port -RemoteAddress LocalSubnet -Profile Domain,Private | Out-Null
}
Start-ScheduledTask -TaskName 'NikanTrainingServer'
Write-Host ('Server configured on port {0}. Daily backup: 02:00 server time. Data: {1}' -f $config.port,$dataDir)

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
 Start-Process powershell.exe -Verb RunAs -ArgumentList ('-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '"') -Wait
 exit
}
$ErrorActionPreference='Stop'
Enable-ScheduledTask -TaskName 'NikanTrainingServer' | Out-Null
Start-ScheduledTask -TaskName 'NikanTrainingServer'
& "$PSScriptRoot/runtime/python.exe" -I "$PSScriptRoot/server_setup.py" --data-dir "$env:ProgramData/NikanTrainingServer" --wait
if ($LASTEXITCODE -ne 0) { throw 'HTTPS server did not become ready' }

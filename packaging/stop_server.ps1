param([string]$InstallDir = $PSScriptRoot)
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
 Start-Process powershell.exe -Verb RunAs -ArgumentList ('-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '"') -Wait
 exit
}
$ErrorActionPreference='Stop'
$root=Join-Path $env:ProgramData 'NikanTrainingServer'
# Disable before stop to prevent automatic task restart during restore or upgrade.
if (Get-ScheduledTask -TaskName 'NikanTrainingServer' -ErrorAction SilentlyContinue) {
 Disable-ScheduledTask -TaskName 'NikanTrainingServer' | Out-Null
}
& "$InstallDir/runtime/python.exe" -I "$InstallDir/network_server.py" --data-dir $root --stop
$deadline=(Get-Date).AddSeconds(45)
do {
 $active=(Get-ScheduledTask -TaskName 'NikanTrainingServer' -ErrorAction SilentlyContinue).State -eq 'Running'
 if (!$active) { break }
 Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $deadline)
if ($active) { throw 'Server did not stop cleanly. Do not restore or upgrade until it has stopped.' }

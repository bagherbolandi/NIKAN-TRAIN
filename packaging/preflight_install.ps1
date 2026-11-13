param(
 [Parameter(Mandatory=$true)][string]$InstallDir,
 [Parameter(Mandatory=$true)][string]$DataDir,
 [Parameter(Mandatory=$true)][string]$SnapshotScript,
 [switch]$Server
)
$ErrorActionPreference = 'Stop'
$wasRunning = $false
try {
 $target = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\') + '\'
 if ($Server) {
  $configPath = Join-Path $DataDir 'server.json'
  if (Test-Path -LiteralPath $configPath) {
   $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
   if (!$config.certificate -and $config.https) {
    throw 'Existing reverse-proxy installation detected. Preserve its proxy settings and follow the documented upgrade procedure before replacing server files.'
   }
  }
  $task = Get-ScheduledTask -TaskName 'NikanTrainingServer' -ErrorAction SilentlyContinue
  $wasRunning = $task -and $task.State -eq 'Running'
  if ($wasRunning) {
   $oldStop = Join-Path $InstallDir 'stop_server.ps1'
   if (Test-Path -LiteralPath $oldStop) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $oldStop -InstallDir $InstallDir
    if ($LASTEXITCODE -ne 0) { throw 'Existing server did not stop cleanly.' }
   } else {
    Stop-ScheduledTask -TaskName 'NikanTrainingServer'
    Start-Sleep -Milliseconds 750
   }
  }
 }
 $running = Get-CimInstance Win32_Process | Where-Object {
  $_.ExecutablePath -and $_.ExecutablePath.StartsWith($target,[StringComparison]::OrdinalIgnoreCase)
 }
 if ($running) { throw 'Application files are in use. Close this installation before upgrading. No files have been replaced.' }
 $drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($target))
 if ($drive.AvailableFreeSpace -lt 350MB) { throw 'At least 350 MB of free installation space is required.' }
 $python = Join-Path $InstallDir 'runtime\python.exe'
 if (Test-Path -LiteralPath $python) {
  & $python -I $SnapshotScript --data-dir $DataDir
  if ($LASTEXITCODE -ne 0) { throw 'Database backup or validation failed. No files have been replaced.' }
 }
 if (Test-Path -LiteralPath $target) {
  foreach ($file in Get-ChildItem -LiteralPath $target -File -Recurse) {
   $handle = $null
   try { $handle = [IO.File]::Open($file.FullName,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None) }
   finally { if ($null -ne $handle) { $handle.Dispose() } }
  }
 }
 Write-Output 'Installation, disk space and database preflight passed.'
 exit 0
} catch {
 if ($Server -and $wasRunning) {
  try { Start-ScheduledTask -TaskName 'NikanTrainingServer' } catch { Write-Output 'Restart the unchanged server task after resolving the preflight error.' }
 }
 Write-Output ('Installation stopped before file replacement: ' + $_.Exception.Message)
 exit 22
}

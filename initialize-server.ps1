#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
$dataDir = Join-Path $env:ProgramData 'NikanTrainingServer'
New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
& icacls.exe $dataDir /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' '*S-1-5-19:(OI)(CI)M' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not secure the database directory' }
Write-Host 'Create the holding administrator locally. After setup, press Ctrl+C and run install-server.ps1.'
& (Join-Path $PSScriptRoot 'runtime\python.exe') (Join-Path $PSScriptRoot 'server.py') --initialize --data-dir $dataDir

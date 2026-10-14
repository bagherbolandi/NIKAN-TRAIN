param([Parameter(Mandatory=$true)][string]$InstallDir)
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:ProgramData 'NikanTrainingServer'
New-Item -ItemType Directory -Force $root | Out-Null
# Password hashes, TLS keys and plaintext snapshot files belong only to SYSTEM and Administrators.
& icacls.exe $root '/inheritance:r' '/grant:r' '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not protect server data directory' }
& "$InstallDir/runtime/python.exe" -I "$InstallDir/server_setup.py" --ini "$InstallDir/install.ini" --data-dir $root
if ($LASTEXITCODE -ne 0) { throw 'Server configuration failed' }
$config = Get-Content "$root/server.json" -Raw | ConvertFrom-Json
& certutil.exe -addstore -f Root "$root/tls/server.cer" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not trust the local HTTPS certificate' }
$action = New-ScheduledTaskAction -Execute "$InstallDir/runtime/pythonw.exe" -Argument "-I `"$InstallDir/network_server.py`" --data-dir `"$root`"" -WorkingDirectory $InstallDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'NikanTrainingServer' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Get-NetFirewallRule -Name 'NikanTrainingHTTPS' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -Name 'NikanTrainingHTTPS' -DisplayName 'Nikan Training HTTPS (LAN)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $config.port -Profile Domain,Private -RemoteAddress LocalSubnet | Out-Null
$docs = Join-Path $env:PUBLIC 'Documents/Nikan Training Network'
New-Item -ItemType Directory -Force $docs | Out-Null
Copy-Item "$root/tls/server.cer" "$docs/Nikan-Server.cer" -Force
Copy-Item "$InstallDir/NETWORK-README.md" $docs -Force
Set-Content "$docs/Server-URL.txt" "https://$($config.hostname):$($config.port)" -Encoding UTF8
Start-ScheduledTask -TaskName 'NikanTrainingServer'
& "$InstallDir/runtime/python.exe" -I "$InstallDir/server_setup.py" --data-dir $root --wait
if ($LASTEXITCODE -ne 0) { throw 'HTTPS server did not become ready' }

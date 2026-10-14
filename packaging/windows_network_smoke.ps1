$ErrorActionPreference='Stop'
$install=Join-Path $env:ProgramFiles 'NikanTrainingServer'
$root=Join-Path $env:ProgramData 'NikanTrainingServer'
$result=@{status='running';stage='checksum';platform=[Environment]::OSVersion.VersionString;details=''}
try {
 foreach ($name in @('NikanTraining-Network-Server-Setup.exe','NikanTraining-Network-Client-Setup.exe')) {
  $expected=(Get-Content "distribution/$name.sha256").Split(' ')[0]
  if ((Get-FileHash "distribution/$name" -Algorithm SHA256).Hash.ToLower() -ne $expected) { throw "Checksum mismatch: $name" }
 }
 $result.stage='server-install'
 $p=Start-Process "$pwd/distribution/NikanTraining-Network-Server-Setup.exe" -ArgumentList '/S' -Wait -PassThru
 if ($p.ExitCode -ne 0 -or !(Test-Path "$install/runtime/python.exe")) { throw "Server installation failed: $($p.ExitCode)" }
 $task=Get-ScheduledTask -TaskName 'NikanTrainingServer'
 if ($task.Principal.UserId -notin @('SYSTEM','S-1-5-18')) { throw 'Server task is not configured for SYSTEM' }
 if (!(Get-NetFirewallRule -Name 'NikanTrainingHTTPS')) { throw 'Missing network firewall rule' }
 $acl=Get-Acl $root
 if (!$acl.AreAccessRulesProtected) { throw 'Server data ACL still inherits public permissions' }
 $config=Get-Content "$root/server.json" -Raw | ConvertFrom-Json
 $result.stage='https-and-recovery'
 $output=& "$install/runtime/python.exe" -I "$pwd/packaging/validate_network_payload.py" --install $install --data-dir $root 2>&1
 if ($LASTEXITCODE -ne 0) { throw "Network payload validation failed: $output" }
 $result.payload=($output -join "`n")
 $keyBefore=(Get-FileHash "$root/keys/recovery.key").Hash
 $certBefore=(Get-FileHash "$root/tls/server.cer").Hash
 $dbBefore=(Get-Item "$root/nikan.sqlite3").Length
 $result.stage='upgrade'
 $p=Start-Process "$pwd/distribution/NikanTraining-Network-Server-Setup.exe" -ArgumentList '/S' -Wait -PassThru
 if ($p.ExitCode -ne 0) { throw 'Server upgrade failed' }
 if ((Get-FileHash "$root/keys/recovery.key").Hash -ne $keyBefore -or (Get-FileHash "$root/tls/server.cer").Hash -ne $certBefore) { throw 'Upgrade changed recovery key or TLS identity' }
 if ((Get-Item "$root/nikan.sqlite3").Length -lt $dbBefore) { throw 'Upgrade lost database data' }
 $result.stage='client-install'
 $url="https://$($config.hostname):$($config.port)"
 $p=Start-Process "$pwd/distribution/NikanTraining-Network-Client-Setup.exe" -ArgumentList @('/S',"/URL=$url") -Wait -PassThru
 if ($p.ExitCode -ne 0) { throw "Network client install failed (exit $($p.ExitCode), URL $url)" }
 $shortcut=Join-Path ([Environment]::GetFolderPath('Desktop')) 'Nikan Training Network.url'
 if (!(Test-Path $shortcut) -or !(Select-String -Path $shortcut -SimpleMatch "URL=$url")) { throw 'Wrong client shortcut target' }
 $p=Start-Process "$env:LOCALAPPDATA/Programs/NikanTrainingNetworkClient/Uninstall.exe" -ArgumentList '/S' -Wait -PassThru
 if ($p.ExitCode -ne 0) { throw 'Network client uninstall failed' }
 $p=Start-Process "$pwd/distribution/NikanTraining-Network-Client-Setup.exe" -ArgumentList @('/S',("/URL=`""+$url+"`"")) -Wait -PassThru
 if ($p.ExitCode -ne 0 -or !(Select-String -Path $shortcut -SimpleMatch "URL=$url")) { throw 'Quoted client URL install failed' }
 $p=Start-Process "$env:LOCALAPPDATA/Programs/NikanTrainingNetworkClient/Uninstall.exe" -ArgumentList '/S' -Wait -PassThru
 if ($p.ExitCode -ne 0) { throw 'Quoted client uninstall failed' }
 $result.stage='server-uninstall'
 $p=Start-Process "$install/Uninstall.exe" -ArgumentList '/S' -Wait -PassThru
 if ($p.ExitCode -ne 0) { throw 'Server uninstall failed' }
 if (!(Test-Path "$root/nikan.sqlite3") -or !(Test-Path "$root/keys/recovery.key") -or !(Get-ChildItem "$root/backups/*.nikbak")) { throw 'Uninstall removed business data or recovery material' }
 if (Get-ScheduledTask -TaskName 'NikanTrainingServer' -ErrorAction SilentlyContinue) { throw 'Uninstall left scheduled task' }
 $result.status='passed';$result.stage='completed'
} catch {
 $result.status='failed';$result.details=$_.Exception.Message
 if (Test-Path "$root/server.log") { $result.server_log=(Get-Content "$root/server.log" -Tail 40) -join "`n" }
 if (Get-ScheduledTask -TaskName 'NikanTrainingServer' -ErrorAction SilentlyContinue) { $result.task=(Get-ScheduledTaskInfo -TaskName 'NikanTrainingServer' | Select-Object LastTaskResult,LastRunTime | ConvertTo-Json -Compress) }
 throw
} finally {
 New-Item -ItemType Directory -Force docs | Out-Null
 $result | ConvertTo-Json | Set-Content docs/windows-network-result.json -Encoding UTF8
}

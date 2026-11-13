$ErrorActionPreference = 'Stop'
$install = Join-Path $env:ProgramFiles 'NikanTrainingServer'
$root = Join-Path $env:ProgramData 'NikanTrainingServer'
$checks = [System.Collections.Generic.List[string]]::new()
$probeRuntime = $null
$result = [ordered]@{
    status = 'running'; stage = 'checksum'; platform = [Environment]::OSVersion.VersionString
    native_windows_execution = $true; source_sha = $env:GITHUB_SHA; run_id = $env:GITHUB_RUN_ID
    server_install = $install; server_data = $root; details = ''
}
function Get-BusinessSnapshot([string]$Interpreter = "$install/runtime/python.exe") {
    $code = 'import sqlite3,sys,json; c=sqlite3.connect("file:"+sys.argv[1]+"?mode=ro",uri=True); assert c.execute("PRAGMA integrity_check").fetchall()==[("ok",)]; print(json.dumps({table:c.execute("SELECT * FROM "+table+" ORDER BY 1,2").fetchall() for table in ("employees","users","course_participants")},sort_keys=True))'
    $output = & $Interpreter -I -c $code "$root/nikan.sqlite3" 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw "Could not read installed database preservation snapshot: $output" }
    return $output.Trim()
}
try {
    foreach ($name in @('NikanHRM-Server-Setup.exe', 'NikanTraining-Network-Client-Setup.exe')) {
        $expected = (Get-Content "distribution/$name.sha256").Split(' ')[0]
        if ((Get-FileHash "distribution/$name" -Algorithm SHA256).Hash.ToLower() -ne $expected) { throw "Checksum mismatch: $name" }
    }
    $checks.Add('server and legacy client SHA-256 checksums match committed artifacts')
    $result.stage = 'server-install'
    $installer = Join-Path $pwd 'distribution/NikanHRM-Server-Setup.exe'
    $process = Start-Process $installer -ArgumentList '/S' -Wait -PassThru
    if ($process.ExitCode -ne 0 -or !(Test-Path "$install/runtime/python.exe")) { throw "Server installation failed: $($process.ExitCode)" }
    $task = Get-ScheduledTask -TaskName 'NikanTrainingServer'
    if ($task.Principal.UserId -notin @('SYSTEM', 'S-1-5-18')) { throw 'Server task is not configured for SYSTEM' }
    if ([IO.Path]::GetFullPath($task.Actions.Execute) -ine [IO.Path]::GetFullPath("$install/runtime/pythonw.exe")) { throw 'Server task does not use its installed bundled interpreter' }
    if (!(Get-NetFirewallRule -Name 'NikanTrainingHTTPS')) { throw 'Missing network firewall rule' }
    if (!(Get-Acl $root).AreAccessRulesProtected) { throw 'Server data ACL still inherits public permissions' }
    $config = Get-Content "$root/server.json" -Raw | ConvertFrom-Json
    $checks.Add('server keeps original paths/task identity, bundled runtime, restricted data ACL and firewall')

    $result.stage = 'https-training-and-encrypted-recovery'
    $output = & "$install/runtime/python.exe" -I "$pwd/packaging/validate_network_payload.py" --install $install --data-dir $root 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw "Network training/recovery payload failed: $output" }
    $result.training_runtime = $output.Trim()
    Write-Host $output.Trim()
    $checks.Add('verified HTTPS, legacy training, concurrent writes, encrypted backup and recovery pass with bundled runtime')
    $result.stage = 'hrm-runtime'
    $output = & "$install/runtime/python.exe" -I "$pwd/packaging/validate_hrm_payload.py" --install $install --report "$pwd/docs/windows-network-hrm-result.json" 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw "Installed server HRM payload failed: $output" }
    $result.hrm_runtime = $output.Trim()
    Write-Host $output.Trim()
    $checks.Add('installed server interpreter passes HRM permissions, attachments, domains and backup checks')
    $keyBefore = (Get-FileHash "$root/keys/recovery.key" -Algorithm SHA256).Hash
    $certBefore = (Get-FileHash "$root/tls/server.cer" -Algorithm SHA256).Hash
    $businessBefore = Get-BusinessSnapshot
    Set-Content "$root/native-preservation-test.txt" 'keep synthetic network business data'

    $result.stage = 'server-upgrade'
    $process = Start-Process $installer -ArgumentList '/S' -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "Server upgrade failed: $($process.ExitCode)" }
    if ((Get-FileHash "$root/keys/recovery.key" -Algorithm SHA256).Hash -ne $keyBefore -or (Get-FileHash "$root/tls/server.cer" -Algorithm SHA256).Hash -ne $certBefore) { throw 'Upgrade changed recovery key or TLS identity' }
    if ((Get-BusinessSnapshot) -cne $businessBefore) { throw 'Upgrade changed personnel, accounts or actual training participation' }
    if ((Get-Content "$root/native-preservation-test.txt" -Raw).Trim() -ne 'keep synthetic network business data') { throw 'Upgrade changed business data marker' }
    if ((Get-ScheduledTask -TaskName 'NikanTrainingServer').Principal.UserId -notin @('SYSTEM', 'S-1-5-18')) { throw 'Upgrade changed server task identity' }
    $checks.Add('server upgrade preserves real business rows, database integrity, recovery key, TLS identity and task identity')

    $result.stage = 'client-install'
    $url = "https://$($config.hostname):$($config.port)"
    $clientInstaller = Join-Path $pwd 'distribution/NikanTraining-Network-Client-Setup.exe'
    $clientRoot = Join-Path $env:LOCALAPPDATA 'Programs/NikanTrainingNetworkClient'
    $shortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Nikan Training Network.url'
    foreach ($urlArgument in @("/URL=$url", "/URL=`"$url`"")) {
        $process = Start-Process $clientInstaller -ArgumentList @('/S', $urlArgument) -Wait -PassThru
        if ($process.ExitCode -ne 0 -or !(Test-Path $shortcut) -or !(Select-String -Path $shortcut -SimpleMatch "URL=$url")) { throw "Network client install/URL failed: $urlArgument" }
        $process = Start-Process "$clientRoot/Uninstall.exe" -ArgumentList '/S' -Wait -PassThru
        if ($process.ExitCode -ne 0 -or (Test-Path $shortcut)) { throw 'Network client uninstall left shortcut or failed' }
    }
    $checks.Add('legacy network client installs/uninstalls with unquoted and quoted HTTPS URLs')

    $result.stage = 'server-uninstall'
    $temporary = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [IO.Path]::GetTempPath() }
    $probeRuntime = Join-Path $temporary ('nikan-network-runtime-' + [guid]::NewGuid())
    Copy-Item "$install/runtime" $probeRuntime -Recurse
    $process = Start-Process "$install/Uninstall.exe" -ArgumentList '/S' -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "Server uninstall failed: $($process.ExitCode)" }
    foreach ($file in @('nikan.sqlite3', 'keys/recovery.key', 'tls/server.cer', 'native-preservation-test.txt')) {
        if (!(Test-Path "$root/$file")) { throw "Server uninstall removed preserved data: $file" }
    }
    if ((Get-BusinessSnapshot "$probeRuntime/python.exe") -cne $businessBefore) { throw 'Server uninstall changed actual business records or database integrity' }
    if (!(Get-ChildItem "$root/backups/*.nikbak")) { throw 'Server uninstall removed encrypted backups' }
    if ((Get-FileHash "$root/keys/recovery.key" -Algorithm SHA256).Hash -ne $keyBefore -or (Get-FileHash "$root/tls/server.cer" -Algorithm SHA256).Hash -ne $certBefore) { throw 'Server uninstall changed recovery material' }
    if (Get-ScheduledTask -TaskName 'NikanTrainingServer' -ErrorAction SilentlyContinue) { throw 'Server uninstall left scheduled task' }
    if (Get-NetFirewallRule -Name 'NikanTrainingHTTPS' -ErrorAction SilentlyContinue) { throw 'Server uninstall left firewall rule' }
    if (Test-Path 'HKLM:/Software/Microsoft/Windows/CurrentVersion/Uninstall/NikanTrainingServer') { throw 'Server uninstall left registration' }
    $checks.Add('server uninstall removes task/firewall/registration and preserves database, encrypted backups and recovery identity')
    $result.status = 'passed'; $result.stage = 'completed'
} catch {
    $result.status = 'failed'; $result.details = $_.Exception.Message
    if (Test-Path "$root/server.log") { $result.server_log = (Get-Content "$root/server.log" -Tail 40) -join "`n" }
    if (Get-ScheduledTask -TaskName 'NikanTrainingServer' -ErrorAction SilentlyContinue) { $result.task = Get-ScheduledTaskInfo -TaskName 'NikanTrainingServer' | Select-Object LastTaskResult, LastRunTime }
    throw
} finally {
    if ($null -ne $probeRuntime -and (Test-Path $probeRuntime)) { Remove-Item $probeRuntime -Recurse -Force -ErrorAction SilentlyContinue }
    $result.checks = $checks.ToArray()
    $failed = if ($result.status -eq 'failed') { 1 } else { 0 }
    $result.test_counts = @{ total = $checks.Count + $failed; passed = $checks.Count; failed = $failed }
    New-Item -ItemType Directory -Force docs | Out-Null
    $result | ConvertTo-Json -Depth 6 | Set-Content docs/windows-network-result.json -Encoding utf8
}

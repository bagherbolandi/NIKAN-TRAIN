$ErrorActionPreference = 'Stop'
$install = Join-Path $env:LOCALAPPDATA 'Programs/NikanHRM-Test'
$data = Join-Path $env:LOCALAPPDATA 'NikanHRM-Test'
$legacyInstall = Join-Path $env:LOCALAPPDATA 'Programs/NikanTraining'
$legacyData = Join-Path $env:LOCALAPPDATA 'NikanTraining'
$desktop = [Environment]::GetFolderPath('Desktop')
$programs = [Environment]::GetFolderPath('Programs')
$shell = New-Object -ComObject WScript.Shell
$checks = [System.Collections.Generic.List[string]]::new()
$result = [ordered]@{
    status = 'running'; stage = 'checksum'; platform = [Environment]::OSVersion.VersionString
    native_windows_execution = $true; source_sha = $env:GITHUB_SHA; run_id = $env:GITHUB_RUN_ID
    profile = 'NikanHRM-Test'; legacy_fixture = 'existing installation'; details = ''
}
$child = $null
$legacyMutex = $null
$legacyStop = $null
$probeRuntime = $null

function Get-LegacyLinks {
    return @((Join-Path $desktop 'Nikan Training.lnk'), (Join-Path $programs 'Nikan Training/Nikan Training.lnk'), (Join-Path $programs 'Nikan Training/Close Nikan Training.lnk'))
}
function Get-TestLinks {
    return @((Join-Path $desktop 'Nikan HRM Test.lnk'), (Join-Path $programs 'Nikan HRM Test/Nikan HRM Test.lnk'), (Join-Path $programs 'Nikan HRM Test/Close Nikan HRM Test.lnk'))
}
function Get-LegacyFingerprint {
    $entries = [System.Collections.Generic.List[string]]::new()
    foreach ($folder in @($legacyInstall, $legacyData)) {
        if (Test-Path $folder) {
            foreach ($file in Get-ChildItem $folder -File -Recurse | Sort-Object FullName) {
                $entries.Add($file.FullName + '=' + (Get-FileHash $file.FullName -Algorithm SHA256).Hash)
            }
        }
    }
    foreach ($link in (Get-LegacyLinks)) {
        if (Test-Path $link) { $entries.Add($link + '=' + (Get-FileHash $link -Algorithm SHA256).Hash) }
    }
    $key = 'HKCU:/Software/Microsoft/Windows/CurrentVersion/Uninstall/NikanTraining'
    if (Test-Path $key) {
        $entries.Add((Get-ItemProperty $key | Select-Object DisplayName, DisplayVersion, InstallLocation, UninstallString | ConvertTo-Json -Compress))
    }
    return ($entries -join "`n")
}
function Assert-LegacyUnchanged {
    if ((Get-LegacyFingerprint) -cne $script:legacyBefore) { throw 'HRM test changed original installation, data, shortcuts or registration' }
    if ($null -ne $script:legacyStop -and $script:legacyStop.WaitOne(0)) { throw 'Test close command signalled the original desktop stop event' }
}
function Invoke-BundledValidator([string]$Name, [string[]]$ValidatorArguments = @()) {
    $output = & "$install/runtime/python.exe" -I "$pwd/packaging/$Name" @ValidatorArguments 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw "$Name failed under the installed interpreter: $output" }
    Write-Host $output.Trim()
    return $output.Trim()
}
function Assert-PreservedDatabase([string]$Interpreter) {
    $code = 'import sqlite3,sys; c=sqlite3.connect("file:"+sys.argv[1]+"?mode=ro",uri=True); assert c.execute("SELECT value FROM native_preservation_probe WHERE id=1").fetchone()==("synthetic HRM data survives",); assert c.execute("PRAGMA integrity_check").fetchall()==[("ok",)]'
    $output = & $Interpreter -I -c $code "$data/nikan.sqlite3" 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw "Database preservation failed: $output" }
    if ((Get-Content "$data/preservation-test.txt" -Raw).Trim() -ne 'keep synthetic HRM data') { throw 'Preservation marker changed' }
}

try {
    $installer = Join-Path $pwd 'distribution/NikanHRM-Test-Setup.exe'
    $expected = (Get-Content "$installer.sha256").Split(' ')[0]
    if ((Get-FileHash $installer -Algorithm SHA256).Hash.ToLower() -ne $expected) { throw 'Test installer checksum mismatch' }
    $checks.Add('installer SHA-256 matches committed checksum')

    # A clean CI runner gets synthetic original paths/links/registration. Existing
    # installations are fingerprinted as-is; no original executable is launched.
    $result.stage = 'legacy-coexistence-baseline'
    if (!(Test-Path $legacyInstall) -and !(Test-Path $legacyData)) {
        New-Item -ItemType Directory -Force $legacyInstall, $legacyData, (Join-Path $programs 'Nikan Training') | Out-Null
        Set-Content "$legacyInstall/legacy-installation.fixture" 'synthetic original installation: do not change'
        Set-Content "$legacyData/legacy-business-data.fixture" 'synthetic original data: do not change'
        Copy-Item packaging/nikan.ico "$legacyData/holding.ico"
        foreach ($link in (Get-LegacyLinks)) {
            $shortcut = $shell.CreateShortcut($link)
            $shortcut.TargetPath = $env:ComSpec
            $shortcut.IconLocation = "$legacyData\holding.ico,0"
            $shortcut.Save()
        }
        $key = 'HKCU:/Software/Microsoft/Windows/CurrentVersion/Uninstall/NikanTraining'
        New-Item $key -Force | Out-Null
        New-ItemProperty $key -Name DisplayName -Value 'Synthetic legacy Nikan Training fixture' -Force | Out-Null
        New-ItemProperty $key -Name DisplayVersion -Value '0.11.2' -Force | Out-Null
        New-ItemProperty $key -Name InstallLocation -Value $legacyInstall -Force | Out-Null
        $result.legacy_fixture = 'synthetic installation/data/shortcuts/registration; no legacy executable launched'
    }
    $legacyBefore = Get-LegacyFingerprint
    $checks.Add('original paths and shortcuts fingerprinted before test installation')

    $result.stage = 'install'
    $setup = Start-Process $installer -ArgumentList '/S' -Wait -PassThru
    if ($setup.ExitCode -ne 0) { throw "Test installer exit $($setup.ExitCode)" }
    foreach ($file in @('runtime/python.exe', 'runtime/pythonw.exe', 'desktop_test.py', 'stop_hrm_test.py', 'app/hrm_common.py')) {
        if (!(Test-Path "$install/$file")) { throw "Installed payload missing $file" }
    }
    Assert-LegacyUnchanged
    $checks.Add('test installer and runtime coexist with untouched original desktop')

    $result.stage = 'shortcuts'
    foreach ($link in (Get-TestLinks)) {
        if (!(Test-Path $link)) { throw "Test shortcut missing: $link" }
        $shortcut = $shell.CreateShortcut($link)
        if ($shortcut.TargetPath -ine "$install\runtime\pythonw.exe") { throw "Wrong bundled interpreter target: $link" }
        $entry = if ($link -like '*Close Nikan HRM Test.lnk') { 'stop_hrm_test.py' } else { 'desktop_test.py' }
        if ($shortcut.Arguments -notlike "*-I*${entry}*") { throw "Wrong isolated shortcut entry point: $link" }
    }
    $checks.Add('launch and close shortcuts target own interpreter and entry points')

    $result.stage = 'packaged-training-runtime'
    $result.training_runtime = Invoke-BundledValidator 'validate_payload.py' @('--install', $install)
    $checks.Add('retained training, exports, workbook and desktop lifecycle pass with bundled runtime')
    # The retained validator starts its own temporary legacy desktop. Create the
    # legacy-running mutex/event fixture only after that independent lifecycle.
    $legacyMutex = [System.Threading.Mutex]::new($false, 'Local\NikanTrainingDesktop')
    $legacyStop = [System.Threading.EventWaitHandle]::new($false, [System.Threading.EventResetMode]::ManualReset, 'Local\NikanTrainingStop')
    Assert-LegacyUnchanged
    $result.stage = 'packaged-hrm-runtime'
    $result.hrm_runtime = Invoke-BundledValidator 'validate_hrm_payload.py' @('--install', $install, '--report', "$pwd/docs/windows-installer-hrm-result.json")
    $checks.Add('HRM permissions, attachments and backup pass with bundled runtime')

    $result.stage = 'branding-icon'
    $result.branding_runtime = Invoke-BundledValidator 'validate_branding.py' @('--install', $install)
    foreach ($link in (Get-TestLinks)) {
        if ($shell.CreateShortcut($link).IconLocation -ine "$data\holding.ico,0") { throw "Custom icon missing from test shortcut: $link" }
    }
    Assert-LegacyUnchanged
    $checks.Add('test branding updates only test shortcuts; original files and icons stay unchanged')
    Set-Content "$data/preservation-test.txt" 'keep synthetic HRM data'
    $code = 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("CREATE TABLE native_preservation_probe(id INTEGER PRIMARY KEY,value TEXT NOT NULL)"); c.execute("INSERT INTO native_preservation_probe VALUES(1,?)",("synthetic HRM data survives",)); c.commit(); c.close()'
    & "$install/runtime/python.exe" -I -c $code "$data/nikan.sqlite3"
    if ($LASTEXITCODE -ne 0) { throw 'Could not seed synthetic preservation row' }
    $iconBefore = (Get-FileHash "$data/holding.ico" -Algorithm SHA256).Hash

    $result.stage = 'desktop-launch-and-close'
    $oldHeadless = $env:NIKAN_HEADLESS_DESKTOP
    try {
        $env:NIKAN_HEADLESS_DESKTOP = '1'
        $child = Start-Process "$install/runtime/pythonw.exe" -ArgumentList @('-I', "`"$install/desktop_test.py`"") -PassThru
        $deadline = (Get-Date).AddSeconds(30)
        while (!(Test-Path "$data/desktop-endpoint.json") -and (Get-Date) -lt $deadline) {
            if ($child.HasExited) { throw 'Installed test desktop exited before readiness' }
            Start-Sleep -Milliseconds 200
        }
        if (!(Test-Path "$data/desktop-endpoint.json")) { throw 'Installed test desktop did not publish readiness' }
        $url = (Get-Content "$data/desktop-endpoint.json" -Raw | ConvertFrom-Json).url
        if ($url -notlike 'http://127.0.0.1:*' -or (Invoke-RestMethod "$url/api/status").setup_required -isnot [bool]) { throw 'Test endpoint did not respond correctly' }
        $second = Start-Process "$install/runtime/pythonw.exe" -ArgumentList @('-I', "`"$install/desktop_test.py`"") -PassThru
        if (!$second.WaitForExit(15000) -or $second.ExitCode -ne 0 -or $child.HasExited) { throw 'Test single-instance handoff failed' }
        & "$install/runtime/python.exe" -I "$install/stop_hrm_test.py"
        if ($LASTEXITCODE -ne 0 -or !$child.WaitForExit(15000) -or $child.ExitCode -ne 0) { throw 'Test close command failed to stop only the test desktop' }
        if (Test-Path "$data/desktop-endpoint.json") { throw 'Test close left stale endpoint' }
    } finally { $env:NIKAN_HEADLESS_DESKTOP = $oldHeadless }
    Assert-LegacyUnchanged
    $checks.Add('installed entry point, single instance and stop event work independently of original mutex/event')

    $result.stage = 'upgrade'
    $upgrade = Start-Process $installer -ArgumentList '/S' -Wait -PassThru
    if ($upgrade.ExitCode -ne 0) { throw "Test upgrade exit $($upgrade.ExitCode)" }
    Assert-PreservedDatabase "$install/runtime/python.exe"
    if ((Get-FileHash "$data/holding.ico" -Algorithm SHA256).Hash -ne $iconBefore) { throw 'Upgrade changed branding bytes' }
    if ($shell.CreateShortcut((Join-Path $desktop 'Nikan HRM Test.lnk')).IconLocation -ine "$data\holding.ico,0") { throw 'Upgrade lost test branding icon' }
    Assert-LegacyUnchanged
    $checks.Add('upgrade preserves SQLite row/integrity, marker, icon and original installation')

    $result.stage = 'uninstall'
    # Copy the already bundled runtime for read-only verification after uninstall.
    $temporary = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [IO.Path]::GetTempPath() }
    $probeRuntime = Join-Path $temporary ('nikan-test-runtime-' + [guid]::NewGuid())
    Copy-Item "$install/runtime" $probeRuntime -Recurse
    $uninstall = Start-Process "$install/Uninstall.exe" -ArgumentList '/S' -Wait -PassThru
    if ($uninstall.ExitCode -ne 0) { throw "Test uninstall exit $($uninstall.ExitCode)" }
    Assert-PreservedDatabase "$probeRuntime/python.exe"
    if (!(Test-Path "$data/holding.ico")) { throw 'Uninstall removed custom test branding' }
    foreach ($link in (Get-TestLinks)) { if (Test-Path $link) { throw "Uninstall left test shortcut: $link" } }
    if (Test-Path 'HKCU:/Software/Microsoft/Windows/CurrentVersion/Uninstall/NikanHRM-Test') { throw 'Uninstall left test registration' }
    Assert-LegacyUnchanged
    $checks.Add('uninstall removes test links/registration and preserves database, branding and original installation')
    $result.stage = 'completed'; $result.status = 'passed'
} catch {
    $result.status = 'failed'; $result.details = $_.Exception.Message
    throw
} finally {
    if ($null -ne $child -and !$child.HasExited) { Stop-Process -Id $child.Id -Force -ErrorAction SilentlyContinue }
    if ($null -ne $probeRuntime -and (Test-Path $probeRuntime)) { Remove-Item $probeRuntime -Recurse -Force -ErrorAction SilentlyContinue }
    if ($null -ne $legacyStop) { $legacyStop.Dispose() }
    if ($null -ne $legacyMutex) { $legacyMutex.Dispose() }
    $result.checks = $checks.ToArray()
    $failed = if ($result.status -eq 'failed') { 1 } else { 0 }
    $result.test_counts = @{ total = $checks.Count + $failed; passed = $checks.Count; failed = $failed }
    New-Item -ItemType Directory -Force docs | Out-Null
    $result | ConvertTo-Json -Depth 6 | Set-Content docs/windows-installer-result.json -Encoding utf8
}

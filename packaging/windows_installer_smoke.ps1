$ErrorActionPreference = 'Stop'
$result = [ordered]@{ status = 'running'; stage = 'checksum'; platform = [Environment]::OSVersion.VersionString; interpreter = ''; details = '' }
try {
    $expected = (Get-Content distribution/NikanTraining-Setup.exe.sha256).Split(' ')[0]
    if ((Get-FileHash distribution/NikanTraining-Setup.exe -Algorithm SHA256).Hash.ToLower() -ne $expected) { throw 'Installer checksum mismatch' }
    $result.stage = 'install'
    $setup = Start-Process -FilePath "$pwd/distribution/NikanTraining-Setup.exe" -ArgumentList '/S' -Wait -PassThru
    if ($setup.ExitCode -ne 0) { throw "Installer exit $($setup.ExitCode)" }
    $install = Join-Path $env:LOCALAPPDATA 'Programs/NikanTraining'
    if (!(Test-Path "$install/runtime/pythonw.exe")) { throw 'Bundled interpreter missing' }
    $result.stage = 'shortcuts'
    if (!(Test-Path ([Environment]::GetFolderPath('Desktop') + '/Nikan Training.lnk'))) { throw 'Desktop shortcut missing' }
    if (!(Test-Path ([Environment]::GetFolderPath('Programs') + '/Nikan Training/Nikan Training.lnk'))) { throw 'Start menu shortcut missing' }
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut(([Environment]::GetFolderPath('Desktop') + '/Nikan Training.lnk'))
    if ($shortcut.TargetPath -ne "$install\runtime\pythonw.exe") { throw 'Shortcut interpreter target incorrect' }
    $result.stage = 'packaged-runtime'
    Copy-Item packaging/validate_payload.py "$install/validate_payload.py"
    $output = & "$install/runtime/python.exe" -I "$install/validate_payload.py" 2>&1 | Out-String
    $result.interpreter = $output.Trim()
    if ($LASTEXITCODE -ne 0) { throw "Packaged runtime validation failed: $output" }
    $data = Join-Path $env:LOCALAPPDATA 'NikanTraining'
    New-Item -ItemType Directory -Force $data | Out-Null
    Set-Content "$data/preservation-test.txt" 'keep business data'
    $result.stage = 'upgrade'
    $upgrade = Start-Process -FilePath "$pwd/distribution/NikanTraining-Setup.exe" -ArgumentList '/S' -Wait -PassThru
    if ($upgrade.ExitCode -ne 0 -or !(Test-Path "$data/preservation-test.txt")) { throw 'Upgrade preservation failed' }
    $result.stage = 'uninstall'
    $uninstall = Start-Process -FilePath "$install/Uninstall.exe" -ArgumentList '/S' -Wait -PassThru
    if ($uninstall.ExitCode -ne 0 -or !(Test-Path "$data/preservation-test.txt")) { throw 'Uninstall preservation failed' }
    if (Test-Path ([Environment]::GetFolderPath('Desktop') + '/Nikan Training.lnk')) { throw 'Uninstall left desktop shortcut' }
    $result.stage = 'completed'; $result.status = 'passed'
} catch {
    $result.status = 'failed'; $result.details = $_.Exception.Message
    throw
} finally {
    New-Item -ItemType Directory -Force docs | Out-Null
    $result | ConvertTo-Json | Set-Content docs/windows-installer-result.json -Encoding utf8
}

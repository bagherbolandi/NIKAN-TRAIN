if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
 Start-Process powershell.exe -Verb RunAs -ArgumentList ('-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '"') -Wait
 exit
}
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
$archive=New-Object System.Windows.Forms.OpenFileDialog
$archive.Title='Select encrypted Nikan backup';$archive.Filter='Nikan encrypted backup (*.nikbak)|*.nikbak'
if ($archive.ShowDialog() -ne 'OK') { exit 0 }
$key=New-Object System.Windows.Forms.OpenFileDialog
$key.Title='Select the matching recovery key';$key.Filter='Recovery key (*.key)|*.key'
if ($key.ShowDialog() -ne 'OK') { exit 0 }
$confirm=[System.Windows.Forms.MessageBox]::Show('The server will stop. A verified safety backup will be created before replacing the database. Continue?','Nikan verified recovery','YesNo','Warning')
if ($confirm -ne 'Yes') { exit 0 }
try {
 & "$PSScriptRoot/stop_server.ps1" -InstallDir $PSScriptRoot
 if (!$?) { throw 'Server stop failed' }
 & "$PSScriptRoot/runtime/python.exe" -I "$PSScriptRoot/recovery.py" $archive.FileName --key $key.FileName --target "$env:ProgramData/NikanTrainingServer" --replace
 if ($LASTEXITCODE -ne 0) { throw 'Recovery failed; original database preserved. Inspect the displayed diagnosis.' }
 & "$PSScriptRoot/start_server.ps1"
 [System.Windows.Forms.MessageBox]::Show('Recovery verified and completed. The server has restarted.','Nikan recovery') | Out-Null
} catch {
 [System.Windows.Forms.MessageBox]::Show($_.Exception.Message,'Nikan recovery failed','OK','Error') | Out-Null
 throw
}

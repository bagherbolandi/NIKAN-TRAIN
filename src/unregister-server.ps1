#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
foreach ($taskName in @('NikanTrainingServer','NikanTrainingBackup')) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $taskName
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
}
Get-NetFirewallRule -Name 'NikanTrainingIntranet' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Write-Host 'Startup tasks removed. Database and backups were preserved.'

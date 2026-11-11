param([Parameter(Mandatory=$true)][string]$InstallDir)
$ErrorActionPreference = 'Stop'
try {
    $target = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\') + '\'
    $running = Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -and $_.ExecutablePath.StartsWith($target, [StringComparison]::OrdinalIgnoreCase)
    }
    if ($running) {
        Write-Output 'Nikan Training is still running. Close it using Close Application, or stop the NikanTrainingServer scheduled task, then run setup again. No files have been replaced.'
        exit 20
    }
    if (Test-Path -LiteralPath $target) {
        foreach ($file in Get-ChildItem -LiteralPath $target -File -Recurse) {
            $handle = $null
            try {
                $handle = [IO.File]::Open($file.FullName, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
            } catch {
                Write-Output ('Cannot update a locked or read-only file: ' + $file.Name + '. Close Nikan Training and try again. No files have been replaced.')
                exit 21
            } finally {
                if ($null -ne $handle) { $handle.Dispose() }
            }
        }
    }
    Write-Output 'Installation preflight passed.'
    exit 0
} catch {
    Write-Output ('Installation preflight failed: ' + $_.Exception.Message)
    exit 22
}

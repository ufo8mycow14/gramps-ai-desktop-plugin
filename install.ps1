param(
    [ValidateSet('6.0', '6.1')][string]$GrampsVersion = '6.1',
    [string]$Python = '',
    [string]$Project = $PSScriptRoot,
    [string]$AddonDirectory = '',
    [switch]$Standalone,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
if (-not $Python) {
    $candidate = Get-Command python -ErrorAction SilentlyContinue
    if ($candidate) { $Python = $candidate.Source }
    else {
        $launcher = Get-Command py -ErrorAction SilentlyContinue
        if ($launcher) {
            $Python = & $launcher.Source -3 -c 'import sys; print(sys.executable)'
            if ($LASTEXITCODE -ne 0) { throw 'Python discovery failed; pass -Python with an executable path.' }
        }
    }
}
if (-not $Python -or -not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'Python 3.11+ is required; pass -Python with its executable path.'
}
$installationArgs = @((Join-Path $PSScriptRoot 'install.py'), '--project', $Project, '--gramps-version', $GrampsVersion)
if ($Standalone) { $installationArgs += '--standalone' }
if ($DryRun) { $installationArgs += '--dry-run' }
if ($AddonDirectory) { $installationArgs += @('--addon-dir', $AddonDirectory) }
& $Python @installationArgs
if ($LASTEXITCODE -ne 0) { throw 'Gramps Desktop plugin installation failed.' }
Write-Output 'Keep this checkout in place. Reconnect client tools after installation; save work before reopening Gramps.'

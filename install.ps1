param(
    [string]$GrampsVersion = '6.1',
    [string]$Python = '',
    [string]$Project = $PSScriptRoot,
    [switch]$Standalone
)
$ErrorActionPreference = 'Stop'
if ($GrampsVersion -ne '6.1') { throw 'This add-on is verified for Gramps 6.1 only.' }
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
    throw 'Install Python 3.11+ or pass -Python with its executable path.'
}
$Python = (Resolve-Path -LiteralPath $Python).Path
& $Python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required.' }
if (-not (Test-Path -LiteralPath $Project -PathType Container)) { throw 'The target workspace must exist.' }
$projectRoot = (Resolve-Path -LiteralPath $Project).Path
if (-not $env:APPDATA) { throw 'This installer requires Windows APPDATA.' }
if (-not $Standalone -and -not (Get-Command codex -ErrorAction SilentlyContinue)) {
    throw 'Codex CLI with plugin commands is required, or use -Standalone for an MCP-only client.'
}
$pluginRoot = Join-Path $env:APPDATA ('gramps\gramps' + $GrampsVersion.Replace('.', '') + '\plugins\DesktopMCPControl')
& $Python -m py_compile (Join-Path $PSScriptRoot 'server.py') (Join-Path $PSScriptRoot 'desktop_bridge.py') (Join-Path $PSScriptRoot 'support.py') (Join-Path $PSScriptRoot 'ui_support.py') (Join-Path $PSScriptRoot 'configure.py')
if ($LASTEXITCODE -ne 0) { throw 'Python compilation failed.' }
foreach ($name in @('DesktopControl.py', 'DesktopControl.gpr.py')) {
    $source = Join-Path $PSScriptRoot $name
    $target = Join-Path $pluginRoot $name
    if (Test-Path -LiteralPath $target) {
        $previous = Get-Content -LiteralPath $target -Raw
        if ($previous -notmatch 'Desktop MCP|desktop_bridge|Desktop MCP Control') {
            throw "Existing unrelated add-on file preserved: $target"
        }
    }
}
New-Item -ItemType Directory -Path $pluginRoot -Force | Out-Null
$configurationArgs = @('--project', $projectRoot, '--tools', $PSScriptRoot, '--python', $Python,
    '--addon', $pluginRoot, '--plugin-id', 'gramps-desktop@gramps-desktop-plugins')
& $Python (Join-Path $PSScriptRoot 'configure.py') @configurationArgs --prepare-only
if ($LASTEXITCODE -ne 0) { throw 'Installer configuration preparation failed.' }
foreach ($name in @('DesktopControl.py', 'DesktopControl.gpr.py')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination (Join-Path $pluginRoot $name)
}
$config = Join-Path $projectRoot '.codex\config.toml'
if (-not $Standalone) {
    & codex plugin marketplace add $PSScriptRoot --json
    if ($LASTEXITCODE -ne 0) { throw 'Local marketplace registration failed.' }
    & codex plugin add gramps-desktop@gramps-desktop-plugins --json
    if ($LASTEXITCODE -ne 0) { throw 'Local plugin installation failed.' }
}
$modeArgs = if ($Standalone) { @('--standalone') } else { @() }
& $Python (Join-Path $PSScriptRoot 'configure.py') @configurationArgs @modeArgs
if ($LASTEXITCODE -ne 0) { throw 'Installer configuration update failed.' }
Write-Output "Installed Gramps Desktop plugin bridge: $pluginRoot"
Write-Output "Project plugin configuration: $config"
Write-Output 'Keep this checkout in place. Save work, reopen Gramps normally, and reconnect the client tools.'

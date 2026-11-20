param(
    [string]$Python = '',
    [switch]$RebuildFrontend
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Program $($Arguments -join ' ')" }
}
$WheelFolder = Join-Path $PSScriptRoot 'offline\python'
$LockFile = Join-Path $PSScriptRoot 'offline\requirements-win-py311.lock.txt'
if (-not (Test-Path -LiteralPath $LockFile) -or -not (Test-Path -LiteralPath $WheelFolder)) {
    throw 'Offline packages are missing. Copy the complete project including offline\.'
}
$VenvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $VenvPython)) {
    $PythonArgs = @()
    if (-not $Python) {
        if (Get-Command py.exe -ErrorAction SilentlyContinue) {
            $Python = 'py.exe'
            $PythonArgs = @('-3.11')
        } else { $Python = 'python.exe' }
    }
    Invoke-Checked $Python ($PythonArgs + @('-c', 'import sys, struct; assert sys.version_info[:2] == (3,11) and struct.calcsize(''P'') == 8, ''Install 64-bit Python 3.11 for this offline bundle'''))
    Write-Host 'Creating isolated local environment...' -ForegroundColor Cyan
    Invoke-Checked $Python ($PythonArgs + @('-m', 'venv', '.venv'))
}
Invoke-Checked $VenvPython @('-c', 'import sys, struct; assert sys.version_info[:2] == (3,11) and struct.calcsize(''P'') == 8, ''This bundle needs 64-bit Python 3.11''; assert sys.prefix != sys.base_prefix, ''Expected a virtual environment''; assert ''include-system-site-packages = false'' in open(sys.prefix + ''/pyvenv.cfg'').read().lower(), ''Existing venv shares global packages; rename .venv and rerun setup''')
Write-Host 'Installing bundled Python packages (offline)...' -ForegroundColor Cyan
Invoke-Checked $VenvPython @('-m', 'pip', '--isolated', '--disable-pip-version-check', 'install', '--no-index', '--no-cache-dir', '--only-binary=:all:', '--find-links', $WheelFolder, '-r', $LockFile)
Invoke-Checked $VenvPython @('-m', 'pip', '--isolated', '--disable-pip-version-check', 'check')
Invoke-Checked $VenvPython @('prepare_models.py')
if ($RebuildFrontend) {
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw 'Node.js/npm is required only to rebuild the frontend.' }
    Push-Location frontend
    try {
        Invoke-Checked 'npm.cmd' @('ci', '--offline', '--cache', (Join-Path $PSScriptRoot 'offline\npm-cache'), '--no-audit', '--no-fund')
        Invoke-Checked 'npm.cmd' @('run', 'build')
    } finally { Pop-Location }
}
if (-not (Test-Path -LiteralPath 'frontend\dist\index.html')) {
    throw 'Bundled frontend missing. Restore frontend\dist or run setup.ps1 -RebuildFrontend with Node.js installed.'
}
Write-Host 'Offline setup complete. Run .\Start.bat or .\run.ps1' -ForegroundColor Green

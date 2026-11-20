param([int]$Port = 5050, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe') -or -not (Test-Path -LiteralPath 'frontend\dist\index.html')) { & (Join-Path $PSScriptRoot 'setup.ps1') }
$Arguments = @('launch.py', '--port', "$Port")
if ($NoBrowser) { $Arguments += '--no-browser' }
& (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') @Arguments
if ($LASTEXITCODE -ne 0) { throw "Application exited with code $LASTEXITCODE" }

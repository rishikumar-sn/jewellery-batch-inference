[CmdletBinding(SupportsShouldProcess = $true)]
param([ValidateRange(1, 65535)][int]$Port = 5050)

$ErrorActionPreference = 'Stop'
$PythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$Listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object { $_.LocalPort -eq $Port })
if (-not $Listeners.Count) {
    Write-Host "AU Inspection is not running on port $Port."
    return
}

foreach ($ServerId in ($Listeners.OwningProcess | Select-Object -Unique)) {
    $Server = Get-CimInstance Win32_Process -Filter "ProcessId = $ServerId"
    if (-not $Server -or $Server.CommandLine -notmatch '(?i)(?:^|[\s"\\/])launch\.py(?:[\s"]|$)') {
        throw "Port $Port belongs to another application; no process was stopped."
    }

    # Windows venv Python starts a child using the base interpreter.
    $Launcher = $Server
    if ($Launcher.ExecutablePath -ne $PythonPath) {
        $Launcher = Get-CimInstance Win32_Process -Filter "ProcessId = $($Server.ParentProcessId)"
    }
    if (-not $Launcher -or $Launcher.ExecutablePath -ne $PythonPath -or $Launcher.CommandLine -notmatch '(?i)(?:^|[\s"\\/])launch\.py(?:[\s"]|$)') {
        throw "The server on port $Port could not be verified as this project's launcher; no process was stopped."
    }

    if ($PSCmdlet.ShouldProcess("AU Inspection on port $Port (PID $($Launcher.ProcessId))", 'Stop server and its child processes')) {
        & taskkill.exe /PID $Launcher.ProcessId /T /F
        if ($LASTEXITCODE -ne 0) { throw "Could not stop AU Inspection (exit code $LASTEXITCODE)." }
        Write-Host "AU Inspection stopped on port $Port."
    }
}

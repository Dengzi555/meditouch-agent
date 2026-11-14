param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$url = 'http://127.0.0.1:8765/'

function Test-MediTouch {
    try {
        $health = Invoke-RestMethod -Uri ($url + 'healthz') -TimeoutSec 2
        return $health.status -eq 'ok' -and $health.schema_version -eq 'pharmacy.ui.v1'
    } catch { return $false }
}

try {
    if (-not (Test-MediTouch)) {
        if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) {
            throw 'Port 8765 is occupied by another service. Close it or check the project port.'
        }
        foreach ($relative in @('.venv-agent\Scripts\python.exe', 'start_with_model.ps1', 'web\dist\index.html')) {
            if (-not (Test-Path -LiteralPath (Join-Path $projectRoot $relative))) {
                throw ('Required file missing: ' + $relative + '. Restore the environment or build the frontend first.')
            }
        }
        $logDir = Join-Path $projectRoot 'data\logs'
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
        $serviceScript = Join-Path $PSScriptRoot 'start_local_service.ps1'
        $process = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') `
            -ArgumentList ('-NoProfile -ExecutionPolicy Bypass -File "' + $serviceScript + '"') `
            -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $logDir ($stamp + '-out.log')) `
            -RedirectStandardError (Join-Path $logDir ($stamp + '-err.log'))
        $ready = $false
        $deadline = [DateTime]::UtcNow.AddSeconds(45)
        while ([DateTime]::UtcNow -lt $deadline) {
            if (Test-MediTouch) { $ready = $true; break }
            if ($process.HasExited) { break }
            Start-Sleep -Milliseconds 400
        }
        if (-not $ready) {
            throw ('Startup did not complete. Check logs: ' + $logDir)
        }
    }
    if (-not $NoBrowser) { Start-Process $url }
    Write-Host ('MediTouch is ready: ' + $url)
    exit 0
} catch {
    Write-Host ('Startup failed: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}

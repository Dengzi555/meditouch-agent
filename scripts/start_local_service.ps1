$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:HOST = '127.0.0.1'
$env:PORT = '8765'
if (-not $env:MED_AGENT_INVENTORY_DB) {
    $mock = Join-Path $projectRoot 'data\pharmacy_inventory_mock.db'
    $real = Join-Path $projectRoot 'data\pharmacy_inventory.db'
    if (Test-Path -LiteralPath $mock) {
        $env:MED_AGENT_INVENTORY_DB = $mock
    } elseif (Test-Path -LiteralPath $real) {
        $env:MED_AGENT_INVENTORY_DB = $real
    }
}
if (-not $env:MED_AGENT_INVENTORY_STORE_ID) { $env:MED_AGENT_INVENTORY_STORE_ID = 'local-store' }
& (Join-Path $projectRoot 'start_with_model.ps1') -Action Agent
exit $LASTEXITCODE

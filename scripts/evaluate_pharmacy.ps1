param([int]$Limit = 30)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$projectRoot = Split-Path -Parent $PSScriptRoot
$keyFile = Join-Path $projectRoot 'data\model-key.dpapi'
$configFile = Join-Path $projectRoot 'data\model-config.json'
$loadedKey = $false
$loadedSearchKey = $false
$secureKey = $null
try {
    if (-not $env:MED_AGENT_API_KEY -and (Test-Path -LiteralPath $keyFile) -and (Test-Path -LiteralPath $configFile)) {
        Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -Force
        $config = Get-Content -LiteralPath $configFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $secureKey = ConvertTo-SecureString (Get-Content -LiteralPath $keyFile -Raw).Trim()
        $env:MED_AGENT_API_KEY = [System.Net.NetworkCredential]::new('', $secureKey).Password
        $env:MED_AGENT_BASE_URL = $config.base_url
        $env:MED_AGENT_MODEL = $config.model
        $loadedKey = $true
    }
    $searchKeyFile = Join-Path $projectRoot 'data\deepseek-search-key.dpapi'
    if (-not $env:MED_AGENT_DEEPSEEK_SEARCH_KEY -and (Test-Path -LiteralPath $searchKeyFile)) {
        Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -Force
        $searchKey = ConvertTo-SecureString (Get-Content -LiteralPath $searchKeyFile -Raw).Trim()
        try {
            $env:MED_AGENT_DEEPSEEK_SEARCH_KEY = [System.Net.NetworkCredential]::new('', $searchKey).Password
            $loadedSearchKey = $true
        } finally { $searchKey.Dispose() }
    }
    Push-Location -LiteralPath $projectRoot
    try {
        & (Join-Path $projectRoot '.venv-agent\Scripts\python.exe') (Join-Path $projectRoot 'evaluate.py') --limit $Limit
        $evalExitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
} finally {
    if ($loadedSearchKey) { Remove-Item Env:MED_AGENT_DEEPSEEK_SEARCH_KEY -ErrorAction SilentlyContinue }
    if ($secureKey) { $secureKey.Dispose() }
    if ($loadedKey) {
        Remove-Item Env:MED_AGENT_API_KEY -ErrorAction SilentlyContinue
        Remove-Item Env:MED_AGENT_BASE_URL -ErrorAction SilentlyContinue
        Remove-Item Env:MED_AGENT_MODEL -ErrorAction SilentlyContinue
    }
}
exit $evalExitCode

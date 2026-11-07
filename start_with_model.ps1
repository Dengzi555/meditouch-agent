param(
    [ValidateSet('Server', 'Check', 'Claw', 'Agent', 'AgentCheck', 'AgentEval', 'AgentPreview', 'PatientEval')]
    [string]$Action = 'Server',
    [string[]]$EvalArguments = @('--help')
)
$ErrorActionPreference = 'Stop'
# Use the security module shipped with this host, even when PSModulePath
# contains modules from a different PowerShell installation.
$securityModule = Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1'
Import-Module -Name $securityModule -ErrorAction Stop
$modelSettings = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'data\model-config.json') -Raw | ConvertFrom-Json
$protectedKey = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'data\model-key.dpapi') -Raw
$secureKey = ConvertTo-SecureString $protectedKey.Trim()
$keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $env:MED_AGENT_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
}
$env:MED_AGENT_BASE_URL = $modelSettings.base_url
$env:MED_AGENT_MODEL = $modelSettings.model
$env:CLAW_EVAL_API_KEY = $env:MED_AGENT_API_KEY
$env:CLAW_EVAL_BASE_URL = $env:MED_AGENT_BASE_URL
$env:CLAW_EVAL_MODEL = $env:MED_AGENT_MODEL
$env:PYTHONUTF8 = '1'
try {
    if ($Action -eq 'Agent' -and -not $env:MED_AGENT_DEEPSEEK_SEARCH_KEY) {
        $searchKeyPath = Join-Path $PSScriptRoot 'data\deepseek-search-key.dpapi'
        if (Test-Path -LiteralPath $searchKeyPath) {
            $searchKey = ConvertTo-SecureString (Get-Content -LiteralPath $searchKeyPath -Raw).Trim()
            try {
                $env:MED_AGENT_DEEPSEEK_SEARCH_KEY = [System.Net.NetworkCredential]::new('', $searchKey).Password
                $loadedSearchKey = $true
            } finally {
                $searchKey.Dispose()
            }
        }
    }
    if ($Action -eq 'Claw') {
        & (Join-Path $PSScriptRoot '.venv-eval\Scripts\python.exe') -m claw_eval.cli @EvalArguments
    } elseif ($Action -in @('Agent', 'AgentCheck', 'AgentEval', 'AgentPreview', 'PatientEval')) {
        $agentPython = Join-Path $PSScriptRoot '.venv-agent\Scripts\python.exe'
        if ($Action -eq 'Agent') {
            & $agentPython (Join-Path $PSScriptRoot 'server.py')
        } elseif ($Action -eq 'AgentCheck') {
            & $agentPython (Join-Path $PSScriptRoot 'agent_acceptance.py') --ping
        } elseif ($Action -eq 'AgentPreview') {
            & $agentPython (Join-Path $PSScriptRoot 'agent_acceptance.py') --real-preview
        } elseif ($Action -eq 'PatientEval') {
            & $agentPython (Join-Path $PSScriptRoot 'patient_acceptance.py') --live
        } else {
            & $agentPython (Join-Path $PSScriptRoot 'agent_acceptance.py') --live
        }
    } elseif ($Action -eq 'Check') {
        & python (Join-Path $PSScriptRoot 'check_model_connection.py')
    } else {
        & python (Join-Path $PSScriptRoot 'server.py')
    }
    $modelExitCode = $LASTEXITCODE
} finally {
    Remove-Item Env:MED_AGENT_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:CLAW_EVAL_API_KEY -ErrorAction SilentlyContinue
    if ($loadedSearchKey) {
        Remove-Item Env:MED_AGENT_DEEPSEEK_SEARCH_KEY -ErrorAction SilentlyContinue
    }
    $secureKey.Dispose()
}
exit $modelExitCode

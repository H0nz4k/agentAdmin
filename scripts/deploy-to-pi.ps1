# Deploy agentAdmin skriptů na Raspberry Pi (čte config.yaml)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Host "Chybí .venv — nejdřív: python -m venv .venv; .venv\Scripts\pip install -r requirements.txt"
    exit 1
}

& $Python (Join-Path $PSScriptRoot "deploy_pi.py") @args
exit $LASTEXITCODE

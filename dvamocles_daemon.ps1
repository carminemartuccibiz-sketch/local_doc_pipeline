# Local AI Orchestrator — avvio server UI (blueprint)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".\.venv\Scripts\Activate.ps1")) {
    Write-Host "[SETUP] Creazione venv..."
    python -m venv .venv
}

& ".\.venv\Scripts\Activate.ps1"
pip install -r requirements.txt -q

Write-Host "`n[ORCHESTRATOR] http://127.0.0.1:7842`n"
python server.py

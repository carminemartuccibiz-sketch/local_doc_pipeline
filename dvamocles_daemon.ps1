# Local AI Orchestrator — avvio server UI (blueprint)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    & "$PSScriptRoot\scripts\setup_venv.ps1"
}

& ".\.venv\Scripts\Activate.ps1"
python -m pip install -r requirements.txt -q

Write-Host "`n[ORCHESTRATOR] http://127.0.0.1:7842`n"
python server.py

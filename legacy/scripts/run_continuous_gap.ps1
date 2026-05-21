# Gap analysis continua — 1 file alla volta fino a fine coda
# Uso: .\run_continuous_gap.ps1
#      .\run_continuous_gap.ps1 -MaxRounds 5   (test)
# Ctrl+C ferma; pipeline_state.json riprende al prossimo avvio

param(
    [int]$MaxRounds = 0,
    [switch]$ForceAllmSync
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

& ".\.venv\Scripts\Activate.ps1"

$argsList = @("orchestrator.py", "--skip-ingest", "--continuous", "--limit", "1")
if ($MaxRounds -gt 0) { $argsList += "--max-rounds", $MaxRounds }
if ($ForceAllmSync) { $argsList += "--force-allm-sync" }

Write-Host "`n[RUN CONTINUO] python $($argsList -join ' ')`n"
python @argsList
exit $LASTEXITCODE

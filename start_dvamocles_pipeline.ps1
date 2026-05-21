# DVAMOCLES SWORD — avvio pipeline (PowerShell)
# Esegui da questa cartella (repo software): .\start_dvamocles_pipeline.ps1

param(
    [switch]$ForceAllmSync,
    [switch]$SkipIngest,
    [switch]$ResetState,
    [int]$Limit = 1,
    [switch]$FullIngest,
    [switch]$SingleRun,
    [int]$MaxRounds = 0
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".\.venv\Scripts\Activate.ps1")) {
    Write-Host "[SETUP] Creazione venv..."
    python -m venv .venv
}

& ".\.venv\Scripts\Activate.ps1"
pip install -r requirements.txt -q
pip install tqdm -q

Write-Host "`n[PRE-VOL0] Verifica server locali...`n"
try {
    $null = Invoke-WebRequest -Uri "http://localhost:1234/v1/models" -UseBasicParsing -TimeoutSec 10
    Write-Host "  OK  LM Studio      (porta 1234)"
} catch {
    Write-Host "  ERRORE: LM Studio non raggiungibile su http://localhost:1234"
    exit 2
}

try {
    $null = Invoke-WebRequest -Uri "http://localhost:3001/api/ping" -UseBasicParsing -TimeoutSec 10
    Write-Host "  OK  AnythingLLM    (porta 3001)"
} catch {
    Write-Host "  ERRORE: AnythingLLM non raggiungibile su http://localhost:3001"
    exit 2
}

$argsList = @("orchestrator.py")
if (-not $FullIngest) { $argsList += "--skip-ingest" }
$argsList += "--limit", $Limit
if (-not $SingleRun) { $argsList += "--continuous" }
if ($MaxRounds -gt 0) { $argsList += "--max-rounds", $MaxRounds }
if ($ForceAllmSync) { $argsList += "--force-allm-sync" }
if ($ResetState) { $argsList += "--reset-state" }
if (-not $SingleRun) {
    Write-Host "  Modalita: CONTINUA (1 file alla volta, prossimo automatico). -SingleRun per un solo file."
}

Write-Host "`n[RUN] python $($argsList -join ' ')`n"
python @argsList
exit $LASTEXITCODE

# Reset completo sessione gap + avvio test (3 file)
# Uso: .\reset_and_restart.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "`n[1/4] Reset state, report, cache SOT..." -ForegroundColor Cyan
python cli.py reset-gap --keep-allm-cache

Write-Host "`n[2/4] Pre-volo..." -ForegroundColor Cyan
python cli.py check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n[3/4] Avvio gap CONTINUO (1 file/iter, fino a fine coda)..." -ForegroundColor Cyan
Write-Host "  Ctrl+C per fermare — state salva il progresso`n"
python orchestrator.py --skip-ingest --continuous --limit 1

exit $LASTEXITCODE

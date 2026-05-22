# Setup / riparazione ambiente virtuale .venv (Local AI Orchestrator)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

$VenvPath = Join-Path $PWD ".venv"
$TypoPath = Join-Path $PWD ".vnev"

# Corregge cartella creata per errore di battitura (.vnev -> .venv)
if ((Test-Path $TypoPath) -and -not (Test-Path $VenvPath)) {
    Write-Host '[FIX] Rinomino .vnev -> .venv'
    Rename-Item -Path $TypoPath -NewName ".venv"
} elseif ((Test-Path $TypoPath) -and (Test-Path $VenvPath)) {
    Write-Host '[WARN] Esistono sia .venv che .vnev - uso .venv; elimina .vnev se duplicato'
}

function Get-PythonLauncher {
    # pywebview/pythonnet: preferire 3.10-3.12 (non 3.14)
    $candidates = @(
        @{ Args = @("-3.10"); Label = "Python 3.10" },
        @{ Args = @("-3.11"); Label = "Python 3.11" },
        @{ Args = @("-3.12"); Label = "Python 3.12" },
        @{ Args = @(); Label = "Python default" }
    )
    foreach ($c in $candidates) {
        try {
            if ($c.Args.Count -gt 0) {
                $ver = & py @($c.Args + "-c", "import sys; print(sys.version_info[:2])") 2>$null
                if ($LASTEXITCODE -ne 0) { continue }
            } else {
                $ver = & python -c "import sys; print(sys.version_info[:2])" 2>$null
                if ($LASTEXITCODE -ne 0) { continue }
            }
            if ($ver -match "3\.14") {
                Write-Host ('[SKIP] ' + $c.Label + " e' 3.14 (pywebview non supportato)")
                continue
            }
            if ($c.Args.Count -gt 0) {
                return @{ Cmd = "py"; Args = $c.Args; Label = $c.Label }
            }
            return @{ Cmd = "python"; Args = @(); Label = $c.Label }
        } catch {
            continue
        }
    }
    return $null
}

$py = Get-PythonLauncher
if (-not $py) {
    Write-Error "Nessun Python 3.10-3.12 trovato. Installa Python 3.10 da python.org"
}

Write-Host ('[SETUP] Usando ' + $py.Label)

$recreate = $false
if ($env:FORCE_VENV_RECREATE -eq "1") { $recreate = $true }
if ((Test-Path $VenvPath) -and -not (Test-Path (Join-Path $VenvPath "Scripts\python.exe"))) {
    $recreate = $true
}
if ((Test-Path $VenvPath) -and $recreate) {
    $bak = ".venv.bak." + (Get-Date -Format "yyyyMMdd_HHmmss")
    Write-Host ('[SETUP] Sposto .venv corrotto in ' + $bak)
    try {
        Remove-Item -Recurse -Force $VenvPath -ErrorAction Stop
    } catch {
        Rename-Item -Path $VenvPath -NewName $bak -Force
    }
}

if (-not (Test-Path $VenvPath)) {
    Write-Host '[SETUP] Creazione .venv...'
    if ($py.Args.Count -gt 0) {
        & py @($py.Args + "-m", "venv", ".venv")
    } else {
        & python -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
}

$venvPython = Join-Path $VenvPath "Scripts\python.exe"
& $venvPython -m pip install --upgrade pip wheel
& $venvPython -m pip install -r requirements.txt

Write-Host ""
& $venvPython -c "import flask, httpx; print('OK flask httpx')"
$null = & $venvPython -c "import webview; print('OK pywebview')" 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host 'OK pywebview'
} else {
    Write-Host 'WARN pywebview non installato - usa python server.py'
}

Write-Host ""
Write-Host 'Attiva con:  .\.venv\Scripts\Activate.ps1'
Write-Host 'Avvio UI:    .\dvamocles_daemon.ps1'

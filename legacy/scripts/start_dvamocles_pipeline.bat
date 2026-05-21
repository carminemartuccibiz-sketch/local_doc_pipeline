@echo off
REM Esegui da questa cartella (repo software)
setlocal EnableExtensions
title DVAMOCLES SWORD - Pipeline autonoma

cd /d "%~dp0"

echo ============================================================
echo  DVAMOCLES SWORD - Material Forge Studio
echo  Pipeline autonoma (Ingest + Gap Analysis)
echo ============================================================
echo.

if not exist ".venv\Scripts\activate.bat" (
    echo [SETUP] Creazione ambiente virtuale Python...
    python -m venv .venv
    if errorlevel 1 (
        echo ERRORE: impossibile creare .venv.
        pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"

echo [SETUP] Dipendenze...
pip install -r requirements.txt -q
pip install tqdm -q

if not exist ".env" (
    echo AVVISO: Copia .env.example in .env e imposta ANYTHINGLLM_API_KEY.
    if exist ".env.example" copy /Y ".env.example" ".env" >nul
)

echo.
echo [PRE-VOL0] Verifica server locali...
echo.

curl -sf -m 10 "http://localhost:1234/v1/models" >nul 2>&1
if errorlevel 1 (
    echo ERRORE: LM Studio non raggiungibile su http://localhost:1234
    pause
    exit /b 2
)
echo   OK  LM Studio      ^(porta 1234^)

curl -sf -m 10 "http://localhost:3001/api/ping" >nul 2>&1
if errorlevel 1 (
    echo ERRORE: AnythingLLM non raggiungibile su http://localhost:3001
    pause
    exit /b 2
)
echo   OK  AnythingLLM    ^(porta 3001^)

echo.
echo [RUN] Avvio orchestratore...
echo.

python orchestrator.py --skip-ingest --limit 10 %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Pipeline terminata con codice %EXIT_CODE%
    pause
)

endlocal
exit /b %EXIT_CODE%

---
author: DVAMOCLES
category: GOV
title: Pacchetto per validazione esterna (Claude)
---

# Cosa allegare a Claude per validare la pipeline

Allega **solo** questa cartella (codice + config esempio):

`tools/local_doc_pipeline/`

## Incluso (software)

- `*.py` — CLI, orchestrator, core, client LM/AnythingLLM
- `requirements.txt`, `.env.example`, `settings.py`, `README.md`
- `start_dvamocles_pipeline.bat` / `.ps1` (nella root repo)

## Escluso (non committare / non allegare)

| Path | Motivo |
|------|--------|
| `01_RAW_INGEST/` | Testi grezzi copiati (1433+ file) |
| `02_SESSION_MEMORY/` | Report gap + `pipeline_state.json` |
| `.env` | API key |
| `.venv/` | Dipendenze Python locali |
| `LAST DOCS/`, `extra files/`, `refactor fatto/` | Corpus SOT (resta nel repo host, fuori dal pacchetto tool) |
| LM Studio / AnythingLLM install | Software di riferimento esterni |

## Comandi di riferimento

```powershell
cd E:\...\DVAMOCLES-SWORD-AMBIENT-FULL-DOCUMENTATION
.\start_dvamocles_pipeline.ps1 -ForceAllmSync
.\start_dvamocles_pipeline.ps1 -Limit 10
```

## Prompt per analisi

Copia il prompt completo da **[`PROMPT_FOR_CLAUDE_ANALYSIS.md`](PROMPT_FOR_CLAUDE_ANALYSIS.md)** (review architettura, fix P0/P1, piano refactor, checklist).

## Note LM Studio

Modello **VL** (`qwen2.5-vl`) spesso risponde `400` su `/v1/chat/completions`.  
Impostare in `.env`: `LM_USE_NATIVE_CHAT=true` oppure caricare un modello **instruct testuale** non-VL.

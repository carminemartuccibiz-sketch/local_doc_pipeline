# Guide operative

Indice della documentazione di progetto e del blueprint Cursor.

## Primo avvio (installazione → UI)

| Documento | Uso |
|-----------|-----|
| [`PRIMO_AVVIO.md`](PRIMO_AVVIO.md) | Guida passo passo: venv, dipendenze, `.env`, LM Studio, avvio :7842 |

## Blueprint principale (sorgente di verità architetturale)

| Documento | Contenuto |
|-----------|-----------|
| [`claude-commands-dir/claude commands`](claude-commands-dir/claude%20commands) | Task 1–8: struttura, orchestrator, sliding window, server, UI, regole Cursor, ordine di lavoro |

**Validazione implementazione:** [`../BLUEPRINT_VALIDATION.md`](../BLUEPRINT_VALIDATION.md)

## Guide DVAMOCLES (pipeline documentale / handoff)

| Documento | Uso |
|-----------|-----|
| [`../EXPORT_FOR_CLAUDE_VALIDATION.md`](../EXPORT_FOR_CLAUDE_VALIDATION.md) | Cosa allegare a Claude per validazione esterna |
| [`../PROMPT_FOR_CLAUDE_ANALYSIS.md`](../PROMPT_FOR_CLAUDE_ANALYSIS.md) | Prompt pronto per analisi gap |
| [`../HANDOFF_REPORTS.md`](../HANDOFF_REPORTS.md) | Handoff report verso LAST DOCS |
| [`../HARDWARE_i9_2080Ti.md`](../HARDWARE_i9_2080Ti.md) | Profilo hardware e .env consigliati |
| [`../ARCHITECTURE_IMPROVEMENTS.md`](../ARCHITECTURE_IMPROVEMENTS.md) | Miglioramenti architettura CLI |

## Avvio rapido (orchestratore UI)

```powershell
# Dalla root local_doc_pipeline
.\dvamocles_daemon.ps1          # Flask + browser http://127.0.0.1:7842
python app.py                   # Finestra PyWebView
python legacy\cli.py check      # Preflight LM Studio + AnythingLLM
python legacy\cli.py run        # Gap analysis CLI (legacy)
```

## Workflow UI (progetto)

- **ingest** / **sliding_window**: file in `projects/<slug>/01_INGEST/` → analisi in sottocartelle + `04_MEMORY/ingest_manifest.json` (dedup MD5).
- **gap_analysis**: stesso ingest, report in `projects/<slug>/03_OUTPUT/Gap_Report_Generale.md`, state in `04_MEMORY/pipeline_state.json`.

## Test sliding window (Fase 3)

```powershell
python -m engine.ingest_processor .\mio_doc.md `
  --out-dir projects\demo\01_INGEST\mio_doc `
  --dry-run

# oppure
python -m engine.ingest_processor .\mio_doc.md `
  --project-ingest projects\demo\01_INGEST
```

## Ordine consigliato (da blueprint)

1. Struttura cartelle → verifica [`BLUEPRINT_VALIDATION.md`](../BLUEPRINT_VALIDATION.md)
2. Orchestrator: `python -c "from engine.orchestrator import get_state; get_state().emit_log('test')"`
3. Ingest su un solo `.md` prima della UI
4. Server + browser prima di PyWebView
5. `python app.py` quando il browser è OK

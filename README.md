# Local AI Orchestrator (`local_doc_pipeline`)

Software Python per **DVAMOCLES SWORD™** — orchestratore desktop (Flask UI + ingest sliding window) e pipeline gap analysis legacy.

## Avvio rapido (UI)

```powershell
.\dvamocles_daemon.ps1    # browser → http://127.0.0.1:7842
# oppure finestra nativa:
python app.py
```

CLI legacy: `python legacy/cli.py check` · `python legacy/cli.py run`

---

## Documentazione e blueprint

| Risorsa | Descrizione |
|---------|-------------|
| [`docs/guides/README.md`](docs/guides/README.md) | Indice guide (blueprint Cursor + handoff DVAMOCLES) |
| [`docs/guides/claude-commands-dir/claude commands`](docs/guides/claude-commands-dir/claude%20commands) | Blueprint Local AI Orchestrator (Task 1–8) |
| [`docs/BLUEPRINT_VALIDATION.md`](docs/BLUEPRINT_VALIDATION.md) | Confronto blueprint ↔ codice (✅ / ⚠️ / ❌) |

Documentazione storica e profili HW: resto di [`docs/`](docs/).

---

# DVAMOCLES — pipeline documentale

Pipeline documentale locale, fault-tolerant, pensata per modelli on-prem (contesto 8k–16k).

**Repository Git dedicato** al software. Il corpus documentale vive nel repo sorella  
`DVAMOCLES-SWORD-AMBIENT-FULL-DOCUMENTATION` (stessa macchina, path in `DVAMOCLES_SOURCE_ROOT` nel `.env`).

---

## Contesto e obiettivo

Il repo documentazione contiene anni di materiali (chat AI, Takeout NotebookLM, raw docs, suite canonica). La documentazione **ufficiale** vive in **`LAST DOCS/`** nel repo host (Source of Truth, tier 1).

Questa pipeline **non** riscrive né fonde automaticamente i materiali grezzi nella SOT. Il suo compito è:

1. **Raccogliere** in modo sicuro i file sparsi in `data/01_RAW_INGEST/` (copia, deduplica MD5).
2. **Confrontare** ogni documento grezzo con la SOT e produrre un **Gap Report** (mancanze e contraddizioni, non riassunti).
3. **Riprendere** dopo interruzioni (Ctrl+C, crash) grazie a `data/02_SESSION_MEMORY/pipeline_state.json`.
4. **Rispettare** i limiti di contesto dei LLM locali (chunking, RAG AnythingLLM, budget token).

Output gap: `data/02_SESSION_MEMORY/GAP_ANALYSIS_REPORTS/` (`GAP_<file>.md` + `Gap_Report_Generale.md`).

Progetti UI: `projects/<slug>/` (ingest sliding window in `01_INGEST/`).

Flusso legacy: `legacy/pipeline.py`, `legacy/orchestrator_v1.py`, `legacy/cli.py`.

**Validazione esterna (Claude):** vedi [`docs/EXPORT_FOR_CLAUDE_VALIDATION.md`](docs/EXPORT_FOR_CLAUDE_VALIDATION.md).

---

## Architettura (moduli)

```
.\start_dvamocles_pipeline.ps1   # da questa cartella
    │ oppure cli.py run
    │
    ├─ preflight (LM Studio :1234, AnythingLLM :3001)
    ├─ ingest_copy → 01_RAW_INGEST/
    └─ gap_runner (resume state)
           ├─ SOT: LAST DOCS/ (+ AnythingLLM RAG workspace dvamocles_sot_canon)
           ├─ per ogni file grezzo (default 10/run):
           │     chunking (##) → LM gap → GAP_*.md
           └─ pipeline_state.json (pending/processing/completed)
```

| Servizio | Ruolo |
|----------|--------|
| **AnythingLLM** | Indicizzazione SOT LAST DOCS, vector-search nel prompt gap |
| **LM Studio** | Inferenza gap (modello instruct consigliato; VL → `LM_USE_NATIVE_CHAT=true`) |

---

## Prerequisiti

1. **LM Studio** → `Developer` → **Start Server** (porta `1234`). Modello **instruct testuale** consigliato (evitare solo VL per gap).
2. **AnythingLLM** desktop → API key in `.env`.
3. Python 3.11+, venv, `pip install -r requirements.txt`.

---

## Installazione

```powershell
cd "E:\DVAMOCLES-SWORD-AMBIENT-FULL-DOCUMENTATION\tools\local_doc_pipeline"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# ANYTHINGLLM_API_KEY, LM_USE_NATIVE_CHAT=true, GAP_SOT_LAST_DOCS_ONLY=true
```

---

## Avvio rapido (one-click)

Dalla **root del repo** (PowerShell):

```powershell
.\start_dvamocles_pipeline.ps1 -ForceAllmSync   # prima volta: indicizza LAST DOCS su AnythingLLM
.\start_dvamocles_pipeline.ps1                 # 10 file grezzi per run, resume automatico
.\start_dvamocles_pipeline.ps1 -Limit 3
```

Guida operativa: [`DVAMOCLES_WORKSPACE/00_START_PIPELINE_GUIDE.md`](../../DVAMOCLES_WORKSPACE/00_START_PIPELINE_GUIDE.md)

---

## Variabili ambiente principali

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `ANYTHINGLLM_API_KEY` | — | Obbligatoria per RAG |
| `LM_STUDIO_MODEL` | `auto` | Discovery via `/v1/models` |
| `LM_USE_NATIVE_CHAT` | `true` | API nativa LM (consigliato con modelli VL) |
| `GAP_SOT_LAST_DOCS_ONLY` | `true` | Solo `LAST DOCS/` come SOT |
| `PIPELINE_MAX_CONCURRENCY` | `1` | Richieste LLM in parallelo (1 = solido su 2080 Ti) |
| `GAP_BATCH_SIZE` | `1` | File grezzi per esecuzione |
| `GAP_CHUNK_MAX_TOKENS` | `1200` | Dimensione chunk analisi |
| `GAP_RAW_INPUT_TOKEN_BUDGET` | `3500` | Budget token contesto grezzo |

Profilo **i9 + 2080 Ti + 32 GB**: vedi `HARDWARE_i9_2080Ti.md` e LM Studio **Max concurrent predictions = 1**.

Vedi `.env.example` per l’elenco completo.

---

## CLI (`cli.py`)

```powershell
python cli.py check
python cli.py run                    # = orchestrator continuo (default start script)
python cli.py run --skip-ingest --continuous --limit 1
python cli.py run --skip-ingest --limit 1   # un solo file (no loop)
python cli.py init-ingest
python cli.py ai-gap-analysis --target-path 01_RAW_INGEST --limit 1
```

**Output handoff (unico):** `02_SESSION_MEMORY/GAP_ANALYSIS_REPORTS/Gap_Report_Generale.md` — allega solo questo a Claude/GPT (`HANDOFF_REPORTS.md`). I `GAP_*.md` per-file sono off by default.

Con `GAP_SOT_LAST_DOCS_ONLY=false` confronta anche `Documentazione vecchia/` (tier 2); in AnythingLLM incorpora entrambe le cartelle nel workspace SOT.

| Flag | Descrizione |
|------|-------------|
| `--skip-ingest` | Salta copia in `01_RAW_INGEST` |
| `--limit N` | File per iterazione (con `--continuous`: 1 = uno alla volta in loop) |
| `--continuous` | Loop automatico fino a fine coda |
| `--max-rounds N` | Stop dopo N iterazioni (test) |
| `--force-allm-sync` | Re-indicizza LAST DOCS su AnythingLLM |
| `--append-only` | Report cumulativo senza merge LLM pesante |

Gerarchia SOT: `settings.py` → tier 1 = `LAST DOCS/`, tier 2 = `Documentazione vecchia/` (disattivato se `GAP_SOT_LAST_DOCS_ONLY=true`).

---

## Flusso legacy extract (`local_doc_pipeline.py`)

Convert → embed AnythingLLM → extract LM → `Dvamocles_Pre_Claude_Refactor/`. Stato in `pipeline_state.json` nella root pipeline (flusso distinto dal gap).

---

## Troubleshooting

| Problema | Soluzione |
|----------|-----------|
| PowerShell: comando non trovato | Usa `.\start_dvamocles_pipeline.bat` o `.\start_dvamocles_pipeline.ps1` |
| `403` AnythingLLM | `ANYTHINGLLM_API_KEY` in `.env` |
| `400` LM completions | `LM_USE_NATIVE_CHAT=true` o modello non-VL |
| `vector-search` workspace invalid | `.\start_dvamocles_pipeline.ps1 -ForceAllmSync` |
| File completato senza analisi | Resume chunk corrotto — il runner resetta da chunk 0 |
| OOM / context exceeded | LM Studio concurrent **1**, abbassa `GAP_CHUNK_MAX_TOKENS` |
| Run troppo lenta (atteso) | Profilo solido: `-Limit 1`, lascia girare overnight |

---

## Analisi esterna con Claude

1. Leggi [`EXPORT_FOR_CLAUDE_VALIDATION.md`](EXPORT_FOR_CLAUDE_VALIDATION.md) — cosa allegare.
2. Copia il prompt da [`PROMPT_FOR_CLAUDE_ANALYSIS.md`](PROMPT_FOR_CLAUDE_ANALYSIS.md).
3. Applica i fix suggeriti (P0) e rilancia la pipeline.

---

## Prossimo passo (dopo i gap)

Consolidare manualmente (o con skill docwriter) in `LAST DOCS/` / `refactor fatto/SUITE/` usando i Gap Report e `refactor fatto/SKILLS/ACTIVE/dvamocles-docwriter-SKILL.md`.

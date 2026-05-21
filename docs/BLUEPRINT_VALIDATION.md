# Validazione blueprint vs implementazione

Confronto tra [`guides/claude-commands-dir/claude commands`](guides/claude-commands-dir/claude%20commands) (Task 1–8 + regole Cursor) e lo stato del repository `local_doc_pipeline` a maggio 2026.

**Legenda:** ✅ allineato · ⚠️ parziale · ❌ mancante · ➕ extra (oltre blueprint)

---

## Task 1 — Cosa tenere / legacy

| Blueprint | Stato | Implementazione attuale |
|-----------|--------|-------------------------|
| `core/session_state.py` | ✅ | Invariato in `core/` |
| `core/ai_tasks.py` | ✅ | Invariato |
| `core/chunking.py` | ✅ | Overlap in `engine/ingest_processor.py`, non in chunking |
| `core/token_budget.py` | ✅ | Invariato |
| `core/gap_allm.py` | ✅ | Usato da gap CLI |
| `core/file_io.py` | ✅ | Invariato |
| `clients/anythingllm.py` | ✅ | Da `anythingllm_client` |
| `config/hardware_profiles.py` | ✅ | In `config/` |
| `core/converters.py` | ✅ | In `core/converters.py` |
| `pipeline.py` → legacy | ✅ | `legacy/pipeline.py` |
| `local_doc_pipeline.py` → legacy | ✅ | `legacy/local_doc_pipeline.py` |
| `orchestrator.py` → legacy | ✅ | `legacy/orchestrator_v1.py` |
| `cli.py` → legacy | ✅ | `legacy/cli.py` |
| `start_dvamocles_pipeline.*` → legacy | ✅ | `legacy/scripts/` |
| `gap_runner` → workflow plugin | ⚠️ | Logica resta in `core/gap_runner.py`; `workflows/gap_analysis.py` è stub |

---

## Task 2 — Struttura directory

| Path blueprint | Stato | Note |
|--------------|--------|------|
| `app.py` | ✅ | PyWebView + `--browser` |
| `server.py` | ✅ | Flask + SSE |
| `engine/*` (6 moduli) | ✅ | Tutti presenti |
| `workflows/*` (base + 3) | ✅ | `blog_post`, `code_analysis` stub |
| `projects/<slug>/` | ✅ | `project_store.py` crea 01–04 |
| `core/` (lista blueprint) | ✅ | + moduli CLI (`gap_runner`, `paths`, …) |
| `clients/` | ✅ | |
| `config/` | ✅ | + `settings.py`, `runtime.py` (split da ex-`config.py`) |
| `ui/` | ✅ | |
| `legacy/` | ✅ | + `shims/`, `scripts/` |
| `.env.example`, `requirements.txt`, `README.md` | ✅ | |
| `projects/.gitkeep` | ✅ | |
| `projects/*/` in `.gitignore` | ✅ | |
| ➕ `data/` | ➕ | Sessione CLI (`01_RAW_INGEST`, `02_SESSION_MEMORY`) |
| ➕ `docs/` | ➕ | Guide e export |
| ➕ `dvamocles_daemon.ps1/.bat` | ➕ | Avvio rapido UI |

### `projects/<slug>/` — sottocartelle

| Elemento | Stato |
|----------|--------|
| `project.json` | ✅ |
| `01_INGEST/<stem>/` (original, chunk_*, chunks.json, analysis.md) | ✅ |
| `02_REFERENCE/file_roles.json` | ✅ |
| `03_OUTPUT/` | ✅ (vuota finché non scrivi output) |
| `04_MEMORY/` (pipeline_state, gap_allm, ingest_manifest) | ⚠️ Cartella creata; **stato gap CLI ancora in `data/02_SESSION_MEMORY`** |
| Deduplica MD5 in ingest | ❌ Non in UI ingest (solo copia file in `01_INGEST` top-level) |

### `chunks.json`

| Campo blueprint | Stato |
|-----------------|--------|
| `source_file`, `source_md5`, `chunked_at` (Z) | ✅ |
| `chunk_strategy`, `total_chunks` | ✅ |
| `overlap_tokens` | ✅ |
| `overlap_chars` | ➕ (utile operativamente) |
| `chunks[]` con char_* e overlap_* | ✅ |

Naming chunk: blueprint esempio `chunk_001.txt` → codice usa **`chunk_000.txt`** (0-based).

---

## Fasi 2–8 — Checklist funzionale

### Fase 2 — `engine/orchestrator.py`

| Requisito | Stato |
|-----------|--------|
| `stop_event`, `job_queue`, `current_job`, `log_stream` | ✅ |
| `active_http_clients` | ✅ come `active_requests` + property alias |
| `kill_all()` 3 livelli + log STOP | ✅ |
| `get_state()` | ✅ + `get_orchestrator_state()` |
| `llm_complete` + kill switch + register client + emit_log | ✅ |

⚠️ **ThreadPoolExecutor** citato nel blueprint Task 2 tree, non implementato: job singolo in `threading.Thread` (`job_runner`).

⚠️ `JobQueue` in `engine/job_queue.py` è **separato** da `OrchestratorState.job_queue` (Queue standard).

### Fase 3 — `ingest_processor.py`

| Requisito | Stato |
|-----------|--------|
| `extract_plain()` | ✅ |
| overlap + `[...contesto...]` | ✅ |
| salvataggio `file_dir/` | ✅ |
| sliding window + `analysis.md` append | ✅ |
| `log_fn` chunk completato | ✅ |
| Firma `file_dir` | ✅ (+ opzionale `project_ingest_dir`) |

### Fase 4 — `model_router.py`

| Requisito | Stato |
|-----------|--------|
| GET `/v1/models`, `refresh()` | ✅ |
| `TASK_KEYWORDS` + `get_model_for_task()` | ✅ |
| Uso in pipeline UI | ⚠️ Log a inizio job; non forza ancora `LM_MODEL` in `ai_tasks` |

### Fase 5 — `cooldown_manager.py`

| Requisito | Stato |
|-----------|--------|
| Legge env profilo HW | ✅ |
| `after_llm_call/chunk/file` interruptible | ✅ |
| Integrato in `llm_complete`, ingest, job_runner | ✅ |

### Fase 6 — `server.py`

| Endpoint | Stato |
|----------|--------|
| `/api/projects` GET/POST | ✅ |
| `/api/projects/<slug>` GET | ✅ |
| `/api/projects/<slug>/roles` POST | ✅ |
| `/api/models` | ✅ |
| `/api/profiles` GET | ✅ |
| `/api/profiles/select` POST | ✅ |
| `/api/jobs/start` | ✅ |
| `/api/jobs/stop` | ✅ (+ alias `/api/stop`) |
| `/api/jobs/status` | ✅ |
| `/api/logs/stream` SSE | ✅ |
| `/api/jobs/reset` | ➕ (+ `/api/reset`) |
| `/api/workflows` | ➕ |

### Fase 7 — UI

| Requisito | Stato |
|-----------|--------|
| Layout 3 colonne | ✅ |
| SSE log colorati | ✅ |
| STOP → `/api/jobs/stop`, RESET | ✅ |
| Ruoli file + modal | ✅ |
| Refresh file 5s durante job | ✅ |
| Estetica Apple/minimal | ✅ (tema chiaro) |

### Fase 8 — `app.py`

| Requisito | Stato |
|-----------|--------|
| Flask thread + pywebview | ✅ |
| Fallback browser | ✅ |
| Porta 7842 da `config.defaults.UI_PORT` | ✅ |

---

## Regole generali Cursor (Task 5)

| # | Regola | Stato |
|---|--------|--------|
| 1 | Non riscrivere core AI/chunking/token | ✅ |
| 2 | `get_state()` per log/stop | ✅ (anche `get_orchestrator_state`) |
| 3 | Loop LLM controllano `stop_event` | ✅ ingest + ai_tasks |
| 4 | Progetto in `projects/<slug>/project.json` | ✅ |
| 5 | Path relativi in `projects/` | ✅ |
| 6 | requirements flask/pywebview/cors | ✅ |
| 7 | `projects/.gitkeep` + gitignore | ✅ |
| 8 | Non modificare `legacy/` | ✅ |

---

## Ordine di lavoro consigliato (dal documento)

| Step | Stato |
|------|--------|
| 1 Fase 1 struttura | ✅ Fatto |
| 2 Fase 2 orchestrator test | ✅ |
| 3 Fase 3 sliding window su .md | ✅ (`python -m engine.ingest_processor`) |
| 4 Fase 6+7 browser :7842 | ✅ `server.py` / `dvamocles_daemon.ps1` |
| 5 Fase 8 PyWebView | ✅ `python app.py` |

---

## Gap prioritari (backlog integrazione)

| # | Voce | Stato (mag 2026) |
|---|------|------------------|
| 1 | Workflow `gap_analysis` → `core/gap_runner` | ✅ `workflows/gap_analysis.py` + `job_runner._run_gap_job` |
| 2 | Memoria per progetto (`04_MEMORY/`) | ✅ `engine/project_memory.py`, state + manifest + `gap_allm` override |
| 3 | Ingest deduplica MD5 | ✅ `project_store` manifest + `mark_ingest_file_done` |
| 4 | ModelRouter → sessione LLM | ✅ `apply_model_for_task` + `set_session_lm_model` |
| 5 | Job queue unificata | ✅ `OrchestratorState.job_queue` (`JobQueue`) + worker daemon |
| 6 | Documentazione operativa | ✅ [`guides/README.md`](guides/README.md) |

**Ancora opzionali / minori:** `ThreadPoolExecutor` nell’orchestrator (worker singolo thread); merge profondo `clients/lm_studio.py`; chunk `chunk_001` naming (✅ 1-based file, index JSON 0-based).

---

## Frase contestuale (da usare con Cursor)

> Il repository di partenza è `tools/local_doc_pipeline`. I file `core/ai_tasks.py`, `core/chunking.py`, `core/session_state.py`, `core/file_io.py` e `core/converters.py` sono già funzionanti — non riscriverli, importali dai nuovi moduli.

---

*Generato per allineamento continuo con il blueprint Local AI Orchestrator Desktop.*

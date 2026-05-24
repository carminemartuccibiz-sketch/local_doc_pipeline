---
session_id: 20260522_2200_triangulation
date: 2026-05-22
agent_used: Cursor
source:
  - docs/guides/perplexity command dir/Agisci come un Principal Security Researcher e Sen.md
  - docs/guides/gpt-comand-dir/# 🚨 1. Sicurezza, T.md
  - docs/guides/claude-commands-dir/claude commands
  - docs/guides/claude-commands-dir/## DiagnosiTre cav.md
target_files:
  - clients/http_pool.py
  - engine/orchestrator.py
  - engine/job_runner.py
  - engine/http_serve.py
  - engine/cooldown_manager.py
  - engine/llm_watchdog.py
  - engine/ingest_processor.py
  - engine/project_memory.py
  - engine/workflow_runner.py
  - engine/project_store.py
  - core/ai_tasks.py
  - core/gap_allm.py
  - config/runtime.py
  - server.py
  - app.py
  - ui/static/app.js
  - workflows/blog_post.py
  - workflows/code_analysis.py
  - workflows/base_workflow.py
  - workflows/test_workflow.py
  - tests/test_ingest_processor.py
  - tests/test_project_memory.py
  - tests/test_blog_post_workflow.py
  - tests/test_code_analysis_workflow.py
  - tests/test_job_runner.py
  - tests/test_gpt_audit_fixes.py
  - scripts/verify_post_fix_checklist.py
status: completed
next_steps: "Smoke end-to-end: test_workflow → blog_post → code_analysis con LM Studio; verificare 03_OUTPUT e progress bar UI."
---

# Triangolazione audit + workflow plugin (sessione consolidata)

Implementazione e patching conclusi su **Local AI Orchestrator Desktop** (`tools/local_doc_pipeline`).
Riferimenti incrociati: audit GPT (sicurezza/pipeline), Perplexity (SSE/threading), DiagnosiTre cav (UI ↔ worker).

---

## 1. Sicurezza, threading e HTTP (`httpx`)

| Area | Intervento |
|------|------------|
| Connection pooling | `clients/http_pool.py` — client LM/ALLM condivisi, `Limits`, chiusura in `kill_all()` |
| Kill switch | `OrchestratorState`: RLock, snapshot `active_requests`, `close_all_http_clients()` |
| LLM calls | `core/ai_tasks.py` — timeout granulari, pool registrato una volta, `release_lm_http_resources()` su timeout/OOM |
| Cooldown | `engine/cooldown_manager.py` — `stop_event.wait()` al posto di `sleep` a fette |
| Watchdog | `engine/llm_watchdog.py` — stall opt-in (`LM_STALL_WATCHDOG_S`) |
| Server WSGI | `engine/http_serve.py` + `server.py` / `app.py` — Waitress se disponibile |

**Conservato (DiagnosiTre cav):** `job_runner` con `RLock`, `_worker_busy()` (no `is_job_running()` nel loop worker), `last_job` per UI Failed/RESET.

---

## 2. Fix SSE e hardening UI (`server.py`, `app.js`)

- SSE: `stream_with_context`, heartbeat ~15s, uscita su `GeneratorExit` / broken pipe / `stop_event`
- CORS ristretto a `127.0.0.1` / `localhost` su `/api/*`
- Header difensivi: CSP, `X-Frame-Options`, `nosniff`, ecc.
- `app.js`: `disconnectSSE(permanent)`, backoff reconnect, `beforeunload` / `pagehide` / `visibilitychange`
- Log stream UI: batching via `requestAnimationFrame` (meno reflow)

---

## 3. Ingest — overlap Markdown strutturale (`engine/ingest_processor.py`)

Sostituito overlap grezzo a N caratteri con **sliding window strutturale**:

- Confini sicuri: paragrafo → riga → frase; rispetto blocchi ` ``` `
- `_refine_chunks_to_token_budget()` dopo `split_markdown_sections`
- `_extract_structural_overlap()` per contesto tra chunk
- Preflight: `read_document_safe()`, `IngestBudgetError`, limiti env (`INGEST_MAX_CHUNKS`, ratio documento)
- Strategia manifest: `markdown_structural_overlap`

---

## 4. Output progetto e progress bar (`project_memory` + `OrchestratorState`)

**`engine/project_memory.py`**

- `save_workflow_output` / `save_workflow_output_markdown` → `03_OUTPUT/<workflow>/`
- Indice `04_MEMORY/workflow_outputs.json`

**`engine/orchestrator.py`**

- `init_current_job`, `update_current_job`, `bump_files_completed`, `record_workflow_output`
- `get_current_job_snapshot()` per `/api/jobs/status` thread-safe

**`engine/job_runner.py`**

- `ctx["orchestrator"]` per plugin; aggiornamenti job solo via API orchestrator

---

## 5. Workflow plugin completati

### `workflows/blog_post.py`

- Lettura `01_INGEST` (`read_document_safe`)
- Prompt stile **Apple** (minimal, benefici, H1/H2, elenchi)
- `llm_complete` → `03_OUTPUT/blog_posts/<stem>.md`
- Log `[BLOG]`, kill switch cooperativo

### `workflows/code_analysis.py`

- Lettura sorgente codice (`_read_text`, estensioni dev)
- Code review LLM in **3 sezioni H2**: Architettura, Vulnerabilità/debito, Refactoring
- Output `03_OUTPUT/code_reviews/<stem>.code_review.md`
- Log SSE a fasi `[CODE] Fase 1/5 … 5/5`

**Registry:** `engine/workflow_runner.py` — label UI aggiornate (non più stub).

---

## 6. Fix UI deadlock (DiagnosiTre cav) — già in repo, non regressi

- `/api/workflows` → `{ workflows: [...] }`
- `test_workflow` diagnostico (3 step, no LLM)
- `scripts/verify_post_fix_checklist.py` (checklist automatizzata)

---

## 7. Test e qualità

```text
py -3.10 -m pytest tests/ -q  →  24 passed
```

Nuovi test: ingest preflight, project_memory/output, blog_post, code_analysis, job_runner, gpt_audit.

---

## 8. Variabili ambiente rilevanti (opzionale `.env`)

| Variabile | Uso |
|-----------|-----|
| `LM_STALL_WATCHDOG_S` | Watchdog stall LLM |
| `GAP_RAG_SCORE_THRESHOLD` | Default 0.45 |
| `INGEST_MAX_DOC_RATIO` / `INGEST_MAX_CHUNKS` | Budget ingest |
| `BLOG_*` / `CODE_*` | Limiti sorgente e output workflow |

---

## 9. Struttura output attesa per progetto

```text
projects/<slug>/
  01_INGEST/          # sorgenti
  03_OUTPUT/
    blog_posts/
    code_reviews/
    Gap_Report_Generale.md   # gap_analysis
  04_MEMORY/
    workflow_outputs.json
    pipeline_state.json
```

---

## Trascrizione

Chat Cursor: triangolazione audit GPT + Perplexity + DiagnosiTre cav; implementazione sequenziale ingest/SSE/workflow/output layer.

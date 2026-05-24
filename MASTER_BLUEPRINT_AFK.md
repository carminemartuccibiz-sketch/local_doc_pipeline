# MASTER_BLUEPRINT_AFK.md

**Progetto:** Local AI Orchestrator (`tools/local_doc_pipeline`) + DMIP greenfield (`tools/dmip/`)  
**Versione documento:** 1.0.0 — 2026-05-24  
**Ruolo:** guida unica per sessione di esecuzione autonoma (AFK). Ogni checkbox è un micro-task atomico.  
**Regola AFK:** completare in ordine; non saltare dipendenze; dopo ogni sotto-fase eseguire gate `pytest` indicato.

---

## Come usare questo documento (agente esecutivo)

1. Leggere **§0 Riconciliazione** (non implementare voci “GIÀ FATTO” salvo regression test).
2. Eseguire **Fase 0 — Gate iniziale** e annotare baseline pytest.
3. Procedere **Fase 1 → 7** in sequenza; spuntare `- [ ]` → `- [x]` solo dopo verify del micro-task.
4. Un micro-task è “done” solo se: codice + test (se indicato) + nessuna regressione gate locale.
5. **Non** spostare `local_doc_pipeline` in `orchestrator/` in questa iterazione (decisione architetturale B6).
6. **Path DMIP confermato:** `E:\DVAMOCLES-SWORD-AMBIENT-FULL-DOCUMENTATION\tools\dmip\`

### Comandi gate standard (ripetere dove indicato)

```powershell
cd E:\DVAMOCLES-SWORD-AMBIENT-FULL-DOCUMENTATION\tools\local_doc_pipeline
py -3.10 -m pytest tests/ -q
py -3.10 scripts/verify_post_fix_checklist.py   # richiede server :7842 per check UI
```

---

## §0 — Riconciliazione (Fase 1 analitica)

### 0.1 Fonti fuse

| ID | Documento | Ruolo nel master |
|----|-----------|------------------|
| BP1 | `.cursor/plans/blueprint_esecutivo_audit_20c9a50e.plan.md` | Wave 1–6, matrice storica, rischi |
| BP2 | `.cursor/plans/audit_orchestrator_dmip_51643148.plan.md` | Fase 0–5, criteri accettazione, DMIP MVP |
| AR | `docs/guides/claude-commands-dir/AUDIT_REPORT.md` | P0/P1, chunking_v2, doc_refactor, DMIP fix |
| GPT-N | `docs/guides/gpt-comand-dir/new integration e fix gpt.md` | Markdown-aware chunking, structured memory, MLOps fallback |
| GPT-S | `docs/guides/gpt-comand-dir/# 🚨 1. Sicurezza, T.md` | Pool httpx, SSE, watchdog (storico) |
| PPX | `docs/guides/perplexity command dir/# Agisci come Produc.md` | FlowWorkflow, DevBlog, Reflect, flow state YAML |
| HIST | `AI_DEV_SESSIONS/*.md` | Fix completati vs backlog |

### 0.2 GIÀ FATTO — solo regression (non re-implementare)

| Voce | File chiave | Sessione |
|------|-------------|----------|
| Pool httpx + kill switch RLock | `clients/http_pool.py`, `engine/orchestrator.py` | 20260521, 20260522 |
| SSE bounded + disconnect UI | `server.py`, `ui/static/app.js` | 20260522 |
| Cooldown interruptibile | `engine/cooldown_manager.py` | 20260521 |
| LLM watchdog opt-in | `engine/llm_watchdog.py` | 20260521 |
| Waitress WSGI | `engine/http_serve.py` | 20260521 |
| UI deadlock fix | `engine/job_runner.py` (`_worker_busy`, RLock) | 20260522_ui_deadlock |
| Overlap strutturale ingest | `engine/ingest_processor.py` | 20260521/22 |
| Budget chunk dinamico **in ingest_processor** | `_resolve_ingest_chunk_tokens()` | 20260521 |
| Workflow blog_post, code_analysis | `workflows/*.py` | 20260522 |
| save_workflow_output + progress | `engine/project_memory.py` | 20260522 |
| tiktoken + resolve_token_limits | `core/token_budget.py` | esistente |
| ModelRouter discovery | `engine/model_router.py` | esistente |

### 0.3 BACKLOG OBBLIGATORIO (storico incompleto — integrato nel piano)

| ID | Origine | Descrizione | Fase master |
|----|---------|-------------|-------------|
| B1 | GPT §2.4, 20260521 | MinHash/SimHash dedup pre-LLM | 2.x ✅ MT-2.15 |
| B2 | 20260522_gpt_audit, AUDIT §5 | semantic_diff → `doc_refactor` 2-fasi | 4.x |
| B3 | GPT | mypy strict | Fuori scope AFK (nota finale) |
| B4 | BLUEPRINT_VALIDATION | Dedup MD5 ingest UI; migrazione `04_MEMORY` | 5.x |
| B5 | AUDIT §4–5 | chunking_v2 + RollingContext | 2.x |
| B6 | AUDIT §3.1 | Spostamento repo `orchestrator/` | **RINVIATO** |
| B7 | ARCHITECTURE_IMPROVEMENTS | `force_allm` sync delta `list_documents` | 5.x ✅ MT-5.06 |
| B8 | PPX | FlowWorkflow + YAML flows + checkpoint JSON | 4.x |
| B9 | PPX | DevBlog cascata code_analysis→blog_post | 4.x |
| B10 | PPX | Reflect workflow self-review | 4.x |
| B11 | GPT-N | smart_llm_complete + fallback chain + eccezioni LLM | 3.x |
| B12 | GPT-N | validate_request_budget preflight unificato | 3.x |
| B13 | GPT-N | WorkflowMemory / merge_memory (structured compression) | 2.x + 4.x |

### 0.4 Conflitti risolti (non rivalutare in AFK)

| Conflitto | Decisione |
|-----------|-----------|
| AUDIT §1.2 sliding_window keyword-only TypeError | **NON confermato**: `file_dir` è 2° arg posizionale in `ingest_processor.py` L716–727. Solo kwargs espliciti (manutenibilità). |
| 20260522_gpt_audit “watchdog non implementato” | **Obsoleto** — file `llm_watchdog.py` esiste. |
| BLUEPRINT_VALIDATION “gap/blog stub” | **Obsoleto** — aggiornare doc in Fase 7. |
| Double-count in `context_budget.py` | Bug reale in `ai_tasks._llm_complete_unlocked_inner` L623–627. |
| `resolve_chunk_max_tokens()` in job_runner | **P0 confermato** L374 — manca argomento `limits`. |
| `log_fn(..., level=)` in gap_analysis | **P0 confermato** L91, L99. |

### 0.5 Mappa fasi master

| Fase | Nome | Contenuto principale |
|------|------|---------------------|
| 0 | Gate & baseline | pytest, checklist, matrice pre/post |
| 1 | Core & Security | P0 crash, config, gitignore, regression security |
| 2 | Ingestion & Advanced Chunking | kwargs ingest, chunking_v2, RollingContext, code-fence |
| 3 | MLOps & Fallback Logic | budget unificato, preflight, fallback chain, error classify |
| 4 | Workflow interconnessi | doc_refactor, flow, devblog, reflect |
| 5 | Memoria & UI | gap_allm TTL, flow state, dedup MD5, DMIP frontend patterns |
| 6 | DMIP greenfield | scaffold `tools/dmip/` con fix audit nativi |
| 7 | Documentazione & chiusura | BLUEPRINT_VALIDATION, AI_DEV_SESSIONS, repomix |

---

## Fase 0 — Gate iniziale e baseline

- [x] **MT-0.01** — Eseguire `py -3.10 -m pytest tests/ -q` e registrare conteggio pass (atteso: ≥24).
  - **File:** n/a
  - **Dipende:** —
  - **Verify:** output salvato in nota sessione AFK

- [x] **MT-0.02** — Verificare presenza bug P0-A: leggere `engine/job_runner.py` ~L374 `resolve_chunk_max_tokens()` senza arg.
  - **File:** `engine/job_runner.py`
  - **Simbolo:** `_run_ingest_job`
  - **Dipende:** —
  - **Verify:** conferma manuale prima del fix

- [x] **MT-0.03** — Verificare presenza bug P0-B: `workflows/gap_analysis.py` chiamate `log_fn(..., level="WARN")`.
  - **File:** `workflows/gap_analysis.py`
  - **Simbolo:** `run_project_gap_analysis`
  - **Dipende:** —
  - **Verify:** grep `level=` nel file

- [x] **MT-0.04** — Confermare `tools/dmip/` assente; creazione prevista Fase 6.
  - **Dipende:** —
  - **Verify:** `Test-Path ..\dmip` → False

- [x] **MT-0.05** — Creare stub sessione `AI_DEV_SESSIONS/YYYYMMDD_afk_master_start.md` con baseline pytest e lista P0.
  - **File:** `AI_DEV_SESSIONS/YYYYMMDD_afk_master_start.md`
  - **Dipende:** MT-0.01
  - **Verify:** file con frontmatter YAML

---

## Fase 1 — Core & Security

> **Obiettivo:** zero crash certi su ingest/gap; hygiene repo; nessuna regressione threading/SSE.

### 1.1 P0 — Crash ingest (token limits)

- [x] **MT-1.01** — Aggiungere import in `engine/job_runner.py`: `get_session_lm_model` da `core.ai_tasks`; `resolve_token_limits` da `core.token_budget`.
  - **File:** `engine/job_runner.py`
  - **Dipende:** MT-0.02

- [x] **MT-1.02** — In `_run_ingest_job`, prima del `for src in sources:`, sostituire `max_tokens = resolve_chunk_max_tokens()` con blocco try/except:
  - `limits = resolve_token_limits(get_session_lm_model())`
  - `max_tokens = resolve_chunk_max_tokens(limits)`
  - `except Exception: max_tokens = 1200`
  - **File:** `engine/job_runner.py`
  - **Simbolo:** `_run_ingest_job`
  - **Dipende:** MT-1.01

- [x] **MT-1.03** — Test: aggiungere in `tests/test_job_runner.py` funzione `test_resolve_chunk_max_tokens_called_with_limits` che patcha `get_session_lm_model` + `resolve_token_limits` e verifica che `_run_ingest_job` non solleva `TypeError` (mock `sliding_window_analyze`, `list_ingest_sources`).
  - **File:** `tests/test_job_runner.py`
  - **Dipende:** MT-1.02
  - **Verify:** `pytest tests/test_job_runner.py -q`

### 1.2 P0 — Crash gap (log_fn)

- [x] **MT-1.04** — Aggiungere in `workflows/gap_analysis.py` helper modulo:
  ```python
  def _gap_log(log_fn, msg: str, level: str = "INFO") -> None:
      if not log_fn:
          return
      prefix = {"WARN": "[WARN] ", "ERROR": "[ERROR] "}.get(level, "")
      log_fn(f"{prefix}{msg}")
  ```
  - **File:** `workflows/gap_analysis.py`
  - **Dipende:** MT-0.03

- [x] **MT-1.05** — Sostituire **tutte** le chiamate `log_fn(..., level="WARN")` e `level="ERROR"` con `_gap_log(log_fn, msg, level=...)`.
  - **File:** `workflows/gap_analysis.py`
  - **Simbolo:** `run_project_gap_analysis`
  - **Dipende:** MT-1.04

- [x] **MT-1.06** — Test nuovo `tests/test_gap_analysis_log.py`: mock `log_fn` con firma `def f(m: str) -> None` (no kwargs); chiamare path preflight fallito (`ctx is None`) → nessuna eccezione.
  - **File:** `tests/test_gap_analysis_log.py`
  - **Dipende:** MT-1.05
  - **Verify:** pytest verde

### 1.3 Manutenibilità ingest call (non P0)

- [x] **MT-1.07** — Riscrivere chiamata `sliding_window_analyze` in `_run_ingest_job` con kwargs espliciti:
  - `file_path=src.resolve()`
  - `file_dir=file_dir.resolve()`
  - `llm_fn=llm_complete`
  - `stop_event=state.stop_event`
  - `log_fn=lambda m: state.emit_log(m)`
  - `max_tokens_per_chunk=max_tokens`
  - **File:** `engine/job_runner.py`
  - **Dipende:** MT-1.02

- [x] **MT-1.08** — Regression: `pytest tests/test_ingest_processor.py -q`
  - **Dipende:** MT-1.07

### 1.4 Config & repository hygiene

- [x] **MT-1.09** — Verificare `config/runtime.py`: nessuna seconda invocazione `load_environment()` oltre a import da `settings`.
  - **File:** `config/runtime.py`, `config/settings.py`
  - **Logica:** se duplicata, rimuovere; solo `settings.load_environment()` al import settings
  - **Dipende:** —

- [x] **MT-1.10** — Aggiungere a `.gitignore`: righe `*.bak`, `*.tmp` (se assenti).
  - **File:** `.gitignore`
  - **Dipende:** —

- [x] **MT-1.11** — `git rm --cached legacy/shims/config.py.bak legacy/shims/settings.py.bak` (non cancellare file locali se servono; solo untrack).
  - **File:** `legacy/shims/*.bak`
  - **Dipende:** MT-1.10

- [x] **MT-1.14** — Spezzare import circolare `clients.http_helpers` ↔ `engine` (lazy `logged_httpx_request`, `clients/http_trace.py`, export lazy in `engine/__init__.py`).
  - **File:** `clients/http_helpers.py`, `clients/http_trace.py`, `engine/__init__.py`, `engine/interaction_logger.py`, `tests/test_http_helpers_import.py`
  - **Verify:** `pytest tests/test_http_helpers_import.py tests/test_gap_allm_delta.py -q` senza `conftest` bootstrap

### 1.5 Gate Fase 1

- [x] **MT-1.12** — `py -3.10 -m pytest tests/ -q` completo.
  - **Dipende:** MT-1.03, MT-1.06, MT-1.08

- [x] **MT-1.13** — Smoke automatizzato: `scripts/smoke_test.py` (progetto temp + `POST /api/jobs/start` `test_workflow`, atteso HTTP 2xx/202); `tests/test_smoke_test.py`.
  - **Comando:** `py -3.10 scripts/smoke_test.py --spawn-server`
  - **Dipende:** MT-1.12

---

## Fase 2 — Ingestion & Advanced Chunking

> **Obiettivo:** chunking semantico gerarchico; memoria strutturata anti-drift; opzionale AST markdown-it.

### 2.1 Modulo chunking_v2 (AUDIT §4 + GPT-N Strategia A/C)

- [x] **MT-2.01** — Creare file `core/chunking_v2.py` con `BoundaryType` Enum (H1, H2, H3, CODE_FENCE, TABLE, PARAGRAPH, SENTENCE).
  - **File:** `core/chunking_v2.py` (nuovo)
  - **Dipende:** MT-1.12

- [x] **MT-2.02** — Aggiungere `@dataclass SemanticChunk` con campi: `index`, `text`, `token_estimate`, `boundary_type`, `parent_heading`, `has_code`, `has_table`, `cross_refs`.
  - **File:** `core/chunking_v2.py`
  - **Dipende:** MT-2.01

- [x] **MT-2.03** — Implementare `_extract_code_fences(body) -> list[tuple[start,end,text]]` con regex non-greedy su ` ``` `; segnare sezioni `atomic=True`.
  - **File:** `core/chunking_v2.py`
  - **Logica:** GPT-N Strategia B — code fence protection
  - **Dipende:** MT-2.01

- [x] **MT-2.04** — Implementare `_extract_heading_tree(body) -> list[dict]` con split su `^#{1,3} ` mantenendo gerarchia H1/H2/H3 e testo sezione.
  - **File:** `core/chunking_v2.py`
  - **Logica:** recursive semantic splitter ordine H1→H2→H3→paragraph
  - **Dipende:** MT-2.03

- [x] **MT-2.05** — Implementare `_flush_chunk(chunks, buffer, heading, model_hint)` che calcola `token_estimate` via `core.token_budget.count_tokens`.
  - **File:** `core/chunking_v2.py`
  - **Dipende:** MT-2.02, MT-2.04

- [x] **MT-2.06** — Implementare `semantic_chunk(body, *, max_tokens, model_hint="cl100k_base", min_tokens=100, overlap_strategy="heading_context")` con pack sezioni atomiche/non atomiche; overlap = solo riga contesto `[Contesto: {heading}]` non char fissi 400.
  - **File:** `core/chunking_v2.py`
  - **Dipende:** MT-2.05

- [x] **MT-2.07** — Implementare `_detect_cross_refs(text, all_headings) -> list[str]`.
  - **File:** `core/chunking_v2.py`
  - **Dipende:** MT-2.06

- [x] **MT-2.08** — Test `tests/test_chunking_v2.py`: documento con fenced code non spezzato; chunk rispetta `max_tokens`; heading parent propagato.
  - **File:** `tests/test_chunking_v2.py`
  - **Dipende:** MT-2.06
  - **Verify:** pytest

### 2.2 RollingContext (AUDIT §4.4 + GPT-N Structured Memory)

- [x] **MT-2.09** — Creare `core/rolling_context.py` con classe `RollingContext(max_facts=20)`.
  - **File:** `core/rolling_context.py` (nuovo)
  - **Dipende:** MT-2.02

- [x] **MT-2.10** — Metodo `add_chunk_result(extract: dict, heading: str)`: append facts con confidence high/medium; trim a `_max`.
  - **File:** `core/rolling_context.py`
  - **Simbolo:** `RollingContext.add_chunk_result`
  - **Dipende:** MT-2.09

- [x] **MT-2.11** — Metodo `build_context_block() -> str`: ultimi 3 heading + ultimi 10 facts formattati `• claim [section]`.
  - **File:** `core/rolling_context.py`
  - **Dipende:** MT-2.10

- [x] **MT-2.12** — Test `tests/test_rolling_context.py`: add 25 facts → len≤20; block non vuoto.
  - **Dipende:** MT-2.11

### 2.3 Integrazione opzionale ingest (flag env, non default)

- [x] **MT-2.13** — Aggiungere env `INGEST_USE_CHUNKING_V2=false` in `.env.example` con commento.
  - **File:** `.env.example`
  - **Dipende:** MT-2.06

- [x] **MT-2.14** — In `engine/ingest_processor._build_chunks_with_overlap`, se `INGEST_USE_CHUNKING_V2=true`, delegare a `semantic_chunk` e mappare `SemanticChunk` → `TextChunk` esistente.
  - **File:** `engine/ingest_processor.py`
  - **Simbolo:** `_build_chunks_with_overlap`
  - **Logica:** coexistenza; default path invariato
  - **Dipende:** MT-2.06, MT-2.13

- [x] **MT-2.15** — (B1) `core/dedup.py`: MinHash + similarità ibrida (Jaccard char, overlap, SequenceMatcher); `core/semantic_dedup.py` delega; skip ≥95% con log `[WARN] Documento già presente`.
  - **File:** `core/dedup.py`, `core/semantic_dedup.py`, `engine/project_store.py`, `tests/test_dedup.py`
  - **Dipende:** MT-2.06
  - **Env:** `INGEST_MINHASH_SIMILARITY=0.95`

### 2.4 Gate Fase 2

- [x] **MT-2.16** — `pytest tests/test_chunking_v2.py tests/test_rolling_context.py tests/test_ingest_processor.py -q`
  - **Dipende:** MT-2.08, MT-2.12, MT-2.14 (o skip MT-2.14 se flag off)

---

## Fase 3 — MLOps & Fallback Logic

> **Obiettivo:** un solo percorso budget token; preflight prima HTTP; catena fallback modelli LM Studio.

### 3.1 Unificazione token budget (AUDIT §2.1 + GPT-N §1)

- [x] **MT-3.01** — In `core/ai_tasks.py`, importare `resolve_token_limits` da `core.token_budget` (se non presente in `_llm_complete_unlocked_inner`).
  - **File:** `core/ai_tasks.py`
  - **Dipende:** MT-1.12

- [x] **MT-3.02** — Estrarre funzione `_truncate_user_for_context(*, model, system_prompt, user_message, max_output_tokens) -> str` che:
  - calcola `limits = resolve_token_limits(model)`
  - `max_in = limits.context_tokens - limits.response_reserve - INGEST_SAFETY_MARGIN` (o costante 128 condivisa con ingest)
  - usa `truncate_middle` se `count_tokens(system+user) > max_in`
  - **non** usa `ctx_cap * 0.72` inline
  - **File:** `core/ai_tasks.py`
  - **Dipende:** MT-3.01

- [x] **MT-3.03** — In `_llm_complete_unlocked_inner`, rimuovere blocco L623–627 (`ctx_cap`, `max_in = int(ctx_cap * 0.72) - safe_max`); chiamare `_truncate_user_for_context`.
  - **File:** `core/ai_tasks.py`
  - **Simbolo:** `_llm_complete_unlocked_inner`
  - **Dipende:** MT-3.02

- [x] **MT-3.04** — Allineare `engine/ingest_processor._preflight_llm_payload` a usare stessa formula `limits` (import condiviso o chiamata helper da ai_tasks) per evitare doppio standard ingest vs gap.
  - **File:** `engine/ingest_processor.py`
  - **Simbolo:** `_preflight_llm_payload`, `_resolve_ingest_call_budget`
  - **Dipende:** MT-3.02

- [x] **MT-3.05** — Test `tests/test_ai_tasks_budget.py`: prompt lungo → `truncate_middle` invocato una sola volta (mock/spy).
  - **File:** `tests/test_ai_tasks_budget.py`
  - **Dipende:** MT-3.03
  - **Verify:** pytest

### 3.2 Preflight validator (GPT-N validate_request_budget)

- [x] **MT-3.06** — Aggiungere in `core/token_budget.py` funzione `validate_request_budget(*, model_id, system_prompt, user_prompt, memory="", rag="", reserved_output) -> dict` con chiavi `fits`, `projected`, `usable`, `overflow`.
  - **File:** `core/token_budget.py`
  - **Logica:** `usable = int(context_tokens * 0.80) - safety`; `projected = sum(token counts) + reserved_output`
  - **Dipende:** MT-3.01

- [x] **MT-3.07** — Test unitario overflow detection su prompt sintetico > usable.
  - **File:** `tests/test_token_budget.py` (nuovo o esteso)
  - **Dipende:** MT-3.06

### 3.3 Eccezioni e classificazione errori LLM (GPT-N §2)

- [x] **MT-3.08** — Creare `core/llm_errors.py` con eccezioni: `LLMRecoverableError`, `LLMFatalError`, `ContextOverflowError`, `ModelOOMError`.
  - **File:** `core/llm_errors.py` (nuovo)
  - **Dipende:** MT-3.03

- [x] **MT-3.09** — Implementare `classify_llm_error(exc: Exception) -> Exception` (regex su messaggio: oom, context exceed, timeout).
  - **File:** `core/llm_errors.py`
  - **Dipende:** MT-3.08

### 3.4 Fallback chain (GPT-N smart_llm_complete)

- [x] **MT-3.10** — Aggiungere in `.env.example`: `LM_FALLBACK_CHAIN=` (es. `model-a,model-b,model-c`) documentato.
  - **File:** `.env.example`
  - **Dipende:** —

- [x] **MT-3.11** — Implementare `parse_fallback_chain() -> list[str]` in `core/ai_tasks.py` o `engine/model_router.py`.
  - **File:** `core/ai_tasks.py`
  - **Logica:** split env per virgola; fallback a `[get_session_lm_model()]`
  - **Dipende:** MT-3.10

- [x] **MT-3.12** — Implementare `smart_llm_complete(*, system_prompt, user_message, temperature, max_tokens) -> str`:
  - loop modelli in chain
  - preflight `validate_request_budget`; se not fits → shrink user_message ratio
  - chiama `_llm_complete_unlocked_inner` con modello sessione temporaneamente impostato
  - on `LLMRecoverableError`: `max_tokens *= 0.7`, sleep 2s, next model
  - on `LLMFatalError`: raise
  - **File:** `core/ai_tasks.py`
  - **Dipende:** MT-3.06, MT-3.09, MT-3.11

- [x] **MT-3.13** — **Non** sostituire globalmente `llm_complete` in questa iterazione; aggiungere env `LM_USE_SMART_FALLBACK=false`. Se true, `llm_complete` delega a `smart_llm_complete`.
  - **File:** `core/ai_tasks.py`
  - **Dipende:** MT-3.12
  - **Rischio:** opt-in per non cambiare comportamento default AFK

- [x] **MT-3.14** — Test mock: primo modello raise `ModelOOMError`, secondo success → ritorno testo.
  - **File:** `tests/test_smart_llm_fallback.py`
  - **Dipende:** MT-3.13

### 3.5 Model health ping (GPT-N opzionale)

- [ ] **MT-3.15** — In `engine/model_router.py`, metodo opzionale `ping_model(model_id) -> dict` con mini prompt "OK" e latency ms; cache in memoria processo.
  - **File:** `engine/model_router.py`
  - **Dipende:** MT-3.11
  - **Nota:** non bloccante gate

### 3.6 Gate Fase 3

- [x] **MT-3.16** — `pytest tests/test_ai_tasks_budget.py tests/test_smart_llm_fallback.py tests/ -q`
  - **Dipende:** MT-3.05, MT-3.14

---

## Fase 4 — Workflow interconnessi

> **Obiettivo:** pipeline 2-fasi documentale; flow YAML; cascate DevBlog; reflection.

### 4.1 doc_refactor (AUDIT §5 + B2)

- [x] **MT-4.01** — Creare `workflows/doc_refactor.py` con costanti `WORKFLOW_ID`, `EXTRACT_TEMP=0.0`, `SYNTH_TEMP=0.05`, `MAX_EXTRACT_OUTPUT=800`, `MAX_SYNTH_OUTPUT=3000`.
  - **File:** `workflows/doc_refactor.py` (nuovo)
  - **Dipende:** MT-2.06, MT-3.03

- [x] **MT-4.02** — Implementare `_parse_json_safe(raw, fallback_chunk) -> dict` (strip markdown fence json).
  - **File:** `workflows/doc_refactor.py`
  - **Dipende:** MT-4.01

- [x] **MT-4.03** — Implementare `_build_extraction_prompt(chunk, n, total, filename, sot_context) -> str` con JSON schema facts/entities/gaps_vs_sot/open_questions.
  - **File:** `workflows/doc_refactor.py`
  - **Dipende:** MT-4.01

- [x] **MT-4.04** — Implementare `_build_synthesis_prompt(filename, extracts, sot_context) -> str`.
  - **File:** `workflows/doc_refactor.py`
  - **Dipende:** MT-4.01

- [x] **MT-4.05** — Implementare `_synth_budget() -> int` usando `resolve_token_limits` × 0.65.
  - **File:** `workflows/doc_refactor.py`
  - **Dipende:** MT-4.01

- [x] **MT-4.06** — Implementare `_hierarchical_synthesis(extracts, filename, sot_context, log_fn) -> str` (group_size=5).
  - **File:** `workflows/doc_refactor.py`
  - **Dipende:** MT-4.04, MT-4.05

- [x] **MT-4.07** — Classe `DocRefactorWorkflow(BaseWorkflow).process_file`:
  - legge file; `semantic_chunk`; loop extract con `llm_complete`; synth; salva `{stem}_gap.md` e `{stem}_extracts.json` via `save_workflow_output`
  - rispetta `stop_event`, `abort_if_stop_requested`
  - **File:** `workflows/doc_refactor.py`
  - **Dipende:** MT-4.02–MT-4.06

- [x] **MT-4.08** — Registrare in `engine/workflow_runner.py` entry `doc_refactor` con label e `WorkflowCapabilities(requires_llm=True, requires_rag=True, supports_cancel=True)`.
  - **File:** `engine/workflow_runner.py`
  - **Dipende:** MT-4.07

- [x] **MT-4.09** — Test `tests/test_doc_refactor.py` con mock `llm_complete` (2+ chunk fittizi).
  - **Dipende:** MT-4.07
  - **Verify:** pytest

### 4.2 FlowWorkflow + persistenza (PPX B8)

- [x] **MT-4.10** — In `engine/project_memory.py` aggiungere `flow_state_path(slug, flow_name) -> Path` → `04_MEMORY/flows/{flow_name}.json`.
  - **File:** `engine/project_memory.py`
  - **Dipende:** MT-1.12

- [x] **MT-4.11** — Implementare `load_flow_state(slug, flow_name) -> dict` e `save_flow_state(slug, flow_name, state)` con `atomic_write_json`.
  - **File:** `engine/project_memory.py`
  - **Dipende:** MT-4.10

- [x] **MT-4.12** — Implementare `load_flow_definition(slug, flow_name) -> dict` che legge `04_MEMORY/flows/{flow_name}.yaml`; valida `version in (1,)`; ogni step ha `workflow` registrato in `WorkflowRunner`.
  - **File:** `engine/project_memory.py`
  - **Dipende:** MT-4.10
  - **Nota:** aggiungere `pyyaml` a requirements se assente

- [x] **MT-4.13** — Creare `workflows/flow.py` classe `FlowWorkflow.process_file`:
  - carica YAML; init/load flow state JSON
  - per ogni step: policy `on_error` (stop/skip/retry), `max_retries`
  - `WorkflowRunner().run_file(step["workflow"], filepath, ctx)`
  - aggiorna checkpoint post-step
  - **File:** `workflows/flow.py` (nuovo)
  - **Dipende:** MT-4.11, MT-4.12

- [x] **MT-4.14** — Registrare `flow` in `engine/workflow_runner.py`.
  - **Dipende:** MT-4.13

- [x] **MT-4.15** — Aggiungere esempio `projects/_template/04_MEMORY/flows/devblog.yaml` version 1 con steps code_analysis → blog_post.
  - **File:** esempio sotto `docs/` o template progetto
  - **Dipende:** MT-4.12

- [x] **MT-4.16** — Test `tests/test_flow_workflow.py` con flow mock 2 step e runner patched.
  - **Dipende:** MT-4.13

### 4.3 DevBlog cascata (PPX B9)

- [x] **MT-4.17** — Creare `workflows/devblog.py` `DevBlogWorkflow.process_file`: run `code_analysis` → locate `03_OUTPUT/code_reviews/{stem}.code_review.md` → run `blog_post` con `ctx["source_override"]` se supportato, altrimenti passare path report.
  - **File:** `workflows/devblog.py` (nuovo)
  - **Dipende:** MT-4.14

- [x] **MT-4.18** — Estendere `workflows/blog_post.py` per accettare `ctx.get("source_override")` Path opzionale.
  - **File:** `workflows/blog_post.py`
  - **Dipende:** MT-4.17

- [x] **MT-4.19** — Registrare `devblog` in workflow_runner.
  - **Dipende:** MT-4.17

### 4.4 Reflect workflow (PPX B10)

- [x] **MT-4.20** — Creare `workflows/reflect.py`: legge output da `03_OUTPUT/<workflow>/`; prompt critica LLM; salva in `03_OUTPUT/reviews/`.
  - **File:** `workflows/reflect.py` (nuovo)
  - **Dipende:** MT-3.03

- [x] **MT-4.21** — Registrare `reflect` in workflow_runner.
  - **Dipende:** MT-4.20

### 4.5 WorkflowMemory merge (GPT-N B13) — leggero

- [x] **MT-4.22** — Creare `core/workflow_memory.py` dataclass `WorkflowMemory` (entities set, decisions list, constraints list, files set) + `merge_memory(memory, extracted)`.
  - **File:** `core/workflow_memory.py` (nuovo)
  - **Dipende:** MT-2.09

- [x] **MT-4.23** — (Opzionale) Usare `WorkflowMemory` in `doc_refactor` fase extract per merge facts tra chunk — solo se non gonfia scope; altrimenti defer.
  - **Dipende:** MT-4.07, MT-4.22

### 4.6 Gate Fase 4

- [x] **MT-4.24** — `pytest tests/test_doc_refactor.py tests/test_flow_workflow.py -q` + full suite.
  - **Dipende:** MT-4.09, MT-4.16

---

## Fase 5 — Memoria, RAG & UI orchestrator

### 5.1 gap_allm TTL (AUDIT §2.4)

- [x] **MT-5.01** — In `core/gap_allm.py` `resolve_sot_workspace_slug`: leggere `slug_verified_at` da state; se assente o >24h UTC, invalidare cache e re-list workspaces.
  - **File:** `core/gap_allm.py`
  - **Simbolo:** `resolve_sot_workspace_slug`
  - **Dipende:** MT-3.16

- [x] **MT-5.02** — Dopo resolve slug valido, salvare `state["slug_verified_at"] = datetime.now(timezone.utc).isoformat()`.
  - **File:** `core/gap_allm.py`
  - **Dipende:** MT-5.01

- [x] **MT-5.03** — Test mock client: cached slug assente in list → re-resolve per `ALLM_SOT_WORKSPACE_NAME`.
  - **File:** `tests/test_gap_allm_slug_ttl.py` (nuovo)
  - **Dipende:** MT-5.02

### 5.2 Ingest dedup MD5 + MinHash (B4 / B1)

- [x] **MT-5.04** — `list_ingest_sources`: `skip_duplicates=True` usa manifest MD5 + MinHash (~95%); warning `Documento già presente`.
  - **File:** `engine/project_store.py`, `04_MEMORY/ingest_manifest.json`, `core/dedup.py`
  - **Dipende:** MT-1.12

- [x] **MT-5.05** — `_run_ingest_job` chiama `list_ingest_sources(slug, skip_duplicates=True, log_fn=...)`.
  - **File:** `engine/job_runner.py`
  - **Dipende:** MT-5.04

### 5.3 force_allm sync delta (B7)

- [x] **MT-5.06** — Delta sync: `AnythingLLMClient.list_documents` + skip upload se doc già nel workspace (anche con `force=True`); `list_workspace_document_keys` delega a `list_documents`.
  - **File:** `core/gap_allm.py`, `clients/anythingllm.py`, `tests/test_gap_allm_delta.py`
  - **Simbolo:** `sync_sot_to_anythingllm`
  - **Dipende:** MT-5.02

### 5.4 ctx orchestrator plugin (PPX)

- [x] **MT-5.07** — Verificare `_run_plugin_workflow` passa `ctx["orchestrator"]=state` (già L318); se manca in altri path job, allineare.
  - **File:** `engine/job_runner.py`
  - **Dipende:** MT-1.12

- [x] **MT-5.08** — Documentare in `workflows/base_workflow.py` docstring campi `ctx` standard: slug, stop_event, log_fn, orchestrator, flow_name, source_override.
  - **File:** `workflows/base_workflow.py`
  - **Dipende:** MT-5.07

- [x] **MT-5.10** — Cablaggio UI workflow: `WorkflowRunner.api_workflow_list()` → `GET /api/workflows`; `app.js` parsing `{workflows}` + label da registry (`code_analysis`, `devblog`, `doc_refactor`).
  - **File:** `server.py`, `engine/workflow_runner.py`, `ui/static/app.js`, `tests/test_api_workflows.py`
  - **Dipende:** MT-4.08, MT-4.19, MT-4.21

- [x] **MT-5.11** — Progress bar intra-file: `OrchestratorState.update_phase_progress`, `workflows/workflow_progress.py`, fasi in `code_analysis` / `blog_post` / `doc_refactor` / `devblog` / `reflect`; `app.js` legge `progress_percent`.
  - **File:** `engine/orchestrator.py`, `engine/job_runner.py`, `workflows/*.py`, `ui/static/app.js`, `tests/test_workflow_progress.py`
  - **Dipende:** MT-5.07

### 5.5 Gate Fase 5

- [x] **MT-5.09** — pytest full + smoke gap con AnythingLLM mock/skip se non disponibile.
  - **Dipende:** MT-5.03, MT-5.05

---

## Fase 6 — DMIP greenfield (`tools/dmip/`)

> **Prerequisito:** MT-1.12 gate verde. DMIP runtime indipendente da orchestrator.

### 6.1 Scaffold struttura

- [x] **MT-6.01** — Creare directory `E:\DVAMOCLES-SWORD-AMBIENT-FULL-DOCUMENTATION\tools\dmip\` con README (Ollama, Chroma, link orchestrator).
  - **Dipende:** MT-0.04

- [x] **MT-6.02** — Creare `tools/dmip/backend/requirements.txt` (fastapi, uvicorn, chromadb, httpx, python-dotenv, pydantic).
  - **Dipende:** MT-6.01

- [x] **MT-6.03** — Creare `tools/dmip/backend/app/main.py` con FastAPI app factory e mount router workspaces.
  - **File:** `tools/dmip/backend/app/main.py`
  - **Dipende:** MT-6.02

- [x] **MT-6.04** — Creare `tools/dmip/frontend/` scaffold Vite+React+TS (`package.json`, `src/main.tsx`).
  - **Dipende:** MT-6.01

### 6.2 Backend servizi (fix audit nativi)

- [x] **MT-6.05** — `WorkspaceStore`: `asyncio.Lock` su `init()`, singleton `get_workspace_store()`, `init_runs` per test; lifespan FastAPI in `app/main.py` (AUDIT §1.4).
  - **Simbolo:** `WorkspaceStore.init`, `get_workspace_store`
  - **Dipende:** MT-6.03

- [x] **MT-6.06** — `VectorStore`: `_delete_all_sync` → bool; abort `collection_unavailable`; success path ricrea collection; API `POST …/documents` → HTTP 503 su failure routing (AUDIT §1.5).
  - **Simbolo:** `VectorStore.upsert_documents`, `COLLECTION_UNAVAILABLE`
  - **Dipende:** MT-6.03

- [x] **MT-6.07** — Implementare `tools/dmip/backend/core/services/ingest_registry.py` `find_by_hash`: if not file_hash return None; caller logs warning (AUDIT §2.2).
  - **Dipende:** MT-6.03

- [x] **MT-6.08** — Implementare `tools/dmip/backend/app/api/workspaces.py` `_ingest_event_stream`: `ingestor = MultimodalIngestor()` prima del try; `finally: await ingestor.aclose()` (AUDIT §1.6).
  - **Simbolo:** `_ingest_event_stream`
  - **Dipende:** MT-6.05, MT-6.06, MT-6.07

### 6.3 Frontend DMIP

- [x] **MT-6.09** — `GenerationChat.tsx` + `generationChatStorage.ts`: cap 50 (`MAX_CHAT_MESSAGES`), prefix `dmip_gen_chat_`, trim su load/save, UI con contatore; `vitest` 7 test storage.
  - **File:** `tools/dmip/frontend/src/components/GenerationChat.tsx`, `generationChatStorage.ts`, `generationChatStorage.test.ts`
  - **Dipende:** MT-6.04

### 6.4 Test DMIP

- [x] **MT-6.10** — `test_workspace_store.py`: 8× `init()` concorrenti + `get_or_create` parallelo → `init_runs == 1`.
  - **Dipende:** MT-6.05

- [x] **MT-6.11** — `test_vector_store.py`: delete fallito (`simulate_delete_failure`) + replace OK + collection None senza replace.
  - **Dipende:** MT-6.06

- [x] **MT-6.12** — Gate: `cd tools/dmip/backend && py -3.10 -m pytest tests/ -q` → **11 passed** (`test_api_workspaces`, `test_ingest_registry`, `pytest.ini`).
  - **Dipende:** MT-6.10, MT-6.11

### 6.5 Gate Fase 6

- [x] **MT-6.13** — README dmip con comando avvio `uvicorn app.main:app --reload` e variabili env minime.
  - **Dipende:** MT-6.08, MT-6.09

---

## Fase 7 — Documentazione, repomix & chiusura AFK

- [x] **MT-7.01** — `docs/BLUEPRINT_VALIDATION.md`: workflow implementati, gate pytest 61+11+7, smoke; rimossi riferimenti stub.
  - **Dipende:** MT-4.24, MT-5.09

- [x] **MT-7.02** — `docs/guides/README.md`: sezione DMIP, AUDIT_REPORT, workflow plugin (`doc_refactor`, `flow`, `devblog`, `reflect`).
  - **Dipende:** MT-6.13

- [x] **MT-7.03** — `ROOTPAM.md` aggiornato via `python scripts/update_dev_router.py`.
  - **Dipende:** MT-7.01

- [x] **MT-7.04** — `scripts/generate_repomix.py --include-dmip` include `tools/dmip/`.
  - **File:** `scripts/generate_repomix.py`
  - **Dipende:** MT-6.13

- [x] **MT-7.05** — Eseguito `python scripts/generate_repomix.py --include-dmip` → `_LLM_CONTEXT_DUMP.txt`.
  - **Dipende:** MT-7.04

- [x] **MT-7.06** — `AI_DEV_SESSIONS/20260524_afk_master_complete.md` (matrice §0.2, pytest, smoke).
  - **Dipende:** MT-7.05

- [x] **MT-7.07** — Gate: `py -3.10 -m pytest tests/ -q` → **61 passed**; smoke `scripts/smoke_test.py`.
  - **Dipende:** tutti MT critici Fase 1–5

---

## Criteri di accettazione globali (Definition of Done AFK)

| Criterio | Verifica |
|----------|----------|
| Zero TypeError ingest UI | MT-1.02, MT-1.13 |
| Zero TypeError gap log_fn | MT-1.05, MT-1.06 |
| Budget token unificato | MT-3.03, MT-3.05 |
| Fallback LLM opt-in testato | MT-3.14 |
| chunking_v2 + test | MT-2.08 |
| doc_refactor registrato + test | MT-4.08, MT-4.09 |
| Flow + DevBlog + Reflect registrati | MT-4.14, MT-4.19, MT-4.21 |
| gap_allm TTL 24h | MT-5.03 |
| tools/dmip esiste con fix §1.4–1.6, §2.2–2.3 | MT-6.05–MT-6.09 |
| Documentazione allineata | MT-7.01, MT-7.02 |
| pytest orchestrator verde | MT-7.07 |
| Nessuna regressione §0.2 | MT-1.13 + MT-7.07 |

---

## Fuori scope AFK (registrare ma non implementare)

- **B3** mypy strict su tutto il repo
- **B6** spostamento fisico `local_doc_pipeline` → `orchestrator/`
- **markdown-it-py** AST chunking full (MT-2.x usa splitter interno; integrazione markdown-it come fase futura se richiesta)
- **gliner** entity extraction locale
- Unificazione `core/` condiviso tra orchestrator e DMIP

---

## Riferimenti incrociati rapidi

| Path | Contenuto |
|------|-----------|
| `docs/guides/claude-commands-dir/AUDIT_REPORT.md` | Audit Claude P0–P6 |
| `.cursor/plans/blueprint_esecutivo_audit_20c9a50e.plan.md` | Blueprint esecutivo wave |
| `.cursor/plans/audit_orchestrator_dmip_51643148.plan.md` | Piano audit DMIP |
| `docs/guides/gpt-comand-dir/new integration e fix gpt.md` | Chunking + MLOps GPT |
| `docs/guides/perplexity command dir/# Agisci come Produc.md` | Flow / DevBlog / Reflect |
| `AI_DEV_SESSIONS/` | Storico fix e triangolazione |

---

*Fine MASTER_BLUEPRINT_AFK.md — v1.1.0 (Fase 7 chiusa 2026-05-24)*

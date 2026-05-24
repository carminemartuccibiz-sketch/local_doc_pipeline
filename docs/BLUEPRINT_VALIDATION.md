# Validazione blueprint vs implementazione

Confronto tra blueprint Local AI Orchestrator Desktop e stato repository `tools/local_doc_pipeline` — **chiusura AFK 2026-05-24** (`MASTER_BLUEPRINT_AFK.md`).

**Legenda:** ✅ allineato · ⚠️ parziale · ❌ mancante · ➕ extra

---

## Gate test automatizzati (AFK)

| Suite | Comando | Esito (2026-05-24) |
|-------|---------|-------------------|
| Orchestrator | `py -3.10 -m pytest tests/ -q` | **61 passed** |
| DMIP backend | `cd ../dmip/backend && py -3.10 -m pytest tests/ -q` | **11 passed** |
| DMIP frontend | `cd ../dmip/frontend && npm test` | **7 passed** |
| Smoke MT-1.13 | `py -3.10 scripts/smoke_test.py --spawn-server` | **PASS** (HTTP 202 jobs/start) |
| Checklist UI | `py -3.10 scripts/verify_post_fix_checklist.py` | Opzionale (server :7842) |

---

## Workflow plugin (non più stub)

| Workflow | File | Registro `workflow_runner` | Test |
|----------|------|---------------------------|------|
| gap_analysis | `workflows/gap_analysis.py` | ingest path dedicato | `test_gap_analysis_log.py` |
| blog_post | `workflows/blog_post.py` | ✅ | `test_blog_post_workflow.py` |
| code_analysis | `workflows/code_analysis.py` | ✅ | `test_code_analysis_workflow.py` |
| doc_refactor | `workflows/doc_refactor.py` | ✅ | `test_doc_refactor.py` |
| flow | `workflows/flow.py` | ✅ | `test_flow_workflow.py` |
| devblog | `workflows/devblog.py` | ✅ | — |
| reflect | `workflows/reflect.py` | ✅ | — |
| test_workflow | `workflows/test_workflow.py` | ✅ | smoke + checklist |

Esposizione UI: `GET /api/workflows` → `WorkflowRunner.api_workflow_list()`; `ui/static/app.js` parsing `{ workflows: [...] }`.

---

## Task 1 — Cosa tenere / legacy

| Blueprint | Stato | Note |
|-----------|--------|------|
| `core/*` riusabili | ✅ | ai_tasks, chunking, token_budget, gap_allm, file_io, converters |
| `clients/anythingllm.py` | ✅ | + `http_helpers` lazy, `http_trace` |
| `legacy/*` | ✅ | pipeline, cli, orchestrator_v1 |
| gap → workflow | ✅ | `workflows/gap_analysis.py` + `core/gap_runner.py` |

---

## Fasi 2–8 — Checklist funzionale

### Fase 2 — Orchestrator

| Requisito | Stato |
|-----------|--------|
| Kill switch 3 livelli + SSE bounded | ✅ |
| `RLock`, `last_job`, worker `job_runner` | ✅ |
| Progress intra-file (`progress_percent`, `workflow_progress`) | ✅ MT-5.11 |

### Fase 3 — Ingest

| Requisito | Stato |
|-----------|--------|
| Sliding window + overlap strutturale | ✅ |
| `INGEST_USE_CHUNKING_V2` opzionale | ✅ `core/chunking_v2.py` |
| Dedup MD5 + MinHash ~95% | ✅ `core/dedup.py`, `project_store` |

### Fase 4 — Model router

| Requisito | Stato |
|-----------|--------|
| Discovery + `apply_model_for_task` | ✅ |

### Fase 5 — Cooldown

| Requisito | Stato |
|-----------|--------|
| Profilo HW + interruptible sleep | ✅ |

### Fase 6 — Server API

| Endpoint | Stato |
|----------|--------|
| projects, jobs, profiles, models | ✅ |
| `/api/workflows` (metadata plugin) | ✅ |
| `/api/logs/stream` SSE | ✅ |

### Fase 7 — UI

| Requisito | Stato |
|-----------|--------|
| Layout 3 colonne, STOP/RESET | ✅ |
| Progress bar + `progress_percent` | ✅ |
| Workflow select da API | ✅ |

### Fase 8 — `app.py`

| Requisito | Stato |
|-----------|--------|
| PyWebView + fallback browser | ✅ |

---

## DMIP greenfield (`tools/dmip/`)

| Voce audit | Stato |
|------------|--------|
| §1.4 WorkspaceStore `asyncio.Lock` | ✅ |
| §1.5 VectorStore abort dopo delete | ✅ |
| §1.6 ingestor `finally aclose` | ✅ |
| §2.2 ingest_registry hash vuoto | ✅ |
| §2.3 GenerationChat cap 50 localStorage | ✅ |

---

## Gap backlog (post-AFK, non bloccanti)

| # | Voce | Stato |
|---|------|--------|
| 1 | ThreadPoolExecutor multi-job parallelo | ⚠️ Worker singolo thread |
| 2 | ModelRouter forza sempre `LM_MODEL` in ogni path | ⚠️ Parziale |
| 3 | mypy strict (B3) | Fuori scope AFK |
| 4 | Repo split `orchestrator/` (B6) | Rinviato |
| 5 | `core/` condiviso orchestrator↔DMIP | Futuro |

---

## Regole Cursor (Task 5)

| # | Regola | Stato |
|---|--------|--------|
| 1–8 | Non riscrivere core; stop_event; projects/; legacy intatto | ✅ |

---

## Script chiusura documentazione

| Script | Uso |
|--------|-----|
| `python scripts/update_dev_router.py` | Aggiorna `ROOTPAM.md` da `AI_DEV_SESSIONS/` |
| `python scripts/generate_repomix.py --include-dmip` | `_LLM_CONTEXT_DUMP.txt` + sibling DMIP |
| `python scripts/smoke_test.py --spawn-server` | Collaudo MT-1.13 |

---

*Aggiornato in Fase 7 AFK — tutti i gate pytest/smoke sopra superati al 2026-05-24.*

# Future AI Prompts — Espansione architetturale DVAMOCLES

Prompt pronti per **GPT-4o** e **Perplexity**. Ogni blocco è **autocontenuto**: copia dall’inizio del fenced block fino alla fine.

**Ecosistema:** `tools/local_doc_pipeline` (orchestratore desktop) + sibling `tools/dmip/` (microservizio RAG).  
**Baseline QA (2026-05-24):** 61 pytest orchestrator · 11 DMIP backend · 7 DMIP frontend · smoke MT-1.13 PASS.  
**Fonti interne:** `MASTER_BLUEPRINT_AFK.md`, `ROOTPAM.md`, `docs/BLUEPRINT_VALIDATION.md`, `AI_DEV_SESSIONS/`, dump opzionale `_LLM_CONTEXT_DUMP.txt`.

---

## Mappa repo (riferimento rapido — non incollare nei prompt)

| Area | Path | Ruolo |
|------|------|--------|
| Entry desktop | `app.py` | PyWebView + thread Flask daemon; fallback `--browser` |
| Entry server | `server.py` | Flask REST + SSE `:7842` |
| Avvio Windows | `dvamocles_daemon.ps1` / `.bat` | venv + deps + server |
| Motore | `engine/` | orchestrator, job_runner, ingest, workflow registry, model router |
| Logica riusabile | `core/` | LLM, chunking, gap, dedup, token budget, converters |
| Integrazioni HTTP | `clients/` | LM Studio, AnythingLLM, httpx pool |
| Plugin workflow | `workflows/` | ABC + 8 workflow registrati |
| Config | `config/` | `.env`, runtime URL, profili HW, model task map |
| UI | `ui/templates/`, `ui/static/` | Layout 3 colonne, SSE, progress bar |
| CLI legacy | `legacy/` | pipeline pre-UI (intatta, non toccare) |
| Script ops | `scripts/` | smoke, repomix, verify checklist, dev router |
| Test | `tests/` | 21 file pytest |
| Runtime utente | `projects/<slug>/`, `data/`, `logs/` | gitignored (contenuto) |
| DMIP | `../dmip/backend/`, `../dmip/frontend/` | FastAPI :7850 + React/Vite |

---

## 1. GPT-4o — Workflow multi-agente (Scrittore ↔ Revisore)

```text
### CONTESTO ARCHITETTURALE (non ignorare)

Stai consigliando espansioni per **DVAMOCLES Local AI Orchestrator** — ecosistema documentale DevSecOps desktop, Windows-first, stabile post-refactoring AFK (2026-05-24).

---

#### A. Panorama ecosistema

Due codebase sibling sotto `tools/`:

1. **`local_doc_pipeline`** — orchestratore desktop: ingest documenti, gap analysis vs Source of Truth (SOT), workflow LLM plugin, UI PyWebView.
2. **`dmip/`** — Document Multimodal Ingest Pipeline: FastAPI + React + Ollama + Chroma (greenfield, nessun bridge HTTP stabile verso orchestrator oggi).

Documentazione di riferimento (non hai accesso al repo): `MASTER_BLUEPRINT_AFK.md`, `ROOTPAM.md`, `docs/BLUEPRINT_VALIDATION.md`.

---

#### B. Stack tecnologico completo

**Runtime & UI orchestrator**
- Python **3.10+**, venv Windows.
- **Flask 3.1+** + **flask-cors** (origins ristretti a `127.0.0.1:7842` / `localhost:7842`).
- **Waitress** WSGI via `engine/http_serve.py`.
- **PyWebView 6** in `app.py` (thread Flask daemon + finestra desktop); fallback `python app.py --browser`.
- UI statica: `ui/templates/index.html`, `ui/static/app.js`, `ui/static/style.css` — layout 3 colonne, SSE log, START/STOP/RESET, progress bar (`progress_percent`), select workflow da API.

**LLM & MLOps (orchestrator)**
- **LM Studio** OpenAI-compatible (`clients/lm_studio.py`, `config/runtime.py`).
- Cuore chiamate: `core/ai_tasks.py` — `llm_complete`, `smart_llm_complete` (fallback chain modelli), `init_gap_analysis_session`, `abort_if_stop_requested`, semaforo `PIPELINE_MAX_CONCURRENCY`.
- Budget token: `core/token_budget.py` (`resolve_token_limits`, `resolve_chunk_max_tokens`, `validate_request_budget`); contesto: `core/context_budget.py`.
- Routing modelli: `engine/model_router.py` (`apply_model_for_task`: summary vs reasoning).
- Profili HW: `config/hardware_profiles.py` — `eco` / `fast` / `deep` → env (cooldown, chunk size, timeout); hardware ref **i9 + RTX 2080 Ti 11GB + 32GB RAM** (`docs/HARDWARE_i9_2080Ti.md`).
- Pause: `engine/cooldown_manager.py` (interruptible, rispetta `stop_event`).
- Watchdog stall LLM opt-in: `engine/llm_watchdog.py` (`LM_STALL_WATCHDOG_S`).
- Preflight: `core/preflight.py` — ping LM Studio + AnythingLLM prima gap analysis.
- Errori LLM classificati: `core/llm_errors.py` (`LLMFatalError`, `LLMRecoverableError`).

**RAG (orchestrator — non DMIP)**
- **AnythingLLM invocation** via `clients/anythingllm.py` (upload, embeddings, vector-search, `list_documents` delta sync).
- Sync/state: `core/gap_allm.py` (TTL 24h, skip delta anche con `force=True`).
- Loop gap: `core/gap_runner.py` + `workflows/gap_analysis.py`.

**HTTP & osservabilità**
- Pool httpx: `clients/http_pool.py` — chiusura su kill switch.
- Trace API: `clients/http_helpers.py` (import lazy anti-ciclo) + `clients/http_trace.py`.
- Log rolling: `engine/interaction_logger.py` — ultime 5 interazioni API + `logs/app_system.log`.
- Scritture atomiche Windows: `core/file_io.atomic_write_json`.

**Dipendenze pip (`requirements.txt`)**
- Core: python-dotenv, tiktoken, tqdm, httpx, beautifulsoup4, pypdf, python-docx, pyyaml.
- UI: flask, flask-cors, pywebview, waitress.
- Opzionale: unstructured[pdf,docx] (non nel path principale attuale).

**CI/CD**
- `.github/workflows/python-app.yml` — pytest + pylint non-blocking.
- `.github/workflows/windows-build.yml` — PyInstaller release.
- `.github/dependabot.yml` — pip weekly.

---

#### C. Layout dati e progetto

**Root repo:** `PIPELINE_ROOT` = cartella `local_doc_pipeline`.

**Progetto UI** — `projects/<slug>/` (contenuto gitignored):
| Cartella | Contenuto |
|----------|-----------|
| `01_INGEST/` | File sorgente da elaborare (PDF, DOCX, MD, HTML, …) |
| `02_REFERENCE/` | Ruoli file SOT/Reference/Raw (`file_roles.json`) |
| `03_OUTPUT/` | Artefatti workflow: `blog_posts/`, `code_reviews/`, `Gap_Report_Generale.md`, `reviews/`, sottocartelle per workflow |
| `04_MEMORY/` | `pipeline_state.json` (resume chunk), `ingest_manifest.json`, `gap_allm_state.json`, `workflow_outputs.json`, `flows/*.yaml` + checkpoint JSON |
| `project.json` | Metadata: name, slug, workflow default, hardware_profile |

**Ingest per file:** `engine/ingest_processor.sliding_window_analyze` scrive sotto `01_INGEST/<stem>/` → `chunks.json`, `analysis.md` (non una cartella `02_STAGING` separata).

**Dedup pre-LLM:** MD5 + MinHash hybrid ~95% (`core/dedup.py`, `engine/project_store.py`) — skip con log `[WARN] Documento già presente`.

**Chunking:** legacy `core/chunking.py` + opt-in V2 markdown-aware `core/chunking_v2.py` (env `INGEST_USE_CHUNKING_V2`); rolling context tra chunk: `core/rolling_context.py`.

**Conversione:** `core/converters.py` (BeautifulSoup, pypdf, python-docx).

**Legacy CLI** (isolata, intatta): `legacy/cli.py`, `legacy/pipeline.py`, `legacy/orchestrator_v1.py` — non interferisce con UI.

**Altri path runtime:** `data/` (sessioni CLI), `logs/` (`api_interactions.json`, `app_system.log`).

---

#### D. Server Flask — API surface (`server.py`)

Binding **127.0.0.1:7842** (config `UI_PORT` in `config/defaults.py`).

| Metodo | Path | Funzione |
|--------|------|----------|
| GET | `/` | UI index |
| GET/POST | `/api/projects` | Lista / crea progetto |
| GET | `/api/projects/<slug>` | Dettaglio |
| POST | `/api/projects/<slug>/roles` | Assegna ruolo SOT/Reference/Raw |
| GET | `/api/workflows` | Lista workflow (built-in + plugin registry) |
| GET | `/api/models` | Discovery LM Studio |
| GET/POST | `/api/profiles`, `/api/profiles/select` | Profili HW |
| POST | `/api/jobs/start` | Accoda job → **202** |
| POST | `/api/stop`, `/api/jobs/stop` | Kill switch |
| POST | `/api/reset`, `/api/jobs/reset` | Reset orchestrator post-stop |
| GET | `/api/jobs/status` | `running`, `stop_requested`, `job`, `last_job`, `progress_percent` |
| GET | `/api/logs/stream` | SSE log bounded (500 msg, heartbeat 15s, disconnect-aware) |

Header sicurezza già presenti: CSP, X-Frame-Options DENY, nosniff, CORS ristretto.

---

#### E. Job runner multithread + kill switch

**Singleton stato:** `engine/orchestrator.OrchestratorState` via `get_orchestrator_state()`; reset: `reset_orchestrator()`.

**Coda:** `engine/job_queue.JobQueue` — PriorityQueue con drain su kill.

**Worker:** `engine/job_runner.py`
- Un **thread daemon** `orchestrator-job-worker` (`_job_worker_loop`), avviato da `_ensure_worker()` sotto **RLock**.
- `start_job(slug, workflow)` → accoda `{slug, workflow}`; rifiuta se `is_job_running()` (409 Conflict via API).
- Built-in workflow path: `ingest`, `sliding_window`, `gap_analysis`, `test_workflow`.
- Plugin: `_run_plugin_workflow` → `WorkflowRunner.get_workflow(name).process_file(file, ctx)`.

**Kill switch 3 livelli** (`OrchestratorState.kill_all()`):
1. `stop_event.set()` — tutti i loop LLM/ingest/workflow polling su `stop_event` o `abort_if_stop_requested()`.
2. Chiusura client **httpx** registrati + `clients/http_pool.close_all_http_clients()`.
3. Drain job queue; snapshot `last_job` per UI.

**Vincoli critici (già fixati, non rompere):**
- Nel worker **non** chiamare `is_job_running()` (deadlock con `qsize()`).
- `_worker_busy()` = solo `current_job.status == "running"`.
- Un job alla volta per design (backlog: parallelismo futuro ⚠️).

**Progress UI:** `OrchestratorState.update_phase_progress()` + `workflows/workflow_progress.py` (`report_phase`, `report_llm_start`, `report_save`).

---

#### F. Sistema workflow a plugin

**Contratto ABC:** `workflows/base_workflow.py` → `process_file(file_path: Path, ctx: dict)`.

**Capabilities:** `workflows/capabilities.py` — `requires_llm`, `requires_rag`, `supports_cancel`.

**Registro:** `engine/workflow_runner.py` (`_REGISTRY`):

| Slug | Classe | Output tipico | Note |
|------|--------|---------------|------|
| `gap_analysis` | GapAnalysisWorkflow | `03_OUTPUT/Gap_Report_Generale.md` | Path dedicato in job_runner, richiede ALLM |
| `blog_post` | BlogPostWorkflow | `03_OUTPUT/blog_posts/` | LLM, stile articolo |
| `code_analysis` | CodeAnalysisWorkflow | `03_OUTPUT/code_reviews/` | DevSecOps review |
| `doc_refactor` | DocRefactorWorkflow | refactor 2-fasi + gap | `requires_rag=True` |
| `flow` | FlowWorkflow | multi-step | YAML in `04_MEMORY/flows/`, checkpoint JSON |
| `devblog` | DevBlogWorkflow | cascata | code_analysis → blog_post |
| `reflect` | ReflectWorkflow | `03_OUTPUT/reviews/` | auto-review output esistenti |
| `test_workflow` | TestWorkflow | n/a | 3 step × 1s, zero LLM — smoke UI |

**Salvataggio:** `engine/project_memory.save_workflow_output(..., bump_progress=False)` — job_runner chiama `bump_files_completed()` a fine file; indice in `04_MEMORY/workflow_outputs.json`.

**ctx tipico:** `slug`, `stop_event`, `orchestrator`, `log_fn`, `file_index`, `files_in_job`, opz. `flow_name`.

**Stato attuale:** pipeline **lineare per file** (LLM chain → output); `reflect` = singolo passaggio review; `flow`/`devblog` = sequenze ma non dialogo multi-agente iterativo sulla stessa bozza.

---

#### G. Test & qualità (contesto stabilità)

21 file pytest (61 test): job_runner, ingest, chunking_v2, dedup, gap_allm delta, workflow (blog, code, doc_refactor, flow), token budget, ai_tasks budget, smart_llm fallback, http_helpers import (no circular import), api workflows, workflow progress, smoke script.

Script: `scripts/smoke_test.py`, `scripts/verify_post_fix_checklist.py` (richiede server live).

---

### RICHIESTA STRATEGICA

Progetta come estendere l’architettura plugin esistente per un **workflow multi-agente** in cui due ruoli LLM — **Scrittore** e **Revisore** — **dialogano** (più round) **prima** di pubblicare il file finale in `03_OUTPUT/`.

**Vincoli hard**
1. Integrazione con `BaseWorkflow.process_file`, `job_runner`, kill switch, `save_workflow_output` — non rompere workflow lineari (`blog_post`, `flow`, `devblog`, `reflect`).
2. Traccia auditabile su disco: path sotto `03_OUTPUT/` e/o `04_MEMORY/` (drafts, `thread.jsonl`, versioni `v001.md`).
3. Sequenziale nello stesso worker thread; rispetto `stop_event` ogni round.
4. Budget: `token_budget` + `cooldown_manager` + max round; early stop se revisore approva.
5. Hook futuro DMIP/Chroma per contesto revisore (senza codice).
6. Compatibilità con `FlowWorkflow` (step multi-agente in YAML?) e `ModelRouter` (modelli diversi per ruolo?).

**Deliverable (struttura obbligatoria)**
A. Modello stati: `DRAFT → IN_REVIEW → REVISION_REQUESTED → APPROVED → FINAL`
B. Design API Python (`MultiAgentWorkflow` / mixin, schema messaggi)
C. Protocollo Scrittore/Revisore (system prompt bullet, JSON feedback, stop criteria)
D. Integrazione registry + `WorkflowCapabilities` + `GET /api/workflows`
E. UX SSE + `progress_percent` multi-fase
F. Rischi (loop infinito, Windows file lock, `atomic_write_json`)
G. Piano MVP → V2 YAML → V3 DMIP retrieval
H. 3–5 pytest suggeriti

Design da **Principal Engineer** → issue GitHub atomiche (1–2 giorni ciascuna). Non implementare codice completo.
```

---

## 2. Perplexity — Sicurezza, CVE e MLOps VRAM (stack locale)

```text
### CONTESTO ARCHITETTURALE (non ignorare)

Stai facendo **threat intelligence + MLOps research** sull’intero ecosistema **DVAMOCLES** (orchestrator desktop + DMIP sibling). Deployment tipico: **singolo utente Windows**, servizi su **loopback/LAN**, nessun multi-tenant pubblico.

---

#### A. Stack completo da includere nella ricerca

**Orchestrator (`tools/local_doc_pipeline`)**
- Python 3.10, Flask 3.1+, Werkzeug (transitive), flask-cors 6.x, Waitress 3.x, PyWebView 6.2.x, httpx 0.28.x.
- Parsing documenti: beautifulsoup4, pypdf 6.x, python-docx, pyyaml, tiktoken; opzionale unstructured[pdf,docx].
- Binding API: `127.0.0.1:7842` — `server.py` con CORS ristretto, CSP, X-Frame-Options DENY, no-store su `/api/*`.
- PyWebView embedda UI; bridge JS↔Python; runtime Chromium/WebView2 OS-dipendente.
- Client HTTP verso **LM Studio** (`localhost:1234` tipico) e **AnythingLLM** (RAG gap analysis).
- Pool httpx condiviso (`clients/http_pool.py`); kill switch chiude client in volo.
- Log: `logs/app_interactions.json` (5 entry rolling), `logs/app_system.log`.
- `.env` via python-dotenv (`config/settings.py`) — segreti API key LM/ALLM locali.

**DMIP (`tools/dmip/`)**
- FastAPI, Starlette, uvicorn, Pydantic; porta dev **7850**.
- Frontend React + Vite + Vitest; localStorage chat (`dmip_gen_chat_*`, cap 50 msg).
- Target LLM: **Ollama**; vector store: **Chroma** (abstraction in `VectorStore`).
- Backend: `WorkspaceStore` (asyncio lock), `ingest_registry` (dedup hash), API `/workspaces/{id}/documents`.

**Processi concorrenti GPU**
- Orchestrator → LM Studio (inferenza + possibile embed ALLM manual mode).
- DMIP → Ollama (inferenza + embedding Chroma).
- Stesso host **RTX 2080 Ti 11GB** — rischio contention VRAM se entrambi attivi.

---

#### B. Superficie attacco & operativa

**API orchestrator esposte localmente**
- CRUD progetti `projects/<slug>/` — sanitizzazione slug/path (`engine/project_store`, `_slugify`).
- Upload indiretto: file copiati in `01_INGEST/` (filesystem utente, no sandbox OS).
- SSE `/api/logs/stream` — disconnect detection anti-zombie thread.
- Nessuna auth HTTP oggi (single-user desktop).

**Job runner & durata esecuzione**
- Thread worker unico; job possono durare ore (ingest sliding window + gap + plugin).
- STOP (`POST /api/stop`) → kill switch 3 livelli: stop_event, chiusura httpx, drain queue.
- `reset_orchestrator()` post-stop prima di nuovo job.
- Timeout httpx → `release_lm_http_resources()`; log possibile OOM LM Studio.

**MLOps già implementato (gap analysis vs industry)**
- Profili HW `eco/fast/deep` → env chunk/cooldown/timeout (`config/hardware_profiles.py`).
- `PIPELINE_MAX_CONCURRENCY=1`, semaforo LLM in `core/ai_tasks.py`.
- Cooldown interruptibile tra chunk/file (`engine/cooldown_manager.py`).
- LLM stall watchdog opt-in (`engine/llm_watchdog.py`).
- Preflight LM+ALLM (`core/preflight.py`).
- `smart_llm_complete` fallback chain modelli (`core/ai_tasks.py`).
- `validate_request_budget` preflight token (`core/token_budget.py`).
- Model router discovery (`engine/model_router.py`).

**Workflow plugin — filesystem**
- Leggono `01_INGEST/`, scrivono `03_OUTPUT/` — path traversal via slug/file name = da valutare.
- Nessuna esecuzione codice arbitrario; solo LLM su contenuto file.

**Legacy CLI** (`legacy/`) — stesso venv, path `data/` separati; fuori UI ma stesse deps parser.

**CI**
- GitHub Actions pytest; Dependabot pip weekly; build PyInstaller Windows.

---

#### C. Componenti DMIP — note sicurezza

- FastAPI senza auth documentata (dev MVP).
- VectorStore: su delete failure → `collection_unavailable` → HTTP **503** (no retry loop cieco) — pattern da estendere lato orchestrator se bridge futuro.
- Ingest SSE placeholder; dedup registry con warning su hash vuoto.

---

### RICHIESTA DI RICERCA (Perplexity)

Agisci come **Security Researcher + MLOps Engineer**. Fonti **2024–2026** con URL citati. Priorità: NVD/CVE, GitHub Security Advisories, docs ufficiali.

**Parte 1 — CVE & hardening (deployment localhost/LAN)**

| Componente | Versione ref. stack |
|------------|---------------------|
| Flask | 3.1.x |
| Werkzeug | transitive Flask 3 |
| flask-cors | 6.x |
| PyWebView | 6.2.x |
| Waitress | 3.x |
| httpx | 0.28.x |
| pypdf | 6.x |
| beautifulsoup4 | 4.12+ |
| python-docx | 1.2+ |
| FastAPI / Starlette / uvicorn | DMIP |
| Chromium / WebView2 | via PyWebView |

Per finding critico/alto: CVE ID, condizione exploit, **rilevanza nostra** (single-user loopback), mitigazione concreta.

Includi:
- PyWebView bridge, `evaluate_js`, file:// / custom scheme.
- Flask debug/SECRET_KEY, CSRF su POST `/api/jobs/start` da origin non autorizzata.
- CORS attuale (solo localhost:7842) — sufficiente?
- Parser ingest: zip bomb PDF, XXE DOCX, HTML malicious (BeautifulSoup).
- DMIP FastAPI esposto su LAN se `--host 0.0.0.0`.
- Dipendenze opzionali unstructured.

**Parte 2 — VRAM / MLOps locale (LM Studio + Ollama + 2080 Ti 11GB)**

1. OOM prevention modelli 7B–13B Q4/Q5, context lungo, batch embed.
2. **LM Studio:** GPU layers, KV cache, unload tra job; correlazione con nostri timeout/cooldown.
3. **Ollama:** `OLLAMA_MAX_LOADED_MODELS`, keep-alive, parallel — contention con LM Studio sullo stesso GPU.
4. Scheduling pipeline lunghe: ingest (`engine/ingest_processor`) + gap (`core/gap_runner`) + plugin — serializzazione job, preflight VRAM prima `POST /api/jobs/start`.
5. Monitoraggio Windows: nvidia-smi signals pre-crash driver.
6. Fallback: CPU offload, model router, riduzione chunk — pattern queue/backpressure industry.

**Formato output obbligatorio**
1. Executive summary (≤10 righe)
2. Tabella CVE/advisory (componente | CVE | severity | rilevanza | azione)
3. Checklist hardening (quick wins <1h vs structural) — mappa su `server.py` headers/CORS già presenti
4. Playbook VRAM operatore non-dev
5. Gap vs codice esistente (kill switch, cooldown, smart_llm, preflight, max_concurrency=1)
6. Fonti verificabili

Non inventare CVE: se assenti, dichiaralo e indica versione patchata consigliata.
```

---

## 3. GPT-4o — Advanced RAG: Orchestratore Desktop ↔ DMIP (Chroma)

```text
### CONTESTO ARCHITETTURALE (non ignorare)

Progetti l’integrazione **Advanced RAG** tra due codebase sibling testate e documentate (AFK 2026-05-24). Non hai il repo: usa solo questa specifica.

---

#### A. Orchestrator — architettura completa (`tools/local_doc_pipeline`)

**Entry & server**
- `app.py`: PyWebView + Flask thread `:7842`.
- `server.py`: REST + SSE; progetti, jobs, profiles, models, workflows, logs stream.
- `dvamocles_daemon.ps1`: bootstrap Windows.

**Motore (`engine/`)**
| Modulo | Ruolo |
|--------|--------|
| `orchestrator.py` | Singleton stato, kill switch, SSE queue, progress |
| `job_runner.py` | Worker thread, coda job, routing workflow |
| `job_queue.py` | PriorityQueue + drain |
| `ingest_processor.py` | Sliding window, chunks.json, analysis.md, overlap strutturale |
| `workflow_runner.py` | Registry plugin + `api_workflow_list()` |
| `project_store.py` | CRUD progetti, dedup MD5+MinHash, ruoli file |
| `project_memory.py` | Path 03/04, save_workflow_output, flow state |
| `model_router.py` | LM Studio discovery, task routing |
| `cooldown_manager.py` | Pause env-driven, interruptible |
| `interaction_logger.py` | Audit API |
| `http_serve.py`, `llm_watchdog.py` | WSGI, stall detection |

**Core (`core/`) — logica RAG/LLM riusabile**
| Modulo | Ruolo |
|--------|--------|
| `ai_tasks.py` | llm_complete, smart_llm_complete, gap session, abort stop |
| `gap_runner.py` | Loop gap fault-tolerant, SOT tier, RAG AnythingLLM |
| `gap_allm.py` | Sync workspace ALLM, delta list_documents, state TTL 24h |
| `gap_prompts.py` | System prompt gap/integrate/consolidate |
| `chunking.py` / `chunking_v2.py` | Split MD; V2 opt-in env |
| `rolling_context.py` | Memoria rolling tra chunk ingest |
| `dedup.py` / `semantic_dedup.py` | MinHash + hybrid similarity |
| `token_budget.py` | Limiti modello, validate_request_budget |
| `context_budget.py` | Bundle contesto LLM |
| `converters.py` | PDF/DOCX/HTML → testo |
| `preflight.py` | Ping LM + ALLM |
| `session_state.py` | pipeline_state.json versioned, resume |
| `paths.py` | Discovery SOT tier |
| `file_io.py` | atomic_write_json |

**Clients (`clients/`)**
- `lm_studio.py`, `anythingllm.py` (upload, embed, vector-search, list_documents).
- `http_pool.py`, `http_helpers.py` (lazy), `http_trace.py`.

**Workflow plugin (`workflows/`)** — tutti via `process_file(file, ctx)`:
- `gap_analysis` (requires_rag), `doc_refactor` (requires_rag), `blog_post`, `code_analysis`, `flow` (YAML multi-step), `devblog`, `reflect`, `test_workflow`.
- Progress: `workflow_progress.py`.

**Config (`config/`)**
- `settings.py`, `runtime.py` (URL LM/ALLM, timeout), `hardware_profiles.py`, `model_task_map.py`, `defaults.py`.

**Layout progetto `projects/<slug>/`**
- `01_INGEST/`, `02_REFERENCE/` (SOT/Reference/Raw), `03_OUTPUT/`, `04_MEMORY/` (+ flows YAML, manifest, sync state futuro).

**RAG attuale (orchestrator)**
- **AnythingLLM only** per gap analysis e workflow `requires_rag=True`.
- Nessun client `dmip` o Chroma nel orchestrator oggi.
- Ingest produce chunk locali JSON; non push automatico verso DMIP.

**Job execution**
- Coda unica, kill switch 3 livelli, un worker thread.
- `start_job` → preflight gap (se non test_workflow) → ingest/gap/plugin.
- Stop deve abortire batch HTTP verso servizi RAG esterni.

---

#### B. DMIP — architettura completa (`tools/dmip/`)

**Backend FastAPI** (`backend/app/main.py`, uvicorn `:7850`)
- Lifespan hooks; router workspaces.

**API attuale** (`backend/app/api/workspaces.py`)
- `GET /workspaces/{id}/ingest/stream` — placeholder SSE ingest.
- `POST /workspaces/{id}/documents` — upsert body `{documents[], replace, file_hash}` → VectorStore.

**Servizi (`backend/core/services/`)**
| Servizio | Ruolo |
|----------|--------|
| `vector_store.py` | Abstraction Chroma; `upsert_documents`; su delete failure → `collection_unavailable` → API **503** |
| `workspace_store.py` | CRUD workspace, **asyncio.Lock** per concorrenza |
| `ingest_registry.py` | Dedup per hash file; warning se hash vuoto |

**Frontend React** (`frontend/src/components/`)
- `GenerationChat.tsx` + `generationChatStorage.ts` — localStorage prefix `dmip_gen_chat_`, cap **50** messaggi, isolamento workspace.

**LLM stack DMIP:** **Ollama** (≠ LM Studio orchestrator).

**Test:** 11 pytest backend (workspace lock, vector abort, 503, ingest registry); 7 vitest frontend.

**README DMIP:** sibling di local_doc_pipeline; non condividono `core/` Python (backlog futuro B6/shared core).

---

#### C. Separazione e obiettivo integrazione

| Aspetto | Orchestrator | DMIP |
|---------|--------------|------|
| LLM inferenza | LM Studio | Ollama |
| Vector store | AnythingLLM workspace | Chroma |
| UI | PyWebView Flask | React SPA |
| Progetti | `projects/<slug>/` | workspace_id API |
| Porta | 7842 | 7850 |

**Obiettivo:** bridge HTTP orchestrator → DMIP per retrieval/indexing Chroma **senza** duplicare ingest chunking né rompere kill switch / dedup esistente. AnythingLLM può restare in parallelo (hybrid) o migrare gradualmente.

**Workflow candidati:** `gap_analysis`, `doc_refactor`, `code_analysis` (retrieval contesto), futuro multi-agente Revisore.

---

### RICHIESTA STRATEGICA — Advanced RAG cross-service

Progetta architettura **ibrida**: Orchestratore invoca DMIP FastAPI per retrieval/upsert Chroma; generazione resta su LM Studio (o opzione Ollama unificato).

**Rispondi come Principal Architect:**

**A. Boundary & mapping**
- Responsabilità ingest chunking / embedding / query / rerank / generation per servizio.
- Mapping `projects/<slug>` ↔ `workspace_id` Chroma (1:1? prefix `orc_{slug}`?).
- Sync delta: rispetto `ingest_manifest.json` + MinHash skip → push solo nuovi chunk a DMIP.

**B. Contratti API DMIP (OpenAPI-style)**
Estendi o conferma:
- `POST /v1/workspaces/{id}/ingest` (batch chunk + metadata: source_file, chunk_id, minhash)
- `POST /v1/workspaces/{id}/query` (top-k, filtri metadata)
- `GET /v1/workspaces/{id}/health` (Chroma ready vs collection_unavailable)
- `DELETE /v1/workspaces/{id}/vectors` (reset progetto / kill switch)
Auth locale, timeout, cancellazione httpx compatibile stop_event.

**C. Client orchestrator**
- Nuovo `clients/dmip_rag.py` o `core/dmip_retriever.py`.
- Env `RAG_BACKEND=anythingllm|dmip|hybrid`.
- Impatto su `core/gap_runner`, `core/gap_allm`, `init_gap_analysis_session`, `ModelRouter`.

**D. Sequence diagram testuali**
1. **Ingest E2E:** 01_INGEST → sliding_window/chunking_v2 → batch DMIP upsert → `04_MEMORY/dmip_sync_state.json`.
2. **Plugin retrieval:** doc_refactor/code_analysis → query DMIP → prompt LM Studio → save_workflow_output.

**E. Failure modes & kill switch**
- STOP mid-batch ingest DMIP: retry, partial index, tombstone.
- DMIP 503 collection_unavailable: fail closed workflow requires_rag.
- Fallback hybrid AnythingLLM se DMIP down.

**F. Embedding consistency**
- Ollama embed (DMIP) vs ALLM embed vs stesso corpus — regole anti vector-space mismatch.

**G. Performance 2080 Ti**
- Serializzare LM Studio inferenza vs Ollama embed; batch size; stima VRAM con profili HW esistenti.

**H. Sicurezza localhost**
- Flask 7842 + FastAPI 7850; CORS PyWebView; API key `.env` condivisa?

**I. Migrazione 3 fasi**
- α read-only query DMIP
- β sync post-ingest orchestrator
- γ gap analysis su Chroma (deprecazione parziale ALLM)

**J. Test pytest (5 scenari mock httpx)**
health ok, 503, query empty, stop mid-batch, hybrid flag.

**K. Tabella decisionale**
AnythingLLM vs Chroma per DevSecOps documentale / gap / blog — pro/contro operativi.

Specifica integrazione + issue atomiche. Non codice completo.
```

---

## Note d’uso

| Prompt | Tool | Allegati opzionali |
|--------|------|-------------------|
| §1 Multi-agente | GPT-4o | Estratto `workflows/base_workflow.py`, schema `projects/<slug>/` |
| §2 Sicurezza / VRAM | Perplexity | `docs/HARDWARE_i9_2080Ti.md`, versioni da `requirements.txt` |
| §3 RAG DMIP | GPT-4o | `tools/dmip/README.md`, estratto `vector_store.py` |

**Workflow post-risposta:** annotare decisioni in `AI_DEV_SESSIONS/` → `python scripts/update_dev_router.py` → micro-task in `MASTER_BLUEPRINT_AFK.md` solo se approvati.

**Dump contesto completo (se serve più dettaglio):** `python scripts/generate_repomix.py --include-dmip` → `_LLM_CONTEXT_DUMP.txt` (~866 KB). Non caricare l’intero dump su GPT/Perplexity: usare questi prompt autocontenuti.

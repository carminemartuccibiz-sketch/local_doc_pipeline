# ROOTPAM — Repo Map (Local AI Orchestrator)

Mappa architetturale per handoff tra sessioni IA. Le righe `↳ [🧠 STORICO AI]` sono aggiornate da `scripts/update_dev_router.py` leggendo `AI_DEV_SESSIONS/`.

**Ultimo aggiornamento router:** 2026-05-22T19:09:47Z

---

## Entry point

- app.py: PyWebView + thread Flask; fallback browser (`--browser`).
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke UI+ingest; valutare semantic_diff workflow (audit §4). _(log: 20260522_gpt_audit_hardening.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke end-to-end: test_workflow → blog_post → code_analysis con LM Studio; verificare 03_OUTPUT e progress bar UI. _(log: 20260522_2200_triangulation_and_workflows.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-21 [completed]. Next: Smoke ingest+gap con LM Studio; opzionale LM_STALL_WATCHDOG_S=120 in .env. _(log: 20260521_2100_gpt_audit_fixes.md)_
- server.py: Backend Flask UI, API REST, SSE log stream (porta 7842).
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke test: test_workflow su UI, poi ingest con LM Studio attivo. _(log: 20260522_ui_deadlock_fix.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke UI+ingest; valutare semantic_diff workflow (audit §4). _(log: 20260522_gpt_audit_hardening.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke end-to-end: test_workflow → blog_post → code_analysis con LM Studio; verificare 03_OUTPUT e progress bar UI. _(log: 20260522_2200_triangulation_and_workflows.md)_
- dvamocles_daemon.ps1: Crea/attiva venv, installa deps, avvia `server.py`.
- dvamocles_daemon.bat: Wrapper PowerShell per Windows.

## engine/ — Motore orchestrazione

- engine/orchestrator.py: Kill switch 3 livelli, JobQueue, stato condiviso UI/worker, log SSE.
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke test: test_workflow su UI, poi ingest con LM Studio attivo. _(log: 20260522_ui_deadlock_fix.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke UI+ingest; valutare semantic_diff workflow (audit §4). _(log: 20260522_gpt_audit_hardening.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke end-to-end: test_workflow → blog_post → code_analysis con LM Studio; verificare 03_OUTPUT e progress bar UI. _(log: 20260522_2200_triangulation_and_workflows.md)_
- engine/job_runner.py: Worker coda job, ingest sliding window, gap_analysis via workflow.
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke test: test_workflow su UI, poi ingest con LM Studio attivo. _(log: 20260522_ui_deadlock_fix.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke end-to-end: test_workflow → blog_post → code_analysis con LM Studio; verificare 03_OUTPUT e progress bar UI. _(log: 20260522_2200_triangulation_and_workflows.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor-Agent il 2026-05-21 [completed]. Next: Aggiungere sessioni reali dopo ogni chat; lanciare generate_repomix prima di handoff Claude _(log: 20260521_cursor_orchestrator_bootstrap.md)_
- engine/job_queue.py: PriorityQueue job con priorità e drain.
- engine/ingest_processor.py: Sliding window chunking, `chunks.json`, `analysis.md` per file.
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke UI+ingest; valutare semantic_diff workflow (audit §4). _(log: 20260522_gpt_audit_hardening.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke end-to-end: test_workflow → blog_post → code_analysis con LM Studio; verificare 03_OUTPUT e progress bar UI. _(log: 20260522_2200_triangulation_and_workflows.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-21 [completed]. Next: Smoke ingest+gap con LM Studio; opzionale LM_STALL_WATCHDOG_S=120 in .env. _(log: 20260521_2100_gpt_audit_fixes.md)_
- engine/model_router.py: Discovery modelli LM Studio, routing per task type.
- engine/cooldown_manager.py: Pause configurabili tra chunk/file/LLM.
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke UI+ingest; valutare semantic_diff workflow (audit §4). _(log: 20260522_gpt_audit_hardening.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke end-to-end: test_workflow → blog_post → code_analysis con LM Studio; verificare 03_OUTPUT e progress bar UI. _(log: 20260522_2200_triangulation_and_workflows.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-21 [completed]. Next: Smoke ingest+gap con LM Studio; opzionale LM_STALL_WATCHDOG_S=120 in .env. _(log: 20260521_2100_gpt_audit_fixes.md)_
- engine/project_store.py: CRUD progetti `projects/<slug>/`, ruoli file, dedup MD5 ingest.
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke end-to-end: test_workflow → blog_post → code_analysis con LM Studio; verificare 03_OUTPUT e progress bar UI. _(log: 20260522_2200_triangulation_and_workflows.md)_
- engine/project_memory.py: Path `04_MEMORY/` per progetto (state, manifest, report).
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke end-to-end: test_workflow → blog_post → code_analysis con LM Studio; verificare 03_OUTPUT e progress bar UI. _(log: 20260522_2200_triangulation_and_workflows.md)_
- engine/workflow_runner.py: Registry plugin workflow.
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke test: test_workflow su UI, poi ingest con LM Studio attivo. _(log: 20260522_ui_deadlock_fix.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke UI+ingest; valutare semantic_diff workflow (audit §4). _(log: 20260522_gpt_audit_hardening.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke end-to-end: test_workflow → blog_post → code_analysis con LM Studio; verificare 03_OUTPUT e progress bar UI. _(log: 20260522_2200_triangulation_and_workflows.md)_
- engine/interaction_logger.py: Log rolling API (5 interazioni) + `app_system.log`.
↳ [🧠 STORICO AI]: Modificato da Cursor-Agent il 2026-05-21 [completed]. Next: Aggiungere sessioni reali dopo ogni chat; lanciare generate_repomix prima di handoff Claude _(log: 20260521_cursor_orchestrator_bootstrap.md)_

## workflows/

- workflows/base_workflow.py: ABC `process_file(file, ctx)`.
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke end-to-end: test_workflow → blog_post → code_analysis con LM Studio; verificare 03_OUTPUT e progress bar UI. _(log: 20260522_2200_triangulation_and_workflows.md)_
- workflows/gap_analysis.py: Gap UI su `core.gap_runner` + memoria progetto.
↳ [🧠 STORICO AI]: Modificato da Cursor-Agent il 2026-05-21 [completed]. Next: Aggiungere sessioni reali dopo ogni chat; lanciare generate_repomix prima di handoff Claude _(log: 20260521_cursor_orchestrator_bootstrap.md)_
- workflows/blog_post.py: Stub workflow blog.
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke end-to-end: test_workflow → blog_post → code_analysis con LM Studio; verificare 03_OUTPUT e progress bar UI. _(log: 20260522_2200_triangulation_and_workflows.md)_
- workflows/code_analysis.py: Stub analisi codice.
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke end-to-end: test_workflow → blog_post → code_analysis con LM Studio; verificare 03_OUTPUT e progress bar UI. _(log: 20260522_2200_triangulation_and_workflows.md)_

## core/ — Logica riusabile (non riscrivere senza motivo)

- core/ai_tasks.py: `llm_complete`, discovery modello, kill switch httpx.
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke UI+ingest; valutare semantic_diff workflow (audit §4). _(log: 20260522_gpt_audit_hardening.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke end-to-end: test_workflow → blog_post → code_analysis con LM Studio; verificare 03_OUTPUT e progress bar UI. _(log: 20260522_2200_triangulation_and_workflows.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-21 [completed]. Next: Smoke ingest+gap con LM Studio; opzionale LM_STALL_WATCHDOG_S=120 in .env. _(log: 20260521_2100_gpt_audit_fixes.md)_
- core/gap_runner.py: Loop gap analysis fault-tolerant, SOT, AnythingLLM RAG.
- core/gap_allm.py: Sync SOT workspace, vector search, state embedding.
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke UI+ingest; valutare semantic_diff workflow (audit §4). _(log: 20260522_gpt_audit_hardening.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke end-to-end: test_workflow → blog_post → code_analysis con LM Studio; verificare 03_OUTPUT e progress bar UI. _(log: 20260522_2200_triangulation_and_workflows.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-21 [completed]. Next: Smoke ingest+gap con LM Studio; opzionale LM_STALL_WATCHDOG_S=120 in .env. _(log: 20260521_2100_gpt_audit_fixes.md)_
- core/session_state.py: `pipeline_state.json` versioned, resume chunk/file.
- core/chunking.py: Split markdown sezioni.
- core/token_budget.py: Limiti token per modello.
- core/converters.py: Estrazione plain da PDF/DOCX/HTML.
- core/file_io.py: `atomic_write_json` Windows-safe.
- core/paths.py: Layout `data/`, discovery SOT tier.

## clients/

- clients/lm_studio.py: Client LM Studio (health, list_models, delega `llm_complete`).
- clients/anythingllm.py: REST workspace, upload, embeddings, vector-search.
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke UI+ingest; valutare semantic_diff workflow (audit §4). _(log: 20260522_gpt_audit_hardening.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-21 [completed]. Next: Smoke ingest+gap con LM Studio; opzionale LM_STALL_WATCHDOG_S=120 in .env. _(log: 20260521_2100_gpt_audit_fixes.md)_
- clients/http_helpers.py: Wrap httpx → `interaction_logger`.

## config/

- config/settings.py: `PIPELINE_ROOT`, `DATA_ROOT`, load `.env`.
- config/runtime.py: URL LM/ALLM, timeout, path sessione.
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke UI+ingest; valutare semantic_diff workflow (audit §4). _(log: 20260522_gpt_audit_hardening.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke end-to-end: test_workflow → blog_post → code_analysis con LM Studio; verificare 03_OUTPUT e progress bar UI. _(log: 20260522_2200_triangulation_and_workflows.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-21 [completed]. Next: Smoke ingest+gap con LM Studio; opzionale LM_STALL_WATCHDOG_S=120 in .env. _(log: 20260521_2100_gpt_audit_fixes.md)_
- config/hardware_profiles.py: Profili eco/fast/deep → variabili env.
- config/model_task_map.py: Keyword routing modelli.
- config/defaults.py: `UI_PORT`, costanti UI.

## ui/

- ui/templates/index.html: Layout 3 colonne orchestratore.
- ui/static/app.js: SSE log, progetti, job START/STOP/RESET.
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke test: test_workflow su UI, poi ingest con LM Studio attivo. _(log: 20260522_ui_deadlock_fix.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke UI+ingest; valutare semantic_diff workflow (audit §4). _(log: 20260522_gpt_audit_hardening.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke end-to-end: test_workflow → blog_post → code_analysis con LM Studio; verificare 03_OUTPUT e progress bar UI. _(log: 20260522_2200_triangulation_and_workflows.md)_
- ui/static/style.css: Tema chiaro minimal.

## legacy/ — CLI pipeline documentale (isolata)

- legacy/cli.py: Entry `check`, `run`, gap analysis batch.
- legacy/pipeline.py: Flusso extract legacy.
- legacy/orchestrator_v1.py: Orchestrator pre-UI.

## docs/ e meta-sviluppo

- docs/guides/PRIMO_AVVIO.md: Guida installazione → UI.
- docs/BLUEPRINT_VALIDATION.md: Confronto blueprint vs codice.
- AI_DEV_SESSIONS/: Transcript / note sessioni IA (input router).
- ROOTPAM.md: Questo file (router + mappa).
↳ [🧠 STORICO AI]: Modificato da Cursor-Agent il 2026-05-21 [completed]. Next: Aggiungere sessioni reali dopo ogni chat; lanciare generate_repomix prima di handoff Claude _(log: 20260521_cursor_orchestrator_bootstrap.md)_
- scripts/update_dev_router.py: Inietta storico AI sotto le righe file.
↳ [🧠 STORICO AI]: Modificato da Cursor-Agent il 2026-05-21 [completed]. Next: Aggiungere sessioni reali dopo ogni chat; lanciare generate_repomix prima di handoff Claude _(log: 20260521_cursor_orchestrator_bootstrap.md)_
- scripts/generate_repomix.py: Dump `_LLM_CONTEXT_DUMP.txt` per LLM esterni.

## Runtime (non committare dati utente)

- projects/<slug>/: Progetti UI (gitignore contenuto).
- data/: Ingest CLI, session memory, log pipeline.
- logs/: `api_interactions.json` (5 entry), `app_system.log`.

## Dipendenze e CI

- requirements.txt: Dipendenze pip (Dependabot weekly); Flask 3.1+, pywebview 6+, httpx 0.28+.
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke UI+ingest; valutare semantic_diff workflow (audit §4). _(log: 20260522_gpt_audit_hardening.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Procedere con i test dell'interfaccia UI e dell'ingestion. _(log: 20260522_1941_dependabot_sync.md)_

## File toccati dalle IA (non ancora in mappa)
- tests/test_job_runner.py: _(aggiungere descrizione in ROOTPAM)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke test: test_workflow su UI, poi ingest con LM Studio attivo. _(log: 20260522_ui_deadlock_fix.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke end-to-end: test_workflow → blog_post → code_analysis con LM Studio; verificare 03_OUTPUT e progress bar UI. _(log: 20260522_2200_triangulation_and_workflows.md)_
- workflows/test_workflow.py: _(aggiungere descrizione in ROOTPAM)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke test: test_workflow su UI, poi ingest con LM Studio attivo. _(log: 20260522_ui_deadlock_fix.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke end-to-end: test_workflow → blog_post → code_analysis con LM Studio; verificare 03_OUTPUT e progress bar UI. _(log: 20260522_2200_triangulation_and_workflows.md)_

## File toccati dalle IA (non ancora in mappa)
- clients/http_pool.py: _(aggiungere descrizione in ROOTPAM)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke UI+ingest; valutare semantic_diff workflow (audit §4). _(log: 20260522_gpt_audit_hardening.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke end-to-end: test_workflow → blog_post → code_analysis con LM Studio; verificare 03_OUTPUT e progress bar UI. _(log: 20260522_2200_triangulation_and_workflows.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-21 [completed]. Next: Smoke ingest+gap con LM Studio; opzionale LM_STALL_WATCHDOG_S=120 in .env. _(log: 20260521_2100_gpt_audit_fixes.md)_
- engine/http_serve.py: _(aggiungere descrizione in ROOTPAM)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke UI+ingest; valutare semantic_diff workflow (audit §4). _(log: 20260522_gpt_audit_hardening.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke end-to-end: test_workflow → blog_post → code_analysis con LM Studio; verificare 03_OUTPUT e progress bar UI. _(log: 20260522_2200_triangulation_and_workflows.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-21 [completed]. Next: Smoke ingest+gap con LM Studio; opzionale LM_STALL_WATCHDOG_S=120 in .env. _(log: 20260521_2100_gpt_audit_fixes.md)_
- engine/llm_watchdog.py: _(aggiungere descrizione in ROOTPAM)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke end-to-end: test_workflow → blog_post → code_analysis con LM Studio; verificare 03_OUTPUT e progress bar UI. _(log: 20260522_2200_triangulation_and_workflows.md)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-21 [completed]. Next: Smoke ingest+gap con LM Studio; opzionale LM_STALL_WATCHDOG_S=120 in .env. _(log: 20260521_2100_gpt_audit_fixes.md)_

## File toccati dalle IA (non ancora in mappa)
- scripts/verify_post_fix_checklist.py: _(aggiungere descrizione in ROOTPAM)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke end-to-end: test_workflow → blog_post → code_analysis con LM Studio; verificare 03_OUTPUT e progress bar UI. _(log: 20260522_2200_triangulation_and_workflows.md)_
- tests/test_blog_post_workflow.py: _(aggiungere descrizione in ROOTPAM)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke end-to-end: test_workflow → blog_post → code_analysis con LM Studio; verificare 03_OUTPUT e progress bar UI. _(log: 20260522_2200_triangulation_and_workflows.md)_
- tests/test_code_analysis_workflow.py: _(aggiungere descrizione in ROOTPAM)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke end-to-end: test_workflow → blog_post → code_analysis con LM Studio; verificare 03_OUTPUT e progress bar UI. _(log: 20260522_2200_triangulation_and_workflows.md)_
- tests/test_gpt_audit_fixes.py: _(aggiungere descrizione in ROOTPAM)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke end-to-end: test_workflow → blog_post → code_analysis con LM Studio; verificare 03_OUTPUT e progress bar UI. _(log: 20260522_2200_triangulation_and_workflows.md)_
- tests/test_ingest_processor.py: _(aggiungere descrizione in ROOTPAM)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke end-to-end: test_workflow → blog_post → code_analysis con LM Studio; verificare 03_OUTPUT e progress bar UI. _(log: 20260522_2200_triangulation_and_workflows.md)_
- tests/test_project_memory.py: _(aggiungere descrizione in ROOTPAM)_
↳ [🧠 STORICO AI]: Modificato da Cursor il 2026-05-22 [completed]. Next: Smoke end-to-end: test_workflow → blog_post → code_analysis con LM Studio; verificare 03_OUTPUT e progress bar UI. _(log: 20260522_2200_triangulation_and_workflows.md)_

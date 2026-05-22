---
session_id: 20260522_ui_deadlock
date: 2026-05-22
agent_used: Cursor
target_files:
  - engine/job_runner.py
  - engine/orchestrator.py
  - server.py
  - ui/static/app.js
  - engine/workflow_runner.py
  - workflows/test_workflow.py
  - tests/test_job_runner.py
status: completed
next_steps: "Smoke test: test_workflow su UI, poi ingest con LM Studio attivo."
---

# Fix tre cavi UI ↔ backend (DiagnosiTre cav)

## Problemi risolti

1. Deadlock worker: `is_job_running()` + `qsize` nel loop → sostituito con `_worker_busy()` (solo `running`).
2. Deadlock RLock: `threading.RLock()` in `start_job` → `_ensure_worker`.
3. `/api/workflows`: risposta `{ workflows: [...] }` + `loadWorkflows()` tollerante.

## Extra

- `is_job_running()` true anche con job in coda (anti doppio START).
- `last_job` su orchestrator per badge Failed dopo completamento.
- Workflow `test_workflow` per test senza LLM.

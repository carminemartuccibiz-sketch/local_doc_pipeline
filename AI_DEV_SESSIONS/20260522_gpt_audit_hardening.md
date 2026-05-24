---
session_id: 20260522_gpt_audit
date: 2026-05-22
agent_used: Cursor
source: docs/guides/gpt-comand-dir/# 🚨 1. Sicurezza, T.md
target_files:
  - clients/http_pool.py
  - engine/orchestrator.py
  - engine/cooldown_manager.py
  - engine/http_serve.py
  - engine/ingest_processor.py
  - engine/workflow_runner.py
  - core/ai_tasks.py
  - clients/anythingllm.py
  - core/gap_allm.py
  - server.py
  - app.py
  - ui/static/app.js
  - config/runtime.py
  - requirements.txt
status: completed
next_steps: "Smoke UI+ingest; valutare semantic_diff workflow (audit §4)."
---

# Hardening post-audit GPT (Principal Architect)

## §1 Sicurezza / threading
- Pool `httpx` condiviso LM/ALLM + `close_all_http_clients()` nel kill switch
- `OrchestratorState`: RLock, coda log bounded (500), snapshot client su STOP
- Cooldown: `stop_event.wait()` invece di sleep a fette
- SSE: `GeneratorExit`, heartbeat 0.5s
- Server: Waitress se installato (`engine/http_serve.py`)

## §2 Pipeline AI
- RAG `GAP_RAG_SCORE_THRESHOLD` default 0.45 (env override)
- Sliding window: contesto rolling ultimi 2 condensati; overlap fence-safe

## §3 UX / qualità
- Log UI batched via `requestAnimationFrame`
- `WorkflowCapabilities` su registry plugin

## Non implementato (backlog)
- Watchdog LLM 600s, dedup MinHash, token budget dinamico, mypy strict, semantic_diff workflow

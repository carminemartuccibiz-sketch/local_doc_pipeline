---
session_id: 20260521_2100_gpt_audit_fixes
date: 2026-05-21
agent_used: Cursor
source: docs/guides/gpt-comand-dir/# 🚨 1. Sicurezza, T.md
target_files:
  - clients/http_pool.py
  - engine/orchestrator.py
  - engine/cooldown_manager.py
  - engine/http_serve.py
  - engine/llm_watchdog.py
  - engine/ingest_processor.py
  - core/ai_tasks.py
  - clients/anythingllm.py
  - core/gap_allm.py
  - config/runtime.py
  - server.py
  - app.py
  - engine/job_runner.py
status: completed
next_steps: "Smoke ingest+gap con LM Studio; opzionale LM_STALL_WATCHDOG_S=120 in .env."
---

# Fix audit GPT — Sezioni 1 e 2 (Sicurezza + Pipeline)

Riferimento: Deep Audit ChatGPT su `_LLM_CONTEXT_DUMP.txt`. Sezioni 3–4 ignorate in questa passata.

## Vulnerabilità / rischi risolti (§1 Sicurezza, Threading, Resilienza)

- **1.1** — Client `httpx` creato/distrutto per ogni request: pool condiviso LM/ALLM con `Limits` e timeout granulari (`clients/http_pool.py`); integrato in `ai_tasks` e `anythingllm`.
- **1.2** — Race su registry kill switch: `threading.RLock` su `OrchestratorState`, iterazione su **snapshot** `list(active_requests)`; chiusura pool atomica.
- **1.3** — SSE memory leak: coda log **bounded** (500, drop oldest); generator con `GeneratorExit`; heartbeat 0.5s (nessuna lista subscriber globale).
- **1.4** — Flask dev server fragile con SSE: avvio via **Waitress** se installato (`engine/http_serve.py`), fallback Flask.
- **1.5** — `time.sleep` non interrompibile: cooldown e `test_workflow` usano `stop_event.wait(timeout)` (kill switch immediato).
- **1.6** — Nessun watchdog su LLM lunghi: `engine/llm_watchdog.py` + hook in `_llm_complete_unlocked` (opt-in `LM_STALL_WATCHDOG_S` > 0, chiude pool HTTP su stall).

## Conservato (fix precedenti DiagnosiTre cav)

- `threading.RLock()` e `_worker_busy()` in `engine/job_runner.py` — **non modificati** nella logica anti-deadlock.
- `last_job` / UI Failed+RESET dopo job terminato.

## Ottimizzazioni pipeline (§2)

- **2.1** — Overlap caratteri su Markdown: chunking strutturale esistente (`split_markdown_sections`) + `_safe_overlap_tail()` (non spezza fenced code).
- **2.2** — Context poisoning sliding window: contesto **rolling** ultimi 2 condensati (non solo l’ultimo).
- **2.3** — Token budget statico ingest: `_resolve_ingest_chunk_tokens()` usa `resolve_token_limits` + `resolve_chunk_max_tokens` dal modello LM attivo.
- **2.5** — RAG threshold 0.25 troppo basso: `GAP_RAG_SCORE_THRESHOLD` default **0.45** (env override).
- **Ingest resilienza** — `try/except` su lettura file, chunking, scrittura chunk/json, chiamate LLM per chunk (errori propagati con messaggio chiaro).

## Backlog audit (non in scope)

- **2.4** — Dedup semantico MinHash/SimHash pre-LLM.
- Parser markdown dedicato (`markdown-it-py`) oltre al chunker attuale.
- Facts canonici persistenti nel global memory (evoluzione verso semantic_diff §4).

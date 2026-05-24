---
session_id: 20260524_afk_master_start
date: 2026-05-24
agent_used: Cursor
source: MASTER_BLUEPRINT_AFK.md
status: in_progress
baseline_pytest: "24 passed"
p0_confirmed:
  - engine/job_runner.py L374 resolve_chunk_max_tokens() senza limits
  - workflows/gap_analysis.py log_fn(..., level=WARN)
dmip_path: E:\DVAMOCLES-SWORD-AMBIENT-FULL-DOCUMENTATION\tools\dmip\
---

# AFK Master Blueprint — avvio esecuzione

Baseline: `py -3.10 -m pytest tests/ -q` → 24 passed.

P0 in corso di fix Fase 1.

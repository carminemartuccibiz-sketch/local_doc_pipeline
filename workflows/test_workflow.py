"""
Test workflow — 3 step da 1 secondo, nessuna chiamata LLM.
Uso: seleziona "Test (no LLM)" dalla UI e premi START.
Permette di verificare START → SSE → STOP senza LM Studio attivo.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from workflows.base_workflow import BaseWorkflow


class TestWorkflow(BaseWorkflow):
    """
    Workflow diagnostico: emette log ogni secondo per 3 step.
    Non richiede LM Studio, AnythingLLM o file in 01_INGEST.
    """

    STEPS = 3
    STEP_DURATION_S = 1.0

    def process_file(self, file_path: Path, ctx: dict[str, Any]) -> Any:
        log_fn = ctx.get("log_fn") or (lambda m: None)
        stop_event = ctx.get("stop_event")

        log_fn(f"[TEST] Elaboro: {file_path.name}")
        for i in range(1, self.STEPS + 1):
            if stop_event and stop_event.is_set():
                log_fn("[TEST] Interrotto da kill switch")
                raise InterruptedError("test_workflow interrotto")
            log_fn(f"[TEST] Step {i}/{self.STEPS} — {file_path.name}")
            time.sleep(self.STEP_DURATION_S)

        log_fn(f"[TEST] ✓ Completato: {file_path.name}")
        return {"status": "ok", "file": str(file_path)}

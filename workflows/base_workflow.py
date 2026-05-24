"""ABC workflow — process_file(file, ctx) -> Result (FASE 2+)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from workflows.capabilities import WorkflowCapabilities


class BaseWorkflow(ABC):
    capabilities: WorkflowCapabilities = WorkflowCapabilities()

    @abstractmethod
    def process_file(self, file_path: Path, ctx: dict[str, Any]) -> Any:
        """
        Elabora un singolo file nel contesto del progetto attivo.

        ctx tipico: slug, stop_event, log_fn, orchestrator (OrchestratorState).
        Per salvare output in 03_OUTPUT usare engine.project_memory.save_workflow_output
        con state=ctx.get("orchestrator"); bump_progress=False se il job_runner
        incrementa già files_completed dopo process_file.
        """

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

        ctx tipico: slug, stop_event, log_fn, orchestrator (OrchestratorState),
        file_index, files_in_job (impostati da job_runner).
        Tra fasi LLM/save chiamare workflows.workflow_progress.report_phase (o
        report_llm_start / report_save) per aggiornare progress_percent UI.
        Per salvare output usare save_workflow_output con state=ctx["orchestrator"];
        bump_progress=False — job_runner chiama bump_files_completed() a fine file.
        """

"""
Plugin loader workflow — registro con nome display e metadata.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from workflows.base_workflow import BaseWorkflow
from workflows.blog_post import BlogPostWorkflow
from workflows.code_analysis import CodeAnalysisWorkflow
from workflows.gap_analysis import GapAnalysisWorkflow
from workflows.test_workflow import TestWorkflow

logger = logging.getLogger(__name__)

# Registro: slug → (classe, label UI, descrizione)
_REGISTRY: dict[str, tuple[type[BaseWorkflow], str, str]] = {
    "gap_analysis": (
        GapAnalysisWorkflow,
        "Gap Analysis (LAST DOCS)",
        "Confronto grezzo vs SOT — richiede LM Studio + AnythingLLM",
    ),
    "blog_post": (
        BlogPostWorkflow,
        "Blog Post (stub)",
        "Generazione post da documenti — da implementare",
    ),
    "code_analysis": (
        CodeAnalysisWorkflow,
        "Code Analysis (stub)",
        "Analisi codice sorgente — da implementare",
    ),
    "test_workflow": (
        TestWorkflow,
        "Test (no LLM, 3 step)",
        "Workflow diagnostico — verifica START/STOP/SSE senza LM Studio",
    ),
}


class WorkflowRunner:
    def get_workflow(self, name: str) -> BaseWorkflow | None:
        entry = _REGISTRY.get(name.strip().lower())
        if entry is None:
            return None
        cls, _, _ = entry
        return cls()

    def run_file(self, workflow_name: str, file_path: Path, ctx: dict[str, Any]) -> Any:
        wf = self.get_workflow(workflow_name)
        if wf is None:
            raise ValueError(f"Workflow sconosciuto: {workflow_name!r}")
        return wf.process_file(file_path, ctx)

    @staticmethod
    def registered() -> list[str]:
        """Slug dei workflow registrati (per server.py)."""
        return sorted(_REGISTRY.keys())

    @staticmethod
    def registered_with_meta() -> list[dict[str, str]]:
        """Lista completa con id/label/description per la UI."""
        return [
            {"id": slug, "label": label, "description": desc}
            for slug, (_, label, desc) in sorted(_REGISTRY.items())
        ]

"""
Plugin loader workflow — registro con nome display, metadata e capabilities.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from workflows.base_workflow import BaseWorkflow
from workflows.blog_post import BlogPostWorkflow
from workflows.capabilities import WorkflowCapabilities
from workflows.code_analysis import CodeAnalysisWorkflow
from workflows.gap_analysis import GapAnalysisWorkflow
from workflows.test_workflow import TestWorkflow

logger = logging.getLogger(__name__)

# slug → (classe, label UI, descrizione, capabilities)
_REGISTRY: dict[str, tuple[type[BaseWorkflow], str, str, WorkflowCapabilities]] = {
    "gap_analysis": (
        GapAnalysisWorkflow,
        "Gap Analysis (LAST DOCS)",
        "Confronto grezzo vs SOT — richiede LM Studio + AnythingLLM",
        WorkflowCapabilities(requires_llm=True, requires_rag=True, supports_cancel=True),
    ),
    "blog_post": (
        BlogPostWorkflow,
        "Blog Post (Apple-style)",
        "Trasforma documenti da 01_INGEST in articoli Markdown in 03_OUTPUT/blog_posts/",
        WorkflowCapabilities(requires_llm=True, requires_rag=False, supports_cancel=True),
    ),
    "code_analysis": (
        CodeAnalysisWorkflow,
        "Code Analysis (DevSecOps)",
        "Code review LLM (architettura, vulnerabilità, refactoring) → 03_OUTPUT/code_reviews/",
        WorkflowCapabilities(requires_llm=True, requires_rag=False, supports_cancel=True),
    ),
    "test_workflow": (
        TestWorkflow,
        "Test (no LLM, 3 step)",
        "Workflow diagnostico — verifica START/STOP/SSE senza LM Studio",
        WorkflowCapabilities(requires_llm=False, requires_rag=False, supports_cancel=True),
    ),
}


class WorkflowRunner:
    def get_workflow(self, name: str) -> BaseWorkflow | None:
        entry = _REGISTRY.get(name.strip().lower())
        if entry is None:
            return None
        cls, _, _, _ = entry
        return cls()

    def get_capabilities(self, name: str) -> WorkflowCapabilities | None:
        entry = _REGISTRY.get(name.strip().lower())
        if entry is None:
            return None
        return entry[3]

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
        """Lista completa con id/label/description/capabilities per la UI."""
        out: list[dict[str, str]] = []
        for slug, (_, label, desc, caps) in sorted(_REGISTRY.items()):
            row: dict[str, str] = {
                "id": slug,
                "label": label,
                "description": desc,
                "requires_llm": str(caps.requires_llm).lower(),
                "requires_rag": str(caps.requires_rag).lower(),
            }
            out.append(row)
        return out

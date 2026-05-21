"""
Plugin loader workflow (FASE 2+).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from workflows.base_workflow import BaseWorkflow
from workflows.blog_post import BlogPostWorkflow
from workflows.code_analysis import CodeAnalysisWorkflow
from workflows.gap_analysis import GapAnalysisWorkflow

logger = logging.getLogger(__name__)

# ingest / sliding_window: eseguiti da job_runner + ingest_processor (non plugin)
_REGISTRY: dict[str, type[BaseWorkflow]] = {
    "gap_analysis": GapAnalysisWorkflow,
    "blog_post": BlogPostWorkflow,
    "code_analysis": CodeAnalysisWorkflow,
}


class WorkflowRunner:
    def get_workflow(self, name: str) -> BaseWorkflow | None:
        cls = _REGISTRY.get(name.strip().lower())
        if cls is None:
            return None
        return cls()

    def run_file(
        self,
        workflow_name: str,
        file_path: Path,
        ctx: dict[str, Any],
    ) -> Any:
        wf = self.get_workflow(workflow_name)
        if wf is None:
            raise ValueError(f"Workflow sconosciuto: {workflow_name}")
        return wf.process_file(file_path, ctx)

    @staticmethod
    def registered() -> list[str]:
        return sorted(_REGISTRY.keys())

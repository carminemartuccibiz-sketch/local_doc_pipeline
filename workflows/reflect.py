"""Self-review di output workflow precedenti."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from core.ai_tasks import abort_if_stop_requested, llm_complete
from engine.project_memory import save_workflow_output, workflow_output_path
from workflows.base_workflow import BaseWorkflow
from workflows.capabilities import WorkflowCapabilities
from workflows.workflow_progress import report_llm_start, report_phase, report_save

REFLECT_MAX_INPUT = int(os.environ.get("REFLECT_MAX_INPUT_CHARS", "24000"))

_SYSTEM = """Sei un revisore tecnico. Analizza l'output fornito e produci:
1. Criticità (bullet)
2. Lacune o ambiguità
3. Suggerimenti di miglioramento concreti
Usa Markdown breve. Non riscrivere l'intero documento."""


class ReflectWorkflow(BaseWorkflow):
    capabilities = WorkflowCapabilities(
        requires_llm=True,
        requires_rag=False,
        supports_cancel=True,
    )

    def process_file(self, file_path: Path, ctx: dict[str, Any]) -> dict[str, Any]:
        slug = ctx.get("slug")
        if not slug:
            raise ValueError("ctx['slug'] richiesto")
        log_fn: Callable[[str], None] = ctx.get("log_fn") or (lambda _m: None)
        state = ctx.get("orchestrator")

        target_wf = ctx.get("reflect_workflow") or "blog_posts"
        target_name = ctx.get("reflect_output") or f"{file_path.stem}.md"
        src_path = workflow_output_path(slug, target_wf, target_name)
        if not src_path.is_file():
            src_path = file_path

        total_phases = 3
        report_phase(
            ctx,
            tag="REFLECT",
            phase=1,
            total=total_phases,
            label=f"Lettura output: {src_path.name}",
            file_path=file_path,
        )
        body = src_path.read_text(encoding="utf-8", errors="replace")[:REFLECT_MAX_INPUT]
        abort_if_stop_requested()
        report_llm_start(
            ctx,
            tag="REFLECT",
            phase=2,
            total=total_phases,
            file_path=file_path,
            detail="Revisione critica LLM",
        )
        critique = llm_complete(
            system_prompt=_SYSTEM,
            user_message=f"OUTPUT DA REVISIONARE:\n\n{body}",
            temperature=0.15,
            max_tokens=1200,
        )
        report_save(
            ctx,
            tag="REFLECT",
            phase=3,
            total=total_phases,
            file_path=file_path,
            subdir="reviews",
        )
        out_path = save_workflow_output(
            slug,
            "reviews",
            f"{src_path.stem}.review.md",
            critique,
            source_file=src_path.name,
            state=state,
            current_file=file_path.name,
        )
        return {
            "status": "ok",
            "workflow": "reflect",
            "source": src_path.name,
            "output": str(out_path),
        }

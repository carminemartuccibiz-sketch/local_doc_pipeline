"""DevRel pipeline: code_analysis → blog_post."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from engine.project_memory import workflow_output_path
from workflows.base_workflow import BaseWorkflow
from workflows.capabilities import WorkflowCapabilities
from workflows.workflow_progress import report_phase


class DevBlogWorkflow(BaseWorkflow):
    capabilities = WorkflowCapabilities(
        requires_llm=True,
        requires_rag=False,
        supports_cancel=True,
    )

    def process_file(self, file_path: Path, ctx: dict[str, Any]) -> dict[str, Any]:
        slug = ctx.get("slug")
        if not slug:
            raise ValueError("ctx['slug'] richiesto")
        stop_event = ctx.get("stop_event")
        log_fn: Callable[[str], None] = ctx.get("log_fn") or (lambda _m: None)
        from engine.workflow_runner import WorkflowRunner

        runner = WorkflowRunner()

        total_phases = 3
        report_phase(
            ctx,
            tag="DEVBLOG",
            phase=1,
            total=total_phases,
            label="Code analysis (sotto-workflow)",
            file_path=file_path,
        )
        ca_res = runner.run_file("code_analysis", file_path, ctx)
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError("DevBlog interrotto dopo code_analysis")

        review_path = workflow_output_path(
            slug,
            "code_reviews",
            f"{file_path.stem}.code_review.md",
        )
        if not review_path.is_file():
            raise FileNotFoundError(f"Report code review non trovato: {review_path}")

        report_phase(
            ctx,
            tag="DEVBLOG",
            phase=2,
            total=total_phases,
            label=f"Blog post da {review_path.name}",
            file_path=file_path,
        )
        blog_ctx = dict(ctx)
        blog_ctx["source_override"] = review_path
        blog_res = runner.run_file("blog_post", file_path, blog_ctx)

        report_phase(
            ctx,
            tag="DEVBLOG",
            phase=3,
            total=total_phases,
            label="Pipeline DevBlog completata",
            file_path=file_path,
        )

        return {
            "status": "ok",
            "workflow": "devblog",
            "source": file_path.name,
            "code_review": ca_res,
            "blog_post": blog_res,
        }

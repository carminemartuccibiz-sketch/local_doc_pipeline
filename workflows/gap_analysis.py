"""
Workflow gap analysis — plugin UI su core.gap_runner (blueprint).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from config import DEFAULT_SOURCE_ROOT, GAP_USE_ALLM_RAG
from core.gap_allm import set_gap_allm_memory_dir
from core.gap_runner import open_gap_pipeline
from core.paths import default_sot_directories
from core.session_state import PipelineSessionState
from engine.project_memory import gap_report_path, ingest_dir, memory_dir
from engine.project_store import load_file_roles, load_project
from workflows.base_workflow import BaseWorkflow

logger = logging.getLogger(__name__)


def _gap_log(
    log_fn: Callable[[str], None] | None,
    msg: str,
    level: str = "INFO",
) -> None:
    if not log_fn:
        return
    prefix = {"WARN": "[WARN] ", "ERROR": "[ERROR] "}.get(level, "")
    log_fn(f"{prefix}{msg}")


def resolve_project_sot_paths(slug: str) -> list[Path]:
    """SOT da file_roles (02_REFERENCE) o default repo documentazione."""
    project_dir = ingest_dir(slug).parent
    roles = load_file_roles(slug)
    paths: list[Path] = []
    for rel, role in roles.items():
        if role != "SOT":
            continue
        p = (project_dir / rel).resolve()
        if p.is_file():
            paths.append(p)
        elif p.is_dir():
            paths.append(p)
    if paths:
        return paths
    return default_sot_directories(DEFAULT_SOURCE_ROOT)


def run_project_gap_analysis(
    slug: str,
    *,
    stop_event=None,
    log_fn: Callable[[str], None] | None = None,
    limit: int | None = None,
    files_per_iteration: int = 1,
) -> int:
    """
    Gap analysis su projects/<slug>/01_INGEST con state in 04_MEMORY/.
    Report in 03_OUTPUT/Gap_Report_Generale.md.
    """
    ingest_root = ingest_dir(slug)
    if not ingest_root.is_dir():
        raise FileNotFoundError(f"Ingest mancante: {ingest_root}")

    has_files = any(
        p.is_file() and not p.name.startswith(".")
        for p in ingest_root.iterdir()
    )
    if not has_files:
        if log_fn:
            log_fn("[GAP] Nessun file in 01_INGEST")
        return 0

    mem = memory_dir(slug)
    set_gap_allm_memory_dir(mem)
    try:
        st = PipelineSessionState(mem / "pipeline_state.json")
        cumulative = gap_report_path(slug)
        cumulative.parent.mkdir(parents=True, exist_ok=True)

        meta = load_project(slug)
        integrate = bool(meta.get("gap_integrate", False))
        append_only = not integrate

        if log_fn:
            log_fn(f"[GAP] Apertura pipeline progetto={slug}")

        ctx = open_gap_pipeline(
            repo_root=DEFAULT_SOURCE_ROOT,
            ingest_root=ingest_root,
            sot_paths=resolve_project_sot_paths(slug),
            integrate=integrate,
            append_only=append_only,
            skip_allm=not GAP_USE_ALLM_RAG,
            state=st,
            cumulative=cumulative,
        )
        if ctx is None:
            _gap_log(
                log_fn,
                "[GAP] Preflight fallito (LM Studio / AnythingLLM)",
                level="WARN",
            )
            return 0

        total = 0
        round_n = 0
        while True:
            if stop_event is not None and stop_event.is_set():
                _gap_log(log_fn, "[GAP] Interrotto da kill switch", level="WARN")
                break

            ctx.st.load()
            before = ctx.st.work_remaining(ctx.all_keys)
            if before == 0:
                if log_fn:
                    log_fn("[GAP] Coda completata")
                break

            round_n += 1
            from core.gap_runner import run_gap_file_batch

            n = run_gap_file_batch(ctx, limit=files_per_iteration)
            total += n
            if log_fn:
                log_fn(
                    f"[GAP] Iter {round_n}: +{n} file "
                    f"(totale sessione {total}, in coda {ctx.st.work_remaining(ctx.all_keys)})"
                )

            if limit is not None and total >= limit:
                break
            if n == 0:
                break

        return total
    finally:
        set_gap_allm_memory_dir(None)


class GapAnalysisWorkflow(BaseWorkflow):
    """Elabora un singolo file ingest nel contesto progetto (batch via job_runner)."""

    def process_file(self, file_path: Path, ctx: dict[str, Any]) -> Any:
        slug = ctx.get("slug")
        if not slug:
            raise ValueError("ctx['slug'] richiesto")
        stop_event = ctx.get("stop_event")
        log_fn = ctx.get("log_fn")
        return run_project_gap_analysis(
            slug,
            stop_event=stop_event,
            log_fn=log_fn,
            limit=1,
            files_per_iteration=1,
        )

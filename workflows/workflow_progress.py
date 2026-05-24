"""
Aggiornamenti thread-safe OrchestratorState durante fasi lunghe (LLM / save).

Usare in ogni plugin workflow tra una fase e l'altra così la progress bar UI
si muove anche prima del bump_files_completed() in job_runner.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


def _state(ctx: dict[str, Any]) -> Any:
    return ctx.get("orchestrator")


def _file_index(ctx: dict[str, Any]) -> int:
    if "file_index" in ctx:
        return int(ctx["file_index"])
    state = _state(ctx)
    if state is None:
        return 0
    snap = state.get_current_job_snapshot()
    if not snap:
        return 0
    return int(snap.get("files_completed") or 0)


def report_phase(
    ctx: dict[str, Any],
    *,
    tag: str,
    phase: int,
    total: int,
    label: str,
    file_path: Path | str,
) -> None:
    """Log SSE + aggiorna current_file / progress_percent sull'orchestrator."""
    name = Path(file_path).name
    log_fn: Callable[[str], None] = ctx.get("log_fn") or (lambda _m: None)
    log_fn(f"[{tag}] Fase {phase}/{total} — {label}")

    state = _state(ctx)
    if state is None:
        return
    updater = getattr(state, "update_phase_progress", None)
    if callable(updater):
        updater(
            current_file=name,
            phase_label=label,
            phase_index=phase,
            phase_total=max(1, total),
            file_index=_file_index(ctx),
        )


def report_llm_start(
    ctx: dict[str, Any],
    *,
    tag: str,
    phase: int,
    total: int,
    file_path: Path | str,
    detail: str = "chiamata LLM",
) -> None:
    report_phase(
        ctx,
        tag=tag,
        phase=phase,
        total=total,
        label=detail,
        file_path=file_path,
    )


def report_save(
    ctx: dict[str, Any],
    *,
    tag: str,
    phase: int,
    total: int,
    file_path: Path | str,
    subdir: str,
) -> None:
    report_phase(
        ctx,
        tag=tag,
        phase=phase,
        total=total,
        label=f"Salvataggio in 03_OUTPUT/{subdir}/",
        file_path=file_path,
    )

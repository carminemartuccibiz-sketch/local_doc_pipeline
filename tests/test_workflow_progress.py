"""Progress bar intra-file — OrchestratorState + workflow_progress."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from engine.orchestrator import reset_orchestrator
from workflows.workflow_progress import report_phase, report_save


def test_update_phase_progress_sets_percent() -> None:
    state = reset_orchestrator()
    state.init_current_job(
        project="demo",
        workflow="code_analysis",
        status="running",
        files_total=2,
        files_completed=0,
    )
    state.update_phase_progress(
        current_file="main.py",
        phase_label="Code review LLM",
        phase_index=2,
        phase_total=5,
        file_index=0,
    )
    snap = state.get_current_job_snapshot()
    assert snap is not None
    assert snap["progress_percent"] == 20
    assert "main.py" in snap["current_file"]
    assert "Code review LLM" in snap["current_file"]


def test_report_phase_emits_log_and_updates_state() -> None:
    state = reset_orchestrator()
    state.init_current_job(
        project="demo",
        workflow="blog_post",
        status="running",
        files_total=1,
        files_completed=0,
    )
    logs: list[str] = []

    ctx = {
        "orchestrator": state,
        "log_fn": logs.append,
        "file_index": 0,
    }
    report_phase(
        ctx,
        tag="BLOG",
        phase=2,
        total=3,
        label="Generazione articolo LLM",
        file_path=Path("doc.md"),
    )
    assert any("Fase 2/3" in m for m in logs)
    snap = state.get_current_job_snapshot()
    assert snap["progress_percent"] >= 50


def test_code_analysis_calls_report_phase() -> None:
    from workflows import code_analysis as mod

    text = Path(mod.__file__).read_text(encoding="utf-8")
    assert "report_phase" in text
    assert "report_llm_start" in text
    assert "report_save" in text


def test_bump_files_completed_updates_progress_percent() -> None:
    state = reset_orchestrator()
    state.init_current_job(
        project="demo",
        workflow="code_analysis",
        status="running",
        files_total=2,
        files_completed=0,
    )
    state.bump_files_completed()
    snap = state.get_current_job_snapshot()
    assert snap["files_completed"] == 1
    assert snap["progress_percent"] == 50

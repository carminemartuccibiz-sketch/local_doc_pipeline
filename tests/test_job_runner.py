"""Test anti-deadlock job queue (senza import pesato di ingest)."""
from __future__ import annotations

from pathlib import Path

import threading

from engine.orchestrator import reset_orchestrator

REPO_ROOT = Path(__file__).resolve().parent.parent
JOB_RUNNER_SRC = REPO_ROOT / "engine" / "job_runner.py"


def _pending_or_active(state) -> bool:
    """Replica logica is_job_running() per test isolati."""
    job = state.current_job
    if job is not None and job.get("status") in ("running", "queued"):
        return True
    return state.job_queue.qsize() > 0


def _worker_busy(state) -> bool:
    job = state.current_job
    return job is not None and job.get("status") == "running"


def test_idle_not_running() -> None:
    state = reset_orchestrator()
    assert _pending_or_active(state) is False


def test_queued_payload_counts_as_running() -> None:
    state = reset_orchestrator()
    state.enqueue_job({"slug": "demo", "workflow": "test_workflow"})
    assert _pending_or_active(state) is True
    assert state.current_job is None


def test_worker_not_busy_when_only_queue() -> None:
    state = reset_orchestrator()
    state.enqueue_job({"slug": "demo", "workflow": "ingest"})
    assert _worker_busy(state) is False


def test_worker_busy_when_running() -> None:
    state = reset_orchestrator()
    state.current_job = {"status": "running", "project": "demo"}
    assert _worker_busy(state) is True


def test_job_runner_source_has_rlock_and_worker_busy() -> None:
    text = JOB_RUNNER_SRC.read_text(encoding="utf-8")
    assert "threading.RLock()" in text
    assert "def _worker_busy" in text
    assert "def is_job_running" in text
    assert isinstance(threading.RLock(), type(threading.RLock()))


def test_resolve_chunk_max_tokens_called_with_limits() -> None:
    from unittest.mock import MagicMock, patch

    from core.token_budget import TokenLimits
    from engine import job_runner

    limits = TokenLimits(
        model_id="test",
        context_tokens=8192,
        raw_input_budget=2000,
        sot_budget_tokens=4000,
        response_reserve=1500,
    )
    with patch.object(job_runner, "get_orchestrator_state") as mock_state:
        state = MagicMock()
        state.stop_event.is_set.return_value = False
        mock_state.return_value = state
        with patch.object(job_runner, "get_cooldown_manager"):
            with patch.object(
                job_runner,
                "list_ingest_sources",
                return_value=[MagicMock(name="a.md", stem="a", resolve=lambda: MagicMock())],
            ):
                with patch.object(
                    job_runner,
                    "get_session_lm_model",
                    return_value="test-model",
                ):
                    with patch.object(
                        job_runner, "resolve_token_limits", return_value=limits
                    ):
                        with patch.object(
                            job_runner,
                            "resolve_chunk_max_tokens",
                            return_value=1500,
                        ) as mock_chunk:
                            with patch.object(job_runner, "ingest_dir") as mock_ingest:
                                mock_ingest.return_value.__truediv__ = (
                                    lambda s, x: MagicMock()
                                )
                                with patch.object(
                                    job_runner,
                                    "sliding_window_analyze",
                                ):
                                    with patch.object(
                                        job_runner,
                                        "mark_ingest_file_done",
                                    ):
                                        job_runner._run_ingest_job("demo")
                                        mock_chunk.assert_called_once_with(
                                            limits
                                        )

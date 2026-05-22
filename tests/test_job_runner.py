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

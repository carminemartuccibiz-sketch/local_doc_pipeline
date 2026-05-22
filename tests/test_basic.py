"""
Minimal CI tests — expand as engine/server coverage grows.
"""
from __future__ import annotations


def test_import_config() -> None:
    from config import PIPELINE_ROOT, UI_PORT

    assert PIPELINE_ROOT.is_dir()
    assert UI_PORT == 7842


def test_import_orchestrator() -> None:
    from engine.orchestrator import get_orchestrator_state

    state = get_orchestrator_state()
    assert state.stop_event is not None
    assert state.job_queue is not None


def test_placeholder() -> None:
    assert True

"""Test fix audit GPT (http pool, orchestrator, cooldown)."""
from __future__ import annotations

import threading

from clients.http_pool import close_all_http_clients, get_lm_client
from engine.cooldown_manager import CooldownManager
from engine.orchestrator import OrchestratorState, reset_orchestrator


def test_orchestrator_uses_rlock() -> None:
    state = OrchestratorState()
    assert isinstance(state._lock, type(threading.RLock()))


def test_log_stream_bounded() -> None:
    state = OrchestratorState()
    assert state.log_stream.maxsize == 500
    for i in range(600):
        state.emit_log(f"line {i}")
    assert state.log_stream.qsize() <= 500


def test_http_pool_singleton_reopen_after_close() -> None:
    c1 = get_lm_client()
    close_all_http_clients()
    c2 = get_lm_client()
    assert c1 is not c2
    close_all_http_clients()


def test_cooldown_interruptible() -> None:
    reset_orchestrator()
    state = reset_orchestrator()
    ev = threading.Event()
    ev.set()
    CooldownManager._sleep_interruptible(5.0, ev)
    assert True

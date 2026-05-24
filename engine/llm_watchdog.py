"""
Watchdog stall LLM (audit GPT §1.6) — chiusura pool HTTP se la chiamata supera LM_STALL_WATCHDOG_S.

Disabilitato se LM_STALL_WATCHDOG_S=0 (default). Non sostituisce httpx read timeout.
"""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_STALL_S = float(os.environ.get("LM_STALL_WATCHDOG_S", "0") or "0")


def run_with_llm_watchdog(fn: Callable[[], T], *, label: str = "llm") -> T:
    if _STALL_S <= 0:
        return fn()

    from engine.orchestrator import get_orchestrator_state

    state = get_orchestrator_state()
    done = threading.Event()

    def _watch() -> None:
        if done.wait(_STALL_S):
            return
        logger.warning("LLM watchdog (%s): stall > %ss", label, _STALL_S)
        try:
            from clients.http_pool import close_all_http_clients

            closed = close_all_http_clients()
            state.emit_log(
                f"[LLM] Watchdog: nessun progresso per {_STALL_S:.0f}s "
                f"({label}) — pool HTTP chiuso ({closed})",
                level="WARN",
            )
        except Exception as e:
            logger.exception("Watchdog close failed: %s", e)

    watcher = threading.Thread(target=_watch, name=f"llm-watchdog-{label}", daemon=True)
    watcher.start()
    try:
        return fn()
    finally:
        done.set()

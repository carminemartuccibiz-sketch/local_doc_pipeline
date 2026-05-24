"""
FASE 5 — Pause/cooldown configurabili (profilo HW / .env).
"""
from __future__ import annotations

import os
import time
from typing import Any


class CooldownManager:
    """
    Legge dal profilo HW attivo (variabili d'ambiente):
      PIPELINE_LM_COOLDOWN_S, PIPELINE_CHUNK_COOLDOWN_S, PIPELINE_FILE_PAUSE_S
    """

    def __init__(self) -> None:
        self.reload()

    def reload(self) -> None:
        self.lm_cooldown = float(os.environ.get("PIPELINE_LM_COOLDOWN_S", "1.5"))
        self.chunk_cooldown = float(os.environ.get("PIPELINE_CHUNK_COOLDOWN_S", "0.5"))
        self.file_pause = float(os.environ.get("PIPELINE_FILE_PAUSE_S", "0"))

    @staticmethod
    def _sleep_interruptible(seconds: float, stop_event: Any) -> None:
        if seconds <= 0:
            return
        if stop_event is None:
            time.sleep(seconds)
            return
        # Audit GPT §1.5: wait interrompibile dal kill switch
        if stop_event.wait(seconds):
            return

    def after_llm_call(self, stop_event: Any) -> None:
        self._sleep_interruptible(self.lm_cooldown, stop_event)

    def after_chunk(self, stop_event: Any) -> None:
        self._sleep_interruptible(self.chunk_cooldown, stop_event)

    def after_file(self, stop_event: Any) -> None:
        self._sleep_interruptible(self.file_pause, stop_event)


_manager: CooldownManager | None = None


def get_cooldown_manager() -> CooldownManager:
    global _manager
    if _manager is None:
        _manager = CooldownManager()
    return _manager

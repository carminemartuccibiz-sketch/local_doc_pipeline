"""
Coda job con priorità e cancellazione (FASE 2 / blueprint).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from queue import Empty, PriorityQueue
from typing import Any


@dataclass(order=True)
class JobItem:
    priority: int
    seq: int = field(compare=True)
    payload: dict[str, Any] = field(compare=False, default_factory=dict)


class JobQueue:
    """Wrapper PriorityQueue + lock; integrato con OrchestratorState.job_queue."""

    def __init__(self) -> None:
        self._q: PriorityQueue = PriorityQueue()
        self._seq = 0
        self._lock = threading.Lock()

    def put(self, payload: dict[str, Any], *, priority: int = 10) -> int:
        with self._lock:
            self._seq += 1
            seq = self._seq
            self._q.put(JobItem(priority, seq, payload))
            return seq

    def get(self, timeout: float | None = None) -> dict[str, Any] | None:
        try:
            if timeout is None:
                item = self._q.get_nowait()
            else:
                item = self._q.get(timeout=timeout)
        except Empty:
            return None
        return dict(item.payload)

    def drain(self) -> int:
        n = 0
        while self.get() is not None:
            n += 1
        return n

    def qsize(self) -> int:
        return self._q.qsize()

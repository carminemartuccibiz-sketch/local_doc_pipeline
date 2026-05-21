"""
engine/orchestrator.py — Kill switch a tre livelli + stato condiviso UI/worker.

Task 3 (blueprint):
  Livello 1: stop_event.set() — segnala stop ai worker
  Livello 2: chiusura client httpx in volo
  Livello 3: svuota job_queue (+ log SSE in FASE 2/6)
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from queue import Empty, Queue
from typing import Any

import httpx

from engine.interaction_logger import get_interaction_logger, log_app_system, setup_app_system_logger
from engine.job_queue import JobQueue

setup_app_system_logger()
logger = logging.getLogger(__name__)


@dataclass
class OrchestratorState:
    stop_event: threading.Event = field(default_factory=threading.Event)
    active_requests: list[httpx.Client] = field(default_factory=list)
    job_queue: JobQueue = field(default_factory=JobQueue)
    current_job: dict[str, Any] | None = None
    log_stream: Queue = field(default_factory=Queue)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def active_http_clients(self) -> list[httpx.Client]:
        """Alias usato da core.ai_tasks (FASE 2)."""
        return self.active_requests

    def register_client(self, client: httpx.Client) -> None:
        with self._lock:
            self.active_requests.append(client)

    def unregister_client(self, client: httpx.Client) -> None:
        with self._lock:
            try:
                self.active_requests.remove(client)
            except ValueError:
                pass

    def emit_log(self, msg: str, level: str = "INFO") -> None:
        """Messaggi verso /api/logs/stream (SSE)."""
        entry = {
            "msg": msg,
            "level": level,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        self.log_stream.put(entry)
        logger.log(
            logging.INFO if level == "INFO" else logging.WARNING,
            msg,
        )

    def kill_all(self) -> None:
        """
        Livello 1: segnala stop a tutti i thread worker.
        Livello 2: chiude i client httpx attivi (annulla richieste in volo).
        Livello 3: svuota la job queue (+ reset job corrente, log UI).
        """
        logger.warning("KILL SWITCH attivato")
        log_app_system("KILL SWITCH attivato", level=logging.WARNING)
        self.stop_event.set()

        closed_clients = 0
        with self._lock:
            for client in list(self.active_requests):
                try:
                    client.close()
                    closed_clients += 1
                    logger.info("Client HTTP chiuso: %s", id(client))
                except Exception:
                    pass
            self.active_requests.clear()

        drained = self.job_queue.drain()
        if drained:
            logger.info("Job queue svuotata: %d elementi", drained)

        try:
            get_interaction_logger().log_system_event(
                "KILL_SWITCH",
                {
                    "active_clients_closed": closed_clients,
                    "jobs_drained": drained,
                    "had_current_job": self.current_job is not None,
                },
            )
        except Exception:
            pass

        self.current_job = None
        self.emit_log("STOP attivato", level="WARN")

    def enqueue_job(self, payload: dict[str, Any], *, priority: int = 10) -> int:
        seq = self.job_queue.put(payload, priority=priority)
        try:
            get_interaction_logger().log_system_event(
                "JOB_ENQUEUED",
                {"payload": payload, "priority": priority, "seq": seq},
            )
        except Exception:
            pass
        return seq

    @staticmethod
    def _drain_queue(q: Queue) -> None:
        while True:
            try:
                q.get_nowait()
            except Empty:
                break


_state = OrchestratorState()


def get_orchestrator_state() -> OrchestratorState:
    """Singleton globale condiviso con la UI (Task 3)."""
    return _state


def get_state() -> OrchestratorState:
    """Alias interno pipeline / server."""
    return _state


def reset_orchestrator() -> OrchestratorState:
    """Reset singleton dopo stop — pronto per un nuovo job."""
    global _state
    _state = OrchestratorState()
    try:
        get_interaction_logger().log_system_event("ORCHESTRATOR_RESET", {})
        log_app_system("Orchestrator reset — nuovo stato")
    except Exception:
        pass
    return _state


reset_state = reset_orchestrator

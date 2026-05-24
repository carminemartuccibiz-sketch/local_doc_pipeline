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
from queue import Empty, Full, Queue
from typing import Any

import httpx

from engine.interaction_logger import get_interaction_logger, log_app_system, setup_app_system_logger
from engine.job_queue import JobQueue

setup_app_system_logger()
logger = logging.getLogger(__name__)

LOG_STREAM_MAX = 500


def _bounded_log_queue() -> Queue:
    return Queue(maxsize=LOG_STREAM_MAX)


@dataclass
class OrchestratorState:
    stop_event: threading.Event = field(default_factory=threading.Event)
    active_requests: list[httpx.Client] = field(default_factory=list)
    job_queue: JobQueue = field(default_factory=JobQueue)
    current_job: dict[str, Any] | None = None
    # Task 3 (DiagnosiTre cav): persiste dopo completamento/stop (doc: _last_job)
    last_job: dict[str, Any] | None = None
    log_stream: Queue = field(default_factory=_bounded_log_queue)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    @property
    def _last_job(self) -> dict[str, Any] | None:
        """Alias blueprint (Task 3) — stesso campo di last_job."""
        return self.last_job

    @_last_job.setter
    def _last_job(self, value: dict[str, Any] | None) -> None:
        self.last_job = value

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

    def get_current_job_snapshot(self) -> dict[str, Any] | None:
        """Copia thread-safe di current_job per API / plugin."""
        with self._lock:
            if self.current_job is None:
                return None
            return dict(self.current_job)

    def init_current_job(self, **fields: Any) -> dict[str, Any]:
        """Crea current_job all'avvio worker (sotto RLock)."""
        with self._lock:
            self.current_job = dict(fields)
            return dict(self.current_job)

    def update_current_job(self, **fields: Any) -> None:
        """Aggiorna campi job (files_total, current_file, status, …) sotto RLock."""
        if not fields:
            return
        with self._lock:
            if self.current_job is None:
                return
            self.current_job.update(fields)

    def bump_files_completed(
        self,
        delta: int = 1,
        *,
        current_file: str | None = None,
    ) -> None:
        """Avanza contatore progress bar UI (files_completed)."""
        if delta == 0:
            return
        with self._lock:
            if self.current_job is None:
                return
            if current_file is not None:
                self.current_job["current_file"] = current_file
            prev = int(self.current_job.get("files_completed") or 0)
            self.current_job["files_completed"] = max(0, prev + delta)

    def bump_files_failed(self, delta: int = 1) -> None:
        with self._lock:
            if self.current_job is None:
                return
            prev = int(self.current_job.get("files_failed") or 0)
            self.current_job["files_failed"] = max(0, prev + delta)

    def clear_current_job(self) -> None:
        with self._lock:
            self.current_job = None

    def record_workflow_output(
        self,
        rel_path: str,
        *,
        workflow: str,
        bump_progress: bool = False,
        current_file: str | None = None,
    ) -> None:
        """
        Registra un file scritto in 03_OUTPUT sul job corrente (outputs_written).
        Opzionale bump di files_completed per plugin che salvano un output per file.
        """
        with self._lock:
            if self.current_job is None:
                return
            if current_file is not None:
                self.current_job["current_file"] = current_file
            outputs: list[str] = list(self.current_job.get("outputs_written") or [])
            outputs.append(rel_path)
            self.current_job["outputs_written"] = outputs[-200:]
            self.current_job["last_output"] = rel_path
            self.current_job["last_output_workflow"] = workflow
            if bump_progress:
                prev = int(self.current_job.get("files_completed") or 0)
                self.current_job["files_completed"] = prev + 1

    def emit_log(self, msg: str, level: str = "INFO") -> None:
        """Messaggi verso /api/logs/stream (SSE) — coda bounded (audit §1.3)."""
        entry = {
            "msg": msg,
            "level": level,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self.log_stream.put_nowait(entry)
        except Full:
            try:
                self.log_stream.get_nowait()
            except Empty:
                pass
            try:
                self.log_stream.put_nowait(entry)
            except Full:
                pass
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

        from clients.http_pool import close_all_http_clients

        closed_clients = close_all_http_clients()
        with self._lock:
            snapshot = list(self.active_requests)
            self.active_requests.clear()
        for client in snapshot:
            try:
                if not client.is_closed:
                    client.close()
                    closed_clients += 1
                    logger.info("Client HTTP chiuso: %s", id(client))
            except Exception:
                pass

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

        # FIX Task 3: salva ultimo stato prima di azzerare current_job
        snap = self.get_current_job_snapshot()
        if snap:
            with self._lock:
                self._last_job = {**snap, "status": "stopped"}
        self.clear_current_job()
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

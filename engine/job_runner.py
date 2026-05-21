"""
Esecuzione job in background — coda orchestrator + ingest / gap (FASE 6).
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from config.hardware_profiles import PROFILE_ALIASES, PROFILES
from core.ai_tasks import init_gap_analysis_session, llm_complete
from core.token_budget import resolve_chunk_max_tokens
from engine.cooldown_manager import get_cooldown_manager
from engine.interaction_logger import get_interaction_logger, log_app_system
from engine.ingest_processor import sliding_window_analyze
from engine.model_router import get_model_router
from engine.orchestrator import get_orchestrator_state, reset_orchestrator
from engine.project_memory import ingest_dir
from engine.project_store import list_ingest_sources, load_project, mark_ingest_file_done
from workflows.gap_analysis import run_project_gap_analysis

_UI_PROFILE_ALIASES = {
    **PROFILE_ALIASES,
    "fast": "I9_2080TI_32GB",
    "deep": "I9_2080TI_32GB",
}

logger = logging.getLogger(__name__)

_worker_thread: threading.Thread | None = None
_worker_lock = threading.Lock()


def apply_hardware_profile(profile_name: str) -> dict[str, str]:
    """Applica variabili profilo HW al processo corrente."""
    import os

    key = profile_name.strip().lower()
    resolved = _UI_PROFILE_ALIASES.get(key, key)
    if resolved not in PROFILES and resolved.upper() in PROFILES:
        resolved = resolved.upper()
    profile = PROFILES.get(resolved)
    if not profile:
        raise ValueError(f"Profilo sconosciuto: {profile_name}")

    for env_key, value in profile.items():
        os.environ[env_key] = str(value)
    return dict(profile)


def is_job_running() -> bool:
    state = get_orchestrator_state()
    job = state.current_job
    if job and job.get("status") in ("running", "queued"):
        return True
    return state.job_queue.qsize() > 0


def _ensure_worker() -> None:
    global _worker_thread
    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return
        _worker_thread = threading.Thread(
            target=_job_worker_loop,
            name="orchestrator-job-worker",
            daemon=True,
        )
        _worker_thread.start()


def _job_worker_loop() -> None:
    while True:
        state = get_orchestrator_state()
        if state.stop_event.is_set():
            time.sleep(0.3)
            continue

        if is_job_running():
            time.sleep(0.2)
            continue

        payload = state.job_queue.get(timeout=0.5)
        if not payload:
            time.sleep(0.2)
            continue

        slug = str(payload.get("slug") or "")
        workflow = str(payload.get("workflow") or "ingest")
        if not slug:
            continue

        state.current_job = {
            "project": slug,
            "workflow": workflow,
            "status": "running",
            "files_total": 0,
            "files_completed": 0,
            "files_failed": 0,
            "current_file": None,
        }

        try:
            _run_job_worker(slug, workflow)
        except Exception as e:
            logger.exception("Job worker errore")
            state.emit_log(f"[JOB] Errore worker: {e}", level="ERROR")
            if state.current_job:
                state.current_job["status"] = "failed"


def start_job(*, slug: str, workflow: str | None = None) -> dict[str, Any]:
    with _worker_lock:
        if is_job_running():
            raise RuntimeError("Un job è già in esecuzione")

        meta = load_project(slug)
        wf = workflow or meta.get("workflow") or "ingest"
        state = get_orchestrator_state()
        if state.stop_event.is_set():
            reset_orchestrator()
            state = get_orchestrator_state()

        prof = meta.get("hardware_profile", "eco")
        try:
            apply_hardware_profile(prof)
            get_cooldown_manager().reload()
        except ValueError as e:
            state.emit_log(str(e), level="WARN")

        seq = state.enqueue_job({"slug": slug, "workflow": wf}, priority=5)
        state.emit_log(f"[JOB] In coda workflow={wf} progetto={slug}")
        log_app_system(f"Job in coda: {slug} workflow={wf}")
        _ensure_worker()
        return {
            "project": slug,
            "workflow": wf,
            "status": "queued",
            "queue_seq": seq,
            "files_total": 0,
            "files_completed": 0,
            "files_failed": 0,
            "current_file": None,
        }


def _run_job_worker(slug: str, workflow: str) -> None:
    state = get_orchestrator_state()

    try:
        router = get_model_router()
        task = "reasoning" if workflow == "gap_analysis" else "summary"
        model = router.apply_model_for_task(task)
        state.emit_log(f"[JOB] Modello ({task}): {model}")

        require_allm = workflow == "gap_analysis"
        try:
            init_gap_analysis_session(
                require_allm=require_allm,
                force_refresh=False,
            )
        except Exception as e:
            state.emit_log(f"[JOB] Preflight: {e}", level="WARN")
            if workflow == "gap_analysis":
                if state.current_job:
                    state.current_job["status"] = "failed"
                return

        if workflow in ("ingest", "sliding_window"):
            _run_ingest_job(slug)
        elif workflow == "gap_analysis":
            _run_gap_job(slug)
        else:
            state.emit_log(f"[JOB] Workflow sconosciuto: {workflow}", level="ERROR")
            if state.current_job:
                state.current_job["status"] = "failed"
            return

        if state.stop_event.is_set():
            if state.current_job:
                state.current_job["status"] = "stopped"
            return

        if state.current_job:
            state.current_job["status"] = "completed"
        state.emit_log(f"[JOB] Completato progetto={slug}")
    except Exception as e:
        logger.exception("Job fallito")
        state.emit_log(f"[JOB] Errore: {e}", level="ERROR")
        if state.current_job:
            state.current_job["status"] = "failed"
    finally:
        if state.current_job and state.current_job.get("status") == "running":
            state.current_job["status"] = "completed"
        state.current_job = None


def _run_gap_job(slug: str) -> None:
    state = get_orchestrator_state()
    sources = list_ingest_sources(slug, skip_duplicates=True, skip_completed=False)
    if state.current_job:
        state.current_job["files_total"] = len(sources) or 1

    def log_fn(msg: str, level: str = "INFO") -> None:
        state.emit_log(msg, level=level)

    n = run_project_gap_analysis(
        slug,
        stop_event=state.stop_event,
        log_fn=lambda m: log_fn(m),
    )
    if state.current_job:
        state.current_job["files_completed"] = n


def _run_ingest_job(slug: str) -> None:
    state = get_orchestrator_state()
    cooldown = get_cooldown_manager()
    sources = list_ingest_sources(slug)
    if not sources:
        state.emit_log(
            "[JOB] Nessun file nuovo in 01_INGEST — copia documenti e riavvia",
            level="WARN",
        )
        if state.current_job:
            state.current_job["files_total"] = 0
        return

    if state.current_job:
        state.current_job["files_total"] = len(sources)

    max_tokens = resolve_chunk_max_tokens()

    def log_fn(msg: str) -> None:
        state.emit_log(msg)

    ingest_root = ingest_dir(slug)

    for src in sources:
        if state.stop_event.is_set():
            break

        if state.current_job:
            state.current_job["current_file"] = src.name

        file_dir = ingest_root / src.stem
        state.emit_log(f"[JOB] Ingest: {src.name}")

        try:
            sliding_window_analyze(
                src.resolve(),
                file_dir.resolve(),
                llm_complete,
                state.stop_event,
                log_fn,
                max_tokens,
            )
            mark_ingest_file_done(slug, src)
            if state.current_job:
                state.current_job["files_completed"] += 1
            cooldown.after_file(state.stop_event)
        except InterruptedError:
            state.emit_log(f"[JOB] Interrotto su {src.name}", level="WARN")
            break
        except Exception as e:
            if state.current_job:
                state.current_job["files_failed"] += 1
            state.emit_log(f"[JOB] Fallito {src.name}: {e}", level="ERROR")

    if state.current_job:
        state.current_job["current_file"] = None

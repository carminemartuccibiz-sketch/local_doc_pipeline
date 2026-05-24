"""
Esecuzione job in background — coda orchestrator + ingest / gap (FASE 6).

FIXES (docs/guides/claude-commands-dir/## DiagnosiTre cav.md):
  - RLock per rientranza start_job → _ensure_worker
  - Worker loop: blocca solo se current_job.status == "running" (non su qsize)
  - is_job_running(): current_job attivo O job ancora in coda (anti doppio START)
  - Error propagation verso UI via emit_log + last_job
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

from config.hardware_profiles import PROFILE_ALIASES, PROFILES
from core.ai_tasks import (
    abort_if_stop_requested,
    init_gap_analysis_session,
    llm_complete,
    release_lm_http_resources,
)
from core.token_budget import resolve_chunk_max_tokens
from engine.cooldown_manager import get_cooldown_manager
from engine.ingest_processor import sliding_window_analyze
from engine.model_router import get_model_router
from engine.orchestrator import get_orchestrator_state, reset_orchestrator
from engine.project_memory import ingest_dir
from engine.project_store import list_ingest_sources, load_project, mark_ingest_file_done

_UI_PROFILE_ALIASES = {
    **PROFILE_ALIASES,
    "fast": "I9_2080TI_32GB",
    "deep": "I9_2080TI_32GB",
}

_SKIP_LM_WORKFLOWS = frozenset({"test_workflow"})

logger = logging.getLogger(__name__)

_worker_lock = threading.RLock()
_worker_thread: threading.Thread | None = None


def apply_hardware_profile(profile_name: str) -> dict[str, str]:
    """Applica variabili profilo HW al processo corrente."""
    import os

    key = profile_name.strip().lower()
    resolved = _UI_PROFILE_ALIASES.get(key, key)
    if resolved not in PROFILES and resolved.upper() in PROFILES:
        resolved = resolved.upper()
    profile = PROFILES.get(resolved)
    if not profile:
        raise ValueError(f"Profilo sconosciuto: {profile_name!r}")
    for env_key, value in profile.items():
        os.environ[env_key] = str(value)
    return dict(profile)


def is_job_running() -> bool:
    """
    True se un job è in esecuzione, in coda (current_job) o ancora nella PriorityQueue.
    Non usare questa funzione nel worker loop (causerebbe deadlock con qsize).
    """
    state = get_orchestrator_state()
    job = state.current_job
    if job is not None and job.get("status") in ("running", "queued"):
        return True
    return state.job_queue.qsize() > 0


def _worker_busy(state) -> bool:
    """Il worker attende solo se un job è già in stato running."""
    job = state.current_job
    return job is not None and job.get("status") == "running"


def _ensure_worker() -> None:
    """Avvia il thread worker se non è vivo. Sicuro con RLock."""
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
        logger.info("Worker thread avviato")


def _persist_last_job_result(state) -> None:
    """Conserva l'ultimo risultato per /api/jobs/status (Task 3: last_job / _last_job)."""
    snap = state.get_current_job_snapshot()
    if snap:
        with state._lock:
            state._last_job = snap
    # Se current_job è già None (es. kill_all), non cancellare last_job impostato da STOP.


def _job_worker_loop() -> None:
    """
    Loop worker job. WARNING: non chiamare is_job_running() qui — deadlock con qsize().
    """
    while True:
        state = get_orchestrator_state()

        if state.stop_event.is_set():
            state.stop_event.wait(0.3)
            continue

        if _worker_busy(state):
            state.stop_event.wait(0.2)
            continue

        payload = state.job_queue.get(timeout=0.5)
        if not payload:
            continue

        slug = str(payload.get("slug") or "")
        workflow = str(payload.get("workflow") or "ingest")
        if not slug:
            logger.warning("Payload senza slug, skip")
            continue

        state.init_current_job(
            project=slug,
            workflow=workflow,
            status="running",
            files_total=0,
            files_completed=0,
            files_failed=0,
            current_file=None,
            error=None,
            outputs_written=[],
        )
        state.emit_log(f"[JOB] ▶ Avvio workflow={workflow} progetto={slug}")

        try:
            _run_job_worker(slug, workflow)
        except InterruptedError:
            state.emit_log(f"[JOB] Interrotto (kill switch) su {slug}", level="WARN")
            release_lm_http_resources()
            state.update_current_job(status="stopped")
        except httpx.TimeoutException as e:
            logger.exception("Job worker: timeout HTTP per %s", slug)
            release_lm_http_resources()
            state.emit_log(
                f"[JOB] Timeout LM Studio — possibile OOM: {e}",
                level="ERROR",
            )
            state.update_current_job(status="failed", error=str(e)[:300])
        except Exception as e:
            logger.exception("Job worker: errore non gestito per %s", slug)
            if "timeout" in str(e).lower() or "OOM" in str(e):
                release_lm_http_resources()
            state.emit_log(f"[JOB] Errore critico: {e}", level="ERROR")
            state.update_current_job(status="failed", error=str(e)[:300])

        _persist_last_job_result(state)
        state.clear_current_job()


def start_job(*, slug: str, workflow: str | None = None) -> dict[str, Any]:
    """Accoda un job. RLock evita deadlock con _ensure_worker."""
    with _worker_lock:
        if is_job_running():
            raise RuntimeError("Un job è già in esecuzione — aspetta o premi STOP")

        try:
            meta = load_project(slug)
        except FileNotFoundError:
            raise FileNotFoundError(f"Progetto non trovato: {slug}")

        wf = (workflow or meta.get("workflow") or "ingest").strip()
        if not wf:
            wf = "ingest"

        state = get_orchestrator_state()
        if state.stop_event.is_set():
            reset_orchestrator()
            state = get_orchestrator_state()

        prof = meta.get("hardware_profile", "eco")
        try:
            apply_hardware_profile(prof)
            get_cooldown_manager().reload()
            state.emit_log(f"[JOB] Profilo HW: {prof}")
        except ValueError as e:
            state.emit_log(
                f"[JOB] Profilo HW non trovato ({e}), uso default",
                level="WARN",
            )

        seq = state.enqueue_job({"slug": slug, "workflow": wf}, priority=5)
        state.emit_log(f"[JOB] In coda — workflow={wf} progetto={slug} seq={seq}")

        from engine.interaction_logger import log_app_system

        log_app_system(f"Job accodato: {slug} workflow={wf}")

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
    abort_if_stop_requested()

    if workflow not in _SKIP_LM_WORKFLOWS:
        try:
            router = get_model_router()
            task = "reasoning" if workflow == "gap_analysis" else "summary"
            model = router.apply_model_for_task(task)
            state.emit_log(f"[JOB] Modello ({task}): {model}")
        except Exception as e:
            state.emit_log(
                f"[JOB] Model router: {e} — continuo senza routing",
                level="WARN",
            )

        require_allm = workflow == "gap_analysis"
        abort_if_stop_requested()
        try:
            init_gap_analysis_session(
                require_allm=require_allm,
                force_refresh=False,
            )
        except httpx.TimeoutException as e:
            release_lm_http_resources()
            err = f"Preflight timeout LM Studio: {e}"
            state.emit_log(f"[JOB] {err}", level="ERROR")
            state.update_current_job(status="failed", error=err[:300])
            return
        except Exception as e:
            err = f"Preflight fallito: {e}"
            state.emit_log(f"[JOB] {err}", level="ERROR")
            state.update_current_job(status="failed", error=err)
            return

    try:
        if workflow in ("ingest", "sliding_window"):
            _run_ingest_job(slug)
        elif workflow == "gap_analysis":
            _run_gap_job(slug)
        elif workflow == "test_workflow":
            _run_test_job(slug)
        else:
            _run_plugin_workflow(slug, workflow)
    except InterruptedError:
        release_lm_http_resources()
        state.emit_log(f"[JOB] Interrotto da kill switch su {slug}", level="WARN")
        state.update_current_job(status="stopped")
        return
    except httpx.TimeoutException as e:
        release_lm_http_resources()
        logger.exception("Workflow %s timeout per %s", workflow, slug)
        state.emit_log(f"[JOB] ✗ Timeout LM Studio: {e}", level="ERROR")
        state.update_current_job(status="failed", error=str(e)[:300])
        return
    except Exception as e:
        logger.exception("Workflow %s fallito per %s", workflow, slug)
        if isinstance(e, RuntimeError) and "timeout" in str(e).lower():
            release_lm_http_resources()
        state.emit_log(f"[JOB] ✗ Workflow fallito: {e}", level="ERROR")
        state.update_current_job(status="failed", error=str(e)[:300])
        return

    if not state.stop_event.is_set():
        state.update_current_job(status="completed")
        state.emit_log(f"[JOB] Completato: {slug}")


def _run_test_job(slug: str) -> None:
    """Workflow di test: 3 cicli da 1s, nessuna chiamata LLM."""
    state = get_orchestrator_state()
    state.update_current_job(files_total=3)

    for i in range(1, 4):
        if state.stop_event.is_set():
            raise InterruptedError("test_workflow interrotto")
        state.emit_log(f"[TEST] Step {i}/3")
        state.update_current_job(files_completed=i)
        if state.stop_event.wait(1.0):
            raise InterruptedError("test_workflow interrotto")

    state.emit_log("[TEST] Test workflow completato senza LLM")


def _run_plugin_workflow(slug: str, workflow: str) -> None:
    """Esegue workflow dal registro WorkflowRunner."""
    from engine.workflow_runner import WorkflowRunner

    state = get_orchestrator_state()
    runner = WorkflowRunner()
    wf_instance = runner.get_workflow(workflow)
    if wf_instance is None:
        raise ValueError(f"Workflow sconosciuto: {workflow!r}")

    ctx: dict[str, Any] = {
        "slug": slug,
        "stop_event": state.stop_event,
        "orchestrator": state,
        "log_fn": lambda m: state.emit_log(m),
    }
    ingest_root = ingest_dir(slug)
    files = [
        p
        for p in sorted(ingest_root.iterdir())
        if p.is_file() and not p.name.startswith(".")
    ]
    if not files:
        state.emit_log(
            "[JOB] Nessun file in 01_INGEST per plugin workflow",
            level="WARN",
        )
        return

    state.update_current_job(files_total=len(files))

    for src in files:
        if state.stop_event.is_set():
            raise InterruptedError(f"Plugin {workflow} interrotto")
        state.update_current_job(current_file=src.name)
        wf_instance.process_file(src, ctx)
        state.bump_files_completed()


def _run_gap_job(slug: str) -> None:
    from workflows.gap_analysis import run_project_gap_analysis

    state = get_orchestrator_state()
    sources = list_ingest_sources(slug, skip_duplicates=True, skip_completed=False)
    state.update_current_job(files_total=len(sources) or 1)

    n = run_project_gap_analysis(
        slug,
        stop_event=state.stop_event,
        log_fn=lambda m: state.emit_log(m),
    )
    state.update_current_job(files_completed=n)


def _run_ingest_job(slug: str) -> None:
    state = get_orchestrator_state()
    cooldown = get_cooldown_manager()
    sources = list_ingest_sources(slug)

    if not sources:
        state.emit_log(
            "[JOB] Nessun file nuovo in 01_INGEST — copia documenti e riavvia",
            level="WARN",
        )
        state.update_current_job(files_total=0)
        return

    state.update_current_job(files_total=len(sources))

    max_tokens = resolve_chunk_max_tokens()
    ingest_root = ingest_dir(slug)

    for src in sources:
        if state.stop_event.is_set():
            raise InterruptedError(f"Ingest interrotto su {src.name}")

        state.update_current_job(current_file=src.name)

        file_dir = ingest_root / src.stem
        state.emit_log(f"[INGEST] Elaboro: {src.name}")

        try:
            sliding_window_analyze(
                src.resolve(),
                file_dir.resolve(),
                llm_complete,
                state.stop_event,
                lambda m: state.emit_log(m),
                max_tokens,
            )
            mark_ingest_file_done(slug, src)
            state.bump_files_completed()
            cooldown.after_file(state.stop_event)
        except InterruptedError:
            raise
        except Exception as e:
            state.bump_files_failed()
            state.emit_log(f"[INGEST] Fallito {src.name}: {e}", level="ERROR")

    state.update_current_job(current_file=None)

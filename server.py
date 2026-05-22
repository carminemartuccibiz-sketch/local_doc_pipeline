"""
Backend Flask + SSE log stream (FASE 6).
Avvio browser: python server.py  →  http://localhost:7842
"""
from __future__ import annotations

import json
import logging
from queue import Empty

from flask import Flask, Response, jsonify, render_template, request
from flask_cors import CORS

from config import UI_PORT, PROFILES, PROFILE_ALIASES
from engine.model_router import get_model_router
from engine.workflow_runner import WorkflowRunner
from core.file_io import atomic_write_json
from engine.cooldown_manager import get_cooldown_manager
from engine.interaction_logger import ensure_logs_dir, log_app_system, setup_app_system_logger
from engine.job_runner import apply_hardware_profile, is_job_running, start_job
from engine.orchestrator import get_orchestrator_state, reset_orchestrator

ensure_logs_dir()
setup_app_system_logger()
log_app_system("server.py caricato")
from engine.project_store import (
    PROJECTS_ROOT,
    create_project,
    get_project_detail,
    list_projects,
    load_project,
    set_file_role,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(
    __name__,
    template_folder="ui/templates",
    static_folder="ui/static",
    static_url_path="/static",
)
CORS(app)

UI_PROFILES = [
    {"id": "eco", "label": "Eco (basso carico)"},
    {"id": "fast", "label": "Fast (2080 Ti)"},
    {"id": "deep", "label": "Deep (analisi estesa)"},
]


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/projects")
def api_projects_list():
    return jsonify(list_projects())


@app.post("/api/projects")
def api_projects_create():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name richiesto"}), 400
    workflow = (body.get("workflow") or "ingest").strip()
    try:
        meta = create_project(name=name, workflow=workflow)
        get_orchestrator_state().emit_log(f"Progetto creato: {meta['slug']}")
        return jsonify(meta), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/api/projects/<slug>")
def api_projects_detail(slug: str):
    try:
        return jsonify(get_project_detail(slug))
    except FileNotFoundError:
        return jsonify({"error": "progetto non trovato"}), 404


@app.post("/api/projects/<slug>/roles")
def api_projects_roles(slug: str):
    body = request.get_json(silent=True) or {}
    file_path = body.get("file_path") or body.get("path")
    role = body.get("role")
    if not file_path or not role:
        return jsonify({"error": "file_path e role richiesti"}), 400
    try:
        roles = set_file_role(slug, str(file_path), str(role))
        return jsonify({"file_roles": roles})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except FileNotFoundError:
        return jsonify({"error": "progetto non trovato"}), 404


@app.get("/api/workflows")
def api_workflows_list():
    """
    FIX: restituisce {"workflows": [...]} (non array diretto).
    app.js destruttura const { workflows } = await api("/api/workflows").
    """
    ingest_workflows = [
        {"id": "ingest", "label": "Ingest (Sliding Window)"},
        {"id": "test_workflow", "label": "Test (no LLM, 3 step)"},
    ]
    ingest_ids = {w["id"] for w in ingest_workflows}
    plugin_workflows = [
        {"id": k, "label": k.replace("_", " ").title()}
        for k in WorkflowRunner.registered()
        if k not in ingest_ids
    ]
    return jsonify({"workflows": ingest_workflows + plugin_workflows})


@app.get("/api/models")
def api_models():
    try:
        router = get_model_router()
        models = router.refresh()
        active = router.get_model_for_task("reasoning")
        return jsonify({"models": models, "active": active})
    except Exception as e:
        return jsonify({"models": [], "active": None, "error": str(e)})


@app.get("/api/profiles")
def api_profiles():
    items = []
    for prof in UI_PROFILES:
        pid = prof["id"]
        label = prof["label"]
        resolved = PROFILE_ALIASES.get(pid, pid)
        if pid in ("fast", "deep"):
            resolved = "I9_2080TI_32GB"
        items.append(
            {
                "id": pid,
                "label": label,
                "resolved": resolved,
                "keys": list(PROFILES.get(resolved, {}).keys())[:6],
            }
        )
    state = get_orchestrator_state()
    active = None
    if state.current_job:
        try:
            active = load_project(state.current_job["project"]).get(
                "hardware_profile"
            )
        except FileNotFoundError:
            pass
    return jsonify({"profiles": items, "active": active})


@app.post("/api/profiles/select")
def api_profiles_select():
    body = request.get_json(silent=True) or {}
    profile_name = body.get("profile_name") or body.get("profile")
    slug = body.get("project")
    if not profile_name:
        return jsonify({"error": "profile_name richiesto"}), 400
    try:
        applied = apply_hardware_profile(str(profile_name))
        get_cooldown_manager().reload()
        if slug:
            proj_dir = PROJECTS_ROOT / slug
            cfg_path = proj_dir / "project.json"
            if cfg_path.is_file():
                meta = json.loads(cfg_path.read_text(encoding="utf-8"))
                meta["hardware_profile"] = profile_name
                atomic_write_json(cfg_path, meta)
        get_orchestrator_state().emit_log(f"Profilo HW: {profile_name}")
        return jsonify(
            {
                "profile_name": profile_name,
                "applied": applied.get("PIPELINE_HARDWARE_PROFILE"),
            }
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/jobs/start")
def api_jobs_start():
    body = request.get_json(silent=True) or {}
    slug = (body.get("project") or body.get("slug") or "").strip()
    workflow = (body.get("workflow") or "").strip() or None

    if not slug:
        return jsonify({"error": "Campo 'project' obbligatorio"}), 400

    try:
        job = start_job(slug=slug, workflow=workflow)
        return jsonify(job), 202
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        logger.exception("Errore avvio job")
        return jsonify({"error": f"Errore interno: {e}"}), 500


@app.post("/api/stop")
def api_stop():
    """Pulsante STOP nella UI (Task 3 blueprint)."""
    get_orchestrator_state().kill_all()
    return jsonify({"status": "stopped", "message": "Pipeline fermata."})


@app.post("/api/reset")
def api_reset():
    """Reset orchestrator dopo stop — nuovo job."""
    reset_orchestrator()
    log_app_system("Orchestrator reset (API)")
    return jsonify({"status": "ready"})


@app.post("/api/jobs/stop")
def api_jobs_stop():
    """Alias compatibilità FASE 6."""
    return api_stop()


@app.post("/api/jobs/reset")
def api_jobs_reset():
    """Alias compatibilità FASE 6."""
    return api_reset()


@app.get("/api/jobs/status")
def api_jobs_status():
    """
    FIX Task 3: espone anche l'ultimo job (failed/completed) dopo che current_job
    è stato azzerato, così la UI può mostrare FAILED e abilitare RESET.
    """
    state = get_orchestrator_state()
    job = state.current_job
    last = state._last_job
    displayed_job = job if job is not None else last

    return jsonify(
        {
            "running": is_job_running(),
            "stop_requested": state.stop_event.is_set(),
            "job": displayed_job,
        }
    )


@app.get("/api/logs/stream")
def logs_stream():
    log_queue = get_orchestrator_state().log_stream

    def generate():
        while True:
            try:
                msg = log_queue.get(timeout=0.3)
                yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
            except Empty:
                yield 'data: {"heartbeat": true}\n\n'

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def main() -> None:
    PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
    logger.info("Local AI Orchestrator — http://127.0.0.1:%s", UI_PORT)
    app.run(host="127.0.0.1", port=UI_PORT, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()

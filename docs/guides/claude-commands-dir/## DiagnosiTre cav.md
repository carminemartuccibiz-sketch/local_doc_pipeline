## Diagnosi

Tre cavi recisi tra UI e backend:

1. **Deadlock nel worker loop** (`job_runner.py`): `_job_worker_loop()` chiama `is_job_running()` che controlla `job_queue.qsize() > 0`. Quando il job è appena accodato, la funzione restituisce `True`, il worker entra nello sleep e non dequeue mai. Deadlock garantito ad ogni click su START.
2. **Deadlock re-entrant lock** (`job_runner.py`): `start_job()` acquisisce `_worker_lock` (non rientrante) e poi chiama `_ensure_worker()` che tenta di acquisire lo stesso lock. La chiamata si blocca indefinitamente sul lock.
3. **Mismatch formato `/api/workflows`** (`server.py` ↔ `app.js`): il server risponde `[{id, label}, ...]` (array diretto), ma `app.js` destruttura `const { workflows } = await api("/api/workflows")` aspettandosi `{ workflows: [...] }`. Il risultato è `undefined` e il `<select>` rimane vuoto — l'utente non può selezionare nulla, il job viene avviato con workflow `""`.


---

## Fix 1 — `engine/job_runner.py` (completo, sezioni chiave)

```python
"""
Esecuzione job in background — coda orchestrator + ingest / gap (FASE 6).
FIXES:
  - RLock per rientranza start_job → _ensure_worker
  - Worker loop: controlla current_job, non is_job_running()
  - is_job_running(): solo current_job, non qsize
  - Error propagation completa verso UI via emit_log
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

_UI_PROFILE_ALIASES = {
    **PROFILE_ALIASES,
    "fast": "I9_2080TI_32GB",
    "deep": "I9_2080TI_32GB",
}

logger = logging.getLogger(__name__)

# FIX #2: RLock invece di Lock — rientrante per start_job → _ensure_worker
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
    FIX #1: controlla solo current_job, NON qsize.
    qsize > 0 significa "c'è lavoro da fare", non "sta girando".
    """
    state = get_orchestrator_state()
    job = state.current_job
    return job is not None and job.get("status") in ("running", "queued")


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


def _job_worker_loop() -> None:
    """
    FIX #1: il loop controlla current_job (non is_job_running) per decidere
    se dequeue. is_job_running() col vecchio codice usava qsize → deadlock.
    """
    while True:
        state = get_orchestrator_state()

        if state.stop_event.is_set():
            time.sleep(0.3)
            continue

        # Se c'è già un job attivo (processing), aspetta che finisca
        if state.current_job is not None:
            time.sleep(0.2)
            continue

        # Aspetta payload — timeout 0.5s, poi ri-controlla
        payload = state.job_queue.get(timeout=0.5)
        if not payload:
            continue

        slug = str(payload.get("slug") or "")
        workflow = str(payload.get("workflow") or "ingest")
        if not slug:
            logger.warning("Payload senza slug, skip")
            continue

        # Marca job come running PRIMA di chiamare il worker
        state.current_job = {
            "project": slug,
            "workflow": workflow,
            "status": "running",
            "files_total": 0,
            "files_completed": 0,
            "files_failed": 0,
            "current_file": None,
            "error": None,
        }
        state.emit_log(f"[JOB] ▶ Avvio workflow={workflow} progetto={slug}")

        try:
            _run_job_worker(slug, workflow)
        except Exception as e:
            # Catch-all: non deve mai far crashare il thread worker
            logger.exception("Job worker: errore non gestito per %s", slug)
            state.emit_log(f"[JOB] ✗ Errore critico: {e}", level="ERROR")
            if state.current_job:
                state.current_job["status"] = "failed"
                state.current_job["error"] = str(e)[:300]

        # Salva ultimo stato prima di azzerare current_job
        _persist_last_job_result(state)
        state.current_job = None


def _persist_last_job_result(state) -> None:
    """Conserva l'ultimo risultato per la UI (accedibile via /api/jobs/status)."""
    if state.current_job:
        state._last_job = dict(state.current_job)
    else:
        state._last_job = None


def start_job(*, slug: str, workflow: str | None = None) -> dict[str, Any]:
    """
    Accoda un job. FIX: usa RLock per evitare deadlock con _ensure_worker.
    """
    with _worker_lock:
        if is_job_running():
            raise RuntimeError("Un job è già in esecuzione — aspetta o premi STOP")

        try:
            meta = load_project(slug)
        except FileNotFoundError:
            raise FileNotFoundError(f"Progetto non trovato: {slug}")

        wf = (workflow or meta.get("workflow") or "ingest").strip()
        state = get_orchestrator_state()

        # Reset stop_event se era stato usato (permette restart dopo STOP)
        if state.stop_event.is_set():
            reset_orchestrator()
            state = get_orchestrator_state()

        # Applica profilo HW
        prof = meta.get("hardware_profile", "eco")
        try:
            apply_hardware_profile(prof)
            get_cooldown_manager().reload()
            state.emit_log(f"[JOB] Profilo HW: {prof}")
        except ValueError as e:
            state.emit_log(f"[JOB] Profilo HW non trovato ({e}), uso default", level="WARN")

        seq = state.enqueue_job({"slug": slug, "workflow": wf}, priority=5)
        state.emit_log(f"[JOB] In coda — workflow={wf} progetto={slug} seq={seq}")
        log_app_system(f"Job accodato: {slug} workflow={wf}")

        _ensure_worker()  # sicuro con RLock

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
    """
    FIX Task 3: try/except completo con emit_log a ogni livello di errore.
    """
    state = get_orchestrator_state()

    # ── Model routing (skip per workflow di test/stub) ─────────────────
    _SKIP_LM_WORKFLOWS = {"test_workflow"}
    if workflow not in _SKIP_LM_WORKFLOWS:
        try:
            router = get_model_router()
            task = "reasoning" if workflow == "gap_analysis" else "summary"
            model = router.apply_model_for_task(task)
            state.emit_log(f"[JOB] Modello ({task}): {model}")
        except Exception as e:
            state.emit_log(f"[JOB] Model router: {e} — continuo senza routing", level="WARN")

    # ── Preflight LM / ALLM ────────────────────────────────────────────
    if workflow not in _SKIP_LM_WORKFLOWS:
        require_allm = workflow == "gap_analysis"
        try:
            init_gap_analysis_session(require_allm=require_allm, force_refresh=False)
        except Exception as e:
            err = f"Preflight fallito: {e}"
            state.emit_log(f"[JOB] ✗ {err}", level="ERROR")
            if state.current_job:
                state.current_job["status"] = "failed"
                state.current_job["error"] = err
            return

    # ── Dispatch workflow ──────────────────────────────────────────────
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
        state.emit_log(f"[JOB] ⏹ Interrotto da kill switch su {slug}", level="WARN")
        if state.current_job:
            state.current_job["status"] = "stopped"
        return
    except Exception as e:
        logger.exception("Workflow %s fallito per %s", workflow, slug)
        state.emit_log(f"[JOB] ✗ Workflow fallito: {e}", level="ERROR")
        if state.current_job:
            state.current_job["status"] = "failed"
            state.current_job["error"] = str(e)[:300]
        return

    # ── Completato ────────────────────────────────────────────────────
    if not state.stop_event.is_set():
        if state.current_job:
            state.current_job["status"] = "completed"
        state.emit_log(f"[JOB] ✓ Completato: {slug}")


def _run_test_job(slug: str) -> None:
    """Workflow di test: 3 cicli da 1s, nessuna chiamata LLM."""
    state = get_orchestrator_state()
    if state.current_job:
        state.current_job["files_total"] = 3

    for i in range(1, 4):
        if state.stop_event.is_set():
            raise InterruptedError("test_workflow interrotto")
        state.emit_log(f"[TEST] Step {i}/3 — simulazione lavoro...")
        if state.current_job:
            state.current_job["files_completed"] = i
        time.sleep(1.0)

    state.emit_log("[TEST] Test workflow completato senza LLM ✓")


def _run_plugin_workflow(slug: str, workflow: str) -> None:
    """Esegue workflow dal registro WorkflowRunner."""
    from engine.workflow_runner import WorkflowRunner

    state = get_orchestrator_state()
    runner = WorkflowRunner()
    wf_instance = runner.get_workflow(workflow)
    if wf_instance is None:
        raise ValueError(f"Workflow sconosciuto: {workflow!r}")

    ctx: dict = {
        "slug": slug,
        "stop_event": state.stop_event,
        "log_fn": lambda m: state.emit_log(m),
    }
    ingest_root = ingest_dir(slug)
    for src in sorted(ingest_root.iterdir()):
        if not src.is_file() or src.name.startswith("."):
            continue
        if state.stop_event.is_set():
            raise InterruptedError(f"Plugin {workflow} interrotto")
        wf_instance.process_file(src, ctx)
        if state.current_job:
            state.current_job["files_completed"] = (
                state.current_job.get("files_completed", 0) + 1
            )


def _run_gap_job(slug: str) -> None:
    from workflows.gap_analysis import run_project_gap_analysis

    state = get_orchestrator_state()
    sources = list_ingest_sources(slug, skip_duplicates=True, skip_completed=False)
    if state.current_job:
        state.current_job["files_total"] = len(sources) or 1

    n = run_project_gap_analysis(
        slug,
        stop_event=state.stop_event,
        log_fn=lambda m: state.emit_log(m),
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
    ingest_root = ingest_dir(slug)

    for src in sources:
        if state.stop_event.is_set():
            raise InterruptedError(f"Ingest interrotto su {src.name}")

        if state.current_job:
            state.current_job["current_file"] = src.name

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
            if state.current_job:
                state.current_job["files_completed"] = (
                    state.current_job.get("files_completed", 0) + 1
                )
            cooldown.after_file(state.stop_event)
        except InterruptedError:
            raise
        except Exception as e:
            if state.current_job:
                state.current_job["files_failed"] = (
                    state.current_job.get("files_failed", 0) + 1
                )
            state.emit_log(f"[INGEST] Fallito {src.name}: {e}", level="ERROR")

    if state.current_job:
        state.current_job["current_file"] = None
```

---

## Fix 2 — `server.py` (solo funzioni modificate)

```python
# ── Aggiornamento in server.py ──────────────────────────────────────────────

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
    plugin_workflows = [
        {"id": k, "label": k.replace("_", " ").title()}
        for k in WorkflowRunner.registered()
    ]
    return jsonify({"workflows": ingest_workflows + plugin_workflows})


@app.get("/api/jobs/status")
def api_jobs_status():
    """
    FIX Task 3: espone anche l'ultimo job (failed/completed) dopo che current_job
    è stato azzerato, così la UI può mostrare "FAILED" e abilitare RESET.
    """
    state = get_orchestrator_state()
    job = state.current_job

    # Se non c'è job attivo, usa l'ultimo risultato conservato
    last = getattr(state, "_last_job", None)
    displayed_job = job if job is not None else last

    return jsonify(
        {
            "running": is_job_running(),
            "stop_requested": state.stop_event.is_set(),
            "job": displayed_job,
        }
    )


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
```

---

## Fix 3 — `app.js` (blocchi modificati)

```javascript
// ── Sostituisci loadWorkflows() ────────────────────────────────────────────

async function loadWorkflows() {
  try {
    // FIX: il server ora restituisce { workflows: [...] }
    const data = await api("/api/workflows");
    const list = Array.isArray(data) ? data : (data.workflows || []);
    const sel = $("workflow-select");
    sel.innerHTML = "";
    if (!list.length) {
      sel.innerHTML = '<option value="ingest">Ingest (default)</option>';
      return;
    }
    list.forEach((w) => {
      const opt = document.createElement("option");
      opt.value = w.id;
      opt.textContent = w.label || w.id;
      sel.appendChild(opt);
    });
  } catch (e) {
    console.warn("[loadWorkflows] fallback:", e);
    $("workflow-select").innerHTML = '<option value="ingest">Ingest</option>';
  }
}

// ── Sostituisci pollJobStatus() — gestisce FAILED e sblocca UI ─────────────

async function pollJobStatus() {
  try {
    const st = await api("/api/jobs/status");
    const job = st.job;

    updateStats(job);

    const failed = job && job.status === "failed";
    const stopped = st.stop_requested && !st.running;
    const completed = job && job.status === "completed" && !st.running;

    // FIX Task 3: sblocca UI su fail/stop/complete
    if (st.running) {
      setJobUi("running");
    } else if (failed) {
      setJobUi("failed");
      appendLog({ msg: `[JOB] ✗ Job fallito: ${job.error || "errore sconosciuto"}`, level: "ERROR" });
    } else if (stopped) {
      setJobUi("stopped");
    } else {
      setJobUi("idle");
    }
  } catch (e) {
    console.warn("[pollJobStatus]", e);
  }
}

// ── Sostituisci setJobUi() — accetta stringa di stato ─────────────────────

function setJobUi(status) {
  // status: "running" | "stopped" | "failed" | "idle"
  jobRunning = status === "running";

  $("btn-start").disabled = status === "running";
  $("btn-stop").disabled = status !== "running";
  $("btn-reset").disabled = status === "running" || status === "idle";

  const badge = $("job-badge");
  badge.className = "pill";

  switch (status) {
    case "running":
      badge.textContent = "Running";
      badge.classList.add("running");
      break;
    case "stopped":
      badge.textContent = "Stopped";
      badge.classList.add("stopped");
      break;
    case "failed":
      badge.textContent = "Failed";
      badge.classList.add("stopped"); // rosso
      break;
    default:
      badge.textContent = "Idle";
  }

  // Auto-refresh durante esecuzione
  if (status === "running" && !refreshTimer) {
    refreshTimer = setInterval(() => {
      if (currentSlug) loadProject(currentSlug);
      pollJobStatus();
    }, 3000);
  }
  if (status !== "running" && refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
}

// ── Sostituisci click btn-start ────────────────────────────────────────────

$("btn-start").addEventListener("click", async () => {
  if (!currentSlug) {
    alert("Seleziona un progetto prima di avviare");
    return;
  }
  const workflow = $("workflow-select").value;
  if (!workflow) {
    alert("Seleziona un workflow");
    return;
  }

  // Applica profilo
  try {
    await api("/api/profiles/select", {
      method: "POST",
      body: JSON.stringify({ profile_name: $("profile-select").value, project: currentSlug }),
    });
  } catch (e) {
    appendLog({ msg: `[UI] Profilo: ${e.message}`, level: "WARN" });
  }

  // Avvia job
  try {
    $("btn-start").disabled = true; // feedback immediato
    const job = await api("/api/jobs/start", {
      method: "POST",
      body: JSON.stringify({ project: currentSlug, workflow }),
    });
    updateStats(job);
    setJobUi("running");
    appendLog({ msg: `[UI] ▶ Job avviato — workflow=${workflow}`, level: "INFO" });
  } catch (e) {
    $("btn-start").disabled = false;
    appendLog({ msg: `[UI] ✗ Errore avvio: ${e.message}`, level: "ERROR" });
    alert(`Impossibile avviare: ${e.message}`);
  }
});

// ── Sostituisci click btn-stop ─────────────────────────────────────────────

$("btn-stop").addEventListener("click", async () => {
  $("btn-stop").disabled = true; // evita doppio click
  try {
    const data = await api("/api/jobs/stop", { method: "POST" });
    appendLog({ msg: `[STOP] ${data.message}`, level: "WARN" });
    setJobUi("stopped");
  } catch (e) {
    appendLog({ msg: `[STOP] Errore: ${e.message}`, level: "ERROR" });
  }
});

// ── Sostituisci click btn-reset ────────────────────────────────────────────

$("btn-reset").addEventListener("click", async () => {
  try {
    await api("/api/jobs/reset", { method: "POST" });
    appendLog({ msg: "[UI] Orchestrator pronto per un nuovo job", level: "INFO" });
    setJobUi("idle");
    updateStats(null);
  } catch (e) {
    appendLog({ msg: `[RESET] Errore: ${e.message}`, level: "ERROR" });
  }
});
```

---

## Fix 4 — `engine/workflow_runner.py` (registro con display names)

```python
"""
Plugin loader workflow — registro con nome display e metadata.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from workflows.base_workflow import BaseWorkflow
from workflows.blog_post import BlogPostWorkflow
from workflows.code_analysis import CodeAnalysisWorkflow
from workflows.gap_analysis import GapAnalysisWorkflow

logger = logging.getLogger(__name__)

# Registro: slug → (classe, label UI, descrizione)
_REGISTRY: dict[str, tuple[type[BaseWorkflow], str, str]] = {
    "gap_analysis": (
        GapAnalysisWorkflow,
        "Gap Analysis (LAST DOCS)",
        "Confronto grezzo vs SOT — richiede LM Studio + AnythingLLM",
    ),
    "blog_post": (
        BlogPostWorkflow,
        "Blog Post (stub)",
        "Generazione post da documenti — da implementare",
    ),
    "code_analysis": (
        CodeAnalysisWorkflow,
        "Code Analysis (stub)",
        "Analisi codice sorgente — da implementare",
    ),
}


class WorkflowRunner:
    def get_workflow(self, name: str) -> BaseWorkflow | None:
        entry = _REGISTRY.get(name.strip().lower())
        if entry is None:
            return None
        cls, _, _ = entry
        return cls()

    def run_file(self, workflow_name: str, file_path: Path, ctx: dict[str, Any]) -> Any:
        wf = self.get_workflow(workflow_name)
        if wf is None:
            raise ValueError(f"Workflow sconosciuto: {workflow_name!r}")
        return wf.process_file(file_path, ctx)

    @staticmethod
    def registered() -> list[str]:
        """Slug dei workflow registrati (per server.py)."""
        return sorted(_REGISTRY.keys())

    @staticmethod
    def registered_with_meta() -> list[dict[str, str]]:
        """Lista completa con id/label/description per la UI."""
        return [
            {"id": slug, "label": label, "description": desc}
            for slug, (_, label, desc) in sorted(_REGISTRY.items())
        ]
```

---

## Task 3 — Aggiornamento `engine/orchestrator.py` (aggiungi `_last_job`)

```python
# Aggiungi in OrchestratorState, subito dopo la definizione della dataclass:

@dataclass
class OrchestratorState:
    stop_event: threading.Event = field(default_factory=threading.Event)
    active_requests: list[httpx.Client] = field(default_factory=list)
    job_queue: JobQueue = field(default_factory=JobQueue)
    current_job: dict[str, Any] | None = None
    _last_job: dict[str, Any] | None = None   # ← AGGIUNTO: persiste dopo completamento
    log_stream: Queue = field(default_factory=Queue)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ... (tutto il resto invariato)

    def kill_all(self) -> None:
        # FIX: salva ultimo stato prima di azzerare
        if self.current_job:
            self._last_job = {**self.current_job, "status": "stopped"}
        # ... resto invariato
        self.current_job = None
        self.emit_log("STOP attivato", level="WARN")
```

---

## Workflow di Test — `workflows/test_workflow.py`

```python
"""
Test workflow — 3 step da 1 secondo, nessuna chiamata LLM.
Uso: seleziona "Test (no LLM)" dalla UI e premi START.
Permette di verificare START → SSE → STOP senza LM Studio attivo.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from workflows.base_workflow import BaseWorkflow


class TestWorkflow(BaseWorkflow):
    """
    Workflow diagnostico: emette log ogni secondo per 3 step.
    Non richiede LM Studio, AnythingLLM o file in 01_INGEST.
    """

    STEPS = 3
    STEP_DURATION_S = 1.0

    def process_file(self, file_path: Path, ctx: dict[str, Any]) -> Any:
        log_fn = ctx.get("log_fn") or (lambda m: None)
        stop_event = ctx.get("stop_event")

        log_fn(f"[TEST] Elaboro: {file_path.name}")
        for i in range(1, self.STEPS + 1):
            if stop_event and stop_event.is_set():
                log_fn("[TEST] Interrotto da kill switch")
                raise InterruptedError("test_workflow interrotto")
            log_fn(f"[TEST] Step {i}/{self.STEPS} — {file_path.name}")
            time.sleep(self.STEP_DURATION_S)

        log_fn(f"[TEST] ✓ Completato: {file_path.name}")
        return {"status": "ok", "file": str(file_path)}
```

Poi registralo in `workflow_runner.py`:

```python
from workflows.test_workflow import TestWorkflow

_REGISTRY: dict[str, tuple[type[BaseWorkflow], str, str]] = {
    # ... esistenti ...
    "test_workflow": (
        TestWorkflow,
        "Test (no LLM, 3 step)",
        "Workflow diagnostico — verifica START/STOP/SSE senza LM Studio",
    ),
}
```

---

## Checklist verifica post-fix

```
[ ] 1. python -c "from engine.job_runner import _worker_lock; print(type(_worker_lock))"
       → atteso: <class '_thread.RLock'>

[ ] 2. python -c "from engine.job_runner import is_job_running; print(is_job_running())"
       → atteso: False  (nessun current_job)

[ ] 3. python server.py → apri http://localhost:7842
       → workflow-select deve mostrare almeno "Test (no LLM, 3 step)"

[ ] 4. Crea progetto "test", seleziona workflow "test_workflow", START
       → nel log SSE compaiono: "[JOB] ▶ Avvio", "[TEST] Step 1/3", "[TEST] Step 2/3", "[TEST] Step 3/3"
       → badge passa da "Idle" → "Running" → "Idle"

[ ] 5. Avvia di nuovo, premi STOP a metà
       → "[STOP] Pipeline fermata." nel log
       → badge → "Stopped", RESET abilitato

[ ] 6. Premi RESET → badge → "Idle", START abilitato di nuovo

[ ] 7. Simula fail: modifica temporaneamente test_workflow per raise Exception("test error")
       → badge → "Failed" (rosso), log "[JOB] ✗ Workflow fallito", RESET abilitato
```

 

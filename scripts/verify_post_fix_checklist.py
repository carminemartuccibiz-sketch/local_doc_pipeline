"""
Verifica automatica checklist DiagnosiTre cav (post-fix).
Esegui: py -3.10 scripts/verify_post_fix_checklist.py
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
BASE = "http://127.0.0.1:7842"


def _get(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=10) as r:
        return json.loads(r.read().decode())


def _post(path: str, body: dict | None = None) -> dict:
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _drain_logs(max_items: int = 80) -> list[str]:
    msgs: list[str] = []
    state = __import__("engine.orchestrator", fromlist=["get_orchestrator_state"]).get_orchestrator_state()
    for _ in range(max_items):
        try:
            entry = state.log_stream.get_nowait()
            msgs.append(str(entry.get("msg", "")))
        except Exception:
            break
    return msgs


def main() -> int:
    ok = True
    results: list[str] = []

    # --- 1–2 (import diretto) ---
    from engine.job_runner import _worker_lock, is_job_running, start_job
    from engine.orchestrator import get_orchestrator_state, reset_orchestrator

    t1 = type(_worker_lock).__name__ == "RLock"
    results.append(f"[{'x' if t1 else ' '}] 1. _worker_lock -> {type(_worker_lock)}")
    ok &= t1

    reset_orchestrator()
    t2 = is_job_running() is False
    results.append(f"[{'x' if t2 else ' '}] 2. is_job_running() idle -> {is_job_running()}")
    ok &= t2

    # --- 3–7 (server Flask in thread) ---
    from server import app

    def run_server():
        app.run(host="127.0.0.1", port=7842, threaded=True, use_reloader=False)

    th = threading.Thread(target=run_server, daemon=True)
    th.start()
    for _ in range(40):
        try:
            _get("/api/workflows")
            break
        except (urllib.error.URLError, ConnectionResetError, OSError):
            time.sleep(0.25)
    else:
        results.append("[ ] 3–7. Server non raggiungibile su :7842")
        for line in results:
            print(line)
        return 1

    wf = _get("/api/workflows")
    workflows = wf.get("workflows") or []
    labels = [w.get("label", "") for w in workflows]
    t3 = any("Test (no LLM" in lb for lb in labels)
    results.append(f"[{'x' if t3 else ' '}] 3. workflow-select contiene Test -> {labels}")
    ok &= t3

    reset_orchestrator()
    # progetto test
    try:
        projects = _get("/api/projects")
        if not any(p.get("slug") == "test" for p in projects):
            _post("/api/projects", {"name": "test", "workflow": "test_workflow"})
    except Exception:
        _post("/api/projects", {"name": "test", "workflow": "test_workflow"})

    reset_orchestrator()
    _post("/api/jobs/start", {"project": "test", "workflow": "test_workflow"})
    time.sleep(4.2)
    msgs = _drain_logs()
    # anche log lasciati in coda durante sleep
    state = get_orchestrator_state()
    while True:
        try:
            entry = state.log_stream.get_nowait()
            msgs.append(str(entry.get("msg", "")))
        except Exception:
            break

    t4_logs = (
        any("[JOB] ▶ Avvio" in m for m in msgs)
        and any("[TEST] Step 1/3" in m for m in msgs)
        and any("[TEST] Step 2/3" in m for m in msgs)
        and any("[TEST] Step 3/3" in m for m in msgs)
    )
    st = _get("/api/jobs/status")
    t4 = t4_logs and not st.get("running")
    results.append(f"[{'x' if t4 else ' '}] 4. test_workflow completo (log + non running)")
    if not t4:
        results.append(f"      log campione: {[m for m in msgs if '[JOB]' in m or '[TEST]' in m][-8:]}")
    ok &= t4

    reset_orchestrator()
    time.sleep(1.0)
    _post("/api/jobs/start", {"project": "test", "workflow": "test_workflow"})
    time.sleep(1.5)
    stop_resp = _post("/api/jobs/stop")
    time.sleep(0.5)
    st5 = _get("/api/jobs/status")
    job5 = st5.get("job") or {}
    t5 = (
        stop_resp.get("message") == "Pipeline fermata."
        and job5.get("status") == "stopped"
        and not st5.get("running")
    )
    results.append(f"[{'x' if t5 else ' '}] 5. STOP a meta -> stopped, message OK")
    ok &= t5

    reset_orchestrator()
    get_orchestrator_state()._last_job = {"status": "stopped", "project": "test"}
    _post("/api/jobs/reset")
    st6 = _get("/api/jobs/status")
    t6 = not st6.get("running") and st6.get("job") is None
    results.append(f"[{'x' if t6 else ' '}] 6. RESET -> idle (job None, non running)")
    ok &= t6

    # 7 — simula fail nel worker test
    reset_orchestrator()
    time.sleep(1.5)
    import engine.job_runner as jr

    orig = jr._run_test_job

    def _fail_test(_slug: str) -> None:
        raise Exception("test error")

    jr._run_test_job = _fail_test
    try:
        start_job(slug="test", workflow="test_workflow")
        time.sleep(2.5)
        st7 = _get("/api/jobs/status")
        job7 = st7.get("job") or {}
        msgs7 = []
        state = get_orchestrator_state()
        while True:
            try:
                entry = state.log_stream.get_nowait()
                msgs7.append(str(entry.get("msg", "")))
            except Exception:
                break
        t7 = job7.get("status") == "failed" and any(
            "[JOB] ✗ Workflow fallito" in m for m in msgs7
        )
        results.append(
            f"[{'x' if t7 else ' '}] 7. fail simulato -> failed + log X ({job7.get('status')})"
        )
        ok &= t7
    finally:
        jr._run_test_job = orig

    print("\n".join(results))
    print("\nEsito:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

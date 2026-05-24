#!/usr/bin/env python3
"""
MT-1.13 — Smoke HTTP orchestrator UI.

Crea un progetto temporaneo, avvia `test_workflow` (no LLM) via API,
verifica risposta di successo da POST /api/jobs/start.

Uso (server già in esecuzione):
  py -3.10 scripts/smoke_test.py

Avvio server Flask in background se :7842 non risponde:
  py -3.10 scripts/smoke_test.py --spawn-server

Exit code 0 = PASS, 1 = FAIL.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DEFAULT_BASE = "http://127.0.0.1:7842"
DEFAULT_WORKFLOW = "test_workflow"


def _request(
    method: str,
    base: str,
    path: str,
    body: dict | None = None,
    *,
    timeout: float = 30.0,
) -> tuple[int, dict | list | None]:
    url = f"{base.rstrip('/')}{path}"
    data = json.dumps(body or {}).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as e:
        status = e.code
        raw = e.read().decode("utf-8", errors="replace")
    payload: dict | list | None = None
    if raw:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"raw": raw}
    return status, payload


def _wait_for_server(base: str, *, attempts: int = 40, delay: float = 0.25) -> bool:
    for _ in range(attempts):
        try:
            code, _ = _request("GET", base, "/api/workflows", timeout=3.0)
            if 200 <= code < 300:
                return True
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(delay)
    return False


def _spawn_flask_server(host: str, port: int) -> threading.Thread:
    from server import app

    def _run() -> None:
        app.run(host=host, port=port, threaded=True, use_reloader=False)

    thread = threading.Thread(target=_run, name="smoke-flask", daemon=True)
    thread.start()
    return thread


def _is_success_status(code: int) -> bool:
    """jobs/start restituisce 202 Accepted; health/workflows 200."""
    return 200 <= code < 300


def run_smoke(
    *,
    base_url: str = DEFAULT_BASE,
    workflow: str = DEFAULT_WORKFLOW,
    spawn_server: bool = False,
    wait_completion_s: float = 5.0,
) -> int:
    host = "127.0.0.1"
    port = 7842
    if ":" in base_url.rsplit("/", 1)[-1]:
        pass
    try:
        port = int(base_url.rsplit(":", 1)[-1])
    except ValueError:
        port = 7842

    if spawn_server or not _wait_for_server(base_url, attempts=4):
        print(f"[smoke] Avvio server Flask su {host}:{port} …")
        _spawn_flask_server(host, port)
        if not _wait_for_server(base_url):
            print("[smoke] FAIL — server non raggiungibile", file=sys.stderr)
            return 1

    print(f"[smoke] Server OK — {base_url}")

    code, _ = _request("POST", base_url, "/api/jobs/reset")
    if code >= 400 and code != 404:
        print(f"[smoke] WARN reset orchestrator: HTTP {code}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    project_name = f"smoke-{stamp}"
    code, created = _request(
        "POST",
        base_url,
        "/api/projects",
        {"name": project_name, "workflow": workflow},
    )
    if not _is_success_status(code):
        print(f"[smoke] FAIL create project — HTTP {code}: {created}", file=sys.stderr)
        return 1

    slug = ""
    if isinstance(created, dict):
        slug = str(created.get("slug") or "")
    if not slug:
        print("[smoke] FAIL — slug progetto mancante nella risposta", file=sys.stderr)
        return 1

    print(f"[smoke] Progetto creato: {slug} (HTTP {code})")

    code, job = _request(
        "POST",
        base_url,
        "/api/jobs/start",
        {"project": slug, "workflow": workflow},
    )
    if not _is_success_status(code):
        print(
            f"[smoke] FAIL jobs/start — HTTP {code}: {job}",
            file=sys.stderr,
        )
        return 1

    print(f"[smoke] jobs/start OK — HTTP {code} workflow={workflow}")
    if isinstance(job, dict):
        print(
            f"[smoke] job status={job.get('status')} "
            f"project={job.get('project')} queue_seq={job.get('queue_seq')}"
        )

    if wait_completion_s > 0 and workflow == DEFAULT_WORKFLOW:
        time.sleep(wait_completion_s)
        st_code, status = _request("GET", base_url, "/api/jobs/status")
        if st_code == 200 and isinstance(status, dict):
            running = status.get("running")
            job_snap = status.get("job") or {}
            print(
                f"[smoke] jobs/status: running={running} "
                f"job_status={job_snap.get('status')}"
            )

    print("[smoke] PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test MT-1.13 (HTTP API)")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE,
        help=f"Base URL orchestrator (default {DEFAULT_BASE})",
    )
    parser.add_argument(
        "--workflow",
        default=DEFAULT_WORKFLOW,
        help="Workflow da avviare (default test_workflow, no LLM)",
    )
    parser.add_argument(
        "--spawn-server",
        action="store_true",
        help="Avvia Flask in background se la porta non risponde",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Non attendere completamento test_workflow",
    )
    args = parser.parse_args()
    wait = 0.0 if args.no_wait else 5.0
    return run_smoke(
        base_url=args.base_url,
        workflow=args.workflow,
        spawn_server=args.spawn_server,
        wait_completion_s=wait,
    )


if __name__ == "__main__":
    raise SystemExit(main())

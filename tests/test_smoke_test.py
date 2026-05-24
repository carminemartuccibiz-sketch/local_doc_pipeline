"""Smoke MT-1.13 — jobs/start via Flask test client (no server esterno)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("flask")

REPO = Path(__file__).resolve().parent.parent
SMOKE_SCRIPT = REPO / "scripts" / "smoke_test.py"


def test_smoke_script_exists() -> None:
    assert SMOKE_SCRIPT.is_file()


@pytest.fixture
def flask_client():
    from server import app

    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_smoke_flow_create_project_and_start_job(flask_client) -> None:
    flask_client.post("/api/jobs/reset")
    create = flask_client.post(
        "/api/projects",
        json={"name": "smoke-pytest", "workflow": "test_workflow"},
    )
    assert create.status_code in (200, 201)
    slug = create.get_json().get("slug")
    assert slug

    start = flask_client.post(
        "/api/jobs/start",
        json={"project": slug, "workflow": "test_workflow"},
    )
    assert 200 <= start.status_code < 300
    body = start.get_json()
    assert body.get("project") == slug
    assert body.get("workflow") == "test_workflow"
    assert body.get("status") in ("queued", "running")


def test_run_smoke_spawn_server() -> None:
    spec = importlib.util.spec_from_file_location("smoke_test", SMOKE_SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rc = mod.run_smoke(spawn_server=True, wait_completion_s=4.0)
    assert rc == 0

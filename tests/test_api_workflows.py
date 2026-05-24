"""GET /api/workflows — esposizione plugin UI."""
from __future__ import annotations

import pytest

pytest.importorskip("flask")

from server import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_api_workflows_returns_object_with_workflows_array(client) -> None:
    r = client.get("/api/workflows")
    assert r.status_code == 200
    data = r.get_json()
    assert isinstance(data, dict)
    assert isinstance(data.get("workflows"), list)
    assert len(data["workflows"]) >= 3


def test_api_workflows_includes_new_plugins(client) -> None:
    r = client.get("/api/workflows")
    by_id = {w["id"]: w for w in r.get_json()["workflows"]}

    assert "ingest" in by_id
    assert by_id["ingest"]["label"] == "Ingest (Sliding Window)"

    for slug in ("code_analysis", "devblog", "doc_refactor"):
        assert slug in by_id, f"missing {slug}"
        assert "label" in by_id[slug]
        assert "description" in by_id[slug]


def test_code_analysis_has_ui_label_not_slug_title(client) -> None:
    by_id = {w["id"]: w for w in client.get("/api/workflows").get_json()["workflows"]}
    assert "DevSecOps" in by_id["code_analysis"]["label"] or "Code Analysis" in by_id["code_analysis"]["label"]


def test_workflow_runner_api_list_matches_registry() -> None:
    from engine.workflow_runner import WorkflowRunner

    api_ids = {w["id"] for w in WorkflowRunner.api_workflow_list()}
    reg_ids = set(WorkflowRunner.registered())
    assert {"code_analysis", "devblog", "doc_refactor"}.issubset(api_ids)
    assert "ingest" in api_ids
    assert reg_ids.issubset(api_ids | {"ingest"})

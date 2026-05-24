"""Verifica assenza import circolare clients.http_helpers ↔ engine."""
from __future__ import annotations


def test_import_http_helpers_before_engine_submodules() -> None:
    from clients import http_helpers

    assert callable(http_helpers.lm_request)
    assert callable(http_helpers.allm_request)


def test_import_anythingllm_without_conftest_bootstrap() -> None:
    from clients.anythingllm import AnythingLLMClient

    assert AnythingLLMClient is not None


def test_import_ai_tasks_and_job_runner_chain() -> None:
    from core import ai_tasks
    from engine import job_runner

    assert callable(ai_tasks.llm_complete)
    assert hasattr(job_runner, "_run_ingest_job")

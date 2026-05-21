"""Helper HTTP condivisi — trace verso interaction_logger."""
from __future__ import annotations

from typing import Any

import httpx

from engine.interaction_logger import SERVICE_ANYTHING_LLM, SERVICE_LM_STUDIO, logged_httpx_request


def lm_request(
    client: httpx.Client,
    method: str,
    url: str,
    **kwargs: Any,
) -> httpx.Response:
    return logged_httpx_request(
        client, method, url, SERVICE_LM_STUDIO, **kwargs
    )


def allm_request(
    client: httpx.Client,
    method: str,
    url: str,
    **kwargs: Any,
) -> httpx.Response:
    return logged_httpx_request(
        client, method, url, SERVICE_ANYTHING_LLM, **kwargs
    )

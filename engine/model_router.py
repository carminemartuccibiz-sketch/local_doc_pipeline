"""
FASE 4 — Auto-discovery modelli LM Studio + routing per task type.
"""
from __future__ import annotations

import logging
import os
from typing import Final

import httpx

from clients.http_helpers import lm_request
from config import LM_API_KEY, LM_OPENAI_BASE_URL, LM_TIMEOUT_S
from config.model_task_map import TASK_KEYWORDS

logger = logging.getLogger(__name__)


def _auth_headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if LM_API_KEY:
        h["Authorization"] = f"Bearer {LM_API_KEY}"
    return h


def _is_embedding_model_id(model_id: str) -> bool:
    low = model_id.lower()
    return any(
        k in low
        for k in ("embed", "embedding", "nomic-embed", "text-embedding", "e5-")
    )


def _is_vl_or_vision_model(model_id: str) -> bool:
    low = model_id.lower()
    return any(k in low for k in ("vl", "vision", "multimodal", "mm-"))


def _model_sort_key(model_id: str) -> tuple[int, str]:
    if _is_embedding_model_id(model_id):
        return (99, model_id)
    if _is_vl_or_vision_model(model_id):
        return (10, model_id)
    if "instruct" in model_id.lower() or "chat" in model_id.lower():
        return (0, model_id)
    return (5, model_id)


class ModelRouter:
    """GET /v1/models da LM Studio e scelta modello per task."""

    TASK_KEYWORDS: Final[dict[str, list[str]]] = TASK_KEYWORDS

    def __init__(self) -> None:
        self.available_models: list[str] = []
        self.refresh()

    def refresh(self) -> list[str]:
        """Re-chiama GET /v1/models e aggiorna self.available_models."""
        url = f"{LM_OPENAI_BASE_URL.rstrip('/')}/models"
        try:
            with httpx.Client(timeout=15.0, headers=_auth_headers()) as client:
                r = lm_request(client, "GET", url)
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPError as e:
            logger.warning("ModelRouter refresh fallito: %s", e)
            return list(self.available_models)

        models: list[str] = []
        for m in data.get("data") or []:
            if not isinstance(m, dict):
                continue
            mid = str(m.get("id") or "").strip()
            if mid and not _is_embedding_model_id(mid):
                models.append(mid)

        models.sort(key=_model_sort_key)
        self.available_models = models
        logger.info("ModelRouter: %d modelli disponibili", len(models))
        return list(self.available_models)

    def get_model_for_task(self, task_type: str) -> str:
        """
        Cerca il modello che matcha meglio per task_type (reasoning/summary/fast).
        Fallback: primo modello non-VL, non-embedding.
        """
        if not self.available_models:
            self.refresh()

        keywords = self.TASK_KEYWORDS.get(task_type.lower(), [])
        candidates = list(self.available_models)

        for kw in keywords:
            for mid in candidates:
                if kw.lower() in mid.lower():
                    return mid

        for mid in candidates:
            if not _is_vl_or_vision_model(mid):
                return mid

        if candidates:
            return candidates[0]

        override = (os.environ.get("LM_STUDIO_MODEL") or "").strip()
        if override and override.lower() not in ("auto", "discover", ""):
            return override

        from core.ai_tasks import discover_lm_studio_model

        return discover_lm_studio_model(force_refresh=True)

    def apply_model_for_task(self, task_type: str) -> str:
        """Sceglie modello e lo fissa per llm_complete / discover (sessione UI)."""
        from core.ai_tasks import set_session_lm_model

        model = self.get_model_for_task(task_type)
        return set_session_lm_model(model)


_router: ModelRouter | None = None


def get_model_router() -> ModelRouter:
    global _router
    if _router is None:
        _router = ModelRouter()
    return _router

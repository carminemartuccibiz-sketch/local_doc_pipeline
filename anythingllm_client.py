"""
Client REST AnythingLLM — workspace, upload, embeddings, vector-search.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from config import (
    ALLM_TIMEOUT_S,
    ANYTHINGLLM_API_KEY,
    ANYTHINGLLM_BASE_URL,
    UPLOAD_BATCH_PAUSE_S,
    WORKSPACE_NAME,
    WORKSPACE_SLUG,
)

logger = logging.getLogger(__name__)


class AnythingLLMError(RuntimeError):
    pass


class AnythingLLMClient:
    def __init__(
        self,
        base_url: str = ANYTHINGLLM_BASE_URL,
        api_key: str = ANYTHINGLLM_API_KEY,
        timeout: float = ALLM_TIMEOUT_S,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._headers = {"Accept": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

    def _url(self, path: str) -> str:
        return f"{self.base_url}/api{path}"

    def health(self) -> bool:
        try:
            with httpx.Client(timeout=10.0, headers=self._headers) as c:
                r = c.get(self._url("/ping"))
                return r.status_code == 200
        except httpx.RequestError:
            return False

    def list_workspaces(self) -> list[dict[str, Any]]:
        with httpx.Client(timeout=self.timeout, headers=self._headers) as c:
            r = c.get(self._url("/v1/workspaces"))
            if r.status_code == 403:
                raise AnythingLLMError(
                    "API key AnythingLLM mancante o non valida. "
                    "Imposta ANYTHINGLLM_API_KEY (Settings → API Keys)."
                )
            r.raise_for_status()
            data = r.json()
        return list(data.get("workspaces") or [])

    def ensure_workspace(
        self,
        name: str = WORKSPACE_NAME,
        slug: str = WORKSPACE_SLUG,
    ) -> str:
        for ws in self.list_workspaces():
            if ws.get("slug") == slug or ws.get("name") == name:
                actual = str(ws.get("slug") or slug)
                logger.info("Workspace esistente: %s (slug=%s)", name, actual)
                return actual

        payload = {
            "name": name,
            "slug": slug,
            "chatMode": "chat",
            "topN": 8,
            "similarityThreshold": 0.25,
        }
        with httpx.Client(timeout=self.timeout, headers=self._headers) as c:
            r = c.post(self._url("/v1/workspace/new"), json=payload)
            if r.status_code == 403:
                raise AnythingLLMError("Forbidden — verifica ANYTHINGLLM_API_KEY")
            r.raise_for_status()
            data = r.json()
        ws = data.get("workspace") or {}
        out_slug = str(ws.get("slug") or slug)
        logger.info("Workspace creato: %s → slug=%s", name, out_slug)
        return out_slug

    def upload_document(
        self,
        file_path: Path,
        *,
        workspace_slug: str,
        metadata: dict[str, str] | None = None,
    ) -> list[str]:
        """
        Upload file; ritorna lista location JSON da usare in update-embeddings.
        """
        import time

        meta_json = None
        if metadata:
            import json as _json

            meta_json = _json.dumps(metadata)

        with httpx.Client(timeout=self.timeout, headers=self._headers) as c:
            with file_path.open("rb") as fh:
                files = {"file": (file_path.name, fh, "text/markdown")}
                data: dict[str, str] = {"addToWorkspaces": workspace_slug}
                if meta_json:
                    data["metadata"] = meta_json
                r = c.post(self._url("/v1/document/upload"), files=files, data=data)
            if r.status_code == 403:
                raise AnythingLLMError("Upload forbidden — API key?")
            if r.status_code >= 400:
                raise AnythingLLMError(f"Upload failed {r.status_code}: {r.text[:500]}")
            body = r.json()

        locations: list[str] = []
        for doc in body.get("documents") or []:
            loc = doc.get("location")
            if loc:
                locations.append(str(loc))
        time.sleep(UPLOAD_BATCH_PAUSE_S)
        return locations

    def update_embeddings(
        self,
        workspace_slug: str,
        *,
        adds: list[str] | None = None,
        deletes: list[str] | None = None,
    ) -> None:
        payload: dict[str, list[str]] = {}
        if adds:
            payload["adds"] = adds
        if deletes:
            payload["deletes"] = deletes
        if not payload:
            return
        with httpx.Client(timeout=self.timeout, headers=self._headers) as c:
            r = c.post(
                self._url(f"/v1/workspace/{workspace_slug}/update-embeddings"),
                json=payload,
            )
            r.raise_for_status()

    def vector_search(
        self,
        workspace_slug: str,
        query: str,
        *,
        top_n: int = 12,
        score_threshold: float = 0.15,
    ) -> list[dict[str, Any]]:
        payload = {
            "query": query,
            "topN": top_n,
            "scoreThreshold": score_threshold,
        }
        with httpx.Client(timeout=self.timeout, headers=self._headers) as c:
            r = c.post(
                self._url(f"/v1/workspace/{workspace_slug}/vector-search"),
                json=payload,
            )
            if r.status_code >= 400:
                logger.warning("vector-search fallita: %s", r.text[:300])
                return []
            data = r.json()
        return list(data.get("results") or [])

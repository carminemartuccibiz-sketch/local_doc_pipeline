"""
Client REST AnythingLLM — workspace, upload, embeddings, vector-search.
"""
from __future__ import annotations

import logging
import os
import time
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

    def get_workspace(self, slug: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout, headers=self._headers) as c:
            r = c.get(self._url(f"/v1/workspace/{slug}"))
            if r.status_code == 404:
                return {}
            r.raise_for_status()
            data = r.json()
        return dict(data.get("workspace") or data or {})

    def list_workspace_document_keys(self, workspace_slug: str) -> set[str]:
        """
        Chiavi documento già nel workspace (title / docSource / filename / docpath).
        Best-effort: dipende dalla versione AnythingLLM.
        """
        keys: set[str] = set()
        ws = self.get_workspace(workspace_slug)

        def _add(val: Any) -> None:
            if not val:
                return
            s = str(val).replace("\\", "/")
            keys.add(s)
            keys.add(Path(s).name)

        for bucket in (
            ws.get("documents"),
            ws.get("files"),
            ws.get("workspaceDocuments"),
        ):
            if not isinstance(bucket, list):
                continue
            for doc in bucket:
                if not isinstance(doc, dict):
                    continue
                for field in (
                    "docSource",
                    "title",
                    "filename",
                    "name",
                    "originalFilename",
                    "docpath",
                    "location",
                ):
                    _add(doc.get(field))
                meta = doc.get("metadata")
                if isinstance(meta, dict):
                    for field in ("docSource", "title", "filename"):
                        _add(meta.get(field))

        # Alcune build annidano documenti in sotto-oggetti
        def _walk(obj: Any, depth: int = 0) -> None:
            if depth > 6:
                return
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k in (
                        "docSource",
                        "title",
                        "filename",
                        "name",
                        "docpath",
                        "location",
                    ):
                        _add(v)
                    _walk(v, depth + 1)
            elif isinstance(obj, list):
                for item in obj:
                    _walk(item, depth + 1)

        _walk(ws)
        return keys

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
        timeout_s: float | None = None,
        retries: int = 1,
    ) -> None:
        """
        Incorpora documenti nel vector DB del workspace.
        L'operazione può richiedere minuti: usare timeout_s alto (es. 600–900).
        """
        payload: dict[str, list[str]] = {}
        if adds:
            payload["adds"] = adds
        if deletes:
            payload["deletes"] = deletes
        if not payload:
            return

        t = timeout_s or float(os.environ.get("ALLM_EMBED_TIMEOUT_S", "600"))
        url = self._url(f"/v1/workspace/{workspace_slug}/update-embeddings")
        last_err: Exception | None = None

        for attempt in range(1, max(1, retries) + 1):
            try:
                logger.info(
                    "AnythingLLM update-embeddings (%d doc, timeout %.0fs, tentativo %d)",
                    len(adds or []),
                    t,
                    attempt,
                )
                with httpx.Client(timeout=t, headers=self._headers) as c:
                    r = c.post(url, json=payload)
                if r.status_code >= 400:
                    raise AnythingLLMError(
                        f"update-embeddings HTTP {r.status_code}: {r.text[:500]}"
                    )
                return
            except (httpx.TimeoutException, httpx.ReadTimeout) as e:
                last_err = e
                logger.warning(
                    "Embedding timeout (%.0fs) — AnythingLLM potrebbe comunque "
                    "completare in background nell'UI",
                    t,
                )
                if attempt < retries:
                    time.sleep(5.0)
            except httpx.HTTPError as e:
                last_err = e
                if attempt < retries:
                    time.sleep(3.0)
                else:
                    raise AnythingLLMError(f"update-embeddings fallito: {e}") from e

        if last_err:
            raise AnythingLLMError(
                f"update-embeddings timeout dopo {retries} tentativi "
                f"(timeout={t:.0f}s). Usa ALLM_EMBED_MODE=manual e incorpora dall'UI."
            ) from last_err

    def probe_vector_search(
        self,
        workspace_slug: str,
        query: str = "DVAMOCLES LAST DOCS Material Forge",
    ) -> bool:
        """True se il workspace risponde a vector-search (embedding pronti)."""
        hits = self.vector_search(workspace_slug, query, top_n=1, score_threshold=0.0)
        return bool(hits)

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

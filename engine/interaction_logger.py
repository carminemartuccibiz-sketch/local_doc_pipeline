"""
Logging integrale API (rolling 5 interazioni) + app system log rotante.

- logs/api_interactions.json — ultimi 5 request/response JSON (LM Studio, AnythingLLM, APP)
- logs/app_system.log — RotatingFileHandler 5 MB × 1 backup
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import httpx

from config.settings import PIPELINE_ROOT
from core.file_io import atomic_write_json

LOGS_ROOT = PIPELINE_ROOT / "logs"
API_INTERACTIONS_PATH = LOGS_ROOT / "api_interactions.json"
APP_SYSTEM_LOG_PATH = LOGS_ROOT / "app_system.log"

MAX_INTERACTIONS = 5
MAX_FIELD_CHARS = 48_000
APP_LOGGER_NAME = "dvamocles.app"

from clients.http_trace import SERVICE_ANYTHING_LLM, SERVICE_LM_STUDIO

SERVICE_APP_SYSTEM = "APP_SYSTEM"

_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "api_key",
        "api-key",
        "apikey",
        "x-api-key",
        "token",
        "access_token",
        "refresh_token",
        "password",
        "secret",
        "bearer",
    }
)
_BEARER_RE = re.compile(r"^Bearer\s+\S+", re.IGNORECASE)

_app_logger_configured = False
_app_config_lock = threading.Lock()


def ensure_logs_dir() -> Path:
    LOGS_ROOT.mkdir(parents=True, exist_ok=True)
    return LOGS_ROOT


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate_str(value: str, limit: int = MAX_FIELD_CHARS) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"... [truncated, {len(value)} chars total]"


def sanitize_payload(obj: Any, *, _depth: int = 0) -> Any:
    """Rimuove/maschera token e API key prima della persistenza."""
    if _depth > 12:
        return "[max depth]"

    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, val in obj.items():
            key_l = str(key).lower().replace("_", "-")
            if key_l in _SENSITIVE_KEYS or any(s in key_l for s in ("api-key", "apikey", "secret")):
                out[key] = "[REDACTED]"
                continue
            if key_l == "authorization" or (isinstance(val, str) and _BEARER_RE.match(val.strip())):
                out[key] = "[REDACTED]"
                continue
            out[key] = sanitize_payload(val, _depth=_depth + 1)
        return out

    if isinstance(obj, list):
        return [sanitize_payload(item, _depth=_depth + 1) for item in obj[:200]]

    if isinstance(obj, str):
        if _BEARER_RE.match(obj.strip()):
            return "[REDACTED]"
        if len(obj) > 24 and re.match(r"^[A-Za-z0-9_\-]{20,}$", obj):
            # possibile API key grezza
            if " " not in obj and obj.count(".") < 2:
                return "[REDACTED]"
        return _truncate_str(obj)

    if isinstance(obj, (int, float, bool)) or obj is None:
        return obj

    return _truncate_str(str(obj))


def build_request_payload(
    method: str,
    url: str,
    *,
    json_body: Any = None,
    data: Any = None,
    files: Any = None,
    headers: dict[str, str] | None = None,
    params: Any = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"method": method.upper(), "url": url}
    if params is not None:
        payload["params"] = sanitize_payload(params)
    if headers:
        payload["headers"] = sanitize_payload(dict(headers))
    if json_body is not None:
        payload["json"] = sanitize_payload(json_body)
    if data is not None:
        if isinstance(data, dict):
            payload["data"] = sanitize_payload(data)
        else:
            payload["data"] = _truncate_str(str(data))
    if files is not None:
        if isinstance(files, dict):
            payload["files"] = {
                k: f"<file:{v[0] if isinstance(v, tuple) else v}>"
                for k, v in files.items()
            }
        elif isinstance(files, list):
            payload["files"] = [
                f"<file:{item[0] if isinstance(item, tuple) else item}>"
                for item in files
            ]
        else:
            payload["files"] = "<multipart>"
    return payload


def parse_response_payload(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
        if isinstance(body, dict):
            out = dict(body)
        elif isinstance(body, list):
            out = {"_list": body}
        else:
            out = {"_value": body}
    except (json.JSONDecodeError, ValueError):
        text = response.text or ""
        out = {
            "_raw_text": _truncate_str(text),
            "_content_type": response.headers.get("content-type"),
        }
    out["status_code"] = response.status_code
    return sanitize_payload(out)


def setup_app_system_logger() -> logging.Logger:
    """RotatingFileHandler su logs/app_system.log (idempotente)."""
    global _app_logger_configured
    with _app_config_lock:
        ensure_logs_dir()
        app_log = logging.getLogger(APP_LOGGER_NAME)
        if _app_logger_configured:
            return app_log

        app_log.setLevel(logging.DEBUG)
        handler = RotatingFileHandler(
            APP_SYSTEM_LOG_PATH,
            maxBytes=5 * 1024 * 1024,
            backupCount=1,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        app_log.addHandler(handler)
        app_log.propagate = True
        _app_logger_configured = True
        app_log.info("App system logger inizializzato → %s", APP_SYSTEM_LOG_PATH)
        return app_log


def log_app_system(message: str, level: int = logging.INFO, **extra: Any) -> None:
    try:
        setup_app_system_logger()
        app_log = logging.getLogger(APP_LOGGER_NAME)
        if extra:
            message = f"{message} | {sanitize_payload(extra)}"
        app_log.log(level, message)
    except Exception:
        pass


class InteractionLogger:
    """Singleton — rolling buffer di 5 interazioni API su disco."""

    _instance: InteractionLogger | None = None
    _singleton_lock = threading.Lock()

    def __init__(self) -> None:
        self._io_lock = threading.Lock()
        ensure_logs_dir()
        setup_app_system_logger()

    @classmethod
    def get(cls) -> InteractionLogger:
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _load_records(self) -> list[dict[str, Any]]:
        if not API_INTERACTIONS_PATH.is_file():
            return []
        try:
            raw = json.loads(API_INTERACTIONS_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                return [r for r in raw if isinstance(r, dict)]
        except (json.JSONDecodeError, OSError):
            pass
        return []

    def record(
        self,
        *,
        service: str,
        endpoint: str,
        request_payload: dict[str, Any],
        response_payload: Any,
        duration_ms: float,
    ) -> None:
        entry = {
            "timestamp": _utc_iso(),
            "service": service,
            "endpoint": endpoint,
            "request_payload": sanitize_payload(request_payload),
            "response_payload": sanitize_payload(response_payload),
            "duration_ms": round(duration_ms, 2),
        }
        try:
            with self._io_lock:
                records = self._load_records()
                if len(records) >= MAX_INTERACTIONS:
                    records.pop(0)
                records.append(entry)
                atomic_write_json(API_INTERACTIONS_PATH, records)
        except Exception as exc:
            logging.getLogger(__name__).debug(
                "Interaction log skip (non bloccante): %s", exc
            )

    def log_system_event(
        self,
        event: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Eventi orchestratore (kill switch, reset, avvio job)."""
        try:
            self.record(
                service=SERVICE_APP_SYSTEM,
                endpoint=f"app://{event}",
                request_payload={"event": event, "details": details or {}},
                response_payload={"status": "logged"},
                duration_ms=0.0,
            )
            log_app_system(f"APP_SYSTEM: {event}", extra=details or {})
        except Exception:
            pass


def get_interaction_logger() -> InteractionLogger:
    return InteractionLogger.get()


def logged_httpx_request(
    client: httpx.Client,
    method: str,
    url: str,
    service: str,
    *,
    json: Any = None,
    data: Any = None,
    files: Any = None,
    params: Any = None,
    headers: dict[str, str] | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """
    Wrap httpx Client.request — registra request/response senza mai propagare errori di log.
    """
    method_u = method.upper()
    req_payload = build_request_payload(
        method_u,
        url,
        json_body=json,
        data=data,
        files=files,
        headers=headers or dict(client.headers),
        params=params,
    )
    started = time.perf_counter()
    try:
        response = client.request(
            method_u,
            url,
            json=json,
            data=data,
            files=files,
            params=params,
            headers=headers,
            **kwargs,
        )
        duration_ms = (time.perf_counter() - started) * 1000.0
        try:
            get_interaction_logger().record(
                service=service,
                endpoint=url,
                request_payload=req_payload,
                response_payload=parse_response_payload(response),
                duration_ms=duration_ms,
            )
        except Exception:
            pass
        return response
    except Exception as exc:
        duration_ms = (time.perf_counter() - started) * 1000.0
        err_payload: dict[str, Any] = {
            "error": str(exc),
            "type": type(exc).__name__,
        }
        if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
            try:
                err_payload["response"] = parse_response_payload(exc.response)
            except Exception:
                err_payload["response_text"] = _truncate_str(exc.response.text or "")
        try:
            get_interaction_logger().record(
                service=service,
                endpoint=url,
                request_payload=req_payload,
                response_payload=err_payload,
                duration_ms=duration_ms,
            )
        except Exception:
            pass
        raise

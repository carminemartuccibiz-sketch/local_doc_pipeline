"""
Configurazione runtime — LM Studio, AnythingLLM, path pipeline.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from config.settings import (  # noqa: F401 — load .env via settings import
    ALLM_SOT_WORKSPACE_NAME,
    ALLM_SOT_WORKSPACE_SLUG,
    ANYTHINGLLM_INSTALL_DIR,
    DATA_ROOT,
    GAP_BATCH_SIZE,
    INGEST_COPY_SOURCES,
    LM_STUDIO_INSTALL_DIR,
    PIPELINE_CHUNK_COOLDOWN_S,
    PIPELINE_HARDWARE_PROFILE,
    PIPELINE_LM_COOLDOWN_S,
    PIPELINE_MAX_CONCURRENCY,
    PIPELINE_ROOT,
    SOT_TIERS,
)

DEFAULT_SOURCE_ROOT: Final[Path] = Path(
    os.environ.get(
        "DVAMOCLES_SOURCE_ROOT",
        r"E:\DVAMOCLES-SWORD-AMBIENT-FULL-DOCUMENTATION",
    )
)

WORKSPACE_NAME: Final[str] = os.environ.get("ALLM_WORKSPACE_NAME", "Dvamocles_Raw_Docs")
WORKSPACE_SLUG: Final[str] = os.environ.get(
    "ALLM_WORKSPACE_SLUG", "dvamocles-raw-docs"
)

ANYTHINGLLM_BASE_URL: Final[str] = os.environ.get(
    "ANYTHINGLLM_BASE_URL", "http://localhost:3001"
).rstrip("/")
ANYTHINGLLM_API_V1_URL: Final[str] = os.environ.get(
    "ANYTHINGLLM_API_V1_URL",
    f"{ANYTHINGLLM_BASE_URL}/api/v1",
).rstrip("/")
ANYTHINGLLM_API_KEY: Final[str] = os.environ.get("ANYTHINGLLM_API_KEY", "").strip()

LM_OPENAI_BASE_URL: Final[str] = os.environ.get(
    "LM_STUDIO_BASE_URL", "http://localhost:1234/v1"
).rstrip("/")
LM_NATIVE_CHAT_URL: Final[str] = os.environ.get(
    "LM_STUDIO_NATIVE_CHAT_URL", "http://localhost:1234/api/v1/chat"
).rstrip("/")
LM_API_KEY: Final[str] = (
    os.environ.get("LM_STUDIO_API_KEY")
    or os.environ.get("OPENAI_API_KEY")
    or "lm-studio"
).strip()
LM_MODEL: Final[str] = os.environ.get("LM_STUDIO_MODEL", "").strip()
LM_MODEL_FALLBACK: Final[str] = os.environ.get(
    "LM_STUDIO_MODEL_FALLBACK", "local-model"
)
LM_USE_NATIVE_CHAT: Final[bool] = os.environ.get("LM_USE_NATIVE_CHAT", "").lower() in (
    "1",
    "true",
    "yes",
)

STAGING_DIR: Final[Path] = DATA_ROOT / ".staging_md"
STATE_FILE: Final[Path] = DATA_ROOT / "pipeline_state.json"
LOG_DIR: Final[Path] = DATA_ROOT / "logs"

OUTPUT_DIR_NAME: Final[str] = "Dvamocles_Pre_Claude_Refactor"

ACCEPTED_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".md",
        ".markdown",
        ".txt",
        ".pdf",
        ".docx",
        ".html",
        ".htm",
        ".json",
        ".xml",
        ".csv",
        ".log",
    }
)

EXCLUDE_DIR_NAMES: Final[frozenset[str]] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".cursor",
        "qdrant_storage",
        "agent-tools",
        ".staging_md",
        "data",
        "Dvamocles_Pre_Claude_Refactor",
        "dist",
        "build",
        ".haystack",
        "projects",
        "legacy",
        "docs",
    }
)

MAX_FILE_BYTES: Final[int] = int(os.environ.get("PIPELINE_MAX_FILE_BYTES", str(8 * 1024 * 1024)))
MAX_CHARS_PER_LM_CALL: Final[int] = int(os.environ.get("PIPELINE_MAX_CHARS", "24000"))
RAG_TOP_N: Final[int] = int(os.environ.get("PIPELINE_RAG_TOP_N", "12"))
LM_TIMEOUT_S: Final[float] = float(os.environ.get("LM_TIMEOUT_S", "300"))
ALLM_TIMEOUT_S: Final[float] = float(os.environ.get("ALLM_TIMEOUT_S", "120"))
UPLOAD_BATCH_PAUSE_S: Final[float] = float(os.environ.get("UPLOAD_BATCH_PAUSE_S", "0.5"))

GAP_USE_ALLM_RAG: Final[bool] = os.environ.get("GAP_USE_ALLM_RAG", "true").lower() in (
    "1",
    "true",
    "yes",
)
GAP_RAG_TOP_N: Final[int] = int(os.environ.get("GAP_RAG_TOP_N", "10"))
GAP_RAG_SCORE_THRESHOLD: Final[float] = float(
    os.environ.get("GAP_RAG_SCORE_THRESHOLD", "0.45")
)


def output_dir(source_root: Path) -> Path:
    return source_root / OUTPUT_DIR_NAME


def ensure_pipeline_dirs(source_root: Path) -> tuple[Path, Path, Path]:
    out = output_dir(source_root)
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    return STAGING_DIR, out, LOG_DIR

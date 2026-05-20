"""
Configurazione pipeline locale DVAMOCLES — LM Studio + AnythingLLM.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Final

import settings as _settings  # noqa: F401 — carica .env

# Repo DVAMOCLES (default)
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

# AnythingLLM (desktop default port 3001, API REST sotto /api/v1)
ANYTHINGLLM_BASE_URL: Final[str] = os.environ.get(
    "ANYTHINGLLM_BASE_URL", "http://localhost:3001"
).rstrip("/")
ANYTHINGLLM_API_V1_URL: Final[str] = os.environ.get(
    "ANYTHINGLLM_API_V1_URL",
    f"{ANYTHINGLLM_BASE_URL}/api/v1",
).rstrip("/")
ANYTHINGLLM_API_KEY: Final[str] = os.environ.get("ANYTHINGLLM_API_KEY", "").strip()

# LM Studio — OpenAI-compatible su porta 1234 (Developer -> Start Server)
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
# Fallback solo se auto-discovery fallisce; preferire LM_STUDIO_MODEL=auto in .env
LM_MODEL: Final[str] = os.environ.get("LM_STUDIO_MODEL", "").strip()
LM_MODEL_FALLBACK: Final[str] = os.environ.get(
    "LM_STUDIO_MODEL_FALLBACK", "local-model"
)
LM_USE_NATIVE_CHAT: Final[bool] = os.environ.get("LM_USE_NATIVE_CHAT", "").lower() in (
    "1",
    "true",
    "yes",
)

# Percorsi pipeline (sotto tools/local_doc_pipeline)
PIPELINE_ROOT: Final[Path] = Path(__file__).resolve().parent
STAGING_DIR: Final[Path] = PIPELINE_ROOT / ".staging_md"
STATE_FILE: Final[Path] = PIPELINE_ROOT / "pipeline_state.json"
LOG_DIR: Final[Path] = PIPELINE_ROOT / "logs"

# Output finale (sotto root repo — mai sovrascrive sorgenti)
OUTPUT_DIR_NAME: Final[str] = "Dvamocles_Pre_Claude_Refactor"

# Estensioni ingestibili
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

# Cartelle sempre escluse dalla scansione
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
        "Dvamocles_Pre_Claude_Refactor",
        "dist",
        "build",
        ".haystack",
    }
)

# File troppo grandi: salta conversione LLM multi-pass se oltre soglia
MAX_FILE_BYTES: Final[int] = int(os.environ.get("PIPELINE_MAX_FILE_BYTES", str(8 * 1024 * 1024)))
MAX_CHARS_PER_LM_CALL: Final[int] = int(os.environ.get("PIPELINE_MAX_CHARS", "24000"))
RAG_TOP_N: Final[int] = int(os.environ.get("PIPELINE_RAG_TOP_N", "12"))
LM_TIMEOUT_S: Final[float] = float(os.environ.get("LM_TIMEOUT_S", "300"))
ALLM_TIMEOUT_S: Final[float] = float(os.environ.get("ALLM_TIMEOUT_S", "120"))
UPLOAD_BATCH_PAUSE_S: Final[float] = float(os.environ.get("UPLOAD_BATCH_PAUSE_S", "0.5"))

# Gap analysis + SOT RAG
from settings import (  # noqa: E402
    ALLM_SOT_WORKSPACE_NAME,
    ALLM_SOT_WORKSPACE_SLUG,
    ANYTHINGLLM_INSTALL_DIR,
    INGEST_COPY_SOURCES,
    LM_STUDIO_INSTALL_DIR,
    SOT_TIERS,
)

GAP_USE_ALLM_RAG: Final[bool] = os.environ.get("GAP_USE_ALLM_RAG", "true").lower() in (
    "1",
    "true",
    "yes",
)
GAP_RAG_TOP_N: Final[int] = int(os.environ.get("GAP_RAG_TOP_N", "10"))


def output_dir(source_root: Path) -> Path:
    return source_root / OUTPUT_DIR_NAME


def ensure_pipeline_dirs(source_root: Path) -> tuple[Path, Path, Path]:
    out = output_dir(source_root)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    return STAGING_DIR, out, LOG_DIR

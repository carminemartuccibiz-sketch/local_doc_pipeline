"""
Impostazioni centrali DVAMOCLES local_doc_pipeline (non committare segreti in questo file).
Carica `.env` dalla cartella pipeline; override con variabili d'ambiente di sistema.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Final

PIPELINE_ROOT: Final[Path] = Path(__file__).resolve().parent


def load_environment() -> None:
    """Carica .env e .env.local dalla root pipeline."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for name in (".env", ".env.local"):
        path = PIPELINE_ROOT / name
        if path.is_file():
            load_dotenv(path, override=False)


load_environment()

# --- Gerarchia Source of Truth (Gap Analysis) ---
# Tier 1 vince su tier 2 in caso di conflitto documentale.
SOT_TIERS: Final[tuple[dict[str, object], ...]] = (
    {
        "tier": 1,
        "rel": "LAST DOCS",
        "label": "Documentazione canonica recente (LAST DOCS)",
    },
    {
        "tier": 2,
        "rel": "Documentazione vecchia",
        "label": "Documentazione canonica precedente",
    },
)

# Cartelle sorgente per popolamento sicuro 01_RAW_INGEST (solo copia, mai delete)
INGEST_COPY_SOURCES: Final[tuple[tuple[str, str], ...]] = (
    ("Takeout", "extra files/Takeout"),
    ("ChatGPT_Chats", "extra files/ChatGPT Chats"),
    ("Claude_Chats", "extra files/claude chats"),
    ("Gemini_Chats", "extra files/Gemini Chats"),
    ("Comet_Perplexity", "extra files/Comet-Perplexity Chat export pdf"),
    ("Raw_Docs", "refactoring documentale raw/Raw docs"),
    ("Docs_Obsoleti", "Docs Obsoleti"),
    ("Documentazione_vecchia_mirror", "Documentazione vecchia"),
    ("refactor_EXTRA", "refactor fatto/EXTRA"),
)

# AnythingLLM workspace dedicato al contesto SOT (RAG gap analysis)
ALLM_SOT_WORKSPACE_NAME: Final[str] = os.environ.get(
    "ALLM_SOT_WORKSPACE_NAME", "Dvamocles_SOT_Canon"
)
# AnythingLLM normalizza spesso gli slug con underscore
ALLM_SOT_WORKSPACE_SLUG: Final[str] = os.environ.get(
    "ALLM_SOT_WORKSPACE_SLUG", "dvamocles_sot_canon"
)

# Gap analysis: solo LAST DOCS come SOT di confronto (tier 1)
GAP_SOT_LAST_DOCS_ONLY: Final[bool] = os.environ.get(
    "GAP_SOT_LAST_DOCS_ONLY", "true"
).lower() in ("1", "true", "yes")

# File grezzi processati per esecuzione del .bat (uno alla volta, poi passa al successivo)
GAP_BATCH_SIZE: Final[int] = int(os.environ.get("GAP_BATCH_SIZE", "10"))

# Installazioni locali (riferimento; gli endpoint REST usano le porte standard)
LM_STUDIO_INSTALL_DIR: Final[Path] = Path(
    os.environ.get(
        "LM_STUDIO_INSTALL_DIR",
        r"C:\Users\Carmine\AppData\Local\Programs\LM Studio",
    )
)
ANYTHINGLLM_INSTALL_DIR: Final[Path] = Path(
    os.environ.get(
        "ANYTHINGLLM_INSTALL_DIR",
        r"C:\Users\Carmine\AppData\Local\Programs\AnythingLLM",
    )
)

# Endpoint REST standard (desktop default)
LM_STUDIO_OPENAI_BASE: Final[str] = os.environ.get(
    "LM_STUDIO_BASE_URL", "http://localhost:1234/v1"
).rstrip("/")
ANYTHINGLLM_HTTP_BASE: Final[str] = os.environ.get(
    "ANYTHINGLLM_BASE_URL", "http://localhost:3001"
).rstrip("/")
ANYTHINGLLM_API_V1_BASE: Final[str] = os.environ.get(
    "ANYTHINGLLM_API_V1_URL",
    f"{ANYTHINGLLM_HTTP_BASE}/api/v1",
).rstrip("/")

# Modello LM: lasciare vuoto o "auto" per discovery a runtime (GET /v1/models)
LM_STUDIO_MODEL_OVERRIDE: Final[str] = os.environ.get("LM_STUDIO_MODEL", "").strip()

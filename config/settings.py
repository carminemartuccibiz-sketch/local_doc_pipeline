"""
Impostazioni centrali — carica .env dalla root pipeline.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Final

# Root repository (parent di config/)
PIPELINE_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
DATA_ROOT: Final[Path] = PIPELINE_ROOT / "data"


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

ALLM_SOT_WORKSPACE_NAME: Final[str] = os.environ.get(
    "ALLM_SOT_WORKSPACE_NAME", "Dvamocles_SOT_Canon"
)
ALLM_SOT_WORKSPACE_SLUG: Final[str] = os.environ.get(
    "ALLM_SOT_WORKSPACE_SLUG", "dvamocles_sot_canon"
)

GAP_SOT_LAST_DOCS_ONLY: Final[bool] = os.environ.get(
    "GAP_SOT_LAST_DOCS_ONLY", "true"
).lower() in ("1", "true", "yes")

PIPELINE_MAX_CONCURRENCY: Final[int] = max(
    1, int(os.environ.get("PIPELINE_MAX_CONCURRENCY", "1"))
)
PIPELINE_LM_COOLDOWN_S: Final[float] = float(
    os.environ.get("PIPELINE_LM_COOLDOWN_S", "1.5")
)
PIPELINE_CHUNK_COOLDOWN_S: Final[float] = float(
    os.environ.get("PIPELINE_CHUNK_COOLDOWN_S", "0.5")
)
PIPELINE_HARDWARE_PROFILE: Final[str] = os.environ.get(
    "PIPELINE_HARDWARE_PROFILE", ""
).strip()

GAP_BATCH_SIZE: Final[int] = int(os.environ.get("GAP_BATCH_SIZE", "1"))

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

LM_STUDIO_MODEL_OVERRIDE: Final[str] = os.environ.get("LM_STUDIO_MODEL", "").strip()

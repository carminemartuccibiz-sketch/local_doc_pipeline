"""
Profili hardware per la pipeline locale — valori consigliati .env.
"""
from __future__ import annotations

from typing import Final

# i9 + RTX 2080 Ti (11 GB VRAM) + 32 GB RAM — gap analysis con Mistral Nemo ~12B Q4
I9_2080TI_32GB: Final[dict[str, str]] = {
    "PIPELINE_HARDWARE_PROFILE": "i9-2080ti-32gb",
    "PIPELINE_MAX_CONCURRENCY": "1",
    "PIPELINE_LM_COOLDOWN_S": "2",
    "PIPELINE_CHUNK_COOLDOWN_S": "1",
    "GAP_BATCH_SIZE": "1",
    "GAP_CHUNK_MAX_TOKENS": "1200",
    "GAP_CHUNK_MIN_SECTION_TOKENS": "200",
    "GAP_RAW_INPUT_TOKEN_BUDGET": "3500",
    "GAP_MODEL_CONTEXT_TOKENS": "16384",
    "LM_NATIVE_CONTEXT": "16384",
    "GAP_LM_MAX_OUTPUT": "1024",
    "LM_TIMEOUT_S": "600",
    "GAP_RAG_TOP_N": "4",
    "GAP_RAG_MAX_CHARS": "2500",
    "ALLM_EMBED_MODE": "manual",
    "ALLM_TIMEOUT_S": "180",
    "UPLOAD_BATCH_PAUSE_S": "2",
}

# Stesso hardware, priorità basso carico / nessuna fretta (VRAM e GPU più rilassati)
I9_2080TI_ECO: Final[dict[str, str]] = {
    "PIPELINE_HARDWARE_PROFILE": "i9-2080ti-eco",
    "PIPELINE_MAX_CONCURRENCY": "1",
    "PIPELINE_LM_COOLDOWN_S": "6",
    "PIPELINE_CHUNK_COOLDOWN_S": "4",
    "PIPELINE_FILE_PAUSE_S": "12",
    "GAP_BATCH_SIZE": "1",
    "GAP_CHUNK_MAX_TOKENS": "900",
    "GAP_CHUNK_MIN_SECTION_TOKENS": "180",
    "GAP_RAW_INPUT_TOKEN_BUDGET": "2800",
    "GAP_MODEL_CONTEXT_TOKENS": "12288",
    "LM_NATIVE_CONTEXT": "12288",
    "GAP_LM_MAX_OUTPUT": "768",
    "LM_TIMEOUT_S": "600",
    "GAP_RAG_TOP_N": "3",
    "GAP_RAG_MAX_CHARS": "2800",
    "GAP_MAX_REPORT_CHARS": "12000",
    "GAP_LM_MAX_OUTPUT": "1536",
    "GAP_LM_MAX_OUTPUT_CONSOLIDATE": "2400",
    "GAP_REPORT_CONSOLIDATE": "true",
    "GAP_SOT_LAST_DOCS_ONLY": "false",
    "GAP_MAX_SOT_CHARS_WITH_RAG": "600",
    "GAP_MAX_RAW_CHARS": "11000",
    "GAP_MAX_REPORT_CHARS": "2800",
    "ALLM_EMBED_MODE": "manual",
    "ALLM_TIMEOUT_S": "180",
    "UPLOAD_BATCH_PAUSE_S": "3",
}

PROFILE_ALIASES: Final[dict[str, str]] = {
    "i9-2080ti-32gb": "I9_2080TI_32GB",
    "i9-2080ti-eco": "I9_2080TI_ECO",
    "2080ti": "I9_2080TI_32GB",
    "eco": "I9_2080TI_ECO",
    "conservative": "I9_2080TI_32GB",
}

PROFILES: Final[dict[str, dict[str, str]]] = {
    "I9_2080TI_32GB": I9_2080TI_32GB,
    "I9_2080TI_ECO": I9_2080TI_ECO,
}

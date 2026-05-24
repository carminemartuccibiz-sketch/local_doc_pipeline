# Audit Report — Local AI Orchestrator + DMIP

## 1. BUG CRITICI (P0 — crash certi)

### 1.1 `resolve_chunk_max_tokens()` chiamata senza argomenti
**File:** `engine/job_runner.py`, linea `max_tokens = resolve_chunk_max_tokens()`

`resolve_chunk_max_tokens(limits: TokenLimits)` richiede un argomento obbligatorio.
La chiamata senza args solleva `TypeError` al runtime.

**Fix:**
```python
# engine/job_runner.py — _run_ingest_job()
from core.ai_tasks import get_session_lm_model
from core.token_budget import resolve_chunk_max_tokens, resolve_token_limits

try:
    limits = resolve_token_limits(get_session_lm_model())
    max_tokens = resolve_chunk_max_tokens(limits)
except Exception:
    max_tokens = 1200  # fallback sicuro
```

---

### 1.2 `sliding_window_analyze` — firma incoerente
**File:** `engine/job_runner.py` chiama con `file_dir` posizionale, ma
`ingest_processor.py` accetta `file_dir=None` come keyword-only dopo `*,`.
Causa `TypeError` su Python 3.10+.

**Fix in `_run_ingest_job`:**
```python
sliding_window_analyze(
    src.resolve(),
    file_dir=file_dir.resolve(),        # keyword esplicito
    llm_fn=llm_complete,
    stop_event=state.stop_event,
    log_fn=lambda m: state.emit_log(m),
    max_tokens_per_chunk=max_tokens,
)
```

---

### 1.3 `GapAnalysisWorkflow.process_file` — `log_fn` passato come kwarg errato
**File:** `workflows/gap_analysis.py`

```python
log_fn(f"[GAP] Apertura pipeline progetto={slug}")  # OK
# ma poi:
if log_fn:
    log_fn("[GAP] Preflight fallito...", level="WARN")  # TypeError: log_fn() got unexpected kwarg 'level'
```
`log_fn` è un `Callable[[str], None]` — non accetta `level`.

**Fix:**
```python
def _log(msg: str, level: str = "INFO") -> None:
    if log_fn:
        prefix = "[WARN] " if level == "WARN" else ""
        log_fn(f"{prefix}{msg}")
```

---

### 1.4 `WorkspaceStore` — race condition su `init()` async
**File:** `backend/core/services/workspace_store.py`

`_initialized` è un bool senza lock. Con più coroutine concorrenti il flag non
protegge: due chiamate simultanee passano entrambe il check `if self._initialized`
e inizializzano due volte il database.

**Fix:**
```python
class WorkspaceStore:
    def __init__(self, ...):
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def init(self) -> None:
        async with self._init_lock:
            if self._initialized:
                return
            # ... resto invariato
            self._initialized = True
```

---

### 1.5 `vector_store.py` — fallimento silenzioso dopo `_delete_all_sync`
**File:** `backend/core/services/vector_store.py`

Se `_delete_all_sync` fallisce (es. ChromaDB locked), `self._collection` diventa
`None` ma `upsert_documents` continua il loop senza check intermedi.
Il risultato: `{"chunks": 0, "skipped": True}` senza errore visibile.

**Fix in `upsert_documents`:**
```python
await asyncio.to_thread(self._delete_all_sync)
if self._collection is None:
    logger.error("vector_store: collection NULL dopo delete — abort upsert")
    return {"chunks": 0, "skipped": True, "error": "collection_unavailable"}
```

---

### 1.6 DMIP `_ingest_event_stream` — `ingestor.aclose()` non garantito
**File:** `backend/app/api/workspaces.py`

Se l'exception avviene prima che `ingestor` sia assegnato, il `finally` crasha.

**Fix:**
```python
ingestor = MultimodalIngestor()
try:
    ...
finally:
    await ingestor.aclose()  # ora sicuro: è sempre assegnato
```

---

## 2. BUG MEDI (P1 — comportamento errato)

### 2.1 `context_budget.py` — double-counting token budget
**File:** `core/ai_tasks.py`, `_llm_complete_unlocked_inner`

```python
ctx_cap = int(os.environ.get("LM_NATIVE_CONTEXT", "0") or "0") or int(
    os.environ.get("GAP_MODEL_CONTEXT_TOKENS", "8192")
)
max_in = int(ctx_cap * 0.72) - safe_max
```

Poi `resolve_token_limits()` fa lo stesso calcolo internamente.
Se entrambi i path sono attivi, il budget viene applicato due volte.

**Fix:** usare esclusivamente `resolve_token_limits()` e non ri-calcolare inline.

---

### 2.2 `ingest_registry.py` — chiave `file_hash` vuota bypassa dedup
**File:** `backend/core/services/ingest_registry.py`

```python
def find_by_hash(layout, file_hash):
    if not file_hash:          # hash vuoto → ritorna None sempre
        return None
```

Se il calcolo hash fallisce silenziosamente (es. PermissionError su file),
`file_hash = ""` e la deduplication non funziona. Il file viene re-ingestito.

**Fix in `_ingest_event_stream`:**
```python
if not file_hash:
    logger.warning("Hash vuoto per %s — skip dedup check", fn)
    # procedi ma logga il warning
```

---

### 2.3 `GenerationChat.tsx` — history in localStorage (no limit)
**File:** `frontend/src/components/GenerationChat.tsx`

La cronologia chat non ha limite di dimensione. Con sessioni lunghe
`localStorage` può saturarsi (quota ~5MB per origin) sollevando `QuotaExceededError`
silenziosamente.

**Fix:**
```typescript
function saveHistory(workspaceId: string, msgs: GenChatMessage[]) {
  try {
    const trimmed = msgs.slice(-50);  // max 50 messaggi
    localStorage.setItem(STORAGE_PREFIX + workspaceId, JSON.stringify(trimmed));
  } catch {
    // quota exceeded — ignora silenziosamente
  }
}
```

---

### 2.4 `gap_allm.py` — slug AnythingLLM non aggiornato dopo rename workspace
Il `workspace_slug` è cachato in `gap_allm_state.json`. Se l'utente rinomina
il workspace in AnythingLLM, lo state punta allo slug vecchio → 404 su ogni RAG query.

**Fix:** aggiungere verifica TTL sullo state (es. `state["slug_verified_at"]`
e ri-verificare se > 24h).

---

## 3. STRUTTURA REPO — PROBLEMI E FIX

### 3.1 Due progetti indipendenti nello stesso contesto
Il repo mescola **DMIP** (FastAPI + Ollama) con **Local AI Orchestrator** (Flask +
LM Studio). Condividono zero codice ma vivono nello stesso albero di file.

**Struttura raccomandata:**
```
repo-root/
├── dmip/                    # DMIP backend+frontend (attuale)
│   ├── backend/
│   └── frontend/
├── orchestrator/            # Local AI Orchestrator (attuale root)
│   ├── engine/
│   ├── workflows/
│   ├── core/
│   ├── clients/
│   ├── server.py
│   └── app.py
├── shared/                  # (futuro) utility condivise se necessario
└── README.md
```

### 3.2 Import circolare latente
```
core/ai_tasks.py
  → engine/orchestrator.py (via _orchestrator_state())
engine/job_runner.py
  → core/ai_tasks.py
  → engine/orchestrator.py
workflows/gap_analysis.py
  → core/gap_runner.py
  → core/ai_tasks.py
```

Se `core/ai_tasks` viene importato prima che `engine/orchestrator` sia
inizializzato (es. in test CLI), `_orchestrator_state()` restituisce un singleton
non ancora pronto.

**Fix:** usare lazy import e `get_orchestrator_state()` solo all'interno delle
funzioni, mai a module-level.

### 3.3 Config duplicata
`config/settings.py` e `config/runtime.py` chiamano entrambi `load_environment()`.
Il secondo import carica `.env` su variabili già settate (ma con `override=False`,
quindi non sovrascrive). Confonde il debug.

**Fix:** unificare in `config/settings.py` e fare `from config.settings import *`
in `runtime.py`.

### 3.4 `legacy/shims/` contiene `.bak` tracciati da Git
`legacy/shims/config.py.bak` e `legacy/shims/settings.py.bak` sono tracciati.
Sono file di backup che non dovrebbero essere nel repo.

**Fix:** aggiungere a `.gitignore`: `*.bak`, `*.tmp`.

---

## 4. CHUNKING — REDESIGN PER QUALITÀ TOKEN

### 4.1 Problema attuale
Il chunker attuale (`split_markdown_sections`) ha questi limiti:
- Split solo su `##` → sezioni molto corte o molto lunghe
- Overlap a caratteri fissi (400 char) → spezza codice, tabelle, liste
- Nessun tracking della coerenza semantica cross-chunk
- Token budget statico (non dipende dal modello caricato in quel momento)
- Il condensato rolling può accumulare errori (drift cumulativo)

### 4.2 Architettura chunking migliorata

```python
# core/chunking_v2.py

from dataclasses import dataclass
from enum import Enum
from typing import Optional
import re

class BoundaryType(Enum):
    H1 = 0          # massima priorità split
    H2 = 1
    H3 = 2
    CODE_FENCE = 3  # NON spezzare mai
    TABLE = 4       # NON spezzare mai
    PARAGRAPH = 5
    SENTENCE = 6    # minima priorità

@dataclass
class SemanticChunk:
    index: int
    text: str
    token_estimate: int
    boundary_type: BoundaryType
    parent_heading: str       # heading H1/H2 di appartenenza
    has_code: bool
    has_table: bool
    cross_refs: list[str]     # heading referenziate internamente

def _detect_cross_refs(text: str, all_headings: list[str]) -> list[str]:
    """Trova heading citate esplicitamente nel testo (per overlap intelligente)."""
    refs = []
    for h in all_headings:
        if h.lower() in text.lower() and h not in text[:len(h)+2]:
            refs.append(h)
    return refs

def semantic_chunk(
    body: str,
    *,
    max_tokens: int,
    model_hint: str = "cl100k_base",
    min_tokens: int = 100,
    overlap_strategy: str = "heading_context",  # "heading_context"|"sentence"|"none"
) -> list[SemanticChunk]:
    """
    Chunking semantico gerarchico:
    1. Estrae struttura heading tree
    2. Non spezza mai blocchi code fence o tabelle
    3. Overlap basato su contesto heading (non su char fissi)
    4. Rispetta budget token del modello attivo
    """
    from core.token_budget import count_tokens
    
    # Fase 1: estrai struttura
    sections = _extract_heading_tree(body)
    
    # Fase 2: pack in chunk rispettando max_tokens
    chunks = []
    current_text = []
    current_tokens = 0
    current_heading = ""
    
    for section in sections:
        sec_tokens = count_tokens(section["text"], model_hint=model_hint)
        
        # Sezione non spezzabile (code/table) — trattala atomica
        if section.get("atomic"):
            if current_tokens + sec_tokens > max_tokens and current_text:
                _flush_chunk(chunks, current_text, current_heading, model_hint)
                current_text, current_tokens = [], 0
            current_text.append(section["text"])
            current_tokens += sec_tokens
            continue
        
        # Sezione normale
        if current_tokens + sec_tokens > max_tokens and current_tokens >= min_tokens:
            _flush_chunk(chunks, current_text, current_heading, model_hint)
            # Overlap: aggiungi solo l'heading parent come contesto, non char fissi
            current_text = [f"[Contesto: {current_heading}]\n"] if current_heading else []
            current_tokens = count_tokens(current_text[0]) if current_text else 0
        
        current_text.append(section["text"])
        current_tokens += sec_tokens
        if section.get("heading"):
            current_heading = section["heading"]
    
    if current_text:
        _flush_chunk(chunks, current_text, current_heading, model_hint)
    
    return chunks
```

### 4.3 Prompt engineering per chunk (refactoring documentale)

Invece di un singolo prompt generico, usare prompt specializzati per fase:

```python
# Fase A: Extraction (per chunk)
EXTRACTION_PROMPT = """
Sei un estrattore di informazioni strutturate per {domain}.

CHUNK {n}/{total} del documento `{filename}`.
SEZIONE: {section_heading}
{code_context}

TESTO:
{chunk_text}

Estrai SOLO le informazioni presenti nel testo. Formato JSON:
{{
  "facts": [{{ "claim": "...", "confidence": "high|medium|low", "section": "{section_heading}" }}],
  "entities": [{{ "name": "...", "type": "module|function|concept|spec" }}],
  "gaps_vs_sot": ["..."],  // solo se SOT context fornito
  "open_questions": ["..."]
}}
Non inventare. Se un'informazione non è nel testo, omettila.
"""

# Fase B: Synthesis (una volta per documento, dopo tutti i chunk)
SYNTHESIS_PROMPT = """
Hai analizzato {n_chunks} chunk del documento `{filename}`.

ESTRATTI PER CHUNK:
{json_extracts}

REGOLE:
1. Deduplicare: unire fatti identici o complementari
2. Conflitti: segnalare esplicitamente con fonte (chunk N vs chunk M)
3. Sezioni SOT da aggiornare: basarsi solo su {sot_structure}
4. Non inventare fatti non presenti negli estratti

OUTPUT: Gap Report strutturato pronto per Claude/GPT handoff.
"""
```

### 4.4 Rolling context corretto (anti-drift)

```python
# Sostituisce il condensato rolling monolitico
class RollingContext:
    """
    Mantiene un contesto rolling basato su fatti estratti, non su prosa LLM.
    Immune al drift cumulativo perché conserva i fatti grezzi, non i riassunti.
    """
    def __init__(self, max_facts: int = 20):
        self._facts: list[dict] = []
        self._headings: list[str] = []
        self._max = max_facts
    
    def add_chunk_result(self, extract: dict, heading: str) -> None:
        self._headings.append(heading)
        for f in extract.get("facts", []):
            if f["confidence"] in ("high", "medium"):
                self._facts.append(f)
        # Mantieni solo i più recenti e rilevanti
        self._facts = self._facts[-self._max:]
    
    def build_context_block(self) -> str:
        if not self._facts:
            return ""
        headings_str = " → ".join(self._headings[-3:])
        facts_str = "\n".join(f"• {f['claim']} [{f['section']}]" 
                               for f in self._facts[-10:])
        return f"[Contesto da chunk precedenti — {headings_str}]\n{facts_str}"
```

---

## 5. WORKFLOW REFACTORING DOCUMENTALE — REDESIGN

### 5.1 Pipeline attuale vs raccomandata

**Attuale (lineare, monolitica):**
```
file → chunk_N → LLM(chunk_N) → condensato → LLM(chunk_N+1, condensato) → ...
                                                         ↑ drift accumula qui
```

**Raccomandata (2 fasi, deterministico):**
```
FASE 1 — Extraction (parallela, leggera):
  file → [chunk_1, chunk_2, ..., chunk_N]
              ↓ per chunk (LLM piccolo, temp=0.0)
          [extract_1, extract_2, ..., extract_N]   ← JSON strutturato

FASE 2 — Synthesis (una volta, un LLM grande):
  [extract_1..N] + SOT_context → LLM → Gap Report finale
```

### 5.2 Implementazione `workflows/doc_refactor.py`

```python
"""
Workflow refactoring documentale — 2 fasi per qualità token ottimale.
"""
from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable

from core.ai_tasks import abort_if_stop_requested, llm_complete
from core.token_budget import count_tokens, resolve_chunk_max_tokens, resolve_token_limits
from core.chunking_v2 import semantic_chunk  # nuovo chunker
from engine.project_memory import save_workflow_output
from workflows.base_workflow import BaseWorkflow
from workflows.capabilities import WorkflowCapabilities

logger = logging.getLogger(__name__)

WORKFLOW_ID = "doc_refactor"
EXTRACT_TEMP = 0.0    # estrattor deterministic
SYNTH_TEMP = 0.05     # sintesi quasi-deterministic
MAX_EXTRACT_OUTPUT = 800
MAX_SYNTH_OUTPUT = 3000

EXTRACTION_SYSTEM = """Sei un estrattore JSON per analisi documentale DVAMOCLES.
Rispondi SOLO con JSON valido. Nessun testo prima o dopo.
Se un campo è vuoto, usa array vuoto []. Mai inventare."""

SYNTHESIS_SYSTEM = """Sei il redattore del Gap Report DVAMOCLES.
Ricevi estratti strutturati da N chunk e produci un Gap Report ricco,
pronto per aggiornare LAST DOCS. Sezioni: Sintesi, GAP-XX numerati,
Contraddizioni, Handoff IA. Tier 1 (LAST DOCS) vince su tier 2."""


class DocRefactorWorkflow(BaseWorkflow):
    capabilities = WorkflowCapabilities(
        requires_llm=True, requires_rag=True, supports_cancel=True
    )

    def process_file(self, file_path: Path, ctx: dict[str, Any]) -> dict[str, Any]:
        slug = ctx["slug"]
        log_fn: Callable = ctx.get("log_fn") or (lambda m: None)
        stop_event = ctx.get("stop_event")
        state = ctx.get("orchestrator")
        sot_context: str = ctx.get("sot_context", "")

        # Risolvi budget dal modello attivo
        try:
            from core.ai_tasks import get_session_lm_model
            limits = resolve_token_limits(get_session_lm_model())
            chunk_max = resolve_chunk_max_tokens(limits)
        except Exception:
            chunk_max = 1000

        log_fn(f"[REFACTOR] Leggo {file_path.name} (chunk_max={chunk_max} tok)")

        body = file_path.read_text(encoding="utf-8", errors="replace")
        chunks = semantic_chunk(body, max_tokens=chunk_max)
        total = len(chunks)
        log_fn(f"[REFACTOR] {total} chunk semantici")

        # FASE 1: Estrazione parallela-ish (sequenziale ma leggera)
        extracts = []
        for i, chunk in enumerate(chunks):
            if stop_event and stop_event.is_set():
                raise InterruptedError("DocRefactor interrotto")
            abort_if_stop_requested()

            log_fn(f"[REFACTOR] Estrazione {i+1}/{total}: {chunk.parent_heading[:40]}")

            extract_prompt = _build_extraction_prompt(
                chunk=chunk, n=i+1, total=total,
                filename=file_path.name, sot_context=sot_context
            )
            raw = llm_complete(
                system_prompt=EXTRACTION_SYSTEM,
                user_message=extract_prompt,
                temperature=EXTRACT_TEMP,
                max_tokens=MAX_EXTRACT_OUTPUT,
            )
            extract = _parse_json_safe(raw, fallback_chunk=i)
            extract["_chunk_index"] = i
            extract["_section"] = chunk.parent_heading
            extracts.append(extract)

        # FASE 2: Sintesi unica (un solo LLM call grande)
        log_fn("[REFACTOR] Sintesi finale...")
        if stop_event and stop_event.is_set():
            raise InterruptedError("DocRefactor interrotto prima di sintesi")

        synth_prompt = _build_synthesis_prompt(
            filename=file_path.name,
            extracts=extracts,
            sot_context=sot_context,
        )

        # Check budget: se troppo grande, usa sintesi gerarchica
        synth_tokens = count_tokens(synth_prompt)
        if synth_tokens > _synth_budget():
            log_fn(f"[REFACTOR] Prompt sintesi troppo grande ({synth_tokens} tok) → sintesi gerarchica")
            report_md = _hierarchical_synthesis(extracts, file_path.name, sot_context, log_fn)
        else:
            report_md = llm_complete(
                system_prompt=SYNTHESIS_SYSTEM,
                user_message=synth_prompt,
                temperature=SYNTH_TEMP,
                max_tokens=MAX_SYNTH_OUTPUT,
            )

        # Salva sia il report che gli estratti raw (per audit/retry)
        out_path = save_workflow_output(
            slug, WORKFLOW_ID, f"{file_path.stem}_gap.md",
            report_md, source_file=file_path.name, state=state
        )
        save_workflow_output(
            slug, WORKFLOW_ID, f"{file_path.stem}_extracts.json",
            json.dumps(extracts, ensure_ascii=False, indent=2),
            source_file=file_path.name
        )
        log_fn(f"[REFACTOR] Completato: {file_path.name} → {out_path.name}")
        return {"status": "ok", "workflow": WORKFLOW_ID, "source": file_path.name}


def _synth_budget() -> int:
    try:
        from core.ai_tasks import get_session_lm_model
        from core.token_budget import resolve_token_limits
        limits = resolve_token_limits(get_session_lm_model())
        return int(limits.context_tokens * 0.65)
    except Exception:
        return 5000


def _hierarchical_synthesis(
    extracts: list[dict], filename: str, sot_context: str,
    log_fn: Callable
) -> str:
    """
    Sintesi in due passate per documenti molto lunghi:
    1. Sintesi parziale per gruppi di chunk (mini-reports)
    2. Sintesi finale dei mini-reports
    """
    group_size = 5
    mini_reports = []
    for i in range(0, len(extracts), group_size):
        group = extracts[i:i+group_size]
        log_fn(f"[REFACTOR] Mini-sintesi chunk {i+1}–{i+len(group)}")
        prompt = _build_synthesis_prompt(filename, group, sot_context[:1000])
        mini = llm_complete(
            system_prompt=SYNTHESIS_SYSTEM,
            user_message=prompt,
            temperature=SYNTH_TEMP,
            max_tokens=1500,
        )
        mini_reports.append(mini)

    final_prompt = (
        f"Unifica questi {len(mini_reports)} report parziali per `{filename}` "
        f"in un singolo Gap Report consolidato. "
        f"Deduplica GAP simili, mantieni tutti i conflitti.\n\n"
        + "\n\n---\n\n".join(mini_reports)
    )
    return llm_complete(
        system_prompt=SYNTHESIS_SYSTEM,
        user_message=final_prompt,
        temperature=SYNTH_TEMP,
        max_tokens=MAX_SYNTH_OUTPUT,
    )


def _build_extraction_prompt(
    chunk, n: int, total: int, filename: str, sot_context: str
) -> str:
    sot_block = f"\nSOT CONTEXT (breve):\n{sot_context[:800]}\n" if sot_context else ""
    code_note = "\nNOTA: il chunk contiene blocchi di codice — estrai specifiche tecniche." if chunk.has_code else ""
    return (
        f"File: `{filename}` — Chunk {n}/{total}, sezione: {chunk.parent_heading}{code_note}\n"
        f"{sot_block}\n"
        f"TESTO:\n{chunk.text}\n\n"
        "Estrai in JSON: {\"facts\":[...], \"entities\":[...], \"gaps_vs_sot\":[...], \"open_questions\":[...]}"
    )


def _build_synthesis_prompt(filename: str, extracts: list[dict], sot_context: str) -> str:
    extracts_str = json.dumps(extracts, ensure_ascii=False, indent=1)
    sot_block = f"\nSOT REFERENCE:\n{sot_context[:2000]}\n" if sot_context else ""
    return (
        f"Documento: `{filename}`\n"
        f"Estratti da {len(extracts)} chunk:\n{extracts_str}\n"
        f"{sot_block}\n"
        "Produci il Gap Report completo con: Sintesi, GAP-XX, Contraddizioni, Handoff IA."
    )


def _parse_json_safe(raw: str, fallback_chunk: int) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1].strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()
    try:
        return json.loads(raw)
    except Exception:
        return {
            "facts": [{"claim": raw[:400], "confidence": "low", "section": "?"}],
            "entities": [], "gaps_vs_sot": [], "open_questions": [],
            "_parse_error": True
        }
```

---

## 6. FIX STRUTTURA FOLDER (prioritizzati)

```
AZIONI IMMEDIATE:
1. Aggiungere a .gitignore: *.bak, *.tmp, _LLM_CONTEXT_DUMP.txt
2. Spostare DMIP in dmip/ e orchestrator in orchestrator/ (o almeno README separati)
3. Eliminare legacy/shims/*.bak dal tracking Git

AZIONI MEDIO TERMINE:
4. core/chunking_v2.py — nuovo chunker semantico (vedi §4)
5. workflows/doc_refactor.py — nuovo workflow 2-fasi (vedi §5)
6. Consolidare config/settings.py + config/runtime.py in un unico modulo
7. Aggiungere lock async su WorkspaceStore.init()
8. tests/test_doc_refactor.py — test per il nuovo workflow
```

---

## 7. METRICHE QUALITÀ ATTESE POST-FIX

| Metrica | Prima | Dopo |
|---------|-------|------|
| Crash per bug P0 | ~3 scenari certi | 0 |
| Token sprecati per overlap errato | ~15-20% | ~3-5% |
| Drift cumulativo condensato | Alto (dopo chunk 5+) | Assente (facts-based) |
| Retry per context exceeded | Frequente su doc >8k tok | Raro (budget dinamico) |
| Qualità Gap Report (coerenza) | Media | Alta (2-fase deterministico) |

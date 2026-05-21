---
author: DVAMOCLES
category: GOV
title: Prompt per analisi esterna pipeline (Claude)
---

# Come usare questo file

1. Allega a Claude **solo** la cartella `tools/local_doc_pipeline/` (vedi `EXPORT_FOR_CLAUDE_VALIDATION.md`).
2. Copia-incolla il blocco sotto in un nuovo messaggio.
3. Opzionale: allega 1–2 report di esempio da `02_SESSION_MEMORY/GAP_ANALYSIS_REPORTS/` (non l’intera cartella).

---

## Prompt (copia da qui)

```markdown
# Ruolo

Agisci come **Lead AI Architect + Senior Python Engineer** specializzato in pipeline documentali local-first (RAG, LLM on-prem, state machine fault-tolerant).

Devi analizzare il software **`local_doc_pipeline`** del progetto **DVAMOCLES SWORD™: Material Forge Studio®** e produrre un piano di miglioramenti concreti (fix, refactor, parametri operativi).

---

# Contesto prodotto

DVAMOCLES è un ecosistema di documentazione tecnica per un prodotto software (Material Forge Studio + SIGNUM SENTINEL): materiali PBR, architettura modulare, dataset, governance, UX.

Esiste una documentazione **canonica** in `LAST DOCS/` (tier 1 — Source of Truth recente). I materiali grezzi (chat export, Takeout NotebookLM, raw docs, documentazione obsoleta) non devono essere fusi automaticamente nella SOT: servono solo a trovare **gap** (mancanze e contraddizioni).

---

# Obiettivo della pipeline

Costruire una pipeline **locale**, **non distruttiva**, **riprendibile dopo interruzione**, che:

1. **Ingest** — copia sicura (deduplica MD5) dei file sparsi in `01_RAW_INGEST/`.
2. **Gap Analysis** — confronto 1:1 di ogni documento grezzo vs SOT (`LAST DOCS` only), con output SPEC in `02_SESSION_MEMORY/GAP_ANALYSIS_REPORTS/`.
3. **Resume** — `02_SESSION_MEMORY/pipeline_state.json` traccia `pending | processing | completed` per file e chunk.
4. **Token-aware** — chunking Markdown (`##`), budget ~6k token input, modelli LM Studio 8k context.
5. **RAG** — AnythingLLM workspace `dvamocles_sot_canon` per recuperare contesto SOT senza dumpare tutti i file nel prompt.

**Non è obiettivo** riassumere o refactorizzare automaticamente i grezzi in questa fase: solo **Gap Report** (elenco lacune/contraddizioni).

---

# Stack tecnico

| Componente | Endpoint | Ruolo |
|------------|----------|--------|
| LM Studio | `http://localhost:1234/v1` + `/api/v1/chat` | Inferenza gap (preferire modello **instruct testuale**, non VL) |
| AnythingLLM | `http://localhost:3001/api/v1` | Upload SOT, embeddings, vector-search |
| Python 3.11+ | `cli.py`, `orchestrator.py` | Orchestrazione |

Entry point: `start_dvamocles_pipeline.ps1` / `.bat` → default `--skip-ingest --limit 10` file per run.

---

# Architettura moduli (da rivedere)

| Modulo | Responsabilità |
|--------|----------------|
| `orchestrator.py` | Pre-volo, ingest, avvio gap loop |
| `core/gap_runner.py` | Loop file, chunking, scrittura report |
| `core/ai_tasks.py` | Prompt gap, LLM, discovery modello LM |
| `core/gap_allm.py` | Sync SOT su AnythingLLM, RAG |
| `core/session_state.py` | Resume `pipeline_state.json` |
| `core/ingest_copy.py` | Copia + deduplica MD5 |
| `core/chunking.py` | Split/merge sezioni Markdown |
| `core/token_budget.py` | tiktoken + limiti per modello |
| `core/paths.py` | Layout workspace, SOT tier |
| `settings.py` | SOT tiers, ingest sources, env |

---

# Problemi noti (da validare nel codice)

1. **Modello VL** (`qwen2.5-vl`): HTTP 400 su `/v1/chat/completions` — workaround `LM_USE_NATIVE_CHAT=true`.
2. **Slug AnythingLLM**: mismatch `dvamocles-sot-canon` vs `dvamocles_sot_canon` (risolto con `resolve_sot_workspace_slug`).
3. **Resume chunk**: file segnato `processing` a chunk 126/126 → loop vuoto → falso `completed` (fix reset chunk).
4. **Scala**: ~1434 file in ingest; 10 file/run; tempi lunghi (SOT upload ~4 min, ~8–25 s/chunk LLM).
5. **SOT nel prompt**: anche con RAG, warning “SOT troncato” se indice + RAG superano budget 8k.
6. **Doppio flusso**: `pipeline.py` (extract → Pre_Claude_Refactor) vs `gap_runner` (gap analysis) — possibile confusione.
7. **State file grande**: migliaia di entry in `pipeline_state.json` dopo test.

---

# Frontmatter obbligatorio output gap

Ogni report in `GAP_ANALYSIS_REPORTS/` deve iniziare con:

```yaml
---
author: DVAMOCLES
category: SPEC
source_file: <path in 01_RAW_INGEST>
status: gap_identified
---
```

---

# Cosa ti chiedo (deliverable)

1. **Architecture review** — diagramma flusso, accoppiamenti deboli, moduli da unificare o eliminare.
2. **Bug & edge cases** — lista prioritizzata (P0/P1/P2) con file/riga e fix proposto.
3. **Performance & costi locali** — come ridurre chunk count, evitare re-upload SOT, batch size ottimale per 8k context.
4. **Prompt engineering** — miglioramenti al system prompt gap (precisione, no allucinazioni, formato output).
5. **Configurazione operativa** — valori consigliati `.env` (modello LM, `GAP_BATCH_SIZE`, `LM_USE_NATIVE_CHAT`, tier SOT).
6. **Piano refactor** — max 5 PR atomiche (titolo, file toccati, rischio).
7. **Checklist validazione** — 10 step per verificare che una run da zero sia corretta.

Formato: Markdown strutturato, tabelle dove utile, snippet Python solo se necessari e minimali.

**Vincoli:** non proporre cloud API obbligatorie; mantenere LM Studio + AnythingLLM locali; non modificare i file sorgenti del repo (`LAST DOCS`, `extra files`) — solo il tool in `tools/local_doc_pipeline/`.
```

---

## Dopo la risposta di Claude

Applica i fix P0 in Cursor, poi rilancia:

```powershell
.\start_dvamocles_pipeline.ps1 -Limit 3
```

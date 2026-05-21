# Handoff — solo `Gap_Report_Generale.md`

Allega **solo** questo file a Claude o GPT:

`02_SESSION_MEMORY/GAP_ANALYSIS_REPORTS/Gap_Report_Generale.md`

I file `GAP_*.md` per singolo grezzo sono **disabilitati** di default (`GAP_PER_FILE_REPORTS=false`).

## Cosa contiene il report generale

- Una sezione per ogni file grezzo analizzato: `## File grezzo: path/...`
- Dentro ogni sezione: sintesi, GAP numerati, citazioni, azioni di redazione, handoff
- File lunghi: già **consolidati** (non lista di 20 chunk grezzi)

## Prompt per Claude / GPT

```
Allego Gap_Report_Generale.md (unico registro gap DVAMOCLES).

Aggiorna i documenti in LAST DOCS/ usando le sezioni «Azione di redazione»
e i GAP-XX. Tier 1 (LAST DOCS) vince su documentazione vecchia.

Procedi file per file del report generale; non inventare oltre citazioni e azioni indicate.
```

## AnythingLLM

Workspace SOT con **LAST DOCS** + **Documentazione vecchia** incorporati (`GAP_SOT_LAST_DOCS_ONLY=false`).

## Opzionale: report per-file

Solo per debug:

```env
GAP_PER_FILE_REPORTS=true
```

# AI_DEV_SESSIONS — Meta-sviluppo

Log delle sessioni di coding con IA esterne (Claude, Cursor, ChatGPT, …).

## Uso

1. Duplica `_template_dev_log.md` → `YYYYMMDD_HHMM_<agente>.md`
2. Compila il frontmatter YAML (`target_files` con path relativi alla root repo)
3. Aggiorna il router della mappa:

```powershell
python scripts/update_dev_router.py
```

4. Prima di esportare contesto per un LLM:

```powershell
python scripts/generate_repomix.py
```

Output: `_LLM_CONTEXT_DUMP.txt` (ROOTPAM aggiornato in cima).

## Git

Questa cartella **è tracciata** da Git (non è in `.gitignore`).

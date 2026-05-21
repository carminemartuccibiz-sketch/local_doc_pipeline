# Primo avvio — Local AI Orchestrator

Guida Windows (PowerShell) da zero all’UI su http://127.0.0.1:7842.

---

## 0. Prerequisiti

| Componente | Versione / nota |
|------------|-----------------|
| **Python** | 3.10+ (`python --version`) |
| **LM Studio** | Server locale attivo su `http://localhost:1234` |
| **AnythingLLM** | Solo se usi **gap_analysis** con RAG (`http://localhost:3001`) |
| **Git** | Opzionale (repo già clonato) |

---

## 1. Apri la cartella del progetto

```powershell
cd E:\DVAMOCLES-SWORD-AMBIENT-FULL-DOCUMENTATION\tools\local_doc_pipeline
```

---

## 2. Ambiente virtuale Python

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Se PowerShell blocca gli script:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Poi riattiva il venv.

---

## 3. Installa le dipendenze

```powershell
pip install --upgrade pip
pip install -r requirements.txt
```

Verifica import base:

```powershell
python -c "import flask, httpx, pywebview; print('OK dipendenze')"
```

---

## 4. File `.env` (consigliato)

Crea `local_doc_pipeline\.env` nella root del tool (accanto a `requirements.txt`):

```env
# Repo documentazione (SOT / LAST DOCS)
DVAMOCLES_SOURCE_ROOT=E:\DVAMOCLES-SWORD-AMBIENT-FULL-DOCUMENTATION

# LM Studio (default già ok se server su 1234)
LM_STUDIO_BASE_URL=http://localhost:1234/v1
# LM_STUDIO_MODEL=auto

# AnythingLLM (gap + RAG)
ANYTHINGLLM_BASE_URL=http://localhost:3001
ANYTHINGLLM_API_KEY=la_tua_api_key_da_impostazioni_ANYTHINGLLM

# Profilo leggero GPU (opzionale)
PIPELINE_HARDWARE_PROFILE=i9-2080ti-eco
PIPELINE_MAX_CONCURRENCY=1
```

Copia opzionale da `.env.example` se presente nel repo.

---

## 5. Avvia i servizi esterni

### LM Studio
1. Apri LM Studio → carica un modello **instruct/chat** (non solo embedding).
2. **Developer → Start Server** (porta **1234**).
3. **Max concurrent predictions = 1** (consigliato).

### AnythingLLM (solo per workflow gap con RAG)
1. Avvia AnythingLLM Desktop.
2. **Settings → API Keys** → copia la key in `ANYTHINGLLM_API_KEY`.
3. Workspace SOT: incorpora documenti dall’UI se usi RAG (`ALLM_EMBED_MODE=manual`).

---

## 6. Preflight (controllo connessioni)

Con venv attivo:

```powershell
python legacy\cli.py check
```

Atteso: **LM Studio OK**, **AnythingLLM OK** (se configurato), modello rilevato.

Se fallisce: server non avviato, porta sbagliata, o API key mancante.

---

## 7. Avvio orchestratore UI

### Opzione A — Script automatico (consigliato)

```powershell
.\dvamocles_daemon.ps1
```

Crea/aggiorna `.venv`, installa dipendenze, avvia Flask e apre il browser.

### Opzione B — Manuale

```powershell
.\.venv\Scripts\Activate.ps1
python server.py
```

Apri: **http://127.0.0.1:7842**

### Opzione C — Finestra desktop (PyWebView)

```powershell
python app.py
```

Se PyWebView non è disponibile, usa `python app.py --browser`.

---

## 8. Primo progetto nell’UI

1. **Crea progetto** (nome a piacere, es. `demo`).
2. Copia uno o più file (`.md`, `.pdf`, `.docx`, …) in:
   ```
   projects\<slug>\01_INGEST\
   ```
3. In UI seleziona profilo HW (**eco** / **fast**) se richiesto.
4. Workflow **ingest** (sliding window) o **gap_analysis** (con LM + ALLM pronti).
5. **START** — segui i log nel pannello SSE.
6. **STOP** interrompe; **RESET** prepara un nuovo job dopo lo stop.

Output ingest: `projects\<slug>\01_INGEST\<nome_file>\` (`chunks.json`, `analysis.md`).

Output gap: `projects\<slug>\03_OUTPUT\Gap_Report_Generale.md`.

---

## 9. Log di debug

| File | Contenuto |
|------|-----------|
| `logs/api_interactions.json` | Ultime **5** chiamate HTTP (LM / AnythingLLM) |
| `logs/app_system.log` | Log applicazione (rotazione 5 MB) |

---

## 10. Problemi comuni

| Sintomo | Azione |
|---------|--------|
| `ModuleNotFoundError: flask` | Venv non attivo → `.\.venv\Scripts\Activate.ps1` + `pip install -r requirements.txt` |
| Porta 7842 occupata | Chiudi altro `python server.py` o cambia `UI_PORT` in `config/defaults.py` |
| LM Studio FAIL al check | Avvia server LM Studio, modello caricato |
| Job non parte | File in `01_INGEST/`? LM Studio raggiungibile? |
| Gap senza RAG | Imposta `ANYTHINGLLM_API_KEY` o disabilita RAG in config |

---

## Ordine riassunto

```
venv → pip install → .env → LM Studio (+ ALLM) → cli check → dvamocles_daemon.ps1 → crea progetto → file in 01_INGEST → START
```

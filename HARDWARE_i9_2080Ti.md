# Profilo hardware — i9 + RTX 2080 Ti + 32 GB RAM

Profilo attivo consigliato: **`i9-2080ti-eco`** (basso carico, nessuna fretta).

Pipeline configurata per **stabilità** e **uso GPU moderato**, non throughput.

## LM Studio (obbligatorio)

In **Developer → Server** (o impostazioni server):

| Impostazione | Valore consigliato |
|--------------|-------------------|
| **Max concurrent predictions** | **1** (non 4) |
| Loaded context length | **12288** in modalità ECO (`.env` `LM_NATIVE_CONTEXT`) — meno VRAM |
| GPU offload | **non al massimo**: ~28–32 layer per 12B Q4 se vuoi GPU più “ferma” tra i chunk |

In LM Studio abbassa anche **GPU %** / power limit se la scheda resta troppo calda.

Dopo ogni crash OOM: **Stop Server** → ricarica modello → **Start Server**.

## AnythingLLM

- Embedding SOT: **Salva e incorpora** dall’UI (`ALLM_EMBED_MODE=manual`).
- Non lasciare altre chat pesanti aperte durante la gap run.

## Variabili pipeline (`.env`)

| Variabile | Valore | Significato |
|-----------|--------|-------------|
| `PIPELINE_MAX_CONCURRENCY` | `1` | Una sola richiesta LLM alla volta |
| `PIPELINE_LM_COOLDOWN_S` | **`6`** (eco) | Pausa dopo ogni risposta LM — GPU si raffredda |
| `PIPELINE_CHUNK_COOLDOWN_S` | **`4`** (eco) | Pausa tra chunk |
| `PIPELINE_FILE_PAUSE_S` | **`12`** (eco) | Riposo tra un file e il successivo |
| `GAP_BATCH_SIZE` | `1` | Un file grezzo per esecuzione |
| `GAP_CHUNK_MAX_TOKENS` | **`900`** (eco) | Burst GPU più corti |
| `GAP_LM_MAX_OUTPUT` | **`768`** (eco) | Meno token generati per chiamata |
| `LM_NATIVE_CONTEXT` | **`12288`** (eco) | Meno VRAM occupata dal modello |

## Avvio tipico (loop automatico)

```powershell
cd tools\local_doc_pipeline
python cli.py reset-gap --keep-allm-cache
.\start_dvamocles_pipeline.ps1
```

Default: **continuo** — finito un file ne parte il successivo fino a fine coda (~1400 file).

Solo un file e stop: `.\start_dvamocles_pipeline.ps1 -SingleRun`

## GPU ~88% ma “attività” anche tra i chunk

Normale su 2080 Ti:

- **Durante** la generazione token LM Studio → GPU 80–95%, VRAM ~8/11 GB.
- **Tra** un chunk e l’altro → breve calo GPU (prefill, RAG AnythingLLM su CPU, pause pipeline).
- LM Studio tiene il modello **sempre caricato** in VRAM → Task Manager mostra uso anche a “riposo”.

Profilo **eco** in `.env`: pause lunghe tra chunk e tra file → GPU spesso sotto il 50% tra un file e l’altro.

## Se vuoi accelerare (a tuo rischio)

Solo dopo prove stabili con concorrenza 1:

- `GAP_BATCH_SIZE=3` — più file per run, sempre 1 LLM alla volta
- `PIPELINE_LM_COOLDOWN_S=0.5`
- In LM Studio: max concurrent **2** (non 4 su 11 GB VRAM)

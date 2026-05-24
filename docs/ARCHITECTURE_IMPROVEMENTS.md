# Architecture review — miglioramenti applicati

Riferimento al piano di review (P0–PR-5). Stato implementazione in codice.

| ID | Stato | Note |
|----|--------|------|
| P0-1 | ✅ | `resume_chunk_index` legge `files[rel]["chunks_done"]` |
| P0-2 | ✅ | `parse_lm_native_response` + raise su risposta vuota |
| P0-3 | ✅ | `write_gap_report(replace=False)` non sovrascrive |
| P1-1 | ✅ | `PipelineState.save()` usa `atomic_write_json` |
| P1-2 | ✅ | `_is_sot_file` legge 512 byte + `lru_cache` |
| P1-3 | ✅ | Log esplicito RAG solo su chunk 0 |
| P1-4 | ✅ | `cli run` e orchestrator: default `GAP_BATCH_SIZE` se `--limit` omesso |
| P1-5 | ✅ | `lm_studio_client` delega a `core.ai_tasks.llm_complete` |
| PR-5 | ✅ | `prune_completed(keep_last_n=200)` a fine run |
| P2-4 | ✅ | `gap_allm_state.json` in `02_SESSION_MEMORY/` |
| PR-3 | ✅ | Client LM unificato |
| Legacy `pipeline.py` | ⏳ | Spostamento in `legacy/` pianificato, non ancora fatto |
| force_allm sync delta | ✅ | `list_documents` + skip remoto in `sync_sot_to_anythingllm` (anche `force=True`) |

## Checklist validazione

Vedi sezione 7 del piano originale; dopo P0-1/P0-2 eseguire `python cli.py check` e run su 1 file.

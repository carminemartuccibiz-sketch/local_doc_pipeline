---
session_id: 20260522_1941_dependabot
date: 2026-05-22
agent_used: Cursor
target_files:
  - requirements.txt
status: completed
next_steps: "Procedere con i test dell'interfaccia UI e dell'ingestion."
---

# Note di Sviluppo

## Contesto

Dopo `git pull` da `main`, allineamento dipendenze gestite da **Dependabot** (security updates e version bump). Eventuali conflitti di merge su `requirements.txt` sono stati risoliti; le librerie core sono portate alle versioni minime/stabili indicate nel file unificato.

## Versioni attuali (snapshot post-sync)

| Pacchetto | Vincolo in requirements.txt |
|-----------|------------------------------|
| python-dotenv | >=1.2.2,<2.0 |
| tiktoken | >=0.7,<1.0 |
| tqdm | >=4.67.3,<5.0 |
| httpx | >=0.28.1,<1.0 |
| beautifulsoup4 | >=4.12,<5 |
| pypdf | >=6.12.1 |
| python-docx | >=1.2.0 |
| flask | >=3.1.3,<4.0 |
| flask-cors | >=6.0.2,<7.0 |
| pywebview | >=6.2.1,<7.0 |
| unstructured[pdf,docx] | >=0.18.32 (opzionale) |

## Impatto atteso

- **UI:** Flask 3.1.x + flask-cors 6.x; PyWebView 6.x (verificare build locale con Python 3.10–3.12, non 3.14).
- **Pipeline:** httpx / bs4 / pypdf allineati per ingest e gap.
- **CI:** workflow `.github/workflows/python-app.yml` reinstalla da questo file su ogni push a `main`.

## Test consigliati post-merge

1. `.\scripts\setup_venv.ps1` (o `pip install -r requirements.txt` nel venv attivo)
2. `python legacy\cli.py check`
3. `python server.py` o `.\dvamocles_daemon.ps1` — smoke UI su :7842
4. Job ingest su un file in `projects/<slug>/01_INGEST/`

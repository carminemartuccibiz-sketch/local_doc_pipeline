#!/usr/bin/env python3
"""
DVAMOCLES — Pipeline documentazione locale (AnythingLLM + LM Studio).

Uso:
  python local_doc_pipeline.py --check
  python local_doc_pipeline.py --step all --limit 3
  python local_doc_pipeline.py --step convert
  python local_doc_pipeline.py --step embed
  python local_doc_pipeline.py --step extract
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from anythingllm_client import AnythingLLMClient, AnythingLLMError
from config import DEFAULT_SOURCE_ROOT
from lm_studio_client import LMStudioClient, LMStudioError
from pipeline import run_pipeline


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)-7s] %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def describe_paths() -> str:
    from config import (
        ANYTHINGLLM_BASE_URL,
        LM_MODEL,
        LM_OPENAI_BASE_URL,
        OUTPUT_DIR_NAME,
        STAGING_DIR,
    )

    return f"""
DVAMOCLES local_doc_pipeline
  source_root     : {DEFAULT_SOURCE_ROOT}
  staging         : {STAGING_DIR}
  output          : {{source}}/ {OUTPUT_DIR_NAME}
  AnythingLLM     : {ANYTHINGLLM_BASE_URL}
  LM Studio       : {LM_OPENAI_BASE_URL} (model={LM_MODEL})
"""


def cmd_check() -> int:
    print(describe_paths())
    allm = AnythingLLMClient()
    lm = LMStudioClient()
    ok_allm = allm.health()
    ok_lm = lm.health()
    print(f"AnythingLLM ping: {'OK' if ok_allm else 'FAIL'}")
    print(f"LM Studio:        {'OK' if ok_lm else 'FAIL'}")
    if ok_lm:
        models = lm.list_models()
        if models:
            print(f"  modelli: {', '.join(models[:8])}")
    if not ok_allm:
        print("  → Avvia AnythingLLM e verifica ANYTHINGLLM_BASE_URL (default :3001)")
    if not ok_lm:
        print("  → LM Studio → Developer → Start Server (porta 1234)")
    return 0 if (ok_allm and ok_lm) else 1


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32":
        import asyncio

        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    parser = argparse.ArgumentParser(
        description="Pipeline refactor documentazione DVAMOCLES (AnythingLLM + LM Studio)"
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=DEFAULT_SOURCE_ROOT,
        help="Radice documentazione da scansionare",
    )
    parser.add_argument(
        "--step",
        choices=("convert", "embed", "extract", "all"),
        default="all",
        help="Fase da eseguire (default: all)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max file per fase (test)")
    parser.add_argument("--force", action="store_true", help="Rielabora anche file già in state")
    parser.add_argument("--dry-run", action="store_true", help="Solo elenco file, nessuna scrittura")
    parser.add_argument("--skip-allm", action="store_true", help="Salta upload/RAG AnythingLLM")
    parser.add_argument("--skip-lm", action="store_true", help="Salta estrazione LM Studio")
    parser.add_argument(
        "--include",
        action="append",
        default=None,
        help="Glob aggiuntivo (es. refactor fatto/**/*.md). Ripetibile.",
    )
    parser.add_argument(
        "--priority-only",
        action="store_true",
        help="Solo cartelle prioritarie: refactor fatto, LAST DOCS, Raw docs, extra files, SUITE",
    )
    parser.add_argument("--check", action="store_true", help="Verifica connettività servizi")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)

    if args.check:
        return cmd_check()

    include = list(args.include or [])
    if args.priority_only:
        include.extend(
            [
                "refactor fatto/**/*.md",
                "LAST DOCS/**/*.md",
                "refactoring documentale raw/**/*.md",
                "extra files/**/*.md",
                "Documentazione vecchia/**/*.md",
            ]
        )

    steps = {args.step} if args.step != "all" else {"all"}

    try:
        run_pipeline(
            source_root=args.source_root.resolve(),
            steps=steps,
            limit=args.limit,
            force=args.force,
            include_globs=include if include else None,
            skip_allm=args.skip_allm,
            skip_lm=args.skip_lm,
            dry_run=args.dry_run,
        )
    except (AnythingLLMError, LMStudioError) as e:
        logging.error("%s", e)
        return 2
    except KeyboardInterrupt:
        logging.warning("Interrotto dall'utente — stato salvato in pipeline_state.json")
        return 130

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

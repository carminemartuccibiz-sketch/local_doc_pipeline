#!/usr/bin/env python3
"""Shim entry point — delega a legacy.local_doc_pipeline."""
from legacy.local_doc_pipeline import main

if __name__ == "__main__":
    raise SystemExit(main())

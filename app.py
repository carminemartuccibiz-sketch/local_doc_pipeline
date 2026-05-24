#!/usr/bin/env python3
"""
Entry point Local AI Orchestrator — PyWebView + Flask (Fase 8).
Uso:
  python app.py              # finestra desktop
  python app.py --browser    # solo server (come server.py)
"""
from __future__ import annotations

import argparse
import sys
import threading
import time


def _start_flask() -> None:
    from config import UI_PORT
    from engine.http_serve import serve_flask_app
    from server import app as flask_app

    serve_flask_app(flask_app, host="127.0.0.1", port=UI_PORT)


def main() -> int:
    parser = argparse.ArgumentParser(description="Local AI Orchestrator")
    parser.add_argument(
        "--browser",
        action="store_true",
        help="Avvia solo Flask (apri il browser manualmente)",
    )
    args = parser.parse_args()

    if args.browser:
        _start_flask()
        return 0

    try:
        import webview
    except ImportError:
        print("pywebview non installato — uso modalità browser.", file=sys.stderr)
        print("  pip install pywebview", file=sys.stderr)
        _start_flask()
        return 0

    from config import UI_PORT

    t = threading.Thread(target=_start_flask, daemon=True)
    t.start()
    time.sleep(0.8)

    webview.create_window(
        "Local AI Orchestrator",
        f"http://127.0.0.1:{UI_PORT}",
        width=1280,
        height=800,
        min_size=(900, 600),
    )
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

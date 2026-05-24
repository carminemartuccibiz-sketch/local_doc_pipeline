"""Avvio server UI — Waitress su Windows se disponibile (audit GPT §1.4)."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def serve_flask_app(app, *, host: str = "127.0.0.1", port: int = 7842) -> None:
    try:
        from waitress import serve

        logger.info("Waitress — http://%s:%s", host, port)
        serve(app, host=host, port=port, threads=8)
    except ImportError:
        logger.info(
            "Waitress non installato — Flask dev server (pip install waitress consigliato)"
        )
        app.run(
            host=host,
            port=port,
            debug=False,
            use_reloader=False,
            threaded=True,
        )

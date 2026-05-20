"""
Scrittura atomica file JSON (Windows-safe, retry su PermissionError).
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def atomic_write_json(
    path: Path,
    data: dict[str, Any] | list[Any],
    *,
    retries: int = 8,
    pause_s: float = 0.15,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_name(path.name + ".tmp")

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            tmp.write_text(content, encoding="utf-8", newline="\n")
            if path.exists():
                try:
                    os.chmod(path, 0o666)
                except OSError:
                    pass
                try:
                    path.unlink()
                except OSError:
                    pass
            os.replace(str(tmp), str(path))
            return
        except (PermissionError, OSError) as e:
            last_err = e
            time.sleep(pause_s * (attempt + 1))
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

    logger.warning(
        "Scrittura atomica fallita dopo %d tentativi, fallback diretto: %s",
        retries,
        path,
    )
    try:
        path.write_text(content, encoding="utf-8", newline="\n")
    except OSError as e:
        raise OSError(f"Impossibile scrivere {path}: {last_err or e}") from e
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass

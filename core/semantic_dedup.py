"""MinHash chunk dedup — delegates to core.dedup (Task B1)."""
from __future__ import annotations

from typing import TypeVar

from core.dedup import minhash_dedup as _minhash_dedup

T = TypeVar("T")


def minhash_dedup(chunks: list[T]) -> list[T]:
    return _minhash_dedup(chunks)

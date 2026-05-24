"""
MinHash / Jaccard deduplication for ingest documents and semantic chunks (Task B1).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Protocol

logger = logging.getLogger(__name__)

DEFAULT_NUM_PERM = int(os.environ.get("INGEST_MINHASH_PERM", "128"))
DEFAULT_SHINGLE_SIZE = int(os.environ.get("INGEST_MINHASH_SHINGLE", "5"))
DEFAULT_SIMILARITY = float(os.environ.get("INGEST_MINHASH_SIMILARITY", "0.95"))
DEFAULT_MAX_TEXT_CHARS = int(os.environ.get("INGEST_DEDUP_MAX_CHARS", "500000"))


class _HasText(Protocol):
    text: str


def similarity_threshold() -> float:
    try:
        return float(os.environ.get("INGEST_MINHASH_SIMILARITY", "0.95"))
    except (TypeError, ValueError):
        return DEFAULT_SIMILARITY


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().strip())


def text_shingles(text: str, *, k: int | None = None) -> set[str]:
    k = k or DEFAULT_SHINGLE_SIZE
    norm = _normalize_text(text)
    if not norm:
        return set()
    if len(norm) <= k:
        return {norm}
    return {norm[i : i + k] for i in range(len(norm) - k + 1)}


def minhash_signature(
    text: str,
    *,
    num_perm: int | None = None,
    shingle_k: int | None = None,
) -> list[int]:
    """MinHash signature for Jaccard estimation on character shingles."""
    num_perm = num_perm or DEFAULT_NUM_PERM
    shingles = text_shingles(text, k=shingle_k)
    if not shingles:
        return [0] * num_perm

    sig = [2**32 - 1] * num_perm
    for shingle in shingles:
        base = int(hashlib.md5(shingle.encode("utf-8", errors="replace")).hexdigest(), 16)
        for i in range(num_perm):
            hv = int(
                hashlib.sha1(f"{base}:{i}".encode()).hexdigest()[:8],
                16,
            )
            if hv < sig[i]:
                sig[i] = hv
    return sig


def jaccard_from_minhash(a: list[int], b: list[int]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    matches = sum(1 for x, y in zip(a, b) if x == y)
    return matches / len(a)


@dataclass(slots=True)
class DedupMatch:
    name: str
    md5: str
    similarity: float
    exact: bool


def word_shingles(text: str, *, k: int = 3) -> set[str]:
    words = _normalize_text(text).split()
    if not words:
        return set()
    if len(words) <= k:
        return {" ".join(words)}
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}


def shingle_jaccard(a: str, b: str, *, word_level: bool = True, k: int = 3) -> float:
    """Jaccard esatto su shingles (word-level default per documenti)."""
    if word_level:
        sa = word_shingles(a, k=k)
        sb = word_shingles(b, k=k)
    else:
        sa = text_shingles(a, k=k)
        sb = text_shingles(b, k=k)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def shingle_overlap(a: str, b: str, *, word_level: bool = False, k: int | None = None) -> float:
    """Overlap coefficient: |A∩B| / min(|A|, |B|) — robusto per doc quasi-contenuti."""
    k = k or (3 if word_level else DEFAULT_SHINGLE_SIZE)
    if word_level:
        sa = word_shingles(a, k=k)
        sb = word_shingles(b, k=k)
    else:
        sa = text_shingles(a, k=k)
        sb = text_shingles(b, k=k)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / min(len(sa), len(sb))


def text_similarity(a: str, b: str) -> float:
    """
    Similarità 0–1: match esatto, poi max tra MinHash, Jaccard char, overlap e SequenceMatcher.
    """
    na = _normalize_text(a)
    nb = _normalize_text(b)
    if not na and not nb:
        return 1.0
    if na == nb:
        return 1.0

    mh = jaccard_from_minhash(minhash_signature(a), minhash_signature(b))
    char_j = shingle_jaccard(a, b, word_level=False, k=DEFAULT_SHINGLE_SIZE)
    overlap = shingle_overlap(a, b, word_level=False, k=DEFAULT_SHINGLE_SIZE)
    seq = SequenceMatcher(None, na, nb).ratio()
    return max(mh, char_j, overlap, seq)


def find_similar_entry(
    signature: list[int],
    entries: list[dict[str, Any]],
    *,
    threshold: float | None = None,
    candidate_text: str | None = None,
    resolve_text: Callable[[str], str] | None = None,
) -> DedupMatch | None:
    """Return best manifest entry with similarity >= threshold."""
    threshold = threshold if threshold is not None else similarity_threshold()
    best: DedupMatch | None = None

    for ent in entries:
        prev_sig = ent.get("signature")
        if not isinstance(prev_sig, list) or not prev_sig:
            continue
        name = str(ent.get("name") or "?")

        if candidate_text and resolve_text:
            other_text = resolve_text(name)
            sim = text_similarity(candidate_text, other_text)
        else:
            try:
                prev_ints = [int(x) for x in prev_sig]
            except (TypeError, ValueError):
                continue
            sim = jaccard_from_minhash(signature, prev_ints)

        if sim >= threshold and (best is None or sim > best.similarity):
            best = DedupMatch(
                name=name,
                md5=str(ent.get("md5") or ""),
                similarity=sim,
                exact=sim >= 0.999,
            )
    return best


def read_text_for_dedup(path: Path, *, max_chars: int | None = None) -> str:
    cap = max_chars or DEFAULT_MAX_TEXT_CHARS
    try:
        from core.converters import extract_plain

        return extract_plain(path)[:cap]
    except Exception:
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:cap]
        except OSError as e:
            logger.debug("Dedup: impossibile leggere %s: %s", path.name, e)
            return ""


def signature_for_file(path: Path) -> list[int]:
    body = read_text_for_dedup(path)
    if not body.strip():
        return minhash_signature(path.name)
    return minhash_signature(body)


def minhash_dedup(chunks: list[_HasText]) -> list[_HasText]:
    """Remove near-duplicate chunks (>= similarity threshold) before LLM."""
    if len(chunks) <= 1:
        return chunks

    threshold = similarity_threshold()
    kept: list[_HasText] = []
    texts: list[str] = []

    for chunk in chunks:
        duplicate = False
        for prev in texts:
            if text_similarity(chunk.text, prev) >= threshold:
                duplicate = True
                logger.debug(
                    "Chunk %s skip: similarità >= %.0f%%",
                    getattr(chunk, "index", "?"),
                    threshold * 100,
                )
                break
        if duplicate:
            continue
        texts.append(chunk.text)
        kept.append(chunk)

    return kept

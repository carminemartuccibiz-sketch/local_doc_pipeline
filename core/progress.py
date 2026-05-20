"""
Barre di progresso terminale (tqdm) con fallback ASCII.
"""
from __future__ import annotations

import sys
from typing import Any, Iterable, Iterator, TypeVar

T = TypeVar("T")


class _FallbackProgress:
    def __init__(self, iterable: Iterable[T], *, desc: str, total: int | None) -> None:
        self._iter = iter(iterable)
        self.desc = desc
        self.total = total
        self.n = 0
        self._width = 40

    def __iter__(self) -> Iterator[T]:
        if self.total:
            print(f"\n{self.desc} (0/{self.total})")
        for item in self._iter:
            self.n += 1
            if self.total and (self.n == 1 or self.n % max(1, self.total // 50) == 0 or self.n == self.total):
                self._print_bar()
            yield item
        if self.total:
            self._print_bar(final=True)
            print()

    def _print_bar(self, final: bool = False) -> None:
        if not self.total:
            return
        pct = min(1.0, self.n / self.total)
        filled = int(self._width * pct)
        bar = "#" * filled + "-" * (self._width - filled)
        line = f"\r{self.desc} [{bar}] {self.n}/{self.total} ({pct * 100:.1f}%)"
        sys.stdout.write(line)
        sys.stdout.flush()
        if final:
            sys.stdout.write("\n")

    def set_postfix_str(self, s: str, refresh: bool = True) -> None:
        if refresh and self.total:
            short = (s[:55] + "...") if len(s) > 58 else s
            sys.stdout.write(f"\n  -> {short}\n")
            sys.stdout.flush()

    def update(self, n: int = 1) -> None:
        self.n += n
        self._print_bar()

    def close(self) -> None:
        if self.total:
            self._print_bar(final=True)


def progress_bar(
    iterable: Iterable[T],
    *,
    desc: str = "",
    total: int | None = None,
    unit: str = "it",
    leave: bool = True,
) -> Any:
    try:
        from tqdm import tqdm

        return tqdm(
            iterable,
            desc=desc,
            total=total,
            unit=unit,
            leave=leave,
            dynamic_ncols=True,
            bar_format="{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
        )
    except ImportError:
        if total is None and hasattr(iterable, "__len__"):
            total = len(iterable)  # type: ignore[arg-type]
        return _FallbackProgress(iterable, desc=desc, total=total)

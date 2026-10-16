"""Traps: deliberate data edge cases that expose conversion bugs.

Each trap lives in its own module and exposes ``NAME`` and ``apply(ctx) -> list[TrapRecord]``.
A trap draws from its own RNG stream, so turning one trap off leaves every other trap's rows
unchanged; the classifier relies on this to attribute a mismatch to a single trap.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass

from semround.fixtures.data import Table, TrapRecord
from semround.fixtures.traps import month_end, null_dimension, null_measure, orphan_fk, weekend_dates


@dataclass
class TrapContext:
    table: Table
    rng: random.Random
    foreign_keys: list[list[str]]  # from_columns of each relationship where this table is the many side
    is_time: dict[str, bool]

    def pick(self, share: float, minimum: int = 1) -> list[int]:
        n = len(self.table.rows)
        if n == 0:
            return []
        count = min(n, max(minimum, round(n * share)))
        return sorted(self.rng.sample(range(n), count))

    def plain_columns(self) -> list[str]:
        return [c for c in self.table.columns if c not in self.table.key_columns]


TrapFn = Callable[[TrapContext], list[TrapRecord]]

# Canonical order: attribution reports the first trap that explains a mismatch.
ALL_TRAPS: dict[str, TrapFn] = {
    m.NAME: m.apply for m in (orphan_fk, null_measure, null_dimension, weekend_dates, month_end)
}
DEFAULT_TRAPS: tuple[str, ...] = tuple(ALL_TRAPS)

__all__ = ["ALL_TRAPS", "DEFAULT_TRAPS", "TrapContext", "TrapFn"]

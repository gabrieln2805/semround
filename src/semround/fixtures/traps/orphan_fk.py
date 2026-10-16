"""Child rows whose foreign key matches no parent: separates inner from left joins."""

from __future__ import annotations

from typing import TYPE_CHECKING

from semround.fixtures.data import TrapRecord
from semround.fixtures.values import orphan_value

if TYPE_CHECKING:
    from semround.fixtures.traps import TrapContext

NAME = "orphan_fk"
SHARE = 0.05


def apply(ctx: TrapContext) -> list[TrapRecord]:
    records = []
    table = ctx.table
    for fk in ctx.foreign_keys:
        rows = ctx.pick(SHARE)
        for n, r in enumerate(rows):
            for c in fk:
                table.rows[r][table.col(c)] = orphan_value(table.types[c], n)
        records.append(TrapRecord(NAME, table.dataset, tuple(fk), tuple(rows)))
    return records

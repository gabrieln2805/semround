"""NULLs in numeric columns: SUM of all-NULL groups, AVG denominators, COALESCE rewrites."""

from __future__ import annotations

from typing import TYPE_CHECKING

from semround.fixtures.data import TrapRecord
from semround.model.types import NUMERIC

if TYPE_CHECKING:
    from semround.fixtures.traps import TrapContext

NAME = "null_measure"
SHARE = 0.05


def apply(ctx: TrapContext) -> list[TrapRecord]:
    records = []
    table = ctx.table
    for c in ctx.plain_columns():
        if table.types[c].kind not in NUMERIC or ctx.is_time.get(c, False):
            continue
        rows = ctx.pick(SHARE)
        for r in rows:
            table.rows[r][table.col(c)] = None
        records.append(TrapRecord(NAME, table.dataset, (c,), tuple(rows)))
    return records

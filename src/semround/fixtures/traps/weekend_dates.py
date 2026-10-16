"""Saturday and Sunday dates: where Sunday-start weeks and ISO (Monday) weeks disagree."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from semround.fixtures.data import TrapRecord
from semround.model.types import Kind

if TYPE_CHECKING:
    from semround.fixtures.traps import TrapContext

NAME = "weekend_dates"
SHARE = 0.08
FIRST_SATURDAY = dt.date(2024, 1, 6)


def apply(ctx: TrapContext) -> list[TrapRecord]:
    records = []
    table = ctx.table
    for c in ctx.plain_columns():
        kind = table.types[c].kind
        if kind not in (Kind.DATE, Kind.TIMESTAMP):
            continue
        rows = ctx.pick(SHARE, minimum=2)
        for n, r in enumerate(rows):
            # Alternate Saturday and Sunday so both always appear, even in small tables.
            day = FIRST_SATURDAY + dt.timedelta(weeks=ctx.rng.randrange(100), days=n % 2)
            value: dt.date = day if kind is Kind.DATE else dt.datetime.combine(day, dt.time(12, 0))
            table.rows[r][table.col(c)] = value
        records.append(TrapRecord(NAME, table.dataset, (c,), tuple(rows)))
    return records

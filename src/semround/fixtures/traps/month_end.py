"""Last day of the month, late in the day: catches off-by-one truncation and timezone shifts."""

from __future__ import annotations

import calendar
import datetime as dt
from typing import TYPE_CHECKING

from semround.fixtures.data import TrapRecord
from semround.model.types import Kind

if TYPE_CHECKING:
    from semround.fixtures.traps import TrapContext

NAME = "month_end"
SHARE = 0.08

# Weekday month ends only: weekend dates belong to the weekend_dates trap, and keeping traps
# disjoint lets the classifier attribute a mismatch to exactly one of them.
MONTH_ENDS = [
    d for y in (2024, 2025) for m in range(1, 13) if (d := dt.date(y, m, calendar.monthrange(y, m)[1])).weekday() < 5
]


def apply(ctx: TrapContext) -> list[TrapRecord]:
    records = []
    table = ctx.table
    for c in ctx.plain_columns():
        kind = table.types[c].kind
        if kind not in (Kind.DATE, Kind.TIMESTAMP):
            continue
        rows = ctx.pick(SHARE)
        for r in rows:
            day = MONTH_ENDS[ctx.rng.randrange(len(MONTH_ENDS))]
            value: dt.date = day if kind is Kind.DATE else dt.datetime.combine(day, dt.time(23, 30))
            table.rows[r][table.col(c)] = value
        records.append(TrapRecord(NAME, table.dataset, (c,), tuple(rows)))
    return records

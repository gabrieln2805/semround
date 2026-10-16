"""Value factories per logical type. Every value is a plain Python object DuckDB can bind."""

from __future__ import annotations

import datetime as dt
import random
import re
import zlib
from decimal import Decimal
from typing import Any

from semround.model.types import ColumnType, Kind

BASE_DATE = dt.date(2024, 1, 1)
DATE_SPAN_DAYS = 731  # 2024-01-01 .. 2025-12-31, includes 29 Feb 2024
ORPHAN_INT_BASE = 900_000

_SCALE = re.compile(r"decimal\s*\(\s*\d+\s*,\s*(\d+)\s*\)", re.I)


def decimal_scale(ct: ColumnType) -> int:
    m = _SCALE.match(ct.sql_type)
    return int(m.group(1)) if m else 2


def cardinality(column: str) -> int:
    """Small, column-stable number of distinct categorical values (4..10)."""
    return 4 + zlib.crc32(column.encode()) % 7


def regular_value(column: str, ct: ColumnType, rng: random.Random, is_time: bool) -> Any:
    k = ct.kind
    if k is Kind.INTEGER:
        if is_time and re.search(r"year", column):
            return rng.randint(2023, 2026)
        if is_time and re.search(r"moy|month", column):
            return rng.randint(1, 12)
        return rng.randint(0, 100)
    if k is Kind.DECIMAL:
        scale = decimal_scale(ct)
        return Decimal(rng.randint(0, 100_000)).scaleb(-2).quantize(Decimal(1).scaleb(-scale))
    if k is Kind.FLOAT:
        return round(rng.uniform(0, 1000), 6)
    if k is Kind.BOOLEAN:
        return rng.random() < 0.5
    if k is Kind.DATE:
        return _plain_day(rng)
    if k is Kind.TIMESTAMP:
        return dt.datetime.combine(_plain_day(rng), dt.time(9)) + dt.timedelta(seconds=rng.randrange(8 * 3600))
    if k is Kind.TIME:
        return (dt.datetime.min + dt.timedelta(seconds=rng.randrange(86_400))).time()
    return f"{column}_{rng.randrange(cardinality(column))}"


def _plain_day(rng: random.Random) -> dt.date:
    """A weekday between the 4th and 27th of a month in 2024-2025.

    Base data stays clear of edge cases (weekends, month ends, late hours) so that each edge
    case comes only from its trap; turning the trap off then removes it entirely, which is
    what trap attribution relies on.
    """
    month = rng.randrange(24)
    day = dt.date(2024 + month // 12, month % 12 + 1, rng.randint(4, 27))
    if day.weekday() >= 5:
        day -= dt.timedelta(days=day.weekday() - 4)  # Saturday/Sunday -> Friday
    return day


def key_value(dataset: str, column: str, ct: ColumnType, ordinal: int) -> Any:
    """Unique value for row ``ordinal`` (0-based) of a key column; sorts in ordinal order."""
    k = ct.kind
    if k in (Kind.INTEGER, Kind.DECIMAL, Kind.FLOAT):
        return ordinal + 1
    if k is Kind.DATE:
        return BASE_DATE + dt.timedelta(days=ordinal)
    if k is Kind.TIMESTAMP:
        return dt.datetime.combine(BASE_DATE, dt.time()) + dt.timedelta(hours=ordinal)
    return f"{dataset}_{ordinal + 1:06d}"


def orphan_value(ct: ColumnType, n: int) -> Any:
    """A key value guaranteed not to exist in any parent table."""
    k = ct.kind
    if k in (Kind.INTEGER, Kind.DECIMAL, Kind.FLOAT):
        return ORPHAN_INT_BASE + n
    if k is Kind.DATE:
        return dt.date(1900, 1, 1) + dt.timedelta(days=n)
    if k is Kind.TIMESTAMP:
        return dt.datetime(1900, 1, 1) + dt.timedelta(hours=n)
    return f"orphan_{n:06d}"

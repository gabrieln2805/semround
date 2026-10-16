"""Compare a target's result with the reference result for one query.

Results are normalised first (number types, dates vs midnight timestamps) and joined on the
dimension tuple. The last column is the metric value; the others are dimensions in order.
"""

from __future__ import annotations

import datetime as dt
import math
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from semround.engine.duck import Result


@dataclass(frozen=True)
class Tolerance:
    relative: float = 1e-9
    absolute: float = 1e-6


@dataclass
class Comparison:
    shape_error: str | None = None
    missing: list[tuple] = field(default_factory=list)  # groups only in the reference
    extra: list[tuple] = field(default_factory=list)  # groups only in the target
    duplicates: list[tuple] = field(default_factory=list)  # groups the target returned twice
    mismatched: list[tuple[tuple, Any, Any]] = field(default_factory=list)  # (group, reference, target)

    @property
    def ok(self) -> bool:
        return not (self.shape_error or self.missing or self.extra or self.duplicates or self.mismatched)


def norm_dim(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, dt.datetime):
        if v.tzinfo is not None:
            v = v.astimezone(dt.UTC).replace(tzinfo=None)
        return v.date().isoformat() if v.time() == dt.time() else v.isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, (int, float, Decimal)):
        f = float(v)
        return int(f) if f.is_integer() else repr(f)
    return str(v)


def norm_value(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float, Decimal)):
        return float(v)
    return norm_dim(v)


def values_equal(a: Any, b: Any, tol: Tolerance) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) or math.isnan(b):
            return math.isnan(a) and math.isnan(b)
        if math.isinf(a) or math.isinf(b):
            return a == b
        return abs(a - b) <= max(tol.absolute, tol.relative * max(abs(a), abs(b)))
    return a == b


def _index(result: Result) -> tuple[dict[tuple, Any], list[tuple]]:
    rows: dict[tuple, Any] = {}
    counts = Counter()
    for row in result.rows:
        key = tuple(norm_dim(v) for v in row[:-1])
        counts[key] += 1
        rows[key] = norm_value(row[-1])
    return rows, sorted((k for k, n in counts.items() if n > 1), key=_sort_key)


def _sort_key(group: tuple) -> tuple:
    return tuple((v is not None, str(v)) for v in group)


def compare(reference: Result, target: Result, tol: Tolerance = Tolerance()) -> Comparison:
    if len(reference.columns) != len(target.columns):
        return Comparison(
            shape_error=f"expected {len(reference.columns)} columns, target returned {len(target.columns)}"
        )
    ref, _ = _index(reference)
    tgt, dups = _index(target)
    out = Comparison(duplicates=dups)
    for key in sorted(ref, key=_sort_key):
        if key not in tgt:
            out.missing.append(key)
        elif not values_equal(ref[key], tgt[key], tol):
            out.mismatched.append((key, ref[key], tgt[key]))
    out.extra = sorted((k for k in tgt if k not in ref), key=_sort_key)
    return out

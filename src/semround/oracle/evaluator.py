"""Independent row-by-row evaluator: the second opinion on the reference compiler (D3).

It deliberately shares no code with ``semround.compiler``: it walks Python rows, follows
relationships with dictionaries and evaluates expressions itself. Only the model loader and
the sqlglot parse tree are shared. Used by the oracle-agreement tests and ``--verify``.

Semantics mirror the reference contract, with DuckDB's arithmetic:
- ``/`` is floating-point division; x/0 is +-inf and 0/0 is NaN.
- Each aggregate counts each row of its dataset at most once per group.
- Unmatched joins give NULL dimension values (left-join semantics).
"""

from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict, deque
from decimal import Decimal
from functools import partial
from itertools import product
from typing import Any

import sqlglot.expressions as exp

from semround.compiler.query import DimRef, Query
from semround.fixtures.data import FixtureSet
from semround.model.loader import LoadedModel, parse_sql


class OracleUnsupported(Exception):
    pass


Row = dict[str, Any]
_MISSING = object()


# ---------------------------------------------------------------- expressions


def _num(v: Any) -> Any:
    return float(v) if isinstance(v, float) else v


def _arith(op: type, a: Any, b: Any) -> Any:
    if a is None or b is None:
        return None
    if op is exp.Div:
        fa, fb = float(a), float(b)
        if fb == 0:
            return math.nan if fa == 0 or math.isnan(fa) else math.copysign(math.inf, fa)
        return fa / fb
    if isinstance(a, float) or isinstance(b, float):
        a, b = float(a), float(b)
    elif isinstance(a, Decimal) or isinstance(b, Decimal):
        a, b = Decimal(a), Decimal(b)
    if op is exp.Add:
        return a + b
    if op is exp.Sub:
        return a - b
    if op is exp.Mul:
        return a * b
    if op is exp.Mod:
        if b == 0:
            return None
        return Decimal(a) % Decimal(b) if not isinstance(a, float) else math.fmod(a, b)
    raise OracleUnsupported(op.__name__)


_CMP = {
    exp.EQ: lambda a, b: a == b,
    exp.NEQ: lambda a, b: a != b,
    exp.GT: lambda a, b: a > b,
    exp.GTE: lambda a, b: a >= b,
    exp.LT: lambda a, b: a < b,
    exp.LTE: lambda a, b: a <= b,
}


def _truthy(v: Any) -> bool:
    return v is True


def scalar(node: exp.Expression, lookup) -> Any:
    """Evaluate a scalar expression; ``lookup(column_node)`` returns a column's value."""
    if isinstance(node, exp.Column):
        return lookup(node)
    if isinstance(node, exp.Literal):
        if node.is_string:
            return node.this
        text = node.this
        return Decimal(text) if any(c in text for c in ".eE") else int(text)
    if isinstance(node, exp.Null):
        return None
    if isinstance(node, exp.Boolean):
        return bool(node.this)
    if isinstance(node, exp.Paren):
        return scalar(node.this, lookup)
    if isinstance(node, exp.Neg):
        v = scalar(node.this, lookup)
        return None if v is None else -v
    if isinstance(node, (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod)):
        return _arith(type(node), scalar(node.left, lookup), scalar(node.right, lookup))
    if type(node) in _CMP:
        a, b = scalar(node.left, lookup), scalar(node.right, lookup)
        return None if a is None or b is None else _CMP[type(node)](a, b)
    if isinstance(node, exp.And):
        a, b = scalar(node.left, lookup), scalar(node.right, lookup)
        if a is False or b is False:
            return False
        return None if a is None or b is None else True
    if isinstance(node, exp.Or):
        a, b = scalar(node.left, lookup), scalar(node.right, lookup)
        if a is True or b is True:
            return True
        return None if a is None or b is None else False
    if isinstance(node, exp.Not):
        v = scalar(node.this, lookup)
        return None if v is None else not v
    if isinstance(node, exp.Is):
        v = scalar(node.this, lookup)
        if isinstance(node.expression, exp.Null):
            return v is None
        raise OracleUnsupported("IS with non-NULL")
    if isinstance(node, exp.Case):
        if node.this is not None:
            raise OracleUnsupported("simple CASE")
        for branch in node.args.get("ifs") or []:
            if _truthy(scalar(branch.this, lookup)):
                return scalar(branch.args["true"], lookup)
        default = node.args.get("default")
        return scalar(default, lookup) if default is not None else None
    if isinstance(node, exp.If):
        if _truthy(scalar(node.this, lookup)):
            return scalar(node.args["true"], lookup)
        f = node.args.get("false")
        return scalar(f, lookup) if f is not None else None
    if isinstance(node, exp.Coalesce):
        for arg in [node.this, *node.expressions]:
            v = scalar(arg, lookup)
            if v is not None:
                return v
        return None
    if isinstance(node, exp.Nullif):
        a, b = scalar(node.this, lookup), scalar(node.expression, lookup)
        return None if a is not None and b is not None and a == b else a
    if isinstance(node, exp.DPipe):
        a, b = scalar(node.left, lookup), scalar(node.right, lookup)
        return None if a is None or b is None else f"{a}{b}"
    if isinstance(node, exp.Lower):
        v = scalar(node.this, lookup)
        return None if v is None else str(v).lower()
    if isinstance(node, exp.Upper):
        v = scalar(node.this, lookup)
        return None if v is None else str(v).upper()
    raise OracleUnsupported(f"expression {type(node).__name__}: {node.sql()}")


# ---------------------------------------------------------------- aggregates


def _aggregate(node: exp.Expression, rows: list[Row], lookup_for) -> Any:
    arg = node.this
    distinct = isinstance(arg, exp.Distinct)
    if distinct:
        if len(arg.expressions) != 1:
            raise OracleUnsupported("multi-column DISTINCT")
        arg = arg.expressions[0]
    if isinstance(node, exp.Count) and isinstance(arg, exp.Star):
        return len(rows)
    values = [scalar(arg, lookup_for(r)) for r in rows]
    values = [v for v in values if v is not None]
    if distinct:
        values = list(dict.fromkeys(values))
    if isinstance(node, exp.Count):
        return len(values)
    if not values:
        return None
    if isinstance(node, exp.Sum):
        if any(isinstance(v, float) for v in values):
            return math.fsum(float(v) for v in values)
        return sum(values[1:], values[0])
    if isinstance(node, exp.Avg):
        return math.fsum(float(v) for v in values) / len(values)
    if isinstance(node, exp.Min):
        return min(values)
    if isinstance(node, exp.Max):
        return max(values)
    raise OracleUnsupported(f"aggregate {type(node).__name__}")


# ---------------------------------------------------------------- joins


def _truncate(value: Any, grain: str | None) -> Any:
    if grain is None or value is None:
        return value
    d = value.date() if isinstance(value, dt.datetime) else value
    if grain == "day":
        out = d
    elif grain == "week":
        out = d - dt.timedelta(days=d.weekday())  # ISO weeks start on Monday
    elif grain == "month":
        out = d.replace(day=1)
    elif grain == "quarter":
        out = d.replace(month=3 * ((d.month - 1) // 3) + 1, day=1)
    elif grain == "year":
        out = d.replace(month=1, day=1)
    else:
        raise OracleUnsupported(grain)
    return dt.datetime.combine(out, dt.time())


class Evaluator:
    def __init__(self, loaded: LoadedModel, fixtures: FixtureSet) -> None:
        self.loaded = loaded
        self.model = loaded.model
        self.rows: dict[str, list[Row]] = {
            t.dataset: [dict(zip(t.columns, r, strict=True)) for r in t.rows] for t in fixtures.tables
        }
        self._index: dict[tuple[str, tuple[str, ...]], dict[tuple, list[Row]]] = {}

    def _keys(self, ds: str, names: list[str]) -> tuple[str, ...]:
        out = []
        for n in names:
            parsed = self.loaded.field_sql[ds].get(n.lower())
            out.append(parsed.name.lower() if isinstance(parsed, exp.Column) else n.lower())
        return tuple(out)

    def _lookup(self, ds: str, cols: tuple[str, ...], key: tuple) -> list[Row]:
        idx = self._index.get((ds, cols))
        if idx is None:
            idx = defaultdict(list)
            for r in self.rows[ds]:
                k = tuple(r[c] for c in cols)
                if None not in k:
                    idx[k].append(r)
            self._index[(ds, cols)] = idx
        return idx.get(key, [])

    def _path(self, start: str, goal: str) -> list[tuple[str, str, Any]]:
        """Steps (from_ds, to_ds, relationship); many-to-one-only paths preferred."""
        if start == goal:
            return []
        for forward_only in (True, False):
            seen = {start: []}
            todo = deque([start])
            while todo:
                cur = todo.popleft()
                for r in self.model.relationships:
                    f, t = self.model.dataset(r.from_).name, self.model.dataset(r.to).name
                    moves = [(f, t)] + ([] if forward_only else [(t, f)])
                    for a, b in moves:
                        if a == cur and b not in seen:
                            seen[b] = seen[cur] + [(a, b, r)]
                            todo.append(b)
            if goal in seen:
                return seen[goal]
        raise OracleUnsupported(f"no path {start} -> {goal}")

    def _reach(self, row: Row | None, steps) -> list[Row | None]:
        """All rows of the path's end reachable from ``row`` (left-join: [None] if none)."""
        frontier: list[Row | None] = [row]
        for a, b, r in steps:
            nxt: list[Row | None] = []
            frm = self.model.dataset(r.from_).name
            fcols = self._keys(frm, r.from_columns)
            tcols = self._keys(self.model.dataset(r.to).name, r.to_columns)
            for cur in frontier:
                if cur is None:
                    nxt.append(None)
                    continue
                if a == frm:  # many-to-one
                    key = tuple(cur[c] for c in fcols)
                    found = self._lookup(b, tcols, key) if None not in key else []
                else:  # one-to-many
                    key = tuple(cur[c] for c in tcols)
                    found = self._lookup(b, fcols, key) if None not in key else []
                nxt.extend(found or [None])
            frontier = nxt
        return frontier

    def _field_value(self, dim: DimRef, row: Row | None) -> Any:
        if row is None:
            return None
        parsed = self.loaded.field_sql[dim.dataset][dim.field.lower()]
        value = scalar(parsed, lambda c: row[c.name.lower()])
        return _truncate(value, dim.grain)

    def _row_lookup(self, home: str, row: Row):
        """Resolve ``dataset.field`` references inside an aggregate against one home row."""

        def look(col: exp.Column) -> Any:
            parsed = self.loaded.field_sql[home].get(col.name.lower())
            if parsed is None:
                return row[col.name.lower()]
            return scalar(parsed, lambda inner: row[inner.name.lower()])

        return look

    def evaluate(self, query: Query) -> list[tuple]:
        metric = self.model.metric(query.metric)
        if metric is None or metric.expression.portable() is None:
            raise OracleUnsupported("metric")
        ast = parse_sql(metric.expression.portable().expression, metric.name)
        if any(ast.find_all(exp.Window)):
            raise OracleUnsupported("window")
        aggs = [n for n in ast.walk() if isinstance(n, exp.AggFunc) and not _has_agg_ancestor(n)]
        per_agg: list[dict[tuple, Any]] = []
        for node in aggs:
            homes = {self.model.dataset(c.table).name for c in node.find_all(exp.Column) if c.table}
            if len(homes) != 1:
                raise OracleUnsupported("aggregate home")
            home = homes.pop()
            paths = [self._path(home, d.dataset) for d in query.dimensions]
            groups: dict[tuple, list[Row]] = defaultdict(list)
            for row in self.rows[home]:
                options = [
                    [self._field_value(d, end) for end in self._reach(row, p)]
                    for d, p in zip(query.dimensions, paths, strict=True)
                ]
                for combo in dict.fromkeys(product(*options)):  # each row once per group
                    groups[combo].append(row)
            if not query.dimensions and () not in groups:
                groups[()] = []
            lookup_for = partial(self._row_lookup, home)
            per_agg.append({g: _aggregate(node, rs, lookup_for) for g, rs in groups.items()})

        all_groups = list(dict.fromkeys(g for values in per_agg for g in values))
        out = []
        for g in all_groups:
            values = {}
            for node, results in zip(aggs, per_agg, strict=True):
                v = results.get(g, _MISSING)
                if v is _MISSING:
                    v = 0 if isinstance(node, (exp.Count, exp.ApproxDistinct)) else None
                values[id(node)] = v
            out.append((*g, _metric_value(ast, values)))
        return out


def _has_agg_ancestor(node: exp.Expression) -> bool:
    p = node.parent
    while p is not None:
        if isinstance(p, exp.AggFunc):
            return True
        p = p.parent
    return False


def _metric_value(node: exp.Expression, values: dict[int, Any]) -> Any:
    """Evaluate the metric expression with each aggregate node replaced by its value."""
    if id(node) in values:
        return values[id(node)]
    replaced = node.copy()
    originals = list(node.walk())
    copies = list(replaced.walk())
    holders: dict[str, Any] = {}
    for orig, cp in zip(originals, copies, strict=True):
        if id(orig) in values:
            name = f"__agg{len(holders)}"
            holders[name] = values[id(orig)]
            cp.replace(exp.column(name))
    return scalar(replaced, lambda c: holders[c.name])

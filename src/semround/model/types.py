"""Column type inference (spike S1).

Ossie 0.1.x carries no column types; 0.2 adds an optional ``datatype`` on fields.
Each physical column gets a type from the first rule that applies:

1. ``hint``      config ``[types]`` entry                     (sure)
2. ``declared``  0.2 ``datatype`` on a field that is a column  (sure)
3. ``usage``     how expressions use the column, e.g. inside SUM, ``||`` or a string comparison (sure)
4. ``key``       primary key or relationship column -> integer (sure)
5. ``relation``  copied from the other side of a relationship  (sure if that side is)
6. ``time``      ``is_time`` field                              (unsure: date vs timestamp vs year number)
7. ``name``      naming conventions such as ``*_price`` or ``*_date`` (unsure)
8. ``default``   string                                         (unsure)

Unsure columns are listed in the report so the user can add hints.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

import sqlglot.expressions as exp

from semround.model.loader import LoadedModel, parse_sql


class Kind(StrEnum):
    INTEGER = "Integer"
    DECIMAL = "Decimal"
    FLOAT = "Float"
    BOOLEAN = "Boolean"
    DATE = "Date"
    TIME = "Time"
    TIMESTAMP = "DateTime"
    STRING = "String"


DEFAULT_SQL = {
    Kind.INTEGER: "BIGINT",
    Kind.DECIMAL: "DECIMAL(18,2)",
    Kind.FLOAT: "DOUBLE",
    Kind.BOOLEAN: "BOOLEAN",
    Kind.DATE: "DATE",
    Kind.TIME: "TIME",
    Kind.TIMESTAMP: "TIMESTAMP",
    Kind.STRING: "VARCHAR",
}

_FROM_DATATYPE = {
    "String": Kind.STRING,
    "Integer": Kind.INTEGER,
    "Decimal": Kind.DECIMAL,
    "Float": Kind.FLOAT,
    "Boolean": Kind.BOOLEAN,
    "Date": Kind.DATE,
    "Time": Kind.TIME,
    "DateTime": Kind.TIMESTAMP,
    "DateTimeTz": Kind.TIMESTAMP,  # interpreted as UTC
}

NUMERIC = frozenset({Kind.INTEGER, Kind.DECIMAL, Kind.FLOAT})

_INT_NAME = re.compile(
    r"(^id$|_id$|_sk$|_key$|_count$|^count_|_cnt$|quantity|qty|_number_|^num_|_num$|employees|units|"
    r"_year$|^year$|_moy$|_dom$|_qoy$|_dow$|_week_seq|_month_seq|_age$)"
)
_DEC_NAME = re.compile(r"(price|amount|amt|profit|cost|revenue|sales|discount|tax|fee|margin|balance|total|value)")
_FLOAT_NAME = re.compile(r"(ratio|rate|pct|percent|score|lat$|lon$|latitude|longitude)")
_DATE_NAME = re.compile(r"(_date$|^date$|_day$|^day$|_dt$)")
_TS_NAME = re.compile(r"(_at$|_ts$|timestamp|_time$|datetime)")
_BOOL_NAME = re.compile(r"(^is_|^has_|_flag$)")
_STR_TIME_NAME = re.compile(r"(_name$|_label$)")


@dataclass(frozen=True)
class ColumnType:
    kind: Kind
    sql_type: str
    rule: str
    sure: bool


def parse_type_hint(text: str) -> Kind:
    t = text.strip().lower()
    if t.startswith(("decimal", "numeric")):
        return Kind.DECIMAL
    if t.startswith(("double", "float", "real")):
        return Kind.FLOAT
    if t.startswith(("bigint", "int", "smallint", "tinyint", "hugeint", "ubigint", "uinteger")):
        return Kind.INTEGER
    if t.startswith("bool"):
        return Kind.BOOLEAN
    if t.startswith(("timestamp", "datetime")):
        return Kind.TIMESTAMP
    if t == "date":
        return Kind.DATE
    if t.startswith("time"):
        return Kind.TIME
    if t.startswith(("varchar", "text", "string", "char")):
        return Kind.STRING
    raise ValueError(f"unknown type hint {text!r}")


def _name_kind(column: str, is_time: bool) -> Kind | None:
    if is_time:
        if _INT_NAME.search(column):
            return Kind.INTEGER
        if _STR_TIME_NAME.search(column):
            return Kind.STRING
        if _TS_NAME.search(column):
            return Kind.TIMESTAMP
        return Kind.DATE
    for pattern, kind in (
        (_BOOL_NAME, Kind.BOOLEAN),
        (_TS_NAME, Kind.TIMESTAMP),
        (_DATE_NAME, Kind.DATE),
        (_INT_NAME, Kind.INTEGER),
        (_FLOAT_NAME, Kind.FLOAT),
        (_DEC_NAME, Kind.DECIMAL),
    ):
        if pattern.search(column):
            return kind
    return None


_NUMERIC_AGGS = (exp.Sum, exp.Avg, exp.Stddev, exp.StddevPop, exp.StddevSamp, exp.Variance, exp.VariancePop)
_NEUTRAL_AGGS = (exp.Count, exp.Min, exp.Max, exp.ApproxDistinct)
_STRING_FUNCS = (exp.DPipe, exp.Concat, exp.Lower, exp.Upper, exp.Trim, exp.Substring, exp.Length, exp.Like, exp.ILike)
_DATE_FUNCS = (exp.DateTrunc, exp.TimestampTrunc, exp.Extract, exp.Year, exp.Month, exp.Day)
_ARITH = (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod, exp.Neg)
_PASS_THROUGH = (exp.Paren, exp.Case, exp.If, exp.Coalesce, exp.Nullif, exp.Alias, exp.Distinct, exp.Cast)


def _usage_kind(col: exp.Column) -> str | None:
    """'numeric', a Kind value, or None, from the nearest typing context above ``col``."""
    node: exp.Expression = col
    while node.parent is not None:
        parent = node.parent
        if isinstance(parent, _NUMERIC_AGGS) or isinstance(parent, _ARITH):
            return "numeric"
        if isinstance(parent, _STRING_FUNCS):
            return Kind.STRING.value
        if isinstance(parent, _DATE_FUNCS):
            return Kind.DATE.value
        if isinstance(parent, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.In, exp.Between)):
            for lit in parent.find_all(exp.Literal):
                return Kind.STRING.value if lit.is_string else "numeric"
            return None
        if isinstance(parent, _NEUTRAL_AGGS) or isinstance(parent, exp.Window):
            return None
        if isinstance(parent, _PASS_THROUGH) or isinstance(parent, exp.Condition):
            node = parent
            continue
        return None
    return None


def infer_types(loaded: LoadedModel, hints: dict[str, str] | None = None) -> dict[str, dict[str, ColumnType]]:
    """Return ``{dataset: {column: ColumnType}}`` for every physical column."""
    hints = {k.lower(): v for k, v in (hints or {}).items()}
    model = loaded.model
    out: dict[str, dict[str, ColumnType]] = {ds.name: {} for ds in model.datasets}
    usage: dict[tuple[str, str], str] = {}
    is_time: dict[tuple[str, str], bool] = {}

    def note(ds: str, column: str, kind: str | None) -> None:
        if kind is None:
            return
        key = (ds, column.lower())
        prev = usage.get(key)
        # A string use beats a numeric guess; never downgrade a concrete Kind to 'numeric'.
        if prev is None or (prev == "numeric" and kind != "numeric"):
            usage[key] = kind

    # Field expressions and declared types.
    for ds in model.datasets:
        for f in ds.fields:
            parsed = loaded.field_sql[ds.name].get(f.name.lower())
            if parsed is None:
                continue
            if isinstance(parsed, exp.Column):
                is_time[(ds.name, parsed.name.lower())] = f.is_time
                if f.datatype and f.datatype in _FROM_DATATYPE:
                    kind = _FROM_DATATYPE[f.datatype]
                    out[ds.name][parsed.name.lower()] = ColumnType(kind, DEFAULT_SQL[kind], "declared", True)
            for col in parsed.find_all(exp.Column):
                note(ds.name, col.name, _usage_kind(col))

    # Metric expressions: map dataset.field references back to physical columns.
    for metric in model.metrics:
        chosen = metric.expression.portable()
        if chosen is None:
            continue
        parsed = parse_sql(chosen.expression, f"metric {metric.name}")
        for col in parsed.find_all(exp.Column):
            ds = model.dataset(col.table) if col.table else None
            if ds is None:
                continue
            field_expr = loaded.field_sql[ds.name].get(col.name.lower())
            if field_expr is None:
                note(ds.name, col.name, _usage_kind(col))
            elif isinstance(field_expr, exp.Column):
                note(ds.name, field_expr.name, _usage_kind(col))

    key_cols: set[tuple[str, str]] = set()
    for ds in model.datasets:
        pk = loaded.primary_key(ds.name)
        for c in pk or []:
            key_cols.add((ds.name, c))
    for r in model.relationships:
        frm, to = model.dataset(r.from_), model.dataset(r.to)
        assert frm is not None and to is not None
        for c in loaded.key_columns(frm.name, r.from_columns):
            key_cols.add((frm.name, c))
        for c in loaded.key_columns(to.name, r.to_columns):
            key_cols.add((to.name, c))

    for ds in model.datasets:
        for column in loaded.columns[ds.name]:
            if column in out[ds.name]:
                continue
            hint = hints.get(f"{ds.name.lower()}.{column}")
            if hint is not None:
                out[ds.name][column] = ColumnType(parse_type_hint(hint), hint, "hint", True)
                continue
            use = usage.get((ds.name, column))
            named = _name_kind(column, is_time.get((ds.name, column), False))
            if use == "numeric":
                kind = named if named in NUMERIC else Kind.DECIMAL
                out[ds.name][column] = ColumnType(kind, DEFAULT_SQL[kind], "usage", True)
            elif use is not None:
                kind = Kind(use)
                out[ds.name][column] = ColumnType(kind, DEFAULT_SQL[kind], "usage", True)
            elif (ds.name, column) in key_cols:
                kind = named if named in (Kind.INTEGER, Kind.STRING) else Kind.INTEGER
                out[ds.name][column] = ColumnType(kind, DEFAULT_SQL[kind], "key", True)

    # Relationship columns share a type; copy from whichever side is better grounded.
    rank = {"hint": 0, "declared": 1, "usage": 2, "key": 3}
    for r in model.relationships:
        frm, to = model.dataset(r.from_), model.dataset(r.to)
        assert frm is not None and to is not None
        pairs = zip(
            loaded.key_columns(frm.name, r.from_columns), loaded.key_columns(to.name, r.to_columns), strict=True
        )
        for fc, tc in pairs:
            a, b = out[frm.name].get(fc), out[to.name].get(tc)
            if a and b and rank.get(a.rule, 9) < rank.get(b.rule, 9):
                out[to.name][tc] = ColumnType(a.kind, a.sql_type, "relation", a.sure)
            elif a and b and rank.get(b.rule, 9) < rank.get(a.rule, 9):
                out[frm.name][fc] = ColumnType(b.kind, b.sql_type, "relation", b.sure)

    for ds in model.datasets:
        # Kimball convention: next to an integer surrogate key (*_sk), *_id columns are
        # natural business keys, which are usually strings.
        surrogate = any(c.endswith("_sk") for c in loaded.primary_key(ds.name) or [])
        for column in loaded.columns[ds.name]:
            if column in out[ds.name]:
                continue
            col_is_time = is_time.get((ds.name, column), False)
            named = _name_kind(column, col_is_time)
            if surrogate and column.endswith("_id"):
                named = Kind.STRING
            if col_is_time:
                kind = named or Kind.DATE
                out[ds.name][column] = ColumnType(kind, DEFAULT_SQL[kind], "time", False)
            elif named is not None:
                out[ds.name][column] = ColumnType(named, DEFAULT_SQL[named], "name", False)
            else:
                out[ds.name][column] = ColumnType(Kind.STRING, DEFAULT_SQL[Kind.STRING], "default", False)

    # Keep physical column order stable.
    return {ds: {c: out[ds][c] for c in loaded.columns[ds]} for ds in out}


def unsure_columns(types: dict[str, dict[str, ColumnType]]) -> list[str]:
    return [f"{ds}.{c}" for ds, cols in types.items() for c, t in cols.items() if not t.sure]

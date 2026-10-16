"""Deliberately broken converters. Each one models a real class of conversion bug, and the
mutation tests assert Semround catches it with the right code (and trap, where one applies).
"""

from __future__ import annotations

import re
from pathlib import Path

import sqlglot.expressions as exp

from semround.compiler import compile_query
from semround.compiler.emit import dimension_sql, q
from semround.compiler.planner import find_path, home_dataset, metric_ast, outer_aggregates
from semround.compiler.query import Query
from semround.errors import Unsupported
from semround.model.loader import LoadedModel
from semround.targets import Compiled, Dropped, Outcome, Refusal


class _Base:
    name = "mutant"

    def prepare(self, loaded: LoadedModel, types, workdir: Path) -> None:
        self.loaded = loaded

    def versions(self) -> dict[str, str]:
        return {"mutant": self.name}

    def reference(self, query: Query) -> str:
        return compile_query(self.loaded, query)[1]


class NaiveJoin(_Base):
    """One SELECT over all joins, GROUP BY dims: double counts across one-to-many joins."""

    name = "naive"
    join = "LEFT JOIN"

    def compile(self, query: Query) -> Outcome:
        loaded = self.loaded
        try:
            ast = metric_ast(loaded, query.metric)
            aggs = outer_aggregates(ast)
            homes = [home_dataset(loaded, a) for a in aggs]
        except Unsupported as u:
            return Refusal(str(u))
        root = homes[0]
        steps = {}
        for target in [*homes[1:], *(d.dataset for d in query.dimensions)]:
            for s in find_path(loaded, root, target)[0]:
                steps.setdefault(s.right, s)
        lines = [f"FROM {loaded.sources[root].sql()} AS {q(root)}"]
        for s in steps.values():
            r = s.rel
            fcols = loaded.key_columns(r.from_, r.from_columns)
            tcols = loaded.key_columns(r.to, r.to_columns)
            on = " AND ".join(f"{q(r.from_)}.{q(f)} = {q(r.to)}.{q(t)}" for f, t in zip(fcols, tcols, strict=True))
            lines.append(f"{self.join} {loaded.sources[s.right].sql()} AS {q(s.right)} ON {on}")
        metric = ast.copy()
        for col in list(metric.find_all(exp.Column)):
            parsed = loaded.field_sql[loaded.model.dataset(col.table).name].get(col.name.lower())
            new = parsed.copy()
            for c in list(new.find_all(exp.Column)) if not isinstance(new, exp.Column) else [new]:
                c.set("table", exp.to_identifier(col.table, quoted=True))
            if new is parsed or isinstance(new, exp.Column):
                new.set("table", exp.to_identifier(col.table, quoted=True))
            col.replace(new if isinstance(new, exp.Column) else exp.Paren(this=new))
        dims = [dimension_sql(loaded, d) for d in query.dimensions]
        select = ", ".join([*(f"{d} AS d{i}" for i, d in enumerate(dims)), f"{metric.sql('duckdb')} AS m"])
        group = f" GROUP BY {', '.join(str(i + 1) for i in range(len(dims)))}" if dims else ""
        return Compiled(f"SELECT {select} " + " ".join(lines) + group)


class NaiveInnerJoin(NaiveJoin):
    """Inner joins: orphan rows vanish instead of landing in the NULL group."""

    name = "naive-inner"
    join = "JOIN"


class SundayWeeks(_Base):
    """Weeks run Sunday-Saturday but keep Monday labels, so only Sunday rows move week.

    (Relabelling every week would be caught too, but no single trap explains it.)
    """

    name = "sunday-weeks"

    def compile(self, query: Query) -> Outcome:
        try:
            sql = self.reference(query)
        except Unsupported as u:
            return Refusal(str(u))
        return Compiled(
            re.sub(r"date_trunc\('week', (\"[^\"]+\"\.\"[^\"]+\")\)", r"date_trunc('week', \1 + INTERVAL 1 DAY)", sql)
        )


class UtcShift(_Base):
    """Timestamps shifted one hour before truncation: a timezone bug."""

    name = "utc-shift"

    def compile(self, query: Query) -> Outcome:
        try:
            sql = self.reference(query)
        except Unsupported as u:
            return Refusal(str(u))
        return Compiled(
            re.sub(r"date_trunc\('(\w+)', (\"[^\"]+\"\.\"[^\"]+\")\)", r"date_trunc('\1', \2 + INTERVAL 1 HOUR)", sql)
        )


class DropFilter(_Base):
    """CASE WHEN filters inside aggregates are lost: SUM(CASE WHEN c THEN x ELSE 0 END) -> SUM(x)."""

    name = "drop-filter"

    def compile(self, query: Query) -> Outcome:
        try:
            sql = self.reference(query)
        except Unsupported as u:
            return Refusal(str(u))
        return Compiled(re.sub(r"CASE WHEN .*? THEN (.*?) ELSE 0 END", r"\1", sql))


class DropDistinctCounts(_Base):
    """Metrics using COUNT(DISTINCT ...) do not survive conversion (apache/ossie#462 style)."""

    name = "drop-distinct"

    def compile(self, query: Query) -> Outcome:
        ast = metric_ast(self.loaded, query.metric)
        if any(isinstance(n, exp.Count) and isinstance(n.this, exp.Distinct) for n in ast.walk()):
            return Dropped("COUNT(DISTINCT) not supported by converter")
        try:
            return Compiled(self.reference(query))
        except Unsupported as u:
            return Refusal(str(u))


ALL = [NaiveJoin, NaiveInnerJoin, SundayWeeks, UtcShift, DropFilter, DropDistinctCounts]

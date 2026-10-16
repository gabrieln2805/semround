"""Emit DuckDB SQL for a :class:`Plan`.

Shape of the output::

    WITH a0 AS (... one CTE per aggregate, grouped by the dimensions ...),
         a1 AS (...),
         g AS (SELECT d0, d1 FROM a0 UNION SELECT d0, d1 FROM a1)
    SELECT g.d0 AS "<dim label>", ..., <metric over a0.v, a1.v> AS "<metric>"
    FROM g LEFT JOIN a0 ON g.d0 IS NOT DISTINCT FROM a0.d0 ...
    ORDER BY ...

Result columns are always the dimensions in query order, then the metric. Targets follow the
same contract so results can be compared column by column.
"""

from __future__ import annotations

import sqlglot.expressions as exp

from semround.compiler.planner import AggPlan, Plan
from semround.compiler.query import DimRef
from semround.errors import Unsupported
from semround.model.loader import LoadedModel


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _qualified(loaded: LoadedModel, dataset: str, field_name: str) -> exp.Expression:
    """The field's portable expression with every column bound to the dataset alias."""
    parsed = loaded.field_sql[dataset].get(field_name.lower())
    if parsed is None:
        ds = loaded.model.dataset(dataset)
        if ds is not None and ds.field(field_name) is not None:
            raise Unsupported(f"{dataset}.{field_name} has no portable expression")
        # Not a declared field: a physical column of the dataset.
        return exp.column(field_name.lower(), table=dataset, quoted=True)
    tree = parsed.copy()
    for col in list(tree.find_all(exp.Column)):
        col.set("table", exp.to_identifier(dataset, quoted=True))
        col.set("this", exp.to_identifier(col.name.lower(), quoted=True))
    return tree if isinstance(tree, exp.Column) else exp.Paren(this=tree)


def _dataset_of(loaded: LoadedModel, col: exp.Column) -> str:
    if col.table:
        ds = loaded.model.dataset(col.table)
        assert ds is not None
        return ds.name
    return next(d.name for d in loaded.model.datasets if d.field(col.name) is not None)


def _bind_aggregate(loaded: LoadedModel, agg: AggPlan) -> str:
    tree = agg.node.copy()
    for col in list(tree.find_all(exp.Column)):
        col.replace(_qualified(loaded, _dataset_of(loaded, col), col.name))
    return tree.sql(dialect="duckdb")


def dimension_sql(loaded: LoadedModel, dim: DimRef) -> str:
    base = _qualified(loaded, dim.dataset, dim.field).sql(dialect="duckdb")
    if dim.grain is None:
        return base
    # DuckDB's date_trunc('week') is ISO: weeks start on Monday.
    return f"date_trunc('{dim.grain}', {base})"


def _from_clause(loaded: LoadedModel, agg: AggPlan) -> str:
    sources = loaded.sources
    lines = [f"FROM {sources[agg.home].sql()} AS {q(agg.home)}"]
    for step in agg.joins:
        rel = step.rel
        frm = loaded.model.dataset(rel.from_)
        to = loaded.model.dataset(rel.to)
        assert frm is not None and to is not None
        fcols = loaded.key_columns(frm.name, rel.from_columns)
        tcols = loaded.key_columns(to.name, rel.to_columns)
        cond = " AND ".join(f"{q(frm.name)}.{q(f)} = {q(to.name)}.{q(t)}" for f, t in zip(fcols, tcols, strict=True))
        lines.append(f"LEFT JOIN {sources[step.right].sql()} AS {q(step.right)} ON {cond}")
    return "\n    ".join(lines)


def _agg_cte(loaded: LoadedModel, plan: Plan, agg: AggPlan) -> str:
    dims = plan.query.dimensions
    group_by = f"\n  GROUP BY {', '.join(str(i + 1) for i in range(len(dims)))}" if dims else ""
    value = _bind_aggregate(loaded, agg)
    if not agg.fanout:
        select = [f"{dimension_sql(loaded, d)} AS {q(f'd{i}')}" for i, d in enumerate(dims)]
        select.append(f"{value} AS v")
        return f"SELECT {', '.join(select)}\n    {_from_clause(loaded, agg)}{group_by}"

    # Fan-out: one row per (home row, group), then aggregate the home rows.
    pk = loaded.primary_key(agg.home)
    assert pk
    inner = [f"{q(agg.home)}.{q(k)} AS {q(f'k{i}')}" for i, k in enumerate(pk)]
    inner += [f"{dimension_sql(loaded, d)} AS {q(f'd{i}')}" for i, d in enumerate(dims)]
    on = " AND ".join(f"{q(agg.home)}.{q(k)} = __g.{q(f'k{i}')}" for i, k in enumerate(pk))
    outer = [f"__g.{q(f'd{i}')} AS {q(f'd{i}')}" for i in range(len(dims))] + [f"{value} AS v"]
    return (
        f"SELECT {', '.join(outer)}\n"
        f"    FROM (SELECT DISTINCT {', '.join(inner)}\n"
        f"          {_from_clause(loaded, agg)}) AS __g\n"
        f"    JOIN {loaded.sources[agg.home].sql()} AS {q(agg.home)} ON {on}{group_by}"
    )


def emit_sql(loaded: LoadedModel, plan: Plan) -> str:
    dims = plan.query.dimensions
    ctes = [f"a{a.index} AS (\n  {_agg_cte(loaded, plan, a)}\n)" for a in plan.aggs]

    # Replace each aggregate in the metric expression by its CTE value.
    metric = plan.metric_ast.copy()
    originals = list(plan.metric_ast.walk())
    copies = list(metric.walk())
    for a in plan.aggs:
        target = copies[next(i for i, n in enumerate(originals) if n is a.node)]
        ref = exp.column("v", table=f"a{a.index}")
        new = exp.func("COALESCE", ref, exp.Literal.number(0)) if a.count_like else ref
        if target is metric:
            metric = new
        else:
            target.replace(new)
    metric_sql = metric.sql(dialect="duckdb")
    metric_name = plan.query.metric

    if not dims:
        sources = ", ".join(f"a{a.index}" for a in plan.aggs)
        select = f"SELECT {metric_sql} AS {q(metric_name)}\nFROM {sources}"
        return "WITH " + ",\n".join(ctes) + "\n" + select

    dcols = [q(f"d{i}") for i in range(len(dims))]
    union = "\n  UNION\n  ".join(f"SELECT {', '.join(dcols)} FROM a{a.index}" for a in plan.aggs)
    ctes.append(f"g AS (\n  {union}\n)")
    joins = []
    for a in plan.aggs:
        on = " AND ".join(f"g.{c} IS NOT DISTINCT FROM a{a.index}.{c}" for c in dcols)
        joins.append(f"LEFT JOIN a{a.index} ON {on}")
    select = ", ".join(f"g.{c} AS {q(d.label)}" for c, d in zip(dcols, dims, strict=True))
    order = ", ".join(f"{i + 1} NULLS FIRST" for i in range(len(dims)))
    return (
        "WITH " + ",\n".join(ctes) + "\n"
        f"SELECT {select}, {metric_sql} AS {q(metric_name)}\n"
        "FROM g\n" + "\n".join(joins) + f"\nORDER BY {order}"
    )


def compile_query(loaded: LoadedModel, query) -> tuple[Plan, str]:
    from semround.compiler.planner import plan_query

    plan = plan_query(loaded, query)
    return plan, emit_sql(loaded, plan)

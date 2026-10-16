"""Plan a query: decompose the metric into aggregates and find each one's join tree.

Semantics follow the Ossie Relational Query Interface rule that every measure aggregates
at its own grain, whatever the joins:

- Each aggregate reads exactly one dataset (its *home*).
- Dimensions reachable from the home along many-to-one steps are joined with LEFT JOIN, so
  orphan rows land in the NULL group instead of disappearing.
- A dimension reachable only through a one-to-many step makes the plan *fan-out*. The
  grain-safe answer then counts each home row at most once per group (deduplicated by the
  home's primary key); a target must match it or refuse the query.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import sqlglot.expressions as exp

from semround.compiler.query import Query
from semround.errors import Unsupported, UsageError
from semround.model.loader import LoadedModel, parse_sql
from semround.model.spec import Relationship

COUNT_LIKE = (exp.Count, exp.ApproxDistinct)


@dataclass(frozen=True)
class JoinStep:
    left: str  # dataset already in the tree
    right: str  # dataset being joined
    rel: Relationship
    forward: bool  # True: many-to-one (left is the 'from' side)


@dataclass
class AggPlan:
    index: int
    node: exp.Expression  # aggregate node inside Plan.metric_ast
    home: str
    joins: list[JoinStep] = field(default_factory=list)
    fanout: bool = False

    @property
    def count_like(self) -> bool:
        return isinstance(self.node, COUNT_LIKE)


@dataclass
class Plan:
    query: Query
    metric_ast: exp.Expression
    aggs: list[AggPlan]

    @property
    def fanout(self) -> bool:
        return any(a.fanout for a in self.aggs)


def metric_ast(loaded: LoadedModel, metric_name: str) -> exp.Expression:
    metric = loaded.model.metric(metric_name)
    if metric is None:
        raise UsageError(f"unknown metric {metric_name!r}")
    chosen = metric.expression.portable()
    if chosen is None:
        raise Unsupported("no OSSIE_SQL_2026 or ANSI_SQL expression")
    return parse_sql(chosen.expression, f"metric {metric.name}")


def outer_aggregates(ast: exp.Expression) -> list[exp.Expression]:
    found = []
    for node in ast.walk():
        if isinstance(node, exp.AggFunc):
            parent = node.parent
            nested = False
            while parent is not None:
                if isinstance(parent, exp.AggFunc):
                    nested = True
                    break
                parent = parent.parent
            if nested:
                raise Unsupported("nested aggregates")
            found.append(node)
    return found


def home_dataset(loaded: LoadedModel, agg: exp.Expression) -> str:
    homes = set()
    for col in agg.find_all(exp.Column):
        if not col.table:
            matches = [d.name for d in loaded.model.datasets if d.field(col.name) is not None]
            if len(matches) != 1:
                raise Unsupported(f"unqualified column {col.name!r} in {agg.sql()!r}")
            homes.add(matches[0])
        else:
            ds = loaded.model.dataset(col.table)
            if ds is None:
                raise UsageError(f"unknown dataset {col.table!r} in {agg.sql()!r}")
            homes.add(ds.name)
    if not homes:
        if len(loaded.model.datasets) == 1:
            return loaded.model.datasets[0].name
        raise Unsupported(f"cannot tell which dataset {agg.sql()!r} counts")
    if len(homes) > 1:
        raise Unsupported(f"aggregate {agg.sql()!r} reads several datasets {sorted(homes)}")
    return homes.pop()


def _edges(loaded: LoadedModel) -> dict[str, list[tuple[str, Relationship, bool]]]:
    model = loaded.model
    adj: dict[str, list[tuple[str, Relationship, bool]]] = {d.name: [] for d in model.datasets}
    for r in model.relationships:
        f, t = model.dataset(r.from_), model.dataset(r.to)
        assert f is not None and t is not None
        adj[f.name].append((t.name, r, True))
        adj[t.name].append((f.name, r, False))
    return adj


def find_path(loaded: LoadedModel, start: str, goal: str) -> tuple[list[JoinStep], bool]:
    """Shortest join path; many-to-one-only paths win. Returns (steps, fanout)."""
    if start == goal:
        return [], False
    adj = _edges(loaded)
    for forward_only in (True, False):
        steps = _bfs(adj, start, goal, forward_only)
        if steps is not None:
            return steps, any(not s.forward for s in steps)
    raise Unsupported(f"no join path from {start} to {goal}")


def _bfs(adj, start: str, goal: str, forward_only: bool) -> list[JoinStep] | None:
    dist = {start: 0}
    ways = {start: 1}
    prev: dict[str, JoinStep] = {}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for nxt, rel, forward in adj[node]:
            if forward_only and not forward:
                continue
            if nxt not in dist:
                dist[nxt] = dist[node] + 1
                ways[nxt] = ways[node]
                prev[nxt] = JoinStep(node, nxt, rel, forward)
                queue.append(nxt)
            elif dist[nxt] == dist[node] + 1:
                ways[nxt] += ways[node]
    if goal not in dist:
        return None
    if ways[goal] > 1:
        raise Unsupported(f"ambiguous join path from {start} to {goal}")
    steps = []
    node = goal
    while node != start:
        step = prev[node]
        steps.append(step)
        node = step.left
    return list(reversed(steps))


def plan_query(loaded: LoadedModel, query: Query) -> Plan:
    ast = metric_ast(loaded, query.metric)
    if any(ast.find_all(exp.Window)):
        raise Unsupported("window functions in metrics")
    aggs = outer_aggregates(ast)
    if not aggs:
        raise Unsupported("metric has no aggregate")
    for col in ast.find_all(exp.Column):
        if not any(col is n or _inside(col, n) for n in aggs):
            raise Unsupported(f"column {col.sql()!r} outside any aggregate")

    plans = []
    for i, node in enumerate(aggs):
        home = home_dataset(loaded, node)
        joined: dict[str, JoinStep] = {}
        fanout = False
        for dim in query.dimensions:
            steps, fo = find_path(loaded, home, dim.dataset)
            fanout |= fo
            for s in steps:
                prior = joined.get(s.right)
                if prior is not None and prior.rel.name != s.rel.name:
                    raise Unsupported(f"two join paths reach {s.right}")
                joined.setdefault(s.right, s)
        if fanout and not loaded.primary_key(home):
            raise Unsupported(f"fan-out query on {home}, which has no primary key")
        plans.append(AggPlan(i, node, home, list(joined.values()), fanout))
    return Plan(query, ast, plans)


def _inside(node: exp.Expression, ancestor: exp.Expression) -> bool:
    p = node.parent
    while p is not None:
        if p is ancestor:
            return True
        p = p.parent
    return False

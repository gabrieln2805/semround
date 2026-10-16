"""The query set ``semround check`` runs for each metric.

Per metric: the grand total, each reachable categorical dimension, each time dimension at
day, week and month grain, and one two-dimension query. Capped so a run stays fast.
"""

from __future__ import annotations

from semround.compiler.planner import find_path, home_dataset, metric_ast, outer_aggregates
from semround.compiler.query import DimRef, Query
from semround.errors import Unsupported
from semround.model.loader import LoadedModel
from semround.model.types import ColumnType, Kind

MAX_QUERIES_PER_METRIC = 16
# Explicit grains only: converters give time dimensions a default grain (dbt: DAY), so a
# grain-less query on a timestamp would compare raw values with truncated ones.
TIME_GRAINS = ("day", "week", "month")


def _homes(loaded: LoadedModel, metric: str) -> list[str]:
    try:
        return sorted({home_dataset(loaded, a) for a in outer_aggregates(metric_ast(loaded, metric))})
    except Unsupported:
        return []


def _reachable(loaded: LoadedModel, homes: list[str], dataset: str) -> bool:
    for h in homes:
        try:
            find_path(loaded, h, dataset)
        except Unsupported:
            return False
    return True


def dimension_candidates(
    loaded: LoadedModel, types: dict[str, dict[str, ColumnType]], homes: list[str]
) -> tuple[list[DimRef], list[DimRef]]:
    """(categorical, temporal) dimension fields, in model order, excluding key columns."""
    categorical, temporal = [], []
    for ds in loaded.model.datasets:
        if not _reachable(loaded, homes, ds.name):
            continue
        keys = set(loaded.primary_key(ds.name) or [])
        for r in loaded.model.relationships:
            if r.from_.lower() == ds.name.lower():
                keys |= set(loaded.key_columns(ds.name, r.from_columns))
            if r.to.lower() == ds.name.lower():
                keys |= set(loaded.key_columns(ds.name, r.to_columns))
        for f in ds.fields:
            if not f.is_dimension:
                continue
            parsed = loaded.field_sql[ds.name].get(f.name.lower())
            if parsed is None:
                continue
            column = parsed.name.lower() if parsed.key == "column" else None
            if column in keys:
                continue
            kind = types[ds.name][column].kind if column else None
            if f.is_time and kind in (Kind.DATE, Kind.TIMESTAMP):
                temporal.append(DimRef(ds.name, f.name))
            else:
                categorical.append(DimRef(ds.name, f.name))
    return categorical, temporal


def queries_for(loaded: LoadedModel, types: dict[str, dict[str, ColumnType]], metric: str) -> list[Query]:
    homes = _homes(loaded, metric)
    out = [Query(metric)]
    if not homes:
        return out
    categorical, temporal = dimension_candidates(loaded, types, homes)
    out += [Query(metric, (d,)) for d in categorical]
    for t in temporal:
        out += [Query(metric, (DimRef(t.dataset, t.field, g),)) for g in TIME_GRAINS]
    if categorical and temporal:
        t = temporal[0]
        out.append(Query(metric, (categorical[0], DimRef(t.dataset, t.field, "month"))))
    return out[:MAX_QUERIES_PER_METRIC]


def all_queries(loaded: LoadedModel, types: dict[str, dict[str, ColumnType]]) -> list[Query]:
    return [q for m in loaded.model.metrics for q in queries_for(loaded, types, m.name)]

"""Seeded synthetic fixture generator.

Parents (the ``to`` side of relationships) are generated before children so foreign keys
can be drawn from real parent keys. Every random choice comes from a stream derived from
(seed, dataset, purpose), so the same inputs always give the same rows.
"""

from __future__ import annotations

from collections.abc import Iterable

from semround.errors import UsageError
from semround.fixtures.data import FixtureSet, Table, TrapRecord, derive_rng
from semround.fixtures.traps import ALL_TRAPS, DEFAULT_TRAPS, TrapContext
from semround.fixtures.values import key_value, regular_value
from semround.model.loader import LoadedModel
from semround.model.types import ColumnType

DEFAULT_CATALOG = "semround"


def _topological(loaded: LoadedModel) -> list[str]:
    model = loaded.model
    parents: dict[str, set[str]] = {d.name: set() for d in model.datasets}
    for r in model.relationships:
        f, t = model.dataset(r.from_), model.dataset(r.to)
        assert f is not None and t is not None
        if f.name != t.name:
            parents[f.name].add(t.name)
    order: list[str] = []
    remaining = dict(parents)
    while remaining:
        ready = sorted(n for n, ps in remaining.items() if ps <= set(order))
        if not ready:
            raise UsageError(f"relationship cycle between datasets {sorted(remaining)}; not supported yet")
        order.extend(ready)
        for n in ready:
            del remaining[n]
    return order


def generate(
    loaded: LoadedModel,
    types: dict[str, dict[str, ColumnType]],
    rows: int = 1000,
    seed: int = 7,
    traps: Iterable[str] = DEFAULT_TRAPS,
) -> FixtureSet:
    traps = tuple(t for t in DEFAULT_TRAPS if t in set(traps))
    model = loaded.model
    parent_names = {model.dataset(r.to).name for r in model.relationships}  # type: ignore[union-attr]
    child_names = {model.dataset(r.from_).name for r in model.relationships}  # type: ignore[union-attr]
    tables: dict[str, Table] = {}
    records: list[TrapRecord] = []

    for name in _topological(loaded):
        ds = model.dataset(name)
        assert ds is not None
        # Facts get `rows`; pure dimensions 1/20 of that; tables in between (orders under
        # order_items) 1/4, so each parent row has a few children.
        if name not in parent_names:
            n_rows = rows
        elif name in child_names:
            n_rows = max(10, rows // 4)
        else:
            n_rows = max(10, rows // 20)
        columns = loaded.columns[name]
        pk = loaded.primary_key(name) or []
        unique = [c for uk in ds.unique_keys or [] for c in loaded.key_columns(name, uk)]
        fks = [
            (r, loaded.key_columns(name, r.from_columns))
            for r in model.relationships
            if model.dataset(r.from_).name == name  # type: ignore[union-attr]
        ]
        fk_cols = {c for _, cols in fks for c in cols}
        key_cols = (
            set(pk)
            | set(unique)
            | fk_cols
            | {
                c
                for r in model.relationships
                if model.dataset(r.to).name == name  # type: ignore[union-attr]
                for c in loaded.key_columns(name, r.to_columns)
            }
        )
        is_time = {}
        for f in ds.fields:
            parsed = loaded.field_sql[name].get(f.name.lower())
            if parsed is not None and parsed.key == "column":
                is_time[parsed.name.lower()] = f.is_time

        rng = derive_rng(seed, name, "base")
        data: list[list] = []
        seen_pk: set[tuple] = set()
        for i in range(n_rows):
            row: dict[str, object] = {}
            for rel, cols in fks:
                parent = tables[model.dataset(rel.to).name]  # type: ignore[union-attr]
                if not parent.rows:
                    continue
                prow = parent.rows[rng.randrange(len(parent.rows))]
                for fc, tc in zip(cols, loaded.key_columns(parent.dataset, rel.to_columns), strict=True):
                    row[fc] = prow[parent.col(tc)]
            for c in columns:
                if c in row:
                    continue
                if c in pk or c in unique or c in key_cols:
                    row[c] = key_value(name, c, types[name][c], i)
                else:
                    row[c] = regular_value(c, types[name][c], rng, is_time.get(c, False))
            if pk:
                key = tuple(row[c] for c in pk)
                if key in seen_pk:  # bridge tables whose key is all foreign keys
                    continue
                seen_pk.add(key)
            data.append([row[c] for c in columns])

        table = Table(name, loaded.sources[name], list(columns), types[name], data, key_columns=key_cols)
        ctx_fks = [cols for _, cols in fks]
        raw: list[TrapRecord] = []
        for trap in traps:
            ctx = TrapContext(table, derive_rng(seed, name, trap), ctx_fks, is_time)
            raw.extend(ALL_TRAPS[trap](ctx))

        # Write rows in key order and translate trap row indexes to written ordinals.
        order = list(range(len(table.rows)))
        if pk:
            idx = [table.col(c) for c in pk]
            order.sort(key=lambda r: tuple(_sortable(table.rows[r][i]) for i in idx))
        ordinal = {old: new for new, old in enumerate(order)}
        table.rows = [table.rows[r] for r in order]
        records.extend(TrapRecord(t.trap, t.dataset, t.columns, tuple(sorted(ordinal[r] for r in t.rows))) for t in raw)
        tables[name] = table

    has_bare = any(t.source.catalog is None for t in tables.values())
    primary = DEFAULT_CATALOG if has_bare else tables[model.datasets[0].name].source.catalog or DEFAULT_CATALOG
    return FixtureSet(
        model_name=model.name,
        spec_version=loaded.spec_version,
        seed=seed,
        rows=rows,
        traps_enabled=traps,
        tables=[tables[d.name] for d in model.datasets],
        traps=records,
        primary_catalog=primary,
    )


def _sortable(value: object) -> tuple:
    return (value is None, str(type(value).__name__), value if value is not None else 0)

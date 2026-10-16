"""DuckDB execution: load fixtures under their source names and run SQL.

Tables live at the path their Ossie ``source`` names (``catalog.schema.table``), each catalog
being an attached database, so reference SQL and every target's SQL resolve the same names.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from semround.fixtures.data import FixtureSet, Table


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


@dataclass
class Result:
    columns: list[str]
    rows: list[tuple[Any, ...]]


def _create(con: duckdb.DuckDBPyConnection, catalog: str, table: Table) -> None:
    schema = table.source.schema or "main"
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {_q(catalog)}.{_q(schema)}")
    name = f"{_q(catalog)}.{_q(schema)}.{_q(table.source.table)}"
    cols = ", ".join(f"{_q(c)} {table.types[c].sql_type}" for c in table.columns)
    con.execute(f"CREATE TABLE {name} ({cols})")
    # Rendered literals: ~100x faster than executemany or list parameters for fixture sizes.
    for start in range(0, len(table.rows), _CHUNK):
        chunk = table.rows[start : start + _CHUNK]
        values = ",".join("(" + ",".join(_literal(v) for v in row) + ")" for row in chunk)
        con.execute(f"INSERT INTO {name} VALUES {values}")


_CHUNK = 2000


def _literal(v: Any) -> str:
    """Exact SQL literal for a fixture value."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, Decimal)):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, dt.datetime):
        return f"TIMESTAMP '{v.isoformat(sep=' ')}'"
    if isinstance(v, dt.date):
        return f"DATE '{v.isoformat()}'"
    if isinstance(v, dt.time):
        return f"TIME '{v.isoformat()}'"
    return _sql_str(str(v))


def load_memory(fixtures: FixtureSet) -> duckdb.DuckDBPyConnection:
    """An in-memory database holding the fixture tables under their source names."""
    con = duckdb.connect(":memory:")
    for catalog in fixtures.catalogs():
        con.execute(f"ATTACH ':memory:' AS {_q(catalog)}")
    for t in fixtures.tables:
        _create(con, t.source.catalog or fixtures.primary_catalog, t)
    con.execute(f"USE {_q(fixtures.primary_catalog)}")
    return con


def write_files(fixtures: FixtureSet, out_dir: Path) -> Path:
    """Write one DuckDB file per catalog plus ``manifest.json``; returns the manifest path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for catalog in fixtures.catalogs():
        path = out_dir / f"{catalog}.duckdb"
        for stale in (path, path.with_suffix(".duckdb.wal")):
            stale.unlink(missing_ok=True)
        con = duckdb.connect(str(path))
        try:
            for t in fixtures.tables:
                if (t.source.catalog or fixtures.primary_catalog) == catalog:
                    _create(con, catalog, t)
            con.execute("CHECKPOINT")
        finally:
            con.close()
    manifest = out_dir / "manifest.json"
    manifest.write_text(json.dumps(fixtures.manifest(), indent=2) + "\n", encoding="utf-8", newline="\n")
    return manifest


def open_files(out_dir: Path) -> duckdb.DuckDBPyConnection:
    """Open fixtures written by :func:`write_files`, read-only."""
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    con = duckdb.connect(":memory:")
    for catalog, filename in manifest["catalogs"].items():
        con.execute(f"ATTACH {_sql_str(str(out_dir / filename))} AS {_q(catalog)} (READ_ONLY)")
    con.execute(f"USE {_q(manifest['primary_catalog'])}")
    return con


def _sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def run(con: duckdb.DuckDBPyConnection, sql: str) -> Result:
    cur = con.execute(sql)
    columns = [d[0] for d in cur.description or []]
    return Result(columns, cur.fetchall())

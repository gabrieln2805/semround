"""In-memory fixture tables, shared by the generator, the traps and the engine."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import Any

from semround.model.loader import PhysicalSource
from semround.model.types import ColumnType


def derive_rng(seed: int, *parts: str) -> random.Random:
    """An independent, platform-stable RNG stream per (seed, dataset, purpose).

    Uses sha256 rather than ``hash()``, which is salted per process.
    """
    digest = hashlib.sha256(":".join([str(seed), *parts]).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


@dataclass
class Table:
    dataset: str
    source: PhysicalSource
    columns: list[str]
    types: dict[str, ColumnType]
    rows: list[list[Any]]
    key_columns: set[str] = field(default_factory=set)  # primary, unique and relationship columns

    def col(self, name: str) -> int:
        return self.columns.index(name)

    def digest(self) -> str:
        canonical = json.dumps(self.rows, default=str, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class TrapRecord:
    trap: str
    dataset: str
    columns: tuple[str, ...]
    rows: tuple[int, ...]  # row ordinals in the written (key-ordered) table

    def to_json(self) -> dict[str, Any]:
        return {"trap": self.trap, "dataset": self.dataset, "columns": list(self.columns), "rows": list(self.rows)}


@dataclass
class FixtureSet:
    model_name: str
    spec_version: str
    seed: int
    rows: int
    traps_enabled: tuple[str, ...]
    tables: list[Table]
    traps: list[TrapRecord]
    primary_catalog: str

    def table(self, dataset: str) -> Table:
        for t in self.tables:
            if t.dataset == dataset:
                return t
        raise KeyError(dataset)

    def catalogs(self) -> list[str]:
        names = {t.source.catalog or self.primary_catalog for t in self.tables}
        return sorted(names, key=lambda c: (c != self.primary_catalog, c))

    def manifest(self) -> dict[str, Any]:
        return {
            "model": self.model_name,
            "spec_version": self.spec_version,
            "seed": self.seed,
            "rows": self.rows,
            "primary_catalog": self.primary_catalog,
            "catalogs": {c: f"{c}.duckdb" for c in self.catalogs()},
            "traps_enabled": list(self.traps_enabled),
            "tables": [
                {
                    "dataset": t.dataset,
                    "source": ".".join(p for p in (t.source.catalog, t.source.schema, t.source.table) if p),
                    "rows": len(t.rows),
                    "digest": t.digest(),
                    "columns": [
                        {"name": c, "type": t.types[c].sql_type, "rule": t.types[c].rule, "sure": t.types[c].sure}
                        for c in t.columns
                    ],
                }
                for t in self.tables
            ],
            "traps": [r.to_json() for r in self.traps],
        }

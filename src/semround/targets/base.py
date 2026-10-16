"""The target protocol: anything that turns an Ossie model into SQL we can run on the fixtures.

A target's SQL must return the query's dimensions in order, then the metric value, and must
read the fixture tables by their Ossie ``source`` names.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from semround.compiler.query import Query
from semround.model.loader import LoadedModel
from semround.model.types import ColumnType


@dataclass(frozen=True)
class Compiled:
    sql: str


@dataclass(frozen=True)
class Refusal:
    """The target declines this query (allowed for fan-out queries)."""

    reason: str


@dataclass(frozen=True)
class Dropped:
    """The metric or a field it needs did not survive conversion."""

    reason: str


Outcome = Compiled | Refusal | Dropped


@runtime_checkable
class Target(Protocol):
    name: str

    def prepare(self, loaded: LoadedModel, types: dict[str, dict[str, ColumnType]], workdir: Path) -> None:
        """Convert the model once; called before any :meth:`compile`."""

    def compile(self, query: Query) -> Outcome:
        """SQL for one query, or why there is none."""

    def versions(self) -> dict[str, str]:
        """Pinned versions of everything the target runs, for the report."""

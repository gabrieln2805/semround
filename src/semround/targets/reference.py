"""The reference compiler exposed as a target: a self-check that must always pass."""

from __future__ import annotations

from pathlib import Path

from semround import __version__
from semround.compiler import compile_query
from semround.compiler.query import Query
from semround.errors import Unsupported
from semround.model.loader import LoadedModel
from semround.model.types import ColumnType
from semround.targets.base import Compiled, Outcome, Refusal


class ReferenceTarget:
    name = "reference"
    is_reference = True  # the answer key itself; skipped by checks that judge converters

    def __init__(self) -> None:
        self._loaded: LoadedModel | None = None

    def prepare(self, loaded: LoadedModel, types: dict[str, dict[str, ColumnType]], workdir: Path) -> None:
        self._loaded = loaded

    def compile(self, query: Query) -> Outcome:
        assert self._loaded is not None, "prepare() first"
        try:
            return Compiled(compile_query(self._loaded, query)[1])
        except Unsupported as u:
            return Refusal(str(u))

    def versions(self) -> dict[str, str]:
        return {"semround": __version__}

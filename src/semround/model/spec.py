"""Pydantic models for one Ossie semantic model.

One internal shape covers both document versions:
- 0.1.0 / 0.1.1: ``{version, semantic_model: [model, ...]}`` (no ``datatype``)
- 0.2.0.dev0: the model's properties sit at the document root and fields may carry ``datatype``

The loader unwraps either shape into :class:`SemanticModel`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from pydantic import Field as PydField

# Dialects whose semantics Semround can execute, in order of preference.
# OSSIE_SQL_2026 is the only dialect the spec defines; ANSI_SQL is the 0.1.x default.
PORTABLE_DIALECTS = ("OSSIE_SQL_2026", "ANSI_SQL")

KNOWN_DIALECTS = frozenset(
    {
        "ANSI_SQL",
        "SNOWFLAKE",
        "MDX",
        "TABLEAU",
        "DATABRICKS",
        "MAQL",
        "BIGQUERY",
        "SIGMA",
        "THOUGHTSPOT",
        "DAX",
        "HOLISTICS_AQL",
        "OSSIE_SQL_2026",
    }
)

DataType = Literal[
    "String", "Integer", "Decimal", "Float", "Boolean", "Date", "Time", "DateTime", "DateTimeTz", "Opaque"
]
TEMPORAL_DATATYPES = frozenset({"Date", "Time", "DateTime", "DateTimeTz"})


class _Base(BaseModel):
    # Mirrors the spec's additionalProperties: false, so typos fail loudly.
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)


class CustomExtension(_Base):
    vendor_name: str
    data: str


class DialectExpression(_Base):
    dialect: str
    expression: str


class Expression(_Base):
    dialects: list[DialectExpression] = PydField(min_length=1)

    def portable(self) -> DialectExpression | None:
        """The expression Semround evaluates: OSSIE_SQL_2026 first, then ANSI_SQL.

        Never falls back to array position (apache/ossie#461).
        """
        for wanted in PORTABLE_DIALECTS:
            for d in self.dialects:
                if d.dialect == wanted:
                    return d
        return None


class Dimension(_Base):
    is_time: bool | None = None


class Field(_Base):
    name: str = PydField(min_length=1)
    expression: Expression
    dimension: Dimension | None = None
    label: str | None = None
    description: str | None = None
    datatype: DataType | None = None
    ai_context: str | dict[str, Any] | None = None
    custom_extensions: list[CustomExtension] | None = None

    @property
    def is_time(self) -> bool:
        """Spec rule: explicit ``is_time`` wins, else temporal datatypes default to true."""
        if self.dimension is not None and self.dimension.is_time is not None:
            return self.dimension.is_time
        return self.datatype in TEMPORAL_DATATYPES

    @property
    def is_dimension(self) -> bool:
        return self.dimension is not None or self.is_time


class Dataset(_Base):
    name: str = PydField(min_length=1)
    source: str = PydField(min_length=1)
    primary_key: list[str] | None = None
    unique_keys: list[list[str]] | None = None
    description: str | None = None
    ai_context: str | dict[str, Any] | None = None
    fields: list[Field] = PydField(default_factory=list)
    custom_extensions: list[CustomExtension] | None = None

    def field(self, name: str) -> Field | None:
        low = name.lower()
        for f in self.fields:
            if f.name.lower() == low:
                return f
        return None


class Relationship(_Base):
    name: str = PydField(min_length=1)
    from_: str = PydField(alias="from", min_length=1)
    to: str = PydField(min_length=1)
    from_columns: list[str] = PydField(min_length=1)
    to_columns: list[str] = PydField(min_length=1)
    ai_context: str | dict[str, Any] | None = None
    custom_extensions: list[CustomExtension] | None = None


class Metric(_Base):
    name: str = PydField(min_length=1)
    expression: Expression
    description: str | None = None
    datatype: DataType | None = None
    ai_context: str | dict[str, Any] | None = None
    custom_extensions: list[CustomExtension] | None = None


class SemanticModel(_Base):
    name: str = PydField(min_length=1)
    description: str | None = None
    ai_context: str | dict[str, Any] | None = None
    datasets: list[Dataset] = PydField(min_length=1)
    relationships: list[Relationship] = PydField(default_factory=list)
    metrics: list[Metric] = PydField(default_factory=list)
    custom_extensions: list[CustomExtension] | None = None

    def dataset(self, name: str) -> Dataset | None:
        low = name.lower()
        for d in self.datasets:
            if d.name.lower() == low:
                return d
        return None

    def metric(self, name: str) -> Metric | None:
        low = name.lower()
        for m in self.metrics:
            if m.name.lower() == low:
                return m
        return None

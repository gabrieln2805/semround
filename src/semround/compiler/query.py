"""A query: one metric, grouped by zero or more dimensions (with optional time grains)."""

from __future__ import annotations

from dataclasses import dataclass

from semround.errors import UsageError
from semround.model.spec import SemanticModel

GRAINS = ("day", "week", "month", "quarter", "year")


@dataclass(frozen=True)
class DimRef:
    dataset: str
    field: str
    grain: str | None = None

    @property
    def label(self) -> str:
        base = f"{self.dataset}.{self.field}"
        return f"{base}__{self.grain}" if self.grain else base


@dataclass(frozen=True)
class Query:
    metric: str
    dimensions: tuple[DimRef, ...] = ()

    @property
    def key(self) -> str:
        """Stable, human-readable query id, e.g. ``total_sales by store.s_state, date_dim.d_date__month``."""
        if not self.dimensions:
            return self.metric
        return f"{self.metric} by " + ", ".join(d.label for d in self.dimensions)


def parse_dimensions(text: str, model: SemanticModel) -> tuple[DimRef, ...]:
    """Parse ``region,orders.order_date:month``; bare field names must be unique in the model."""
    dims = []
    for raw in (p.strip() for p in text.split(",")):
        if not raw:
            continue
        name, _, grain = raw.partition(":")
        grain_value = grain.lower() or None
        if grain_value is not None and grain_value not in GRAINS:
            raise UsageError(f"unknown time grain {grain!r}; use one of {', '.join(GRAINS)}")
        if "." in name:
            ds_name, _, field_name = name.partition(".")
            ds = model.dataset(ds_name)
            if ds is None or ds.field(field_name) is None:
                raise UsageError(f"unknown dimension {name!r}")
            f = ds.field(field_name)
            assert f is not None
            dims.append(DimRef(ds.name, f.name, grain_value))
            continue
        matches = [(d, d.field(name)) for d in model.datasets if d.field(name) is not None]
        if not matches:
            raise UsageError(f"unknown dimension {name!r}")
        if len(matches) > 1:
            options = ", ".join(f"{d.name}.{name}" for d, _ in matches)
            raise UsageError(f"dimension {name!r} is ambiguous; use one of {options}")
        d, f = matches[0]
        assert f is not None
        dims.append(DimRef(d.name, f.name, grain_value))
    return tuple(dims)

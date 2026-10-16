"""Target registry: reference (self-check) and dbt; ossie-dbt and round-trips follow (spike S3)."""

from __future__ import annotations

from collections.abc import Callable

from semround.errors import UsageError
from semround.targets.base import Compiled, Dropped, Outcome, Refusal, Target
from semround.targets.dbt import DbtTarget
from semround.targets.reference import ReferenceTarget

_REGISTRY: dict[str, Callable[[], Target]] = {"reference": ReferenceTarget, "dbt": DbtTarget}


def register(name: str, factory: Callable[[], Target]) -> None:
    _REGISTRY[name] = factory


def available() -> list[str]:
    return sorted(_REGISTRY)


def create(name: str) -> Target:
    factory = _REGISTRY.get(name)
    if factory is None:
        raise UsageError(f"unknown target {name!r}; available: {', '.join(available())}")
    return factory()


__all__ = ["Compiled", "Dropped", "Outcome", "Refusal", "Target", "available", "create", "register"]

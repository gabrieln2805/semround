"""Target registry. Built-in targets are added as their spikes land (dbt: S2, ossie-dbt: S3)."""

from __future__ import annotations

from collections.abc import Callable

from semround.errors import UsageError
from semround.targets.base import Compiled, Dropped, Outcome, Refusal, Target
from semround.targets.reference import ReferenceTarget

_REGISTRY: dict[str, Callable[[], Target]] = {"reference": ReferenceTarget}


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

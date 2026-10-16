"""``semround.toml``: project settings, type hints, pins and accepted findings."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from semround.errors import ConfigError

DEFAULT_PATH = Path("semround.toml")


class ToleranceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relative: float = Field(1e-9, ge=0)
    absolute: float = Field(1e-6, ge=0)


class AcceptEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^SR-[0-9a-f]{6}$")
    reason: str = Field(min_length=1)


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    models: list[str] = Field(default_factory=list)
    targets: list[str] = Field(default_factory=list)
    rows: int = Field(1000, ge=10, le=1_000_000)
    seed: int = 7
    tolerance: ToleranceConfig = Field(default_factory=ToleranceConfig)
    types: dict[str, str] = Field(default_factory=dict)
    pins: dict[str, str] = Field(default_factory=dict)
    accept: list[AcceptEntry] = Field(default_factory=list)
    spec_dev: bool = False
    traps: list[str] | None = None

    def accepted(self) -> dict[str, str]:
        return {a.id: a.reason for a in self.accept}


def load_config(path: Path | None) -> Config:
    """Read ``path``, or ``./semround.toml`` if present, or return defaults."""
    if path is None:
        if not DEFAULT_PATH.is_file():
            return Config()
        path = DEFAULT_PATH
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return Config.model_validate(data)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: invalid TOML: {e}") from None
    except ValidationError as e:
        lines = [f"  {'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()[:10]]
        raise ConfigError(f"{path}: invalid config:\n" + "\n".join(lines)) from None

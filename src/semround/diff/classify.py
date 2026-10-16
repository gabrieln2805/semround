"""Turn a comparison into finding codes, and name stable finding ids."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from semround.diff.compare import Comparison


class Code(StrEnum):
    DROPPED = "DROPPED"
    DIALECT_FALLBACK = "DIALECT_FALLBACK"
    VALUE_MISMATCH = "VALUE_MISMATCH"
    MISSING_GROUPS = "MISSING_GROUPS"
    EXTRA_GROUPS = "EXTRA_GROUPS"
    FANOUT_UNSAFE = "FANOUT_UNSAFE"
    TARGET_ERROR = "TARGET_ERROR"


SAMPLE_SIZE = 3


@dataclass
class Issue:
    """One code observed on one query, before findings are merged across queries."""

    code: Code
    detail: str
    sample: list[dict[str, Any]] = field(default_factory=list)


def _group(g: tuple) -> list[Any]:
    return list(g)


def classify(cmp: Comparison, fanout: bool) -> list[Issue]:
    if cmp.shape_error:
        return [Issue(Code.TARGET_ERROR, cmp.shape_error)]
    issues = []
    if cmp.missing:
        issues.append(
            Issue(
                Code.MISSING_GROUPS,
                f"{len(cmp.missing)} group(s) in the reference are missing from the target",
                [{"group": _group(g)} for g in cmp.missing[:SAMPLE_SIZE]],
            )
        )
    if cmp.extra or cmp.duplicates:
        extra = cmp.extra + cmp.duplicates
        issues.append(
            Issue(
                Code.EXTRA_GROUPS,
                f"target returned {len(cmp.extra)} unexpected and {len(cmp.duplicates)} duplicated group(s)",
                [{"group": _group(g)} for g in extra[:SAMPLE_SIZE]],
            )
        )
    if cmp.mismatched:
        code = Code.FANOUT_UNSAFE if fanout else Code.VALUE_MISMATCH
        issues.append(
            Issue(
                code,
                f"{len(cmp.mismatched)} group(s) differ",
                [{"group": _group(g), "expected": r, "actual": t} for g, r, t in cmp.mismatched[:SAMPLE_SIZE]],
            )
        )
    return issues


def finding_id(model_path: str, metric: str, target: str, code: Code, trap: str | None) -> str:
    """``SR-`` + 6 hex chars; stable across runs so findings can be accepted in config."""
    key = "|".join([model_path, metric, target, code.value, trap or ""])
    return "SR-" + hashlib.sha256(key.encode()).hexdigest()[:6]

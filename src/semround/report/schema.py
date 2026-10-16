"""Report schema (draft of v1; frozen at Semround 1.0).

Rule I3: a report holds model metadata and fixture-derived values only. There are no
free-form data fields: samples come from the synthetic fixtures, everything else from the model.

``shareable`` is the subset with no sample values. It is the only part a future hosted tier
will accept.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

SCHEMA_VERSION = "1-draft"


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TargetInfo(_Base):
    name: str
    versions: dict[str, str]


class QueryRecord(_Base):
    key: str
    metric: str
    dimensions: list[str]
    status: Literal["ok", "skipped"]
    reason: str | None = None
    fanout: bool = False


class Finding(_Base):
    id: str
    code: str
    metric: str
    target: str
    trap: str | None
    queries: list[str]
    detail: str
    sample: list[dict[str, Any]]
    repro_hint: str
    accepted: bool = False
    accept_reason: str | None = None


class ModelResult(_Base):
    path: str
    name: str
    spec_version: str
    seed: int
    rows: int
    traps: list[str]
    queries: list[QueryRecord]
    findings: list[Finding]
    unsure_types: list[str]
    warnings: list[str]


class Summary(_Base):
    status: Literal["pass", "findings"]
    models: int
    queries: int
    skipped: int
    findings: int
    accepted: int


class ShareableFinding(_Base):
    id: str
    code: str
    metric: str
    target: str
    trap: str | None
    queries: int
    accepted: bool


class Shareable(_Base):
    semround_version: str
    targets: list[TargetInfo]
    summary: Summary
    findings: list[ShareableFinding]


class Report(_Base):
    schema_version: str = SCHEMA_VERSION
    semround_version: str
    targets: list[TargetInfo]
    models: list[ModelResult]
    summary: Summary
    shareable: Shareable

    @property
    def exit_code(self) -> int:
        return 1 if self.summary.status == "findings" else 0


def build_report(semround_version: str, targets: list[TargetInfo], models: list[ModelResult]) -> Report:
    findings = [f for m in models for f in m.findings]
    queries = [q for m in models for q in m.queries]
    open_findings = [f for f in findings if not f.accepted]
    summary = Summary(
        status="findings" if open_findings else "pass",
        models=len(models),
        queries=len(queries),
        skipped=sum(q.status == "skipped" for q in queries),
        findings=len(open_findings),
        accepted=len(findings) - len(open_findings),
    )
    shareable = Shareable(
        semround_version=semround_version,
        targets=targets,
        summary=summary,
        findings=[
            ShareableFinding(
                id=f.id,
                code=f.code,
                metric=f.metric,
                target=f.target,
                trap=f.trap,
                queries=len(f.queries),
                accepted=f.accepted,
            )
            for f in findings
        ],
    )
    return Report(
        semround_version=semround_version, targets=targets, models=models, summary=summary, shareable=shareable
    )

"""The core loop: model -> fixtures -> reference vs each target -> findings -> report."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from semround import __version__
from semround.compiler import compile_query
from semround.compiler.queries import all_queries
from semround.compiler.query import Query
from semround.diff import Code, Issue, Tolerance, classify, compare, finding_id
from semround.engine import Result, load_memory, run
from semround.errors import Unsupported
from semround.fixtures import DEFAULT_TRAPS, FixtureSet, generate
from semround.model import LoadedModel, load_model
from semround.model.types import ColumnType, infer_types, unsure_columns
from semround.report import Finding, ModelResult, QueryRecord, Report, TargetInfo, build_report
from semround.targets import Compiled, Dropped, Refusal, Target, create


@dataclass
class CheckOptions:
    models: list[Path]
    targets: list[str]
    rows: int = 1000
    seed: int = 7
    tolerance: Tolerance = field(default_factory=Tolerance)
    hints: dict[str, str] = field(default_factory=dict)
    accepted: dict[str, str] = field(default_factory=dict)
    traps: tuple[str, ...] = DEFAULT_TRAPS
    allow_dev: bool = False
    model_name: str | None = None


def display_path(path: Path) -> str:
    """Path as stored in reports and hashed into finding ids: relative to cwd when possible."""
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


class Attributor:
    """Names the trap behind a mismatch by re-running both SQLs on fixtures without that trap."""

    def __init__(self, loaded: LoadedModel, types, fixtures: FixtureSet, tol: Tolerance) -> None:
        self._loaded, self._types, self._fixtures, self._tol = loaded, types, fixtures, tol
        self._cons: dict[str, duckdb.DuckDBPyConnection] = {}

    def _without(self, trap: str) -> duckdb.DuckDBPyConnection:
        if trap not in self._cons:
            fx = self._fixtures
            kept = tuple(t for t in fx.traps_enabled if t != trap)
            self._cons[trap] = load_memory(generate(self._loaded, self._types, fx.rows, fx.seed, kept))
        return self._cons[trap]

    def explain(self, ref_sql: str, target_sql: str) -> str | None:
        for trap in self._fixtures.traps_enabled:
            con = self._without(trap)
            try:
                if compare(run(con, ref_sql), run(con, target_sql), self._tol).ok:
                    return trap
            except duckdb.Error:
                continue
        return None

    def close(self) -> None:
        for con in self._cons.values():
            con.close()


def _execute(con: duckdb.DuckDBPyConnection, sql: str) -> Result:
    return run(con, sql)


def check_model(path: Path, targets: list[Target], opts: CheckOptions, workdir: Path) -> ModelResult:
    loaded = load_model(path, opts.model_name, opts.allow_dev)
    types: dict[str, dict[str, ColumnType]] = infer_types(loaded, opts.hints)
    fixtures = generate(loaded, types, opts.rows, opts.seed, opts.traps)
    con = load_memory(fixtures)
    attributor = Attributor(loaded, types, fixtures, opts.tolerance)
    shown = display_path(path)
    findings: dict[str, Finding] = {}
    records: list[QueryRecord] = []

    def add(metric: str, target: str, issue: Issue, trap: str | None, query: Query) -> None:
        fid = finding_id(shown, metric, target, issue.code, trap)
        if fid in findings:
            existing = findings[fid]
            if query.key not in existing.queries:
                existing.queries.append(query.key)
            if metric == "(model)" and issue.detail not in existing.detail:
                # Distinct model-level problems share one id; keep every explanation.
                existing.detail += f" | {issue.detail}"
            return
        findings[fid] = Finding(
            id=fid,
            code=issue.code.value,
            metric=metric,
            target=target,
            trap=trap,
            queries=[query.key],
            detail=issue.detail,
            sample=issue.sample,
            repro_hint=f"semround check {shown} --to {target} --seed {opts.seed} --rows {opts.rows}",
            accepted=fid in opts.accepted,
            accept_reason=opts.accepted.get(fid),
        )

    queries = all_queries(loaded, types)
    ready: list[Target] = []
    for t in targets:
        try:
            t.prepare(loaded, types, workdir / t.name)
            precompile = getattr(t, "precompile", None)
            if precompile is not None:
                precompile(queries)
            ready.append(t)
        except Exception as e:  # a broken converter is a finding, not a crash
            add("(model)", t.name, Issue(Code.TARGET_ERROR, f"prepare failed: {e}"), None, Query("(model)"))
        # Model-level problems a target found while preparing, e.g. constructs it dropped.
        for code, metric, detail in getattr(t, "model_issues", []):
            add(metric, t.name, Issue(Code(code), detail), None, Query(metric))

    try:
        for query in queries:
            try:
                plan, ref_sql = compile_query(loaded, query)
            except Unsupported as u:
                records.append(_record(query, "skipped", str(u)))
                continue
            reference = _execute(con, ref_sql)
            records.append(_record(query, "ok", None, plan.fanout))
            for t in ready:
                for issue, trap in _check_target(t, query, plan.fanout, ref_sql, reference, con, attributor, opts):
                    add(query.metric, t.name, issue, trap, query)
    finally:
        attributor.close()
        con.close()

    ordered = sorted(findings.values(), key=lambda f: (f.metric, f.target, f.code, f.trap or "", f.id))
    return ModelResult(
        path=shown,
        name=loaded.model.name,
        spec_version=loaded.spec_version,
        seed=opts.seed,
        rows=opts.rows,
        traps=list(fixtures.traps_enabled),
        queries=records,
        findings=ordered,
        unsure_types=unsure_columns(types),
        warnings=loaded.warnings,
    )


def _record(query: Query, status: str, reason: str | None, fanout: bool = False) -> QueryRecord:
    return QueryRecord(
        key=query.key,
        metric=query.metric,
        dimensions=[d.label for d in query.dimensions],
        status=status,  # type: ignore[arg-type]
        reason=reason,
        fanout=fanout,
    )


def _check_target(t, query, fanout, ref_sql, reference, con, attributor, opts):
    try:
        outcome = t.compile(query)
    except Exception as e:
        return [(Issue(Code.TARGET_ERROR, f"compile failed: {e}"), None)]
    if isinstance(outcome, Dropped):
        return [(Issue(Code.DROPPED, outcome.reason), None)]
    if isinstance(outcome, Refusal):
        if fanout:
            return []  # refusing a fan-out query is allowed
        return [(Issue(Code.TARGET_ERROR, f"refused an answerable query: {outcome.reason}"), None)]
    assert isinstance(outcome, Compiled)
    try:
        actual = _execute(con, outcome.sql)
    except duckdb.Error as e:
        return [(Issue(Code.TARGET_ERROR, f"SQL failed on DuckDB: {str(e).splitlines()[0]}"), None)]
    cmp = compare(reference, actual, opts.tolerance)
    if cmp.ok:
        return []
    trap = attributor.explain(ref_sql, outcome.sql)
    return [(issue, trap) for issue in classify(cmp, fanout)]


def run_check(opts: CheckOptions) -> Report:
    targets = [create(name) for name in opts.targets]
    with tempfile.TemporaryDirectory(prefix="semround-") as tmp:
        results = [check_model(p, targets, opts, Path(tmp)) for p in opts.models]
    infos = [TargetInfo(name=t.name, versions=t.versions()) for t in targets]
    return build_report(__version__, infos, results)

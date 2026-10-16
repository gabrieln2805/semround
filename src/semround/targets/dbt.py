"""The ``dbt`` target: Ossie JSON -> dbt v1.12 ``osi/`` -> parse -> MetricFlow explain -> SQL.

dbt, MetricFlow and their dependencies live in their own virtual environment (D7), by
default ``.targets/dbt`` (override with ``SEMROUND_DBT_VENV``). Create it with::

    uv venv .targets/dbt
    uv pip install --python .targets/dbt "dbt-core==1.12.*" dbt-duckdb dbt-metricflow

Generated SQL reads ``catalog.schema.table`` exactly as the Ossie ``source`` names it, so it
runs unchanged on Semround's fixtures.

Known upstream behaviour this target reports (dbt-core 1.12.6, metricflow 0.213.0):
- MetricFlow's Ossie converter leaves ``agg_time_dimension`` unset on every metric, and the
  query engine then rejects the whole manifest. The target reports that once, then patches a
  copy of the manifest so the remaining checks can still run; findings say so.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from semround.compiler.query import Query
from semround.model.loader import LoadedModel, read_document
from semround.model.types import ColumnType
from semround.targets.base import Compiled, Dropped, Outcome, Refusal

DRIVER = Path(__file__).parent / "_drivers" / "metricflow_explain.py"
DEFAULT_VENV = Path(".targets") / "dbt"

TIME_SPINE_SQL = (
    "select cast(range as date) as date_day\nfrom range(date '2000-01-01', date '2040-01-01', interval 1 day)\n"
)
TIME_SPINE_YML = """models:
  - name: metricflow_time_spine
    time_spine:
      standard_granularity_column: date_day
    columns:
      - name: date_day
        granularity: day
"""
SCHEMA_MACRO = (
    "{% macro generate_schema_name(custom_schema_name, node) -%}\n"
    "{{ custom_schema_name or target.schema }}\n"
    "{%- endmacro %}\n"
)
# Rule I1: no network from the toolchain either.
QUIET_ENV = {"DBT_SEND_ANONYMOUS_USAGE_STATS": "false", "DBT_VERSION_CHECK": "false", "DO_NOT_TRACK": "1"}


def _venv() -> Path:
    return Path(os.environ.get("SEMROUND_DBT_VENV", DEFAULT_VENV))


def _exe(venv: Path, name: str) -> Path:
    return (
        venv / ("Scripts" if sys.platform == "win32" else "bin") / (f"{name}.exe" if sys.platform == "win32" else name)
    )


class DbtTarget:
    name = "dbt"

    def __init__(self) -> None:
        self.model_issues: list[tuple[str, str, str]] = []
        self._sql: dict[str, Outcome] = {}
        self._versions: dict[str, str] = {}
        self._metrics: set[str] = set()
        self._entities: dict[str, str] = {}

    # -- setup ---------------------------------------------------------------------------

    def prepare(self, loaded: LoadedModel, types: dict[str, dict[str, ColumnType]], workdir: Path) -> None:
        venv = _venv()
        if not _exe(venv, "dbt").exists():
            raise RuntimeError(
                f"dbt target is not installed in {venv}; run: uv venv {venv} && "
                f'uv pip install --python {venv} "dbt-core==1.12.*" dbt-duckdb dbt-metricflow'
            )
        if loaded.spec_version not in ("0.1.0", "0.1.1"):
            raise RuntimeError(f"dbt v1.12 reads Ossie 0.1.0/0.1.1 only, not {loaded.spec_version}")
        self._loaded = loaded
        self._workdir = workdir
        project = workdir / "project"
        if project.exists():
            shutil.rmtree(project)
        self._write_project(loaded, project)
        self._parse(venv, project)
        manifest_path = project / "target" / "semantic_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self._index_manifest(loaded, manifest)
        self._manifest_path = self._patch_agg_time(manifest, workdir / "semantic_manifest.patched.json")

    def _write_project(self, loaded: LoadedModel, project: Path) -> None:
        (project / "osi").mkdir(parents=True)
        (project / "models").mkdir()
        (project / "macros").mkdir()
        doc = read_document(loaded.path)
        (project / "osi" / f"{loaded.model.name}.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")

        catalogs = sorted({s.catalog or "semround" for s in loaded.sources.values()})
        primary = loaded.sources[loaded.model.datasets[0].name].catalog or "semround"
        for ds in loaded.model.datasets:
            src = loaded.sources[ds.name]
            config = f"database='{src.catalog or primary}', schema='{src.schema or 'main'}', alias='{src.table}'"
            # Placeholder bodies: dbt only parses the project; the fixtures hold the data.
            (project / "models" / f"{ds.name}.sql").write_text(
                f"{{{{ config({config}) }}}}\nselect 1 as placeholder\n", encoding="utf-8"
            )
        (project / "models" / "metricflow_time_spine.sql").write_text(TIME_SPINE_SQL, encoding="utf-8")
        (project / "models" / "metricflow_time_spine.yml").write_text(TIME_SPINE_YML, encoding="utf-8")
        (project / "macros" / "generate_schema_name.sql").write_text(SCHEMA_MACRO, encoding="utf-8")
        (project / "dbt_project.yml").write_text(
            "name: semround_target\nversion: '1.0'\nprofile: semround\nmodel-paths: [models]\nmacro-paths: [macros]\n",
            encoding="utf-8",
        )
        attach = "".join(
            f"\n        - path: '{(project / f'{c}.duckdb').as_posix()}'\n          alias: {c}"
            for c in catalogs
            if c != primary
        )
        profile = (
            "semround:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n"
            f"      path: '{(project / f'{primary}.duckdb').as_posix()}'\n      schema: main\n      threads: 1\n"
        )
        if attach:
            profile += f"      attach:{attach}\n"
        (project / "profiles.yml").write_text(profile, encoding="utf-8")

    def _parse(self, venv: Path, project: Path) -> None:
        env = {**os.environ, **QUIET_ENV, "DBT_PROFILES_DIR": str(project)}
        proc = subprocess.run(
            [str(_exe(venv, "dbt")), "--no-send-anonymous-usage-stats", "--no-version-check", "parse"],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
        if proc.returncode != 0:
            lines = [ln for ln in _strip_ansi(proc.stdout + proc.stderr).splitlines() if ln.strip()]
            wanted = [ln for ln in lines if "WARNING" in ln or "Error" in ln or "error" in ln][-6:]
            raise RuntimeError("dbt parse failed: " + " | ".join(_after_timestamp(x) for x in wanted or lines[-4:]))

    def _index_manifest(self, loaded: LoadedModel, manifest: dict) -> None:
        self._metrics = {m["name"] for m in manifest.get("metrics", [])}
        for sm in manifest.get("semantic_models", []):
            primary = [e["name"] for e in sm.get("entities", []) if e.get("type") == "primary"]
            if primary:
                self._entities[sm["name"].lower()] = primary[0]
        for metric in loaded.model.metrics:
            if metric.expression.portable() is None and metric.name in self._metrics:
                have = sorted({d.dialect for d in metric.expression.dialects})
                self.model_issues.append(
                    (
                        "DIALECT_FALLBACK",
                        metric.name,
                        f"metric has only {have} expressions, but dbt kept it as portable SQL",
                    )
                )

    def _patch_agg_time(self, manifest: dict, out: Path) -> Path:
        missing = [
            m["name"]
            for m in manifest.get("metrics", [])
            if m.get("type") == "simple"
            and not (m["type_params"].get("metric_aggregation_params") or {}).get("agg_time_dimension")
        ]
        models_without_default = [
            sm for sm in manifest.get("semantic_models", []) if not (sm.get("defaults") or {}).get("agg_time_dimension")
        ]
        if missing and models_without_default:
            unpatchable = []
            for sm in models_without_default:
                times = [d["name"] for d in sm.get("dimensions", []) if d.get("type") == "time"]
                if times:
                    sm["defaults"] = {**(sm.get("defaults") or {}), "agg_time_dimension": times[0]}
                else:
                    unpatchable.append(sm["name"])
            note = (
                f" Semantic model(s) {', '.join(sorted(unpatchable))} have no time dimension of their own"
                " (Ossie keeps time in a joined dataset), so they cannot be patched."
                if unpatchable
                else ""
            )
            self.model_issues.append(
                (
                    "TARGET_ERROR",
                    "(model)",
                    f"MetricFlow rejects the semantic manifest: {len(missing)} metric(s) have no agg_time_dimension "
                    "and no semantic model sets a default, so no metric can be queried. Semround patched a copy "
                    "(first time dimension of each semantic model) to run the remaining checks." + note,
                )
            )
        out.write_text(json.dumps(manifest), encoding="utf-8")
        return out

    # -- queries -------------------------------------------------------------------------

    def _group_by(self, query: Query) -> list[str] | None:
        names = []
        for d in query.dimensions:
            entity = self._entities.get(d.dataset.lower())
            if entity is None:
                return None
            names.append(f"{entity}__{d.field}" + (f"__{d.grain}" if d.grain else ""))
        return names

    def precompile(self, queries: list[Query]) -> None:
        requests = []
        for i, q in enumerate(queries):
            if q.metric not in self._metrics:
                self._sql[q.key] = Dropped("metric is missing from dbt's semantic manifest")
                continue
            group_by = self._group_by(q)
            if group_by is None:
                self._sql[q.key] = Refusal("a dimension's dataset has no primary entity in dbt's manifest")
                continue
            requests.append({"id": str(i), "key": q.key, "metrics": [q.metric], "group_by": group_by})
        if not requests:
            return
        payload = {"semantic_manifest": str(self._manifest_path), "queries": requests}
        proc = subprocess.run(
            [str(_exe(_venv(), "python")), str(DRIVER)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, **QUIET_ENV},
            timeout=900,
        )
        if proc.returncode != 0:
            tail = [ln for ln in proc.stderr.splitlines() if ln.strip()][-1:] or ["no output"]
            raise RuntimeError(f"MetricFlow driver failed: {tail[0][:500]}")
        response = json.loads(proc.stdout)
        self._versions = response.get("versions", {})
        if "fatal" in response:
            raise RuntimeError(f"MetricFlow cannot load dbt's semantic manifest: {response['fatal']}")
        keys = {r["id"]: r["key"] for r in requests}
        by_id = {r["id"]: r for r in requests}
        for r in response["results"]:
            if "sql" in r:
                # MetricFlow orders output columns its own way; project them into the target
                # contract (dimensions in query order, then the metric) by MetricFlow's aliases.
                req = by_id[r["id"]]
                cols = ", ".join(f'"{c}"' for c in [*req["group_by"], *req["metrics"]])
                self._sql[keys[r["id"]]] = Compiled(f"SELECT {cols} FROM (\n{r['sql']}\n) AS mf")
            else:
                self._sql[keys[r["id"]]] = Refusal(f"MetricFlow: {r['error'][:300]}")

    def compile(self, query: Query) -> Outcome:
        # A MetricFlow refusal of an answerable query becomes a finding in check.py.
        return self._sql.get(query.key) or Refusal("query was not precompiled")

    def versions(self) -> dict[str, str]:
        if self._versions:
            return self._versions
        return {"dbt-core": "not run"}


def _strip_ansi(text: str) -> str:
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _after_timestamp(line: str) -> str:
    parts = line.split("  ", 1)
    return parts[1].strip() if len(parts) == 2 and parts[0].count(":") == 2 else line.strip()

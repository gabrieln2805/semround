"""MetricFlow driver: runs inside the dbt target's own virtual environment (D7).

Never imported by Semround itself. Reads one JSON request on stdin, writes one JSON
response on stdout:

    {"semantic_manifest": "<path>", "list_dimensions": ["metric", ...],
     "queries": [{"id": "q1", "metrics": ["m"], "group_by": ["entity__dim__month"]}]}
->  {"versions": {...}, "dimensions": {"metric": ["..."]},
     "results": [{"id": "q1", "sql": "..."} | {"id": "q1", "error": "..."}]}

Uses the Python API rather than the ``mf`` CLI: ``--explain`` needs no warehouse connection,
and the CLI fails on Windows.
"""

from __future__ import annotations

import json
import sys
from importlib.metadata import PackageNotFoundError, version


def _version(dist: str) -> str:
    try:
        return version(dist)
    except PackageNotFoundError:
        return "not installed"


def _sql_client():
    from metricflow.protocols.sql_client import SqlEngine
    from metricflow.sql.render.duckdb_renderer import DuckDbSqlPlanRenderer

    class ExplainOnlyClient:
        """Renders DuckDB SQL; never connects to anything."""

        sql_engine_type = SqlEngine.DUCKDB
        sql_plan_renderer = DuckDbSqlPlanRenderer()

        def __getattr__(self, name):
            raise RuntimeError(f"explain-only client: {name} is not available")

    return ExplainOnlyClient()


def main() -> None:
    from metricflow.engine.metricflow_engine import MetricFlowEngine, MetricFlowQueryRequest
    from metricflow_semantics.model.dbt_manifest_parser import parse_manifest_from_dbt_generated_manifest
    from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup

    request = json.load(sys.stdin)
    response: dict = {
        "versions": {p: _version(p) for p in ("dbt-core", "metricflow", "dbt-metricflow", "dbt-duckdb")},
        "dimensions": {},
        "results": [],
    }
    try:
        with open(request["semantic_manifest"], encoding="utf-8") as f:
            manifest = parse_manifest_from_dbt_generated_manifest(f.read())
        engine = MetricFlowEngine(SemanticManifestLookup(manifest), _sql_client())
    except Exception as e:  # the manifest itself is unusable: report, don't crash
        response["fatal"] = f"{type(e).__name__}: {str(e).strip().splitlines()[0][:300]}"
        json.dump(response, sys.stdout)
        return
    for metric in request.get("list_dimensions", []):
        try:
            dims = engine.simple_dimensions_for_metrics([metric])
            response["dimensions"][metric] = sorted(d.dunder_name for d in dims)
        except Exception as e:  # report, keep going
            response["dimensions"][metric] = {"error": f"{type(e).__name__}: {e}"}
    for q in request.get("queries", []):
        try:
            result = engine.explain(
                MetricFlowQueryRequest.create(metric_names=q["metrics"], group_by_names=q["group_by"])
            )
            response["results"].append({"id": q["id"], "sql": result.sql_statement.sql})
        except Exception as e:
            response["results"].append({"id": q["id"], "error": f"{type(e).__name__}: {str(e)[:2000]}"})
    json.dump(response, sys.stdout)


if __name__ == "__main__":
    main()

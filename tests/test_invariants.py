"""Architecture rules I1-I7 that can be checked statically (see the technical plan).

I1 (no network during check) is enforced for the whole suite by pytest-socket
(``--disable-socket`` in pyproject). I6 (licences) runs as a CI step with pip-licenses.
"""

from __future__ import annotations

import ast
import json
import socket
from pathlib import Path

import pytest
from conftest import SHOP
from pytest_socket import SocketBlockedError

from semround.check import CheckOptions, run_check
from semround.report.schema import Report

SRC = Path(__file__).resolve().parent.parent / "src" / "semround"

# I2: no warehouse connectors. I4: no telemetry / HTTP clients in the CLI.
BANNED = {
    "snowflake",
    "databricks",
    "google.cloud",
    "psycopg",
    "psycopg2",
    "pyodbc",
    "pymysql",
    "redshift_connector",
    "sqlalchemy",
    "requests",
    "httpx",
    "aiohttp",
    "urllib3",
    "urllib.request",
    "http.client",
    "socket",
    "sentry_sdk",
    "posthog",
    "segment",
    "mixpanel",
}


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
    return names


@pytest.mark.parametrize("path", sorted(SRC.rglob("*.py")), ids=lambda p: p.relative_to(SRC).as_posix())
def test_i2_i4_no_banned_imports(path: Path) -> None:
    bad = [n for n in _imports(path) if any(n == b or n.startswith(b + ".") for b in BANNED)]
    assert not bad, f"{path.name} imports {bad}"


def test_i1_sockets_are_disabled_in_tests() -> None:
    with pytest.raises(SocketBlockedError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)


def test_i3_report_has_no_free_form_fields() -> None:
    # Every model in the schema forbids extra keys, so nothing unplanned can be added.
    def walk(schema: dict) -> None:
        for name, sub in schema.get("$defs", {}).items():
            if sub.get("type") == "object":
                assert sub.get("additionalProperties") is False, name

    walk(Report.model_json_schema())


def test_i3_samples_come_from_fixtures(mutant_targets) -> None:
    from semround.fixtures import generate
    from semround.model import load_model
    from semround.model.types import infer_types

    report = run_check(CheckOptions(models=[SHOP], targets=["naive-inner"], rows=200, seed=3))
    loaded = load_model(SHOP)
    fx = generate(loaded, infer_types(loaded), 200, 3)
    fixture_values = {str(v) for t in fx.tables for r in t.rows for v in r if isinstance(v, str)}
    for f in report.models[0].findings:
        for s in f.sample:
            for v in s["group"]:
                if isinstance(v, str) and not v[:4].isdigit():  # dates are derived by truncation
                    assert v in fixture_values, v


def test_i7_shareable_has_no_scores() -> None:
    report = run_check(CheckOptions(models=[SHOP], targets=["reference"], rows=100))
    text = json.dumps(report.shareable.model_dump()).lower()
    assert "score" not in text and "rank" not in text

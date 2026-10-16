from __future__ import annotations

import json
from pathlib import Path

from conftest import SHOP, TPCDS_011, TPCDS_020
from typer.testing import CliRunner

from semround.cli import app

runner = CliRunner()


def test_check_passes_with_exit_0(tmp_path: Path) -> None:
    out = tmp_path / "r.json"
    res = runner.invoke(app, ["check", str(TPCDS_011), "--to", "reference", "--json", str(out)])
    assert res.exit_code == 0, res.output
    assert "Semround: PASS" in res.output
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["summary"]["status"] == "pass"
    assert "sample" not in json.dumps(report["shareable"])  # shareable subset has no values


def test_findings_exit_1_and_accepting_them_exits_0(tmp_path: Path, mutant_targets) -> None:
    out = tmp_path / "r.json"
    res = runner.invoke(app, ["check", str(SHOP), "--to", "drop-distinct", "--json", str(out)])
    assert res.exit_code == 1, res.output
    ids = sorted({f["id"] for f in json.loads(out.read_text(encoding="utf-8"))["models"][0]["findings"]})
    assert ids and all(i.startswith("SR-") and len(i) == 9 for i in ids)

    cfg = tmp_path / "semround.toml"
    cfg.write_text(
        "".join(f'[[accept]]\nid = "{i}"\nreason = "converter has no COUNT DISTINCT yet"\n' for i in ids),
        encoding="utf-8",
    )
    res = runner.invoke(app, ["check", str(SHOP), "--to", "drop-distinct", "--config", str(cfg)])
    assert res.exit_code == 0, res.output
    assert "Accepted:" in res.output


def test_reports_are_byte_identical(tmp_path: Path) -> None:
    paths = [tmp_path / "a.json", tmp_path / "b.json"]
    for p in paths:
        assert runner.invoke(app, ["check", str(SHOP), "--to", "reference", "--json", str(p)]).exit_code == 0
    assert paths[0].read_bytes() == paths[1].read_bytes()


def test_usage_errors_exit_2(tmp_path: Path) -> None:
    assert runner.invoke(app, ["check", str(SHOP), "--to", "nope"]).exit_code == 2
    assert runner.invoke(app, ["check", str(tmp_path / "missing.yaml"), "--to", "reference"]).exit_code == 2
    assert runner.invoke(app, ["check", str(TPCDS_020), "--to", "reference"]).exit_code == 2  # needs --spec-dev
    bad = tmp_path / "bad.toml"
    bad.write_text("rows = 'many'\n", encoding="utf-8")
    assert runner.invoke(app, ["check", str(SHOP), "--to", "reference", "-c", str(bad)]).exit_code == 2


def test_compile_prints_sql() -> None:
    res = runner.invoke(app, ["compile", str(SHOP), "--metric", "revenue", "--by", "region,ordered_at:month"])
    assert res.exit_code == 0, res.output
    assert "date_trunc('month'" in res.output and res.output.rstrip().endswith(";")


def test_fixtures_writes_manifest(tmp_path: Path) -> None:
    res = runner.invoke(app, ["fixtures", str(SHOP), "--out", str(tmp_path), "--rows", "100"])
    assert res.exit_code == 0, res.output
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["catalogs"] == {"shop": "shop.duckdb"}
    assert (tmp_path / "shop.duckdb").is_file()


def test_targets_list() -> None:
    res = runner.invoke(app, ["targets", "list"])
    assert res.exit_code == 0 and "reference" in res.output

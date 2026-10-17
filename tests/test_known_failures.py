"""Known upstream failures in corpus/known/, each with the findings Semround must report."""

from __future__ import annotations

import pytest
from conftest import CORPUS

from semround.check import CheckOptions, run_check
from semround.targets.dbt import _exe, _venv

ISSUE_462 = CORPUS / "known" / "issue_462_thoughtspot_tpcds.yaml"
METRICS_462 = {
    "total_sales",
    "total_profit",
    "customer_lifetime_value",
    "sales_by_brand",
    "store_productivity",
    "avg_price_adjustment",
    "profit_margin",
    "prior_period_profit",
}


def test_462_every_metric_reported_dropped(mutant_targets) -> None:
    # apache/ossie#462: ThoughtSpot exports metrics only in the THOUGHTSPOT dialect, so a
    # converter that needs portable SQL drops all of them.
    report = run_check(CheckOptions(models=[ISSUE_462], targets=["reference", "portable-only"], rows=100))
    findings = report.models[0].findings
    assert {f.metric for f in findings if f.code == "DROPPED" and f.target == "portable-only"} == METRICS_462
    assert not [f for f in findings if f.target == "reference"]  # the answer key is never judged
    assert report.exit_code == 1


def test_462_finding_ids_are_stable(mutant_targets) -> None:
    def ids() -> list[str]:
        report = run_check(CheckOptions(models=[ISSUE_462], targets=["portable-only"], rows=50))
        return sorted(f.id for f in report.models[0].findings)

    first = ids()
    assert first == ids()
    assert len(first) == len(METRICS_462)


@pytest.mark.skipif(not _exe(_venv(), "dbt").exists(), reason="dbt target venv not installed")
def test_462_dbt_rejects_the_whole_document() -> None:
    # dbt v1.12 only knows the 0.1.1 dialects, so a ThoughtSpot export fails to parse at all.
    report = run_check(CheckOptions(models=[ISSUE_462], targets=["dbt"], rows=50))
    (finding,) = report.models[0].findings
    assert finding.code == "TARGET_ERROR" and "not a valid enumeration member" in finding.detail

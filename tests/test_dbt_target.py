"""E2E: the real dbt v1.12 + MetricFlow toolchain in its own venv (skipped when not installed).

These assert today's upstream behaviour (dbt-core 1.12.6, metricflow 0.213.0). When upstream
fixes a bug, the matching assertion fails on purpose: update the expectation and close the
upstream issue's loop.
"""

from __future__ import annotations

import pytest
from conftest import CORPUS, SHOP, TPCDS_011

from semround.check import CheckOptions, run_check
from semround.targets.dbt import _exe, _venv

pytestmark = pytest.mark.skipif(not _exe(_venv(), "dbt").exists(), reason="dbt target venv not installed")

SHOP_SMALL = CORPUS / "traps" / "shop_small.yaml"


@pytest.fixture(scope="module")
def shop_small():
    report = run_check(CheckOptions(models=[SHOP_SMALL], targets=["dbt"], rows=400))
    return {f.metric: f for f in report.models[0].findings}, report


def test_plain_metrics_match_the_reference(shop_small) -> None:
    findings, report = shop_small
    for metric in ("revenue", "order_count", "largest_order"):
        assert metric not in findings, findings[metric]
    assert report.targets[0].versions["dbt-core"].startswith("1.12")


def test_agg_time_dimension_blocker_is_reported(shop_small) -> None:
    findings, _ = shop_small
    assert findings["(model)"].code == "TARGET_ERROR"
    assert "agg_time_dimension" in findings["(model)"].detail


def test_expression_split_on_dot(shop_small) -> None:
    # AVG(orders.amount + COALESCE(orders.shipping_fee, 0)) is split at a '.' by the converter.
    findings, _ = shop_small
    assert findings["avg_order_value"].code == "TARGET_ERROR"


def test_qualified_columns_in_case_metric_do_not_bind(shop_small) -> None:
    findings, _ = shop_small
    assert findings["completed_revenue"].code == "TARGET_ERROR"
    assert "Binder Error" in findings["completed_revenue"].detail


def test_composite_primary_keys_fail_dbt_parse() -> None:
    report = run_check(CheckOptions(models=[SHOP], targets=["dbt"], rows=100))
    (finding,) = report.models[0].findings
    assert "only one primary entity" in finding.detail


def test_tpcds_metrics_cannot_be_queried() -> None:
    report = run_check(CheckOptions(models=[TPCDS_011], targets=["dbt"], rows=100))
    (finding,) = report.models[0].findings
    assert "store_sales" in finding.detail and "agg_time_dimension" in finding.detail

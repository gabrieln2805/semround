"""Mutation check: every deliberately broken converter must be caught, with the right trap."""

from __future__ import annotations

import pytest
from conftest import SHOP, TPCDS_011

from semround.check import CheckOptions, run_check


@pytest.fixture(scope="module")
def shop_report(request):
    from mutants import ALL

    from semround import targets

    for m in ALL:
        targets.register(m.name, m)
    try:
        report = run_check(CheckOptions(models=[SHOP], targets=["reference"] + [m.name for m in ALL], rows=400))
    finally:
        for m in ALL:
            targets._REGISTRY.pop(m.name, None)
    return report.models[0].findings


def _codes(findings, target: str) -> set[tuple[str, str | None]]:
    return {(f.code, f.trap) for f in findings if f.target == target}


def test_reference_target_is_clean(shop_report) -> None:
    assert _codes(shop_report, "reference") == set()


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("naive", {("FANOUT_UNSAFE", None)}),
        ("naive-inner", {("VALUE_MISMATCH", "orphan_fk"), ("MISSING_GROUPS", "orphan_fk")}),
        ("sunday-weeks", {("VALUE_MISMATCH", "weekend_dates")}),
        ("utc-shift", {("VALUE_MISMATCH", "month_end")}),
        ("drop-filter", {("VALUE_MISMATCH", None)}),
        ("drop-distinct", {("DROPPED", None)}),
    ],
)
def test_mutant_is_caught(shop_report, target: str, expected: set) -> None:
    found = _codes(shop_report, target)
    assert expected <= found, found


def test_drop_filter_only_hits_filtered_metric(shop_report) -> None:
    metrics = {f.metric for f in shop_report if f.target == "drop-filter"}
    assert metrics == {"completed_revenue"}


def test_tpcds_reference_passes() -> None:
    report = run_check(CheckOptions(models=[TPCDS_011], targets=["reference"]))
    assert report.summary.status == "pass"
    assert report.summary.queries == 80

"""Reference compiler: known answers on hand-written rows, plus golden SQL snapshots.

Set SEMROUND_UPDATE_GOLDEN=1 to rewrite the snapshots after an intended change.
"""

from __future__ import annotations

import datetime as dt
import os
import textwrap
from decimal import Decimal
from pathlib import Path

import pytest
from conftest import SHOP, TPCDS_011, TPCDS_020

from semround.compiler import Query, compile_query, parse_dimensions
from semround.engine import load_memory, run
from semround.errors import Unsupported, UsageError
from semround.fixtures.data import FixtureSet, Table
from semround.model import load_model
from semround.model.types import ColumnType, Kind

GOLDEN = Path(__file__).parent / "golden"

TINY = """
version: "0.1.1"
semantic_model:
  - name: tiny
    datasets:
      - name: orders
        source: t.main.orders
        primary_key: [id]
        fields:
          - {name: id, expression: {dialects: [{dialect: ANSI_SQL, expression: id}]}}
          - {name: cust, expression: {dialects: [{dialect: ANSI_SQL, expression: cust}]}}
          - {name: amount, expression: {dialects: [{dialect: ANSI_SQL, expression: amount}]}}
          - name: day
            expression: {dialects: [{dialect: ANSI_SQL, expression: day}]}
            dimension: {is_time: true}
      - name: customers
        source: t.main.customers
        primary_key: [cust]
        fields:
          - {name: cust, expression: {dialects: [{dialect: ANSI_SQL, expression: cust}]}}
          - name: region
            expression: {dialects: [{dialect: ANSI_SQL, expression: region}]}
            dimension: {is_time: false}
      - name: items
        source: t.main.items
        primary_key: [order_id, line]
        fields:
          - {name: order_id, expression: {dialects: [{dialect: ANSI_SQL, expression: order_id}]}}
          - {name: line, expression: {dialects: [{dialect: ANSI_SQL, expression: line}]}}
          - name: wrap
            expression: {dialects: [{dialect: ANSI_SQL, expression: wrap}]}
            dimension: {is_time: false}
    relationships:
      - {name: o_c, from: orders, to: customers, from_columns: [cust], to_columns: [cust]}
      - {name: i_o, from: items, to: orders, from_columns: [order_id], to_columns: [id]}
    metrics:
      - name: revenue
        expression: {dialects: [{dialect: ANSI_SQL, expression: SUM(orders.amount)}]}
      - name: orders_per_customer
        expression: {dialects: [{dialect: ANSI_SQL, expression: "COUNT(orders.id) / COUNT(customers.cust)"}]}
      - name: running
        expression: {dialects: [{dialect: ANSI_SQL, expression: "SUM(SUM(orders.amount)) OVER (ORDER BY orders.day)"}]}
"""


def _t(kind: Kind, sql: str) -> ColumnType:
    return ColumnType(kind, sql, "hint", True)


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    path = tmp_path_factory.mktemp("tiny") / "tiny.yaml"
    path.write_text(textwrap.dedent(TINY), encoding="utf-8")
    loaded = load_model(path)
    INT, DEC, STR, DATE = (
        _t(Kind.INTEGER, "BIGINT"),
        _t(Kind.DECIMAL, "DECIMAL(18,2)"),
        _t(Kind.STRING, "VARCHAR"),
        _t(Kind.DATE, "DATE"),
    )
    day = dt.date(2024, 3, 6)  # a Wednesday
    tables = [
        Table(
            "orders",
            loaded.sources["orders"],
            ["id", "cust", "amount", "day"],
            {"id": INT, "cust": INT, "amount": DEC, "day": DATE},
            [
                [1, 10, Decimal("10.00"), day],
                [2, 10, Decimal("20.00"), day],
                [3, 99, Decimal("5.00"), day],  # orphan customer
                [4, 20, None, dt.date(2024, 3, 10)],  # Sunday; NULL amount
            ],
        ),
        Table(
            "customers",
            loaded.sources["customers"],
            ["cust", "region"],
            {"cust": INT, "region": STR},
            [[10, "EU"], [20, None], [30, "US"]],
        ),
        Table(
            "items",
            loaded.sources["items"],
            ["order_id", "line", "wrap"],
            {"order_id": INT, "line": INT, "wrap": STR},
            [[1, 1, "Y"], [1, 2, "N"], [1, 3, "Y"], [2, 1, "N"]],
        ),
    ]
    fx = FixtureSet("tiny", "0.1.1", 0, 4, (), tables, [], "t")
    return loaded, load_memory(fx)


def _ask(tiny, metric: str, by: str = ""):
    loaded, con = tiny
    plan, sql = compile_query(loaded, Query(metric, parse_dimensions(by, loaded.model)))
    return plan, run(con, sql).rows


def test_total(tiny) -> None:
    assert _ask(tiny, "revenue")[1] == [(Decimal("35.00"),)]


def test_left_join_keeps_orphans_in_the_null_group(tiny) -> None:
    # Orphan order 3 (5.00) and customer 20's NULL region (amount NULL) share the NULL group.
    _, rows = _ask(tiny, "revenue", "region")
    assert rows == [(None, Decimal("5.00")), ("EU", Decimal("30.00"))]


def test_fan_out_counts_each_order_once_per_group(tiny) -> None:
    # Order 1 has items Y, N, Y: it counts once in Y and once in N, never twice in Y.
    # Orders 3 and 4 have no items, so they form the NULL group: 5.00 + NULL = 5.00.
    plan, rows = _ask(tiny, "revenue", "wrap")
    assert plan.fanout
    assert rows == [(None, Decimal("5.00")), ("N", Decimal("30.00")), ("Y", Decimal("10.00"))]


def test_each_aggregate_at_its_own_grain(tiny) -> None:
    # COUNT(orders.id) by region: EU 2, NULL 2 (orphan + NULL-region customer).
    # COUNT(customers.cust) by region: EU 1, NULL 1, US 1. US has no orders -> 0 / 1.
    _, rows = _ask(tiny, "orders_per_customer", "region")
    assert rows == [(None, 2.0), ("EU", 2.0), ("US", 0.0)]


def test_iso_weeks_start_monday(tiny) -> None:
    _, rows = _ask(tiny, "revenue", "day:week")
    weeks = [r[0].date() for r in rows]
    assert weeks == [dt.date(2024, 3, 4)]  # Wed 6th and Sun 10th are in the same ISO week


def test_window_metrics_are_unsupported(tiny) -> None:
    with pytest.raises(Unsupported, match="window"):
        _ask(tiny, "running")


def test_unknown_dimension_is_a_usage_error(tiny) -> None:
    with pytest.raises(UsageError, match="unknown dimension"):
        _ask(tiny, "revenue", "nope")


CASES = [
    ("tpcds_total_sales_by_state_week", TPCDS_011, False, "total_sales", "s_state,d_date:week"),
    ("tpcds_store_productivity_by_state", TPCDS_011, False, "store_productivity", "s_state"),
    ("tpcds_clv_by_state_fanout", TPCDS_020, True, "customer_lifetime_value", "s_state"),
    ("shop_units_per_order_by_region", SHOP, False, "units_per_order", "region"),
    ("shop_revenue_by_gift_wrap_fanout", SHOP, False, "revenue", "gift_wrap"),
]


@pytest.mark.parametrize(("name", "path", "dev", "metric", "by"), CASES, ids=[c[0] for c in CASES])
def test_golden_sql(name: str, path: Path, dev: bool, metric: str, by: str) -> None:
    loaded = load_model(path, allow_dev=dev)
    sql = compile_query(loaded, Query(metric, parse_dimensions(by, loaded.model)))[1] + "\n"
    snapshot = GOLDEN / f"{name}.sql"
    if os.environ.get("SEMROUND_UPDATE_GOLDEN") or not snapshot.exists():
        snapshot.write_text(sql, encoding="utf-8", newline="\n")
    assert sql == snapshot.read_text(encoding="utf-8")

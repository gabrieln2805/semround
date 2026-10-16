from __future__ import annotations

from conftest import SHOP, TPCDS_011, TPCDS_020

from semround.model import load_model
from semround.model.types import Kind, infer_types, parse_type_hint, unsure_columns


def test_spike_s1_tpcds_types_without_hints() -> None:
    """S1 exit criterion: >= 80% of the TPC-DS example typed correctly without hints.

    Ground truth is the ``datatype`` the 0.2.0.dev0 example declares for the same columns;
    the 0.1.1 example declares none, so inference has nothing but structure and names.
    """
    truth_model = load_model(TPCDS_020, allow_dev=True).model
    truth = {(d.name, f.name.lower()): f.datatype for d in truth_model.datasets for f in d.fields if f.datatype}
    inferred = infer_types(load_model(TPCDS_011))
    scored = [
        (ct.kind.value, truth[(ds, c)]) for ds, cols in inferred.items() for c, ct in cols.items() if (ds, c) in truth
    ]
    correct = sum(a == b for a, b in scored)
    assert len(scored) == 28
    assert correct / len(scored) >= 0.8, f"{correct}/{len(scored)}"


def test_declared_datatypes_win() -> None:
    inferred = infer_types(load_model(TPCDS_020, allow_dev=True))
    t = inferred["store_sales"]["ss_ext_sales_price"]
    assert (t.kind, t.rule, t.sure) == (Kind.DECIMAL, "declared", True)


def test_hints_override_everything() -> None:
    inferred = infer_types(load_model(TPCDS_011), {"store_sales.ss_quantity": "decimal(10,4)"})
    t = inferred["store_sales"]["ss_quantity"]
    assert (t.kind, t.sql_type, t.rule) == (Kind.DECIMAL, "decimal(10,4)", "hint")


def test_usage_rules() -> None:
    inferred = infer_types(load_model(SHOP))
    assert inferred["orders"]["status"].kind is Kind.STRING  # compared with a string literal
    assert inferred["orders"]["channel"].kind is Kind.STRING  # inside LOWER()
    assert inferred["order_items"]["unit_price"].kind is Kind.DECIMAL  # arithmetic inside SUM
    assert inferred["order_items"]["quantity"].kind is Kind.INTEGER  # numeric use + name
    assert inferred["customers"]["signup_date"].kind is Kind.DATE  # is_time
    assert "orders.ordered_at" in unsure_columns(inferred)


def test_parse_type_hint() -> None:
    assert parse_type_hint("DECIMAL(18,2)") is Kind.DECIMAL
    assert parse_type_hint("timestamp with time zone") is Kind.TIMESTAMP
    assert parse_type_hint("varchar") is Kind.STRING

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from conftest import SHOP, TPCDS_011, TPCDS_020

from semround.errors import ModelError, UsageError
from semround.model import load_model
from semround.model.spec import DialectExpression, Expression


def write(tmp_path: Path, text: str, name: str = "m.yaml") -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return p


def test_loads_both_document_shapes() -> None:
    old = load_model(TPCDS_011)
    new = load_model(TPCDS_020, allow_dev=True)
    assert old.spec_version == "0.1.1" and new.spec_version == "0.2.0.dev0"
    assert old.model.name == new.model.name == "tpcds_retail_model"
    assert [d.name for d in old.model.datasets] == [d.name for d in new.model.datasets]


def test_dev_spec_needs_flag() -> None:
    with pytest.raises(UsageError, match="--spec-dev"):
        load_model(TPCDS_020)


def test_key_columns_that_are_not_fields_become_physical_columns() -> None:
    # TPC-DS declares ss_ticket_number in the primary key but never as a field.
    loaded = load_model(TPCDS_011)
    assert loaded.primary_key("store_sales") == ["ss_item_sk", "ss_ticket_number"]
    assert "ss_ticket_number" in loaded.columns["store_sales"]


def test_sources_parse_to_catalog_schema_table() -> None:
    src = load_model(TPCDS_011).sources["store_sales"]
    assert (src.catalog, src.schema, src.table) == ("tpcds", "public", "store_sales")


def test_dialect_choice_ignores_array_position() -> None:
    # apache/ossie#461: never fall back to dialects[0].
    e = Expression(
        dialects=[
            DialectExpression(dialect="SNOWFLAKE", expression="amt_snowflake"),
            DialectExpression(dialect="ANSI_SQL", expression="amount"),
            DialectExpression(dialect="OSSIE_SQL_2026", expression="amount_portable"),
        ]
    )
    assert e.portable().expression == "amount_portable"
    only_vendor = Expression(dialects=[DialectExpression(dialect="THOUGHTSPOT", expression="sum ( x )")])
    assert only_vendor.portable() is None


def test_metric_without_portable_dialect_warns(tmp_path: Path) -> None:
    # apache/ossie#462 shape: a metric only in a vendor dialect.
    p = write(
        tmp_path,
        """
        version: "0.1.1"
        semantic_model:
          - name: m
            datasets:
              - name: t
                source: db.s.t
                fields:
                  - name: x
                    expression: {dialects: [{dialect: ANSI_SQL, expression: x}]}
            metrics:
              - name: total
                expression: {dialects: [{dialect: TABLEAU, expression: "SUM([x])"}]}
        """,
    )
    loaded = load_model(p)
    assert any("#462" in w for w in loaded.warnings)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("version: '9.9'\n", "unsupported Ossie version"),
        ("version: '0.1.1'\nsemantic_model: []\n", "non-empty 'semantic_model'"),
        ("- just a list\n", "mapping at the top level"),
        (": : :\n", "not valid YAML"),
    ],
)
def test_invalid_documents_raise_model_errors(tmp_path: Path, body: str, message: str) -> None:
    with pytest.raises(UsageError, match=message):
        load_model(write(tmp_path, body))


def test_typos_are_rejected(tmp_path: Path) -> None:
    p = write(
        tmp_path,
        """
        version: "0.1.1"
        semantic_model:
          - name: m
            datasets:
              - name: t
                source: db.s.t
                primary_keys: [id]
        """,
    )
    with pytest.raises(ModelError, match="primary_keys"):
        load_model(p)


def test_relationship_to_unknown_dataset(tmp_path: Path) -> None:
    p = write(
        tmp_path,
        """
        version: "0.1.1"
        semantic_model:
          - name: m
            datasets:
              - {name: a, source: db.s.a}
            relationships:
              - {name: r, from: a, to: nowhere, from_columns: [x], to_columns: [y]}
        """,
    )
    with pytest.raises(ModelError, match="unknown dataset 'nowhere'"):
        load_model(p)


def test_several_models_need_a_name(tmp_path: Path) -> None:
    p = write(
        tmp_path,
        """
        version: "0.1.1"
        semantic_model:
          - {name: one, datasets: [{name: a, source: db.s.a}]}
          - {name: two, datasets: [{name: b, source: db.s.b}]}
        """,
    )
    with pytest.raises(UsageError, match="--model-name"):
        load_model(p)
    assert load_model(p, model_name="two").model.datasets[0].name == "b"


def test_shop_corpus_loads() -> None:
    loaded = load_model(SHOP)
    assert loaded.key_columns("order_items", ["product_id", "variant_id"]) == ["product_id", "variant_id"]
    assert loaded.warnings == []

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import pytest
from conftest import SHOP, TPCDS_011

from semround.engine import load_memory, open_files, run, write_files
from semround.fixtures import DEFAULT_TRAPS, generate
from semround.model import load_model
from semround.model.types import infer_types


@pytest.fixture(scope="module")
def shop():
    loaded = load_model(SHOP)
    return loaded, infer_types(loaded)


def test_same_seed_same_rows_and_manifest(shop, tmp_path: Path) -> None:
    loaded, types = shop
    a, b = generate(loaded, types, 300, 11), generate(loaded, types, 300, 11)
    assert a.manifest() == b.manifest()
    ma = write_files(a, tmp_path / "a").read_bytes()
    mb = write_files(b, tmp_path / "b").read_bytes()
    assert ma == mb  # manifest is byte-identical (DuckDB files themselves are not)
    assert generate(loaded, types, 300, 12).manifest() != a.manifest()


def test_disabling_one_trap_leaves_the_others_unchanged(shop) -> None:
    loaded, types = shop
    full = generate(loaded, types, 300, 3)
    without = generate(loaded, types, 300, 3, [t for t in DEFAULT_TRAPS if t != "null_measure"])
    keep = lambda fx: sorted((r.trap, r.dataset, r.columns, r.rows) for r in fx.traps if r.trap != "null_measure")  # noqa: E731
    assert keep(full) == keep(without)


def test_primary_keys_unique_and_orphans_recorded(shop) -> None:
    loaded, types = shop
    fx = generate(loaded, types, 400, 5)
    con = load_memory(fx)
    dup = run(con, "SELECT count(*) - count(DISTINCT (order_id, line_number)) FROM shop.main.order_items")
    assert dup.rows == [(0,)]
    orphans = run(
        con,
        "SELECT count(*) FROM shop.main.orders o LEFT JOIN shop.main.customers c USING (customer_id) "
        "WHERE c.customer_id IS NULL",
    ).rows[0][0]
    recorded = [r for r in fx.traps if r.trap == "orphan_fk" and r.dataset == "orders"]
    assert orphans == len(recorded[0].rows) > 0


def test_base_dates_avoid_trap_edges(shop) -> None:
    loaded, types = shop
    fx = generate(loaded, types, 400, 5, traps=[])
    col = fx.table("customers").col("signup_date")
    for row in fx.table("customers").rows:
        d: dt.date = row[col]
        assert d.weekday() < 5 and 2 <= d.day <= 27


def test_traps_touch_expected_columns(shop) -> None:
    loaded, types = shop
    fx = generate(loaded, types, 400, 5)
    by_trap = {(r.trap, r.dataset, r.columns) for r in fx.traps}
    assert ("orphan_fk", "order_items", ("product_id", "variant_id")) in by_trap
    assert ("null_measure", "orders", ("amount",)) in by_trap
    assert ("month_end", "orders", ("ordered_at",)) in by_trap
    assert not any(r.columns == ("order_id",) and r.trap.startswith("null") for r in fx.traps)


def test_written_files_reopen_read_only(tmp_path: Path) -> None:
    loaded = load_model(TPCDS_011)
    fx = generate(loaded, infer_types(loaded), 200, 7)
    write_files(fx, tmp_path)
    con = open_files(tmp_path)
    assert run(con, "SELECT count(*) FROM tpcds.public.store_sales").rows == [(200,)]
    with pytest.raises(duckdb.Error, match="read-only"):
        con.execute("DELETE FROM tpcds.public.store_sales")

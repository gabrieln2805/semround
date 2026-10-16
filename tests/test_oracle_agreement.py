"""Oracle agreement: reference SQL on DuckDB must equal the row-by-row Python evaluator.

Every push: a few hundred examples. Nightly: HYPOTHESIS_PROFILE=nightly (5,000).
"""

from __future__ import annotations

import os
from functools import cache

import pytest
from conftest import SHOP, TPCDS_011, TPCDS_020
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from semround.compiler import compile_query
from semround.compiler.queries import all_queries
from semround.diff import compare
from semround.engine import Result, load_memory, run
from semround.errors import Unsupported
from semround.fixtures import DEFAULT_TRAPS, generate
from semround.model import load_model
from semround.model.types import infer_types
from semround.oracle import Evaluator

settings.register_profile("ci", max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
settings.register_profile("nightly", max_examples=5000, deadline=None, suppress_health_check=[HealthCheck.too_slow])
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))

MODELS = {"tpcds_0.1.1": (TPCDS_011, False), "tpcds_0.2.0": (TPCDS_020, True), "shop": (SHOP, False)}


@cache
def _model(name: str):
    path, dev = MODELS[name]
    loaded = load_model(path, allow_dev=dev)
    types = infer_types(loaded)
    compiled = []
    for q in all_queries(loaded, types):
        try:
            compiled.append((q, compile_query(loaded, q)[1]))
        except Unsupported:
            pass
    return loaded, types, compiled


@pytest.mark.parametrize("model", sorted(MODELS))
@given(
    seed=st.integers(min_value=0, max_value=2**31),
    rows=st.integers(min_value=10, max_value=400),
    traps=st.sets(st.sampled_from(DEFAULT_TRAPS)),
)
def test_reference_matches_oracle(model: str, seed: int, rows: int, traps: set[str]) -> None:
    loaded, types, compiled = _model(model)
    fx = generate(loaded, types, rows, seed, traps)
    con = load_memory(fx)
    oracle = Evaluator(loaded, fx)
    try:
        for query, sql in compiled:
            expected = run(con, sql)
            actual = Result(expected.columns, oracle.evaluate(query))
            cmp = compare(expected, actual)
            assert cmp.ok, (query.key, cmp)
    finally:
        con.close()

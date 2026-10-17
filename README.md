# Semround

Semround checks that converting a semantic model keeps every metric's numbers.

Point it at an [Apache Ossie](https://github.com/apache/ossie) model and choose the converters to test. Semround builds synthetic DuckDB tables seeded with known edge cases, runs the original metrics and the converted ones on the same tables, and reports every number that differs.

> **Status: pre-alpha.** The core loop works end to end with two targets: `reference` (a self-check) and `dbt` (dbt v1.12 with MetricFlow). Nothing is on PyPI yet.

## How it works

```text
model.yaml ──► loader ──► type inference ──► seeded fixtures (DuckDB, with traps)
                  │                                   │
                  ├──► reference compiler ──► SQL ──► run ──┐
                  │                                         ├──► diff ──► findings ──► report
                  └──► each target (converter) ──► SQL ──► run ──┘
```

- **Reference compiler.** It follows the Ossie rule that each measure aggregates at its own grain, whatever the joins. Each aggregate is computed in its own CTE, with left joins along many-to-one paths. A dimension reachable only through a one-to-many join makes the query *fan-out*. Values are then deduplicated by the measured dataset's primary key, and a target must either match that or refuse the query.
- **Independent oracle.** A second, plain-Python row-by-row evaluator shares no code with the compiler. Property-based tests require the two to agree on random fixtures, so a Semround bug does not get reported as a converter bug.
- **Traps.** Fixtures deliberately contain edge cases: orphan foreign keys, NULL measures, NULL dimensions, weekend dates and weekday month-ends. Each trap draws from its own random stream, so Semround can name the trap behind a mismatch by turning that trap off and re-running.
- **Local only.** `semround check` makes no network calls, sends no telemetry and never connects to a warehouse. The test suite runs with sockets disabled.

## Quickstart (from source)

```bash
uv sync
uv run semround check corpus/traps/shop.yaml --to reference --html report.html
uv run semround compile corpus/traps/shop.yaml --metric revenue --by region,ordered_at:month
uv run semround fixtures corpus/upstream/tpcds_0.1.1.yaml --out /tmp/tpcds
```

Exit codes: `0` pass, `1` findings, `2` usage or config error, `3` internal error.

### The `dbt` target

dbt and MetricFlow pin dependencies that conflict with other converters, so each target gets its own virtual environment:

```bash
uv venv .targets/dbt
uv pip install --python .targets/dbt "dbt-core==1.12.*" dbt-duckdb dbt-metricflow
uv run semround check corpus/traps/shop_small.yaml --to dbt
```

Semround writes the model into a temporary dbt project's `osi/` folder and runs `dbt parse` with tracking and version checks off. It then asks MetricFlow's Python API for the SQL of every query (`--explain`; no warehouse connection) and runs that SQL on the fixtures. Set `SEMROUND_DBT_VENV` to use a venv somewhere else.

## Finding codes

| Code | Meaning |
| --- | --- |
| `DROPPED` | A metric or field did not survive conversion |
| `DIALECT_FALLBACK` | The converter picked a non-portable dialect expression (planned) |
| `VALUE_MISMATCH` | Numbers differ; attributed to the trap whose rows explain the difference |
| `MISSING_GROUPS` / `EXTRA_GROUPS` | The set of groups differs, usually a join-type issue |
| `FANOUT_UNSAFE` | The target answered a fan-out query with duplicated values |
| `TARGET_ERROR` | The converter or its SQL failed |

Finding IDs (`SR-` and 6 hex characters) are stable across runs, so you can accept one in config:

```toml
# semround.toml
models = ["osi/*.yaml"]
targets = ["reference"]
rows = 1000
seed = 7
tolerance = { relative = 1e-9, absolute = 1e-6 }

[types]                       # hints when type inference is unsure
"orders.total_amount" = "decimal(18,2)"

[[accept]]
id = "SR-7f3a9c"
reason = "Cumulative metrics are not portable to Ossie yet"
```

## Spec support

- Ossie **0.1.0 / 0.1.1** (the `semantic_model` array shape, which dbt v1.12 reads).
- Ossie **0.2.0.dev0** (flat documents with `datatype`), behind `--spec-dev`.
- Expressions: `OSSIE_SQL_2026` first, then `ANSI_SQL`. Semround never falls back to whichever dialect happens to come first in the list ([apache/ossie#461](https://github.com/apache/ossie/issues/461)).
- The reference compiler skips window-function metrics for now and reports them as skipped.

## Development

```bash
uv sync
uv run pytest                    # unit, golden SQL, oracle agreement, mutants, CLI, invariants
uv run ruff check src tests
SEMROUND_UPDATE_GOLDEN=1 uv run pytest tests/test_compiler.py   # after an intended SQL change
HYPOTHESIS_PROFILE=nightly uv run pytest tests/test_oracle_agreement.py
```

`tests/mutants/` holds deliberately broken converters: naive joins, inner joins, Sunday weeks, a timezone shift, a dropped filter and dropped metrics. Each one must be caught, with the right finding code and trap.

## Licence

Apache-2.0. `corpus/upstream/` contains example models and schemas from Apache Ossie; see `NOTICE`.

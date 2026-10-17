 # How Semround works

A plain-language guide to the code. For what is done and what comes next, see [STATUS.md](../STATUS.md).

## The idea in one paragraph

Tools like dbt and Snowflake can import a metric definition from another format. Sometimes that conversion has a bug: nothing errors, but the metric returns a different number than it should. Semround catches this. It creates small test tables with deliberately tricky rows, such as orders with no matching customer or empty amounts. It calculates each metric correctly on those tables, then runs the converted version on the same tables. Any number that doesn’t match is reported, along with the tricky case that caused it.

## The pipeline

```text
model file ─► 1 load ─► 2 guess column types ─► 3 build fake tables (with traps)
                 │                                        │
                 ├─► 4 correct SQL (reference) ───────────┤─► 6 compare ─► 7 report
                 └─► 5 converted SQL (each target) ───────┘
```

| Step | What it does | Code |
| --- | --- | --- |
| 1. Load | Reads the Ossie model (YAML or JSON) and checks it makes sense | [model/loader.py](../src/semround/model/loader.py), [model/spec.py](../src/semround/model/spec.py) |
| 2. Types | Ossie files often have no column types, so Semround guesses them, for example "used inside SUM, so it's a number" | [model/types.py](../src/semround/model/types.py) |
| 3. Fake data | Builds small DuckDB tables from a seed, so the same seed always gives the same data | [fixtures/generator.py](../src/semround/fixtures/generator.py), [fixtures/values.py](../src/semround/fixtures/values.py) |
| 3a. Traps | Adds tricky rows on purpose; one file per trap | [fixtures/traps/](../src/semround/fixtures/traps/) |
| 4. Reference | Writes the *correct* SQL for each metric: our answer key | [compiler/](../src/semround/compiler/) |
| 5. Targets | Gets the converted SQL from each tool being tested | [targets/](../src/semround/targets/) |
| 6. Compare | Lines up both results group by group and names what differs | [diff/](../src/semround/diff/) |
| 7. Report | Writes the findings as JSON, Markdown, or an HTML page to open in a browser ([html.py](../src/semround/report/html.py)) | [report/](../src/semround/report/) |

The glue that runs all the steps is [check.py](../src/semround/check.py). The command line is [cli.py](../src/semround/cli.py).

## Key pieces

**Traps.** Each trap is one kind of tricky data:

| Trap | Tricky case | Catches |
| --- | --- | --- |
| `orphan_fk` | Orders pointing to a customer that doesn't exist | Inner joins that silently drop rows |
| `null_measure` | Empty amounts | Wrong handling of missing numbers |
| `null_dimension` | Empty group values (e.g. no region) | Lost or merged "empty" groups |
| `weekend_dates` | Saturdays and Sundays | Weeks starting on Sunday instead of Monday |
| `month_end` | Last day of the month, late at night | Timezone shifts, off-by-one months |

Normal data never contains these cases; only the traps add them. So when Semround turns one trap off and the problem disappears, it knows that trap caused it.

**Reference compiler** ([planner.py](../src/semround/compiler/planner.py), [emit.py](../src/semround/compiler/emit.py)). It writes the correct SQL. The main rule is that each metric is counted at its own level and never double-counted by a join. Example: "revenue by gift-wrap option" must count an order once, even if it has three items.

**Oracle** ([oracle/evaluator.py](../src/semround/oracle/evaluator.py)). A second, completely separate calculator that loops over rows in plain Python. Tests require it to agree with the reference compiler. If the two disagree, the bug is in Semround, not in the tool being tested.

**Targets** ([targets/](../src/semround/targets/)):

- `reference`: our own compiler, used as a self-check that must always pass.
- `dbt`: puts the model in a temporary dbt project, runs `dbt parse`, then asks MetricFlow for the SQL. dbt lives in its own environment in `.targets/dbt`. It talks to Semround through a small JSON script, [_drivers/metricflow_explain.py](../src/semround/targets/_drivers/metricflow_explain.py).

**Findings** ([diff/classify.py](../src/semround/diff/classify.py)). Each problem gets a code (`VALUE_MISMATCH`, `MISSING_GROUPS`, `DROPPED`, …) and a stable ID such as `SR-7f3a9c`. A known problem can be accepted in `semround.toml` so CI stays green.

**Vendor-only metrics.** Some metrics exist only in a vendor dialect, such as ThoughtSpot formulas, with no portable SQL. The reference compiler can't compute those, so Semround asks each target what it did with them instead: dropped (`DROPPED`, the [apache/ossie#462](https://github.com/apache/ossie/issues/462) symptom) or guessed SQL anyway (`DIALECT_FALLBACK`). The code is `_check_vendor_only` in [check.py](../src/semround/check.py).

## Rules the code must keep

These come from the plan, and tests enforce them ([tests/test_invariants.py](../tests/test_invariants.py)):

- No internet during a check. Tests run with network access blocked.
- Fake data only, with no connections to real warehouses.
- No telemetry.
- Results are pass/fail per test, never scores or rankings.

## Tests

| File | Checks |
| --- | --- |
| `test_loader.py`, `test_types.py` | Loading models; type guessing (TPC-DS: 28/28 correct) |
| `test_fixtures.py` | Same seed gives the same data; traps land where expected |
| `test_compiler.py` | Hand-calculated answers, plus saved SQL snapshots in `tests/golden/` |
| `test_oracle_agreement.py` | Compiler and oracle agree on random data |
| `test_mutants.py` | Six deliberately broken converters in `tests/mutants/` are all caught |
| `test_dbt_target.py` | Real dbt run (skipped if `.targets/dbt` is missing) |
| `test_known_failures.py` | Known upstream bugs in `corpus/known/`, e.g. #462, are reported with stable IDs |
| `test_cli.py` | Commands, exit codes, identical reports on reruns |

## Test models

- `corpus/upstream/`: the official TPC-DS example from Apache Ossie, in versions 0.1.1 and 0.2.
- `corpus/traps/shop.yaml`: our own model, built to hit tricky cases.
- `corpus/traps/shop_small.yaml`: a smaller version that dbt can parse.
- `corpus/known/issue_462_thoughtspot_tpcds.yaml`: TPC-DS as the ThoughtSpot converter exports it, with every metric in ThoughtSpot formulas only (issue #462).

`upstream/` (local only, not in git) holds drafts of bug reports for other projects, each with a small script that reproduces the bug.

## Everyday commands

```bash
uv sync                                   # install
uv run pytest                             # all tests (~1 min)
uv run semround check corpus/traps/shop.yaml --to reference
uv run semround check corpus/traps/shop.yaml --to reference --html report.html   # visual report
uv run semround check corpus/traps/shop_small.yaml --to dbt     # needs .targets/dbt
uv run semround compile corpus/traps/shop.yaml --metric revenue --by region
```

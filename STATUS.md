# Status

_Last updated: 17 Oct 2026 (session 2)_

## Current stage

**Pre-alpha. The core loop works, and the first real target (dbt) is running.** This is ahead of the plan, whose work starts 28 Oct. Nothing is published yet: no GitHub remote, nothing on PyPI.

| Plan item | State |
| --- | --- |
| S1: guess column types | Done. 28/28 TPC-DS columns correct with no hints |
| S2: Ossie → dbt → MetricFlow → DuckDB | Done, with a workaround for an upstream bug |
| S3: official Ossie converters in separate environments | Not started |
| v0.1 core loop (E1.1–E1.6) | Mostly done. Still missing: reproduce issue #462 and find 2 new failures (5 found, not yet filed) |
| Oracle cross-check (E3.1, planned for later) | Basic version done early |
| HTML report (E4.2, planned for v0.8) | First version done: `--html report.html` |
| v0.2 launch work (Action, docs, PyPI) | Not started |

Tests: 107 passing. CI is written but has not run yet (nothing pushed).

## Findings so far

Five likely bugs in dbt-core 1.12.6 / MetricFlow 0.213.0. **Not reported upstream yet.**

1. No metric from an Ossie file can be queried: MetricFlow requires a time setting (`agg_time_dimension`) that the converter never fills in.
2. Composite primary keys make `dbt parse` fail.
3. `AVG(a.x + COALESCE(a.y, 0))` is cut at a dot into nonsense.
4. CASE metrics keep `table.column` names that break the SQL.
5. Window and vendor-only metrics are kept silently, with no warning.

## Differences from the plan

- Ossie's latest version (0.2) changed the file layout, but dbt only reads 0.1.
- The Databricks converter is Java, not Python.
- DuckDB files are never byte-identical between runs, so the test for repeatable data uses a manifest plus a row checksum instead.
- MetricFlow's `mf` command crashes on Windows, so Semround calls its Python API instead.

## Next steps

1. Decide whether to report the 5 bugs (needs your approval, since it's public).
2. Create the GitHub repo, push, and check that CI passes.
3. Reproduce issue #462 in the corpus (finishes v0.1).
4. HTML report, second step: show the correct and converted SQL side by side.
5. Spike S3: run the official Ossie converters in their own environments.
6. v0.2 launch work: GitHub Action with PR comment, docs quickstart, PyPI release.

## Session log

- **16 Oct 2026:** Created the repo. Built the loader, type guessing, fake data with traps, reference compiler, oracle, compare/report, CLI and tests. Added the dbt target and found 5 upstream bugs.
- **17 Oct 2026:** Added the HTML report (`semround check … --html report.html`): a metrics × targets grid of match/fail, and each finding expands to show its cause, the affected queries, and correct vs converted values. One file, works offline.

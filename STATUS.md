# Status

_Last updated: 17 Oct 2026 (session 3)_

## Current stage

**Pre-alpha. The v0.1 core loop is complete, and the first real target (dbt) is running.** This is ahead of the plan, whose work starts 28 Oct. The GitHub repo exists (gabrieln2805/semround) but nothing has been pushed yet; nothing is on PyPI.

| Plan item | State |
| --- | --- |
| S1: guess column types | Done. 28/28 TPC-DS columns correct with no hints |
| S2: Ossie → dbt → MetricFlow → DuckDB | Done, with a workaround for an upstream bug |
| S3: official Ossie converters in separate environments | Not started |
| v0.1 core loop (E1.1–E1.6) | Done. #462 reproduced as `DROPPED` with stable IDs; new upstream failures found (4 issue drafts, not filed yet) |
| Oracle cross-check (E3.1, planned for later) | Basic version done early |
| HTML report (E4.2, planned for v0.8) | First version done: `--html report.html` |
| v0.2 launch work (Action, docs, PyPI) | Not started |

Tests: 110 passing. CI is written but has not run yet (nothing pushed).

## Findings so far

Bugs in MetricFlow 0.213.0's Ossie converter (used by dbt-core 1.12.6), re-checked on 17 Oct against the newest releases and `main`. They're grouped into **4 issue drafts in `upstream/`** (local only), each with a verified repro script. **Not filed yet.**

1. No metric from an Ossie file can be queried: MetricFlow requires a time setting (`agg_time_dimension`) that the converter never fills in.
2. Composite primary keys make `dbt parse` fail.
3. `AVG(a.x + COALESCE(a.y, 0))` is cut at a dot into nonsense.
4. CASE metrics keep `table.column` names that break the SQL.
5. Window and vendor-only metrics are kept silently, with no warning.

Items 4 and 5 share one cause, so they're one draft. Also: dbt v1.12 rejects a ThoughtSpot-exported model entirely, because that dialect exists only in Ossie 0.2. That's spec-correct, so it isn't filed.

## Differences from the plan

- Ossie's latest version (0.2) changed the file layout, but dbt only reads 0.1.
- The Databricks converter is Java, not Python.
- DuckDB files are never byte-identical between runs, so the test for repeatable data uses a manifest plus a row checksum instead.
- MetricFlow's `mf` command crashes on Windows, so Semround calls its Python API instead.

## Next steps

1. Review `upstream/*/ISSUE.md` and file the ones you approve at dbt-labs/metricflow.
2. Push to GitHub and check that CI passes.
3. HTML report, second step: show the correct and converted SQL side by side.
4. Spike S3: run the official Ossie converters in their own environments.
5. v0.2 launch work: GitHub Action with PR comment, docs quickstart, PyPI release.

## Session log

- **16 Oct 2026:** Created the repo. Built the loader, type guessing, fake data with traps, reference compiler, oracle, compare/report, CLI and tests. Added the dbt target and found 5 upstream bugs.
- **17 Oct 2026:** Added the HTML report (`semround check … --html report.html`): a metrics × targets grid of match/fail, and each finding expands to show its cause, the affected queries, and correct vs converted values. One file, works offline.
- **17 Oct 2026 (session 3):** Reproduced #462 using upstream's own ThoughtSpot TPC-DS export (`corpus/known/`). Semround now reports metrics that exist only in a vendor dialect as `DROPPED` or `DIALECT_FALLBACK` per target, instead of skipping them silently. Re-checked the dbt/MetricFlow bugs against the newest versions and wrote 4 issue drafts with repro scripts in `upstream/`.

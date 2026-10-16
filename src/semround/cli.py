"""Command-line interface.

Exit codes: 0 pass, 1 findings, 2 usage or config error, 3 internal error.
"""

from __future__ import annotations

import glob
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, TypeVar

import typer

from semround import __version__
from semround.errors import EXIT_INTERNAL, EXIT_USAGE, SemroundError, Unsupported, UsageError

app = typer.Typer(
    name="semround",
    help="Check that semantic-model conversions keep every metric's numbers.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
targets_app = typer.Typer(help="Inspect available targets.", no_args_is_help=True)
app.add_typer(targets_app, name="targets")

T = TypeVar("T")

ConfigOpt = Annotated[Path | None, typer.Option("--config", "-c", help="Path to semround.toml.")]
SpecDevOpt = Annotated[bool, typer.Option("--spec-dev", help="Accept Ossie 0.2.0.dev0 documents.")]
ModelNameOpt = Annotated[
    str | None, typer.Option("--model-name", help="Model to use when a 0.1.x document holds several.")
]


def _guard(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except SemroundError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(e.exit_code) from None
    except typer.Exit:
        raise
    except Exception:
        typer.echo("internal error; please report it with the traceback below", err=True)
        traceback.print_exc()
        raise typer.Exit(EXIT_INTERNAL) from None


def _split(values: list[str] | None) -> list[str]:
    return [v.strip() for raw in values or [] for v in raw.split(",") if v.strip()]


@app.callback(invoke_without_command=True)
def _root(
    version: Annotated[bool, typer.Option("--version", help="Print the version and exit.")] = False,
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit(0)


@app.command()
def check(
    models: Annotated[list[Path] | None, typer.Argument(help="Ossie model files (default: config 'models').")] = None,
    to: Annotated[list[str] | None, typer.Option("--to", "-t", help="Targets, comma separated.")] = None,
    rows: Annotated[int | None, typer.Option(help="Fact-table rows per fixture.")] = None,
    seed: Annotated[int | None, typer.Option(help="Fixture seed.")] = None,
    json_out: Annotated[Path | None, typer.Option("--json", help="Write the JSON report here.")] = None,
    md_out: Annotated[Path | None, typer.Option("--md", help="Write the Markdown report here.")] = None,
    config: ConfigOpt = None,
    spec_dev: SpecDevOpt = False,
    model_name: ModelNameOpt = None,
) -> None:
    """Run every metric through the reference and each target; report differences."""
    from semround.check import CheckOptions, run_check
    from semround.config import load_config
    from semround.diff import Tolerance
    from semround.fixtures import DEFAULT_TRAPS
    from semround.report import to_json, to_markdown

    def go() -> int:
        cfg = load_config(config)
        paths = list(models or [])
        if not paths:
            for pattern in cfg.models:
                paths += [Path(p) for p in sorted(glob.glob(pattern))]
        if not paths:
            raise UsageError("no model given; pass a model file or set 'models' in semround.toml")
        targets = _split(to) or cfg.targets
        if not targets:
            raise UsageError("no target given; pass --to (e.g. --to reference) or set 'targets' in semround.toml")
        opts = CheckOptions(
            models=paths,
            targets=targets,
            rows=rows or cfg.rows,
            seed=cfg.seed if seed is None else seed,
            tolerance=Tolerance(cfg.tolerance.relative, cfg.tolerance.absolute),
            hints=cfg.types,
            accepted=cfg.accepted(),
            traps=tuple(cfg.traps) if cfg.traps is not None else DEFAULT_TRAPS,
            allow_dev=spec_dev or cfg.spec_dev,
            model_name=model_name,
        )
        report = run_check(opts)
        md = to_markdown(report)
        if json_out:
            json_out.write_text(to_json(report), encoding="utf-8", newline="\n")
        if md_out:
            md_out.write_text(md, encoding="utf-8", newline="\n")
        typer.echo(md)
        return report.exit_code

    raise typer.Exit(_guard(go))


@app.command()
def fixtures(
    model: Annotated[Path, typer.Argument(help="Ossie model file.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Output directory.")],
    rows: Annotated[int, typer.Option(help="Fact-table rows.")] = 1000,
    seed: Annotated[int, typer.Option(help="Fixture seed.")] = 7,
    config: ConfigOpt = None,
    spec_dev: SpecDevOpt = False,
    model_name: ModelNameOpt = None,
) -> None:
    """Write the synthetic DuckDB fixtures and a trap manifest."""
    from semround.config import load_config
    from semround.engine import write_files
    from semround.fixtures import generate
    from semround.model import load_model
    from semround.model.types import infer_types, unsure_columns

    def go() -> int:
        cfg = load_config(config)
        loaded = load_model(model, model_name, spec_dev or cfg.spec_dev)
        types = infer_types(loaded, cfg.types)
        fx = generate(loaded, types, rows, seed)
        manifest = write_files(fx, out)
        typer.echo(f"wrote {len(fx.tables)} tables and {len(fx.traps)} trap records; manifest: {manifest}")
        unsure = unsure_columns(types)
        if unsure:
            typer.echo(f"guessed types for {len(unsure)} column(s): {', '.join(unsure)}")
        return 0

    raise typer.Exit(_guard(go))


@app.command()
def compile(
    model: Annotated[Path, typer.Argument(help="Ossie model file.")],
    metric: Annotated[str, typer.Option("--metric", "-m", help="Metric name.")],
    by: Annotated[str, typer.Option("--by", help="Dimensions, e.g. region,orders.order_date:month.")] = "",
    spec_dev: SpecDevOpt = False,
    model_name: ModelNameOpt = None,
) -> None:
    """Print the reference SQL for one metric."""
    from semround.compiler import Query, compile_query, parse_dimensions
    from semround.model import load_model

    def go() -> int:
        loaded = load_model(model, model_name, spec_dev)
        query = Query(metric, parse_dimensions(by, loaded.model))
        try:
            plan, sql = compile_query(loaded, query)
        except Unsupported as u:
            raise UsageError(f"the reference compiler does not support this query yet: {u}") from None
        if plan.fanout:
            typer.echo("-- fan-out query: values are deduplicated by the measured dataset's primary key")
        typer.echo(sql + ";")
        return 0

    raise typer.Exit(_guard(go))


@targets_app.command("list")
def targets_list() -> None:
    """Show available targets and their pinned versions."""
    from semround.targets import available, create

    for name in available():
        versions = ", ".join(f"{k} {v}" for k, v in create(name).versions().items())
        typer.echo(f"{name:<12} {versions}")


def run() -> None:
    try:
        app()
    except SystemExit as e:
        # Typer reports its own usage errors with code 2, which matches our contract.
        sys.exit(e.code if isinstance(e.code, int) else EXIT_USAGE)

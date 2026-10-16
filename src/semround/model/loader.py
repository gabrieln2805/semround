"""Load an Ossie document (YAML or JSON) into a :class:`LoadedModel`.

Handles both document shapes (0.1.x ``semantic_model`` array, 0.2.0.dev0 flat root),
checks cross-references the JSON Schema cannot express, and resolves each dataset's
physical columns, which the fixture generator and the compiler both need.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
import sqlglot.expressions as exp
import yaml
from pydantic import ValidationError
from sqlglot.errors import ParseError, TokenError

from semround.errors import ModelError, UsageError
from semround.model.spec import KNOWN_DIALECTS, Dataset, SemanticModel

STABLE_VERSIONS = ("0.1.0", "0.1.1")
DEV_VERSIONS = ("0.2.0.dev0",)


@dataclass(frozen=True)
class PhysicalSource:
    catalog: str | None
    schema: str | None
    table: str

    def sql(self) -> str:
        parts = [p for p in (self.catalog, self.schema, self.table) if p]
        return ".".join(f'"{p}"' for p in parts)


@dataclass
class LoadedModel:
    path: Path
    spec_version: str
    model: SemanticModel
    warnings: list[str] = field(default_factory=list)
    sources: dict[str, PhysicalSource] = field(default_factory=dict)
    # dataset name -> ordered physical column names (lower case)
    columns: dict[str, list[str]] = field(default_factory=dict)
    # dataset name -> field name (lower) -> parsed portable expression, qualified-free
    field_sql: dict[str, dict[str, exp.Expression | None]] = field(default_factory=dict)

    def key_columns(self, dataset: str, names: list[str]) -> list[str]:
        return [self._key_column(dataset, n) for n in names]

    def _key_column(self, dataset: str, name: str) -> str:
        expr = self.field_sql[dataset].get(name.lower())
        if expr is None:
            # Key not declared as a field: it is a physical column (TPC-DS ss_ticket_number).
            return name.lower()
        if isinstance(expr, exp.Column):
            return expr.name.lower()
        raise ModelError(f"dataset {dataset!r}: key field {name!r} must be a plain column, got {expr.sql()!r}")

    def primary_key(self, dataset: str) -> list[str] | None:
        ds = self.model.dataset(dataset)
        assert ds is not None
        return self.key_columns(ds.name, ds.primary_key) if ds.primary_key else None


def parse_sql(text: str, context: str) -> exp.Expression:
    try:
        return sqlglot.parse_one(text, read=None)
    except (ParseError, TokenError) as e:
        raise ModelError(f"{context}: cannot parse expression {text!r}: {e}") from None


def parse_source(source: str, dataset: str) -> PhysicalSource:
    text = source.strip()
    if any(ch.isspace() for ch in text) or text.lower().startswith("select"):
        raise UsageError(f"dataset {dataset!r}: query sources are not supported yet ({text[:40]!r})")
    try:
        table = exp.to_table(text)
    except (ParseError, TokenError, ValueError) as e:
        raise ModelError(f"dataset {dataset!r}: bad source {text!r}: {e}") from None
    return PhysicalSource(
        catalog=table.catalog.lower() or None, schema=table.db.lower() or None, table=table.name.lower()
    )


def _read_document(path: Path) -> object:
    if not path.is_file():
        raise ModelError(f"model file not found: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        if path.suffix.lower() == ".json":
            return json.loads(text)
        return yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        raise ModelError(f"{path}: not valid {'JSON' if path.suffix.lower() == '.json' else 'YAML'}: {e}") from None


def _unwrap(doc: object, path: Path, model_name: str | None, allow_dev: bool) -> tuple[str, dict]:
    if not isinstance(doc, dict):
        raise ModelError(f"{path}: an Ossie document must be a mapping at the top level")
    version = str(doc.get("version", ""))
    if version in STABLE_VERSIONS:
        models = doc.get("semantic_model")
        if not isinstance(models, list) or not models:
            raise ModelError(f"{path}: Ossie {version} documents need a non-empty 'semantic_model' array")
        extra = set(doc) - {"version", "semantic_model", "dialects", "vendors"}
        if extra:
            raise ModelError(f"{path}: unknown top-level keys {sorted(extra)}")
        names = [m.get("name") for m in models if isinstance(m, dict)]
        if model_name is not None:
            chosen = [m for m in models if isinstance(m, dict) and m.get("name") == model_name]
            if not chosen:
                raise ModelError(f"{path}: no model named {model_name!r}; found {names}")
            return version, chosen[0]
        if len(models) > 1:
            raise UsageError(f"{path}: {len(models)} models in one document {names}; choose one with --model-name")
        return version, models[0]
    if version in DEV_VERSIONS:
        if not allow_dev:
            raise UsageError(f"{path}: Ossie {version} is a development version; pass --spec-dev to load it")
        if "semantic_model" in doc:
            raise ModelError(f"{path}: Ossie {version} documents are flat; 'semantic_model' was removed")
        body = {k: v for k, v in doc.items() if k != "version"}
        return version, body
    supported = ", ".join(STABLE_VERSIONS + DEV_VERSIONS)
    raise ModelError(f"{path}: unsupported Ossie version {version or '(missing)'}; supported: {supported}")


def _duplicates(names: list[str]) -> list[str]:
    seen: set[str] = set()
    dups: set[str] = set()
    for n in names:
        low = n.lower()
        (dups if low in seen else seen).add(low)
    return sorted(dups)


def _field_columns(expr: exp.Expression, dataset: str) -> list[str]:
    cols = []
    for c in expr.find_all(exp.Column):
        if c.table and c.table.lower() != dataset.lower():
            raise ModelError(f"dataset {dataset!r}: field expression {expr.sql()!r} references another dataset")
        cols.append(c.name.lower())
    return cols


def load_model(path: str | Path, model_name: str | None = None, allow_dev: bool = False) -> LoadedModel:
    path = Path(path)
    version, body = _unwrap(_read_document(path), path, model_name, allow_dev)
    try:
        model = SemanticModel.model_validate(body)
    except ValidationError as e:
        lines = [f"  {'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()[:10]]
        raise ModelError(f"{path}: model does not match the Ossie {version} schema:\n" + "\n".join(lines)) from None

    loaded = LoadedModel(path=path, spec_version=version, model=model)
    _check_names(loaded)
    for ds in model.datasets:
        _index_dataset(loaded, ds)
    _check_relationships(loaded)
    _check_metrics(loaded)
    return loaded


def _check_names(loaded: LoadedModel) -> None:
    m = loaded.model
    problems = []
    for kind, names in (
        ("dataset", [d.name for d in m.datasets]),
        ("relationship", [r.name for r in m.relationships]),
        ("metric", [x.name for x in m.metrics]),
    ):
        if dups := _duplicates(names):
            problems.append(f"duplicate {kind} names: {dups}")
    for d in m.datasets:
        if dups := _duplicates([f.name for f in d.fields]):
            problems.append(f"dataset {d.name!r}: duplicate field names {dups}")
    if problems:
        raise ModelError(f"{loaded.path}: " + "; ".join(problems))


def _index_dataset(loaded: LoadedModel, ds: Dataset) -> None:
    loaded.sources[ds.name] = parse_source(ds.source, ds.name)
    sqls: dict[str, exp.Expression | None] = {}
    for f in ds.fields:
        for d in f.expression.dialects:
            if d.dialect not in KNOWN_DIALECTS:
                loaded.warnings.append(f"{ds.name}.{f.name}: unknown dialect {d.dialect!r}")
        chosen = f.expression.portable()
        if chosen is None:
            loaded.warnings.append(f"{ds.name}.{f.name}: no OSSIE_SQL_2026 or ANSI_SQL expression; field skipped")
            sqls[f.name.lower()] = None
            continue
        parsed = parse_sql(chosen.expression, f"{ds.name}.{f.name}")
        if any(parsed.find_all(exp.AggFunc)):
            raise ModelError(f"{ds.name}.{f.name}: field expressions must be scalar, found an aggregate")
        sqls[f.name.lower()] = parsed
    loaded.field_sql[ds.name] = sqls

    cols: list[str] = []

    def add(names: list[str]) -> None:
        for n in names:
            if n not in cols:
                cols.append(n)

    if ds.primary_key:
        add(loaded.key_columns(ds.name, ds.primary_key))
    for uk in ds.unique_keys or []:
        add(loaded.key_columns(ds.name, uk))
    for f in ds.fields:
        parsed = sqls[f.name.lower()]
        if parsed is not None:
            add(_field_columns(parsed, ds.name))
    loaded.columns[ds.name] = cols


def _check_relationships(loaded: LoadedModel) -> None:
    m = loaded.model
    for r in m.relationships:
        for side in (r.from_, r.to):
            if m.dataset(side) is None:
                raise ModelError(f"relationship {r.name!r}: unknown dataset {side!r}")
        if len(r.from_columns) != len(r.to_columns):
            raise ModelError(f"relationship {r.name!r}: from_columns and to_columns differ in length")
        frm, to = m.dataset(r.from_), m.dataset(r.to)
        assert frm is not None and to is not None
        for ds, names in ((frm, r.from_columns), (to, r.to_columns)):
            for c in loaded.key_columns(ds.name, names):
                if c not in loaded.columns[ds.name]:
                    loaded.columns[ds.name].append(c)


def _check_metrics(loaded: LoadedModel) -> None:
    m = loaded.model
    for metric in m.metrics:
        chosen = metric.expression.portable()
        if chosen is None:
            have = sorted({d.dialect for d in metric.expression.dialects})
            loaded.warnings.append(
                f"metric {metric.name!r}: no OSSIE_SQL_2026 or ANSI_SQL expression (has {have}); "
                "converters that require a portable dialect will drop it (apache/ossie#462 pattern)"
            )
            continue
        parsed = parse_sql(chosen.expression, f"metric {metric.name}")
        for col in parsed.find_all(exp.Column):
            if not col.table:
                continue
            ds = m.dataset(col.table)
            if ds is None:
                raise ModelError(f"metric {metric.name!r}: unknown dataset {col.table!r} in {chosen.expression!r}")
            if ds.field(col.name) is None:
                # A physical column the model never declared as a field. Allowed, but it needs fixture data.
                name = col.name.lower()
                if name not in loaded.columns[ds.name]:
                    loaded.columns[ds.name].append(name)
                loaded.warnings.append(f"metric {metric.name!r}: {ds.name}.{col.name} is not a declared field")

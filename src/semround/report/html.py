"""HTML report: one self-contained file to open in a browser.

No scripts and no external links (rule I1: the report must work offline and fetch nothing).
Expanding a finding uses plain ``<details>`` elements.
"""

from __future__ import annotations

from html import escape
from typing import Any

from semround.report.schema import Finding, ModelResult, Report

CODE_HELP = {
    "DROPPED": "The metric (or a field it needs) did not survive the conversion.",
    "DIALECT_FALLBACK": "The converter used a vendor-only expression as if it were portable SQL.",
    "VALUE_MISMATCH": "Same groups, different numbers.",
    "MISSING_GROUPS": "Some groups in the correct answer are missing from the converted one.",
    "EXTRA_GROUPS": "The converted answer has groups the correct one does not.",
    "FANOUT_UNSAFE": "A join multiplied rows, so values were counted more than once.",
    "TARGET_ERROR": "The converter or its SQL failed.",
}

TRAP_HELP = {
    "orphan_fk": "rows pointing to a parent that does not exist (inner vs left joins)",
    "null_measure": "empty numeric values",
    "null_dimension": "empty grouping values",
    "weekend_dates": "Saturday and Sunday dates (week start day)",
    "month_end": "last day of the month, late at night (timezones, month boundaries)",
}

CSS = """
:root { --bg:#fff; --fg:#1d2329; --muted:#5f6b76; --line:#dde3e8; --card:#f6f8fa;
        --ok:#1a7f37; --ok-bg:#dafbe1; --bad:#cf222e; --bad-bg:#ffebe9; --skip:#6e7781; --skip-bg:#eaeef2;
        --acc:#9a6700; --acc-bg:#fff8c5; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0d1117; --fg:#e6edf3; --muted:#8d96a0; --line:#30363d; --card:#161b22;
          --ok:#3fb950; --ok-bg:#12261e; --bad:#f85149; --bad-bg:#2d1214; --skip:#8d96a0; --skip-bg:#21262d;
          --acc:#d29922; --acc-bg:#2b2111; }
}
* { box-sizing: border-box; }
body { margin:0; padding:24px 16px; background:var(--bg); color:var(--fg);
       font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }
main { max-width:1100px; margin:0 auto; }
h1 { font-size:22px; margin:0 0 4px; } h2 { font-size:18px; margin:32px 0 8px; }
h3 { font-size:15px; margin:20px 0 8px; }
.muted { color:var(--muted); } code { font:13px ui-monospace,Consolas,monospace; }
.badge { display:inline-block; padding:1px 8px; border-radius:10px; font-size:12px; font-weight:600; }
.pass { color:var(--ok); background:var(--ok-bg); } .fail { color:var(--bad); background:var(--bad-bg); }
.skip { color:var(--skip); background:var(--skip-bg); } .acc { color:var(--acc); background:var(--acc-bg); }
.scroll { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-size:14px; }
th, td { border-bottom:1px solid var(--line); padding:6px 10px; text-align:left; vertical-align:top; }
th { color:var(--muted); font-weight:600; }
td.num { text-align:right; font-variant-numeric:tabular-nums; }
.grid td { white-space:nowrap; }
details { border:1px solid var(--line); border-radius:8px; margin:8px 0; background:var(--card); }
summary { cursor:pointer; padding:10px 12px; list-style-position:inside; }
details > div { padding:0 12px 12px; }
.diff { color:var(--bad); font-weight:600; }
"""


def _e(value: Any) -> str:
    return escape("NULL" if value is None else str(value))


def _cell_status(findings: list[Finding], skipped: bool) -> str:
    open_ = [f for f in findings if not f.accepted]
    if open_:
        codes = sorted({f.code for f in open_})
        return f'<span class="badge fail">{_e(", ".join(codes))}</span>'
    if findings:
        return '<span class="badge acc">accepted</span>'
    if skipped:
        return '<span class="badge skip">skipped</span>'
    return '<span class="badge pass">match</span>'


def _grid(m: ModelResult, targets: list[str]) -> str:
    metrics = list(dict.fromkeys(q.metric for q in m.queries))
    skipped = {q.metric for q in m.queries if q.status == "skipped"} - {q.metric for q in m.queries if q.status == "ok"}
    head = "".join(f"<th>{_e(t)}</th>" for t in targets)
    rows = []
    model_level = [f for f in m.findings if f.metric == "(model)"]
    if model_level:
        cells = "".join(f"<td>{_cell_status([f for f in model_level if f.target == t], False)}</td>" for t in targets)
        rows.append(f"<tr><td><em>whole model</em></td>{cells}</tr>")
    for metric in metrics:
        mine = [f for f in m.findings if f.metric == metric]
        cells = "".join(
            f"<td>{_cell_status([f for f in mine if f.target == t], metric in skipped)}</td>" for t in targets
        )
        rows.append(f"<tr><td><code>{_e(metric)}</code></td>{cells}</tr>")
    return (
        f'<div class="scroll"><table class="grid"><thead><tr><th>Metric</th>{head}</tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _sample(f: Finding) -> str:
    if not f.sample:
        return ""
    with_values = any("expected" in s for s in f.sample)
    head = "<th>Group</th>" + ("<th>Correct</th><th>Converted</th>" if with_values else "")
    rows = []
    for s in f.sample:
        group = ", ".join(_e(v) for v in s.get("group", [])) or "(total)"
        if with_values:
            rows.append(
                f"<tr><td>{group}</td><td class='num'>{_e(s.get('expected'))}</td>"
                f"<td class='num diff'>{_e(s.get('actual'))}</td></tr>"
            )
        else:
            rows.append(f"<tr><td>{group}</td></tr>")
    note = '<p class="muted">First few differing groups.</p>'
    return (
        f'{note}<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )


def _finding(f: Finding) -> str:
    badge = '<span class="badge acc">accepted</span>' if f.accepted else f'<span class="badge fail">{_e(f.code)}</span>'
    trap = (
        f"<p><strong>Likely cause:</strong> trap <code>{_e(f.trap)}</code>, {_e(TRAP_HELP.get(f.trap, ''))}.</p>"
        if f.trap
        else ""
    )
    accepted = f"<p><strong>Accepted:</strong> {_e(f.accept_reason)}</p>" if f.accepted else ""
    queries = "".join(f"<li><code>{_e(q)}</code></li>" for q in f.queries[:10])
    more = f"<li class='muted'>and {len(f.queries) - 10} more</li>" if len(f.queries) > 10 else ""
    return (
        f"<details><summary>{badge} <code>{_e(f.id)}</code> &middot; <code>{_e(f.metric)}</code> "
        f"&middot; {_e(f.target)}</summary><div>"
        f"<p>{_e(CODE_HELP.get(f.code, ''))} {_e(f.detail)}</p>{trap}{accepted}"
        f"{_sample(f)}"
        f"<p><strong>Affected queries ({len(f.queries)}):</strong></p><ul>{queries}{more}</ul>"
        f'<p class="muted">Re-run: <code>{_e(f.repro_hint)}</code></p>'
        "</div></details>"
    )


def _model(m: ModelResult, targets: list[str]) -> str:
    ok = sum(q.status == "ok" for q in m.queries)
    skipped = len(m.queries) - ok
    parts = [
        f"<h2><code>{_e(m.path)}</code></h2>",
        f'<p class="muted">Model <code>{_e(m.name)}</code>, Ossie {_e(m.spec_version)}, '
        f"seed {m.seed}, {m.rows} rows. {ok} queries compared, {skipped} skipped.</p>",
        _grid(m, targets),
    ]
    if m.findings:
        parts.append("<h3>Findings</h3>")
        parts += [_finding(f) for f in m.findings]
    skipped_reasons = sorted({(q.reason or "", q.metric) for q in m.queries if q.status == "skipped"})
    if skipped_reasons:
        items = "".join(f"<li><code>{_e(metric)}</code>: {_e(reason)}</li>" for reason, metric in skipped_reasons)
        parts.append(f"<h3>Skipped by the reference compiler</h3><ul>{items}</ul>")
    if m.unsure_types:
        cols = ", ".join(f"<code>{_e(c)}</code>" for c in m.unsure_types)
        parts.append(f'<p class="muted">Column types guessed (add <code>[types]</code> hints to confirm): {cols}</p>')
    return "".join(parts)


def to_html(report: Report) -> str:
    s = report.summary
    status = (
        '<span class="badge pass">PASS</span>' if s.status == "pass" else '<span class="badge fail">FINDINGS</span>'
    )
    targets = [t.name for t in report.targets]
    versions = "; ".join(
        f"{_e(t.name)}: " + ", ".join(f"{_e(k)} {_e(v)}" for k, v in t.versions.items()) for t in report.targets
    )
    body = (
        f"<h1>Semround report {status}</h1>"
        f'<p class="muted">{s.models} model(s), {s.queries} queries ({s.skipped} skipped), '
        f"{s.findings} open finding(s), {s.accepted} accepted. Semround {_e(report.semround_version)}.</p>"
        f'<p class="muted">Targets: {versions or "none"}</p>' + "".join(_model(m, targets) for m in report.models)
    )
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Semround report</title><style>{CSS}</style></head>"
        f"<body><main>{body}</main></body></html>\n"
    )

"""Report renderers: deterministic JSON and Markdown (also used for PR comments)."""

from __future__ import annotations

import json

from semround.report.schema import Report


def to_json(report: Report) -> str:
    return json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def to_markdown(report: Report) -> str:
    s = report.summary
    icon = "PASS" if s.status == "pass" else "FINDINGS"
    targets = ", ".join(f"{t.name}" for t in report.targets) or "none"
    lines = [
        f"## Semround: {icon}",
        "",
        f"{s.models} model(s), {s.queries} queries ({s.skipped} skipped), targets: {targets}. "
        f"{s.findings} open finding(s), {s.accepted} accepted.",
        "",
    ]
    for m in report.models:
        lines.append(f"### `{m.path}` ({m.name}, Ossie {m.spec_version})")
        lines.append("")
        open_f = [f for f in m.findings if not f.accepted]
        if open_f:
            lines += ["| ID | Code | Metric | Target | Trap | Queries | Detail |", "|---|---|---|---|---|---|---|"]
            for f in open_f:
                lines.append(
                    f"| `{f.id}` | {f.code} | {_cell(f.metric)} | {f.target} | {f.trap or '-'} "
                    f"| {len(f.queries)} | {_cell(f.detail)} |"
                )
            lines.append("")
        accepted = [f for f in m.findings if f.accepted]
        if accepted:
            lines.append("Accepted:")
            lines += [f"- `{f.id}` {f.code} {f.metric} ({f.target}): {_cell(f.accept_reason)}" for f in accepted]
            lines.append("")
        skipped = [q for q in m.queries if q.status == "skipped"]
        if skipped:
            reasons: dict[str, list[str]] = {}
            for q in skipped:
                reasons.setdefault(q.reason or "", []).append(q.metric)
            lines.append("Skipped by the reference compiler:")
            for reason, metrics in sorted(reasons.items()):
                lines.append(f"- {reason}: {', '.join(sorted(set(metrics)))}")
            lines.append("")
        if m.unsure_types:
            shown = ", ".join(f"`{c}`" for c in m.unsure_types[:12])
            more = f" and {len(m.unsure_types) - 12} more" if len(m.unsure_types) > 12 else ""
            lines.append(f"Column types guessed (add `[types]` hints to confirm): {shown}{more}")
            lines.append("")
        for w in m.warnings:
            lines.append(f"> {w}")
        if m.warnings:
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"

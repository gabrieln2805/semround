# Working on Semround

- Read [STATUS.md](STATUS.md) first: it has the current stage and next steps.
- **At the end of every session, update STATUS.md:** the "Last updated" line, the stage table, the findings, the next steps, and one line in the session log.
- How the code fits together: [docs/codebase.md](docs/codebase.md). Keep it current when modules change.
- The plan is "Semround technical plan.md". It holds private notes and stays out of git (excluded in `.git/info/exclude`).
- Before finishing: `uv run ruff check src tests`, `uv run ruff format --check src tests` and `uv run pytest` must pass.
